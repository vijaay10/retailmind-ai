"""Check `.env.example` against the settings model.

`.env.example` opens with "CI checks this file against the settings model for
drift". It did not. This is that check.

Drift runs both ways and each direction fails differently:

* **A setting the model reads but the example never mentions** is the worse
  one. Someone deploying from the example gets the code's default silently —
  and defaults are chosen to be safe for development, which is exactly wrong
  for production. `RM_DB_SSLMODE` defaulting to `disable` is the example.

* **A key in the example the model ignores** is a lie in documentation. An
  operator sets it, nothing happens, and they spend an afternoon proving the
  application is broken when the variable was never read.

Secrets get a third rule: anything whose name looks like a credential must
either be absent from the example or carry an obviously non-real value. An
example file with a working password in it ends up in a screenshot.
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / ".env.example"

#: Prefixes the settings classes actually claim. A variable outside these is
#: not a settings drift problem — it belongs to compose, a container image, or
#: a third-party tool.
INFRA_PREFIXES = (
    "RM_API_PORT",
    "RM_API_LOCAL_PORT",
    "RM_UI_PORT",
    "RM_HTTP_PORT",
    "RM_HTTPS_PORT",
    "RM_APP_VERSION",
    "RM_DOMAIN",
    "RM_ACME_EMAIL",
    "RM_GRAFANA_URL",
    "RM_API_WORKERS",
    "RM_WORKER_CONCURRENCY",
    "RM_API_BASE_URL",
    "RM_PREVIEW_",
)

#: A value here would be a real credential. The example must not carry one.
SECRET_HINT = re.compile(r"(password|secret|token|key)$", re.I)

#: Values that are obviously placeholders rather than working credentials.
SAFE_PLACEHOLDER = re.compile(
    r"^$|dev-only|change|placeholder|example|your-|xxx|<.*>|minioadmin|/run/secrets/",
    re.I,
)


def model_settings() -> set[str]:
    """Every `RM_*` variable a settings class declares."""
    sys.path.insert(0, str(REPO / "backend"))
    sys.path.insert(0, str(REPO / "data_platform"))
    from pydantic_settings import BaseSettings

    from app.core import config as backend_config
    from ingestion.core import config as etl_config

    found: set[str] = set()
    for module in (backend_config, etl_config):
        for name in dir(module):
            candidate = getattr(module, name)
            if not (isinstance(candidate, type) and issubclass(candidate, BaseSettings)):
                continue
            if candidate is BaseSettings:
                continue
            prefix = candidate.model_config.get("env_prefix", "")
            if not prefix:
                continue
            found |= {f"{prefix}{field}".upper() for field in candidate.model_fields}
    return found


#: Not every variable goes through pydantic. Celery reads its broker URL
#: directly, the worker runtime reads SMTP settings, and the entrypoint reads
#: its own sizing knobs. Scanning for those keeps the check honest — otherwise
#: it reports a working variable as dead documentation.
ENV_READ = re.compile(r"""os\.environ(?:\.get)?[(\[]\s*["'](RM_[A-Z0-9_]+)["']""")


def source_settings() -> set[str]:
    """Every `RM_*` variable read from the environment directly."""
    found: set[str] = set()
    for root in ("backend/app", "data_platform/ingestion", "ml", "ui", "infra"):
        for path in (REPO / root).rglob("*"):
            if path.suffix in {".py", ".sh", ".yml", ".yaml"} and path.is_file():
                text = path.read_text(errors="ignore")
                found |= set(ENV_READ.findall(text))
                # Shell and compose reference them as ${RM_...}
                found |= set(re.findall(r"\$\{(RM_[A-Z0-9_]+)", text))
    return found


def example_keys() -> set[str]:
    keys: set[str] = set()
    for line in EXAMPLE.read_text().splitlines():
        stripped = line.strip().lstrip("#").strip()
        match = re.match(r"^(RM_[A-Z0-9_]+)\s*=", stripped)
        if match:
            keys.add(match.group(1))
    return keys


#: Variables documented ahead of the code that will read them. Allowed, but
#: only below an explicit heading, so nobody sets one expecting an effect.
PLANNED_HEADING = "# ── Not yet wired"


def planned_keys() -> set[str]:
    """Keys documented below the 'not yet wired' heading."""
    text = EXAMPLE.read_text()
    if PLANNED_HEADING not in text:
        return set()
    tail = text.split(PLANNED_HEADING, 1)[1]
    return set(re.findall(r"^#?\s*(RM_[A-Z0-9_]+)\s*=", tail, re.M))


def example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in EXAMPLE.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.split("#")[0].strip()
    return values


def main() -> int:
    if not EXAMPLE.exists():
        print(f"✗ {EXAMPLE} is missing")
        return 1

    declared = model_settings() | source_settings()
    documented = example_keys()
    problems: list[str] = []

    # The `*_FILE` convention: a secret may be documented either as the value
    # or as the path, and both satisfy the model's field.
    documented_bases = {key.removesuffix("_FILE") for key in documented}

    for key in sorted(declared - documented - documented_bases):
        if key.endswith("_FILE"):
            continue
        problems.append(
            f"✗ {key} is read by the settings model but absent from .env.example — "
            "a deployment from the example silently inherits the dev default"
        )

    planned = planned_keys()
    for key in sorted(documented - declared - planned):
        if key.startswith(INFRA_PREFIXES) or key.removesuffix("_FILE") in declared:
            continue
        problems.append(
            f"✗ {key} is in .env.example but nothing reads it — setting it does "
            "nothing, which costs somebody an afternoon. Move it under the "
            "'not yet wired' heading if it is planned."
        )

    for key, value in sorted(example_values().items()):
        if SECRET_HINT.search(key) and not SAFE_PLACEHOLDER.match(value):
            problems.append(
                f"✗ {key} carries what looks like a real credential — "
                "the example file ends up in screenshots and pull requests"
            )

    if problems:
        print("\n".join(problems))
        print(f"\n{len(problems)} problem(s). Fix .env.example or the settings model.")
        return 1

    print(f"✓ .env.example matches the settings model ({len(declared)} variables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
