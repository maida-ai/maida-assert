"""Supply-chain contracts across executable workflows and consumer examples."""

from pathlib import Path
import re
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_third_party_actions_use_full_commit_shas():
    paths = [ROOT / "README.md", *ROOT.glob("docs/**/*.md"),
             *ROOT.glob("**/action.yml"),
             *ROOT.glob(".github/workflows/*.yml")]
    for path in paths:
        for reference in re.findall(r"\buses:\s*([^\s#]+)", path.read_text()):
            if reference.startswith(("./", "maida-ai/maida-assert@", "maida-ai/maida-assert/")):
                continue
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), (path, reference)


def test_workflows_default_to_read_only_permissions():
    for path in ROOT.glob(".github/workflows/*.yml"):
        workflow = yaml.safe_load(path.read_text())
        assert workflow["permissions"] == {"contents": "read"}, path


def test_capture_uv_version_and_cache_settings_survive_yaml_parsing():
    action = yaml.safe_load((ROOT / "capture-acceptance/action.yml").read_text())
    uv = next(s for s in action["runs"]["steps"] if s.get("name") == "Set up uv")
    assert uv["with"] == {"version": "0.12.17", "enable-cache": "false"}


def test_uv_lock_pins_registry_packages_and_hashes():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    registry = [p for p in lock["package"] if "registry" in p["source"]]
    assert registry
    for package in registry:
        assert package["version"]
        assert package["wheels"], package["name"]
        assert all(w["hash"].startswith("sha256:") for w in package["wheels"])


def test_e2e_generator_checkout_is_immutable():
    workflow = yaml.safe_load((ROOT / ".github/workflows/e2e.yml").read_text())
    steps = workflow["jobs"]["deterministic-e2e"]["steps"]
    checkout = next(s for s in steps if s.get("name") == "Read current workflow generator")
    assert re.fullmatch(r"[0-9a-f]{40}", checkout["with"]["ref"])


def test_uv_groups_share_the_reviewed_engine_version():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    groups = project["dependency-groups"]
    assert {"dev", "maida", "e2e"} <= groups.keys()
    assert groups["e2e"] == [{"include-group": "dev"}, {"include-group": "maida"}]
    requirement, = groups["maida"]
    version = requirement.removeprefix("maida-ai==")
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    engine, = [p for p in lock["package"] if p["name"] == "maida-ai"]
    assert engine["version"] == version
    for path in (ROOT / "action.yml", ROOT / "capture-acceptance/action.yml"):
        action = yaml.safe_load(path.read_text())
        assert action["inputs"]["maida-version"]["default"] == "v" + version


def test_workflows_use_locked_uv_groups():
    for name, job, group in (("ci.yml", "unit-tests", "dev"),
                              ("release.yml", "test", "dev"),
                              ("e2e.yml", "deterministic-e2e", "e2e")):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        commands = "\n".join(s.get("run", "") for s in workflow["jobs"][job]["steps"])
        assert f"uv sync --locked --only-group {group} --no-build" in commands
        assert "uv run --locked" in commands
        assert "requirements-" not in commands
