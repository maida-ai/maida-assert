"""Exercise each composite installation step without contacting package indexes."""

import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("directory", [".", "capture-acceptance"])
@pytest.mark.parametrize("version", ["default", "v9.9.9", "invalid"])
def test_installation_lock_and_explicit_override(tmp_path, directory, version):
    action_path = ROOT / directory
    action = yaml.safe_load((action_path / "action.yml").read_text())
    step = next(s for s in action["runs"]["steps"] if s.get("name") == "Install Maida")
    selected = action["inputs"]["maida-version"]["default"] if version == "default" else version
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    log = tmp_path / "arguments.json"
    for name in ("python", "uv"):
        executable = bin_path / name
        executable.write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            "from pathlib import Path\n"
            "Path(os.environ['ARGUMENT_LOG']).write_text(json.dumps(sys.argv[1:]))\n"
        )
        executable.chmod(0o755)
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", step["run"]],
        cwd=tmp_path, text=True, capture_output=True,
        env={**os.environ, "PATH": f"{bin_path}:{os.environ['PATH']}",
             "GITHUB_ACTION_PATH": str(action_path), "MAIDA_VERSION": selected,
             "ARGUMENT_LOG": str(log)},
    )
    if version == "invalid":
        assert result.returncode != 0
        assert not log.exists()
        return
    assert result.returncode == 0, result.stderr
    arguments = json.loads(log.read_text())
    if version == "default":
        assert "--require-hashes" in arguments
        assert "--only-binary=:all:" in arguments
        lock = Path(arguments[arguments.index("-r") + 1])
        assert lock.resolve() == ROOT / "requirements-maida.lock"
        assert f"maida-ai=={selected[1:]} " in lock.read_text()
    else:
        assert f"maida-ai=={selected[1:]}" in arguments
        assert "::warning::" in result.stdout
        assert "lock" in result.stdout.lower()
