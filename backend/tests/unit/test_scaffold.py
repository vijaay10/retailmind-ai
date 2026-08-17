"""Scaffold smoke tests: the app factory boots and serves liveness.

These exist so `make test` and CI are green from commit one; they are replaced
by real suites as modules land (Backend design–37).
"""

from fastapi.testclient import TestClient

from app.main import create_app


def test_app_factory_boots() -> None:
    app = create_app()
    assert app.title == "RetailMind AI"


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
