# Maida Assert Action

A GitHub Action that runs [`maida assert`](https://github.com/maida-ai/maida)
against your AI agent on every PR. It executes your traced agent script,
compares the resulting run to a baseline and policy, and posts a Markdown
regression report as a sticky PR comment. The job fails if any check regresses.

The report leads with a pass/fail verdict, shows top behavior changes
(steps, tool path, loops/cycles, guardrails, terminal state, latency/cost,
and models), groups failed checks by stable reason code, and includes concise
next steps so reviewers see *why* the gate failed without leaving the PR.
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
  pull-requests: write

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@V4
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
```

Your `agent-script` must instrument the agent with `@trace` or
`traced_run()` so that Maida can capture the run.

### Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `agent-script` | yes | — | Path to the Python script that runs the agent. The script must use `@trace` or `traced_run()` so a run is recorded. |
| `baseline` | no | `''` | Path to a baseline JSON file produced by `maida baseline`. If omitted, only the policy is enforced. |
| `policy` | no | `.maida/policy.yaml` | Path to a policy YAML file. |
| `maida-version` | no | `@main` | Version of Maida to install. Use `v<version>` to install the `maida-ai` PyPI package (e.g. `v0.2.1`) or `@<ref>` to install from the [`maida`](https://github.com/maida-ai/maida) repo (branch, tag, or commit, e.g. `@main`). |
| `python-version` | no | `3.12` | Python version passed to `actions/setup-python`. |
| `extra-args` | no | `''` | Additional CLI arguments forwarded to `maida assert` (e.g. `--max-steps 20 --no-loops`). CLI flags override policy values. |
| `post-comment` | no | `true` | When `true` and the workflow runs on a `pull_request` event, the Markdown report is posted as a sticky PR comment. |

**Note:** If the `post-comment` input is `true` and the workflow runs on a `pull_request` event, the workflow requires `contents: read` for checkout and `pull-requests: write` for the sticky PR comment.
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
  pull-requests: write

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@V4
        with:
          agent-script: my_agent.py
```

### Baseline regression check with inline overrides

Pin `maida` to a PyPI release, override a couple of thresholds via
`extra-args`, and assert against a committed baseline:

```yaml
name: Agent Regression Check
on: [pull_request]

# Required for checkout plus sticky PR comments.
permissions:
  contents: read
  pull-requests: write

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@V4
        with:
          agent-script: examples/my_agent.py
          baseline: baselines/my_agent.json
          policy: .maida/policy.yaml
          maida-version: v0.3.2
          python-version: '3.11'
          extra-args: --max-steps 20 --max-tool-calls 10
```

### Run on `main` without posting a PR comment

Useful for nightly or post-merge runs where there is no PR to comment
on:

```yaml
name: Nightly Agent Check
on:
  schedule:
    - cron: '0 6 * * *'
  workflow_dispatch:

# Checkout only; this workflow does not post PR comments.
permissions:
  contents: read

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: maida-ai/maida-assert@V4
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
          post-comment: 'false'
```

## Policy example

The policy file controls what `maida assert` checks on every run.
The full list of supported keys is documented in the
[policy reference](https://github.com/maida-ai/maida/blob/main/docs/reference/policy.md).

A minimal `.maida/policy.yaml` looks like this:

```yaml
assert:
  no_loops: true
  no_guardrails: true
  step_tolerance: 0.5
  expect_status: ok
```

CLI flags passed via `extra-args` always override values from the
policy file.

## Running `maida assert` locally

For a quick local check before pushing, install the `maida-ai` package and run the
same commands the action runs (`maida assert` defaults to the latest run):

```bash
uv add maida-ai

python my_agent.py
maida assert \
  --baseline baselines/my_agent.json \
  --policy .maida/policy.yaml
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

When `maida assert` reports failed checks, the action still publishes the
Markdown report and then exits `1`. Missing runs or baselines and internal
errors exit immediately with the underlying CLI/setup code. See the
[`maida` reference](https://github.com/maida-ai/maida/blob/main/docs/cli.md)
for the full exit-code contract.

For installation, tracing your agent, and the rest of the workflow,
see the Maida
[getting started guide](https://github.com/maida-ai/maida/blob/main/docs/getting-started.md).
