"""Executable tests for the baseline write-back sub-action."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import urllib.error
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_PATH = REPO_ROOT / "write-back" / "action.yml"
SCRIPT_PATH = REPO_ROOT / "scripts" / "write_back.py"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def _load_write_back_module():
    spec = importlib.util.spec_from_file_location("maida_write_back", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    worktree = tmp_path / "worktree"
    remote.mkdir()
    worktree.mkdir()
    _git(remote, "init", "--bare", "--initial-branch=main")
    _git(worktree, "init", "--initial-branch=main")
    _git(worktree, "config", "user.name", "Test User")
    _git(worktree, "config", "user.email", "test@example.com")
    (worktree / "baseline.json").write_text('{"source_run_id": "old"}\n')
    (worktree / "agent.py").write_text("print('agent')\n")
    _git(worktree, "add", "baseline.json", "agent.py")
    _git(worktree, "commit", "-m", "Initial state")
    _git(worktree, "remote", "add", "origin", str(remote))
    _git(worktree, "push", "-u", "origin", "main")
    _git(worktree, "switch", "-c", "feature/write-back")
    _git(worktree, "push", "-u", "origin", "feature/write-back")
    return worktree, remote, _git(worktree, "rev-parse", "HEAD").stdout.strip()


def _install_fake_maida(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "maida"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

assert sys.argv[1] == "accept"
baseline = pathlib.Path(sys.argv[sys.argv.index("--baseline") + 1])
reason = sys.argv[sys.argv.index("--reason") + 1]
if os.environ.get("FAKE_MAIDA_NO_CHANGE") != "1":
    baseline.write_text(
        json.dumps(
            {"source_run_id": "new", "acceptance": {"reason": reason}},
            indent=2,
            sort_keys=True,
        )
        + "\\n"
    )
"""
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _args(head_sha: str, *, repository: str = "owner/repo") -> list[str]:
    return [
        "--baseline",
        "baseline.json",
        "--reason",
        "expected tool flow",
        "--pr-number",
        "42",
        "--head-repository",
        repository,
        "--head-branch",
        "feature/write-back",
        "--expected-head-sha",
        head_sha,
    ]


def _environment(output_path: Path) -> dict[str, str]:
    return {
        **os.environ,
        "GITHUB_API_URL": "https://api.github.test",
        "GITHUB_OUTPUT": str(output_path),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_TOKEN": "secret-token",
    }


def test_write_back_action_exposes_stable_interface():
    assert ACTION_PATH.is_file()
    action = yaml.safe_load(ACTION_PATH.read_text())

    expected_required = {
        "baseline",
        "reason",
        "pr-number",
        "head-repository",
        "head-branch",
        "expected-head-sha",
        "github-token",
    }
    inputs = action["inputs"]
    assert expected_required.issubset(inputs)
    assert all(inputs[name]["required"] is True for name in expected_required)
    assert inputs["run-id"]["required"] is False
    assert inputs["run-id"]["default"] == ""
    assert action["outputs"] == {
        "changed": {
            "description": "Whether a new baseline commit was created",
            "value": "${{ steps.write-back.outputs.changed }}",
        },
        "commit-sha": {
            "description": "Created baseline commit SHA, or empty when unchanged",
            "value": "${{ steps.write-back.outputs.commit-sha }}",
        },
        "head-sha": {
            "description": "PR head SHA sent to the fresh-gate dispatch",
            "value": "${{ steps.write-back.outputs.head-sha }}",
        },
    }

    assert action["runs"]["using"] == "composite"
    step = action["runs"]["steps"][0]
    assert step["id"] == "write-back"
    assert step["env"]["GITHUB_TOKEN"] == "${{ inputs.github-token }}"
    assert step["env"]["MAIDA_ACCEPT_REASON"] == "${{ inputs.reason }}"
    assert "${{ inputs.reason }}" not in step["run"]
    assert "scripts/write_back.py" in step["run"]


def test_write_back_commits_only_baseline_pushes_and_dispatches(
    tmp_path, monkeypatch
):
    worktree, remote, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    module = _load_write_back_module()
    dispatched = []
    monkeypatch.setattr(
        module,
        "_dispatch_repository",
        lambda **kwargs: dispatched.append(kwargs),
    )
    output_path = tmp_path / "github-output"

    result = module.main(_args(original_sha), environ=_environment(output_path), cwd=worktree)

    assert result == 0
    new_sha = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert new_sha != original_sha
    assert (
        _git(remote, "rev-parse", "refs/heads/feature/write-back").stdout.strip()
        == new_sha
    )
    assert _git(worktree, "show", "--format=", "--name-only", "HEAD").stdout.strip() == "baseline.json"
    assert _git(worktree, "show", "-s", "--format=%an", "HEAD").stdout.strip() == "github-actions[bot]"
    assert (
        _git(worktree, "show", "-s", "--format=%ae", "HEAD").stdout.strip()
        == "41898282+github-actions[bot]@users.noreply.github.com"
    )
    assert _git(worktree, "show", "-s", "--format=%s", "HEAD").stdout.strip() == "chore(maida): accept baseline update"
    assert dispatched == [
        {
            "api_url": "https://api.github.test",
            "repository": "owner/repo",
            "token": "secret-token",
            "payload": {
                "event_type": "maida_baseline_updated",
                "client_payload": {
                    "pr_number": 42,
                    "ref": "feature/write-back",
                    "sha": new_sha,
                    "baseline": "baseline.json",
                },
            },
        }
    ]
    assert output_path.read_text().splitlines() == [
        "changed=true",
        f"commit-sha={new_sha}",
        f"head-sha={new_sha}",
    ]


def test_write_back_rejects_forks_before_running_maida(tmp_path, monkeypatch, capsys):
    worktree, _, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    module = _load_write_back_module()
    monkeypatch.setattr(
        module,
        "_dispatch_repository",
        lambda **kwargs: pytest.fail("fork rejection must not dispatch"),
    )

    result = module.main(
        _args(original_sha, repository="someone/fork"),
        environ=_environment(tmp_path / "output"),
        cwd=worktree,
    )

    assert result == 1
    assert "Fork pull requests are not supported" in capsys.readouterr().err
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == original_sha
    assert (worktree / "baseline.json").read_text() == '{"source_run_id": "old"}\n'


def test_write_back_rejects_stale_checkout(tmp_path, monkeypatch, capsys):
    worktree, _, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    module = _load_write_back_module()

    result = module.main(
        _args("0" * len(original_sha)),
        environ=_environment(tmp_path / "output"),
        cwd=worktree,
    )

    assert result == 1
    assert "does not match expected PR head" in capsys.readouterr().err
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == original_sha


def test_write_back_rejects_staged_changes(tmp_path, monkeypatch, capsys):
    worktree, _, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    module = _load_write_back_module()
    (worktree / "agent.py").write_text("print('changed')\n")
    _git(worktree, "add", "agent.py")

    result = module.main(
        _args(original_sha),
        environ=_environment(tmp_path / "output"),
        cwd=worktree,
    )

    assert result == 1
    assert "staged changes" in capsys.readouterr().err
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == original_sha


def test_write_back_rejects_baseline_outside_repository(tmp_path, monkeypatch, capsys):
    worktree, _, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    module = _load_write_back_module()
    args = _args(original_sha)
    args[args.index("baseline.json")] = "../outside.json"
    (tmp_path / "outside.json").write_text("{}\n")

    result = module.main(
        args,
        environ=_environment(tmp_path / "output"),
        cwd=worktree,
    )

    assert result == 1
    assert "must be inside the repository" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "value", "message"),
    [
        ("--pr-number", "0", "positive integer"),
        ("--head-branch", "../invalid", "Invalid PR head branch"),
    ],
)
def test_write_back_rejects_invalid_pr_metadata(
    tmp_path, monkeypatch, capsys, flag, value, message
):
    worktree, _, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    module = _load_write_back_module()
    args = _args(original_sha)
    args[args.index(flag) + 1] = value

    result = module.main(
        args,
        environ=_environment(tmp_path / "output"),
        cwd=worktree,
    )

    assert result == 1
    assert message in capsys.readouterr().err
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == original_sha


def test_write_back_does_not_dispatch_when_push_fails(tmp_path, monkeypatch, capsys):
    worktree, remote, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    module = _load_write_back_module()
    monkeypatch.setattr(
        module,
        "_dispatch_repository",
        lambda **kwargs: pytest.fail("a rejected push must not dispatch"),
    )
    _git(worktree, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    result = module.main(
        _args(original_sha),
        environ=_environment(tmp_path / "output"),
        cwd=worktree,
    )

    assert result == 1
    assert "git failed" in capsys.readouterr().err
    assert (
        _git(remote, "rev-parse", "refs/heads/feature/write-back").stdout.strip()
        == original_sha
    )


def test_no_change_still_dispatches_current_head_for_safe_retry(
    tmp_path, monkeypatch
):
    worktree, _, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    monkeypatch.setenv("FAKE_MAIDA_NO_CHANGE", "1")
    module = _load_write_back_module()
    dispatched = []
    monkeypatch.setattr(
        module,
        "_dispatch_repository",
        lambda **kwargs: dispatched.append(kwargs),
    )
    output_path = tmp_path / "output"

    result = module.main(_args(original_sha), environ=_environment(output_path), cwd=worktree)

    assert result == 0
    assert _git(worktree, "rev-parse", "HEAD").stdout.strip() == original_sha
    assert dispatched[0]["payload"]["client_payload"]["sha"] == original_sha
    assert output_path.read_text().splitlines() == [
        "changed=false",
        "commit-sha=",
        f"head-sha={original_sha}",
    ]


def test_dispatch_failure_after_push_is_retryable(tmp_path, monkeypatch, capsys):
    worktree, remote, original_sha = _make_repository(tmp_path)
    _install_fake_maida(tmp_path, monkeypatch)
    module = _load_write_back_module()

    def fail_dispatch(**kwargs):
        raise module.WriteBackError("dispatch unavailable")

    monkeypatch.setattr(module, "_dispatch_repository", fail_dispatch)
    result = module.main(
        _args(original_sha),
        environ=_environment(tmp_path / "first-output"),
        cwd=worktree,
    )

    pushed_sha = _git(remote, "rev-parse", "refs/heads/feature/write-back").stdout.strip()
    assert result == 1
    assert pushed_sha != original_sha
    assert "baseline commit was pushed" in capsys.readouterr().err

    monkeypatch.setenv("FAKE_MAIDA_NO_CHANGE", "1")
    dispatched = []
    monkeypatch.setattr(
        module,
        "_dispatch_repository",
        lambda **kwargs: dispatched.append(kwargs),
    )
    result = module.main(
        _args(pushed_sha),
        environ=_environment(tmp_path / "retry-output"),
        cwd=worktree,
    )

    assert result == 0
    assert dispatched[0]["payload"]["client_payload"]["sha"] == pushed_sha


def test_repository_dispatch_sends_authenticated_json(monkeypatch):
    module = _load_write_back_module()
    captured = {}

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    payload = {
        "event_type": "maida_baseline_updated",
        "client_payload": {"sha": "abc"},
    }

    module._dispatch_repository(
        api_url="https://github.example/api/v3/",
        repository="owner/repo",
        token="secret-token",
        payload=payload,
    )

    assert captured == {
        "url": "https://github.example/api/v3/repos/owner/repo/dispatches",
        "headers": {
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer secret-token",
            "Content-type": "application/json",
            "X-github-api-version": "2022-11-28",
        },
        "payload": payload,
        "timeout": 30,
    }


def test_repository_dispatch_reports_http_status_without_token(monkeypatch):
    module = _load_write_back_module()

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            422,
            "Unprocessable Entity",
            {},
            io.BytesIO(b'{"message":"invalid"}'),
        )

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(module.WriteBackError) as exc_info:
        module._dispatch_repository(
            api_url="https://api.github.test",
            repository="owner/repo",
            token="secret-token",
            payload={"event_type": "maida_baseline_updated"},
        )

    assert "HTTP 422" in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)
