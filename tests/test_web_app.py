import os
import sqlite3

import pytest


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import web_app
    web_app.DATA_DIR = str(tmp_path)
    web_app.DB_PATH = os.path.join(str(tmp_path), "customer_leads.db")
    web_app._rate_limit.clear()
    web_app.init_db()
    web_app.app.config.update(TESTING=True)
    return web_app.app.test_client(), web_app


def payload():
    return {
        "name": "Test Business",
        "email": "owner@example.com",
        "business_type": "Cleaner",
        "location": "Luton",
        "services": "Home cleaning",
        "goal": "Get more enquiries",
        "consent": "on",
    }


def test_health_home_and_privacy(app_client):
    client, _ = app_client
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/privacy").status_code == 200


def test_valid_form_is_stored(app_client):
    client, web_app = app_client
    response = client.post("/api/enquiries", data=payload())
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    with sqlite3.connect(web_app.DB_PATH) as conn:
        row = conn.execute("SELECT email, consent FROM leads").fetchone()
    assert row == ("owner@example.com", 1)


def test_invalid_email_rejected(app_client):
    client, _ = app_client
    data = payload()
    data["email"] = "not-an-email"
    response = client.post("/api/enquiries", data=data)
    assert response.status_code == 400
    assert "valid email" in response.get_json()["error"]


def test_consent_required(app_client):
    client, _ = app_client
    data = payload()
    data.pop("consent")
    assert client.post("/api/enquiries", data=data).status_code == 400


def test_json_request_supported(app_client):
    client, _ = app_client
    assert client.post("/api/enquiries", json=payload()).status_code == 200


def test_rate_limit_after_five_requests(app_client):
    client, _ = app_client
    for _ in range(5):
        assert client.post("/api/enquiries", data=payload()).status_code == 200
    assert client.post("/api/enquiries", data=payload()).status_code == 429
