"""Test the archive interface locally; signing still requires a GitHub runner."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tomllib

from packaging.specifiers import SpecifierSet
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", [
    "valid", "valid-zero", "valid-rc", "valid-rc-zero", "branch", "wrong-commit", "major-alias",
    "minor-alias", "uppercase", "leading-zero", "metadata", "semver-prerelease",
    "empty-rc", "leading-zero-rc", "negative-rc", "alpha", "rc-metadata",
])
def test_release_archive(tmp_path, case):
    source = tmp_path / "source"
    source.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()
    git("init", "--quiet")
    (source / "action.yml").write_text("name: fixture\n")
    git("add", "action.yml")
    git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Fixture")
    commit = git("rev-parse", "HEAD")
    (source / "untracked-secret").write_text("must not ship")
    output = tmp_path / "release output"
    outputs = tmp_path / "step-output"
    env = {**os.environ, "GITHUB_SHA": commit, "GITHUB_REF": "refs/tags/v1.2.3",
           "GITHUB_OUTPUT": str(outputs)}
    if case == "valid-zero":
        env["GITHUB_REF"] = "refs/tags/v0.0.0"
    if case in {"valid-rc", "valid-rc-zero"}:
        env["GITHUB_REF"] = "refs/tags/v0.0.0rc1" if case == "valid-rc" else "refs/tags/v0.0.0rc0"
    if case == "branch":
        env["GITHUB_REF"] = "refs/heads/main"
    if case == "wrong-commit":
        env["GITHUB_SHA"] = "0" * 40
    rejected_tags = {
        "major-alias": "v5", "minor-alias": "v0.1", "uppercase": "V4",
        "leading-zero": "v01.2.3", "metadata": "v0.0.0.post1",
        "semver-prerelease": "v0.0.0-rc.1", "empty-rc": "v0.0.0rc",
        "leading-zero-rc": "v0.0.0rc01", "negative-rc": "v0.0.0rc-1",
        "alpha": "v0.0.0a1", "rc-metadata": "v0.0.0rc1.post1",
    }
    if case in rejected_tags:
        env["GITHUB_REF"] = f"refs/tags/{rejected_tags[case]}"
    command = ["bash", str(ROOT / "scripts/build_release.sh"), str(output)]
    result = subprocess.run(command, cwd=source, env=env, text=True, capture_output=True)
    if not case.startswith("valid"):
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert not output.exists()
        assert not outputs.exists()
        return
    assert result.returncode == 0, result.stderr
    assert outputs.read_text() == f"prerelease={'true' if case.startswith('valid-rc') else 'false'}\n"
    archive = output / "maida-assert.tar.gz"
    contents = archive.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    assert (output / "SHA256SUMS").read_text() == f"{digest}  maida-assert.tar.gz\n"
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ["maida-assert", "maida-assert/action.yml"]
    subprocess.run(command, cwd=source, env=env, check=True)
    assert archive.read_bytes() == contents


def test_release_attests_and_verifies_before_creating_draft():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert workflow.get("on", workflow.get(True)) == {"push": {"tags": ["v*.*.*"]}}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["release"]
    assert job["needs"] == "test"
    assert job["permissions"] == {
        "contents": "write", "id-token": "write", "attestations": "write",
    }
    steps = job["steps"]
    build = next(i for i, s in enumerate(steps) if s.get("id") == "archive")
    attest = next(i for i, s in enumerate(steps) if s.get("id") == "attest")
    verify = next(i for i, s in enumerate(steps) if s.get("name") == "Verify provenance")
    publish = next(i for i, s in enumerate(steps) if s.get("name") == "Create draft release with verified assets")
    assert build < attest < verify < publish
    assert steps[attest]["with"]["subject-path"] == "${{ runner.temp }}/release/maida-assert.tar.gz"
    assert "--source-digest" in steps[verify]["run"]
    assert "--signer-workflow" in steps[verify]["run"]
    assert "SHA256SUMS" in steps[publish]["run"]
    assert "--verify-tag" in steps[publish]["run"]
    assert steps[publish]["env"]["IS_PRERELEASE"] == "${{ steps.archive.outputs.prerelease }}"
    assert not any(s.get("continue-on-error") for s in steps)


@pytest.mark.parametrize("classification", ["true", "false", "", "invalid", "missing-notes"])
def test_draft_marks_only_release_candidates_as_prereleases(tmp_path, classification):
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    step = next(s for s in workflow["jobs"]["release"]["steps"] if s.get("name") == "Create draft release with verified assets")
    release = tmp_path / "release"
    release.mkdir()
    archive = release / "maida-assert.tar.gz"
    archive.write_bytes(b"fixture archive")
    (release / "SHA256SUMS").write_text(f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  maida-assert.tar.gz\n")
    bundle = tmp_path / "bundle.jsonl"
    bundle.write_text("fixture attestation\n")
    (tmp_path / "CHANGELOG.md").write_text(
        "# Changelog\n\n## v0.0.0\n\nStable release notes.\n\n## v0.0.0rc1\n\nRC notes.\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    arguments = tmp_path / "arguments.json"
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
        "Path(os.environ['ARGUMENT_LOG']).write_text(json.dumps(sys.argv[1:]))\n"
    )
    gh.chmod(0o755)
    tag = {
        "true": "v0.0.0rc1",
        "missing-notes": "v9.9.9",
    }.get(classification, "v0.0.0")
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path, text=True, capture_output=True,
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}",
             "ARGUMENT_LOG": str(arguments), "RUNNER_TEMP": str(tmp_path),
             "GITHUB_WORKSPACE": str(tmp_path),
             "ATTESTATION_BUNDLE": str(bundle), "GITHUB_REF_NAME": tag,
             "GITHUB_REPOSITORY": "fixture/action",
             "IS_PRERELEASE": "false" if classification == "missing-notes" else classification},
    )
    if classification not in {"true", "false"}:
        assert result.returncode != 0
        assert not arguments.exists()
        if classification == "missing-notes":
            assert "CHANGELOG.md has no notes for v9.9.9" in result.stderr
        return
    assert result.returncode == 0, result.stderr
    args = json.loads(arguments.read_text())
    assert args[:3] == ["release", "create", tag]
    assert "--verify-tag" in args
    assert "--draft" in args
    assert ("--prerelease" in args) is (classification == "true")
    assert ("--latest=false" in args) is (classification == "true")
    if classification == "false":
        assert "--notes-file" in args
        assert "Stable release notes." in (release / "release-notes.md").read_text()
        assert "RC notes." not in (release / "release-notes.md").read_text()
        assert "--generate-notes" not in args
    else:
        assert "--generate-notes" in args
    assert args[-3:] == ["maida-assert.tar.gz", "SHA256SUMS", "provenance.jsonl"]


def test_tooling_and_lock_require_supported_python():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    required = project["project"]["requires-python"]
    assert SpecifierSet(lock["requires-python"]) == SpecifierSet(required)
    versions = SpecifierSet(required)
    assert all(version in versions for version in ("3.12", "3.13", "3.14"))
    assert all(version not in versions for version in ("3.10", "3.11", "3.15"))
