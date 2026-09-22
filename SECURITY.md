# Security policy

## Scope

This project processes public open data and produces static 3D artefacts. It has no
authentication, handles no personal data, and stores no secrets. The realistic risk
surface is small: dependency vulnerabilities, and parsers that read untrusted files.

The parsers are worth real attention. `mtl_roofs.io.remotezip` reads ZIP structures
over HTTP, and `mtl_roofs.io.reference` parses XML — both are classic places for
malformed input to cause trouble (zip bombs, oversized allocations, XML entity
expansion). Reports about them are welcome.

## Supported versions

The latest release and `main`. This is a portfolio project; there are no backports.

## Reporting a vulnerability

Please **do not open a public issue.** Use GitHub's
[private vulnerability reporting](https://github.com/grigorybaluev/mtl-roofs/security/advisories/new).

Expect an acknowledgement within a week. Since this is a personal project maintained
in spare time, please allow a reasonable window before public disclosure.

## What is automated

- CodeQL for Python and TypeScript, weekly and on every PR
- Dependabot alerts and security updates
- Secret scanning with push protection
- `gitleaks` in pre-commit, so a secret is caught before it is committed
