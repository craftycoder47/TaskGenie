import os
import tempfile

import pytest


def make_app(tmp_path):
    os.environ["DATA_DIR"] = str(tmp_path)
    import web_app
    web_app.DATA_DIR = str(tmp_path)
    web_app.DB_PATH = os.path.join(str(tmp_path), "customer_leads.db")
    web_app._rate_limit.clear()
    web_app.init_db()
    web_app.app.config.update(TESTING=True)
    return web_app.app, web_app


@pytest.fixture
def client(tmp_path):
    app, _ = make_app(tmp_path)
    return app.test_client()


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


def test_health_and_home(client):
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/privacy").status_code == 200


def test_valid_form_is_stored(client, tmp_path):
    response = client.post("/api/enquiries", data=payload())
    assert response.status_code == 200
    assert response.get_json()["ok"] is True

    db = os.path.join(str(tmp_path), "customer_leads.db")
    import sqlite3
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT email, consent FROM leads").fetchone()
    assert row == ("owner@example.com", 1)


def test_invalid_email_rejected(client):
    data = payload()
    data["email"] = "not-an-email"
    response = client.post("/api/enquiries", data=data)
    assert response.status_code == 400
    assert "valid email" in response.get_json()["error"]


def test_consent_required(client):
    data = payload()
    data.pop("consent")
    response = client.post("/api/enquiries", data=data)
    assert response.status_code == 400


def test_json_request_supported(client):
    response = client.post("/api/enquiries", json=payload())
    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_rate_limit_after_five_requests(client):
    for _ in range(5):
        assert client.post("/api/enquiries", data=payload()).status_code == 200
    assert client.post("/api/enquiries", data=payload()).status_code == 429
