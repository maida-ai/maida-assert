"""The writer consumes data in isolation and rejects its retired interface."""

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_retired_writer_fails_without_executing_candidate(
    tmp_path, monkeypatch, capsys
):
    spec = importlib.util.spec_from_file_location(
        "legacy_writer", ROOT / "scripts/write_back.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import subprocess

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("candidate execution")),
    )
    assert (
        module.main(
            ["--baseline", "baseline.json"],
            cwd=tmp_path,
            environ={"GITHUB_TOKEN": "write-secret"},
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "three-job" in error
    assert "write-secret" not in error


def test_write_back_action_consumes_only_artifact_and_trusted_context():
    action = yaml.safe_load((ROOT / "write-back/action.yml").read_text())
    assert set(action["inputs"]) == {"context", "artifact-directory", "github-token"}
    assert all(value["required"] for value in action["inputs"].values())
    steps = action["runs"]["steps"]
    assert steps[0]["id"] == "write-back"
    assert "python3 -I" in steps[0]["run"]
    assert "accept_artifact.py" in steps[0]["run"]
    assert all("uses" not in step for step in steps)
    assert steps[1]["if"] == "always()"
    assert set(action["outputs"]) == {"changed", "commit-sha", "head-sha"}
