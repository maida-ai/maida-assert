"""Build a GitHub Checks API payload from a Maida statistical report."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any, Sequence


CHECK_NAME = "Maida statistical gate"
LEGACY_REPORT_VERSION = "1"
SUPPORTED_REPORT_MAJOR = 2
SEMANTIC_VERSION = re.compile(
    r"(?P<major>0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
)
VERDICT_CONCLUSIONS = {
    "pass": "success",
    "fail": "failure",
    "inconclusive": "failure",
}
VERDICT_PASSED = {
    "pass": True,
    "fail": False,
    "inconclusive": None,
}


class ReportError(ValueError):
    """Raised when the Maida sidecar does not satisfy a supported report schema."""


def _is_supported_report_version(value: Any) -> bool:
    if value == LEGACY_REPORT_VERSION:
        return True
    if not isinstance(value, str):
        return False
    match = SEMANTIC_VERSION.fullmatch(value)
    return match is not None and int(match.group("major")) == SUPPORTED_REPORT_MAJOR


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReportError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ReportError(f"{field} must be finite")
    return result


def _escape_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("`", "\\`").replace("|", "\\|")


def _non_negative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReportError(f"{field} must be a non-negative integer")
    return value


def _summary_footer(
    lines: list[str], report: dict[str, Any], details_url: str
) -> str:
    lines.extend(["", f"[Open this workflow run]({details_url}) for full gate output."])
    if report["verdict"] == "inconclusive":
        lines.extend(
            [
                "",
                "Insufficient evidence is not a behavioral PASS. "
                f"[Re-run this workflow]({details_url}) to collect a fresh trial set.",
            ]
        )
    return "\n".join(lines)


def _summary_v1(report: dict[str, Any], details_url: str) -> str:
    metadata = report.get("metadata")
    if not isinstance(metadata, dict):
        raise ReportError("metadata must be an object")
    trials = _non_negative_integer(
        metadata.get("trials_completed"), "metadata.trials_completed"
    )

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
            f"{lower:.3f}-{upper:.3f} | {threshold:.3f} |"
        )

    return _summary_footer(lines, report, details_url)


def _v2_evidence(result: dict[str, Any], field: str) -> str:
    kind = result.get("kind")
    if kind not in {"invariant", "measured", "statistical", "distributional"}:
        raise ReportError(
            f"{field}.kind must be invariant, measured, statistical, or distributional"
        )

    mode = result.get("mode")
    if mode not in {"gating", "report_only"}:
        raise ReportError(f"{field}.mode must be gating or report_only")
    verdict = result.get("verdict")
    if mode == "gating" and verdict not in VERDICT_CONCLUSIONS:
        raise ReportError(
            f"{field}.verdict must be pass, fail, or inconclusive for a gating metric"
        )
    if mode == "report_only" and verdict is not None:
        raise ReportError(f"{field}.verdict must be null for a report-only metric")

    trials_used = _non_negative_integer(
        result.get("trials_used"), f"{field}.trials_used"
    )
    trials_budgeted = _non_negative_integer(
        result.get("trials_budgeted"), f"{field}.trials_budgeted"
    )
    if trials_used > trials_budgeted:
        raise ReportError(f"{field}.trials_used must not exceed trials_budgeted")

    evidence = result.get("evidence")
    if not isinstance(evidence, dict):
        raise ReportError(f"{field}.evidence must be an object")

    if kind == "invariant":
        violations = _non_negative_integer(
            evidence.get("violations"), f"{field}.evidence.violations"
        )
        if violations > trials_used:
            raise ReportError(
                f"{field}.evidence.violations must not exceed trials_used"
            )
        return f"violated in {violations}/{trials_used} trials"

    if kind == "measured":
        delta = evidence.get("delta")
        delta_text = (
            "n/a"
            if delta is None
            else f"{_finite_number(delta, f'{field}.evidence.delta'):+.3g}"
        )
        sample = evidence.get("sample")
        if not isinstance(sample, dict):
            raise ReportError(f"{field}.evidence.sample must be an object")
        minimum = _finite_number(sample.get("min"), f"{field}.evidence.sample.min")
        median = _finite_number(
            sample.get("median"), f"{field}.evidence.sample.median"
        )
        maximum = _finite_number(sample.get("max"), f"{field}.evidence.sample.max")
        if not minimum <= median <= maximum:
            raise ReportError(
                f"{field}.evidence.sample must satisfy min <= median <= max"
            )
        return (
            f"delta {delta_text}; min/median/max "
            f"{minimum}/{median}/{maximum}"
        )

    bounds = evidence.get("confidence_bounds")
    if isinstance(bounds, dict):
        lower = _finite_number(
            bounds.get("lower"), f"{field}.evidence.confidence_bounds.lower"
        )
        upper = _finite_number(
            bounds.get("upper"), f"{field}.evidence.confidence_bounds.upper"
        )
        if not 0 <= lower <= upper <= 1:
            raise ReportError(
                f"{field}.evidence.confidence_bounds must satisfy "
                "0 <= lower <= upper <= 1"
            )
        if mode == "report_only":
            observed = _finite_number(
                evidence.get("observed_rate"), f"{field}.evidence.observed_rate"
            )
            if not 0 <= observed <= 1:
                raise ReportError(
                    f"{field}.evidence.observed_rate must be between 0 and 1"
                )
            return f"observed rate {observed:.3f}; no confidence verdict"
        return f"one-sided bounds {lower:.3f}-{upper:.3f}"

    if kind == "distributional":
        observed = _finite_number(
            evidence.get("observed"), f"{field}.evidence.observed"
        )
        bound = _finite_number(
            evidence.get("prediction_bound"), f"{field}.evidence.prediction_bound"
        )
        return f"observed {observed:.3g}; prediction bound {bound:.3g}"

    raise ReportError(f"{field}.evidence.confidence_bounds must be an object")


def _summary_v2(report: dict[str, Any], details_url: str) -> str:
    metadata = report.get("metadata")
    if not isinstance(metadata, dict):
        raise ReportError("metadata must be an object")
    trials_used = _non_negative_integer(
        metadata.get("trials_used"), "metadata.trials_used"
    )
    trials_budgeted = _non_negative_integer(
        metadata.get("trials_budgeted"), "metadata.trials_budgeted"
    )
    if trials_used > trials_budgeted:
        raise ReportError("metadata.trials_used must not exceed trials_budgeted")

    results = report.get("aggregate_results")
    if not isinstance(results, list) or not results:
        raise ReportError("aggregate_results must be a non-empty list")

    lines = [
        f"Overall verdict: **{report['verdict'].upper()}** across "
        f"{trials_used}/{trials_budgeted} trials used.",
        "",
        "| Metric | Kind | Mode | Direction | Verdict | Evidence |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for index, result in enumerate(results):
        field = f"aggregate_results[{index}]"
        if not isinstance(result, dict):
            raise ReportError(f"{field} must be an object")
        name = result.get("check_name")
        if not isinstance(name, str) or not name:
            raise ReportError(f"{field}.check_name must be a non-empty string")

        detail = _v2_evidence(result, field)
        verdict = result.get("verdict")
        verdict_text = verdict.upper() if verdict is not None else "REPORT ONLY"
        direction = result.get("direction")
        if direction not in {None, "lower", "upper", "both"}:
            raise ReportError(f"{field}.direction must be lower, upper, both, or null")
        lines.append(
            f"| `{_escape_cell(name)}` | {result['kind']} | {result['mode']} | "
            f"{direction or 'n/a'} | **{verdict_text}** | {detail} |"
        )

    return _summary_footer(lines, report, details_url)


def _summary(report: dict[str, Any], details_url: str) -> str:
    if report["report_version"] == "1":
        return _summary_v1(report, details_url)
    return _summary_v2(report, details_url)


def build_check_payload(
    report: dict[str, Any], *, head_sha: str, details_url: str,
    mode: str = "blocking", configuration_blocked: bool = False,
) -> dict[str, Any]:
    """Validate *report* and return a completed Checks API request body."""
    if not _is_supported_report_version(report.get("report_version")):
        raise ReportError(
            "report_version must be legacy '1' or a semantic version with major 2"
        )

    verdict = report.get("verdict")
    if verdict not in VERDICT_CONCLUSIONS:
        raise ReportError("verdict must be pass, fail, or inconclusive")
    if report.get("passed") is not VERDICT_PASSED[verdict]:
        raise ReportError(f"passed is inconsistent with verdict {verdict}")
    if not isinstance(head_sha, str) or not head_sha:
        raise ReportError("head_sha must be a non-empty string")
    if not isinstance(details_url, str) or not details_url:
        raise ReportError("details_url must be a non-empty string")

    if mode not in {"blocking", "report-only"}:
        raise ReportError("mode must be blocking or report-only")
    summary = _summary(report, details_url)
    legacy = report["report_version"] == LEGACY_REPORT_VERSION
    metadata = report["metadata"]
    used = metadata["trials_completed" if legacy else "trials_used"]
    if used == 0 or metadata.get("abort_reason") is not None:
        raise ReportError("missing trial evidence or aborted evaluation; rerun the gate")
    trials = report.get("trials")
    if not isinstance(trials, list) or len(trials) != used:
        raise ReportError("trials must contain the reported number of observed trials")
    identities = [trial.get("trace_id") if isinstance(trial, dict) else None for trial in trials]
    if any(not isinstance(identity, str) or not identity for identity in identities):
        raise ReportError("each observed trial must identify a trace")
    if len(set(identities)) != used:
        raise ReportError("observed trials must identify distinct traces")
    gating = [r for r in report["aggregate_results"] if legacy or r["mode"] == "gating"]
    for result in gating:
        count = _non_negative_integer(
            result.get("trials" if legacy else "trials_used"), "metric trials"
        )
        if count == 0 or count > used:
            raise ReportError("gating metrics require observed trials within the report count")
    if gating:
        verdicts = {r["verdict"] for r in gating}
        expected = "fail" if "fail" in verdicts else (
            "inconclusive" if "inconclusive" in verdicts else "pass"
        )
        if verdict != expected:
            raise ReportError("overall verdict is inconsistent with gating metrics")

    name = CHECK_NAME
    conclusion = VERDICT_CONCLUSIONS[verdict]
    if not any(r["check_name"] != "agent_process" for r in gating):
        conclusion = "failure"
        summary += "\n\nNo gating metrics: report-only evidence cannot certify this change."
    if configuration_blocked:
        conclusion = "failure"
        summary += "\n\nConfiguration change requires explicit acceptance for this commit."
    if mode == "report-only":
        name = "Maida behavioral report (non-blocking)"
        conclusion = "neutral"
        summary += "\n\nReport-only mode: this report is not merge authorization."
    else:
        summary += "\n\nBlocking mode: FAIL and INCONCLUSIVE prevent approval."

    return {
        "name": name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": conclusion,
        "details_url": details_url,
        "output": {
            "title": f"{name}: {verdict.upper()}",
            "summary": summary,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--details-url", required=True)
    parser.add_argument("--mode", choices=("blocking", "report-only"), default="blocking")
    parser.add_argument("--context", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--cli-status", type=int, choices=(0, 1))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReportError(f"could not read Maida report: {error}") from error
    if not isinstance(report, dict):
        raise ReportError("Maida report root must be an object")

    context = json.loads(args.context.read_text()) if args.context else None
    if context is not None and context["head_sha"] != args.head_sha:
        raise ReportError("evaluation context belongs to another commit")
    payload = build_check_payload(
        report, head_sha=args.head_sha, details_url=args.details_url, mode=args.mode,
        configuration_blocked=context["configuration_blocked"] if context else False,
    )
    if context and args.mode == "blocking":
        payload["output"]["summary"] += (
            f"\n\nTrusted policy revision: `{context['base_sha']}`."
            f"\n\nCandidate configuration SHA-256: `{context['configuration_sha256']}`."
            f"\n\nConfiguration acceptance: {context['configuration_status']}."
            f"\n\nEvaluated baseline SHA-256: `{context['baseline_sha256']}`."
        )
    if args.cli_status is not None and args.cli_status != int(report["verdict"] == "fail"):
        raise ReportError("CLI exit status is inconsistent with the reported verdict")
    if args.markdown:
        if not args.markdown.read_text(encoding="utf-8").strip():
            raise ReportError("CLI Markdown report is empty; refusing to publish")
        with args.markdown.open("a", encoding="utf-8") as markdown:
            markdown.write(
                "\n\n### Action merge decision\n\n"
                f"Mode: **{args.mode}**. GitHub conclusion: **{payload['conclusion']}**.\n\n"
            )
            if args.mode == "report-only":
                markdown.write("This report is not merge authorization.\n")
            else:
                markdown.write("Only a behavioral PASS with accepted configuration and successful check publication can authorize this commit.\n")
                if context:
                    markdown.write(
                        f"\nConfiguration: **{context['configuration_status']}**. "
                        "Policy always comes from the trusted PR base. "
                        "See the named check for revision and configuration hashes.\n"
                    )
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"verdict={report['verdict']}")
    print(f"conclusion={payload['conclusion']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
