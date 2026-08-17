"""HTTP client for the RetailMind API.

**The console renders what the API returns; it computes nothing.** Every
number in this product arrives already qualified — an evidence tier, a
confidence ceiling, a note about what was not checked — and a client that
recomputed a total or re-derived a rate would eventually disagree with the
screen it is drawing. There is no arithmetic in this package beyond formatting.

Two behaviours worth knowing about.

**Access tokens refresh once, silently.** A 401 triggers one refresh attempt
and one retry. Retrying indefinitely turns an expired session into a loop
against the auth endpoint; not retrying at all logs a user out mid-click for a
token that expired thirty seconds ago.

**Errors surface as problem details, not stack traces.** The API speaks
RFC 7807, so a validation failure carries a human-readable `detail` and often
a `hint`. Discarding those in favour of "Request failed" throws away the part
that tells the user what to do — and this API's hints are frequently the whole
answer ("Ask /nlq/catalogue for the vocabulary available").
"""

import contextlib
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8090"

#: Generous by API standards. A root-cause investigation sweeps nine
#: dimensions and a report renders three documents; both legitimately take
#: seconds, and a client that gives up at five turns a slow answer into a
#: broken one.
TIMEOUT = httpx.Timeout(60.0, connect=10.0)


@dataclass(frozen=True, slots=True)
class ApiError(Exception):
    """A failed call, in the terms the API described it.

    Carries the problem-detail fields rather than an HTTP status alone,
    because this API's hints usually name the fix.
    """

    status: int
    title: str
    detail: str = ""
    hint: str = ""

    def __str__(self) -> str:
        parts = [self.detail or self.title]
        if self.hint:
            parts.append(self.hint)
        return " — ".join(parts)

    @property
    def is_auth(self) -> bool:
        return self.status in {401, 403}

    @property
    def is_dependency_unavailable(self) -> bool:
        """503 — a real backend dependency (today: only the warehouse) isn't
        reachable. For a brand-new tenant this almost always means "not
        provisioned yet", not "something broke" — the two read very
        differently to a first-time user and should not share the same red
        "did not load" panel. See `components.workspace_error`."""
        return self.status == 503


@dataclass(frozen=True, slots=True)
class Tokens:
    access: str
    refresh: str = ""


@dataclass
class ApiClient:
    """Calls the API on behalf of one signed-in user."""

    base_url: str = DEFAULT_BASE_URL
    tokens: Tokens | None = None
    _client: httpx.Client | None = field(default=None, repr=False, compare=False)

    # ── Lifecycle ────────────────────────────────────────────────────

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self.base_url, timeout=TIMEOUT)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── Authentication ───────────────────────────────────────────────

    def login(self, email: str, password: str) -> Tokens:
        """Exchange credentials for a token pair.

        The password is never stored, logged, or held in session state — it
        exists for the duration of this call and nowhere else.
        """
        payload = self._request(
            "POST", "/api/v1/auth/login", json={"email": email, "password": password}, auth=False
        )
        self.tokens = Tokens(
            access=str(payload["access_token"]), refresh=str(payload.get("refresh_token", ""))
        )
        return self.tokens

    def refresh(self) -> bool:
        """Rotate the access token. Returns whether it worked."""
        if not self.tokens or not self.tokens.refresh:
            return False
        try:
            payload = self._request(
                "POST",
                "/api/v1/auth/refresh",
                json={"refresh_token": self.tokens.refresh},
                auth=False,
            )
        except ApiError:
            return False

        self.tokens = Tokens(
            access=str(payload["access_token"]),
            refresh=str(payload.get("refresh_token", self.tokens.refresh)),
        )
        return True

    def logout(self) -> None:
        if self.tokens and self.tokens.refresh:
            # A failed logout must not trap someone in a signed-in state. The
            # local tokens are dropped either way, and the server-side session
            # expires on its own.
            with contextlib.suppress(ApiError):
                self._request(
                    "POST", "/api/v1/auth/logout", json={"refresh_token": self.tokens.refresh}
                )
        self.tokens = None

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/auth/me")

    # ── Verbs ────────────────────────────────────────────────────────

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request("GET", path, params=_clean(params))

    def post(self, path: str, **json: Any) -> dict[str, Any]:
        return self._request("POST", path, json=json)

    def patch(self, path: str, **json: Any) -> dict[str, Any]:
        return self._request("PATCH", path, json=_clean(json))

    def download(self, path: str, **params: Any) -> tuple[bytes, str]:
        """Fetch a binary export, returning its bytes and content type."""
        response = self._send("GET", path, params=_clean(params))
        if response.status_code == 401 and self.refresh():
            response = self._send("GET", path, params=_clean(params))
        if response.status_code >= 400:
            raise _problem(response)
        return response.content, response.headers.get("content-type", "application/octet-stream")

    # ── Internals ────────────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> dict[str, Any]:
        response = self._send(method, path, params=params, json=json, auth=auth)

        # One refresh, one retry. A loop here turns an expired session into a
        # denial-of-service against our own auth endpoint.
        if response.status_code == 401 and auth and self.refresh():
            response = self._send(method, path, params=params, json=json, auth=auth)

        if response.status_code >= 400:
            raise _problem(response)

        if not response.content:
            return {}
        body = response.json()
        return body if isinstance(body, dict) else {"data": body}

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        auth: bool = True,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if auth and self.tokens:
            headers["Authorization"] = f"Bearer {self.tokens.access}"
        try:
            return self._http().request(method, path, params=params, json=json, headers=headers)
        except httpx.RequestError as error:
            raise ApiError(
                status=0,
                title="Cannot reach the API",
                detail=str(error),
                hint=f"Is the backend running at {self.base_url}?",
            ) from error


def _problem(response: httpx.Response) -> ApiError:
    """Turn a response into an error carrying the API's own explanation."""
    title, detail, hint = "Request failed", "", ""
    try:
        body = response.json()
        if isinstance(body, dict):
            title = str(body.get("title") or title)
            detail = str(body.get("detail") or "")
            hint = str(body.get("hint") or "")
            if not detail and "detail" in body:
                detail = str(body["detail"])
    except ValueError:
        detail = response.text[:400]

    if response.status_code == 401:
        title, hint = "Session expired", hint or "Sign in again to continue."
    elif response.status_code == 403:
        title = "Not permitted"
        hint = hint or "Your role does not include this area."

    return ApiError(status=response.status_code, title=title, detail=detail, hint=hint)


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop unset parameters so the API applies its own defaults.

    Sending ``None`` would override a default with a null and, for a date
    range, silently widen a query to everything.
    """
    return {key: value for key, value in params.items() if value is not None}
