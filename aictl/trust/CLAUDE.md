# Trust Module Rules

- Cosign v3 ONLY (not v2) — requires --output json flag
- ORAS for OCI model pull — never use docker pull
- Keyless signing via OIDC (GitHub Actions, Google)
- Model verification is optional by default (trust_policy: warn)
- For regulated tenants: trust_policy must be "enforce"
- NEVER bypass signature verification for convenience
