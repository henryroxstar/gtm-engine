from fastapi.testclient import TestClient


def test_native_registration_disabled(monkeypatch):
    monkeypatch.setenv("ALLOW_NATIVE_REGISTRATION", "false")
    from backend.main import app

    client = TestClient(app)
    response = client.post(
        "/v1/auth/register",
        json={"email": "hacker@example.com", "password": "hackpassword", "display_name": "Hacker"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "native_registration_disabled"
