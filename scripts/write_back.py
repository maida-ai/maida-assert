#!/usr/bin/env python3
"""Regenerate, commit, push, and dispatch a Maida baseline update."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping, Sequence


BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
COMMIT_MESSAGE = "chore(maida): accept baseline update"
DISPATCH_EVENT = "maida_baseline_updated"


class WriteBackError(RuntimeError):
    """A safe, actionable write-back failure."""


def _run(
    args: Sequence[str],
    *,
    cwd: Path,
    environ: Mapping[str, str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        env=dict(environ),
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise WriteBackError(f"{args[0]} failed: {detail}")
    return result


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--head-repository", required=True)
    parser.add_argument("--head-branch", required=True)
    parser.add_argument("--expected-head-sha", required=True)
    parser.add_argument("--run-id", default="")
    return parser.parse_args(argv)


def _required_env(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise WriteBackError(f"Required environment variable is missing: {name}")
    return value


def _validate_request(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str],
    cwd: Path,
) -> tuple[Path, str, str]:
    current_repository = _required_env(environ, "GITHUB_REPOSITORY")
    if args.head_repository.casefold() != current_repository.casefold():
        raise WriteBackError(
            "Fork pull requests are not supported for baseline write-back. "
            "Push the branch to the base repository or update the baseline locally."
        )
    if not args.reason.strip():
        raise WriteBackError("An acceptance reason is required.")
    if args.pr_number <= 0:
        raise WriteBackError("The pull request number must be a positive integer.")

    repository_root = Path(
        _run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            environ=environ,
        ).stdout.strip()
    ).resolve()

    branch_check = _run(
        ["git", "check-ref-format", "--branch", args.head_branch],
        cwd=repository_root,
        environ=environ,
        check=False,
    )
    if branch_check.returncode != 0:
        raise WriteBackError(f"Invalid PR head branch: {args.head_branch}")

    current_head = _run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        environ=environ,
    ).stdout.strip()
    if current_head != args.expected_head_sha:
        raise WriteBackError(
            f"Checked-out HEAD {current_head} does not match expected PR head "
            f"{args.expected_head_sha}. Rerun against the latest PR commit."
        )

    staged = _run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repository_root,
        environ=environ,
        check=False,
    )
    if staged.returncode == 1:
        raise WriteBackError(
            "The checkout already has staged changes; refusing to mix them into "
            "the baseline write-back commit."
        )
    if staged.returncode != 0:
        raise WriteBackError("Unable to inspect staged changes before write-back.")

    candidate = (cwd / args.baseline).resolve()
    try:
        baseline_relative = candidate.relative_to(repository_root).as_posix()
    except ValueError as exc:
        raise WriteBackError("The baseline path must be inside the repository.") from exc
    if not candidate.is_file():
        raise WriteBackError(f"Baseline file does not exist: {baseline_relative}")

    tracked = _run(
        ["git", "ls-files", "--error-unmatch", "--", baseline_relative],
        cwd=repository_root,
        environ=environ,
        check=False,
    )
    if tracked.returncode != 0:
        raise WriteBackError(
            f"Baseline must already be tracked by Git: {baseline_relative}"
        )

    return repository_root, baseline_relative, current_head


def _accept_baseline(
    *,
    repository_root: Path,
    baseline: str,
    reason: str,
    run_id: str,
    environ: Mapping[str, str],
) -> None:
    command = ["maida", "accept"]
    if run_id.strip():
        command.append(run_id.strip())
    command.extend(["--baseline", baseline, "--reason", reason.strip()])
    result = _run(command, cwd=repository_root, environ=environ)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)


def _commit_and_push(
    *,
    repository_root: Path,
    baseline: str,
    branch: str,
    environ: Mapping[str, str],
) -> tuple[bool, str]:
    _run(
        ["git", "add", "--", baseline],
        cwd=repository_root,
        environ=environ,
    )
    staged_paths = _run(
        ["git", "diff", "--cached", "--name-only", "-z"],
        cwd=repository_root,
        environ=environ,
    ).stdout.split("\0")
    staged_paths = [path for path in staged_paths if path]
    if not staged_paths:
        current_head = _run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            environ=environ,
        ).stdout.strip()
        return False, current_head
    if staged_paths != [baseline]:
        raise WriteBackError(
            "Write-back attempted to stage files other than the configured baseline."
        )

    _run(
        ["git", "config", "user.name", BOT_NAME],
        cwd=repository_root,
        environ=environ,
    )
    _run(
        ["git", "config", "user.email", BOT_EMAIL],
        cwd=repository_root,
        environ=environ,
    )
    _run(
        ["git", "commit", "-m", COMMIT_MESSAGE, "--", baseline],
        cwd=repository_root,
        environ=environ,
    )
    commit_sha = _run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root,
        environ=environ,
    ).stdout.strip()
    _run(
        ["git", "push", "origin", f"HEAD:refs/heads/{branch}"],
        cwd=repository_root,
        environ=environ,
    )
    return True, commit_sha


def _write_outputs(
    *,
    output_path: Path,
    changed: bool,
    commit_sha: str,
    head_sha: str,
) -> None:
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"changed={'true' if changed else 'false'}\n")
        output.write(f"commit-sha={commit_sha}\n")
        output.write(f"head-sha={head_sha}\n")


def _dispatch_repository(
    *,
    api_url: str,
    repository: str,
    token: str,
    payload: dict,
) -> None:
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/repos/{repository}/dispatches",
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 204:
                raise WriteBackError(
                    f"repository dispatch returned HTTP {response.status}"
                )
    except urllib.error.HTTPError as exc:
        raise WriteBackError(
            f"repository dispatch returned HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise WriteBackError(f"repository dispatch failed: {exc.reason}") from exc


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> int:
    environment = dict(os.environ if environ is None else environ)
    working_directory = Path.cwd() if cwd is None else Path(cwd)
    pushed = False
    head_sha = ""
    try:
        args = _parse_args(argv)
        token = _required_env(environment, "GITHUB_TOKEN")
        api_url = _required_env(environment, "GITHUB_API_URL")
        output_path = Path(_required_env(environment, "GITHUB_OUTPUT"))
        repository_root, baseline, _ = _validate_request(
            args,
            environ=environment,
            cwd=working_directory,
        )
        _accept_baseline(
            repository_root=repository_root,
            baseline=baseline,
            reason=args.reason,
            run_id=args.run_id,
            environ=environment,
        )
        changed, head_sha = _commit_and_push(
            repository_root=repository_root,
            baseline=baseline,
            branch=args.head_branch,
            environ=environment,
        )
        pushed = changed
        commit_sha = head_sha if changed else ""
        _write_outputs(
            output_path=output_path,
            changed=changed,
            commit_sha=commit_sha,
            head_sha=head_sha,
        )
        payload = {
            "event_type": DISPATCH_EVENT,
            "client_payload": {
                "pr_number": args.pr_number,
                "ref": args.head_branch,
                "sha": head_sha,
                "baseline": baseline,
            },
        }
        _dispatch_repository(
            api_url=api_url,
            repository=args.head_repository,
            token=token,
            payload=payload,
        )
        if changed:
            print(f"Baseline write-back commit pushed: {head_sha}")
        else:
            print(f"Baseline already matched; fresh gate requested for: {head_sha}")
        return 0
    except WriteBackError as exc:
        if pushed:
            print(
                "error: baseline commit was pushed, but the fresh-gate dispatch "
                f"failed: {exc}. Retry the write-back command to dispatch {head_sha}.",
                file=sys.stderr,
            )
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
