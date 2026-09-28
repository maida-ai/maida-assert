# Security Policy

## Supported versions

Security fixes for the Maida GitHub Action (`maida-ai/maida-assert`) target the latest released Action version. Older versions are not maintained for security fixes; upgrade to the latest release. If your workflow pins the Action to a full commit SHA, update that pin to the reviewed commit containing the fix.

The `maida-version` input selects the Maida engine independently of the Action version. This policy covers the Action; engine vulnerabilities can also be reported to the contact below.

## Reporting a vulnerability

Report suspected vulnerabilities privately to [security@maida.ai](mailto:security@maida.ai). This mailbox is monitored by the maintainer. Please do not disclose vulnerability details in a public issue or pull request before a fix or coordinated disclosure is ready.

Include the following information when available:

- The affected Action version or commit SHA and the selected Maida engine version.
- A description of the vulnerability, its potential impact, and any required conditions.
- Minimal reproduction steps or a proof of concept, including relevant workflow configuration.
- Your preferred contact details for follow-up.

Remove credentials, tokens, private keys, customer data, and sensitive trace content from reports and examples. Use a minimal, sanitized reproduction whenever possible.

## Response and disclosure

We aim to acknowledge reports within five business days. This is a response target, not a guaranteed resolution time. Investigation and remediation time depend on the severity and complexity of the issue.

We will coordinate with the reporter on follow-up information, remediation, and public disclosure. Please allow time for investigation and a fix before publishing details that could put users at risk.

## Repository security controls

Repository workflows declare read-only default token permissions and explicit job grants; publishing jobs request only the write scopes they need.

[Dependabot configuration](.github/dependabot.yml) schedules weekly version checks for Python requirements, GitHub Actions workflows, and composite actions. Dependabot alerts and security updates are separate repository settings. Version checks do not replace reviewing dependency alerts. See the [dependency integrity guidance](README.md#dependency-and-release-integrity) for the locked installation manifests.

The [CodeQL workflow](.github/workflows/codeql.yml) scans Python and GitHub Actions on pushes and pull requests targeting `main` or `release/**`, weekly, and on manual dispatch. It uses pinned actions and grants `security-events: write` only to the analysis job. Use this workflow as advanced setup; do not also enable CodeQL default setup. Verify successful scans and Dependabot update checks, triage alerts in the repository Security tab, and record results before considering the security baseline complete. These configurations do not themselves prove a successful scan or the absence of vulnerabilities.
