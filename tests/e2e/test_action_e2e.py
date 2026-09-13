"""Real CLI, real composite shell, real sticky action; local GitHub transport."""

import json
import re
from hashlib import sha256
from pathlib import Path

import pytest
from harness import Composite, Consumer, GitHubFixture


@pytest.fixture
def consumer(tmp_path):
    with GitHubFixture() as api:
        try:
            yield Consumer(tmp_path, api)
        finally:
            (tmp_path / "github-api.json").write_text(
                json.dumps(
                    {
                        "requests": api.requests,
                        "checks": api.checks,
                        "comments": api.comments,
                        "dispatches": api.dispatches,
                    },
                    indent=2,
                )
            )


def gate(consumer, **inputs):
    return Composite(
        consumer,
        "action.yml",
        {
            "agent-script": "agent.py",
            "baseline": "baseline.json",
            "policy": "policy.yaml",
            "accept-command-enabled": "true",
            **inputs,
        },
    ).run()


def snapshot(name, text, directory):
    # Normalize only actual generated trace identities, never verdicts or evidence.
    report = json.loads((directory / "maida-report.json").read_text())
    for trial in report["trials"]:
        text = re.sub(
            r"\b" + re.escape(trial["trace_id"][:8]) + r"\b", "<trace-id>", text
        )
    (directory / "comment.actual.md").write_text(text)
    expected = Path(__file__).parent / "snapshots" / f"{name}.md"
    assert text == expected.read_text(), f"Rendering changed; review {expected}"


@pytest.mark.parametrize(
    "state,verdict,conclusion",
    [
        ("good", "pass", "success"),
        ("regressed", "fail", "failure"),
    ],
)
def test_gate_verdict_and_comment(consumer, state, verdict, conclusion):
    if state == "regressed":
        consumer.regress()
    result = gate(consumer)
    assert result.failed is (verdict == "fail"), result.logs
    report = json.loads((consumer.root / "maida-report.json").read_text())
    assert report["verdict"] == verdict
    assert consumer.api.checks[-1]["conclusion"] == conclusion
    assert consumer.api.checks[-1]["head_sha"] == consumer.api.head
    assert len(consumer.api.comments) == 1, result.logs
    assert (
        "a repository maintainer can comment `/maida accept [optional reason]`"
        in consumer.api.comments[0]["body"]
    )
    snapshot(verdict, consumer.api.comments[0]["body"], consumer.root)


def test_inconclusive_is_neutral_and_never_printed_as_pass(consumer):
    assert not gate(consumer).failed
    consumer.run(
        "maida",
        "baseline",
        "--from-report",
        "maida-report.json",
        "--out",
        "baseline.json",
    )
    (consumer.root / "policy.yaml").write_text(
        "version: 2.1\ntrials: 2\nmetrics:\n"
        "  tool_call_count:\n    kind: distributional\n    direction: upper\n"
        "    coverage: 0.5\n    confidence: 0.95\n    mode: gating\n"
    )
    result = gate(consumer)
    assert not result.failed, result.logs
    assert result.steps["check"]["outputs"]["verdict"] == "inconclusive"
    assert consumer.api.checks[-1]["conclusion"] == "neutral"
    assert len(consumer.api.comments) == 1, result.logs
    snapshot("inconclusive", consumer.api.comments[0]["body"], consumer.root)


@pytest.mark.parametrize("authorized", [True, False])
def test_accept_command_and_rerun(consumer, authorized):
    consumer.regress()
    original_head = consumer.api.head
    original_baseline = (consumer.root / "baseline.json").read_bytes()
    failed = gate(consumer)
    assert failed.failed
    assert len(consumer.api.comments) == 1, failed.logs
    comment_id = consumer.api.comments[0]["id"]
    consumer.api.permission = "write" if authorized else "read"
    result = Composite(
        consumer,
        "accept-command/action.yml",
        {
            "agent-script": "agent.py",
            "baseline": "baseline.json",
            "policy": "policy.yaml",
            "github-token": "fixture-token",
        },
    ).run()
    assert not result.failed, result.logs
    if not authorized:
        assert result.steps["prepare"]["outputs"]["authorized"] == "false"
        assert "Produce completed run" not in result.executed
        assert "Check out verified PR head" not in result.executed
        assert not consumer.api.dispatches
        assert consumer.run("git", "rev-parse", "HEAD").stdout.strip() == original_head
        assert (consumer.root / "baseline.json").read_bytes() == original_baseline
        assert "requires write access" in consumer.api.comments[-1]["body"]
        return
    assert result.steps["write-back"]["outputs"]["changed"] == "true"
    consumer.update_head()
    assert consumer.api.head != original_head
    assert (
        consumer.run(
            "git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"
        ).stdout.strip()
        == "baseline.json"
    )
    assert (
        consumer.run(
            "git",
            "--git-dir",
            str(consumer.remote),
            "rev-parse",
            "refs/heads/candidate",
        ).stdout.strip()
        == consumer.api.head
    )
    baseline = json.loads((consumer.root / "baseline.json").read_text())
    acceptance = baseline["acceptance"]
    assert acceptance["reason"] == "intentional retry"
    assert (
        acceptance["previous_baseline"]["sha256"]
        == sha256(original_baseline).hexdigest()
    )
    assert acceptance["source"]["commit_sha"] == original_head
    assert consumer.api.dispatches[-1]["client_payload"]["sha"] == consumer.api.head
    rerun = gate(consumer)
    assert not rerun.failed, rerun.logs
    assert consumer.api.checks[-1]["conclusion"] == "success"
    reports = [
        c
        for c in consumer.api.comments
        if "<!-- Sticky Pull Request Comment -->" in c["body"]
    ]
    assert len(reports) == 1
    assert reports[0]["id"] == comment_id
    assert "Maida verdict: pass" in reports[0]["body"]
    assert "Maida verdict: fail" not in reports[0]["body"]


def test_trace_command_ingests_one_completed_run(consumer):
    result = gate(consumer, **{"agent-script": "", "trace-command": "python agent.py"})
    assert not result.failed, result.logs
    assert consumer.api.checks[-1]["conclusion"] == "success"
    report = json.loads((consumer.root / "maida-report.json").read_text())
    assert len(report["trials"]) == 1


def test_setup_failure_does_not_publish_a_verdict(consumer):
    result = gate(consumer, **{"agent-script": "missing.py"})
    assert result.failed
    assert not consumer.api.checks
    assert not consumer.api.comments
    assert "did not produce a statistical gate report" in "".join(result.logs)


def test_read_only_check_token_warns_without_losing_failure(consumer):
    consumer.regress()
    consumer.api.reject_checks = True
    result = gate(consumer)
    assert result.failed
    assert not consumer.api.checks
    assert "::warning::Could not publish" in "".join(result.logs)
    assert len(consumer.api.comments) == 1, result.logs
    assert "Maida verdict: fail" in consumer.api.comments[0]["body"]
