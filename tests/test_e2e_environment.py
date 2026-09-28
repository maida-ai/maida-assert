"""Run the E2E workflow command with an unrelated Python on the runner PATH."""

import os
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_e2e_workflow_activates_environment_for_child_commands(tmp_path):
    workflow = yaml.safe_load((ROOT / ".github/workflows/e2e.yml").read_text())
    steps = workflow["jobs"]["deterministic-e2e"]["steps"]
    run = next(s["run"] for s in steps
               if s.get("name") == "Exercise verdicts, comments, and acceptance")
    venv = tmp_path / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    # This pytest stand-in probes the subprocess lookup used by the E2E harness.
    (tmp_path / "pytest.py").write_text(
        "import subprocess, sys\n"
        "subprocess.run(['python', '-c', "
        "'import sys; assert sys.prefix == ' + repr(sys.prefix)], check=True)\n"
        "subprocess.run(['maida'], check=True)\n"
    )
    runner_bin = tmp_path / "runner-bin"
    runner_bin.mkdir()
    for directory, names, status in (
        (runner_bin, ("python", "maida"), 97),
        (venv / "bin", ("maida",), 0),
    ):
        for name in names:
            command = directory / name
            command.write_text(f"#!/bin/sh\nexit {status}\n")
            command.chmod(0o755)
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c",
         run.replace("${{ runner.temp }}", str(tmp_path))],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{runner_bin}:{os.environ['PATH']}"},
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
