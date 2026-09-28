"""First-run failures stay actionable even without a publishable CLI report."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("stage,expected", [
    ("VALIDATION", "exactly one"),
    ("TRUST", "trusted base"),
    ("INSTALL", "locked Maida installation"),
    ("GATE", "exactly one completed"),
    ("IDENTITY", "latest PR head"),
    ("CHECK", "report schema"),
    ("PUBLICATION", "checks: write"),
])
def test_failure_summary_survives_absent_or_stale_report(tmp_path, stage, expected):
    action = yaml.safe_load((ROOT / "action.yml").read_text())
    step = next(s for s in action["runs"]["steps"] if s["name"] == "Summarize the next safe action")
    assert step["if"] == "always()"
    # A stale PASS must never mask a setup error in a subsequent invocation.
    (tmp_path / "maida-check-payload.json").write_text(json.dumps({
        "output": {"summary": "STALE PASS"}, "conclusion": "success",
    }))
    summary = tmp_path / "summary.md"
    stages = ["VALIDATION", "TRUST", "INSTALL", "GATE", "IDENTITY", "CHECK", "PUBLICATION"]
    outcomes = {name: ("success" if stages.index(name) < stages.index(stage) else "skipped") for name in stages}
    outcomes[stage] = "failure"
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]], cwd=tmp_path, text=True,
        capture_output=True, timeout=10, check=False,
        env={"PATH": os.environ["PATH"], "GITHUB_ACTION_PATH": str(ROOT),
             "GITHUB_STEP_SUMMARY": str(summary), **outcomes},
    )
    assert result.returncode == 0, result.stderr
    text = summary.read_text()
    assert "### Next safe action" in text and expected in text
    assert "STALE PASS" not in text
    assert "No merge authorization" in text


def test_success_summary_preserves_validated_next_action(tmp_path):
    (tmp_path / "maida-check-payload.json").write_text(json.dumps({
        "output": {"summary": "Overall verdict: PASS\n\n### Next safe action\n\nReview coverage."},
    }))
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["python3", "-I", str(ROOT / "scripts/workflow_summary.py")], cwd=tmp_path,
        capture_output=True, text=True, check=False,
        env={"PATH": os.environ["PATH"], "GITHUB_STEP_SUMMARY": str(summary),
             **{stage: "success" for stage in ("VALIDATION", "TRUST", "INSTALL", "GATE", "IDENTITY", "CHECK", "PUBLICATION")}},
    )
    assert result.returncode == 0, result.stderr
    assert "Review coverage." in summary.read_text()
