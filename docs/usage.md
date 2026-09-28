# Action usage and workflows

[Back to the README](../README.md).

The report leads with a pass/fail/inconclusive verdict, shows top behavior changes (steps, tool path, loops/cycles, guardrails, terminal state, latency/cost, and models), groups failed checks by stable reason code, and includes concise next steps so reviewers see *why* the gate failed without leaving the PR. Workflow reruns update the existing Maida marker comment in place, keeping one current gate report on the PR instead of hiding or appending older comments. For baseline failures, the local reproduction hint also shows the explicit `maida accept --reason ...` path to use only after the change is inspected and intentional.

If you scaffold with [`maida init --github`](https://github.com/maida-ai/maida), apply the checkout and protection settings below; older CLI templates do not include them. These blocking-mode inputs require an Action revision containing this change. Pin the reviewed Action revision by full commit SHA in production.

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
      - uses: maida-ai/maida-assert@v5
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
```

Pass exactly one trace source. An `agent-script` must instrument the agent with `@trace` or `traced_run()`. A `trace-command` may import an external trace, but it must create exactly one completed Maida run.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `mode` | no | `blocking` | `blocking` evaluates the trusted PR base policy; `report-only` observes candidate configuration and never authorizes merging. |
| `configuration-acceptance` | no | `''` | Maintainer-controlled `base-SHA:head-SHA:configuration-SHA256` value for a reviewed configuration change. Never read it from PR content. |
| `agent-script` | one trace source | `''` | Path to the Python script that runs the agent. The script must use `@trace` or `traced_run()` so a run is recorded. |
| `trace-command` | one trace source | `''` | Trusted shell command that creates exactly one completed Maida run, such as an importer invocation. Do not include secrets in the command. |
| `baseline` | no | `''` | Path to a baseline JSON file produced by `maida baseline`. If omitted, only the policy is enforced. |
| `policy` | no | `.maida/policy.yaml` | Repository-relative path, required on the trusted base in blocking mode. |
| `maida-version` | no | `v0.5.3` | Version of Maida to install. Use `v<version>` for PyPI or `@<ref>` to track a branch of the [`maida`](https://github.com/maida-ai/maida) repository. |
| `python-version` | no | `3.12` | Python version passed to `actions/setup-python`. |
| `extra-args` | no | `''` | Report-only CLI overrides (for example, `--trials 5 --max-steps 20`). Blocking mode rejects overrides; edit the base policy through review. |
| `post-comment` | no | `true` | When `true` and the workflow runs on a `pull_request` event, the Markdown report is posted as a sticky PR comment. |

### Token permissions

The usage example grants `contents: read` by default and adds writes only to the gate job. `contents: read` permits checkout and trusted base-file reads; `checks: write` publishes the named Maida check; `pull-requests: write` creates or updates the optional sticky comment. With `post-comment: 'false'`, use:

```yaml
permissions:
  contents: read
jobs:
  agent-check:
    permissions:
      contents: read
      checks: write
```

This is a permissions excerpt; retain the steps from the complete workflows. Unspecified permissions are disabled. The normal gate needs no `contents: write`, `actions: write`, `packages: write`, `attestations: write`, or `id-token: write`. The optional acceptance workflow separately grants `pull-requests: write` to its authorizer for replies, read-only access to capture, and `contents: write` plus `pull-requests: write` to its isolated writer for the baseline commit, dispatch, and replies. The dispatch listener additionally needs `statuses: write` to publish its explicit commit status. Do not copy those grants into capture.

GitHub does not expose a reliable complete effective-permission inventory to this composite Action. **Broader token grants are not detected or rejected at runtime.** Use the exact job permission blocks, review workflow changes, and avoid substituting a broader PAT. Missing check publication rights fail blocking evaluation; optional comment failures remain warnings. The Action cannot reduce permissions already granted to a job. See GitHub's [token permission reference](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions#permissions).

### Blocking mode and required repository settings

Blocking mode supports `pull_request` and verified `maida_baseline_updated` dispatch events with a clean checkout of the exact PR head and the base commit available locally (`fetch-depth: 0`). The Action makes no Git fetches. PR identity verification uses authenticated GitHub API reads. It snapshots policy and baseline blobs from the verified PR base outside the candidate workspace, and publishes results only against the evaluated head SHA. A missing base, policy, baseline, report, trace evidence, or an invalid report stops evaluation. Setup errors do not publish a behavioral verdict or an empty comment.

The `Maida statistical gate` check reports PASS/success, FAIL/failure, and INCONCLUSIVE/failure. The CLI's three-valued report stays intact; a separate Action merge decision explains configuration and authorization. Process health alone and report-only metrics do not establish a behavioral PASS for merging.

Configure branch protection to require **both** the workflow job (`agent-check` in the examples) and **Maida statistical gate**, with branches required to be up to date. Require fresh reviews on new commits and code-owner review for `.github/workflows/`, the gate's scripts/dependencies, policy, baselines, and the CODEOWNERS file itself. Protect these controls from bypass and use distinct job names. A skipped job is not an enforcement substitute; keep the required gate unconditional, without path filters or `continue-on-error`. See GitHub's [required-check semantics](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

`checks: write` is required to publish the named check. Publication failure fails a blocking job even when behavior passes. Fork/read-only tokens therefore cannot produce a blocking success through this Action; do not switch to `pull_request_target` with candidate execution to work around permissions. `pull-requests: write` is needed only for the optional sticky comment; comment failure does not erase a check result. A previous result belongs to its old commit, and an updated base requires fresh evaluation.

The workflow, Action revision, CLI installation, agent harness and its dependencies must be trusted to execute on the runner. This composite Action is not a sandbox against hostile code with the same filesystem and process privileges. In particular, do not expose a privileged token to arbitrary candidate code. The controls above are dependencies of the intended merge boundary, not protection against an attacker who can replace the workflow or forge reports on that runner.

### Explicit configuration acceptance

Any change under `.maida/`, or to the configured policy/baseline outside that directory, is a separate gated configuration event. Candidate policy is never used to grade that same candidate, even after acceptance. By default the baseline also comes from the base. Files must be regular tracked files, not symlinks or submodules. Establish the initial policy and baseline on the base branch before using blocking mode.

For an intentional change, review the complete diff and the initial blocking report. A maintainer can set a protected repository variable `MAIDA_CONFIGURATION_ACCEPTANCE` to the `base-SHA:head-SHA:configuration-SHA256` value printed by **Resolve trusted evaluation inputs**, and pass it from the trusted workflow:

```yaml
          configuration-acceptance: ${{ vars.MAIDA_CONFIGURATION_ACCEPTANCE }}
```

Restrict who can edit that variable and the workflow that supplies it; never accept a value from a PR file, comment, title, artifact, or dispatch payload. The digest covers file paths, modes, and SHA-256 hashes of all relevant candidate configuration, including baseline content. Rerun that PR evaluation after review. Matching acceptance clears only the configuration event and selects the reviewed candidate baseline. Policy still comes from the base, so a policy relaxation cannot excuse a behavioral failure in the same PR. A policy-only change that passes the existing policy can succeed through this route. If a relaxation is needed for subsequent agent work, review it in a separate policy PR first.

A new head or base commit, or a different configuration digest, invalidates acceptance and fails closed. Clear the variable after merging; review again for the next change. Baseline JSON provenance and `/maida accept` comments are not credentials and cannot substitute for this approval.

### Report-only mode

Set `mode: report-only` for experiments, schedules, and other non-PR events. It reads the candidate checkout and publishes the distinct **Maida behavioral report (non-blocking)** check as neutral for every valid behavioral verdict. FAIL and INCONCLUSIVE remain visible. Missing or malformed inputs/evidence still fail the job; check-publication errors warn. Never require this observational check as a merge gate.

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
      - uses: maida-ai/maida-assert@v5
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
      - uses: maida-ai/maida-assert@v5
        with:
          agent-script: examples/my_agent.py
          baseline: baselines/my_agent.json
          policy: .maida/policy.yaml
          maida-version: 'v0.5.3'
          python-version: '3.11'
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
      - uses: maida-ai/maida-assert@v5
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
      - uses: maida-ai/maida-assert@v5
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
uv add "maida-ai>=0.5"

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
