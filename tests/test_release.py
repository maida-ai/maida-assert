"""Test the archive interface locally; signing still requires a GitHub runner."""

import hashlib
import os
from pathlib import Path
import subprocess
import tarfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", ["valid", "branch", "wrong-commit"])
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
    env = {**os.environ, "GITHUB_SHA": commit, "GITHUB_REF": "refs/tags/v1.2.3"}
    if case == "branch":
        env["GITHUB_REF"] = "refs/heads/main"
    if case == "wrong-commit":
        env["GITHUB_SHA"] = "0" * 40
    command = ["bash", str(ROOT / "scripts/build_release.sh"), str(output)]
    result = subprocess.run(command, cwd=source, env=env, text=True, capture_output=True)
    if case != "valid":
        assert result.returncode != 0
        assert "::error::" in result.stderr
        assert not output.exists()
        return
    assert result.returncode == 0, result.stderr
    archive = output / "maida-assert.tar.gz"
    contents = archive.read_bytes()
    digest = hashlib.sha256(contents).hexdigest()
    assert (output / "SHA256SUMS").read_text() == f"{digest}  maida-assert.tar.gz\n"
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ["maida-assert", "maida-assert/action.yml"]
    subprocess.run(command, cwd=source, env=env, check=True)
    assert archive.read_bytes() == contents


def test_release_attests_and_verifies_before_publishing():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert workflow.get("on", workflow.get(True)) == {"push": {"tags": ["v*"]}}
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["release"]
    assert job["needs"] == "test"
    assert job["permissions"] == {
        "contents": "write", "id-token": "write", "attestations": "write",
    }
    steps = job["steps"]
    attest = next(i for i, s in enumerate(steps) if s.get("id") == "attest")
    verify = next(i for i, s in enumerate(steps) if s.get("name") == "Verify provenance")
    publish = next(i for i, s in enumerate(steps) if s.get("name") == "Publish release assets")
    assert attest < verify < publish
    assert steps[attest]["with"]["subject-path"] == "${{ runner.temp }}/release/maida-assert.tar.gz"
    assert "--source-digest" in steps[verify]["run"]
    assert "--signer-workflow" in steps[verify]["run"]
    assert "SHA256SUMS" in steps[publish]["run"]
    assert "--verify-tag" in steps[publish]["run"]
    assert not any(s.get("continue-on-error") for s in steps)
