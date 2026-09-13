"""Execute the comment shell fragment to catch accidental command substitution."""

import os
import subprocess
from pathlib import Path

import yaml


def test_accept_hint_preserves_literal_command(tmp_path):
    action = yaml.safe_load((Path(__file__).parents[1] / "action.yml").read_text())
    step = next(
        s for s in action["runs"]["steps"] if s["name"] == "Add local reproduction hint"
    )
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]],
        check=False,
        cwd=tmp_path,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(tmp_path),
            "AGENT_SCRIPT": "agent.py",
            "TRACE_COMMAND": "",
            "BASELINE": "baseline.json",
            "POLICY": "policy.yaml",
            "EXTRA_ARGS": "",
            "MAIDA_VERSION": "v0.5.3",
            "ACCEPT_COMMAND_ENABLED": "true",
        },
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert (
        "comment `/maida accept [optional reason]` on this PR"
        in (tmp_path / "maida-report.md").read_text()
    )
