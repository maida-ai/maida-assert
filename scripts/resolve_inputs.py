"""Resolve blocking evaluation inputs from the PR's immutable base revision.

The caller must check out the exact candidate with history. Dispatch events
are verified against the live GitHub PR before selecting trusted configuration.
Acceptance is a trusted workflow input, never a file or claim from the PR.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath


class ConfigurationError(ValueError):
    """Evaluation cannot safely select authoritative configuration."""


def _git(workspace: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "--no-replace-objects", *args], cwd=workspace,
        capture_output=True, check=False,
    )
    if result.returncode:
        raise ConfigurationError(
            "Cannot read trusted Git revisions; check out the PR head with fetch-depth: 0."
        )
    return result.stdout


def _path(value: str) -> str:
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ".." in path.parts
            or str(path) != value or any(ord(c) < 32 for c in value)):
        raise ConfigurationError("Policy and baseline must be normalized repository-relative paths.")
    return value


def _configuration(workspace: Path, revision: str, paths: set[str]) -> dict:
    files = {}
    for entry in _git(workspace, "ls-tree", "-rz", revision).split(b"\0"):
        if not entry:
            continue
        info, name = entry.split(b"\t", 1)
        name = name.decode("utf-8")
        if not (name == ".maida" or name.startswith(".maida/") or name in paths):
            continue
        mode, kind, oid = info.decode().split()
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ConfigurationError("Maida configuration must contain regular files, not symlinks or submodules.")
        data = _git(workspace, "cat-file", "blob", oid)
        files[name] = {"mode": mode, "sha256": hashlib.sha256(data).hexdigest(), "data": data}
    return files


def _digest(files: dict) -> str:
    manifest = {name: {k: v for k, v in item.items() if k != "data"}
                for name, item in files.items()}
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def resolve(*, workspace: Path, destination: Path, mode: str, event: dict,
            event_name: str, repository: str, policy: str, baseline: str,
            extra_args: str, acceptance: str) -> dict:
    if mode not in {"blocking", "report-only"}:
        raise ConfigurationError("mode must be blocking or report-only.")
    head = _git(workspace, "rev-parse", "HEAD").decode().strip()
    if any(any(ord(c) < 32 for c in value) for value in (policy, baseline)):
        raise ConfigurationError("Policy and baseline paths must not contain control characters.")
    if mode == "report-only":
        for value in (policy, baseline):
            if value and not (workspace / value).is_file():
                raise ConfigurationError(f"Evaluation input file does not exist: {value}")
        if not (policy or baseline):
            raise ConfigurationError("Either baseline, policy, or both must be provided.")
        return {"policy": policy, "baseline": baseline, "head_sha": head,
                "pr_number": str(event.get("pull_request", {}).get("number", "")),
                "configuration_blocked": False}
    if event_name != "pull_request":
        raise ConfigurationError("Blocking mode requires a pull_request event; use report-only for other events.")
    if extra_args.strip():
        raise ConfigurationError("Blocking mode forbids extra-args overrides; set trial budgets and thresholds in base policy.")
    policy = _path(policy)
    paths = {policy}
    if baseline:
        paths.add(_path(baseline))
    pr = event.get("pull_request", {})
    base = pr.get("base", {}).get("sha", "")
    expected_head = pr.get("head", {}).get("sha", "")
    if (not re.fullmatch(r"[0-9a-f]{40}", base)
            or not re.fullmatch(r"[0-9a-f]{40}", expected_head)
            or pr.get("base", {}).get("repo", {}).get("full_name") != repository):
        raise ConfigurationError("Missing trusted PR base/head identity in the GitHub event.")
    if head != expected_head:
        raise ConfigurationError("Check out github.event.pull_request.head.sha, not the merge or default-branch revision.")
    if _git(workspace, "diff", "HEAD", "--", "."):
        raise ConfigurationError("Blocking evaluation requires a clean checkout of the exact PR head.")
    if _git(workspace, "ls-files", "--others", "--", ".maida", *sorted(paths)):
        raise ConfigurationError("Blocking evaluation requires a clean checkout without untracked Maida configuration.")
    trusted = _configuration(workspace, base, paths)
    candidate = _configuration(workspace, head, paths)
    if policy not in trusted or (baseline and baseline not in trusted):
        raise ConfigurationError("Policy/baseline missing at the trusted base revision; establish configuration on the base branch first.")
    digest = _digest(candidate)
    changed = _digest(trusted) != digest
    expected_acceptance = f"{base}:{head}:{digest}"
    accepted = changed and acceptance == expected_acceptance
    if acceptance and not accepted:
        raise ConfigurationError("Stale or invalid configuration acceptance; review this base, head, and configuration digest again.")
    destination.mkdir(parents=True, exist_ok=True)
    policy_file = destination / "policy.yaml"
    policy_file.write_bytes(trusted[policy]["data"])
    baseline_hash = "none"
    baseline_file = ""
    if baseline:
        selected = candidate if accepted else trusted
        if baseline not in selected:
            raise ConfigurationError("An accepted change cannot remove the configured baseline.")
        baseline_file = str(destination / "baseline.json")
        Path(baseline_file).write_bytes(selected[baseline]["data"])
        baseline_hash = selected[baseline]["sha256"]
    return {
        "policy": str(policy_file), "baseline": baseline_file,
        "pr_number": str(pr.get("number", "")),
        "head_sha": head, "base_sha": base, "baseline_revision": head if accepted else base,
        "configuration_sha256": digest,
        "baseline_sha256": baseline_hash, "configuration_blocked": changed and not accepted,
        "configuration_status": "accepted" if accepted else "requires acceptance" if changed else "unchanged",
        "acceptance": expected_acceptance if changed else "",
    }


def main() -> int:
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        event_name = os.environ["GITHUB_EVENT_NAME"]
        if event_name == "repository_dispatch" and (
            os.environ["MODE"] == "blocking" or event.get("action") == "maida_baseline_updated"
        ):
            from pr_context import live_context
            pull, _ = live_context()
            event = {"pull_request": pull}
            event_name = "pull_request"
        destination = Path(tempfile.mkdtemp(prefix="maida-trusted-", dir=os.environ["RUNNER_TEMP"]))
        context = resolve(
            workspace=Path(os.environ["GITHUB_WORKSPACE"]), destination=destination,
            mode=os.environ["MODE"], event=event,
            event_name=event_name, repository=os.environ["GITHUB_REPOSITORY"],
            policy=os.environ["POLICY"], baseline=os.environ["BASELINE"],
            extra_args=os.environ.get("EXTRA_ARGS", ""), acceptance=os.environ.get("CONFIGURATION_ACCEPTANCE", ""),
        )
        context_path = destination / "context.json"
        context_path.write_text(json.dumps(context, indent=2) + "\n")
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            for name in ("policy", "baseline", "head_sha", "base_sha", "baseline_revision", "pr_number"):
                if name not in context:
                    continue
                output.write(f"{name}={context[name]}\n")
            output.write(f"context={context_path}\n")
        if context.get("acceptance"):
            print("Configuration changed. Review the full .maida/, policy, and baseline diff.")
            print(f"Maintainer acceptance value: {context['acceptance']}")
            print(f"Configuration status: {context['configuration_status']}")
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(f"Maida configuration error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
