# Contributing to RetailMind AI

The full contribution guide — development setup, coding standards, testing
requirements, and the pull-request process — lives at
[`docs/contributing.md`](docs/contributing.md).

Quick links while you're here:

- **Local setup & running the app:** see the [Quick Start](README.md#quick-start) in the README
- **Running tests:** `make test` (fast, no Docker) and `make test-integration` (needs Docker)
- **Linting:** `make lint`
- **Known issues:** [`docs/known-issues.md`](docs/known-issues.md) — check
  before filing a bug that might already be tracked
- **Security issues:** do **not** open a public issue — see
  [`SECURITY.md`](SECURITY.md)

Every PR is expected to keep `make lint` and `make test` passing, and to
update the documentation that describes any behavior it changes — this
repository's own [CLAUDE.md](CLAUDE.md) explains why that matters here
specifically.
