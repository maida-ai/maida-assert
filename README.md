# Maida Behavioral Regression Gate Action

Maida runs your traced AI agent in CI, compares its behavior with a baseline and policy, and posts a Markdown report on the PR. Reports show PASS, FAIL, or INCONCLUSIVE so reviewers can inspect behavioral changes before merging.

## Get started

Instrument your Python agent with `@trace` or `traced_run()`, install its project dependencies in the workflow, and check in `.maida/policy.yaml` and any baseline on the base branch. See the [setup guide](docs/usage.md#usage) for inputs and the [local gate commands](docs/usage.md#running-the-maida-statistical-gate-locally) for creating and reviewing a baseline.

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
