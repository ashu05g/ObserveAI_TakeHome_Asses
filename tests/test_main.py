import pytest
from fastapi.testclient import TestClient

from api.main import app


def test_health_endpoint():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_lifespan_fails_when_required_env_missing(monkeypatch):
    monkeypatch.delenv("AIRTABLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="AIRTABLE_API_KEY"), TestClient(app):
        pass


def test_routes_registered():
    paths = {route.path for route in app.routes}
    assert "/lookup" in paths
    assert "/webhook" in paths
    assert "/health" in paths
