from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_direct_requests_cannot_forge_authorization():
    response = TestClient(app).get("/api/me", headers={"Authorization": "Bearer fake"})
    assert response.status_code == 401
