# Action usage and workflows

[Back to the README](../README.md).

The report shows a pass/fail/inconclusive verdict, top behavior changes and failed checks grouped by stable reason code, with concise next steps. Workflow reruns update the existing Maida marker comment in place. The local `maida accept --reason ...` path is for reviewed intentional changes.

Blocking and acceptance features are included in Action `v0.6.0`. Pin a reviewed full SHA and apply the repository settings below, including when using `maida init --github`.

## Read your first report

Action `v0.6.0` adds **Next safe action** to the named check, PR report and workflow summary. The workflow summary remains available when setup fails before a report can be published.

- **PASS:** review the observed coverage and confirm the required checks belong to the current PR head; continue your normal correctness and security review.
- **FAIL:** reproduce the failed check locally and inspect its trace. Fix unintended behavior; accept an intentional baseline change only after reviewing it, then require fresh results on the resulting head.
- **INCONCLUSIVE:** inspect missing evidence and budget feasibility before paying for more trials. Preserve the stated requirement; repeatedly rerunning an infeasible budget cannot establish it.
- **Configuration awaiting acceptance:** review the configuration diff against the trusted base. Acceptance is bound to the current head and digest; changed policy needs separate review.
- **Report-only or no behavioral gating metrics:** review candidate invariants before committing a policy that enforces them. An observation is not merge authorization.
- **Setup or publication incomplete:** follow the first failing step in the workflow summary. Missing traces, malformed reports and insufficient token permissions have different recovery instructions; none is a behavioral PASS.

The `/maida accept` hint appears only for a failed baseline report when the acceptance workflow is enabled. It is not a remedy for missing evidence. If the named check could not be published, follow the workflow summary even if the CLI report says PASS.

A fail-fast stop caused by an observed gating invariant violation is a behavioral FAIL, even when fewer trials ran than the budget. A process failure or an early stop without decisive invariant evidence remains a setup error.

## Usage

Add a workflow to your repository (for example `.github/workflows/maida-check.yml`):

```yaml
name: Agent Regression Check
on: [pull_request]

# Read-only by default; the gate job grants only its required writes.
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
      - uses: maida-ai/maida-assert@v0.6.0
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
```

Pass exactly one trace source. An `agent-script` must instrument the agent with `@trace` or `traced_run()`. A `trace-command` may import an external trace, but it must create exactly one completed Maida run.

### Inputs

Defaults below describe the current unreleased checkout. Published Action `v0.6.0` still defaults to Maida `v0.6.0`; selecting `maida-version: v0.6.1` explicitly bypasses that release's dependency lock. Use the same engine override for the gate and acceptance capture.

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `mode` | no | `blocking` | `blocking` evaluates the trusted PR base policy; `report-only` observes candidate configuration and never authorizes merging. |
| `configuration-acceptance` | no | `''` | Maintainer-controlled `base-SHA:head-SHA:configuration-SHA256` value for a reviewed configuration change. Never read it from PR content. |
| `agent-script` | one trace source | `''` | Path to the Python script that runs the agent. The script must use `@trace` or `traced_run()` so a run is recorded. |
| `trace-command` | one trace source | `''` | Trusted shell command that creates exactly one completed Maida run, such as an importer invocation. Do not include secrets in the command. |
| `baseline` | no | `''` | Path to a baseline JSON file produced by `maida baseline`. If omitted, only the policy is enforced. |
| `policy` | no | `.maida/policy.yaml` | Repository-relative path, required on the trusted base in blocking mode. |
| `maida-version` | no | `v0.6.1` | Version of Maida to install. Use `v<version>` for PyPI or `@<ref>` to track a branch of the [`maida`](https://github.com/maida-ai/maida) repository. |
| `python-version` | no | `3.12` | Python version passed to `actions/setup-python`. |
| `extra-args` | no | `''` | Report-only CLI overrides (for example, `--trials 5 --max-steps 20`). Blocking mode rejects overrides; edit the base policy through review. |
| `post-comment` | no | `true` | When `true` and the workflow runs on a `pull_request` event, the Markdown report is posted as a sticky PR comment. |

### Token permissions

Grant only the scopes each job needs:

- Gate: `contents: read`, `checks: write`; add `pull-requests: write` for sticky comments.
- Acceptance: read-only capture; isolated writer with `contents: write` and `pull-requests: write`.
- Dispatch listener: `statuses: write` for its explicit commit status.

With `post-comment: 'false'`, omit the PR write grant:

```yaml
permissions:
  contents: read
jobs:
  agent-check:
    permissions:
      contents: read
      checks: write
```

This is a permissions excerpt; keep the full workflow steps. Broader token grants are not detected or reduced by the Action. Use the exact job grants and avoid broader PATs. See [GitHub’s permission reference](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#permissions).

### Blocking mode and required repository settings

Blocking mode requires a clean checkout of the exact PR head, the base commit locally available (`fetch-depth: 0`), and a policy/baseline on that base. It supports `pull_request` and verified `maida_baseline_updated` events. The Action reads trusted base files, makes no Git fetches, and publishes only against the evaluated head.

Configure the repository:

- For a pull-request-only workflow, require both `agent-check` and **Maida statistical gate**, with branches up to date. If using the combined acceptance/dispatch listener, require its explicit **Maida / agent-check** status instead; follow the [dispatch protection settings](acceptance.md#baseline-write-back-engine). Keep unrelated required checks.
- Require fresh reviews after new commits and code-owner review of workflows, harness dependencies, policies, baselines and CODEOWNERS.
- Keep the gate unconditional: no path filters, `continue-on-error` or bypasses.

The named check maps PASS to success, FAIL and INCONCLUSIVE to failure. Missing inputs/evidence and publication errors fail blocking evaluation. Setup errors produce no behavioral verdict or empty comment. Sticky-comment errors warn without erasing the check result. Every new head or base needs fresh evaluation.

Fork/read-only tokens cannot publish a blocking success. Do not execute candidate code through `pull_request_target` to bypass that limit. The Action is not a sandbox: workflows, dependencies and candidate execution must be trusted for the runner and token privileges. See [required-check semantics](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

### Explicit configuration acceptance

Changes under `.maida/` or to the configured policy/baseline are separate gated events. Policy always comes from the base; candidate files cannot grade their own changes. Files must be regular tracked files, not symlinks or submodules.

For an intentional change:

1. Review the diff and blocking report.
2. Set the protected repository variable `MAIDA_CONFIGURATION_ACCEPTANCE` to the reported `base-SHA:head-SHA:configuration-SHA256` value.
3. Pass it from the trusted workflow and rerun:

```yaml
          configuration-acceptance: ${{ vars.MAIDA_CONFIGURATION_ACCEPTANCE }}
```

Acceptance clears the configuration event and selects the reviewed candidate baseline. It does not relax the base policy or excuse behavioral failure. Review policy relaxations separately before subsequent agent work.

Protect the variable and workflow; never take acceptance from PR content or artifacts. A changed head, base, file mode or configuration content invalidates it. Clear the variable after merging. Baseline provenance and `/maida accept` comments are not substitutes.

### Report-only mode

Use `mode: report-only` for experiments, schedules and non-PR events. The **Maida behavioral report (non-blocking)** check is neutral for every valid verdict; FAIL and INCONCLUSIVE remain visible. Missing/malformed evidence fails the job, while publication errors warn. Never require this observational check as a merge gate.

## Example workflows

### Minimal: policy-only check

Use this when you don't have a baseline yet but want to enforce hard limits (no loops, no guardrail violations, max steps, etc.) defined in `.maida/policy.yaml`:

```yaml
name: Agent Policy Check
on: [pull_request]

# Read-only by default; the gate job grants only its required writes.
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
      - uses: maida-ai/maida-assert@v0.6.0
        with:
          agent-script: my_agent.py
```

### Report-only baseline comparison with inline overrides

For an observational run, override the trial count and a threshold via `extra-args`. This workflow is not a blocking gate:

```yaml
name: Agent Regression Check
on: [pull_request]

# Read-only by default; the gate job grants only its required writes.
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
      - uses: maida-ai/maida-assert@v0.6.0
        with:
          agent-script: examples/my_agent.py
          baseline: baselines/my_agent.json
          policy: .maida/policy.yaml
          maida-version: 'v0.6.1'
          python-version: '3.12'
          mode: report-only
          extra-args: --trials 5 --max-steps 20
```

### Gate a Langfuse trace

Use `trace-command` when the run comes from an importer instead of a traced Python entrypoint. This example keeps credentials in GitHub secrets and the trace ID in a repository variable:

```yaml
name: Imported Trace Regression Check
on: [pull_request]

permissions:
  contents: read

jobs:
  imported-trace-check:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      checks: write
      pull-requests: write
    env:
      LANGFUSE_PUBLIC_KEY: ${{ secrets.LANGFUSE_PUBLIC_KEY }}
      LANGFUSE_SECRET_KEY: ${{ secrets.LANGFUSE_SECRET_KEY }}
      LANGFUSE_TRACE_ID: ${{ vars.LANGFUSE_TRACE_ID }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          ref: ${{ github.event.pull_request.head.sha }}
          fetch-depth: 0
          persist-credentials: false
      - uses: maida-ai/maida-assert@v0.6.0
        with:
          trace-command: maida import langfuse --trace-id "$LANGFUSE_TRACE_ID"
          baseline: baselines/imported-agent.json
          policy: .maida/policy.yaml
```

The command must create exactly one completed Maida run. Imported traces use a fixed one-trial gate: do not add `--trials` to `extra-args`. Blocking mode requires `trials: 1` in the trusted base policy; it refuses to override a larger trial budget. Import a single trace ID rather than a range or query that can create multiple runs. Policies that require several statistical trials should continue to use `agent-script`.

`trace-command` is trusted workflow code. Do not build `trace-command` from pull-request-controlled text. Pass credentials through `env` and GitHub secrets; the Action does not print the command in its reproduction hint.

### Run on `main` without posting a PR comment

Useful for nightly or post-merge runs where there is no PR to comment on:

```yaml
name: Nightly Agent Check
on:
  schedule:
    - cron: '0 6 * * *'
  workflow_dispatch:

# Read-only by default; this workflow does not post PR comments.
permissions:
  contents: read

jobs:
  agent-check:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      checks: write
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: maida-ai/maida-assert@v0.6.0
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
          mode: report-only
          post-comment: 'false'
```

## Policy example

Policy files require an explicit supported v2+ version. Policy v1 and files without a version are unsupported.

The policy file controls what `maida run` checks across isolated candidate trials. Policy v2 fails closed: unknown fields are errors. The full list of supported keys is documented in the [policy reference](https://github.com/maida-ai/maida/blob/main/docs/reference/policy.md).

A minimal `.maida/policy.yaml` looks like this:

```yaml
version: 2.1
trials: 3
fail_fast: true
metrics:
  stop_condition_reached:
    kind: invariant
    require: true
  forbidden_tools:
    kind: invariant
    none_of: [admin_delete]
  step_count:
    kind: measured
    direction: upper
    tolerance: {relative: 0.5}
  task_pass_rate:
    kind: statistical
    direction: lower
    threshold: 0.90
    confidence: 0.95
    success_predicate: all_invariants_passed
    mode: report_only
```

CLI flags passed via `extra-args` override policy values only in report-only mode. In blocking mode, budgets and thresholds come from the reviewed base policy; overrides are rejected.

## Running the Maida statistical gate locally

For a quick local check before pushing, install the `maida-ai` package and run the same command the action runs:

```bash
uv add "maida-ai>=0.6.1"

maida run my_agent.py \
  --baseline baselines/my_agent.json \
  --policy .maida/policy.yaml \
  --format markdown
```

To capture a new baseline from a known-good run:

```bash
maida baseline --out baselines/my_agent.json
```

If a PR failure is an intentional behavior change, inspect it first and then update the baseline explicitly:

```bash
maida diff --baseline baselines/my_agent.json
maida view
maida accept --baseline baselines/my_agent.json --reason "expected tool flow change"
git diff baselines/my_agent.json
```

Review the baseline JSON diff before committing it. The updated file records the acceptance reason, the accepted run, and the previous baseline hash so the baseline change remains reviewable in Git. Do not use `maida accept` for a regression you have not inspected; fix the agent behavior instead.

## Baseline acceptance

See the [acceptance and write-back guide](acceptance.md) for the optional PR acceptance workflows.
