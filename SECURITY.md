# Security Policy

## Supported versions

Security fixes target the latest released Action. Upgrade older versions, including reviewed SHA pins. The `maida-version` input selects the engine separately; engine vulnerabilities may also be reported below.

## Reporting a vulnerability

Email [security@maida.ai](mailto:security@maida.ai) privately. Do not post vulnerability details publicly before coordinated disclosure.

Include the Action SHA/version, engine version, impact, reproduction steps and a contact address. Remove secrets, customer data and sensitive traces from the reproduction.

## Response and disclosure

We aim to acknowledge reports within five business days. Resolution depends on severity and complexity. We coordinate investigation, fixes and disclosure with the reporter.

## Repository security controls

- Workflows use read-only default tokens and explicit job grants. Publishing jobs receive only their required write scopes.
- [Dependabot](.github/dependabot.yml) checks dependencies and Actions weekly. Enable and review repository alerts separately.
- [CodeQL](.github/workflows/codeql.yml) scans Python and Actions on PRs/pushes to `main` and `release/**`, weekly and on dispatch. Use advanced setup without also enabling default setup.
- Verify successful scans and triage alerts; configuration alone is not evidence of a clean scan.

## Dependency integrity

Third-party Actions use full SHA pins. Pin this Action to a reviewed SHA too; `@v5` and `@main` are discovery references.

Default engine installation uses `requirements-maida.lock`: exact versions, hashes and binary wheels only. Explicit `maida-version` overrides warn that they bypass the lock. The lock does not cover packages already present in the consumer environment.

See [dependency maintenance](CONTRIBUTING.md#dependency-maintenance) and [release verification](CONTRIBUTING.md#tagged-release-provenance).
