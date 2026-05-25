"""Regression tests for SquareAPI HTTP status handling."""

from unittest.mock import patch, MagicMock


def _make_response(status_code, json_body):
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = 200 <= status_code < 300
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


def test_create_shift_accepts_200(logged_in, monkeypatch):
    """Square returns 200 for created scheduled shifts in newer Labor API
    versions. The app must treat any 2xx as success, not just 201."""
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "fake-token")
    app_module = logged_in.application_module
    api = app_module.square

    success_body = {
        "scheduled_shift": {
            "id": "SB23FHB5DM5QP",
            "draft_shift_details": {
                "team_member_id": "TM_1",
                "location_id": "L_1",
                "job_id": "J_1",
                "start_at": "2026-05-26T09:00:00-04:00",
                "end_at": "2026-05-26T10:00:00-04:00",
            },
            "version": 1,
        }
    }
    with patch("square_api.requests.post", return_value=_make_response(200, success_body)):
        result = api.create_shift({
            "location_id": "L_1",
            "job_id": "J_1",
            "team_member_id": "TM_1",
            "employee_name": "Jane",
            "date": "2026-05-26",
            "start_time": "09:00",
            "end_time": "10:00",
            "timezone": "-04:00",
        })

    assert result["success"] is True
    assert result["shift_id"] == "SB23FHB5DM5QP"


def test_create_shift_reports_error_on_4xx(logged_in, monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "fake-token")
    api = logged_in.application_module.square

    err_body = {"errors": [{"detail": "Bad job_id"}]}
    with patch("square_api.requests.post", return_value=_make_response(400, err_body)):
        result = api.create_shift({
            "location_id": "L_1",
            "job_id": "BAD",
            "team_member_id": "TM_1",
            "employee_name": "Jane",
            "date": "2026-05-26",
            "start_time": "09:00",
            "end_time": "10:00",
            "timezone": "-04:00",
        })

    assert result["success"] is False
    assert "Bad job_id" in result["error"]


def test_publish_shift_accepts_2xx(logged_in, monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "fake-token")
    api = logged_in.application_module.square

    with patch("square_api.requests.post", return_value=_make_response(204, {})):
        result = api.publish_shift("SB23FHB5DM5QP")

    assert result["success"] is True


def test_create_shift_omits_team_member_id_when_none(logged_in, monkeypatch):
    """Square production rejects an explicit team_member_id=null with
    "Field must not be blank". The request body must omit the key entirely
    for open shifts."""
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "fake-token")
    api = logged_in.application_module.square

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured['body'] = json
        return _make_response(200, {"scheduled_shift": {"id": "SB1", "draft_shift_details": {}}})

    with patch("square_api.requests.post", side_effect=fake_post):
        api.create_shift({
            "location_id": "L_1",
            "job_id": "J_1",
            "team_member_id": None,
            "employee_name": "",
            "date": "2026-05-26",
            "start_time": "09:00",
            "end_time": "10:00",
            "timezone": "-04:00",
        })

    draft = captured['body']['scheduled_shift']['draft_shift_details']
    assert 'team_member_id' not in draft, \
        "team_member_id key must be omitted when None; Square rejects null with 'Field must not be blank'"


def test_create_shift_includes_team_member_id_when_present(logged_in, monkeypatch):
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "fake-token")
    api = logged_in.application_module.square

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured['body'] = json
        return _make_response(200, {"scheduled_shift": {"id": "SB1", "draft_shift_details": {}}})

    with patch("square_api.requests.post", side_effect=fake_post):
        api.create_shift({
            "location_id": "L_1",
            "job_id": "J_1",
            "team_member_id": "TM_42",
            "employee_name": "Jane",
            "date": "2026-05-26",
            "start_time": "09:00",
            "end_time": "10:00",
            "timezone": "-04:00",
        })

    draft = captured['body']['scheduled_shift']['draft_shift_details']
    assert draft['team_member_id'] == "TM_42"


def test_error_message_includes_field_name(logged_in, monkeypatch):
    """When Square says "Field must not be blank", the error should also tell
    us WHICH field — the user can't troubleshoot without that."""
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "fake-token")
    api = logged_in.application_module.square

    err_body = {"errors": [{
        "category": "INVALID_REQUEST_ERROR",
        "code": "VALUE_NOT_BLANK_VIOLATION",
        "detail": "Field must not be blank.",
        "field": "scheduled_shift.draft_shift_details.team_member_id",
    }]}
    with patch("square_api.requests.post", return_value=_make_response(400, err_body)):
        result = api.create_shift({
            "location_id": "L_1",
            "job_id": "J_1",
            "team_member_id": "TM_1",
            "employee_name": "Jane",
            "date": "2026-05-26",
            "start_time": "09:00",
            "end_time": "10:00",
            "timezone": "-04:00",
        })

    assert result["success"] is False
    assert "Field must not be blank" in result["error"]
    assert "team_member_id" in result["error"]
    assert "VALUE_NOT_BLANK_VIOLATION" in result["error"]


def test_search_scheduled_shifts_uses_max_50_limit(logged_in, monkeypatch):
    """Square's scheduled-shifts search caps `limit` at 50. Anything higher
    is rejected with VALUE_TOO_HIGH."""
    monkeypatch.setenv("SQUARE_ENVIRONMENT", "sandbox")
    monkeypatch.setenv("SANDBOX_ACCESS_TOKEN", "fake-token")
    api = logged_in.application_module.square

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured.setdefault('bodies', []).append(json)
        return _make_response(200, {"scheduled_shifts": []})

    with patch("square_api.requests.post", side_effect=fake_post):
        api.search_scheduled_shifts(start_at="2026-05-25T00:00:00Z", end_at="2026-05-30T23:59:59Z")

    assert captured['bodies'], "expected at least one request"
    assert captured['bodies'][0]['limit'] == 50
