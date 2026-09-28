"""Supply-chain contracts across executable workflows and consumer examples."""

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_third_party_actions_use_full_commit_shas():
    paths = [ROOT / "README.md", *ROOT.glob("**/action.yml"),
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


def test_dependency_locks_pin_versions_and_hashes():
    for name in ("dev", "e2e"):
        lock = (ROOT / f"requirements-{name}.lock").read_text()
        requirements = [line for line in lock.replace("\\\n", " ").splitlines()
                        if line and not line.startswith(("#", " "))]
        assert requirements
        for requirement in requirements:
            assert re.match(r"[\w.-]+==[^\s]+", requirement)
            assert "--hash=sha256:" in requirement


def test_e2e_generator_checkout_is_immutable():
    workflow = yaml.safe_load((ROOT / ".github/workflows/e2e.yml").read_text())
    steps = workflow["jobs"]["deterministic-e2e"]["steps"]
    checkout = next(s for s in steps if s.get("name") == "Read current workflow generator")
    assert re.fullmatch(r"[0-9a-f]{40}", checkout["with"]["ref"])


def test_dependency_locks_include_declared_requirements():
    def declared_names(path):
        names = set()
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("-r "):
                names.update(declared_names(path.parent / line[3:].strip()))
            else:
                names.add(re.match(r"[\w.-]+", line)[0].lower().replace("_", "-"))
        return names

    for name in ("dev", "e2e"):
        locked = set(re.findall(r"^([\w.-]+)==", (ROOT / f"requirements-{name}.lock").read_text(), re.M))
        assert declared_names(ROOT / f"requirements-{name}.txt") <= locked
