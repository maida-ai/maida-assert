# Changelog

## Unreleased

- Synchronize the engine contract, hash-locked Maida dependency, gate and acceptance capture defaults, CI generator pin, and current documentation with Maida `v0.6.1`. The Action remains independently versioned; existing `v0.6.0` Action tags are unchanged, and this sync is reserved for a future bundled release.

## v0.6.0

This release consolidates the v0.6.0 release candidate into the first stable 0.6 Action. It targets `maida-ai==0.6.0` on Python 3.12–3.14. Pin the Action's reviewed full commit SHA in production workflows; `@v0.6.0` identifies this release once its GitHub release is published.

### Highlights

- **Blocking PR gate:** the Action evaluates policy and baselines from the trusted PR base, reports PASS, FAIL, INCONCLUSIVE, configuration changes and setup failures distinctly, and publishes a named check for the evaluated head. Report-only mode remains observational.
- **Acceptance workflow:** authorization, candidate capture and baseline write-back run in separate jobs. Acceptance is bound to the reviewed commit and baseline; the read-only verifier checks the post-accept dispatch, report and current-head status.
- **Clearer reports and onboarding:** the named check, PR comment and workflow summary give the next safe action, including first-run recovery guidance. The README leads with the offline regression demo and coding-agent walkthrough.
- **Dependency and release integrity:** the default engine installation uses a reviewed hash-locked dependency group. The tagged release workflow tests the committed source, archives it with checksums, verifies provenance and prepares a draft GitHub release for review.
- **Local verification:** deterministic end-to-end fixtures exercise verdicts, comments, trusted policy and acceptance; a scheduled smoke checks real GitHub check publication. These checks do not replace live consumer merge-protection verification.

### Upgrading

- Replace legacy Action refs with the reviewed `v0.6.0` commit SHA. The `v5` alias remains on its historical v0.5.0 commit and does not contain the blocking or isolated acceptance features.
- Use `maida-ai==0.6.0` with Python 3.12–3.14. The Action and acceptance capture defaults now select that engine version.
- Review `.maida/policy.yaml` and required repository settings before making the gate required. Follow the [consumer verification procedure](docs/acceptance.md#verify-the-actual-post-accept-loop) for the pinned revision.
- Replace the retired single-job acceptance handler with the [isolated acceptance workflow](docs/acceptance.md). Re-pin every sub-action to the same reviewed Action commit.
