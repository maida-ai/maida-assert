# Baseline acceptance and write-back

[Back to the README](../README.md).

## Accept an intentional change from a PR

An authorized `/maida accept [optional reason]` comment can create a baseline-only bot commit. Users need write access. Fork pull requests are rejected before any candidate checkout. A bare command records the commenter as the reason.

**Migrate existing handlers:** the former single-job `accept-command` and `write-back` interfaces are retired. Authorization does not make candidate code safe to run with a write token. Use three separate GitHub-hosted jobs as below; these interfaces require an Action revision containing this migration. Replace `@main` with that reviewed full commit SHA before production use.

Add this workflow on the default branch. Set the baseline, policy and agent path to your repository's files. Install any additional agent dependencies only in `capture`, using `uv`; never install candidate dependencies in `authorize` or `write`. The example explicitly uploads the candidate baseline data to a GitHub Actions artifact for one day; it uploads no raw traces or reports. Review what your baseline contains before enabling this opt-in workflow.

```yaml
name: Accept Maida Baseline
on:
  issue_comment:
    types: [created]
permissions: {}
concurrency:
  group: maida-accept-${{ github.event.issue.number }}
  cancel-in-progress: false
jobs:
  authorize:
    if: >-
      github.event.issue.pull_request &&
      startsWith(github.event.comment.body, '/maida accept')
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    outputs:
      authorized: ${{ steps.command.outputs.authorized }}
      context: ${{ steps.command.outputs.context }}
      head-sha: ${{ steps.command.outputs.head-sha }}
    steps:
      - id: command
        uses: maida-ai/maida-assert/accept-command@main
        with:
          stage: authorize
          baseline: baselines/my_agent.json
          policy: .maida/policy.yaml
          github-token: ${{ github.token }}

  capture:
    needs: authorize
    if: needs.authorize.outputs.authorized == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          ref: ${{ needs.authorize.outputs.head-sha }}
          persist-credentials: false
      - uses: maida-ai/maida-assert/capture-acceptance@main
        with:
          context: ${{ needs.authorize.outputs.context }}
          agent-script: my_agent.py
          artifact-directory: ${{ runner.temp }}/maida-acceptance
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: maida-accept-${{ github.run_id }}-${{ github.run_attempt }}
          path: ${{ runner.temp }}/maida-acceptance/acceptance.json
          if-no-files-found: error
          retention-days: 1

  write:
    needs: [authorize, capture]
    if: always() && needs.authorize.outputs.authorized == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        if: needs.capture.result == 'success'
        with:
          name: maida-accept-${{ github.run_id }}-${{ github.run_attempt }}
          path: ${{ runner.temp }}/maida-acceptance
      - uses: maida-ai/maida-assert/write-back@main
        if: always()
        with:
          context: ${{ needs.authorize.outputs.context }}
          artifact-directory: ${{ runner.temp }}/maida-acceptance
          github-token: ${{ github.token }}
```

Enable `accept-command-enabled: 'true'` in the gate to show the hint. Capture records one configured trace source and runs `maida assert`. Exit 1 is a reviewable behavioral failure; other errors stop capture.

Keep capture read-only: no secrets, persisted credentials, PATs, OIDC, environment secrets or shared self-hosted runners. Never combine it with the privileged writer or execute it through `pull_request_target`.

## Baseline write-back engine

The writer supports same-repository pull requests only. Run it in a fresh trusted job with no candidate checkout, caches, dependencies or executable artifacts. Pass authorization `context` through `needs`; never use capture outputs or artifact content.

- Accept only `acceptance.json` (up to 1 MiB). Reject extra files, symlinks, incomplete baselines and staged candidate changes.
- Bind acceptance to the PR, head/base, baseline/policy hashes, commenter, reason and workflow attempt. Recheck permissions and refs before a baseline-only, non-force update.
- Reject stale heads, closed PRs, changed policy and revoked access. Required up-to-date checks still cover base-update races.
- Treat artifacts as untrusted observations. Hashes and bindings do not prove behavioral PASS. Record the artifact digest and prior hashes for review.

The bot commit requires [configuration acceptance](usage.md#explicit-configuration-acceptance) and fresh gate results. It emits `maida_baseline_updated` because `GITHUB_TOKEN` pushes do not trigger ordinary PR workflows. Dispatch requests evaluation, not approval. A later head/base invalidates acceptance.

Install the listener on the default branch. The coordinated `maida init --github` scaffold includes it; pin these `@main` sub-actions to a reviewed SHA before production use.

```yaml
name: Evaluate Accepted Maida Baseline
on:
  pull_request:
  repository_dispatch:
    types: [maida_baseline_updated]
permissions:
  contents: read

jobs:
  agent-check:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      checks: write
      pull-requests: write
      statuses: write
    steps:
      - id: pr
        uses: maida-ai/maida-assert/pr-context@main
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7
        with:
          ref: ${{ steps.pr.outputs.head-sha }}
          fetch-depth: 0
          persist-credentials: false
      - id: gate
        uses: maida-ai/maida-assert@main
        with:
          agent-script: my_agent.py
          baseline: baselines/my_agent.json
          policy: .maida/policy.yaml
          configuration-acceptance: ${{ vars.MAIDA_CONFIGURATION_ACCEPTANCE }}
      - if: always() && steps.pr.outcome == 'success'
        uses: maida-ai/maida-assert/publish-status@main
        with:
          head-sha: ${{ steps.pr.outputs.head-sha }}
          base-sha: ${{ steps.pr.outputs.base-sha }}
          verdict: ${{ steps.gate.outputs.verdict }}
          conclusion: ${{ steps.gate.outputs.conclusion }}
          publication: ${{ steps.gate.outputs.publication }}
```

The dispatch payload includes `pr_number`, `ref`, `sha` and `baseline`. `pr-context` verifies `github.event.client_payload.pr_number` and `github.event.client_payload.sha` against the current open, same-repository PR before checkout. Paths come from the trusted workflow.

GitHub runs dispatch workflows at the default-branch SHA. Require **Maida / agent-check** (the explicit commit status) and **Maida statistical gate** for this combined listener. Both publish against the verified PR head. Retain the [workflow/review protections](usage.md#blocking-mode-and-required-repository-settings).

- Validated PASS, accepted configuration and successful check publication permit a success status.
- INCONCLUSIVE gets failure; incomplete evaluation or publication gets error.
- Status publication failure fails the job. Old-head results never authorize new commits.

If the baseline write succeeds but dispatch fails, request acceptance again on the latest head. An unchanged artifact creates no duplicate commit and still requests evaluation. Acceptance alone never means PASS.

Failed behavioral checks publish the Markdown report and exit 1. Missing evidence and internal errors stop immediately. See the [CLI reference](https://github.com/maida-ai/maida/blob/main/docs/cli.md) for exit codes and the [getting started guide](https://github.com/maida-ai/maida/blob/main/docs/getting-started.md) for tracing and setup.
