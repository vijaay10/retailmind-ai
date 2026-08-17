# Prompt 17 — First Public GitHub Release

Record of the release performed on 2026-08-17. Every value below was read back
from the repository or from GitHub after the push, not carried forward from the
plan.

## 1. Repository URL

<https://github.com/vijaay10/retailmind-ai>

## 2. Visibility

`PUBLIC` — confirmed via `gh repo view`. License detected by GitHub as **MIT**.

Description: *Production-oriented multi-tenant retail intelligence platform for
analytics, forecasting, and decision intelligence.*

## 3. Branch

`main`, and it is the repository default. `main` tracks `origin/main`; working
tree clean and in sync after the push.

## 4. Commit hash

`bb99fba09f3b4709eb32cff31751809b3e5cc9ee`

Pushed on top of 34 pre-existing commits — history preserved in full, nothing
rewritten, squashed, or force-pushed. The public repository contains all 35
commits.

## 5. Commit author

| Field | Value |
|---|---|
| Author | `vijaay10 <senvijaay10@gmail.com>` |
| Committer | `vijaay10 <senvijaay10@gmail.com>` |
| Trailers | none |

Git identity was read, not modified. It resolves from `~/.gitconfig`.

## 6. Files added

256 files changed in the release commit: 47,144 insertions, 634 deletions.
567 files on `origin/main` in total.

| Area | Files |
|---|---|
| `backend/` | 117 |
| `docs/` | 45 |
| `data_platform/` | 45 |
| `ui/` | 19 |
| `infra/` | 10 |
| `scripts/` | 5 |
| root (LICENSE, CHANGELOG, CONTRIBUTING, SECURITY, README, Makefile, …) | 15 |

Substantively: multi-tenant warehouse isolation and company onboarding, the LLM
narration gateway (mock by default, real provider opt-in), Dagster
orchestration as an optional extra, Alertmanager routing, Grafana dashboards,
Postgres backup/restore scripts, application rate limiting, idempotency keys,
load-test tooling, and 46 documentation files.

## 7. Files intentionally excluded

Excluded by `.gitignore` and verified absent from `origin/main` after the push:

| Excluded | Why | Verified |
|---|---|---|
| `.env` | Real local environment, present on disk | 0 matches on remote |
| `infra/secrets/*` (6 files) | Generated credentials — DB password, JWT private key, MinIO, SMTP, Grafana | Only `.gitignore` and `README.md` published |
| `infra/docker/nginx/tls/` | Local certificates | 0 `.pem`/`.key` on remote |
| `.local/`, `*.duckdb` | Built warehouses | 0 matches on remote |
| `dbt/target/`, `dbt/logs/`, caches, `htmlcov` | Build artifacts | absent |

## 8. Security scan result

**PASS — no secrets published.**

- Scanned all 255 staged files plus the full staged diff for Anthropic/OpenAI
  keys, AWS access keys, GitHub tokens, Slack tokens, Google API keys, and
  `BEGIN … PRIVATE KEY` blocks. **Zero matches.**
- The LLM gateway reads its key from settings
  (`settings.anthropic_api_key`); no key is hardcoded anywhere.
- JWT strings in `docs/api.md` are truncated illustrations
  (`eyJ0eXAiOiJKV1QiLCJhbGc...`), not usable tokens.
- No real customer or transaction data. All data is synthetically generated
  from fixed seeds by `data_platform/ingestion/demo.py`.
- No database files, logs, or generated artifacts.

**One accepted, non-blocking finding.** Five occurrences of the local path
`/Users/vijaays/...` remain inside historical audit reports, where they appear
as part of documenting a path finding. These expose a local username and
nothing else; the same account name is already public in every commit author
line. Left in place because Phase 6 requires historical reports stay as
written.

## 9. Test result

Re-derived from actual runs immediately before the commit.

| Gate | Result |
|---|---|
| `ruff check .` | clean |
| `ruff format --check` | 343 files already formatted |
| `mypy backend/app` | clean, 178 files |
| `mypy ui` | clean, 36 files |
| `lint-imports` (clean architecture) | 1 contract kept, 0 broken |
| `scripts/check_env.py` | 77 variables reconciled |
| `scripts/check_ports.py` | production publishes only the edge |
| `scripts/check_docs_integrity.py` | passed |
| Fast ladder (backend/data_platform/ml/ui unit) | **1022 passed, 1 failed** |
| Integration (backend + data_platform) | **343 passed, 0 failed** |
| **Total** | **1365 / 1366** |

**The one failure is a documented, pre-existing limitation, not a release
blocker.** `ui/tests/test_workspaces.py::test_command_center_loads_without_error`
asserts the greeting is `app.markdown[0]`, but `design.configure()` emits a
global CSS block as the first `st.markdown` call. Confirmed at run time that
the observed failure matches the root cause recorded in
`docs/known-issues.md`. It is a test-fixture assumption about ordering, not a
product defect, and was left failing rather than weakened.

**Toolchain note.** `dagster` is an optional extra, and
`data_platform/tests/unit/test_dagster_orchestration.py` imports it
unconditionally — so `uv sync --all-packages` alone makes that module fail to
import, which aborts collection for the *entire* fast ladder rather than
skipping one file. Run the suite with `uv sync --all-packages --all-extras`.
Recorded in `CLAUDE.md`.

## 10. Git history attribution result

**PASS — no AI attribution anywhere in history.**

- All 35 commits: author *and* committer are `vijaay10 <senvijaay10@gmail.com>`.
  No other identity appears.
- Zero `Co-authored-by` / `Co-Authored-By` / `AI-generated-by` trailers in any
  commit.
- Searched all commit messages for `Claude`, `Anthropic`, `Co-authored-by`,
  `AI-generated`, `Claude Code`, `generated with`. One match: commit `f981285`
  names the *file* `CLAUDE.md` among the files it adds. That is a file
  reference, not an authorship claim.
- **No history rewriting was required or performed.**

Three files reference Anthropic or Claude for legitimate product reasons, and
will be publicly visible:

| File | Reference | Nature |
|---|---|---|
| `backend/pyproject.toml` | `anthropic>=0.40` | Real dependency of the opt-in LLM provider |
| `backend/.../seeds/sample.py` | `model_id="claude-sonnet-5"` | Seeded product data recording which model narrated a row |
| `.vscode/extensions.json` | `anthropic.claude-code` | Editor extension recommendation |
| `CLAUDE.md` | whole file | Project instructions for Claude Code sessions |

None attributes authorship. `CLAUDE.md`'s presence does disclose that the
project is developed with AI assistance; it is published deliberately and can
be removed at any time without affecting the code.

## 11. README verification

`README.md` is 370 lines and covers every required section: what RetailMind is,
the problem, the solution, core capabilities, how it works, what users provide,
what users receive, architecture, technology stack, screenshots, quick start,
testing, security, current limitations, roadmap, documentation index, license.

Ten claims were checked against the filesystem rather than accepted — LLM
gateway, mock-by-default narration, Dagster orchestration, multi-tenant
warehouse isolation, company onboarding API, Alertmanager, Grafana dashboards,
backup scripts, application rate limiting, idempotency keys. **All ten exist.**

One correction was made: the test badge read `1361/1362` and now reads
`1365/1366`, re-derived from the runs in §9.

`CLAUDE.md` was corrected in the same commit. Its "things that do not exist"
table still denied the LLM gateway, Dagster, Alertmanager and backups, all four
of which now exist — the file was wrong in the optimistic direction as well as
the pessimistic one. Stale test and workspace counts were fixed with it.

## 12. Final GitHub verification

Read back from `origin/main` after the push:

- Repository exists at the expected URL, owned by `vijaay10`
- Visibility `PUBLIC`, default branch `main`
- 567 files published; local `main` in sync with `origin/main`
- `README.md` present and rendering; `LICENSE` present, detected as MIT
- `.env`: **0** matches · `*.pem`/`*.key`: **0** · `*.duckdb`/`*.sqlite`: **0**
- `infra/secrets/` publishes only `.gitignore` and `README.md`
- Release commit carries no trailers; author and committer are the owner

---

## Verdict

🟡 **PUBLIC RELEASE COMPLETE WITH DOCUMENTED LIMITATIONS**

The release is complete, safe, and correctly attributed. It is marked amber
rather than green for one reason: **1 of 1366 tests fails.** That failure is
pre-existing, understood, documented in `docs/known-issues.md`, and a
test-fixture assumption rather than a product defect — but a green verdict
would mean every gate passed, and one did not.

Secondary items, none blocking:

- Four `*_IMPLEMENTATION.md` working notes sit at the repository root rather
  than in `docs/`. Two are self-marked stale. They are honest documents; moving
  or removing them is cosmetic and was not done unilaterally.
- Five local-path references remain in historical audit reports (§8).
- The full test suite requires `--all-extras` (§9).
