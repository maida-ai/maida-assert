"""Run the smoke verifier with GitHub's rewritten check URL and stale checks."""

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "case", ["new", "stale", "wrong_report", "wrong_head", "failed", "duplicate"]
)
def test_smoke_requires_a_new_successful_check_for_this_report(tmp_path, case):
    expected = {
        "name": "Maida statistical gate",
        "head_sha": "a" * 40,
        "conclusion": "success",
        "details_url": "https://github.example/fixture/repo/actions/runs/100",
        "output": {
            "title": "PASS",
            "summary": "PASS\nWorkflow: https://github.example/fixture/repo/actions/runs/100",
        },
    }
    actual = {
        **copy.deepcopy(expected),
        "id": 11,
        "details_url": "https://github.example/fixture/repo/runs/900",
    }
    before = []
    if case == "stale":
        before = [actual]
    if case == "wrong_report":
        actual["output"]["summary"] = "Report from an earlier attempt"
    if case == "wrong_head":
        actual["head_sha"] = "b" * 40
    if case == "failed":
        actual["conclusion"] = "failure"
    after = [actual, {**actual, "id": 12}] if case == "duplicate" else [actual]
    for name, data in (
        ("checks-before.json", [{"check_runs": before}]),
        ("after.json", [{"check_runs": after}]),
        ("maida-check-payload.json", expected),
    ):
        (tmp_path / name).write_text(json.dumps(data))
    gh = tmp_path / "gh"
    gh.write_text('#!/bin/sh\ncat "$CHECKS_FIXTURE"\n')
    gh.chmod(0o755)
    workflow = yaml.safe_load((ROOT / ".github/workflows/e2e.yml").read_text())
    step = workflow["jobs"]["live-smoke"]["steps"][-1]
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
        env={
            "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "CHECKS_FIXTURE": str(tmp_path / "after.json"),
            "GITHUB_REPOSITORY": "fixture/repo",
            "GITHUB_SERVER_URL": "https://github.example",
            "GITHUB_RUN_ID": "100",
            "GITHUB_SHA": "a" * 40,
        },
    )
    assert (result.returncode == 0) is (case == "new"), result.stdout + result.stderr
