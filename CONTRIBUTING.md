# Contributing to Maida’s GitHub Action

## Versioning

Follow the [cross-repository compatibility policy](https://github.com/maida-ai/maida/blob/main/CONTRIBUTING.md#versioning-and-compatibility). The Action uses the tested engine’s `MAJOR.MINOR` line and its own `PATCH` number. State the tested engine range in each release; matching numbers alone do not establish compatibility. The `maida-version` input selects the engine independently.

- Full `vMAJOR.MINOR.PATCH` tags are immutable stable releases.
- Before 1.0, advance a minor alias such as `v0.5` only after verifying its release. There is no `v0` alias.
- From 1.0, use moving major aliases. Document migration of the existing legacy `v1` before reusing that name.
- Alias updates, prereleases and Python `.postN` tags do not publish release artifacts.
- Pin a reviewed full commit SHA in production workflows.

### Legacy tags and migration

Legacy tags remain on their original commits: `v1` → `v0.1.0`, `v2` → `v0.2.0`, `V3`/`v3` → `v0.3.0`, `V4`/`v4` → `v0.4.0`, and `v5` → `v0.5.0`. Tags are case-sensitive. In particular, `v5` does not track `v0.5`. Preserve these references during compatibility migrations.

## Dependency maintenance

`pyproject.toml` declares the `dev` (unit), `e2e` (unit + released engine), and `maida` (runtime engine) groups. Commit `uv.lock` and use locked installs in CI. This is a virtual test project; its tooling version is separate from Action release tags.

After editing dependencies, update and review the shared lock:

```bash
uv lock
uv sync --locked --only-group dev --no-build
uv run --locked --no-sync pytest -q --ignore=tests/e2e
```

Use `uv lock --upgrade-package NAME` for a deliberate upgrade. Update the `maida` group and both Action engine defaults together; review the E2E generator SHA separately.

## Tagged release provenance

The release workflow tests the full stable tag, archives its committed source, and publishes `maida-assert.tar.gz`, `SHA256SUMS`, and `provenance.jsonl`. It verifies the attestation against the workflow, source commit and tag before publication. Protect release tags and require review of the release workflow; do not overwrite full tags or existing releases.

To verify an archive, set its reviewed tag and commit, then run in an empty directory:

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

Checksums alone do not authenticate an artifact. GitHub’s automatic source downloads are separate from the attested archive. Older releases have no retroactive provenance, and provenance does not certify that code is safe. See [GitHub’s verification options](https://cli.github.com/manual/gh_attestation_verify).

## Testing the Action

Use Python 3.12, Node 24, Bash and Git. The E2E suite needs the pinned sticky-comment Action and a coordinated core checkout at `../maida` (or `MAIDA_E2E_SCAFFOLD_PATH`). Fetch the fixture and install the test dependencies:

```bash
git init /tmp/maida-sticky-comment
git -C /tmp/maida-sticky-comment remote add origin https://github.com/marocchino/sticky-pull-request-comment.git
git -C /tmp/maida-sticky-comment fetch --depth 1 origin 0ea0beb66eb9baf113663a64ec522f60e49231c0
git -C /tmp/maida-sticky-comment checkout --detach FETCH_HEAD
uv sync --locked --only-group e2e --no-build --python 3.12
uv run --locked --no-sync pytest -q --ignore=tests/e2e
MAIDA_E2E_STICKY_PATH=/tmp/maida-sticky-comment MAIDA_E2E_SCAFFOLD_PATH=../maida/maida/scaffold.py uv run --locked --no-sync pytest -q tests/e2e
```

The local tests exercise verdicts, PR comments, trusted base policy, acceptance and dispatch using a temporary consumer repo and loopback GitHub fixtures. Agents are simulated; no model or external API calls occur after setup. Review `consumer/comment.actual.md` before updating report snapshots.

With the parameterized `init` generator, the suite also installs a real local-wheel dependency into separate temporary Python environments for the gate and acceptance capture, including repositories with a tools-only `pyproject.toml` and `requirements.txt`. It consumes the generated entrypoint and baseline paths and stays offline. The older generator pinned in CI predates dependency setup, so those cases explicitly skip there; update the reviewed generator pin after the coordinated core change is available remotely.

Require **Action end-to-end** in this repository’s branch protection. The weekly/manual report-only smoke tests runner installation and check publication with a simulated agent, a five-minute timeout and $0 model spend. It does not test live PR comments or merge protection.

### Live consumer verification

Local fixtures do not prove GitHub enforcement. Before claiming it, use a disposable consumer with the [required protection settings](docs/usage.md#blocking-mode-and-required-repository-settings), record the evaluated hashes/check IDs, and verify:

- PASS permits merging; FAIL and gating INCONCLUSIVE refuse it.
- Policy weakening/removal cannot grade itself.
- Configuration acceptance applies only to its exact commit and digest.
- New commits, stale results and publication failures cannot authorize merging.
- Workflow-file protection and required reviews work.

Use the [read-only post-accept verifier](docs/acceptance.md#verify-the-actual-post-accept-loop) to bind the acceptance, dispatch, accepted baseline, current-head check/status and sticky report. A success result establishes that observed loop and configured required checks; it does not establish merge refusal. Keep the fixture results, this evidence and actual merge/refusal results distinct in release sign-off.
