"""Build a GitHub Checks API payload from a Maida statistical report."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence


CHECK_NAME = "Maida statistical gate"
VERDICT_CONCLUSIONS = {
    "pass": "success",
    "fail": "failure",
    "inconclusive": "neutral",
}
VERDICT_PASSED = {
    "pass": True,
    "fail": False,
    "inconclusive": None,
}


class ReportError(ValueError):
    """Raised when the Maida sidecar does not satisfy report version 1."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReportError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ReportError(f"{field} must be finite")
    return result


def _escape_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("|", "\\|")


def _summary(report: dict[str, Any], details_url: str) -> str:
    metadata = report.get("metadata")
    if not isinstance(metadata, dict):
        raise ReportError("metadata must be an object")
    trials = metadata.get("trials_completed")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 0:
        raise ReportError("metadata.trials_completed must be a non-negative integer")

    results = report.get("aggregate_results")
    if not isinstance(results, list) or not results:
        raise ReportError("aggregate_results must be a non-empty list")

    lines = [
        f"Overall verdict: **{report['verdict'].upper()}** across {trials} trials.",
        "",
        "| Assertion | Verdict | Confidence interval | Pass-rate threshold |",
        "| --- | --- | --- | ---: |",
    ]
    for index, result in enumerate(results):
        field = f"aggregate_results[{index}]"
        if not isinstance(result, dict):
            raise ReportError(f"{field} must be an object")

        name = result.get("check_name")
        if not isinstance(name, str) or not name:
            raise ReportError(f"{field}.check_name must be a non-empty string")
        verdict = result.get("verdict")
        if verdict not in VERDICT_CONCLUSIONS:
            raise ReportError(f"{field}.verdict must be pass, fail, or inconclusive")

        interval = result.get("confidence_interval")
        if not isinstance(interval, list) or len(interval) != 2:
            raise ReportError(f"{field}.confidence_interval must contain two numbers")
        lower = _finite_number(interval[0], f"{field}.confidence_interval[0]")
        upper = _finite_number(interval[1], f"{field}.confidence_interval[1]")
        if not 0 <= lower <= upper <= 1:
            raise ReportError(
                f"{field}.confidence_interval must satisfy 0 <= lower <= upper <= 1"
            )

        threshold = _finite_number(
            result.get("pass_rate_threshold"), f"{field}.pass_rate_threshold"
        )
        if not 0 <= threshold <= 1:
            raise ReportError(f"{field}.pass_rate_threshold must be between 0 and 1")

        lines.append(
            f"| `{_escape_cell(name)}` | **{verdict.upper()}** | "
            f"{lower:.3f}–{upper:.3f} | {threshold:.3f} |"
        )

    lines.extend(["", f"[Open this workflow run]({details_url}) for full gate output."])
    if report["verdict"] == "inconclusive":
        lines.extend(
            [
                "",
                "This conclusion is neutral and does not block by itself. "
                f"[Re-run this workflow]({details_url}) to collect a fresh trial set.",
            ]
        )
    return "\n".join(lines)


def build_check_payload(
    report: dict[str, Any], *, head_sha: str, details_url: str
) -> dict[str, Any]:
    """Validate *report* and return a completed Checks API request body."""
    if report.get("report_version") != "1":
        raise ReportError("report_version must be '1'")

    verdict = report.get("verdict")
    if verdict not in VERDICT_CONCLUSIONS:
        raise ReportError("verdict must be pass, fail, or inconclusive")
    if report.get("passed") is not VERDICT_PASSED[verdict]:
        raise ReportError(f"passed is inconsistent with verdict {verdict}")
    if not isinstance(head_sha, str) or not head_sha:
        raise ReportError("head_sha must be a non-empty string")
    if not isinstance(details_url, str) or not details_url:
        raise ReportError("details_url must be a non-empty string")

    return {
        "name": CHECK_NAME,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": VERDICT_CONCLUSIONS[verdict],
        "details_url": details_url,
        "output": {
            "title": f"{CHECK_NAME}: {verdict.upper()}",
            "summary": _summary(report, details_url),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--details-url", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReportError(f"could not read Maida report: {error}") from error
    if not isinstance(report, dict):
        raise ReportError("Maida report root must be an object")

    payload = build_check_payload(
        report, head_sha=args.head_sha, details_url=args.details_url
    )
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"verdict={report['verdict']}")
    print(f"conclusion={payload['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
