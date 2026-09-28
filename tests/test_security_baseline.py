"""Repository security configuration must remain explicit and least-privilege."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_yaml(path):
    # BaseLoader preserves GitHub's `on` key instead of parsing it as a boolean.
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def test_all_workflows_declare_read_only_defaults_and_explicit_job_grants():
    expected_grants = {
        ("ci.yml", "integration"): {
            "contents": "read", "checks": "write", "pull-requests": "write",
        },
        ("e2e.yml", "live-smoke"): {"contents": "read", "checks": "write"},
        ("codeql.yml", "analyze"): {"contents": "read", "security-events": "write"},
        ("release.yml", "release"): {
            "contents": "write", "id-token": "write", "attestations": "write",
        },
    }
    for path in (ROOT / ".github/workflows").glob("*.y*ml"):
        workflow = load_yaml(path)
        assert workflow["permissions"] == {"contents": "read"}, path.name
        for name, job in workflow["jobs"].items():
            grants = job["permissions"]
            assert grants == expected_grants.get((path.name, name), {"contents": "read"}), (path.name, name)


def test_dependabot_covers_python_and_all_composite_actions():
    config = load_yaml(ROOT / ".github/dependabot.yml")
    assert config["version"] == "2"
    updates = {item["package-ecosystem"]: item for item in config["updates"]}
    assert set(updates) == {"uv", "github-actions"}
    assert updates["uv"]["directory"] == "/"
    action_dirs = {"/"} | {
        "/" + path.parent.name for path in ROOT.glob("*/action.yml")
    }
    assert action_dirs <= set(updates["github-actions"]["directories"])
    for update in updates.values():
        assert update["schedule"]["interval"] == "weekly"


def test_codeql_scans_code_and_workflows_without_executing_candidate_scripts():
    workflow = load_yaml(ROOT / ".github/workflows/codeql.yml")
    triggers = workflow["on"]
    assert {"push", "pull_request", "schedule", "workflow_dispatch"} <= set(triggers)
    assert "pull_request_target" not in triggers
    for event in ("push", "pull_request"):
        assert set(triggers[event]["branches"]) == {"main", "release/**"}
    job = workflow["jobs"]["analyze"]
    assert set(job["strategy"]["matrix"]["language"]) == {"actions", "python"}
    steps = job["steps"]
    assert all("run" not in step for step in steps)
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", step["uses"]) for step in steps)
    checkout, init, analyze = steps
    assert checkout["with"]["persist-credentials"] == "false"
    assert init["uses"].startswith("github/codeql-action/init@")
    assert init["with"]["build-mode"] == "none"
    assert init["with"]["languages"] == "${{ matrix.language }}"
    assert analyze["uses"].startswith("github/codeql-action/analyze@")
