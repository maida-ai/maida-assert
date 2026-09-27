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
