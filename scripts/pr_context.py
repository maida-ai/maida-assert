"""Resolve live PR identity and publish a verdict-derived required status."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# Support isolated Python execution without importing from the candidate checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from accept_command import CommandError, _request_json, _write_outputs


class ContextError(ValueError):
    """PR identity or publication context cannot safely be used."""


def verify(event, event_name, repository, fetch):
    if not isinstance(event, dict):
        raise ContextError("GitHub event must be an object.")
    if event_name == "repository_dispatch":
        if event.get("action") != "maida_baseline_updated":
            raise ContextError("Unsupported dispatch; expected maida_baseline_updated.")
        claimed = event.get("client_payload", {})
        if not isinstance(claimed, dict):
            raise ContextError("Dispatch payload must be an object.")
        number, sha, ref = (claimed.get(key) for key in ("pr_number", "sha", "ref"))
    elif event_name == "pull_request":
        claimed = event.get("pull_request", {})
        if not isinstance(claimed, dict):
            raise ContextError("Missing PR identity in the event.")
        number = claimed.get("number")
        sha = claimed.get("head", {}).get("sha")
        ref = None
    else:
        raise ContextError("Expected a pull_request or baseline dispatch event.")
    if (
        type(number) is not int
        or number <= 0
        or not isinstance(sha, str)
        or not re.fullmatch(r"[0-9a-f]{40}", sha)
    ):
        raise ContextError("Missing valid PR number and head SHA.")
    pull = fetch(number)
    head, base = pull.get("head", {}), pull.get("base", {})
    if (
        pull.get("state") != "open"
        or pull.get("number") != number
        or head.get("sha") != sha
        or base.get("repo", {}).get("full_name") != repository
        or not re.fullmatch(r"[0-9a-f]{40}", str(base.get("sha", "")))
    ):
        raise ContextError(
            "PR is closed or its identity changed; rerun for the latest PR head."
        )
    if event_name == "repository_dispatch" and (
        not ref
        or head.get("ref") != ref
        or head.get("repo", {}).get("full_name") != repository
    ):
        raise ContextError(
            "Dispatch must match the current same-repository PR head and branch."
        )
    if event_name == "pull_request" and base["sha"] != claimed.get("base", {}).get(
        "sha"
    ):
        raise ContextError("PR base changed; rerun the gate against the latest base.")
    return pull


def live_context():
    repository = os.environ["GITHUB_REPOSITORY"]

    def api(method, path, **kwargs):
        return _request_json(
            method,
            path,
            api_url=os.environ["GITHUB_API_URL"],
            token=os.environ["GITHUB_TOKEN"],
            **kwargs,
        )

    pull = verify(
        json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()),
        os.environ["GITHUB_EVENT_NAME"],
        repository,
        lambda number: api("GET", f"/repos/{repository}/pulls/{number}"),
    )
    for side in ("head", "base"):
        expected = os.environ.get(f"EXPECTED_{side.upper()}_SHA")
        if expected and pull[side]["sha"] != expected:
            raise ContextError(
                f"PR {side} changed during evaluation; rerun for fresh results."
            )
    return pull, api


def status_payload(verdict, conclusion, publication, details_url):
    if (
        verdict not in {"pass", "fail", "inconclusive"}
        or conclusion not in {"success", "failure", "neutral"}
        or publication != "success"
    ):
        state, description = (
            "error",
            "Maida evaluation/publication incomplete; rerun the workflow",
        )
    elif verdict == "pass" and conclusion == "success":
        state, description = "success", "Maida behavioral verdict: PASS"
    else:
        state, description = (
            "failure",
            f"Maida verdict: {verdict.upper()}; merge not authorized",
        )
    return {
        "state": state,
        "description": description,
        "context": "Maida / agent-check",
        "target_url": details_url,
    }


def main():
    try:
        pull, api = live_context()
        if len(sys.argv) > 1 and sys.argv[1] == "status":
            if not os.environ.get("EXPECTED_HEAD_SHA") or not os.environ.get(
                "EXPECTED_BASE_SHA"
            ):
                raise ContextError(
                    "No verified evaluation identity; refusing to publish a status."
                )
            details = (
                f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
                f"/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            )
            payload = status_payload(
                os.environ.get("VERDICT"),
                os.environ.get("CONCLUSION"),
                os.environ.get("PUBLICATION"),
                details,
            )
            api(
                "POST",
                f"/repos/{os.environ['GITHUB_REPOSITORY']}/statuses/{pull['head']['sha']}",
                payload=payload,
            )
        else:
            _write_outputs(
                Path(os.environ["GITHUB_OUTPUT"]),
                {
                    "head-sha": pull["head"]["sha"],
                    "base-sha": pull["base"]["sha"],
                    "pr-number": str(pull["number"]),
                },
            )
        return 0
    except (ValueError, KeyError, OSError, CommandError) as exc:
        print(f"Maida PR context error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
