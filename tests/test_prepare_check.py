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
        "trials": [],
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


@pytest.mark.parametrize(
    ("verdict", "passed", "conclusion"),
    [
        ("pass", True, "success"),
        ("fail", False, "failure"),
        ("inconclusive", None, "neutral"),
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
    assert "0.439–1.000" in summary
    assert "0.900" in summary
    assert "3 trials" in summary
    assert "[Re-run this workflow]" in summary
    assert "actions/runs/456" in summary


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
