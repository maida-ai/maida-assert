"""Dispatch claims never substitute for the live PR identity or gate verdict."""

import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location(
        "pr_context", Path(__file__).resolve().parents[1] / "scripts/pr_context.py"
    )
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.fixture
def identity():
    pull = {
        "number": 7,
        "state": "open",
        "base": {"sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
        "head": {
            "sha": "a" * 40,
            "ref": "candidate",
            "repo": {"full_name": "owner/repo"},
        },
    }
    event = {
        "action": "maida_baseline_updated",
        "client_payload": {"pr_number": 7, "sha": "a" * 40, "ref": "candidate"},
    }
    return pull, event


def test_pull_request_requires_current_base_and_allows_read_only_fork(identity):
    pull, _ = identity
    m = module()
    event = {
        "pull_request": {
            "number": 7,
            "head": {"sha": "a" * 40},
            "base": {"sha": "b" * 40},
        }
    }
    pull["head"]["repo"]["full_name"] = "fork/repo"
    assert m.verify(event, "pull_request", "owner/repo", lambda number: pull) == pull
    event["pull_request"]["base"]["sha"] = "c" * 40
    with pytest.raises(ValueError, match="base changed"):
        m.verify(event, "pull_request", "owner/repo", lambda number: pull)


@pytest.mark.parametrize(
    "event", [None, [], {"action": "maida_baseline_updated", "client_payload": None}]
)
def test_malformed_event_is_actionable(event):
    with pytest.raises(ValueError, match="object"):
        module().verify(
            event,
            "repository_dispatch",
            "owner/repo",
            lambda number: pytest.fail("unexpected lookup"),
        )


def test_dispatch_uses_verified_pr(identity):
    pull, event = identity
    assert (
        module().verify(event, "repository_dispatch", "owner/repo", lambda number: pull)
        == pull
    )


@pytest.mark.parametrize(
    "change", ["head", "ref", "base-repo", "fork", "closed", "number", "event"]
)
def test_forged_or_stale_dispatch_fails_closed(identity, change):
    pull, event = identity
    if change == "head":
        pull["head"]["sha"] = "c" * 40
    elif change == "ref":
        pull["head"]["ref"] = "another"
    elif change in {"base-repo", "fork"}:
        pull["base" if change == "base-repo" else "head"]["repo"]["full_name"] = (
            "other/repo"
        )
    elif change == "closed":
        pull["state"] = "closed"
    elif change == "number":
        event["client_payload"]["pr_number"] = True
    else:
        event["action"] = "unrelated"
    with pytest.raises(ValueError):
        module().verify(event, "repository_dispatch", "owner/repo", lambda number: pull)


@pytest.mark.parametrize(
    "verdict,conclusion,publication,state",
    [
        ("pass", "success", "success", "success"),
        ("inconclusive", "failure", "success", "failure"),
        ("inconclusive", "neutral", "success", "failure"),
        ("fail", "failure", "success", "failure"),
        ("pass", "failure", "success", "failure"),
        ("pass", "neutral", "success", "failure"),
        ("pass", "success", "failure", "error"),
        ("pass", "", "success", "error"),
        ("", "", "", "error"),
    ],
)
def test_status_requires_validated_pass_and_publication(
    verdict, conclusion, publication, state
):
    payload = module().status_payload(verdict, conclusion, publication, "https://run")
    assert payload["state"] == state
    if state != "success":
        assert "passed" not in payload["description"].lower()
    if verdict == "inconclusive":
        assert "INCONCLUSIVE" in payload["description"]


@pytest.mark.parametrize("side", ["head", "base"])
def test_ref_change_before_status_publication_makes_no_write(
    identity, monkeypatch, tmp_path, side
):
    import json

    m = module()
    pull, event = identity
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event))
    for key, value in {
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_API_URL": "https://api.test",
        "GITHUB_TOKEN": "test-token",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_EVENT_NAME": "repository_dispatch",
        "EXPECTED_HEAD_SHA": "a" * 40,
        "EXPECTED_BASE_SHA": "b" * 40,
    }.items():
        monkeypatch.setenv(key, value)
    pull[side]["sha"] = "c" * 40
    requests = []

    def request(method, *args, **kwargs):
        requests.append(method)
        return pull

    monkeypatch.setattr(m, "_request_json", request)
    monkeypatch.setattr(m.sys, "argv", ["pr_context.py", "status"])
    assert m.main() == 2
    assert requests == ["GET"]
