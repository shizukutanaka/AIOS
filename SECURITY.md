# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.6.x   | ✓ Active  |
| < 1.6   | ✗ No longer supported |

## Reporting a Vulnerability

**Please do not report security vulnerabilities via GitHub Issues.**

Report security issues privately via GitHub's Security Advisory feature:
https://github.com/shizukutanaka/aios/security/advisories/new

Or email: security@shizukutanaka.dev

### What to include

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

### What to expect

- Acknowledgement within 48 hours
- Initial assessment within 7 days
- Patch release within 30 days for confirmed critical issues

## Security Design

aictl is designed with security as a first-class concern:

### Data handling
- **PII minimization** — no user data leaves the machine without explicit opt-in
- **Local-first** — all inference, caching, and guardrails run locally by default
- **No telemetry** — aictl collects no usage data
- **Audit log** — all operations are logged locally via `aictl audit`

### Secrets
- Never hardcodes credentials
- Reads secrets from environment variables or OS keychain
- No secrets in logs or error messages
- `gitleaks` scan required before every commit

### Dependencies
- Zero external Python runtime dependencies (stdlib only)
- All Go dependencies audited quarterly
- Supply chain: `go mod verify` + `pip-audit` in CI

### Authentication
- API keys use Ed25519 signatures (`aictl apikey create`)
- All inter-service communication uses mTLS
- JWT tokens use RS256/ES256 with 15-minute expiry

### Known security boundaries

- The mock engine (`aictl demo`) is **not** safe for production use
- `--non-interactive` mode skips confirmation prompts — do not use in untrusted environments
- MCP server stdio transport trusts all input on stdin — run in a trusted process context
