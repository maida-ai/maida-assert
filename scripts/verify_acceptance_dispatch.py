"""Read GitHub evidence for one acceptance -> dispatch -> current PR report loop.

This verifier makes GET requests only. It does not create PRs, trigger workflows,
accept configuration, change protection, or attempt a merge.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from datetime import datetime
from hashlib import sha256
from urllib.parse import quote


class VerificationError(ValueError):
    """The supplied runs do not establish the claimed live dispatch loop."""


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def verify(
    *,
    repository,
    pr_number,
    acceptance_run,
    gate_run,
    head_sha,
    baseline,
    verdict,
    fetch,
):
    require(bool(re.fullmatch(r"[\w.-]+/[\w.-]+", repository)), "Invalid repository.")
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", head_sha)),
        "Supply the full accepted PR head SHA.",
    )
    require(
        verdict in {"pass", "fail", "inconclusive"}, "Unsupported expected verdict."
    )
    require(
        pr_number > 0 and acceptance_run > 0 and gate_run > 0, "IDs must be positive."
    )
    prefix = f"/repos/{repository}/"

    def get(path):
        return fetch(prefix + path)

    pull = get(f"pulls/{pr_number}")
    require(
        pull["state"] == "open"
        and pull["head"]["sha"] == head_sha
        and pull["head"]["repo"]["full_name"] == repository
        and pull["base"]["repo"]["full_name"] == repository,
        "Use the current open same-repository PR head; stale evidence cannot verify a new head.",
    )
    accepted = get(f"actions/runs/{acceptance_run}")
    dispatched = get(f"actions/runs/{gate_run}")
    require(
        accepted["id"] == acceptance_run and dispatched["id"] == gate_run,
        "Workflow run identity mismatch.",
    )
    require(
        accepted["event"] == "issue_comment"
        and accepted["status"] == "completed"
        and accepted["conclusion"] == "success",
        "Acceptance run did not complete successfully from an issue comment.",
    )
    require(
        dispatched["event"] == "repository_dispatch"
        and dispatched["status"] == "completed",
        "Gate must be a completed repository_dispatch run, not a manual or scheduled smoke.",
    )
    require(
        timestamp(dispatched["created_at"]) >= timestamp(accepted["created_at"]),
        "Dispatch predates acceptance.",
    )
    started = timestamp(dispatched["run_started_at"])
    expected_conclusion = "success" if verdict == "pass" else "failure"
    require(
        dispatched["conclusion"] == expected_conclusion,
        "Dispatch workflow conclusion does not match the expected verdict.",
    )
    run_url = dispatched["html_url"]

    commit = get(f"commits/{head_sha}")
    require(
        commit["sha"] == head_sha and len(commit["parents"]) == 1,
        "Accepted head must have one parent.",
    )
    require(
        [f["filename"] for f in commit["files"]] == [baseline],
        "Accepted commit must change only the configured baseline.",
    )
    content = get(f"contents/{quote(baseline, safe='/')}?ref={head_sha}")
    require(content["encoding"] == "base64", "Expected a readable baseline blob.")
    raw = base64.b64decode(content["content"].replace("\n", ""), validate=True)
    provenance = json.loads(raw)["acceptance"]
    require(
        str(provenance["capture"]["run_id"]) == str(acceptance_run)
        and str(provenance["capture"]["run_attempt"]) == str(accepted["run_attempt"]),
        "Baseline belongs to another acceptance run or attempt.",
    )
    source = provenance["source"]
    require(
        source["repository"] == repository
        and source["pull_request"]["number"] == pr_number
        and source["commit_sha"] == commit["parents"][0]["sha"],
        "Baseline acceptance does not bind this PR and parent commit.",
    )

    checks = get(f"commits/{head_sha}/check-runs?filter=all&per_page=100")
    require(
        checks["total_count"] == len(checks["check_runs"]),
        "Check response is truncated; export complete evidence before verification.",
    )
    matches = [
        c
        for c in checks["check_runs"]
        if c["name"] == "Maida statistical gate"
        and c["head_sha"] == head_sha
        and re.search(
            re.escape(run_url) + r"(?![\w/?#])", c.get("output", {}).get("summary", "")
        )
        and timestamp(c["started_at"]) >= started
    ]
    require(
        len(matches) == 1,
        "Expected exactly one named gate check for this dispatch attempt and PR head.",
    )
    check = matches[0]
    require(
        check["status"] == "completed"
        and check["conclusion"] == expected_conclusion
        and check["output"]["title"] == f"Maida statistical gate: {verdict.upper()}",
        "Named check verdict/conclusion mismatch.",
    )
    summary = check["output"]["summary"]
    require(
        f"Trusted policy revision: `{pull['base']['sha']}`" in summary,
        "Check did not evaluate the current trusted base policy.",
    )
    require(
        f"Evaluated baseline SHA-256: `{sha256(raw).hexdigest()}`" in summary,
        "Check did not evaluate the accepted baseline bytes.",
    )

    statuses = get(f"commits/{head_sha}/statuses?per_page=100")
    # The endpoint is newest-first: a later failure must not be hidden by an older success.
    status = next((s for s in statuses if s["context"] == "Maida / agent-check"), None)
    require(
        status is not None
        and status["target_url"] == run_url
        and status["state"] == expected_conclusion
        and timestamp(status["created_at"]) >= started,
        "Latest required status is missing, stale, or inconsistent with this dispatch.",
    )
    comments = get(f"issues/{pr_number}/comments?per_page=100")
    reports = [
        c
        for c in comments
        if "<!-- Sticky Pull Request Comment -->" in c["body"]
        and f"Evaluated PR head: `{head_sha}`" in c["body"]
        and f"GitHub conclusion: **{expected_conclusion}**" in c["body"]
        and timestamp(c["updated_at"]) >= started
    ]
    require(
        len(reports) == 1,
        "Expected one updated sticky PR report for this accepted head.",
    )
    protection = get(
        f"branches/{quote(pull['base']['ref'], safe='')}/protection/required_status_checks"
    )
    required = set(protection.get("contexts", [])) | {
        c["context"] for c in protection.get("checks", [])
    }
    require(
        "Maida / agent-check" in required
        and protection.get("strict") is True,
        "The explicit current-head status and strict up-to-date protection must be required.",
    )
    require(
        not ({"Maida statistical gate", "agent-check"} & required),
        "The dispatch listener must use its explicit status as the Maida requirement; "
        "named/job checks can remain expected after bot acceptance.",
    )
    current = get(f"pulls/{pr_number}")
    require(
        current["state"] == "open"
        and current["head"]["sha"] == head_sha
        and current["base"]["sha"] == pull["base"]["sha"],
        "PR changed while reading evidence; verify the fresh head/base.",
    )
    return {
        "repository": repository,
        "pr_number": pr_number,
        "head_sha": head_sha,
        "base_sha": pull["base"]["sha"],
        "acceptance_run": acceptance_run,
        "dispatch_run": gate_run,
        "dispatch_url": run_url,
        "verdict": verdict,
        "check_id": check["id"],
        "status_id": status["id"],
        "comment_id": reports[0]["id"],
        "baseline_sha256": sha256(raw).hexdigest(),
        "required_checks_verified": True,
        "merge_attempt_verified": False,
    }


def github_get(path):
    result = subprocess.run(
        ["gh", "api", "--method", "GET", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise VerificationError(
            "GitHub evidence read failed; check gh authentication and repository read permissions."
        )
    return json.loads(result.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--acceptance-run", required=True, type=int)
    parser.add_argument("--gate-run", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument(
        "--verdict", choices=("pass", "fail", "inconclusive"), default="pass"
    )
    try:
        result = verify(**vars(parser.parse_args(argv)), fetch=github_get)
        print(json.dumps(result, indent=2))
        return 0
    except (VerificationError, KeyError, TypeError, ValueError, OSError) as exc:
        print(f"Dispatch verification incomplete: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
