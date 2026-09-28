# Maida Behavioral Regression Gate Action

Maida runs your traced AI agent in CI, compares its behavior with a baseline and policy, and posts a Markdown report on the PR. Reports show PASS, FAIL, or INCONCLUSIVE so reviewers can inspect behavioral changes before merging.

## Get started

Start with the released CLI and inspect one failed check before configuring CI:

```bash
uv tool install "maida-ai==0.5.3"
maida demo --regression
```

The offline demo shows a regression verdict and the PR-comment preview without an API key or repository clone. Continue with the [coding-agent getting started guide](https://maida.ai/docs/getting-started/) and its [practical walkthrough](https://github.com/maida-ai/maida-tutorials/blob/main/guides/coding-agent.md): capture one bounded task in your repository, inspect the observation, review a small policy, and reproduce a change locally before adding the gate to a PR. Each step has an observable result; you do not need to read the Action reference first.

For a Python tool-calling agent, follow the [Python setup path](https://github.com/maida-ai/maida/blob/main/docs/python-agent.md). Install Maida into the project environment before importing it; `uv tool install` only installs the standalone CLI.

## Add CI after the local check works

The published Action alias remains `@v5`. The blocking, acceptance-dispatch and first-run recovery features documented for a coordinated revision require a reviewed full Action SHA; they are not retroactively available in `v5`. A local branch is not a release. Before enabling a required gate, complete the [consumer verification](docs/acceptance.md#verify-the-actual-post-accept-loop) for the pinned revision.

For the Python integration, instrument your agent with `@trace` or `traced_run()`, install its project dependencies in the workflow, and check in `.maida/policy.yaml` and any baseline on the base branch. See the [setup reference](docs/usage.md#usage) for inputs and the [local gate commands](docs/usage.md#running-the-maida-statistical-gate-locally) for creating and reviewing a baseline.

Add `.github/workflows/maida-check.yml`:

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

Examples use `@v5`; newer blocking and acceptance features require a coordinated Action revision. Pin a reviewed SHA and follow the [required repository settings](docs/usage.md#blocking-mode-and-required-repository-settings). Blocking mode refuses FAIL, INCONCLUSIVE, unaccepted configuration changes and setup/publication errors; report-only mode is observational.

## More information

- [Inputs and token permissions](docs/usage.md#inputs)
- [Workflow examples](docs/usage.md#example-workflows), including imported traces and report-only runs
- [Policy examples](docs/usage.md#policy-example) and [configuration acceptance](docs/usage.md#explicit-configuration-acceptance)
- [Accept intentional changes from a PR](docs/acceptance.md#accept-an-intentional-change-from-a-pr) and [baseline write-back](docs/acceptance.md#baseline-write-back-engine)
- [Dependency integrity](SECURITY.md#dependency-integrity) and [release provenance](CONTRIBUTING.md#tagged-release-provenance)
- [Contributor testing instructions](CONTRIBUTING.md#testing-the-action)

## Security

See [SECURITY.md](SECURITY.md) for private vulnerability reporting and repository security controls.

## Versioning

See the [versioning policy](CONTRIBUTING.md#versioning) for compatibility, release tags, and legacy references.
