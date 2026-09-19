#!/usr/bin/env python3
"""Capture untrusted baseline data in a read-only, disposable job."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from hashlib import sha256
from pathlib import Path


def capture(context, agent_script, trace_command, directory):
    if bool(agent_script) == bool(trace_command):
        raise ValueError("Pass exactly one of agent-script or trace-command.")
    if (
        subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        != context["head_sha"]
    ):
        raise ValueError("Checkout does not match the authorized PR head.")
    for kind in ("baseline", "policy"):
        if (
            sha256(Path(context[kind]).read_bytes()).hexdigest()
            != context[f"{kind}_sha256"]
        ):
            raise ValueError(f"Changed {kind}; capture requires the authorized files.")
    # The workflow's job permission is the isolation boundary, not this cleanup.
    env = dict(os.environ)
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        env.pop(key, None)
    with tempfile.TemporaryDirectory(
        prefix="maida-accept-", dir=os.environ["RUNNER_TEMP"]
    ) as temp:
        env["MAIDA_DATA_DIR"] = str(Path(temp) / "runs")
        command = (
            [sys.executable, agent_script]
            if agent_script
            else ["bash", "-o", "pipefail", "-c", trace_command]
        )
        subprocess.run(command, env=env, check=True)
        if subprocess.run(
            ["git", "diff", "--cached", "--quiet"], check=False
        ).returncode:
            raise ValueError(
                "Candidate staged changes; refusing extra staged files during acceptance."
            )
        for kind in ("baseline", "policy"):
            if (
                sha256(Path(context[kind]).read_bytes()).hexdigest()
                != context[f"{kind}_sha256"]
            ):
                raise ValueError(
                    f"Candidate changed the authorized {kind} during capture."
                )
        runs = json.loads(
            subprocess.check_output(["maida", "list", "--json"], env=env, text=True)
        )["runs"]
        if len(runs) != 1 or runs[0].get("status") not in {"ok", "error"}:
            raise ValueError(
                "Trace source must create exactly one completed Maida run."
            )
        result = subprocess.run(
            [
                "maida",
                "assert",
                "--baseline",
                context["baseline"],
                "--policy",
                context["policy"],
                "--format",
                "markdown",
            ],
            env=env,
            capture_output=True,
            text=True,
        )
        if result.returncode not in {0, 1}:
            raise ValueError(
                f"maida assert could not verify the run (exit {result.returncode})."
            )
        # This report stays on the capture runner; it is not merge authorization.
        print(result.stdout, end="")
        baseline_path = Path(temp) / "baseline.json"
        subprocess.run(
            ["maida", "baseline", "--out", str(baseline_path)], env=env, check=True
        )
        baseline = json.loads(baseline_path.read_bytes())
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "acceptance.json").write_text(
            json.dumps({"context": context, "baseline": baseline}, allow_nan=False)
        )


def main():
    try:
        capture(
            json.loads(os.environ["MAIDA_ACCEPT_CONTEXT"]),
            os.environ.get("AGENT_SCRIPT", ""),
            os.environ.get("TRACE_COMMAND", ""),
            Path(os.environ["MAIDA_ARTIFACT_DIRECTORY"]),
        )
        return 0
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        print(f"error: acceptance capture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
