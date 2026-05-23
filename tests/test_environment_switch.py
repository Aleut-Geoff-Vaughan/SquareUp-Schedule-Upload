def test_health_defaults_to_sandbox(logged_in, monkeypatch):
    for var in ("SQUARE_ENVIRONMENT", "SANDBOX_ACCESS_TOKEN", "PRODUCTION_ACCESS_TOKEN",
                "SANDBOX_APPLICATION_ID", "PRODUCTION_APPLICATION_ID", "SQUARE_ACCESS_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    resp = logged_in.get("/api/health")
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["environment"] == "sandbox"
    assert body["token_configured"] is False


def test_health_reports_sandbox_token_configured(logged_in, monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "sbx-token-xxx")
    monkeypatch.setenv("SANDBOX_APPLICATION_ID", "sbx-app-xxx")

    body = logged_in.get("/api/health").get_json()
    assert body["environment"] == "sandbox"
    assert body["token_configured"] is True
    assert body["application_id_configured"] is True


def test_admin_can_switch_to_production(logged_in, monkeypatch):
    monkeypatch.setenv("PRODUCTION_ACCESS_TOKEN", "prod-token-yyy")

    resp = logged_in.post("/api/settings/environment", json={"environment": "production"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["environment"] == "production"
    assert body["token_configured"] is True

    # Verify persistence: another GET should still report production.
    follow = logged_in.get("/api/health").get_json()
    assert follow["environment"] == "production"


def test_switch_rejects_invalid_environment(logged_in):
    resp = logged_in.post("/api/settings/environment", json={"environment": "staging"})
    assert resp.status_code == 400


def test_switch_requires_admin(client):
    app_module = client.application_module
    app_module.db.add_user("regular", app_module.hash_password("pw"), is_admin=False)
    client.post("/login", data={"username": "regular", "password": "pw"})

    resp = client.post("/api/settings/environment", json={"environment": "production"})
    assert resp.status_code == 403


def test_square_api_uses_sandbox_host(monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "sbx")
    from square_api import SquareAPI
    api = SquareAPI()
    assert "squareupsandbox.com" in api.base_url
    assert api.access_token == "sbx"


def test_square_api_uses_production_host(monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
    monkeypatch.setenv("PRODUCTION_ACCESS_TOKEN", "prod")
    from square_api import SquareAPI
    api = SquareAPI()
    assert "squareup.com" in api.base_url
    assert "sandbox" not in api.base_url
    assert api.access_token == "prod"


def test_persisted_environment_is_loaded_on_startup(logged_in, monkeypatch):
    """After an admin sets production, restarting the app should remember it."""
    app_module = logged_in.application_module
    app_module.db.set_setting("square_environment", "production")
    monkeypatch.setenv("PRODUCTION_ACCESS_TOKEN", "prod")

    app_module._apply_persisted_environment()

    from square_api import resolve_credentials
    assert resolve_credentials()["environment"] == "production"
