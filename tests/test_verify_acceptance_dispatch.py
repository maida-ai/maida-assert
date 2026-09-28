"""Evidence must connect one real acceptance to reports on its new PR head."""

import base64
import copy
import importlib.util
import json
from hashlib import sha256
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verify_acceptance_dispatch",
    Path(__file__).parents[1] / "scripts/verify_acceptance_dispatch.py",
)


@pytest.fixture
def evidence():
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    head, base, previous = "a" * 40, "b" * 40, "c" * 40
    baseline = json.dumps(
        {
            "acceptance": {
                "capture": {"run_id": "100", "run_attempt": "1"},
                "source": {
                    "repository": "owner/repo",
                    "pull_request": {"number": 7},
                    "commit_sha": previous,
                },
            }
        }
    ).encode()
    run_url = "https://github.com/owner/repo/actions/runs/101"
    pull = {
        "number": 7,
        "state": "open",
        "head": {"sha": head, "repo": {"full_name": "owner/repo"}},
        "base": {"sha": base, "ref": "main", "repo": {"full_name": "owner/repo"}},
    }
    data = {
        "pulls/7": pull,
        "actions/runs/100": {
            "id": 100,
            "run_attempt": 1,
            "event": "issue_comment",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-28T10:00:00Z",
            "run_started_at": "2026-09-28T10:00:00Z",
        },
        # GitHub's dispatch head_sha identifies the default branch, not the evaluated PR.
        "actions/runs/101": {
            "id": 101,
            "event": "repository_dispatch",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-28T10:01:00Z",
            "run_started_at": "2026-09-28T10:01:00Z",
            "html_url": run_url,
            "head_sha": base,
        },
        f"commits/{head}": {
            "sha": head,
            "parents": [{"sha": previous}],
            "files": [{"filename": "baseline.json"}],
        },
        f"contents/baseline.json?ref={head}": {
            "encoding": "base64",
            "content": base64.b64encode(baseline).decode(),
        },
        f"commits/{head}/check-runs?filter=all&per_page=100": {
            "total_count": 1,
            "check_runs": [
                {
                    "id": 201,
                    "name": "Maida statistical gate",
                    "head_sha": head,
                    "status": "completed",
                    "conclusion": "success",
                    "started_at": "2026-09-28T10:02:00Z",
                    "completed_at": "2026-09-28T10:02:00Z",
                    "output": {
                        "title": "Maida statistical gate: PASS",
                        "summary": f"Workflow: {run_url}\nTrusted policy revision: `{base}`.\nEvaluated baseline SHA-256: `{sha256(baseline).hexdigest()}`.",
                    },
                }
            ],
        },
        f"commits/{head}/statuses?per_page=100": [
            {
                "id": 301,
                "context": "Maida / agent-check",
                "state": "success",
                "target_url": run_url,
                "created_at": "2026-09-28T10:02:00Z",
            }
        ],
        "issues/7/comments?per_page=100": [
            {
                "id": 401,
                "body": f"<!-- Sticky Pull Request Comment -->\nEvaluated PR head: `{head}`.\nGitHub conclusion: **success**.",
                "updated_at": "2026-09-28T10:02:00Z",
            }
        ],
        "branches/main/protection/required_status_checks": {
            "strict": True,
            "contexts": ["Maida / agent-check"],
        },
    }
    args = {
        "repository": "owner/repo",
        "pr_number": 7,
        "acceptance_run": 100,
        "gate_run": 101,
        "head_sha": head,
        "baseline": "baseline.json",
        "verdict": "pass",
    }
    return module, data, args


def verify(evidence):
    module, data, args = evidence
    return module.verify(
        **args,
        fetch=lambda path: copy.deepcopy(data[path.removeprefix("/repos/owner/repo/")]),
    )


def test_real_dispatch_evidence_uses_pr_head_not_dispatch_workflow_head(evidence):
    result = verify(evidence)
    assert result["head_sha"] == "a" * 40
    assert result["check_id"] == 201 and result["status_id"] == 301
    assert result["merge_attempt_verified"] is False


@pytest.mark.parametrize("context", ["Maida statistical gate", "agent-check"])
def test_dispatch_rejects_required_checks_that_cannot_follow_bot_acceptance(evidence, context):
    module, data, _args = evidence
    data["branches/main/protection/required_status_checks"]["contexts"].append(context)
    with pytest.raises(module.VerificationError, match="dispatch"):
        verify(evidence)


def test_dispatch_requires_explicit_status_even_when_named_check_is_required(evidence):
    module, data, _args = evidence
    data["branches/main/protection/required_status_checks"]["contexts"] = ["Maida statistical gate"]
    with pytest.raises(module.VerificationError, match="status"):
        verify(evidence)


def test_dispatch_allows_other_repository_requirements(evidence):
    _module, data, _args = evidence
    protection = data["branches/main/protection/required_status_checks"]
    protection["contexts"] = ["unit-tests"]
    protection["checks"] = [{"context": "Maida / agent-check", "app_id": -1}]
    assert verify(evidence)["required_checks_verified"] is True


@pytest.mark.parametrize(
    "case",
    [
        "old-head",
        "wrong-event",
        "failed-acceptance",
        "wrong-parent",
        "extra-file",
        "other-acceptance",
        "other-run-check",
        "old-attempt-check",
        "wrong-baseline",
        "missing-named-check",
        "missing-status",
        "stale-status",
        "wrong-comment",
        "missing-protection",
        "non-strict",
        "truncated",
    ],
)
def test_incomplete_or_stale_evidence_fails_closed(evidence, case):
    module, data, args = evidence
    head = args["head_sha"]
    check_response = data[f"commits/{head}/check-runs?filter=all&per_page=100"]
    check = check_response["check_runs"][0]
    if case == "old-head":
        data["pulls/7"]["head"]["sha"] = "d" * 40
    elif case == "wrong-event":
        data["actions/runs/101"]["event"] = "workflow_dispatch"
    elif case == "failed-acceptance":
        data["actions/runs/100"]["conclusion"] = "failure"
    elif case == "wrong-parent":
        data[f"commits/{head}"]["parents"][0]["sha"] = "d" * 40
    elif case == "extra-file":
        data[f"commits/{head}"]["files"].append({"filename": "agent.py"})
    elif case == "other-acceptance":
        args["acceptance_run"] = 102
        data["actions/runs/102"] = data["actions/runs/100"]
    elif case == "other-run-check":
        check["output"]["summary"] = check["output"]["summary"].replace(
            "runs/101", "runs/99"
        )
    elif case == "old-attempt-check":
        data["actions/runs/101"]["run_started_at"] = "2026-09-28T10:03:00Z"
    elif case == "wrong-baseline":
        check["output"]["summary"] = check["output"]["summary"].split(
            "Evaluated baseline"
        )[0]
    elif case == "missing-status":
        data[f"commits/{head}/statuses?per_page=100"] = []
    elif case == "missing-named-check":
        check_response.update(total_count=0, check_runs=[])
    elif case == "stale-status":
        data[f"commits/{head}/statuses?per_page=100"][0]["target_url"] = (
            "https://github.com/owner/repo/actions/runs/99"
        )
    elif case == "wrong-comment":
        data["issues/7/comments?per_page=100"][0]["body"] = "PASS on an old head"
    elif case == "missing-protection":
        data["branches/main/protection/required_status_checks"]["contexts"] = []
    elif case == "non-strict":
        data["branches/main/protection/required_status_checks"]["strict"] = False
    else:
        check_response["total_count"] = 101
    with pytest.raises(module.VerificationError):
        verify(evidence)


def test_head_movement_during_read_invalidates_evidence(evidence):
    module, data, args = evidence
    reads = 0

    def fetch(path):
        nonlocal reads
        value = copy.deepcopy(data[path.removeprefix("/repos/owner/repo/")])
        if path.endswith("/pulls/7"):
            reads += 1
            if reads > 1:
                value["head"]["sha"] = "d" * 40
        return value

    with pytest.raises(module.VerificationError, match="changed"):
        module.verify(**args, fetch=fetch)


@pytest.mark.parametrize("verdict", ["fail", "inconclusive"])
def test_negative_verdict_evidence_remains_a_failed_gate(evidence, verdict):
    _module, data, args = evidence
    args["verdict"] = verdict
    head = args["head_sha"]
    data["actions/runs/101"]["conclusion"] = "failure"
    check = data[f"commits/{head}/check-runs?filter=all&per_page=100"]["check_runs"][0]
    check["conclusion"] = "failure"
    check["output"]["title"] = f"Maida statistical gate: {verdict.upper()}"
    data[f"commits/{head}/statuses?per_page=100"][0]["state"] = "failure"
    comment = data["issues/7/comments?per_page=100"][0]
    comment["body"] = comment["body"].replace("**success**", "**failure**")
    assert verify(evidence)["verdict"] == verdict


def test_newer_error_status_cannot_hide_behind_older_success(evidence):
    module, data, args = evidence
    statuses = data[f"commits/{args['head_sha']}/statuses?per_page=100"]
    statuses.insert(0, {**statuses[0], "state": "error"})
    with pytest.raises(module.VerificationError, match="Latest required status"):
        verify(evidence)


def test_run_url_prefix_cannot_match_another_run(evidence):
    module, data, args = evidence
    check = data[f"commits/{args['head_sha']}/check-runs?filter=all&per_page=100"][
        "check_runs"
    ][0]
    check["output"]["summary"] = check["output"]["summary"].replace(
        "runs/101", "runs/1019"
    )
    with pytest.raises(module.VerificationError, match="exactly one named gate check"):
        verify(evidence)


def test_baseline_provenance_must_match_current_acceptance_attempt(evidence):
    module, data, _args = evidence
    data["actions/runs/100"]["run_attempt"] = 2
    with pytest.raises(
        module.VerificationError, match="another acceptance run or attempt"
    ):
        verify(evidence)


def test_live_transport_is_read_only_and_hides_api_error_payload(evidence, monkeypatch):
    from types import SimpleNamespace

    module, _data, _args = evidence
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=1, stdout="private-payload", stderr="private-token"
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(module.VerificationError) as error:
        module.github_get("/repos/owner/repo/pulls/7")
    assert calls == [["gh", "api", "--method", "GET", "/repos/owner/repo/pulls/7"]]
    assert "private" not in str(error.value)
