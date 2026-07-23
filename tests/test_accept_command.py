"""Tests for the authorized ``/maida accept`` command handler."""

from __future__ import annotations

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "accept_command.py"
ACTION_PATH = REPO_ROOT / "accept-command" / "action.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("maida_accept_command", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(body="/maida accept", *, login="maintainer", number=42):
    return {
        "issue": {"number": number, "pull_request": {"url": "https://api/pr/42"}},
        "comment": {"body": body, "user": {"login": login, "type": "User"}},
        "repository": {"full_name": "owner/repo"},
    }


def _pull(*, repository="owner/repo"):
    return {
        "html_url": "https://github.test/owner/repo/pull/42",
        "head": {
            "ref": "feature/intentional-change",
            "sha": "a" * 40,
            "repo": {"full_name": repository},
        },
    }


def _read_outputs(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("/maida accept", "Accepted via /maida accept by @alice"),
        ("  /maida accept   expected tool split  ", "expected tool split"),
    ],
)
def test_parse_accept_command_supports_default_and_human_reason(body, expected):
    module = _load_module()
    assert module.parse_accept_command(body, login="alice") == expected


@pytest.mark.parametrize(
    "body",
    [
        "/maida ACCEPT",
        "/maida acceptance",
        "/maida accept\nquoted text",
        "/maida accept " + "x" * 501,
    ],
)
def test_parse_accept_command_rejects_ambiguous_or_oversized_comments(body):
    module = _load_module()
    with pytest.raises(module.CommandError):
        module.parse_accept_command(body, login="alice")


def test_prepare_authorizes_write_user_and_emits_verified_pr_outputs(
    tmp_path, monkeypatch
):
    module = _load_module()
    output = tmp_path / "output"
    requests = []

    def fake_request(method, path, **kwargs):
        requests.append((method, path))
        if path.endswith("/permission"):
            return {"permission": "write", "role_name": "maintain"}
        if path.endswith("/pulls/42"):
            return _pull()
        raise AssertionError(path)

    monkeypatch.setattr(module, "_request_json", fake_request)
    module.prepare_command(
        event=_event("/maida accept expected retrieval flow"),
        baseline="baselines/agent.json",
        api_url="https://api.github.test",
        token="secret",
        output_path=output,
    )

    assert requests == [
        ("GET", "/repos/owner/repo/collaborators/maintainer/permission"),
        ("GET", "/repos/owner/repo/pulls/42"),
    ]
    assert _read_outputs(output) == {
        "handled": "true",
        "authorized": "true",
        "reason": "expected retrieval flow",
        "pr-number": "42",
        "head-repository": "owner/repo",
        "head-branch": "feature/intentional-change",
        "head-sha": "a" * 40,
    }


def test_prepare_ignores_non_pull_request_comments(tmp_path, monkeypatch):
    module = _load_module()
    output = tmp_path / "output"
    event = _event()
    event["issue"].pop("pull_request")

    monkeypatch.setattr(
        module,
        "_request_json",
        lambda *args, **kwargs: pytest.fail("unexpected GitHub API request"),
    )
    module.prepare_command(
        event=event,
        baseline="baselines/agent.json",
        api_url="https://api.github.test",
        token="secret",
        output_path=output,
    )

    assert _read_outputs(output) == {"handled": "false", "authorized": "false"}


def test_prepare_replies_to_malformed_command(tmp_path, monkeypatch):
    module = _load_module()
    output = tmp_path / "output"
    comments = []

    def fake_request(method, path, **kwargs):
        assert method == "POST"
        comments.append(kwargs["payload"]["body"])
        return {}

    monkeypatch.setattr(module, "_request_json", fake_request)
    module.prepare_command(
        event=_event("/maida accept\nquoted text"),
        baseline="baselines/agent.json",
        api_url="https://api.github.test",
        token="secret",
        output_path=output,
    )

    assert _read_outputs(output) == {"handled": "true", "authorized": "false"}
    assert "one line" in comments[0]


@pytest.mark.parametrize("permission", ["read", "none"])
def test_prepare_politely_refuses_unauthorized_users(
    tmp_path, monkeypatch, permission
):
    module = _load_module()
    output = tmp_path / "output"
    comments = []

    def fake_request(method, path, **kwargs):
        if path.endswith("/permission"):
            return {"permission": permission, "role_name": permission}
        if method == "POST":
            comments.append(kwargs["payload"]["body"])
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(module, "_request_json", fake_request)
    module.prepare_command(
        event=_event(),
        baseline="baselines/agent.json",
        api_url="https://api.github.test",
        token="secret",
        output_path=output,
    )

    assert _read_outputs(output) == {"handled": "true", "authorized": "false"}
    assert len(comments) == 1
    assert "write access" in comments[0]
    assert "@maintainer" in comments[0]


def test_prepare_explains_unconfigured_baseline_without_api_lookup(
    tmp_path, monkeypatch
):
    module = _load_module()
    output = tmp_path / "output"
    comments = []

    def fake_request(method, path, **kwargs):
        assert method == "POST"
        comments.append(kwargs["payload"]["body"])
        return {}

    monkeypatch.setattr(module, "_request_json", fake_request)
    module.prepare_command(
        event=_event(),
        baseline="",
        api_url="https://api.github.test",
        token="secret",
        output_path=output,
    )

    assert _read_outputs(output)["authorized"] == "false"
    assert "baseline" in comments[0].lower()
    assert "MAIDA_BASELINE" in comments[0]


def test_prepare_refuses_fork_before_checkout(tmp_path, monkeypatch):
    module = _load_module()
    output = tmp_path / "output"
    comments = []

    def fake_request(method, path, **kwargs):
        if path.endswith("/permission"):
            return {"permission": "admin"}
        if path.endswith("/pulls/42"):
            return _pull(repository="someone/fork")
        if method == "POST":
            comments.append(kwargs["payload"]["body"])
            return {}
        raise AssertionError(path)

    monkeypatch.setattr(module, "_request_json", fake_request)
    module.prepare_command(
        event=_event(),
        baseline="baselines/agent.json",
        api_url="https://api.github.test",
        token="secret",
        output_path=output,
    )

    assert _read_outputs(output)["authorized"] == "false"
    assert "Fork pull requests" in comments[0]


@pytest.mark.parametrize(
    ("outcome", "changed", "expected"),
    [
        ("success", "true", "Baseline accepted"),
        ("success", "false", "already matches"),
        ("failure", "false", "could not accept"),
        ("skipped", "false", "could not accept"),
    ],
)
def test_finalize_posts_confirmation_or_actionable_failure(
    monkeypatch, outcome, changed, expected
):
    module = _load_module()
    comments = []

    def fake_request(method, path, **kwargs):
        comments.append(kwargs["payload"]["body"])
        return {}

    monkeypatch.setattr(module, "_request_json", fake_request)
    module.finalize_command(
        event=_event(),
        outcome=outcome,
        changed=changed,
        commit_sha="b" * 40,
        head_sha="c" * 40,
        api_url="https://api.github.test",
        token="secret",
        server_url="https://github.test",
        run_id="1234",
    )

    assert len(comments) == 1
    assert expected in comments[0]
    if outcome == "success" and changed == "true":
        assert "https://github.test/owner/repo/commit/" + "b" * 40 in comments[0]
    if outcome != "success":
        assert "https://github.test/owner/repo/actions/runs/1234" in comments[0]


def test_request_json_sends_authenticated_json(monkeypatch):
    module = _load_module()
    observed = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"permission":"write"}'

    def fake_urlopen(request, timeout):
        observed["request"] = request
        observed["timeout"] = timeout
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    result = module._request_json(
        "POST",
        "/repos/owner/repo/example",
        api_url="https://api.github.test/",
        token="secret",
        payload={"value": "ok"},
    )

    request = observed["request"]
    assert result == {"permission": "write"}
    assert request.full_url == "https://api.github.test/repos/owner/repo/example"
    assert request.method == "POST"
    assert request.data == b'{"value":"ok"}'
    assert request.get_header("Authorization") == "Bearer secret"
    assert observed["timeout"] == 30


def test_request_json_turns_http_failure_into_safe_error(monkeypatch):
    module = _load_module()

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(module.CommandError, match="HTTP 403"):
        module._request_json(
            "GET",
            "/repos/owner/repo/example",
            api_url="https://api.github.test",
            token="secret",
        )


def test_main_prepares_authorized_command_from_event_file(tmp_path, monkeypatch):
    module = _load_module()
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "output"
    event_path.write_text(json.dumps(_event()))

    def fake_request(method, path, **kwargs):
        if path.endswith("/permission"):
            return {"permission": "write"}
        if path.endswith("/pulls/42"):
            return _pull()
        raise AssertionError(path)

    monkeypatch.setattr(module, "_request_json", fake_request)
    result = module.main(
        ["prepare", "--baseline", "baselines/agent.json"],
        environ={
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_API_URL": "https://api.github.test",
            "GITHUB_TOKEN": "secret",
            "GITHUB_OUTPUT": str(output_path),
        },
    )

    assert result == 0
    assert _read_outputs(output_path)["authorized"] == "true"


def test_main_returns_error_for_missing_environment(capsys):
    module = _load_module()

    assert module.main(["prepare"], environ={}) == 1
    assert "GITHUB_EVENT_PATH" in capsys.readouterr().err


def test_accept_command_action_checks_authorization_before_checkout():
    assert ACTION_PATH.is_file()
    action = yaml.safe_load(ACTION_PATH.read_text())
    inputs = action["inputs"]
    for name in (
        "agent-script",
        "baseline",
        "policy",
        "maida-version",
        "python-version",
        "extra-args",
        "github-token",
    ):
        assert name in inputs

    steps = action["runs"]["steps"]
    prepare_index = next(i for i, step in enumerate(steps) if step.get("id") == "prepare")
    checkout_index = next(
        i for i, step in enumerate(steps) if step.get("name") == "Check out verified PR head"
    )
    run_index = next(i for i, step in enumerate(steps) if step.get("name") == "Run agent")
    write_index = next(i for i, step in enumerate(steps) if step.get("id") == "write-back")
    finalize_index = next(i for i, step in enumerate(steps) if step.get("name") == "Report command result")

    assert prepare_index < checkout_index < run_index < write_index < finalize_index
    assert steps[checkout_index]["if"] == "steps.prepare.outputs.authorized == 'true'"
    assert steps[run_index]["if"] == "steps.prepare.outputs.authorized == 'true'"
    assert "always()" in steps[finalize_index]["if"]
    assert steps[finalize_index]["env"]["WRITE_BACK_OUTCOME"] == "${{ steps.write-back.outcome }}"
    assert action["outputs"]["commit-sha"]["value"] == "${{ steps.write-back.outputs.commit-sha }}"
