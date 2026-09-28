# Contributing to Maida’s GitHub Action

## Versioning

The [Maida contributor policy](https://github.com/maida-ai/maida/blob/main/CONTRIBUTING.md#versioning-and-compatibility) defines cross-repository compatibility. This Action has its own `PATCH` releases within the `MAJOR.MINOR` compatibility line set by the `maida-ai` engine. The Action is the CI gate product, so each new release must test the engine range and functionality it claims to support. The `maida-version` input selects the actual engine package installed in a run; the Action tag alone does not select or prove a compatible engine version.

For new releases, create an immutable full tag such as `v0.5.2` for an Action tested with the engine's `0.5` line, and advance the `v0.5` alias to the latest compatible Action patch. Do not move a full release tag. At version 1 or later, also maintain a moving major tag such as `v1`. There is no `v0` alias: pre-1.0 minor releases may contain incompatible changes. A full commit SHA remains the immutable reference for production workflows. The README’s `@v5` examples select the latest published GitHub release under the legacy scheme; they do not track the newer `v0.5` alias. Update them only after the replacement Action release exists and has been verified.

The release workflow publishes only full stable `vMAJOR.MINOR.PATCH` tags. Moving aliases, prereleases, and Python `.postN` tags are not publication inputs. Advance a compatibility alias only after verifying the full release and its provenance; alias updates do not publish another release.

### Legacy tags and migration

Historical tags remain available at their existing commits: `v1` corresponds to `v0.1.0`, `v2` to `v0.2.0`, `V3`/`v3` to `v0.3.0`, `V4`/`v4` to `v0.4.0`, and `v5` to `v0.5.0`. Tag names are case-sensitive. These are legacy references, not the moving major aliases of the new policy. In particular, `v5` does not advance with the `v0.5` compatibility alias. Preserve historical tags during compatibility-line migrations; migrate consumers to a reviewed full commit SHA or a verified compatibility alias rather than repointing a historical tag. Before adopting a genuine `v1` moving major alias at 1.0, explicitly document the migration from the existing legacy `v1` reference.

## Dependency maintenance

CI installs from `requirements-dev.lock` and `requirements-e2e.lock` with hash verification. The E2E workflow also pins its core workflow generator checkout; update that SHA deliberately when testing a coordinated core change. To refresh locks using the repository's pinned `uv` version, review changes to the input `.txt` files and regenerate all three manifests:

```bash
uv pip compile --universal --python-version 3.10 --generate-hashes requirements-dev.txt -o requirements-dev.lock
uv pip compile --universal --python-version 3.10 --generate-hashes requirements-maida.txt -o requirements-maida.lock
uv pip compile --universal --python-version 3.10 --generate-hashes requirements-e2e.txt -o requirements-e2e.lock
```

Run the [Action tests](#testing-the-action) and review the version/hash diff before committing. Use `--upgrade` only for a deliberate dependency refresh. Update the default Action versions and lock input together when changing the default CLI release.

## Tagged release provenance

The release workflow runs on full stable version-tag pushes. It tests the tagged source, archives that exact commit as `maida-assert.tar.gz`, generates `SHA256SUMS`, and creates GitHub build provenance using [`actions/attest`](https://github.com/actions/attest). It verifies the archive's attestation against the repository, release workflow, source commit and tag before publishing the archive, checksums and `provenance.jsonl` bundle together. The archive includes committed source only; local files are excluded. GitHub's automatically generated source downloads are separate and are not the attested artifact. This establishes source provenance, not a SLSA certification or a guarantee that the source is safe.

Only the release job receives `contents: write` (release assets), `id-token: write` (signing identity), and `attestations: write` (provenance publication); consumers need none of these for the ordinary gate. Protect version tags against unauthorized creation or movement and require review of the release workflow. Publish a new version tag from reviewed source after CI passes. Existing tags/releases are not overwritten; if publication partially fails, inspect the existing release and assets before maintainer recovery.

To verify a release produced by this workflow, set the reviewed version tag and full source commit, then run in an empty directory:

```bash
RELEASE_TAG=vX.Y.Z
RELEASE_COMMIT=FULL_REVIEWED_COMMIT_SHA
gh release download "$RELEASE_TAG" --repo maida-ai/maida-assert \
  --pattern maida-assert.tar.gz --pattern SHA256SUMS --pattern provenance.jsonl
sha256sum --check SHA256SUMS
gh attestation verify maida-assert.tar.gz --repo maida-ai/maida-assert \
  --bundle provenance.jsonl \
  --signer-workflow maida-ai/maida-assert/.github/workflows/release.yml \
  --source-digest "$RELEASE_COMMIT" --source-ref "refs/tags/$RELEASE_TAG"
```

See the [GitHub verification options](https://cli.github.com/manual/gh_attestation_verify). Checksums alone do not authenticate an artifact. Releases predating this workflow have no retroactive provenance guarantee; verify the selected release's assets and attestation before relying on it.

## Testing the Action

The `Action end-to-end` job runs on every PR, including forks and documentation changes. Add that exact check name to this repository's required checks; a workflow file alone cannot configure branch protection.

The local harness executes the composite shell steps, released `maida-ai==0.5.3`, and the pinned sticky-comment Action against a temporary consumer Git repository. It tests PASS/success, FAIL/failure, blocking INCONCLUSIVE/failure, report-only neutral results, base-policy selection, configuration acceptance and invalidation, trace-command ingestion, setup errors, and read-only check publication. Authorized acceptance runs the real CLI in a separate checkout and consumes only artifact bytes in a fresh writer directory. The local Git API fixture creates a baseline-only commit in a bare repository. The E2E runner executes the generated workflow event routing, separate acceptance jobs and dispatch steps directly from the coordinated `maida/maida/scaffold.py` generator. CI checks out a reviewed core commit by full SHA; local runs use the sibling `maida` checkout or `MAIDA_E2E_SCAFFOLD_PATH`. The tests exercise check/status/comment head identity, stale pushes, configuration acceptance, INCONCLUSIVE, and dispatch/publication failure recovery. They run the released CLI for behavioral evaluation. Planted candidate code probes credential exposure and writer-module injection; extra staged files, stale bindings, and policy changes are rejected. These local tests simulate the job boundary; they do not prove GitHub runner isolation or artifact-service permissions. The sticky Action must update the existing comment in place. Snapshots compare the complete posted Markdown, normalizing only trace IDs and binding expected reproduction paths to the actual trusted snapshot; the agent fixture supplies fixed recorded timing. All agent behavior is simulated.

Install test dependencies and run the suites with `uv` (Python 3.12, Node 24, Bash, and Git are required). Use the coordinated core checkout at `../maida` (or set `MAIDA_E2E_SCAFFOLD_PATH` to its `maida/scaffold.py`). Fetch the pinned third-party Action once:

```bash
git init /tmp/maida-sticky-comment
git -C /tmp/maida-sticky-comment remote add origin https://github.com/marocchino/sticky-pull-request-comment.git
git -C /tmp/maida-sticky-comment fetch --depth 1 origin 0ea0beb66eb9baf113663a64ec522f60e49231c0
git -C /tmp/maida-sticky-comment checkout --detach FETCH_HEAD
uv venv --python 3.12 /tmp/maida-action-tests
uv pip sync --python /tmp/maida-action-tests/bin/python --require-hashes --only-binary=:all: requirements-e2e.lock
uv run --python /tmp/maida-action-tests/bin/python --no-project pytest -q --ignore=tests/e2e
MAIDA_E2E_STICKY_PATH=/tmp/maida-sticky-comment MAIDA_E2E_SCAFFOLD_PATH=../maida/maida/scaffold.py uv run --python /tmp/maida-action-tests/bin/python --no-project pytest -q tests/e2e
```

Tests make no external API or model calls after dependency setup. The harness replaces Python/package setup, validates the pre-created checkout, and directs GitHub HTTP calls to a loopback fixture. Missing dependencies or unsupported runner expressions fail the suite. On snapshot failure, review the generated `consumer/comment.actual.md` before updating `tests/e2e/snapshots/`. CI retains fixture reports and API transcripts for seven days.

The weekly/manual report-only smoke uses a real GitHub runner, package installation, and Checks API with the simulated agent: one trial, no test retries, five-minute job timeout, and **$0 model spend**. It requests no provider credentials and does not post comments. GitHub Actions minutes remain subject to the account's billing plan; the timeout bounds runtime, not a dollar charge.

These tests do not establish merge-boundary enforcement. The scheduled smoke does not exercise live PR comments, required checks, or acceptance. Before claiming enforcement, use a disposable GitHub consumer with the [consumer protection settings](docs/usage.md#blocking-mode-and-required-repository-settings) and record the policy/base/head hashes, check IDs, job results, and actual merge attempts: PASS allowed; FAIL and gating INCONCLUSIVE refused; policy weakening/removal refused; accepted configuration change permitted only for its exact commit; subsequent commits blocked until reevaluated; and read-only/publication failures unable to authorize a merge. Verify workflow-file protection and required reviews on that repository too. No local fixture proves those GitHub settings. Live consumer verification remains outstanding.
