#!/usr/bin/env python3
"""Authorize and report the ``/maida accept`` pull-request command."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Mapping, Sequence


_COMMAND = re.compile(r"^/maida accept(?:[ \t]+(.+?))?[ \t]*$")
_MAX_REASON_LENGTH = 500


class CommandError(RuntimeError):
    """An actionable command-handler failure."""


def parse_accept_command(body: str, *, login: str) -> str:
    """Return the acceptance reason encoded by one exact command line."""
    if "\n" in body or "\r" in body:
        raise CommandError("Use `/maida accept [optional reason]` on one line.")
    match = _COMMAND.fullmatch(body.strip())
    if match is None:
        raise CommandError("Use `/maida accept [optional reason]` exactly.")
    reason = (match.group(1) or "").strip()
    if len(reason) > _MAX_REASON_LENGTH:
        raise CommandError("The acceptance reason must be 500 characters or fewer.")
    return reason or f"Accepted via /maida accept by @{login}"


def _request_json(
    method: str,
    path: str,
    *,
    api_url: str,
    token: str,
    payload: dict | None = None,
) -> dict:
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}{path}",
        data=data,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
    except urllib.error.HTTPError as exc:
        raise CommandError(f"GitHub API returned HTTP {exc.code} for {path}") from exc
    except urllib.error.URLError as exc:
        raise CommandError(f"GitHub API request failed: {exc.reason}") from exc
    if not content:
        return {}
    result = json.loads(content)
    if not isinstance(result, dict):
        raise CommandError(f"GitHub API returned an invalid response for {path}")
    return result


def _write_outputs(output_path: Path, values: Mapping[str, str]) -> None:
    with output_path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise CommandError(f"Unsafe multiline action output: {key}")
            output.write(f"{key}={value}\n")


def _post_comment(
    *,
    repository: str,
    issue_number: int,
    body: str,
    api_url: str,
    token: str,
) -> None:
    _request_json(
        "POST",
        f"/repos/{repository}/issues/{issue_number}/comments",
        api_url=api_url,
        token=token,
        payload={"body": body},
    )


def prepare_command(
    *,
    event: dict,
    baseline: str,
    api_url: str,
    token: str,
    output_path: Path,
) -> None:
    issue = event.get("issue") or {}
    comment = event.get("comment") or {}
    repository = (event.get("repository") or {}).get("full_name", "")
    login = (comment.get("user") or {}).get("login", "")
    issue_number = issue.get("number")
    if not repository or not login or not isinstance(issue_number, int):
        raise CommandError("The issue_comment event is missing repository metadata.")
    if not issue.get("pull_request"):
        _write_outputs(output_path, {"handled": "false", "authorized": "false"})
        return

    try:
        reason = parse_accept_command(str(comment.get("body", "")), login=login)
    except CommandError as exc:
        _post_comment(
            repository=repository,
            issue_number=issue_number,
            body=f"@{login}, {exc}",
            api_url=api_url,
            token=token,
        )
        _write_outputs(output_path, {"handled": "true", "authorized": "false"})
        return

    if not baseline.strip():
        _post_comment(
            repository=repository,
            issue_number=issue_number,
            body=(
                f"@{login}, Maida baseline write-back is not configured yet. "
                "Set `MAIDA_BASELINE` in `.github/workflows/maida.yml`, then rerun "
                "`/maida accept [optional reason]`."
            ),
            api_url=api_url,
            token=token,
        )
        _write_outputs(output_path, {"handled": "true", "authorized": "false"})
        return

    permission = _request_json(
        "GET",
        "/repos/"
        f"{repository}/collaborators/{urllib.parse.quote(login, safe='')}/permission",
        api_url=api_url,
        token=token,
    ).get("permission")
    if permission not in {"write", "admin"}:
        _post_comment(
            repository=repository,
            issue_number=issue_number,
            body=(
                f"@{login}, `/maida accept` requires write access to this repository. "
                "A maintainer can review the behavior change and run the command."
            ),
            api_url=api_url,
            token=token,
        )
        _write_outputs(output_path, {"handled": "true", "authorized": "false"})
        return

    pull = _request_json(
        "GET",
        f"/repos/{repository}/pulls/{issue_number}",
        api_url=api_url,
        token=token,
    )
    head = pull.get("head") or {}
    head_repository = (head.get("repo") or {}).get("full_name", "")
    head_branch = str(head.get("ref", ""))
    head_sha = str(head.get("sha", ""))
    if head_repository.casefold() != repository.casefold():
        _post_comment(
            repository=repository,
            issue_number=issue_number,
            body=(
                f"@{login}, Fork pull requests cannot use baseline write-back. "
                "Update the baseline locally or move the branch into the base repository."
            ),
            api_url=api_url,
            token=token,
        )
        _write_outputs(output_path, {"handled": "true", "authorized": "false"})
        return
    if not head_branch or not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        raise CommandError("GitHub returned invalid PR head metadata.")

    _write_outputs(
        output_path,
        {
            "handled": "true",
            "authorized": "true",
            "reason": reason,
            "pr-number": str(issue_number),
            "head-repository": head_repository,
            "head-branch": head_branch,
            "head-sha": head_sha,
        },
    )


def finalize_command(
    *,
    event: dict,
    outcome: str,
    changed: str,
    commit_sha: str,
    head_sha: str,
    api_url: str,
    token: str,
    server_url: str,
    run_id: str,
) -> None:
    repository = (event.get("repository") or {}).get("full_name", "")
    issue_number = (event.get("issue") or {}).get("number")
    login = ((event.get("comment") or {}).get("user") or {}).get("login", "")
    if not repository or not isinstance(issue_number, int):
        raise CommandError("The issue_comment event is missing repository metadata.")

    if outcome == "success" and changed == "true":
        short_sha = commit_sha[:8]
        body = (
            f"✅ Baseline accepted by @{login} in "
            f"[`{short_sha}`]({server_url}/{repository}/commit/{commit_sha}). "
            f"A fresh Maida gate was requested for `{head_sha[:8]}`."
        )
    elif outcome == "success":
        body = (
            f"✅ The baseline already matches the accepted run, @{login}. "
            f"A fresh Maida gate was requested for `{head_sha[:8]}`."
        )
    else:
        body = (
            f"❌ Maida could not accept this baseline change, @{login}. "
            f"Review the [workflow run]({server_url}/{repository}/actions/runs/{run_id}) "
            "and retry after fixing the reported error. No baseline update was confirmed."
        )
    _post_comment(
        repository=repository,
        issue_number=issue_number,
        body=body,
        api_url=api_url,
        token=token,
    )


def _required_env(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise CommandError(f"Required environment variable is missing: {name}")
    return value


def _load_event(environ: Mapping[str, str]) -> dict:
    path = Path(_required_env(environ, "GITHUB_EVENT_PATH"))
    event = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(event, dict):
        raise CommandError("GITHUB_EVENT_PATH does not contain an object.")
    return event


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="operation", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--baseline", default="")
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--outcome", required=True)
    finalize.add_argument("--changed", default="false")
    finalize.add_argument("--commit-sha", default="")
    finalize.add_argument("--head-sha", default="")
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    environment = dict(os.environ if environ is None else environ)
    try:
        args = _parse_args(argv)
        event = _load_event(environment)
        api_url = _required_env(environment, "GITHUB_API_URL")
        token = _required_env(environment, "GITHUB_TOKEN")
        if args.operation == "prepare":
            prepare_command(
                event=event,
                baseline=args.baseline,
                api_url=api_url,
                token=token,
                output_path=Path(_required_env(environment, "GITHUB_OUTPUT")),
            )
        else:
            finalize_command(
                event=event,
                outcome=args.outcome,
                changed=args.changed,
                commit_sha=args.commit_sha,
                head_sha=args.head_sha,
                api_url=api_url,
                token=token,
                server_url=_required_env(environment, "GITHUB_SERVER_URL"),
                run_id=_required_env(environment, "GITHUB_RUN_ID"),
            )
        return 0
    except (CommandError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
