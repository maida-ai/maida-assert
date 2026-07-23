"""Structural tests for action.yml and public repo text."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ACTION_PATH = REPO_ROOT / "action.yml"
README_PATH = REPO_ROOT / "README.md"
CI_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_action():
    return yaml.safe_load(ACTION_PATH.read_text())


def _readme_yaml_blocks():
    readme = README_PATH.read_text()
    blocks = []
    in_yaml = False
    current = []
    for line in readme.splitlines():
        if line == "```yaml":
            in_yaml = True
            current = []
            continue
        if line == "```" and in_yaml:
            in_yaml = False
            blocks.append("\n".join(current))
            continue
        if in_yaml:
            current.append(line)
    return blocks


def test_composite_action_type():
    action = _load_action()
    assert action["runs"]["using"] == "composite"


def test_required_inputs_present():
    inputs = _load_action()["inputs"]
    assert "agent-script" in inputs
    assert inputs["agent-script"]["required"] is True


def test_optional_inputs_have_defaults():
    inputs = _load_action()["inputs"]
    for name in (
        "baseline",
        "policy",
        "maida-version",
        "python-version",
        "extra-args",
        "post-comment",
    ):
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
    """`maida run` owns execution and report generation without ID plumbing."""
    steps = _load_action()["runs"]["steps"]
    for step in steps:
        assert step.get("id") != "get-run"
        assert step.get("name") != "Get latest run ID"


def test_gate_step_uses_run_command_and_json_sidecar():
    steps = _load_action()["runs"]["steps"]
    gate_step = next(step for step in steps if step.get("id") == "gate")
    script = gate_step["run"]
    assert 'maida run "$AGENT_SCRIPT" "${ARGS[@]}"' in script
    assert "--format markdown --json-out maida-report.json" in script
    assert "> maida-report.md" in script
    assert "steps.get-run" not in script
    assert not any(step.get("name") == "Run agent" for step in steps)


def test_gate_step_distinguishes_failure_from_error():
    """Exit 1 is a reportable FAIL; setup/internal errors fail immediately."""
    steps = _load_action()["runs"]["steps"]
    gate_step = next(step for step in steps if step.get("id") == "gate")
    script = gate_step["run"]
    assert "status=$?" in script
    assert 'if [ "$status" -ne 0 ] && [ "$status" -ne 1 ]; then' in script
    assert 'exit "$status"' in script
    assert "did not produce a statistical gate report" in script


def test_gate_failure_is_reported_before_action_fails():
    steps = _load_action()["runs"]["steps"]
    gate_step_index = next(
        index for index, step in enumerate(steps) if step.get("id") == "gate"
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

    check_step_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Publish Maida statistical gate check"
    )

    assert gate_step_index < show_step_index
    assert show_step_index < post_step_index < fail_step_index
    assert check_step_index < fail_step_index

    fail_step = steps[fail_step_index]
    assert fail_step["if"] == "steps.check.outputs.verdict == 'fail'"
    assert fail_step["run"] == "exit 1"


def test_action_report_uses_cli_generated_markdown_file():
    steps = _load_action()["runs"]["steps"]
    gate_step = next(step for step in steps if step.get("id") == "gate")
    show_step = next(
        step
        for step in steps
        if step.get("name") == "Show PR comment in the action log"
    )
    post_step = next(
        step for step in steps if step.get("name") == "Post PR comment if possible"
    )

    assert "> maida-report.md" in gate_step["run"]
    assert "cat maida-report.md" in show_step["run"]
    assert post_step["with"]["path"] == "maida-report.md"


def test_action_prepares_and_publishes_stable_check_payload():
    steps = _load_action()["runs"]["steps"]
    prepare_step = next(step for step in steps if step.get("id") == "check")
    publish_step = next(
        step
        for step in steps
        if step.get("name") == "Publish Maida statistical gate check"
    )
    warning_step = next(
        step
        for step in steps
        if step.get("name") == "Warn when check publication is unavailable"
    )

    assert "scripts/prepare_check.py" in prepare_step["run"]
    assert "maida-report.json" in prepare_step["run"]
    assert "maida-check-payload.json" in prepare_step["run"]
    assert publish_step["continue-on-error"] is True
    assert "check-runs" in publish_step["run"]
    assert "maida-check-payload.json" in publish_step["run"]
    assert warning_step["if"] == "steps.publish-check.outcome == 'failure'"
    assert "::warning::" in warning_step["run"]


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
    assert 'printf "maida run %q" "$AGENT_SCRIPT"' in script
    assert 'printf " --baseline %q" "$BASELINE"' in script
    assert 'printf " --policy %q" "$POLICY"' in script
    assert 'printf " %s" "$EXTRA_ARGS"' in script
    assert 'if [ -n "$BASELINE" ]; then' in script
    assert "If the local behavior change is intentional" in script
    assert 'printf "maida diff <local-id> --baseline %q\\n" "$BASELINE"' in script
    assert (
        'printf "maida accept <local-id> --baseline %q --reason %q\\n" "$BASELINE" "why this behavior is expected"'
        in script
    )
    assert 'printf "git diff -- %q\\n" "$BASELINE"' in script
    assert ">> maida-report.md" in script


def test_pypi_install_uses_maida_ai_package():
    steps = _load_action()["runs"]["steps"]
    install_step = next(step for step in steps if step.get("name") == "Install Maida")
    script = install_step["run"]
    assert "maida-ai==${MAIDA_VERSION:1}" in script
    assert "maida==${MAIDA_VERSION:1}" not in script


def test_maida_version_description_documents_run_command_coupling():
    description = _load_action()["inputs"]["maida-version"]["description"]
    assert "statistical `maida run` command" in description
    assert "Use '@main' until" in description


def test_readme_uses_maida_ai_package_for_local_install():
    readme = (REPO_ROOT / "README.md").read_text()
    assert (
        'uv add "maida-ai @ git+https://github.com/maida-ai/maida.git@main"' in readme
    )
    assert "uv add maida\n" not in readme


def test_readme_workflows_use_current_action_version():
    readme = README_PATH.read_text()
    assert "maida-ai/maida-assert@V4" in readme
    assert "maida-ai/maida-assert@v1" not in readme
    assert "maida-ai/maida-assert@v2" not in readme
    assert "maida-ai/maida-assert@V3" not in readme


def test_readme_describes_current_pr_comment_contract():
    readme = README_PATH.read_text()
    assert "pass/fail/inconclusive verdict" in readme
    assert "top behavior changes" in readme
    assert "stable reason code" in readme
    assert "concise\nnext steps" in readme
    assert "`maida accept --reason ...` path" in readme


def test_readme_documents_baseline_acceptance_workflow():
    readme = README_PATH.read_text()
    assert "intentional behavior change" in readme
    assert "maida diff --baseline baselines/my_agent.json" in readme
    assert (
        'maida accept --baseline baselines/my_agent.json --reason "expected tool flow change"'
        in readme
    )
    assert "git diff baselines/my_agent.json" in readme
    assert "previous baseline hash" in readme
    assert (
        "Do not use `maida accept` for a\nregression you have not inspected" in readme
    )


def test_readme_pr_workflows_declare_minimal_permissions():
    pr_workflows = [
        block for block in _readme_yaml_blocks() if "on: [pull_request]" in block
    ]
    assert pr_workflows
    for block in pr_workflows:
        assert (
            "permissions:\n  contents: read\n  checks: write\n  pull-requests: write"
            in block
        )


def test_readme_no_comment_workflow_omits_pr_write_permission():
    no_comment_workflows = [
        block for block in _readme_yaml_blocks() if "post-comment: 'false'" in block
    ]
    assert len(no_comment_workflows) == 1
    workflow = no_comment_workflows[0]
    assert "permissions:\n  contents: read\n  checks: write" in workflow
    assert "pull-requests: write" not in workflow


def test_ci_workflow_uses_minimal_job_permissions():
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text())
    jobs = workflow["jobs"]
    assert jobs["unit-tests"]["permissions"] == {"contents": "read"}
    assert jobs["integration"]["permissions"] == {
        "contents": "read",
        "checks": "write",
        "pull-requests": "write",
    }


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
