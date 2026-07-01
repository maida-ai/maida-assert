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


def test_assert_failure_is_reported_before_action_fails():
    steps = _load_action()["runs"]["steps"]
    assert_step_index = next(
        index for index, step in enumerate(steps) if step.get("id") == "assert"
    )
    show_step_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Show PR comment in the action log"
    )
    post_step_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Post PR comment if possible"
    )
    fail_step_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Fail if assertions failed"
    )

    assert assert_step_index < show_step_index
    assert show_step_index < post_step_index < fail_step_index

    fail_step = steps[fail_step_index]
    assert fail_step["if"] == "steps.assert.outputs.assert_failed == 'true'"
    assert fail_step["run"] == "exit 1"


def test_action_report_uses_cli_generated_markdown_file():
    steps = _load_action()["runs"]["steps"]
    assert_step = next(step for step in steps if step.get("id") == "assert")
    show_step = next(
        step for step in steps if step.get("name") == "Show PR comment in the action log"
    )
    post_step = next(
        step for step in steps if step.get("name") == "Post PR comment if possible"
    )

    assert "maida assert $ARGS --format markdown > maida-report.md" in assert_step["run"]
    assert "cat maida-report.md" in show_step["run"]
    assert post_step["with"]["path"] == "maida-report.md"


def test_report_adds_local_reproduction_hint_before_posting():
    steps = _load_action()["runs"]["steps"]
    append_step_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Add local reproduction hint"
    )
    show_step_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Show PR comment in the action log"
    )
    post_step_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Post PR comment if possible"
    )

    assert append_step_index < show_step_index < post_step_index

    append_step = steps[append_step_index]
    script = append_step["run"]
    assert "Reproducibility Instructions" in script
    assert "CI job's trace store" in script
    assert 'printf "python -m pip install %q\\n"' in script
    assert "git+https://github.com/maida-ai/maida.git${MAIDA_VERSION}" in script
    assert "maida-ai==${MAIDA_VERSION:1}" in script
    assert 'printf "python %q\\n" "$AGENT_SCRIPT"' in script
    assert 'printf "maida assert"' in script
    assert 'printf " --baseline %q" "$BASELINE"' in script
    assert 'printf " --policy %q" "$POLICY"' in script
    assert 'printf " %s" "$EXTRA_ARGS"' in script
    assert ">> maida-report.md" in script


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


def test_readme_workflows_use_current_action_version():
    readme = (REPO_ROOT / "README.md").read_text()
    assert "maida-ai/maida-assert@V4" in readme
    assert "maida-ai/maida-assert@v1" not in readme
    assert "maida-ai/maida-assert@v2" not in readme
    assert "maida-ai/maida-assert@V3" not in readme


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
