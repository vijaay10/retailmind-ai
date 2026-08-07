# Secrets

Files mounted into `/run/secrets`. Each holds one value, no trailing newline
handling assumed beyond what the consumer does.

Generate them before the first production start:

```bash
umask 077
openssl rand -base64 48 > db_password
openssl genpkey -algorithm ed25519 -out jwt_private_key
openssl rand -base64 32 > smtp_password       # or the real SMTP credential
openssl rand -base64 24 > minio_user
openssl rand -base64 48 > minio_password
openssl rand -base64 32 > grafana_password
```

**Why files rather than environment variables.** A value in `environment:` is
visible to anyone who can run `docker inspect`, shows up in `ps` output on the
host, and is inherited by every child process the container spawns — including
anything an attacker manages to execute. A file is readable by the process and
nothing else.

Swap `file:` for `external: true` in the compose overlay when running under
Swarm or a platform with its own secret store. Nothing else changes.
