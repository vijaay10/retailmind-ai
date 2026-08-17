"""Assert that the production overlay publishes nothing but the edge.

This exists because the obvious way to write it was wrong and looked right.

The overlay used to close its ports with `ports: []`, which reads as "publish
nothing". Compose does not merge overlays that way: mappings merge key by key,
but sequences like `ports` are *unioned*, so an empty list contributes nothing
and every port the base file published stayed published. The overlay claimed
the API was reachable only through the edge while the API sat on host port
8090 with no TLS, no rate limit, and no access log. Nothing failed. `up`
succeeded, the site worked, and the only symptom was a port that should not
have been open.

`!reset null` is the construct that actually removes an inherited value. That
fix is one word long and silently reverts the moment somebody rewrites the
overlay from memory — so the property is checked here instead of trusted to a
comment. A deployment's attack surface should not depend on knowing a YAML
merge rule.

Run: `uv run python scripts/check_ports.py`
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
#: How compose renders "every interface" when no host_ip is given.
ANY_INTERFACE = "0.0.0.0"  # noqa: S104 — a label in a message, not a bind
COMPOSE = REPO / "infra" / "compose"

#: Everything the production stack is allowed to bind on the host.
#:
#: The edge terminates TLS and is the front door. Grafana and Alertmanager are
#: bound to loopback deliberately — reachable over an SSH tunnel, never a
#: second login page published to the internet. Anything else appearing here
#: is a service that bypassed the proxy.
ALLOWED: dict[str, set[tuple[str, str]]] = {
    "edge": {("", "80"), ("", "443")},
    "grafana": {("127.0.0.1", "3000")},
    "alertmanager": {("127.0.0.1", "9093")},
}

#: Placeholders for the variables the overlay requires. Interpolation has to
#: succeed for compose to render the config at all; none of these values reach
#: a container.
PLACEHOLDERS = {
    "RM_DB_HOST": "postgres",
    "RM_DB_NAME": "retailmind_app",
    "RM_DB_USER": "api_rw",
    "RM_APP_BASE_URL": "https://retailmind.example.com",
}

#: Variables that would move the edge off 80/443. Cleared before rendering so
#: the check always validates the *default* deployment — a developer with
#: RM_HTTP_PORT set in their shell should not get a green check for a
#: configuration nobody deploys.
OVERRIDES = ("RM_HTTP_PORT", "RM_HTTPS_PORT")

SECRETS = (
    "db_password",
    "jwt_private_key",
    "smtp_password",
    "minio_user",
    "minio_password",
    "grafana_password",
)


def check_environment() -> dict[str, str]:
    """Placeholders in, local port overrides out."""
    env = {k: v for k, v in os.environ.items() if k not in OVERRIDES}
    return {**env, **PLACEHOLDERS}


def rendered_config(docker: str) -> dict:
    """The fully merged production configuration, as compose sees it."""
    # Secret *files* must exist for `config` to resolve them. Empty ones are
    # enough — the check never starts a container.
    secrets_dir = REPO / "infra" / "secrets"
    secrets_dir.mkdir(parents=True, exist_ok=True)
    created = []
    for name in SECRETS:
        path = secrets_dir / name
        if not path.exists():
            path.touch()
            created.append(path)

    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell
            [
                docker,
                "compose",
                "-f",
                "compose.yml",
                "-f",
                "compose.prod.yml",
                "config",
                "--format",
                "json",
            ],
            cwd=COMPOSE,
            env=check_environment(),
            capture_output=True,
            text=True,
            check=True,
        )
    finally:
        for path in created:
            path.unlink(missing_ok=True)

    return json.loads(result.stdout)


def main() -> int:
    docker = shutil.which("docker")
    if docker is None:
        print("• docker is not available — skipping the published-port check")
        return 0

    config = rendered_config(docker)
    problems: list[str] = []

    for name, service in sorted(config.get("services", {}).items()):
        published = {
            (str(entry.get("host_ip", "")), str(entry["published"]))
            for entry in service.get("ports", [])
            if entry.get("published")
        }
        allowed = ALLOWED.get(name, set())

        for host_ip, port in sorted(published - allowed):
            where = f"{host_ip or ANY_INTERFACE}:{port}"
            problems.append(
                f"✗ {name} publishes {where} in production. Only the edge should be "
                "reachable from the host. If the overlay closes this port with "
                "`ports: []`, that is the bug: compose unions sequences, so an "
                "empty list changes nothing. Use `ports: !reset null`."
            )

        for host_ip, port in sorted(allowed - published):
            where = f"{host_ip or ANY_INTERFACE}:{port}"
            problems.append(f"✗ {name} no longer publishes {where} — the stack has no front door")

    if problems:
        print("\n".join(problems))
        return 1

    surface = ", ".join(
        f"{name} {host_ip or ANY_INTERFACE}:{port}"
        for name, ports in sorted(ALLOWED.items())
        for host_ip, port in sorted(ports)
    )
    print(f"✓ production publishes only: {surface}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
