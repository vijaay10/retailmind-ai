# Deployment

How RetailMind is built, shipped, and run — and, where it matters more, the
things that were wrong until somebody ran them.

Most of what follows is ordinary. The parts worth reading twice are the ones
marked **silent failure**: configuration that was accepted, started cleanly,
reported healthy, and did not do what it said. Every one of them was found by
running the stack rather than by reading it, which is the argument for the
verification section at the end.

---

## 1. The shape of it

One image serves five roles. `infra/docker/api-entrypoint.sh` dispatches on
`$1`: `api`, `api-reload`, `migrate`, `worker`, `beat`. A worker built from a
separate image is a worker running different code from the API that enqueued
the task, and the symptom is a task that fails only in production.

The console (`infra/docker/ui.Dockerfile`) is a second image because Streamlit
and FastAPI have genuinely different runtimes.

```
                    ┌─────────┐
   443 ─── TLS ────▶│  edge   │  nginx: TLS, rate limits, security headers
                    └────┬────┘
                 /api/   │   /
              ┌──────────┴─────────┐
              ▼                    ▼
         ┌─────────┐          ┌─────────┐
         │   api   │          │   ui    │   Streamlit
         └────┬────┘          └─────────┘
              │
   ┌──────────┼───────────┬─────────────┐
   ▼          ▼           ▼             ▼
postgres  redis-cache  redis-state   warehouse
                            ▲
                            │  broker
                    ┌───────┴────────┐
                    ▼                ▼
                 worker             beat
```

`migrate` runs to completion before anything serves
(`service_completed_successfully`). A migration racing four API workers is how
two of them apply the same revision.

## 2. Files

| Path | What it is |
|---|---|
| `infra/compose/compose.yml` | Base stack. Runs with no proxy, publishes app ports, dev passwords. |
| `infra/compose/compose.dev.yml` | Source mounts, reload, debug logging. |
| `infra/compose/compose.prod.yml` | Production overlay — see §4, it is where the traps are. |
| `infra/docker/api.Dockerfile` | Multi-stage, non-root uid 10001, venv at its runtime path. |
| `infra/docker/ui.Dockerfile` | Same conventions, Streamlit healthcheck. |
| `infra/docker/nginx/` | `nginx.conf` plus `conf.d/retailmind.conf`. |
| `infra/monitoring/` | Prometheus config, alert rules, Grafana provisioning. |
| `infra/secrets/` | Secret files, gitignored. `README.md` explains generation. |
| `scripts/check_env.py` | `.env.example` against the settings model, both directions. |
| `scripts/check_ports.py` | What production publishes. See §4. |
| `scripts/tls-local.sh` | Self-signed cert for local TLS. |
| `scripts/tls-letsencrypt.sh` | ACME issuance; staging by default. |

## 3. Running it

Development:

```bash
cd infra/compose && docker compose -f compose.yml -f compose.dev.yml up -d
```

Production:

```bash
cd infra/compose
export RM_DB_HOST=postgres RM_DB_NAME=retailmind_app RM_DB_USER=api_rw
export RM_APP_BASE_URL=https://retailmind.example.com
docker compose -f compose.yml -f compose.prod.yml up -d
```

Secrets must exist first — `infra/secrets/README.md` has the generation
commands. The overlay declares `${RM_DB_HOST:?...}` for the variables that
have no safe default, so a missing one fails at `up` rather than silently
starting against localhost.

## 4. The production overlay, and two ways it lied

### Ports: `ports: []` does not remove ports

**Silent failure.** The overlay closed the API and console ports with
`ports: []`, which reads as "publish nothing". Compose does not merge that
way. Mappings merge key by key, but sequences like `ports` are *unioned* — so
an empty list contributes nothing, and every port the base file published
stayed published. The overlay claimed the API was reachable only through the
edge while the API sat on host port 8090: no TLS, no rate limit, no access
log. `up` succeeded. The site worked. The only symptom was an open port.

`!reset null` is the construct that actually removes an inherited value:

```yaml
api:
  ports: !reset null   # the edge is the only way in
```

That fix is one word long and reverts the moment somebody rewrites the overlay
from memory, so it is asserted rather than trusted:

```bash
uv run python scripts/check_ports.py
```

It renders the merged production config and fails if anything but the edge
(80/443) and loopback Grafana publishes a host port. CI runs it. A
deployment's attack surface should not depend on knowing a YAML merge rule.

### Environment: overlays add, they do not replace

Same rule, opposite direction. `compose.yml` sets
`POSTGRES_PASSWORD: dev-only-password`; the overlay adds
`POSTGRES_PASSWORD_FILE`. Both reach the container, and the Postgres image
refuses to start when both are set — *this* one at least failed loudly. The
overlay blanks the inherited value (`POSTGRES_PASSWORD: ""`), which is how an
overlay removes a variable the base defined.

## 5. Secrets

Production uses the `*_FILE` convention throughout. A value in `environment:`
is visible to anyone who can run `docker inspect`, appears in `ps`, and is
inherited by every child process. The platform mounts the secret; the
application reads the path.

**Silent failure.** The convention was only half implemented. `AuthSettings`
resolved `RM_AUTH_JWT_PRIVATE_KEY_FILE`, but `DatabaseSettings` had no
`password_file` field at all — and pydantic's `extra="ignore"` meant
`RM_DB_PASSWORD_FILE` was accepted, discarded, and the dev default used
instead. The observable symptom was `password authentication failed for user
"api_rw"` against a database initialised from that very secret file.

Both now go through `read_secret()` in `backend/app/core/config.py`, which
strips trailing newlines — and that detail is load-bearing. Docker's own
`file_env` helper reads secrets with `$(< file)`, which drops trailing
newlines. A secret file written with a final newline gives Postgres a password
without it and the application a password with it. The two disagree,
authentication fails, and nothing in the error mentions a newline.

## 6. The edge

`infra/docker/nginx/conf.d/retailmind.conf`. TLS 1.2/1.3, HTTP→HTTPS redirect
with the ACME challenge path exempted, per-zone rate limits (login gets its
own bucket), and security headers set at the edge because both upstreams would
otherwise have to agree.

**Silent failure.** The Content-Security-Policy header was written across
several lines with trailing backslashes, the way one would in shell. nginx has
no line continuation inside a quoted string: the value kept literal newlines,
`nginx -t` passed, and HTTP/2 dropped the malformed header. There was no CSP
on any response and nothing anywhere said so. It is one line now, and §9
checks for it.

The access log is JSON, one object per request, carrying a request id that
honours an inbound `X-Request-Id` and generates one otherwise. Its duration
fields are named `duration_s` / `upstream_s`: nginx's `$request_time` is
seconds with millisecond resolution, and the fields were originally named
`_ms`, so a 9 ms request logged `0.009` under a name that would have any
consumer reporting it a thousand times fast.

There is no log volume. The nginx image symlinks `access.log` to `/dev/stdout`,
so a volume mounted at `/var/log/nginx` collects two symlinks and nothing else
while looking exactly like log persistence in the compose file. Logs go to the
json-file driver, rotated, like every other service.

`/metrics` returns 404 at the edge — exposing it publicly hands an attacker
request rates, error counts and endpoint names for free. Prometheus scrapes
`api:8000/metrics` on the internal network instead.

Streamlit's session runs entirely over a WebSocket, so `/` carries the upgrade
headers and 3600s read timeouts. A shorter timeout disconnects idle users and
they see "connection error" for having gone to lunch.

### TLS

```bash
scripts/tls-local.sh                        # self-signed, local
RM_DOMAIN=... RM_ACME_EMAIL=... scripts/tls-letsencrypt.sh   # staging
RM_DOMAIN=... RM_ACME_EMAIL=... LIVE=1 scripts/tls-letsencrypt.sh
```

Staging by default: Let's Encrypt rate-limits failed issuance per domain, and
the way to discover a webroot misconfiguration is not by burning the week's
quota.

## 7. Workers

**Silent failure, and the worst of them.** `autodiscover_tasks(["app.workers.tasks"])`
searches for a `tasks` submodule *inside* each package listed — so it looked
for `app.workers.tasks.tasks`, found nothing, and registered nothing. The
worker started, logged "ready", connected to the broker, and held an empty
registry. Beat published on schedule. Every message came back `Received
unregistered task`, once every ten minutes, into a log nobody reads while
everything appears to work.

This is characteristic of scheduled work: there is no caller waiting on a
response, so a task that never runs looks exactly like a quiet system. The
detection sweep *is* the alerting pipeline — the one component whose silence
is indistinguishable from good news.

Tasks are now listed explicitly in `celery_app.conf.imports`, where a wrong
module name is an `ImportError` at startup instead of silence.
`backend/tests/unit/test_worker_contract.py` asserts that every scheduled task
is registered, which catches it without a broker.

Two smaller ones in the same area:

* The broker URL was never set in compose. The code's default is
  `redis://localhost:6379/1`, which inside a container is the container
  itself, so the worker started fine and consumed nothing. Both `worker` and
  `beat` now set it explicitly to the durable Redis — never the cache, because
  a broker on an LRU-evicting instance drops queued tasks under memory
  pressure and nothing reports it.
* The worker healthcheck used exec-form `CMD`, which runs no shell, so
  `$HOSTNAME` stayed a literal and the ping addressed a node that does not
  exist. The container was permanently unhealthy while working perfectly.
  `CMD-SHELL` now. Pinning the ping to *this* container's node is still right:
  an unpinned ping is answered by any worker on the broker, so a dead
  container stays "healthy" as long as a sibling replies.

## 8. Monitoring

Prometheus scrapes the API; alert rules live in `infra/monitoring/alerts.yml`:

| Alert | Fires when |
|---|---|
| `ApiDown` | the API stops answering scrapes |
| `HighErrorRate` | sustained 5xx share |
| `SlowRequests` | p95 beyond the SLO |
| `NoDetectionSweep` | the sweep gauge goes stale — silence, not errors |

Grafana is bound to `127.0.0.1:3000`: reachable over an SSH tunnel, never a
second login page published to the internet.

**Silent failure.** The API runs four uvicorn workers, each with its own
metrics registry, and `PROMETHEUS_MULTIPROC_DIR` was never set. Prometheus
scraped whichever worker the kernel happened to hand the connection to, so
every rate read low by roughly the worker count and jumped between scrapes.
The entrypoint now sets the directory and clears it at boot — files left by a
previous container describe dead processes, and their counters would be added
to the live ones, every restart inflating the totals a little more. The two
gauges declare `multiprocess_mode="max"` so they do not fan out into one
series per pid.

Labels are route templates, never raw paths: `/api/v1/forecasts/{target}` is
one series, and labelling by raw path lets anyone with a URL bar add unbounded
cardinality. Requests that match nothing collapse into `<unmatched>`.

A related correction: FastAPI's `APIRoute` puts itself in the request scope,
but a plain `starlette.routing.Route` — which is what FastAPI uses for
`/api/openapi.json` and the docs — does not. Those requests were counted as
`<unmatched>` next to scanner traffic despite returning 200. `_route_label()`
falls back to the raw path only when the request demonstrably matched
something and bound no path parameters, which keeps the label set bounded by
the routes the application registers.

## 9. Verifying a deployment

Configuration that parses is not configuration that works. Every check below
corresponds to something in this document that passed inspection and failed in
practice.

```bash
# Only the edge is exposed
uv run python scripts/check_ports.py

# The environment contract holds in both directions
uv run python scripts/check_env.py

# TLS, and the redirect
curl -sk -o /dev/null -w '%{http_code}\n' https://HOST/
curl -s  -o /dev/null -w '%{http_code}\n' http://HOST/           # 301

# Security headers — CSP especially, it is the one that vanished
curl -sk -D - -o /dev/null https://HOST/ | grep -i 'content-security-policy'

# API routing, and metrics not being public
curl -sk -o /dev/null -w '%{http_code}\n' https://HOST/api/openapi.json   # 200
curl -sk -o /dev/null -w '%{http_code}\n' https://HOST/metrics            # 404

# The console's WebSocket actually upgrades
curl -sk -o /dev/null -D - --http1.1 \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  https://HOST/_stcore/stream | head -1                                  # 101

# The worker has tasks — "ready" in the log does not mean this
docker exec <worker> python -m celery -A app.workers.celery_app inspect registered

# Prometheus is actually scraping
docker exec <prometheus> wget -qO- 'http://localhost:9090/api/v1/targets?state=active'
```

Every one of these was run against the stack described here, and four of them
failed the first time.

## 10. CI

`.github/workflows/build.yml`:

* **images** — build once to the local daemon, Trivy gate on fixable
  CRITICAL/HIGH, smoke-test that the entrypoint runs and the app imports, SBOM,
  then push and sign with keyless cosign. Building separately for the scan and
  the push produces two different images and proves nothing about the one that
  ships. Pull requests stop before the push.
* **compose** — every overlay combination parses, then `check_ports.py`
  asserts the merged result. Parsing was never the property that mattered.
* **environment** — `check_env.py`.
