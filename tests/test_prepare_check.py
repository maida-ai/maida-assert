"""Offline tests for the GitHub Checks payload generated from Maida reports."""

import json
import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare_check.py"
SPEC = importlib.util.spec_from_file_location("prepare_check", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
prepare_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_check)

ReportError = prepare_check.ReportError
build_check_payload = prepare_check.build_check_payload
main = prepare_check.main


def test_python_owned_current_report_uses_supported_major() -> None:
    contract_path = Path(__file__).parent / "contracts" / "current-main.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    assert prepare_check._is_supported_report_version(
        contract["schemas"]["report"]
    )


def _report(verdict="pass", *, passed=True):
    return {
        "report_version": "1",
        "verdict": verdict,
        "passed": passed,
        "metadata": {
            "trials_requested": 3,
            "trials_completed": 3,
            "confidence_level": 0.95,
            "pass_rate_threshold": 0.90,
        },
        "trials": [{"trace_id": str(i)} for i in range(3)],
        "aggregate_results": [
            {
                "check_name": "no_loops",
                "verdict": verdict,
                "trials": 3,
                "successes": 3 if verdict == "pass" else 2,
                "pass_rate": 1.0 if verdict == "pass" else 2 / 3,
                "confidence_interval": [0.438503, 1.0],
                "confidence_level": 0.95,
                "pass_rate_threshold": 0.90,
                "decision_rule": "unanimous_n3"
                if verdict == "pass"
                else "wilson_two_sided",
                "trial_outcomes": [True, True, verdict == "pass"],
            }
        ],
    }


def _report_v2(verdict="pass", *, passed=True, report_version="2.0.0"):
    return {
        "report_version": report_version,
        "verdict": verdict,
        "passed": passed,
        "trials_requested": 3,
        "metadata": {
            "trials_used": 3,
            "trials_budgeted": 3,
            "stopping_rule": "fixed_n",
            "abort_reason": None,
        },
        "trials": [{"trace_id": str(i)} for i in range(3)],
        "aggregate_results": [
            {
                "check_name": "no_loops",
                "kind": "invariant",
                "direction": None,
                "mode": "gating",
                "verdict": verdict,
                "decision_rule": "invariant",
                "stopping_rule": "fixed_n",
                "trials_used": 3,
                "trials_budgeted": 3,
                "trial_outcomes": [True, True, verdict == "pass"],
                "evidence": {"violations": 0 if verdict == "pass" else 1},
            },
            {
                "check_name": "step_count",
                "kind": "measured",
                "direction": "upper",
                "mode": "gating",
                "verdict": verdict,
                "decision_rule": "tolerance",
                "stopping_rule": "fixed_n",
                "trials_used": 3,
                "trials_budgeted": 3,
                "trial_outcomes": [],
                "evidence": {
                    "delta": 1.0,
                    "sample": {"min": 10.0, "median": 11.0, "max": 12.0},
                },
            },
            {
                "check_name": "task_pass_rate",
                "kind": "statistical",
                "direction": "lower",
                "mode": "report_only",
                "verdict": None,
                "decision_rule": "report_only",
                "stopping_rule": "fixed_n",
                "trials_used": 3,
                "trials_budgeted": 3,
                "trial_outcomes": [True, True, False],
                "evidence": {
                    "observed_rate": 2 / 3,
                    "confidence_bounds": {"lower": 0.253534, "upper": 0.921734},
                },
            },
        ],
    }


@pytest.mark.parametrize(
    ("verdict", "passed", "conclusion"),
    [
        ("pass", True, "success"),
        ("fail", False, "failure"),
        ("inconclusive", None, "failure"),
    ],
)
def test_build_check_payload_maps_verdicts(verdict, passed, conclusion):
    payload = build_check_payload(
        _report(verdict, passed=passed),
        head_sha="a" * 40,
        details_url="https://github.com/maida-ai/example/actions/runs/123",
    )

    assert payload["name"] == "Maida statistical gate"
    assert payload["head_sha"] == "a" * 40
    assert payload["status"] == "completed"
    assert payload["conclusion"] == conclusion
    assert payload["details_url"].endswith("/actions/runs/123")
    assert payload["output"]["title"] == f"Maida statistical gate: {verdict.upper()}"


def test_summary_lists_assertion_verdict_interval_threshold_and_rerun_link():
    payload = build_check_payload(
        _report("inconclusive", passed=None),
        head_sha="b" * 40,
        details_url="https://github.com/maida-ai/example/actions/runs/456",
    )

    summary = payload["output"]["summary"]
    assert "`no_loops`" in summary
    assert "INCONCLUSIVE" in summary
    assert "0.439-1.000" in summary
    assert "0.900" in summary
    assert "3 trials" in summary
    assert "[Re-run this workflow]" in summary
    assert "actions/runs/456" in summary


@pytest.mark.parametrize(
    ("verdict", "passed", "conclusion"),
    [
        ("pass", True, "success"),
        ("fail", False, "failure"),
        ("inconclusive", None, "failure"),
    ],
)
def test_build_check_payload_maps_v2_verdicts(verdict, passed, conclusion):
    payload = build_check_payload(
        _report_v2(verdict, passed=passed),
        head_sha="f" * 40,
        details_url="https://github.com/maida-ai/example/actions/runs/202",
    )

    assert payload["conclusion"] == conclusion
    assert payload["output"]["title"] == (
        f"Maida statistical gate: {verdict.upper()}"
    )


@pytest.mark.parametrize("report_version", ["2.0.0", "2.0.1", "2.1.0"])
def test_build_check_payload_accepts_report_v2_patch_versions(report_version):
    payload = build_check_payload(
        _report_v2(report_version=report_version),
        head_sha="a" * 40,
        details_url="https://github.com/maida-ai/example/actions/runs/203",
    )

    assert payload["conclusion"] == "success"


def test_build_check_payload_rejects_incompatible_report_major():
    with pytest.raises(ReportError, match="major 2"):
        build_check_payload(
            _report_v2(report_version="3.0.0"),
            head_sha="a" * 40,
            details_url="https://github.com/maida-ai/example/actions/runs/204",
        )


def test_v2_summary_lists_tier_evidence_and_report_only_metrics():
    payload = build_check_payload(
        _report_v2(),
        head_sha="1" * 40,
        details_url="https://github.com/maida-ai/example/actions/runs/303",
    )

    summary = payload["output"]["summary"]
    assert "3/3 trials used" in summary
    assert "`no_loops`" in summary
    assert "violated in 0/3 trials" in summary
    assert "`step_count`" in summary
    assert "delta +1; min/median/max 10.0/11.0/12.0" in summary
    assert "`task_pass_rate`" in summary
    assert "REPORT ONLY" in summary
    assert "observed rate 0.667; no confidence verdict" in summary


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"report_version": "2"}, "report_version"),
        ({"verdict": "maybe"}, "verdict"),
        ({"verdict": "inconclusive", "passed": False}, "passed"),
        ({"aggregate_results": []}, "aggregate_results"),
    ],
)
def test_invalid_report_contract_is_rejected(change, message):
    report = _report()
    report.update(change)

    with pytest.raises(ReportError, match=message):
        build_check_payload(
            report,
            head_sha="c" * 40,
            details_url="https://github.com/maida-ai/example/actions/runs/789",
        )


def test_invalid_confidence_interval_is_rejected():
    report = _report()
    report["aggregate_results"][0]["confidence_interval"] = [0.9, 0.2]

    with pytest.raises(ReportError, match="confidence_interval"):
        build_check_payload(
            report,
            head_sha="d" * 40,
            details_url="https://github.com/maida-ai/example/actions/runs/789",
        )


def test_v2_gating_metric_requires_a_verdict():
    report = _report_v2()
    report["aggregate_results"][0]["verdict"] = None

    with pytest.raises(ReportError, match="verdict"):
        build_check_payload(
            report,
            head_sha="2" * 40,
            details_url="https://github.com/maida-ai/example/actions/runs/404",
        )


def test_cli_writes_payload_and_github_outputs(tmp_path, capsys):
    report_path = tmp_path / "report.json"
    payload_path = tmp_path / "payload.json"
    report_path.write_text(json.dumps(_report("fail", passed=False)), encoding="utf-8")

    exit_code = main(
        [
            "--report",
            str(report_path),
            "--output",
            str(payload_path),
            "--head-sha",
            "e" * 40,
            "--details-url",
            "https://github.com/maida-ai/example/actions/runs/101",
        ]
    )

    assert exit_code == 0
    assert (
        json.loads(payload_path.read_text(encoding="utf-8"))["conclusion"] == "failure"
    )
    assert capsys.readouterr().out == "verdict=fail\nconclusion=failure\n"


@pytest.mark.parametrize("verdict,passed", [("pass", True), ("fail", False), ("inconclusive", None)])
def test_report_only_mode_never_certifies_a_change(verdict, passed):
    payload = build_check_payload(
        _report_v2(verdict, passed=passed), head_sha="a" * 40,
        details_url="https://example.invalid/run", mode="report-only",
    )
    assert payload["name"] == "Maida behavioral report (non-blocking)"
    assert payload["conclusion"] == "neutral"
    assert "not merge authorization" in payload["output"]["summary"]


@pytest.mark.parametrize("bad_evidence", ["zero", "aborted", "contradictory", "unused"])
def test_missing_or_contradictory_evidence_fails_closed(bad_evidence):
    report = _report_v2()
    if bad_evidence == "zero":
        report["metadata"]["trials_used"] = 0
    elif bad_evidence == "aborted":
        report["metadata"]["abort_reason"] = "agent_crashed"
    elif bad_evidence == "unused":
        report["aggregate_results"][0]["trials_used"] = 0
    else:
        report["aggregate_results"][0]["verdict"] = "inconclusive"
    with pytest.raises(ReportError):
        build_check_payload(report, head_sha="a" * 40, details_url="https://example.invalid/run")


def test_report_only_metrics_cannot_produce_blocking_success():
    report = _report_v2()
    report["aggregate_results"] = report["aggregate_results"][2:]
    payload = build_check_payload(report, head_sha="a" * 40, details_url="https://example.invalid/run")
    assert payload["conclusion"] == "failure"
    assert "No gating metrics" in payload["output"]["summary"]


def test_unaccepted_configuration_change_blocks_even_with_pass():
    payload = build_check_payload(
        _report_v2(), head_sha="a" * 40, details_url="https://example.invalid/run",
        configuration_blocked=True,
    )
    assert payload["conclusion"] == "failure"
    assert "PASS" in payload["output"]["title"]
    assert "Configuration change requires explicit acceptance" in payload["output"]["summary"]


@pytest.mark.parametrize("trials", [[], None, [{"trace_id": "same"}] * 3, [{}, {}, {}]])
def test_invalid_trial_records_fail_closed(trials):
    report = _report_v2()
    report["trials"] = trials
    with pytest.raises(ReportError):
        build_check_payload(report, head_sha="a" * 40, details_url="https://example.invalid/run")


def test_process_health_alone_does_not_certify_report_only_metrics():
    report = _report_v2()
    report["aggregate_results"][0]["check_name"] = "agent_process"
    del report["aggregate_results"][1]
    payload = build_check_payload(report, head_sha="a" * 40, details_url="https://example.invalid/run")
    assert payload["conclusion"] == "failure"


def test_cli_rejects_exit_status_conflicting_with_verdict(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report_v2()))
    output = tmp_path / "payload.json"
    with pytest.raises(ReportError, match="exit status"):
        main(["--report", str(report), "--output", str(output), "--head-sha", "a" * 40,
              "--details-url", "https://example.invalid/run", "--cli-status", "1"])
    assert not output.exists()


def test_cli_rejects_empty_markdown_before_publishing(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report_v2()))
    markdown = tmp_path / "report.md"
    markdown.write_text("")
    output = tmp_path / "payload.json"
    with pytest.raises(ReportError, match="Markdown report is empty"):
        main(["--report", str(report), "--output", str(output), "--head-sha", "a" * 40,
              "--details-url", "https://example.invalid/run", "--markdown", str(markdown)])
    assert not output.exists()
