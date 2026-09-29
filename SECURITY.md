# Security Policy

## Supported versions

Security fixes target the latest released Action. Upgrade older versions, including reviewed SHA pins. Reports about older versions are welcome; reproduce on the latest release when possible. The `maida-version` input selects the engine separately; include both versions when reporting an issue that crosses the Action/engine boundary.

## Reporting a vulnerability

Email [security@maida.ai](mailto:security@maida.ai) privately. Do not post vulnerability details publicly before coordinated disclosure.

Include the Action SHA/version, engine version, runner environment, impact, reproduction steps and a contact address. Remove secrets, customer data and sensitive traces from the reproduction. Use simulated data where possible; do not send credentials or an unredacted workflow log.

## Response and disclosure

We aim to acknowledge reports within five business days. This is an acknowledgement target, not a guaranteed fix deadline. Resolution depends on severity and complexity. We coordinate investigation, fixes and disclosure with the reporter, and communicate the next steps after initial triage.

## Repository security controls

- Workflows use read-only default tokens and explicit job grants. Publishing jobs receive only their required write scopes.
- [Dependabot](.github/dependabot.yml) checks dependencies and Actions weekly. Enable and review repository alerts separately.
- [CodeQL](.github/workflows/codeql.yml) scans Python and Actions on PRs/pushes to `main` and `release/**`, weekly and on dispatch. Use advanced setup without also enabling default setup.
- Verify successful scans and triage alerts; configuration alone is not evidence of a clean scan.

## Runner data

The Action copies trusted policy and baseline files into temporary runner storage for evaluation. Temporary storage is not a guarantee that its contents are non-sensitive. Keep secrets out of checked-in configuration and baselines, restrict access to the runner, and review any artifact upload that includes these files. Include the runner's access and retention conditions when reporting a possible exposure; use a redacted reproduction.

## Dependency integrity

Third-party Actions use full SHA pins. Pin this Action to a reviewed SHA too; version tags are discovery references.

Default engine installation exports the `maida` group from `uv.lock` offline, then installs exact versions with hashes and binary wheels only. A stale lock stops installation. Explicit `maida-version` overrides warn that they bypass the lock. The lock does not cover packages already present in the consumer environment.

See [dependency maintenance](CONTRIBUTING.md#dependency-maintenance) and [release verification](CONTRIBUTING.md#tagged-release-provenance).
