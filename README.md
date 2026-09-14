# Maida Behavioral Regression Gate Action

A GitHub Action that runs the [`maida`](https://github.com/maida-ai/maida)
statistical gate against your AI agent on every PR. It executes your traced
agent script in isolated trials, compares the resulting runs to a baseline and
policy, and posts a Markdown regression report as a sticky PR comment. The job
fails if any check regresses.

The report leads with a pass/fail/inconclusive verdict, shows top behavior changes
(steps, tool path, loops/cycles, guardrails, terminal state, latency/cost,
and models), groups failed checks by stable reason code, and includes concise
next steps so reviewers see *why* the gate failed without leaving the PR.
Workflow reruns update the existing Maida marker comment in place, keeping one
current gate report on the PR instead of hiding or appending older comments.
For baseline failures, the local reproduction hint also shows the explicit
`maida accept --reason ...` path to use only after the change is inspected and
intentional.

Tip: scaffold this workflow with [`maida init --github`](https://github.com/maida-ai/maida).

## Usage

Add a workflow to your repository (for example
`.github/workflows/maida-check.yml`):

```yaml
name: Agent Regression Check
on: [pull_request]

# Required for checkout plus sticky PR comments.
permissions:
  contents: read
  checks: write
  pull-requests: write

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@v5
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
```

Pass exactly one trace source. An `agent-script` must instrument the agent with
`@trace` or `traced_run()`. A `trace-command` may import an external trace, but
it must create exactly one completed Maida run.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `agent-script` | one trace source | `''` | Path to the Python script that runs the agent. The script must use `@trace` or `traced_run()` so a run is recorded. |
| `trace-command` | one trace source | `''` | Trusted shell command that creates exactly one completed Maida run, such as an importer invocation. Do not include secrets in the command. |
| `baseline` | no | `''` | Path to a baseline JSON file produced by `maida baseline`. If omitted, only the policy is enforced. |
| `policy` | no | `.maida/policy.yaml` | Path to a policy YAML file. |
| `maida-version` | no | `v0.5.3` | Version of Maida to install. Use `v<version>` for PyPI or `@<ref>` to track a branch of the [`maida`](https://github.com/maida-ai/maida) repository. |
| `python-version` | no | `3.12` | Python version passed to `actions/setup-python`. |
| `extra-args` | no | `''` | Additional CLI arguments forwarded to `maida run` (for example, `--trials 5 --max-steps 20`). CLI flags override policy values. |
| `post-comment` | no | `true` | When `true` and the workflow runs on a `pull_request` event, the Markdown report is posted as a sticky PR comment. |

**Note:** `checks: write` publishes the stable `Maida statistical gate` check.
`PASS` maps to success, `FAIL` to failure, and `INCONCLUSIVE` to neutral. If
the token is read-only, as it commonly is for forked PRs, publication emits a
warning without changing the gate verdict. When `post-comment` is `true` on a
`pull_request` event, `pull-requests: write` is also required for the sticky
comment.
More details can be found in the [GitHub Actions documentation](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#permissions) and [sticky-pull-request-comment documentation](https://github.com/marocchino/sticky-pull-request-comment#error-resource-not-accessible-by-integration).

## Example workflows

### Minimal: policy-only check

Use this when you don't have a baseline yet but want to enforce hard
limits (no loops, no guardrail violations, max steps, etc.) defined in
`.maida/policy.yaml`:

```yaml
name: Agent Policy Check
on: [pull_request]

# Required for checkout plus sticky PR comments.
permissions:
  contents: read
  checks: write
  pull-requests: write

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@v5
        with:
          agent-script: my_agent.py
```

### Baseline regression check with inline overrides

Override the trial count and a threshold via `extra-args`, and assert against a
committed baseline:

```yaml
name: Agent Regression Check
on: [pull_request]

# Required for checkout plus sticky PR comments.
permissions:
  contents: read
  checks: write
  pull-requests: write

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@v5
        with:
          agent-script: examples/my_agent.py
          baseline: baselines/my_agent.json
          policy: .maida/policy.yaml
          maida-version: 'v0.5.3'
          python-version: '3.11'
          extra-args: --trials 5 --max-steps 20
```

### Gate a Langfuse trace

Use `trace-command` when the run comes from an importer instead of a traced
Python entrypoint. This example keeps credentials in GitHub secrets and the
trace ID in a repository variable:

```yaml
name: Imported Trace Regression Check
on: [pull_request]

permissions:
  contents: read
  checks: write
  pull-requests: write

jobs:
  imported-trace-check:
    runs-on: ubuntu-latest
    env:
      LANGFUSE_PUBLIC_KEY: ${{ secrets.LANGFUSE_PUBLIC_KEY }}
      LANGFUSE_SECRET_KEY: ${{ secrets.LANGFUSE_SECRET_KEY }}
      LANGFUSE_TRACE_ID: ${{ vars.LANGFUSE_TRACE_ID }}
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@v5
        with:
          trace-command: maida import langfuse --trace-id "$LANGFUSE_TRACE_ID"
          baseline: baselines/imported-agent.json
          policy: .maida/policy.yaml
```

The command must create exactly one completed Maida run. Imported traces use a
fixed one-trial gate: do not add `--trials` to `extra-args`. Import a single
trace ID rather than a range or query that can create multiple runs. Policies
that require several statistical trials should continue to use `agent-script`.

`trace-command` is trusted workflow code. Do not build `trace-command` from pull-request-controlled text.
Pass credentials through `env` and GitHub secrets; the Action does not print
the command in its reproduction hint.

### Run on `main` without posting a PR comment

Useful for nightly or post-merge runs where there is no PR to comment
on:

```yaml
name: Nightly Agent Check
on:
  schedule:
    - cron: '0 6 * * *'
  workflow_dispatch:

# Checkout plus the Maida check; this workflow does not post PR comments.
permissions:
  contents: read
  checks: write

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@v5
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
          post-comment: 'false'
```

## Policy example

Policy files require an explicit supported v2+ version. Policy v1 and files
without a version are unsupported.

The policy file controls what `maida run` checks across isolated candidate
trials. Policy v2 fails closed: unknown fields are errors.
The full list of supported keys is documented in the
[policy reference](https://github.com/maida-ai/maida/blob/main/docs/reference/policy.md).

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

CLI flags passed via `extra-args` always override values from the
policy file.

## Running the Maida statistical gate locally

For a quick local check before pushing, install the `maida-ai` package and run the
same command the action runs:

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

If a PR failure is an intentional behavior change, inspect it first and then
update the baseline explicitly:

```bash
maida diff --baseline baselines/my_agent.json
maida view
maida accept --baseline baselines/my_agent.json --reason "expected tool flow change"
git diff baselines/my_agent.json
```

Review the baseline JSON diff before committing it. The updated file records
the acceptance reason, the accepted run, and the previous baseline hash so the
baseline change remains reviewable in Git. Do not use `maida accept` for a
regression you have not inspected; fix the agent behavior instead.

## Accept an intentional change from a PR

The `accept-command` sub-action turns an authorized
`/maida accept [optional reason]` PR comment
into a baseline-only bot commit. It checks the commenter's repository permission
before checking out or running PR code. Users need write access. Fork pull requests
are refused before checkout. A bare command records the commenter as
the reason, while `/maida accept expected retrieval flow` records the trailing
text.

Add an `issue_comment` workflow on the default branch:

```yaml
name: Accept Maida Baseline
on:
  issue_comment:
    types: [created]

permissions: {}

jobs:
  accept:
    if: >-
      github.event.issue.pull_request &&
      startsWith(github.event.comment.body, '/maida accept')
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: maida-ai/maida-assert/accept-command@v5
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
          policy: .maida/policy.yaml
          github-token: ${{ github.token }}
```

Enable the visible command hint in the normal gate step with
`accept-command-enabled: 'true'`. The handler reruns exactly one configured
trace source (`agent-script` or `trace-command`) and the assertion inputs,
delegates the baseline-only commit to the write-back engine,
and posts either a commit link, an already-current confirmation, or an
actionable workflow failure. The write-back dispatch still requires the normal
gate workflow to listen for `maida_baseline_updated` as described below.

## Baseline write-back engine

The `write-back` sub-action is the mutation engine for an authorized PR command
handler. It accepts a completed Maida run, commits only the configured baseline
with the standard Actions bot identity, pushes it to the exact PR head branch,
and requests a fresh gate run. It supports same-repository pull requests only;
fork PRs fail before the baseline is changed.

The handler job must check out the verified PR head SHA with the Actions token,
run the traced agent so a completed run exists, and grant `contents: write`:

```yaml
permissions:
  contents: write

steps:
  - uses: actions/checkout@v7
    with:
      repository: ${{ steps.pr.outputs.head_repository }}
      ref: ${{ steps.pr.outputs.head_sha }}

  # Install Maida and run the traced agent before this step.
  - id: write-back
    uses: maida-ai/maida-assert/write-back@v5
    with:
      baseline: baselines/my_agent.json
      reason: ${{ steps.command.outputs.reason }}
      pr-number: ${{ github.event.issue.number }}
      head-repository: ${{ steps.pr.outputs.head_repository }}
      head-branch: ${{ steps.pr.outputs.head_branch }}
      expected-head-sha: ${{ steps.pr.outputs.head_sha }}
      github-token: ${{ github.token }}
```

The action refuses a stale checkout and uses a normal, non-force push, so a
concurrent update to the PR branch fails safely. It emits a
`maida_baseline_updated` `repository_dispatch` after acceptance because pushes
made with `GITHUB_TOKEN` do not trigger ordinary workflow runs. A workflow on
the default branch must listen for that event and check out the dispatched SHA:

```yaml
on:
  repository_dispatch:
    types: [maida_baseline_updated]

jobs:
  agent-check:
    if: github.event.client_payload.pr_number != ''
    steps:
      - uses: actions/checkout@v7
        with:
          ref: ${{ github.event.client_payload.sha }}
      # Run the normal Maida gate against this checkout.
      # Publish its conclusion against github.event.client_payload.sha.
```

The dispatch payload contains `pr_number`, `ref`, `sha`, and `baseline`. If a
push succeeds but dispatch fails, rerun the authorized command: an unchanged
baseline creates no duplicate commit but still requests the fresh gate.
GitHub associates the dispatch workflow itself with the default-branch SHA, so
the consuming command-handler workflow must publish the gate status or check
against `client_payload.sha` for required PR checks to recognize the result.

When `maida run` reports failed checks, the action still publishes the
Markdown report and then exits `1`. Missing runs or baselines and internal
errors exit immediately with the underlying CLI/setup code. See the
[`maida` reference](https://github.com/maida-ai/maida/blob/main/docs/cli.md)
for the full exit-code contract.

For installation, tracing your agent, and the rest of the workflow,
see the Maida
[getting started guide](https://github.com/maida-ai/maida/blob/main/docs/getting-started.md).

## Testing the Action

The `Action end-to-end` job runs on every PR, including forks and documentation
changes. Add that exact check name to this repository's required checks; a
workflow file alone cannot configure branch protection.

The local harness executes the composite shell steps, released `maida-ai==0.5.3`,
and the pinned sticky-comment Action against a temporary consumer Git repository.
It tests PASS/success, FAIL/failure, INCONCLUSIVE/neutral, trace-command ingestion,
setup errors, read-only check publication, and authorized/unauthorized acceptance.
Authorized acceptance runs the real CLI, creates a baseline-only commit, pushes
to a local bare repository, requests a rerun through a local API fixture, and
reruns the gate. The sticky Action must update the existing comment in place.
Snapshots compare the complete posted Markdown, normalizing only trace IDs;
the agent fixture supplies fixed recorded timing. All agent behavior is simulated.

Install test dependencies and run the suites with `uv` (Python 3.12, Node 24,
Bash, and Git are required). Fetch the pinned third-party Action once:

```bash
git clone --branch v3.0.4 --depth 1 https://github.com/marocchino/sticky-pull-request-comment.git /tmp/maida-sticky-comment
git -C /tmp/maida-sticky-comment rev-parse HEAD
# Expected: 0ea0beb66eb9baf113663a64ec522f60e49231c0
uv run --python 3.12 --with-requirements requirements-dev.txt pytest -q --ignore=tests/e2e
MAIDA_E2E_STICKY_PATH=/tmp/maida-sticky-comment uv run --python 3.12 --with-requirements requirements-e2e.txt pytest -q tests/e2e
```

Tests make no external API or model calls after dependency setup. The harness
replaces Python/package setup, validates the pre-created checkout, and directs
GitHub HTTP calls to a loopback fixture. Missing dependencies or unsupported
runner expressions fail the suite. On snapshot failure, review the generated
`consumer/comment.actual.md` before updating `tests/e2e/snapshots/`.
CI retains fixture reports and API transcripts for seven days.

The weekly/manual smoke uses a real GitHub runner, package installation, and
Checks API with the simulated agent: one trial, no test retries, five-minute
job timeout, and **$0 model spend**. It requests no provider credentials and
does not post comments. GitHub Actions minutes remain subject to the account's
billing plan; the timeout bounds runtime, not a dollar charge.

These tests do not establish merge-boundary enforcement. The scheduled smoke
does not exercise live PR comments or acceptance. A disposable GitHub consumer
with real branch protection is still needed to verify those paths, base-ref
policy evaluation, acceptance binding to baseline hash and commit, and reviewed
changes under `.maida/`. The current Action publishes INCONCLUSIVE as neutral,
which does not block merging by itself. Protect `.github/workflows/` through
CODEOWNERS with required review or equivalent repository rules; no local fixture
can verify that configuration.
