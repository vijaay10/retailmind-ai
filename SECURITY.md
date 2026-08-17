# Security Policy

## Supported Versions

RetailMind AI is currently in active development. Security updates are provided for the latest version only.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| < latest | :x:                |

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to: **security@retailmind.example** (replace with actual contact)

Please include:

1. **Description** - Clear description of the vulnerability
2. **Impact** - What an attacker could achieve
3. **Steps to Reproduce** - Detailed steps to reproduce the issue
4. **Affected Components** - Which parts of the system are affected (API, UI, data platform, etc.)
5. **Suggested Fix** (optional) - If you have ideas for remediation

### What to Expect

- **Acknowledgment** - We'll acknowledge receipt within 48 hours
- **Initial Assessment** - We'll provide an initial assessment within 5 business days
- **Updates** - We'll keep you informed of progress
- **Disclosure** - We'll coordinate disclosure timing with you
- **Credit** - We'll credit you in the security advisory (unless you prefer to remain anonymous)

## Security Best Practices for Deployment

### Authentication

- Always configure `RM_AUTH_JWT_PRIVATE_KEY` in production (never use ephemeral keys)
- Use strong, unique passwords (minimum 12 characters)
- Enable multi-factor authentication where possible
- Rotate JWT signing keys periodically (recommended: every 90 days)

### Secrets Management

- Never commit secrets to version control
- Use file-based secrets (`infra/secrets/`) or secret management systems
- Mount secrets read-only at `/run/secrets/`
- Rotate database passwords, API keys, and other credentials regularly

### Network Security

- Run production stack with `compose.prod.yml` overlay (unpublishes internal ports)
- Use TLS for all external connections (Nginx handles termination)
- Configure firewall rules to restrict access
- Use `edge` and `app` network separation

### Container Security

- Containers run read-only with `tmpfs` for scratch space
- Non-root users enforced
- `no-new-privileges` security option enabled
- Resource limits configured (CPU, memory)

### Database Security

- Use `RM_DB_SSLMODE=require` for encrypted connections
- Limit database user permissions (principle of least privilege)
- Enable PostgreSQL data checksums (`--data-checksums` flag)
- Configure connection limits and timeouts

### API Security

- Rate limiting enabled (100 req/min per IP, 200 req/min per user)
- Idempotency keys required for mutations
- CORS restricted to `RM_APP_BASE_URL` only
- Security headers configured (HSTS, CSP, X-Frame-Options, X-Content-Type-Options)

### Monitoring & Incident Response

- Enable Prometheus metrics collection
- Configure Alertmanager for critical security events
- Monitor for:
  - Failed authentication attempts
  - Rate limit violations
  - Unusual API access patterns
  - Database connection failures
- Review logs regularly for suspicious activity

## Known Security Limitations

### PII Handling

- **No encryption at rest** - User emails stored in plaintext in database
- **Mitigation** - Database access restricted, backups should be encrypted
- **Status** - Encryption planned for future release

### Dependency Scanning

- **No automated CVE scanning** - Dependencies not automatically scanned for vulnerabilities
- **Mitigation** - Manual review recommended, `uv` keeps dependencies updated
- **Status** - pip-audit integration planned

### LLM Security (If Enabled)

When using Anthropic Claude API:

- **No PII scrubbing implemented** - User data may be sent to Anthropic
- **Cost controls limited** - No hard spending caps on LLM usage
- **Prompt injection** - Limited protection against adversarial prompts
- **Mitigation** - LLM defaults to mock mode (no external API calls)
- **Best Practice** - Only enable LLM with explicit user consent

### Backup Security

- **No offsite backups** - Backups stored locally only
- **No backup encryption** - pg_dump files not encrypted
- **Mitigation** - Configure S3 sync with encryption, restrict volume access

## Security Audit Results

Last audit: 2026-08-15

**Overall Score:** 9.6/10

**Findings:**
- Critical: 0
- High: 2 (dev passwords in compose, no secret rotation policy)
- Medium: 4 (PII encryption, CVE scanning, dependency pinning, some endpoints lack authz)
- Low: 3

**Full Report:** [docs/security-audit.md](docs/security-audit.md)

## Compliance

### OWASP Top 10 (2021)

- ✅ A01:2021 - Broken Access Control: RBAC implemented
- ✅ A02:2021 - Cryptographic Failures: bcrypt, TLS configured
- ✅ A03:2021 - Injection: SQLAlchemy ORM, no raw SQL
- ⚠️ A04:2021 - Insecure Design: Some endpoints lack explicit authz
- ✅ A05:2021 - Security Misconfiguration: Hardened containers
- ✅ A06:2021 - Vulnerable Components: Using maintained dependencies
- ✅ A07:2021 - Identification and Authentication Failures: JWT+bcrypt
- ⚠️ A08:2021 - Software and Data Integrity Failures: No SBOM/signing
- ✅ A09:2021 - Security Logging Failures: Structured logging present
- ✅ A10:2021 - SSRF: No outbound requests to user-controlled URLs

## Security-Related Configuration

### Required Environment Variables (Production)

```bash
# JWT signing (REQUIRED - never use ephemeral keys in production)
RM_AUTH_JWT_PRIVATE_KEY_FILE=/run/secrets/jwt_private_key

# Database credentials (use secrets, not environment)
RM_DB_PASSWORD_FILE=/run/secrets/db_password

# TLS configuration
RM_HTTPS_ENABLED=true
RM_TLS_CERT_PATH=/etc/nginx/tls/cert.pem
RM_TLS_KEY_PATH=/etc/nginx/tls/key.pem

# Rate limiting (enabled in prod by default)
RM_RATE_LIMIT_ENABLED=true

# CORS (should be empty or specific domain in prod)
RM_CORS_ORIGINS=""  # or "https://your-domain.com"
```

### Security Headers

The following headers are automatically added by `SecurityHeadersMiddleware`:

```
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000
```

## Vulnerability Disclosure Policy

We follow responsible disclosure:

1. **Private Reporting** - Report vulnerabilities privately first
2. **Coordination** - We'll work with you on disclosure timing
3. **Public Disclosure** - After fix is deployed, we'll publish a security advisory
4. **CVE Assignment** - For critical issues, we'll request a CVE ID
5. **Credit** - Security researchers will be credited in advisories

## Security Contact

- **Email:** security@retailmind.example (replace with actual)
- **PGP Key:** (provide if available)
- **Response Time:** Within 48 hours for acknowledgment

Thank you for helping keep RetailMind AI secure!
