"""Structural tests for action.yml and public repo text."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_PATH = REPO_ROOT / "action.yml"


def _load_action():
    return yaml.safe_load(ACTION_PATH.read_text())


def test_composite_action_type():
    action = _load_action()
    assert action["runs"]["using"] == "composite"


def test_required_inputs_present():
    inputs = _load_action()["inputs"]
    assert "agent-script" in inputs
    assert inputs["agent-script"]["required"] is True


def test_optional_inputs_have_defaults():
    inputs = _load_action()["inputs"]
    for name in ("baseline", "policy", "maida-version", "python-version",
                 "extra-args", "post-comment"):
        assert name in inputs, f"missing input: {name}"
        assert "default" in inputs[name], f"{name} has no default"
    assert "agent" + "dbg-version" not in inputs


def test_steps_non_empty():
    steps = _load_action()["runs"]["steps"]
    assert isinstance(steps, list) and len(steps) > 0


def test_no_broken_baseline_generation_step():
    """The old 'Create baseline if not provided' step expanded --out to an
    empty string when baseline was omitted. It should no longer exist."""
    steps = _load_action()["runs"]["steps"]
    for step in steps:
        assert step.get("name") != "Create baseline if not provided"


def test_no_run_id_extraction_step():
    """`maida assert` defaults to the latest run, so the old 'Get latest
    run ID' step (a maida list --json | python -c pipeline) must be gone."""
    steps = _load_action()["runs"]["steps"]
    for step in steps:
        assert step.get("id") != "get-run"
        assert step.get("name") != "Get latest run ID"


def test_assert_step_runs_without_run_id():
    steps = _load_action()["runs"]["steps"]
    assert_step = next(step for step in steps if step.get("id") == "assert")
    script = assert_step["run"]
    assert "maida assert $ARGS --format markdown" in script
    assert "steps.get-run" not in script


def test_assert_step_distinguishes_failure_from_error():
    """Exit 1 (checks failed) posts the report; other non-zero exits
    (no run recorded, internal error) fail the step immediately."""
    steps = _load_action()["runs"]["steps"]
    assert_step = next(step for step in steps if step.get("id") == "assert")
    script = assert_step["run"]
    assert "assert_failed=true" in script
    assert 'exit "$status"' in script
    assert "No Maida run found" in script


def test_pypi_install_uses_maida_ai_package():
    steps = _load_action()["runs"]["steps"]
    install_step = next(
        step for step in steps if step.get("name") == "Install Maida"
    )
    script = install_step["run"]
    assert "maida-ai==${MAIDA_VERSION:1}" in script
    assert "maida==${MAIDA_VERSION:1}" not in script


def test_readme_uses_maida_ai_package_for_local_install():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "uv add maida-ai" in readme
    assert "uv add maida\n" not in readme


def test_public_files_use_current_branding():
    forbidden = (
        "Agent" + "Dbg",
        "agent" + "dbg",
        "agent" + "-dbg",
        "Ref" + "ine",
        "Ref" + "ineHQ",
        "ref" + "inehq",
        "ref" + "inehq.ai",
        ".agent" + "dbg",
        "AGENT" + "DBG",
    )
    skipped_dirs = {".git", ".pytest_cache", "__pycache__"}
    skipped_files = {"AGENTS.md"}

    leaks = []
    for path in sorted(REPO_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative_path = path.relative_to(REPO_ROOT)
        if relative_path.name in skipped_files:
            continue
        if any(part in skipped_dirs for part in relative_path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        for term in forbidden:
            if term in text:
                leaks.append(f"{relative_path} contains {term}")

    assert leaks == []
