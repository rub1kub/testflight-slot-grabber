# Security

## Scope

This project operates only on public TestFlight invitation pages and the local TestFlight UI. It must not be used to bypass invitation eligibility, certificate pinning, Apple Account controls, device binding, or beta-program limits.

## Secrets

- `config.json`, logs, state, generated app bundles and build products are excluded from Git.
- Optional notification credentials are read from macOS Keychain.
- Authorization, cookies, passwords, tokens and chat IDs are recursively redacted from structured logs.
- The experimental API replay path is fail-closed and disabled because no stable, safe request format was established.

AX tree dumps and screenshots can contain visible UI text. Review them manually before attaching them to an issue.

## Reporting

Do not open a public issue containing credentials, cookies, Apple Account identifiers, screenshots with personal data, or unredacted diagnostic bundles. Report only a minimal reproduction with secrets removed.
