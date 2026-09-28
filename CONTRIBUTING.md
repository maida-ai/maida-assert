# Contributing to Maida’s GitHub Action

## Versioning

The [Maida contributor policy](https://github.com/maida-ai/maida/blob/main/CONTRIBUTING.md#versioning-and-compatibility) defines cross-repository compatibility. This Action has its own `PATCH` releases within the `MAJOR.MINOR` compatibility line set by the `maida-ai` engine. The Action is the CI gate product, so each new release must test the engine range and functionality it claims to support. The `maida-version` input selects the actual engine package installed in a run; the Action tag alone does not select or prove a compatible engine version.

For new releases, create an immutable full tag such as `v0.5.2` for an Action tested with the engine's `0.5` line, and advance the `v0.5` alias to the latest compatible Action patch. Do not move a full release tag. At version 1 or later, also maintain a moving major tag such as `v1`. There is no `v0` alias: pre-1.0 minor releases may contain incompatible changes. A full commit SHA remains the immutable reference for production workflows. The README’s `@v5` examples select the latest published GitHub release under the legacy scheme; they do not track the newer `v0.5` alias. Update them only after the replacement Action release exists and has been verified.

The release workflow publishes only full stable `vMAJOR.MINOR.PATCH` tags. Moving aliases, prereleases, and Python `.postN` tags are not publication inputs. Advance a compatibility alias only after verifying the full release and its provenance; alias updates do not publish another release.

### Legacy tags and migration

Historical tags remain available at their existing commits: `v1` corresponds to `v0.1.0`, `v2` to `v0.2.0`, `V3`/`v3` to `v0.3.0`, `V4`/`v4` to `v0.4.0`, and `v5` to `v0.5.0`. Tag names are case-sensitive. These are legacy references, not the moving major aliases of the new policy. In particular, `v5` does not advance with the `v0.5` compatibility alias. Preserve historical tags during compatibility-line migrations; migrate consumers to a reviewed full commit SHA or a verified compatibility alias rather than repointing a historical tag. Before adopting a genuine `v1` moving major alias at 1.0, explicitly document the migration from the existing legacy `v1` reference.
