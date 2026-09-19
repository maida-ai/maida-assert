"""Keep the Action's baseline-free example protections explicit.

The engine's CLI contract suite exercises these rules' runtime behavior. This
consumer test prevents editing the checked-in configuration to omit them.
"""

from pathlib import Path

import yaml


def test_checked_in_policy_preserves_each_baseline_free_protection():
    path = Path(__file__).resolve().parents[1] / ".maida/policy.yaml"
    policy = yaml.safe_load(path.read_text())
    assert str(policy["version"]).split(".")[0] == "2"
    assert policy["metrics"] == {
        "no_loops": {"kind": "invariant", "require": True},
        "no_guardrails": {"kind": "invariant", "require": True},
        "stop_condition_reached": {"kind": "invariant", "require": True},
    }
