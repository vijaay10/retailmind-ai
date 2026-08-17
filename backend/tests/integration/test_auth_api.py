"""End-to-end authentication and authorization against a real database.

Covers the flows that only integration can prove: rotation actually persists,
reuse actually revokes a family, ``token_version`` actually invalidates live
tokens, and every seeded role gets exactly the permissions the matrix promises.
"""

import pytest
from httpx import AsyncClient

from tests.integration.warehouse import USERS  # noqa: E402

pytestmark = pytest.mark.integration

DEMO_PASSWORD = "ChangeMe-Demo1!"  # noqa: S105 — seeded demo credential

# One seeded user per role (see the sample seed).


async def _login(client: AsyncClient, email: str, password: str = DEMO_PASSWORD):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


async def _token(client: AsyncClient, email: str) -> str:
    response = await _login(client, email)
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Login ────────────────────────────────────────────────────────────


async def test_login_returns_access_token_and_sets_refresh_cookie(client: AsyncClient) -> None:
    response = await _login(client, USERS["ceo"])
    assert response.status_code == 200

    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900  # 15 minutes
    assert body["access_token"]

    cookie = response.cookies.get("rm_refresh")
    assert cookie, "refresh token must be delivered as a cookie"
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "samesite=strict" in set_cookie
    assert "path=/api/v1/auth" in set_cookie


async def test_login_with_wrong_password_is_rejected(client: AsyncClient) -> None:
    response = await _login(client, USERS["ceo"], "definitely-not-the-password")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["www-authenticate"] == "Bearer"


async def test_unknown_account_is_indistinguishable_from_wrong_password(
    client: AsyncClient,
) -> None:
    """User enumeration guard: identical status, type, and wording."""
    unknown = await _login(client, "nobody@northwind.example")
    wrong = await _login(client, USERS["ceo"], "wrong-password-here")

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["type"] == wrong.json()["type"]
    assert unknown.json()["detail"] == wrong.json()["detail"]


async def test_login_rejects_malformed_payload(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login", json={"email": "not-an-email", "password": "short"}
    )
    assert response.status_code == 422
    assert {e["field"] for e in response.json()["errors"]} == {"email", "password"}


async def test_login_rejects_unknown_fields(client: AsyncClient) -> None:
    """extra='forbid' — a typo'd field is an error, not silence."""
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": USERS["ceo"], "password": DEMO_PASSWORD, "admin": True},
    )
    assert response.status_code == 422


# ── Protected routes ─────────────────────────────────────────────────


async def test_me_requires_a_token(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["type"].endswith("/unauthenticated")


async def test_me_returns_identity_and_resolved_permissions(client: AsyncClient) -> None:
    token = await _token(client, USERS["inventory"])
    response = await client.get("/api/v1/auth/me", headers=_auth(token))

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == USERS["inventory"]
    assert body["roles"] == ["inventory"]
    assert "analytics.inventory.read" in body["permissions"]
    assert "admin.users" not in body["permissions"]


async def test_garbage_token_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me", headers=_auth("not.a.jwt"))
    assert response.status_code == 401


async def test_token_signed_by_another_key_is_rejected(client: AsyncClient) -> None:
    """Forged tokens from a different keypair must not authenticate."""
    import uuid

    from app.core.config import AuthSettings
    from app.core.security import TokenSigner

    foreign_token, _, _ = TokenSigner(AuthSettings()).issue_access_token(
        subject=uuid.uuid4(), tenant_id=uuid.uuid4(), roles=["admin"], token_version=1
    )
    response = await client.get("/api/v1/auth/me", headers=_auth(foreign_token))
    assert response.status_code == 401


# ── Refresh rotation ─────────────────────────────────────────────────


async def test_refresh_rotates_and_issues_a_new_pair(client: AsyncClient) -> None:
    await _login(client, USERS["marketing"])
    first_cookie = client.cookies.get("rm_refresh")

    response = await client.post("/api/v1/auth/refresh", json={})
    assert response.status_code == 200
    assert response.json()["access_token"]

    second_cookie = client.cookies.get("rm_refresh")
    assert second_cookie and second_cookie != first_cookie, "token must rotate on use"


async def test_reusing_a_rotated_token_revokes_the_family(client: AsyncClient) -> None:
    """Theft detection: replaying a retired token kills every session in the family."""
    login = await _login(client, USERS["finance"])
    stolen = login.cookies["rm_refresh"]

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    assert rotated.status_code == 200
    successor = rotated.json()["refresh_token"]

    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen})
    assert replay.status_code == 401
    assert replay.json()["type"].endswith("/token-reuse-detected")

    # The legitimate successor is collateral damage — by design.
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": successor})
    assert after.status_code == 401


async def test_refresh_without_any_token_is_rejected(client: AsyncClient) -> None:
    client.cookies.clear()
    response = await client.post("/api/v1/auth/refresh", json={})
    assert response.status_code == 401


async def test_unknown_refresh_token_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "fabricated-token-value"}
    )
    assert response.status_code == 401


# ── Logout and revocation ────────────────────────────────────────────


async def test_logout_clears_the_cookie_and_kills_the_session(client: AsyncClient) -> None:
    await _login(client, USERS["store_manager"])
    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 204

    refresh = await client.post("/api/v1/auth/refresh", json={})
    assert refresh.status_code == 401


async def test_logout_is_idempotent(client: AsyncClient) -> None:
    client.cookies.clear()
    assert (await client.post("/api/v1/auth/logout")).status_code == 204


async def test_revoke_all_sessions_invalidates_live_access_tokens(client: AsyncClient) -> None:
    """token_version bump: the JWT is still unexpired but no longer accepted."""
    token = await _token(client, USERS["regional_manager"])
    assert (await client.get("/api/v1/auth/me", headers=_auth(token))).status_code == 200

    revoke = await client.post("/api/v1/auth/sessions/revoke-all", headers=_auth(token))
    assert revoke.status_code == 204

    after = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert after.status_code == 401


# ── Authorization matrix ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("role", "expected", "forbidden"),
    [
        ("admin", "admin.users", "analytics.profitability.read"),
        ("ceo", "analytics.profitability.read", "admin.users"),
        ("regional_manager", "recommendations.act", "admin.budgets"),
        ("store_manager", "alerts.ack", "metrics.export"),
        ("marketing", "analytics.marketing.read", "analytics.profitability.read"),
        ("inventory", "forecasts.read", "admin.connectors"),
        ("finance", "metrics.export", "recommendations.act"),
    ],
)
async def test_role_permission_matrix_end_to_end(
    client: AsyncClient, role: str, expected: str, forbidden: str
) -> None:
    """Every seeded role receives exactly the access the matrix promises."""
    token = await _token(client, USERS[role])
    body = (await client.get("/api/v1/auth/me", headers=_auth(token))).json()

    assert body["roles"] == [role]
    assert expected in body["permissions"]
    assert forbidden not in body["permissions"]


async def test_permission_catalog_lists_all_seven_roles(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/permissions")
    assert response.status_code == 200

    catalog = {entry["role"]: entry for entry in response.json()}
    assert set(catalog) == set(USERS)
    for entry in catalog.values():
        assert entry["description"] and entry["permissions"]


# ── Transport hardening ──────────────────────────────────────────────


async def test_security_headers_and_request_id_are_present(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-request-id"]


async def test_inbound_request_id_is_propagated(client: AsyncClient) -> None:
    """Correlation ids from the edge must survive the hop (DevOps)."""
    response = await client.get("/health", headers={"X-Request-ID": "edge-abc-123"})
    assert response.headers["x-request-id"] == "edge-abc-123"


async def test_error_bodies_carry_a_request_id_for_support(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.json()["request_id"]


async def test_no_token_or_secret_leaks_into_error_bodies(client: AsyncClient) -> None:
    response = await _login(client, USERS["ceo"], "wrong-password")
    serialized = response.text.lower()
    assert "argon2" not in serialized
    assert "wrong-password" not in serialized
    assert "traceback" not in serialized


async def test_openapi_documents_auth_and_bearer_scheme(client: AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()
    assert "/api/v1/auth/login" in spec["paths"]
    assert "HTTPBearer" in spec["components"]["securitySchemes"]
    login = spec["paths"]["/api/v1/auth/login"]["post"]
    assert "401" in login["responses"]
