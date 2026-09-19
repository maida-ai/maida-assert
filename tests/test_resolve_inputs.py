"""Trusted configuration selection against real, disposable Git histories."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / ".maida").mkdir()
    (root / ".maida/policy.yaml").write_text("trusted policy\n")
    (root / "baseline.json").write_text('{"trusted": true}\n')
    (root / "agent.py").write_text("pass\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Base")
    base = git(root, "rev-parse", "HEAD")
    return root, base


def resolve(repo, tmp_path, **kwargs):
    root, base = repo
    spec = importlib.util.spec_from_file_location("resolve_inputs", ROOT / "scripts/resolve_inputs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    options = dict(
        workspace=root, destination=tmp_path / "trusted", mode="blocking",
        event={"pull_request": {"base": {"sha": base, "repo": {"full_name": "fixture/repo"}},
                                "head": {"sha": git(root, "rev-parse", "HEAD")}}},
        event_name="pull_request", repository="fixture/repo", policy=".maida/policy.yaml",
        baseline="baseline.json", extra_args="", acceptance="",
    )
    options.update(kwargs)
    return module.resolve(**options)


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-m", "Candidate")


@pytest.mark.parametrize("change", ["weaken", "delete", "baseline", "add"])
def test_candidate_configuration_cannot_grade_itself(repo, tmp_path, change):
    root, _ = repo
    if change == "weaken":
        (root / ".maida/policy.yaml").write_text("weakened policy\n")
    elif change == "delete":
        (root / ".maida/policy.yaml").unlink()
    elif change == "baseline":
        (root / "baseline.json").write_text("candidate baseline\n")
    else:
        (root / ".maida/extra.yaml").write_text("new config\n")
    commit(root)
    context = resolve(repo, tmp_path)
    assert Path(context["policy"]).read_text() == "trusted policy\n"
    assert Path(context["baseline"]).read_text() == '{"trusted": true}\n'
    assert context["configuration_blocked"]


def test_unchanged_configuration_allows_evaluation(repo, tmp_path):
    context = resolve(repo, tmp_path)
    assert not context["configuration_blocked"]
    assert context["configuration_status"] == "unchanged"


def test_missing_base_and_dirty_checkout_fail_closed(repo, tmp_path):
    root, _ = repo
    (root / "agent.py").write_text("changed\n")
    with pytest.raises(ValueError, match="clean checkout"):
        resolve(repo, tmp_path)
    git(root, "restore", "agent.py")
    with pytest.raises(ValueError, match="fetch-depth"):
        resolve((root, "f" * 40), tmp_path)


def test_explicit_acceptance_binds_base_head_and_configuration(repo, tmp_path):
    root, base = repo
    (root / "baseline.json").write_text('accepted baseline\n')
    (root / ".maida/policy.yaml").write_text('candidate policy\n')
    commit(root)
    initial = resolve(repo, tmp_path)
    accepted = resolve(repo, tmp_path, acceptance=initial["acceptance"])
    assert not accepted["configuration_blocked"]
    assert Path(accepted["baseline"]).read_text() == "accepted baseline\n"
    assert Path(accepted["policy"]).read_text() == "trusted policy\n"
    (root / "agent.py").write_text("new commit\n")
    commit(root)
    with pytest.raises(ValueError, match="Stale or invalid"):
        resolve(repo, tmp_path, acceptance=initial["acceptance"])
    # Advancing the base invalidates acceptance even without another head update.
    with pytest.raises(ValueError, match="Stale or invalid"):
        resolve((root, initial["head_sha"]), tmp_path, acceptance=initial["acceptance"])


@pytest.mark.parametrize("kwargs,message", [
    ({"extra_args": "--policy weaker.yaml"}, "extra-args"),
    ({"extra_args": "--max-steps 999"}, "extra-args"),
    ({"policy": "../policy.yaml"}, "repository-relative"),
    ({"policy": "/tmp/policy.yaml"}, "repository-relative"),
    ({"policy": ""}, "repository-relative"),
    ({"mode": "unknown"}, "mode"),
    ({"event_name": "pull_request_target"}, "pull_request event"),
    ({"event_name": "repository_dispatch"}, "pull_request event"),
    ({"event": {}}, "identity"),
    ({"policy": "missing.yaml"}, "trusted base"),
])
def test_unsafe_or_ambiguous_inputs_are_rejected(repo, tmp_path, kwargs, message):
    with pytest.raises(ValueError, match=message):
        resolve(repo, tmp_path, **kwargs)


def test_configuration_symlink_fails_closed(repo, tmp_path):
    root, _ = repo
    (root / ".maida/link").symlink_to("../baseline.json")
    commit(root)
    with pytest.raises(ValueError, match="regular files"):
        resolve(repo, tmp_path)


def test_report_only_uses_candidate_inputs_and_requires_files(repo, tmp_path):
    context = resolve(repo, tmp_path, mode="report-only", event_name="push")
    assert context["policy"] == ".maida/policy.yaml"
    assert not context["configuration_blocked"]
    with pytest.raises(ValueError, match="does not exist"):
        resolve(repo, tmp_path, mode="report-only", policy="missing.yaml")


def test_untracked_configuration_is_not_silently_used(repo, tmp_path):
    root, _ = repo
    (root / ".maida/config.yaml").write_text("unreviewed config\n")
    with pytest.raises(ValueError, match="untracked Maida"):
        resolve(repo, tmp_path)


def test_wrong_checkout_cannot_publish_against_requested_head(repo, tmp_path):
    root, base = repo
    event = {"pull_request": {"base": {"sha": base, "repo": {"full_name": "fixture/repo"}},
                              "head": {"sha": "f" * 40}}}
    with pytest.raises(ValueError, match="Check out"):
        resolve(repo, tmp_path, event=event)
