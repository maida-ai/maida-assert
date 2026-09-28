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

Enable `accept-command-enabled: 'true'` in the normal gate step to show the command hint. Capture runs exactly one configured trace source (`agent-script` or `trace-command`), then `maida assert` against the authorized baseline and policy. An assertion exit of 1 is a reviewable behavioral failure; other nonzero exits stop capture. The capture job has only `contents: read`, no secrets, and no persisted checkout credentials. Do not grant it a PAT, installation token, OIDC permission, environment secrets, or access to a shared self-hosted runner. Do not combine these jobs or move candidate execution to `pull_request_target`.

## Baseline write-back engine

The `write-back` sub-action consumes data in a fresh trusted job with **no candidate checkout, caches, dependencies or executable artifacts**. It supports same-repository pull requests only. Pass its `context` directly from the authorization job through `needs`, never from capture outputs or artifact content. The writer accepts only `acceptance.json` (up to 1 MiB); extra files, symlinks and incomplete baselines are rejected. The candidate's staged changes also reject capture; there is no Git index in the writer to mix into its commit.

Authorization binds the repository, PR, head and base SHA, baseline path and hash, policy path and hash, commenter, reason, comment ID, workflow run and attempt. The selected policy must match the PR base. Changed-policy, stale-head, closed-PR, revoked-permission and fork cases stop before updating the branch. The writer reads artifact bytes once, checks their binding, and replaces candidate-supplied acceptance metadata with trusted provenance. It commits only the configured existing baseline through the Git API, rechecks authorization and both refs immediately before updating the branch, and uses a non-force update. A concurrent forward update is rejected. GitHub does not offer an atomic update of both PR refs; required up-to-date checks remain necessary if the base moves after the last check.

Artifacts are **untrusted observations**, not proof that candidate code behaved honestly. JSON shape and binding checks do not certify the trace or produce a behavioral PASS. The baseline commit records the artifact digest and prior baseline/policy hashes so it can be reviewed. New configuration still needs the [explicit configuration-acceptance mechanism](../README.md#explicit-configuration-acceptance) and fresh gate results for the resulting head; the bot commit never authorizes a merge.

The writer emits `maida_baseline_updated`, because pushes made with `GITHUB_TOKEN` do not trigger ordinary PR workflows. The dispatch requests a fresh evaluation; it does not grant configuration acceptance. Review the new base/head/configuration digest, set `MAIDA_CONFIGURATION_ACCEPTANCE`, and rerun the workflow. A later head or base invalidates that acceptance.

Install this listener on the default branch (or use the coordinated `maida init --github` scaffold). These new sub-actions currently use `@main`; pin a reviewed coordinated Action commit before production use.

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

The dispatch payload contains `pr_number`, `ref`, `sha`, and `baseline`. `pr-context` verifies the event type, `github.event.client_payload.pr_number`, and `github.event.client_payload.sha` against the current open, same-repository PR using the GitHub API before checkout. Policy/baseline paths come from the trusted workflow, never from payload claims. The Action verifies identity again before publishing its check and updates the existing sticky comment for that PR. The report includes the full evaluated head SHA.

GitHub associates the dispatch workflow itself with the default-branch SHA. For this combined PR/dispatch workflow, require **Maida / agent-check** (the explicit commit status) and **Maida statistical gate**, in place of the ordinary PR job requirement. Retain the up-to-date and [workflow/review protections](../README.md#blocking-mode-and-required-repository-settings). The status is published for both event paths against the verified PR head and requires a validated PASS, an accepted configuration and successful named-check publication. INCONCLUSIVE receives a failure status and an explicit INCONCLUSIVE description; incomplete evaluation or check publication receives an error status. Status publication failure fails the workflow and can be retried. The status publisher rechecks the head/base before sending any result. API operations are not atomic with branch updates; old-head results never authorize a later head.

If the baseline commit succeeds but dispatch fails, the command reply identifies the written head and the failure to request fresh results. Request acceptance again against the latest head; an unchanged artifact creates no duplicate commit and still requests fresh results. Acceptance alone never means the gate passed.

When `maida run` reports failed checks, the action still publishes the Markdown report and then exits `1`. Missing runs or baselines and internal errors exit immediately with the underlying CLI/setup code. See the [`maida` reference](https://github.com/maida-ai/maida/blob/main/docs/cli.md) for the full exit-code contract.

For installation, tracing your agent, and the rest of the workflow, see the Maida [getting started guide](https://github.com/maida-ai/maida/blob/main/docs/getting-started.md).
