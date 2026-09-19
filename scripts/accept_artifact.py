#!/usr/bin/env python3
"""Data-only acceptance boundary. Run the writer on a fresh trusted runner.

The candidate controls its evidence, never the authorization context. No source
file, Git hook, package, or command from the candidate is executed by the writer.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import stat
import sys
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from urllib.parse import quote

# Also works with python -I; only this reviewed Action directory is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from accept_command import CommandError, _request_json, _write_outputs

MAX_BYTES = 1024 * 1024


def _json(data):
    try:
        value = json.loads(data, parse_constant=lambda _: None)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CommandError("Invalid acceptance JSON.") from exc
    if not isinstance(value, dict):
        raise CommandError("Acceptance JSON must be an object.")
    return value


def _path(value, kind):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value
            or any(part in {"..", ".git", ".github"} for part in path.parts)
            or any(ord(c) < 32 for c in value) or "\\" in value):
        raise CommandError(f"Invalid {kind} repository-relative path.")
    return value


def _pull(repository, pr_number, login, api):
    permission = api("GET", f"/repos/{repository}/collaborators/{quote(login, safe='')}/permission")
    if permission.get("permission") not in {"write", "admin"}:
        raise CommandError("Acceptance requires current write access to this repository.")
    pull = api("GET", f"/repos/{repository}/pulls/{pr_number}")
    if pull.get("state") != "open":
        raise CommandError("Acceptance requires an open pull request.")
    head, base = pull["head"], pull["base"]
    if head["repo"]["full_name"].casefold() != repository.casefold():
        raise CommandError("Fork pull requests cannot use baseline write-back.")
    if head["ref"] == base["ref"]:
        raise CommandError("PR head cannot be the base branch.")
    for ref in (head, base):
        if not re.fullmatch(r"[0-9a-f]{40}", ref["sha"]):
            raise CommandError("Invalid PR revision metadata.")
    return pull


def _snapshot(repository, sha, paths, api):
    commit = api("GET", f"/repos/{repository}/git/commits/{sha}")
    tree_sha = commit["tree"]["sha"]
    tree = api("GET", f"/repos/{repository}/git/trees/{tree_sha}?recursive=1")
    if tree.get("truncated") is not False:
        raise CommandError("Repository tree is incomplete; cannot verify acceptance paths.")
    entries = {item["path"]: item for item in tree["tree"]}
    files = {}
    for path in paths:
        entry = entries.get(path, {})
        if entry.get("mode") != "100644" or entry.get("type") != "blob":
            raise CommandError(f"Acceptance path must be a tracked regular file: {path}")
        blob = api("GET", f"/repos/{repository}/git/blobs/{entry['sha']}")
        if blob.get("encoding") != "base64":
            raise CommandError(f"Cannot read acceptance path: {path}")
        content = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
        if len(content) > MAX_BYTES:
            raise CommandError(f"Acceptance file exceeds size limit: {path}")
        files[path] = content
    return tree_sha, files


def prepare_context(*, repository, pr_number, login, reason, baseline, policy,
                    run_id, run_attempt, comment_id, api_url, token):
    _path(baseline, "baseline")
    _path(policy, "policy")
    if not baseline.endswith(".json") or baseline == policy:
        raise CommandError("The baseline must be a separate JSON file.")
    if not reason.strip() or len(reason) > 500 or any(ord(c) < 32 for c in reason):
        raise CommandError("A single-line acceptance reason of at most 500 characters is required.")
    api = lambda method, path: _request_json(method, path, api_url=api_url, token=token)
    pull = _pull(repository, pr_number, login, api)
    _, files = _snapshot(repository, pull["head"]["sha"], [baseline, policy], api)
    _, base_files = _snapshot(repository, pull["base"]["sha"], [policy], api)
    if files[policy] != base_files[policy]:
        raise CommandError("Changed policy cannot be accepted with a baseline; review and merge policy separately.")
    return {
        "version": 1, "repository": repository, "pr_number": pr_number,
        "login": login, "reason": reason, "baseline": baseline, "policy": policy,
        "head_sha": pull["head"]["sha"], "head_branch": pull["head"]["ref"],
        "base_sha": pull["base"]["sha"], "baseline_sha256": sha256(files[baseline]).hexdigest(),
        "policy_sha256": sha256(files[policy]).hexdigest(),
        "run_id": run_id, "run_attempt": run_attempt, "comment_id": comment_id,
    }


def validate_context(context, api):
    pull = _pull(context["repository"], context["pr_number"], context["login"], api)
    for side, key in (("head", "head_sha"), ("base", "base_sha")):
        if pull[side]["sha"] != context[key]:
            raise CommandError(f"PR {side} changed; request acceptance again for the latest revision.")
    if pull["head"]["ref"] != context["head_branch"]:
        raise CommandError("PR head branch changed; request acceptance again.")
    tree, files = _snapshot(context["repository"], context["head_sha"], [context["baseline"], context["policy"]], api)
    for kind in ("baseline", "policy"):
        if sha256(files[context[kind]]).hexdigest() != context[f"{kind}_sha256"]:
            raise CommandError(f"Changed {kind}; request acceptance again.")
    _, base_files = _snapshot(context["repository"], context["base_sha"], [context["policy"]], api)
    if base_files[context["policy"]] != files[context["policy"]]:
        raise CommandError("Changed policy cannot be accepted with a baseline.")
    return tree, files


def _read_artifact(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise CommandError("Artifact directory must be a regular directory.")
    if sorted(p.name for p in directory.iterdir()) != ["acceptance.json"]:
        raise CommandError("Artifact must contain only acceptance.json; extra files are refused.")
    path = directory / "acceptance.json"
    if not stat.S_ISREG(path.lstat().st_mode):
        raise CommandError("Acceptance artifact must be a regular file, not a symlink.")
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise CommandError("Acceptance artifact exceeds size limit.")
    return data, _json(data)


def write_artifact(*, context, artifact_directory, repository, run_id, run_attempt, api_url, token):
    if (context["repository"] != repository or context["run_id"] != run_id
            or context["run_attempt"] != run_attempt):
        raise CommandError("Acceptance binding is from a different repository or workflow attempt.")
    data, artifact = _read_artifact(artifact_directory)
    if set(artifact) != {"context", "baseline"} or artifact["context"] != context:
        raise CommandError("Artifact binding does not match the trusted authorization context.")
    baseline = artifact["baseline"]
    if (not isinstance(baseline, dict) or not isinstance(baseline.get("summary"), dict)
            or not isinstance(baseline.get("source_run_id"), str) or not baseline["source_run_id"]
            or baseline.get("final_status") not in {"ok", "error"}
            or not isinstance(baseline.get("schema_version"), str)):
        raise CommandError("Artifact must describe a completed Maida baseline.")
    api = lambda method, path, **kwargs: _request_json(method, path, api_url=api_url, token=token, **kwargs)
    tree, files = validate_context(context, api)
    previous = _json(files[context["baseline"]])
    # These are data-shape checks, not a behavioral PASS or proof of execution.
    if baseline["schema_version"] != previous.get("schema_version"):
        raise CommandError("Baseline schema changed; update the baseline locally with review.")
    baseline.pop("acceptance", None)
    old = {key: value for key, value in previous.items() if key not in {"acceptance", "source_run_id", "created_at"}}
    new = {key: value for key, value in baseline.items() if key not in {"source_run_id", "created_at"}}
    changed = old != new
    head_sha = context["head_sha"]
    if changed:
        baseline["acceptance"] = {
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "accepted_by": context["login"], "reason": context["reason"],
            "source_run_id": baseline["source_run_id"],
            "source": {"repository": repository, "pull_request": {"number": context["pr_number"]}, "commit_sha": context["head_sha"]},
            "previous_baseline": {"path": context["baseline"], "sha256": context["baseline_sha256"]},
            "policy": {"path": context["policy"], "sha256": context["policy_sha256"], "base_sha": context["base_sha"]},
            "capture": {"run_id": run_id, "run_attempt": run_attempt, "comment_id": context["comment_id"], "artifact_sha256": sha256(data).hexdigest()},
            "verdict": {"outcome": "accepted", "summary": "Intentional baseline update; fresh gate results required."},
        }
        created_tree = api("POST", f"/repos/{repository}/git/trees", payload={"base_tree": tree, "tree": [{"path": context["baseline"], "mode": "100644", "type": "blob", "content": json.dumps(baseline, indent=2, allow_nan=False) + "\n"}]})
        commit = api("POST", f"/repos/{repository}/git/commits", payload={
            "message": "chore(maida): accept baseline update", "tree": created_tree["sha"],
            "parents": [head_sha], "author": {"name": "github-actions[bot]", "email": "41898282+github-actions[bot]@users.noreply.github.com"},
        })
        # Recheck permission, open state and both refs immediately before mutation.
        validate_context(context, api)
        head_sha = commit["sha"]
        api("PATCH", f"/repos/{repository}/git/refs/heads/{quote(context['head_branch'], safe='/')}", payload={"sha": head_sha, "force": False})
    try:
        api("POST", f"/repos/{repository}/dispatches", payload={"event_type": "maida_baseline_updated", "client_payload": {"pr_number": context["pr_number"], "ref": context["head_branch"], "sha": head_sha, "baseline": context["baseline"]}})
    except CommandError as exc:
        raise CommandError(f"Baseline head is {head_sha}, but fresh-gate dispatch failed. Request acceptance again to retry.") from exc
    return {"changed": str(changed).lower(), "commit-sha": head_sha if changed else "", "head-sha": head_sha, "message": "Fresh gate results and configuration review are required for this head."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-directory", required=True, type=Path)
    args = parser.parse_args()
    try:
        context = _json(os.environ["MAIDA_ACCEPT_CONTEXT"])
        result = write_artifact(context=context, artifact_directory=args.artifact_directory,
                                repository=os.environ["GITHUB_REPOSITORY"], run_id=os.environ["GITHUB_RUN_ID"],
                                run_attempt=os.environ["GITHUB_RUN_ATTEMPT"], api_url=os.environ["GITHUB_API_URL"], token=os.environ["GITHUB_TOKEN"])
        _write_outputs(Path(os.environ["GITHUB_OUTPUT"]), result)
        print(result["message"])
        return 0
    except (CommandError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: acceptance failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
