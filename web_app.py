"""Small TaskGenie web front door for customer enquiries.

This is intentionally separate from the Telegram adapter. It captures only the
minimum business details needed for an initial Growth Pack enquiry and stores
leads in a local SQLite database for the prototype.
"""

import os
import sqlite3
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

app = Flask(__name__, template_folder="web/templates")
DATA_DIR = os.environ.get("DATA_DIR", ".")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "customer_leads.db")


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with get_db() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                business_type TEXT NOT NULL,
                location TEXT NOT NULL,
                services TEXT NOT NULL,
                goal TEXT NOT NULL,
                consent INTEGER NOT NULL DEFAULT 0
            )"""
        )


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/api/enquiries")
def create_enquiry():
    data = request.form if request.form else (request.get_json(silent=True) or {})
    required = ["name", "email", "business_type", "location", "services", "goal"]
    missing = [field for field in required if not str(data.get(field, "")).strip()]
    if missing:
        return jsonify({"ok": False, "error": "Please complete all required fields."}), 400

    email = str(data["email"]).strip()
    if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
        return jsonify({"ok": False, "error": "Please enter a valid email address."}), 400

    if str(data.get("consent", "")).lower() not in {"1", "true", "on", "yes"}:
        return jsonify({"ok": False, "error": "Please confirm that we can contact you about your enquiry."}), 400

    with get_db() as db:
        db.execute(
            """INSERT INTO leads
            (created_at, name, email, business_type, location, services, goal, consent)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                datetime.now(timezone.utc).isoformat(),
                str(data["name"]).strip(),
                email,
                str(data["business_type"]).strip(),
                str(data["location"]).strip(),
                str(data["services"]).strip(),
                str(data["goal"]).strip(),
            ),
        )

    return jsonify({"ok": True, "message": "Thanks — your TaskGenie enquiry has been received."})


@app.get("/health")
def health():
    return jsonify({"ok": True})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
