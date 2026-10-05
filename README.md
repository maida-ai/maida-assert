# Add Maida's PR gate

**Check an agent change before merge.** Tests may say the answer or result looks fine while the agent's execution behavior regressed. After you have proved that distinction locally, this Action brings the reviewed checks into the PR and shows **PASS, FAIL, or INCONCLUSIVE** to reviewers.

This core product repository owns Maida's GitHub boundary. It wraps the [Maida engine and CLI](https://github.com/maida-ai/maida); [maida-tutorials](https://github.com/maida-ai/maida-tutorials) owns the canonical runnable experience.

## Start with a useful local report

If you have not checked your coding agent yet, use the [coding-agent getting started guide](https://maida.ai/docs/getting-started/) first. With Python 3.12–3.14 and Claude Code in your own Git repository:

```bash
uv tool install "maida-ai==0.6.1"

cd my-repo
maida init

# Run one normal Claude Code task and exit the session.

maida check
# Then run the exact "View:" command printed by Maida.
```

Approve setup and start a new agent session. **Success looks like `3 active checks passed`**, your task's trace ID, and its viewer command. If Maida is already in the project's uv environment, prefix its commands with `uv run`; plain `claude` works afterward. No tutorial clone is needed.

For example: `maida view 83aa19e3`. Use the command from your own report.

**Runs on your machine or CI runner. No Maida cloud account required.** Task evidence is not uploaded to Maida. Enabling this Action publishes the selected report to your GitHub repository; your agent's normal provider calls remain separate.

## Protect the next agent change

The first local check covers completion, recorded loops, and guardrail events. Next, [review the observed behavior, keep a baseline and policy, and reproduce a safe failure](https://github.com/maida-ai/maida-tutorials/blob/main/guides/coding-agent.md#protect-the-next-agent-change). Keep ordinary correctness tests and evals; Maida checks observed execution behavior, not answer correctness.

Instructions, skills, tools, model configuration, harness code, and application code can all affect behavior. Maida gates the resulting change whether a human or the agent authored it.

For memorable proof, the [canonical storefront demo](https://github.com/maida-ai/maida-tutorials/tree/main/demos/pr-gate) shows **green application tests approving $15 VIP shipping while Maida fails the agent change** for rewriting the protected regression test. It uses released Maida v0.6.1 and runs deterministically offline after installation. That stronger reviewed policy is separate from the first check's three built-in requirements.

## Add the merge boundary

**Bring a repeatable task and reviewed requirements to CI.** An interactive session saved on your laptop is not a repeatable CI task. Before adding the workflow:

- Make the task repeatable with pinned agent/model configuration and explicit budgets; [coding-agent scenarios](https://maida.ai/docs/cli/scenario-run/) describe isolated comparisons.
- Keep the reviewed `.maida/policy.yaml` and baseline on the base branch.
- Prepare a trusted runner that records or imports exactly one completed Maida run per invocation. Install its dependencies and configure its required credentials in CI. Do not feed PR-controlled text into the runner command.
- Use [Action inputs and trace-source requirements](docs/usage.md#inputs) to connect that runner. For an existing traced Python entrypoint, the secondary [Python setup guide](https://github.com/maida-ai/maida/blob/main/docs/python-agent.md) explains `agent-script`.

The template below assumes you have prepared `./scripts/capture-agent-task` as that trusted runner; replace it with your actual command. A scenario's own verdict is not automatically an Action trace source: the runner must supply the completed run the Action evaluates.

Add `.github/workflows/maida-check.yml`:

```yaml
name: Agent Regression Check
on: [pull_request]

permissions:
  contents: read

jobs:
  agent-check:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      checks: write
      pull-requests: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
          persist-credentials: false
      - uses: maida-ai/maida-assert@8ba54c181ef26df9eaecd38bb46a107756297b28 # released Action v0.6.0
        with:
          maida-version: v0.6.1
          trace-command: ./scripts/capture-agent-task
          baseline: .maida/baselines/agent.json
          policy: .maida/policy.yaml
```

The Action revision above is the published release SHA. Explicitly selecting the released **Maida v0.6.1 engine** keeps CI aligned with your local check. This selection bypasses that Action release's bundled dependency lock and emits a warning; review [dependency integrity](SECURITY.md#dependency-integrity). Use the same engine selection and reviewed Action SHA for acceptance capture if you enable it.

**Configure required checks and workflow-file review protection, then verify an actual PR.** Blocking mode evaluates the trusted base policy and refuses FAIL, INCONCLUSIVE, unaccepted configuration changes, and setup/publication errors. Report-only mode is observational. Exit zero alone is not approval.

Follow the [required repository settings](docs/usage.md#blocking-mode-and-required-repository-settings) and [consumer verification](docs/acceptance.md#verify-the-actual-post-accept-loop) for your pinned revision. Test a safe failure and repair, and require fresh results on the new PR head after intentional acceptance. Generating the workflow alone does not establish merge enforcement.

## Investigate and accept a change

The PR report names failed checks and changes against the baseline. Reproduce the task locally and run the exact `View:` command printed in your local report; a CI trace ID is not automatically on your machine. Repair an unintended regression. Accept an intentional baseline or configuration change only after reviewing its evidence and reason, then require a fresh result on the resulting commit.

## Reference

- [Inputs, token permissions, and workflow examples](docs/usage.md).
- [Policy examples](docs/usage.md#policy-example) and [configuration acceptance](docs/usage.md#explicit-configuration-acceptance).
- [Intentional changes and baseline write-back](docs/acceptance.md).
- [Security and dependency integrity](SECURITY.md) and [release provenance](CONTRIBUTING.md#tagged-release-provenance).
- [Action release notes](CHANGELOG.md), [versioning](CONTRIBUTING.md#versioning), and [contributor checks](CONTRIBUTING.md#testing-the-action).
