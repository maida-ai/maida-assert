"""Write an actionable job summary even when no valid gate report exists."""

import json
import os
from pathlib import Path


RECOVERY = {
    "VALIDATION": "Configure exactly one of agent-script or trace-command in the workflow, then rerun.",
    "TRUST": "Read the input-resolution error. Check that the policy and baseline exist on the trusted base and that the checkout matches the current PR head; review configuration changes separately.",
    "INSTALL": "Read the setup error and restore the locked Maida installation and supported runner/Python version before rerunning.",
    "GATE": "Reproduce the configured trace source locally in the project environment. It must record exactly one completed Maida run per trial. Fix missing dependencies or capture errors before rerunning; use maida demo --regression to check the installation.",
    "IDENTITY": "The PR identity could not be verified. Start a fresh workflow for the latest PR head and base; old results cannot authorize a later commit.",
    "CHECK": "Inspect the report schema, trial evidence and CLI exit status in the failed preparation step. Fix the reported error before rerunning; missing or aborted evidence is not a behavioral FAIL or PASS.",
    "PUBLICATION": "A maintainer should check checks: write permission and repository check settings, then rerun on the same current head. Fork tokens may be read-only; do not expose write credentials to candidate code.",
}


def main():
    # Stage outcomes come from the runner, never from a stale report on disk.
    failed = next((stage for stage in RECOVERY if os.environ.get(stage) == "failure"), None)
    if failed:
        summary = (
            "## Maida setup/publication incomplete\n\nNo merge authorization was issued.\n\n"
            "### Next safe action\n\n" + RECOVERY[failed]
        )
    elif os.environ.get("CHECK") == "success" and os.environ.get("PUBLICATION") == "success":
        try:
            summary = json.loads(Path("maida-check-payload.json").read_text())["output"]["summary"]
        except (OSError, ValueError, KeyError, TypeError):
            summary = "### Next safe action\n\nOpen the check preparation logs; the validated report is unavailable. No merge authorization is established by this summary."
    else:
        summary = "### Next safe action\n\nOpen the first failed or cancelled setup step and correct it before rerunning for the latest PR head. No merge authorization was issued."
    print(summary)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(summary + "\n")


if __name__ == "__main__":
    main()
