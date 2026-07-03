"""Tests for password hashing, legacy-hash migration, CSRF, and the
SECRET_KEY startup guard."""

import hashlib
import os
import subprocess
import sys

import security


def test_hash_is_salted_and_not_plain_sha256():
    h1 = security.hash_password("hunter2")
    h2 = security.hash_password("hunter2")
    # Salted: same password hashes differently each time.
    assert h1 != h2
    # Not a bare sha256 digest.
    assert h1 != hashlib.sha256(b"hunter2").hexdigest()
    assert security.verify_password("hunter2", h1)
    assert not security.verify_password("wrong", h1)


def test_legacy_sha256_hash_verifies_and_is_flagged_for_rehash():
    legacy = hashlib.sha256(b"admin123").hexdigest()
    assert security.verify_password("admin123", legacy)
    assert not security.verify_password("nope", legacy)
    assert security.needs_rehash(legacy) is True
    assert security.needs_rehash(security.hash_password("admin123")) is False


def test_login_upgrades_legacy_hash(client):
    """A user stored with the old unsalted SHA-256 scheme logs in and gets
    their hash transparently upgraded."""
    app_module = client.application_module
    legacy = hashlib.sha256(b"oldpass1").hexdigest()
    uid = app_module.db.add_user("legacyuser", legacy, is_admin=False)

    resp = client.post("/login", data={"username": "legacyuser", "password": "oldpass1"})
    assert resp.status_code == 302  # success -> redirect

    upgraded = app_module.db.get_user_by_id(uid)["password_hash"]
    assert upgraded != legacy
    assert security.needs_rehash(upgraded) is False
    assert security.verify_password("oldpass1", upgraded)


def test_csrf_blocks_unsafe_post_without_token(logged_in):
    app_module = logged_in.application_module
    app_module.app.testing = False
    app_module.app.config["CSRF_ENABLED"] = True
    try:
        # Render a page so the session gets a CSRF token.
        logged_in.get("/account")
        with logged_in.session_transaction() as sess:
            token = sess.get("_csrf_token")
        assert token

        # Without the token header -> rejected.
        blocked = logged_in.post("/api/settings/environment", json={"environment": "sandbox"})
        assert blocked.status_code == 400
        assert "CSRF" in blocked.get_json()["error"]

        # With the token header -> allowed through.
        ok = logged_in.post(
            "/api/settings/environment",
            json={"environment": "sandbox"},
            headers={"X-CSRFToken": token},
        )
        assert ok.status_code == 200
    finally:
        app_module.app.testing = True
        app_module.app.config["CSRF_ENABLED"] = False


def test_secret_key_guard_rejects_placeholder():
    """Importing the app with a banned/short SECRET_KEY must fail fast."""
    env = dict(os.environ)
    env["SECRET_KEY"] = "change-this-secret-key-in-production"  # the shipped placeholder
    env.pop("PYTHONPATH", None)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, "-c", "import app"],
        env={**env, "PYTHONPATH": repo},
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert proc.returncode != 0
    assert "SECRET_KEY" in proc.stderr


def test_secret_key_guard_rejects_short_key():
    env = dict(os.environ)
    env["SECRET_KEY"] = "tooshort"
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run(
        [sys.executable, "-c", "import app"],
        env={**env, "PYTHONPATH": repo},
        capture_output=True,
        text=True,
        cwd=repo,
    )
    assert proc.returncode != 0
