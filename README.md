# Maida Behavioral Regression Gate Action

A GitHub Action that runs the [`maida`](https://github.com/maida-ai/maida)
statistical gate against your AI agent on every PR. It executes your traced
agent script in isolated trials, compares the resulting runs to a baseline and
policy, and posts a Markdown regression report as a sticky PR comment. The job
defaults to blocking mode: FAIL, INCONCLUSIVE, unaccepted configuration changes,
and setup/publication errors fail the job. Explicit report-only mode is available
for observation without merge authorization.

The report leads with a pass/fail/inconclusive verdict, shows top behavior changes
(steps, tool path, loops/cycles, guardrails, terminal state, latency/cost,
and models), groups failed checks by stable reason code, and includes concise
next steps so reviewers see *why* the gate failed without leaving the PR.
Workflow reruns update the existing Maida marker comment in place, keeping one
current gate report on the PR instead of hiding or appending older comments.
For baseline failures, the local reproduction hint also shows the explicit
`maida accept --reason ...` path to use only after the change is inspected and
intentional.

If you scaffold with [`maida init --github`](https://github.com/maida-ai/maida),
apply the checkout and protection settings below; older CLI templates do not
include them. These blocking-mode inputs require an Action revision containing
this change. Pin the reviewed Action revision by full commit SHA in production.

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

Pass exactly one trace source. An `agent-script` must instrument the agent with
`@trace` or `traced_run()`. A `trace-command` may import an external trace, but
it must create exactly one completed Maida run.

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

### Blocking mode and required repository settings

Blocking mode supports `pull_request` events with a clean checkout of the exact
PR head and the base commit available locally (`fetch-depth: 0`). The Action
makes no Git fetches. It snapshots policy and baseline blobs from
`github.event.pull_request.base.sha` outside the candidate workspace, and
publishes results only against the evaluated head SHA. A missing base, policy,
baseline, report, trace evidence, or an invalid report stops evaluation. Setup
errors do not publish a behavioral verdict or an empty comment.

The `Maida statistical gate` check reports PASS/success, FAIL/failure, and
INCONCLUSIVE/failure. The CLI's three-valued report stays intact; a separate
Action merge decision explains configuration and authorization. Process health
alone and report-only metrics do not establish a behavioral PASS for merging.

Configure branch protection to require **both** the workflow job (`agent-check`
in the examples) and **Maida statistical gate**, with branches required to be
up to date. Require fresh reviews on new commits and code-owner review for
`.github/workflows/`, the gate's scripts/dependencies, policy, baselines, and the
CODEOWNERS file itself. Protect these controls from bypass and use distinct job
names. A skipped job is not an enforcement substitute; keep the required gate
unconditional, without path filters or `continue-on-error`. See GitHub's
[required-check semantics](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

`checks: write` is required to publish the named check. Publication failure fails
a blocking job even when behavior passes. Fork/read-only tokens therefore cannot
produce a blocking success through this Action; do not switch to
`pull_request_target` with candidate execution to work around permissions.
`pull-requests: write` is needed only for the optional sticky comment; comment
failure does not erase a check result. A previous result belongs to its old
commit, and an updated base requires fresh evaluation.

The workflow, Action revision, CLI installation, agent harness and its dependencies
must be trusted to execute on the runner. This composite Action is not a sandbox
against hostile code with the same filesystem and process privileges. In
particular, do not expose a privileged token to arbitrary candidate code. The
controls above are dependencies of the intended merge boundary, not protection
against an attacker who can replace the workflow or forge reports on that runner.

### Explicit configuration acceptance

Any change under `.maida/`, or to the configured policy/baseline outside that
directory, is a separate gated configuration event. Candidate policy is never
used to grade that same candidate, even after acceptance. By default the baseline
also comes from the base. Files must be regular tracked files, not symlinks or
submodules. Establish the initial policy and baseline on the base branch before
using blocking mode.

For an intentional change, review the complete diff and the initial blocking
report. A maintainer can set a protected repository variable
`MAIDA_CONFIGURATION_ACCEPTANCE` to the `base-SHA:head-SHA:configuration-SHA256`
value printed by **Resolve trusted evaluation inputs**, and pass it from the
trusted workflow:

```yaml
          configuration-acceptance: ${{ vars.MAIDA_CONFIGURATION_ACCEPTANCE }}
```

Restrict who can edit that variable and the workflow that supplies it; never
accept a value from a PR file, comment, title, artifact, or dispatch payload.
The digest covers file paths, modes, and SHA-256 hashes of all relevant candidate
configuration, including baseline content. Rerun that PR evaluation after review.
Matching acceptance clears only the configuration event and selects the reviewed
candidate baseline. Policy still comes from the base, so a policy relaxation
cannot excuse a behavioral failure in the same PR. A policy-only change that
passes the existing policy can succeed through this route. If a relaxation is
needed for subsequent agent work, review it in a separate policy PR first.

A new head or base commit, or a different configuration digest, invalidates
acceptance and fails closed. Clear the variable after merging; review again for
the next change. Baseline JSON provenance and `/maida accept` comments are not
credentials and cannot substitute for this approval.

### Report-only mode

Set `mode: report-only` for experiments, schedules, dispatches, and other
non-PR events. It reads the candidate checkout and publishes the distinct
**Maida behavioral report (non-blocking)** check as neutral for every valid
behavioral verdict. FAIL and INCONCLUSIVE remain visible. Missing or malformed
inputs/evidence still fail the job; check-publication errors warn. Never require
this observational check as a merge gate.

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

For an observational run, override the trial count and a threshold via
`extra-args`. This workflow is not a blocking gate:

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

The command must create exactly one completed Maida run. Imported traces use a
fixed one-trial gate: do not add `--trials` to `extra-args`. Blocking mode
requires `trials: 1` in the trusted base policy; it refuses to override a larger
trial budget. Import a single
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

CLI flags passed via `extra-args` override policy values only in report-only
mode. In blocking mode, budgets and thresholds come from the reviewed base
policy; overrides are rejected.

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
actionable workflow failure. The write-back dispatch can request an observational rerun as described below.
For blocking evaluation, separately approve the resulting configuration digest
and use a fresh `pull_request` event for the new head. A push by `GITHUB_TOKEN`
does not create that event; use a maintainer push or a separately authorized
installation token workflow. The bot commit itself is never automatic merge
authorization.

## Baseline write-back engine

The `write-back` sub-action is the mutation engine for an authorized PR command
handler. It accepts a completed Maida run, commits only the configured baseline
with the standard Actions bot identity, pushes it to the exact PR head branch,
and requests a fresh report-only run. It supports same-repository pull requests only;
fork PRs fail before the baseline is changed.

The handler job must check out the verified PR head SHA with the Actions token,
run the traced agent so a completed run exists, and grant `contents: write`:

```yaml
permissions:
  contents: write

steps:
  - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
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

permissions:
  contents: read
  checks: write

jobs:
  agent-check:
    if: github.event.client_payload.pr_number != ''
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          ref: ${{ github.event.client_payload.sha }}
      - uses: maida-ai/maida-assert@v5
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
          policy: .maida/policy.yaml
          mode: report-only
```

The dispatch payload contains `pr_number`, `ref`, `sha`, and `baseline`. If a
push succeeds but dispatch fails, rerun the authorized command: an unchanged
baseline creates no duplicate commit but still requests the fresh gate.
GitHub associates the dispatch workflow itself with the default-branch SHA, so
the Action binds its observational check to the actual checkout SHA. Blocking
mode rejects dispatch events; do not treat a dispatch report as PR authorization.

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
It tests PASS/success, FAIL/failure, blocking INCONCLUSIVE/failure, report-only
neutral results, base-policy selection, configuration acceptance and invalidation,
trace-command ingestion, setup errors, and read-only check publication.
Authorized acceptance runs the real CLI, creates a baseline-only commit, pushes
to a local bare repository, requests a rerun through a local API fixture, and
reruns the gate. The sticky Action must update the existing comment in place.
Snapshots compare the complete posted Markdown, normalizing only trace IDs and binding expected reproduction paths to the actual
trusted snapshot;
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

The weekly/manual report-only smoke uses a real GitHub runner, package installation, and
Checks API with the simulated agent: one trial, no test retries, five-minute
job timeout, and **$0 model spend**. It requests no provider credentials and
does not post comments. GitHub Actions minutes remain subject to the account's
billing plan; the timeout bounds runtime, not a dollar charge.

These tests do not establish merge-boundary enforcement. The scheduled smoke
does not exercise live PR comments, required checks, or acceptance. Before
claiming enforcement, use a disposable GitHub consumer with the protection
settings above and record the policy/base/head hashes, check IDs, job results,
and actual merge attempts: PASS allowed; FAIL and gating INCONCLUSIVE refused;
policy weakening/removal refused; accepted configuration change permitted only
for its exact commit; subsequent commits blocked until reevaluated; and
read-only/publication failures unable to authorize a merge. Verify workflow-file
protection and required reviews on that repository too. No local fixture proves
those GitHub settings. Live consumer verification remains outstanding.
