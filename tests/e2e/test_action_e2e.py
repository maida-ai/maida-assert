"""Real CLI, real composite shell, real sticky action; local GitHub transport."""

import json
import re
from hashlib import sha256
from pathlib import Path

import pytest
from harness import Composite, Consumer, GitHubFixture, acceptance


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


def snapshot(name, text, directory, result):
    # Normalize only actual generated trace identities, never verdicts or evidence.
    report = json.loads((directory / "maida-report.json").read_text())
    for trial in report["trials"]:
        text = re.sub(
            r"\b" + re.escape(trial["trace_id"][:8]) + r"\b", "<trace-id>", text
        )
    (directory / "comment.actual.md").write_text(text)
    expected = Path(__file__).parent / "snapshots" / f"{name}.md"
    expected_text = expected.read_text().replace(
        "<base-sha>", result.steps["trust"]["outputs"]["base_sha"]
    )
    # Bind expected CLI links to the actual trusted snapshot; do not hide content drift.
    baseline = result.steps["trust"]["outputs"]["baseline"]
    cli, suffix = expected_text.split("\n---\n\n### Accept this intentional change", 1)
    cli = cli.replace("--baseline baseline.json", f"--baseline {baseline}")
    expected_text = cli + "\n---\n\n### Accept this intentional change" + suffix
    assert text == expected_text, f"Rendering changed; review {expected}"


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
    snapshot(verdict, consumer.api.comments[0]["body"], consumer.root, result)


def test_inconclusive_blocks_and_never_printed_as_pass(consumer):
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
    consumer.run("git", "add", "baseline.json", "policy.yaml")
    consumer.run("git", "commit", "-m", "Establish distributional base policy")
    consumer.update_head()
    consumer.base = consumer.api.head
    result = gate(consumer)
    assert result.failed, result.logs
    assert result.steps["check"]["outputs"]["verdict"] == "inconclusive"
    assert consumer.api.checks[-1]["conclusion"] == "failure"
    assert len(consumer.api.comments) == 1, result.logs
    snapshot("inconclusive", consumer.api.comments[0]["body"], consumer.root, result)


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
    result = acceptance(consumer)
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
    consumer.run("git", "fetch", "origin", "candidate")
    consumer.run("git", "merge", "--ff-only", "origin/candidate")
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
    provenance = baseline["acceptance"]
    assert provenance["reason"] == "intentional retry"
    assert (
        provenance["previous_baseline"]["sha256"]
        == sha256(original_baseline).hexdigest()
    )
    assert provenance["source"]["commit_sha"] == original_head
    assert consumer.api.dispatches[-1]["client_payload"]["sha"] == consumer.api.head
    unapproved = gate(consumer)
    assert unapproved.failed, unapproved.logs
    context = json.loads(
        Path(unapproved.steps["trust"]["outputs"]["context"]).read_text()
    )
    rerun = gate(consumer, **{"configuration-acceptance": context["acceptance"]})
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


def establish_base(consumer):
    consumer.run("git", "add", "policy.yaml", "baseline.json")
    consumer.run("git", "commit", "-m", "Establish trusted policy")
    consumer.update_head()
    consumer.base = consumer.api.head


@pytest.mark.parametrize("change", ["weaken", "remove", "baseline"])
def test_candidate_cannot_weaken_its_evaluation(consumer, change):
    consumer.regress()
    if change == "weaken":
        path = consumer.root / "policy.yaml"
        path.write_text(path.read_text().replace("absolute: 0", "absolute: 999"))
    elif change == "remove":
        (consumer.root / "policy.yaml").unlink()
    else:
        consumer.run("python", "agent.py")
        consumer.run("maida", "baseline", "--out", "baseline.json")
    consumer.run("git", "add", "policy.yaml", "baseline.json")
    consumer.run("git", "commit", "-m", "Candidate configuration change")
    consumer.update_head()
    result = gate(consumer)
    assert result.failed, result.logs
    assert result.steps["check"]["outputs"]["verdict"] == "fail"
    assert consumer.api.checks[-1]["conclusion"] == "failure"
    assert (
        "Configuration change requires explicit acceptance"
        in consumer.api.checks[-1]["output"]["summary"]
    )


def test_configuration_acceptance_is_invalidated_by_next_commit(consumer):
    (consumer.root / ".maida").mkdir()
    (consumer.root / ".maida/reviewed.txt").write_text("intentional config addition\n")
    consumer.run("git", "add", ".maida")
    consumer.run("git", "commit", "-m", "Configuration event")
    consumer.update_head()
    first = gate(consumer)
    assert first.failed, first.logs
    assert first.steps["check"]["outputs"]["verdict"] == "pass"
    context = json.loads(Path(first.steps["trust"]["outputs"]["context"]).read_text())
    accepted = gate(consumer, **{"configuration-acceptance": context["acceptance"]})
    assert not accepted.failed, accepted.logs
    approved_head = consumer.api.head
    consumer.regress()
    count = len(consumer.api.checks)
    stale = gate(consumer, **{"configuration-acceptance": context["acceptance"]})
    assert stale.failed
    assert "Stale or invalid" in "".join(stale.logs)
    assert len(consumer.api.checks) == count
    assert consumer.api.checks[-1]["head_sha"] == approved_head != consumer.api.head


@pytest.mark.parametrize("mode", ["blocking", "report-only"])
def test_check_publication_failure_cannot_authorize_pass(consumer, mode):
    consumer.api.reject_checks = True
    result = gate(consumer, mode=mode)
    assert result.failed is (mode == "blocking"), result.logs
    assert result.steps["check"]["outputs"]["verdict"] == "pass"
    assert not consumer.api.checks
    assert "Could not publish" in "".join(result.logs)


@pytest.mark.parametrize("regressed", [False, True])
def test_report_only_mode_never_publishes_blocking_success(consumer, regressed):
    if regressed:
        consumer.regress()
    result = gate(consumer, mode="report-only")
    assert not result.failed, result.logs
    assert consumer.api.checks[-1]["name"] == "Maida behavioral report (non-blocking)"
    assert consumer.api.checks[-1]["conclusion"] == "neutral"
    assert "not merge authorization" in consumer.api.comments[0]["body"]


def test_report_only_metrics_do_not_certify_behavior(consumer):
    (consumer.root / "policy.yaml").write_text(
        "version: 2.1\ntrials: 1\nmetrics:\n"
        "  task_pass_rate:\n    kind: statistical\n    direction: lower\n"
        "    threshold: 0.90\n    confidence: 0.95\n"
        "    success_predicate: all_invariants_passed\n    mode: report_only\n"
    )
    establish_base(consumer)
    result = gate(consumer)
    assert result.failed, result.logs
    assert consumer.api.checks[-1]["conclusion"] == "failure"
    assert "No gating metrics" in consumer.api.checks[-1]["output"]["summary"]


def test_import_cannot_override_trusted_trial_budget(consumer):
    path = consumer.root / "policy.yaml"
    path.write_text(path.read_text().replace("trials: 1", "trials: 3"))
    establish_base(consumer)
    result = gate(consumer, **{"agent-script": "", "trace-command": "python agent.py"})
    assert result.failed
    assert "requires trials: 1" in "".join(result.logs)
    assert not consumer.api.checks


def test_missing_new_sidecar_cannot_reuse_previous_success(consumer):
    assert not gate(consumer).failed
    count = len(consumer.api.checks)
    # Simulate a broken CLI returning success without writing the requested JSON.
    executable = Path(consumer.env["PATH"].split(":")[0]) / "maida"
    executable.write_text('#!/bin/sh\nprintf "Incomplete report\\n"\nexit 0\n')
    executable.chmod(0o755)
    result = gate(consumer)
    assert result.failed
    assert "could not read Maida report" in "".join(result.logs)
    assert len(consumer.api.checks) == count
    assert "Incomplete report" not in consumer.api.comments[0]["body"]


def test_planted_candidate_has_no_write_credential_and_cannot_inject_writer(consumer):
    consumer.regress()
    planted = """import os, pathlib, runpy, subprocess
assert not os.environ.get("GITHUB_TOKEN")
assert not os.environ.get("GH_TOKEN")
credentials = subprocess.run(["git", "config", "--local", "--get-regexp", "extraheader|credential"], capture_output=True, text=True)
assert not credentials.stdout
assert not pathlib.Path("../trusted-writer").exists()
runpy.run_path("agent.py", run_name="__main__")
# A candidate-controlled writer module must never be imported on the writer.
pathlib.Path("accept_artifact.py").write_text("raise RuntimeError('planted module executed')")
pathlib.Path(".git/hooks/pre-commit").write_text("#!/bin/sh\\nexit 99\\n")
pathlib.Path(".git/hooks/pre-commit").chmod(0o755)
"""
    (consumer.root / "planted.py").write_text(planted)
    consumer.run("git", "add", "planted.py")
    consumer.run("git", "commit", "-m", "Plant credential and writer injection probes")
    consumer.run("git", "push", "origin", "candidate")
    consumer.update_head()
    result = acceptance(consumer, agent_script="planted.py")
    assert not result.failed, result.logs
    assert result.steps["write-back"]["outputs"]["changed"] == "true"
    assert (
        consumer.run(
            "git",
            "--git-dir",
            str(consumer.remote),
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            consumer.api.head,
        ).stdout.strip()
        == "baseline.json"
    )


def test_candidate_extra_staged_file_rejects_before_write(consumer):
    (consumer.root / "planted.py").write_text(
        'import pathlib, subprocess\npathlib.Path("extra.txt").write_text("planted")\nsubprocess.run(["git", "add", "extra.txt"], check=True)\n'
    )
    consumer.run("git", "add", "planted.py")
    consumer.run("git", "commit", "-m", "Plant staged file")
    consumer.run("git", "push", "origin", "candidate")
    consumer.update_head()
    head = consumer.api.head
    result = acceptance(consumer, agent_script="planted.py")
    assert result.failed
    assert "extra staged files" in "\n".join(result.logs)
    assert consumer.api.head == head
    assert not consumer.api.dispatches


def test_capture_missing_run_rejects_before_write(consumer):
    (consumer.root / "empty.py").write_text('print("no trace")\n')
    consumer.run("git", "add", "empty.py")
    consumer.run("git", "commit", "-m", "Missing trace")
    consumer.run("git", "push", "origin", "candidate")
    consumer.update_head()
    result = acceptance(consumer, agent_script="empty.py")
    assert result.failed
    assert "exactly one completed" in "\n".join(result.logs)
    assert not consumer.api.dispatches


def test_accept_current_baseline_dispatches_without_commit(consumer):
    head = consumer.api.head
    result = acceptance(consumer)
    assert not result.failed, result.logs
    assert result.steps["write-back"]["outputs"]["changed"] == "false"
    assert consumer.api.head == head
    assert consumer.api.dispatches[-1]["client_payload"]["sha"] == head


def test_accept_changed_policy_rejects_before_candidate_checkout(consumer):
    with (consumer.root / "policy.yaml").open("a") as policy:
        policy.write("\n# Candidate policy change\n")
    consumer.run("git", "add", "policy.yaml")
    consumer.run("git", "commit", "-m", "Change candidate policy")
    consumer.run("git", "push", "origin", "candidate")
    consumer.update_head()
    result = acceptance(consumer)
    assert result.failed
    assert "Changed policy" in "\n".join(result.logs)
    assert "Traceback" not in "\n".join(result.logs)
    assert not (consumer.root.parent / "capture").exists()
    assert not consumer.api.dispatches
