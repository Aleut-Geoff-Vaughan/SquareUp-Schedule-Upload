"""Tests for Square API reliability: idempotency stability, retry/backoff,
frozen credentials, and partial-failure handling."""

from unittest.mock import patch, MagicMock

from square_api import SquareAPI, RETRYABLE_STATUS


def _resp(status_code, json_body=None, headers=None):
    r = MagicMock()
    r.status_code = status_code
    r.ok = 200 <= status_code < 300
    r.json.return_value = json_body or {}
    r.text = str(json_body)
    r.headers = headers or {}
    return r


SHIFT = {
    "location_id": "L1", "job_id": "J1", "team_member_id": "T1",
    "employee_name": "Jane", "date": "2026-06-01",
    "start_time": "09:00", "end_time": "17:00", "timezone": "-04:00",
}


def test_idempotency_key_is_stable_across_calls(monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "tok")
    api = SquareAPI()
    k1 = api._generate_idempotency_key(SHIFT)
    k2 = api._generate_idempotency_key(dict(SHIFT))
    assert k1 == k2  # same content -> same key (retries dedupe)
    assert "SCHED_" in k1
    # Different content -> different key.
    other = dict(SHIFT, start_time="10:00")
    assert api._generate_idempotency_key(other) != k1


def test_retry_on_transient_5xx_then_success(monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "tok")
    api = SquareAPI()
    responses = [_resp(503), _resp(503),
                 _resp(200, {"scheduled_shift": {"id": "SB1"}})]
    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        i = calls["n"]
        calls["n"] += 1
        return responses[i]

    with patch("square_api.time.sleep"), patch("square_api.requests.post", side_effect=fake_post):
        result = api.create_shift(SHIFT)

    assert result["success"] is True
    assert result["shift_id"] == "SB1"
    assert calls["n"] == 3  # retried twice, succeeded on the third


def test_retry_gives_up_after_max_and_reports_error(monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "tok")
    api = SquareAPI()

    def fake_post(url, headers=None, json=None, timeout=None):
        return _resp(429, {"errors": [{"detail": "rate limited"}]}, headers={"Retry-After": "0"})

    with patch("square_api.time.sleep"), patch("square_api.requests.post", side_effect=fake_post):
        result = api.create_shift(SHIFT)

    assert result["success"] is False
    assert "rate limited" in result["error"]


def test_frozen_credentials_pin_environment(monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "sbx")
    monkeypatch.setenv("PRODUCTION_ACCESS_TOKEN", "prod")
    api = SquareAPI()

    with api.frozen_credentials():
        assert api.environment == "sandbox"
        assert "squareupsandbox" in api.base_url
        # Someone flips the global env mid-batch...
        monkeypatch.setenv("SQUARE_ENVIRONMENT", "production")
        api._update_token()  # no-op while frozen
        assert api.environment == "sandbox"
        assert "squareupsandbox" in api.base_url

    # After the batch, the flip takes effect again.
    api._update_token()
    assert api.environment == "production"


def test_all_retryable_statuses_defined():
    assert 429 in RETRYABLE_STATUS
    assert 503 in RETRYABLE_STATUS
    assert 400 not in RETRYABLE_STATUS
