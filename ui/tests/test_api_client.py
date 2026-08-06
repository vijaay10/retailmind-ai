"""The client's two load-bearing behaviours: bounded refresh, and honest errors."""

import httpx
import pytest

from retailmind_ui.api import ApiClient, ApiError, Tokens, _clean


def transport(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api")


def build(handler) -> ApiClient:  # type: ignore[no-untyped-def]
    client = ApiClient(base_url="http://api", tokens=Tokens(access="old", refresh="r1"))
    client._client = transport(handler)
    return client


# ── Refresh is bounded ───────────────────────────────────────────────


def test_an_expired_token_is_refreshed_and_the_call_retried() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/auth/refresh":
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2"})
        if request.headers.get("Authorization") == "Bearer old":
            return httpx.Response(401, json={"title": "expired"})
        return httpx.Response(200, json={"ok": True})

    client = build(handler)
    assert client.get("/api/v1/sales")["ok"] is True
    assert calls == ["/api/v1/sales", "/api/v1/auth/refresh", "/api/v1/sales"]
    assert client.tokens is not None
    assert client.tokens.access == "new"


def test_a_refresh_that_also_fails_does_not_loop() -> None:
    """The failure mode this bound exists for.

    An unbounded retry turns one expired session into a sustained hammering of
    the auth endpoint — a self-inflicted denial of service, and worst exactly
    when auth is already unhealthy.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(401, json={"title": "expired"})

    with pytest.raises(ApiError) as raised:
        build(handler).get("/api/v1/sales")

    assert raised.value.status == 401
    assert calls.count("/api/v1/auth/refresh") == 1


def test_a_session_with_no_refresh_token_fails_immediately() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(401, json={})

    client = ApiClient(base_url="http://api", tokens=Tokens(access="a", refresh=""))
    client._client = transport(handler)

    with pytest.raises(ApiError):
        client.get("/api/v1/sales")
    assert "/api/v1/auth/refresh" not in calls


def test_a_download_refreshes_once_too() -> None:
    """Exports go through a separate path, and it drifted easily."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/v1/auth/refresh":
            return httpx.Response(200, json={"access_token": "new"})
        if request.headers.get("Authorization") == "Bearer old":
            return httpx.Response(401, json={})
        return httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})

    payload, content_type = build(handler).download("/api/v1/reports/export", format="pdf")
    assert payload.startswith(b"%PDF")
    assert content_type == "application/pdf"


# ── Errors keep the API's own explanation ────────────────────────────


def test_a_problem_detail_survives_to_the_message() -> None:
    """This API's hints usually name the fix; "Request failed" throws that away."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "title": "Unknown dimension",
                "detail": "No dimension named 'channel'.",
                "hint": "Ask /nlq/catalogue for the vocabulary available.",
            },
        )

    with pytest.raises(ApiError) as raised:
        build(handler).get("/api/v1/nlq/ask")

    message = str(raised.value)
    assert "No dimension named 'channel'." in message
    assert "catalogue" in message


def test_a_non_json_error_body_is_not_swallowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream gateway exploded")

    with pytest.raises(ApiError) as raised:
        build(handler).get("/api/v1/sales")
    assert "gateway" in str(raised.value)


def test_a_forbidden_response_reads_as_permissions_not_breakage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    with pytest.raises(ApiError) as raised:
        build(handler).get("/api/v1/reports")

    assert raised.value.is_auth
    assert "role" in str(raised.value)


def test_an_unreachable_backend_says_so() -> None:
    """Otherwise the console reports a data problem for a stopped server."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ApiError) as raised:
        build(handler).get("/api/v1/sales")

    assert raised.value.status == 0
    assert "backend running" in str(raised.value)


# ── Small guarantees ─────────────────────────────────────────────────


def test_unset_parameters_are_dropped_rather_than_sent_as_null() -> None:
    """Sending ``None`` overrides a server-side default with nothing, and for a
    date range that silently widens the query to all of history."""
    assert _clean({"limit": 10, "region": None, "since": ""}) == {"limit": 10, "since": ""}


def test_a_list_response_is_wrapped_so_callers_see_one_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"a": 1}])

    assert build(handler).get("/api/v1/x") == {"data": [{"a": 1}]}


def test_logout_drops_local_tokens_even_when_the_server_refuses() -> None:
    """A failed logout must not trap someone in a signed-in state."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={})

    client = build(handler)
    client.logout()
    assert client.tokens is None


def test_login_stores_the_pair_and_never_the_password() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r"})

    client = ApiClient(base_url="http://api")
    client._client = transport(handler)
    tokens = client.login("priya@northwind.example", "hunter2")

    assert tokens.access == "a"
    assert "hunter2" not in repr(client)
