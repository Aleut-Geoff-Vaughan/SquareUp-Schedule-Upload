import os
import sys
import tempfile

import pytest


@pytest.fixture
def client(monkeypatch):
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    monkeypatch.setenv("DB_PATH", tmp_db.name)
    monkeypatch.setenv("SECRET_KEY", "test-secret")

    # Force reimport so module-level Database() picks up our DB_PATH
    for mod in ("app", "database", "square_api"):
        sys.modules.pop(mod, None)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import app as app_module

    app_module.app.config["TESTING"] = True

    # Seed an admin so login works without re-running __main__
    admin_hash = app_module.hash_password("admin123")
    app_module.db.add_user("admin", admin_hash, is_admin=True)

    with app_module.app.test_client() as client:
        client.application_module = app_module
        yield client

    try:
        os.unlink(tmp_db.name)
    except OSError:
        pass


@pytest.fixture
def logged_in(client):
    resp = client.post("/login", data={"username": "admin", "password": "admin123"})
    assert resp.status_code == 302
    return client
