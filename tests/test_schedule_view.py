"""Tests for the live schedule view that pulls scheduled shifts from Square."""

from unittest.mock import patch


def _seed(app_module):
    app_module.db.add_location("Main Street", "L_MAIN", timezone="-04:00")
    app_module.db.add_job("Barista", "J_BAR")
    app_module.db.add_team_member("Jane Doe", "T_JANE")


def _sample_shift(shift_id, loc="L_MAIN", job="J_BAR", member="T_JANE",
                  start="2026-05-26T09:00:00-04:00", end="2026-05-26T17:00:00-04:00"):
    return {
        "id": shift_id,
        "version": 1,
        "draft_shift_details": {
            "location_id": loc,
            "job_id": job,
            "team_member_id": member,
            "start_at": start,
            "end_at": end,
            "timezone": "America/New_York",
        },
    }


def test_schedule_fetch_marks_app_vs_external(logged_in, monkeypatch):
    _seed(logged_in.application_module)
    app_module = logged_in.application_module

    # Record one shift in the local schedules table — this should be marked "App".
    app_module.db.add_schedule(
        upload_id=None,
        square_shift_id="SHIFT_APP_1",
        location_id="L_MAIN", job_id="J_BAR", team_member_id="T_JANE",
        shift_date="2026-05-26", start_time="09:00", end_time="17:00",
    )

    fake_response = {
        "success": True,
        "scheduled_shifts": [
            _sample_shift("SHIFT_APP_1"),
            _sample_shift("SHIFT_EXTERNAL_1", start="2026-05-27T10:00:00-04:00",
                          end="2026-05-27T18:00:00-04:00"),
        ],
    }

    with patch.object(app_module.square, "search_scheduled_shifts",
                      return_value=fake_response) as mock_search:
        resp = logged_in.post("/api/schedule/fetch", json={
            "start_date": "2026-05-25",
            "end_date": "2026-05-30",
        })

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["count"] == 2
    assert data["app_count"] == 1
    assert data["external_count"] == 1

    sources = {s["square_shift_id"]: s["source"] for s in data["shifts"]}
    assert sources["SHIFT_APP_1"] == "App"
    assert sources["SHIFT_EXTERNAL_1"] == "External"

    # Names resolved from local lookups
    for s in data["shifts"]:
        assert s["location_name"] == "Main Street"
        assert s["job_title"] == "Barista"
        assert s["team_member_name"] == "Jane Doe"

    # Ensure location filter passed through when supplied
    mock_search.assert_called_once()
    kwargs = mock_search.call_args.kwargs
    assert kwargs.get("location_ids") is None


def test_schedule_fetch_passes_location_filter(logged_in):
    _seed(logged_in.application_module)
    app_module = logged_in.application_module

    captured = {}

    def fake_search(location_ids=None, start_at=None, end_at=None):
        captured["location_ids"] = location_ids
        captured["start_at"] = start_at
        captured["end_at"] = end_at
        return {"success": True, "scheduled_shifts": []}

    with patch.object(app_module.square, "search_scheduled_shifts", side_effect=fake_search):
        resp = logged_in.post("/api/schedule/fetch", json={
            "start_date": "2026-05-25",
            "end_date": "2026-05-30",
            "location_id": "L_MAIN",
        })
    assert resp.status_code == 200
    assert captured["location_ids"] == ["L_MAIN"]
    assert captured["start_at"].startswith("2026-05-25")
    assert captured["end_at"].startswith("2026-05-30")


def test_schedule_fetch_requires_dates(logged_in):
    resp = logged_in.post("/api/schedule/fetch", json={})
    assert resp.status_code == 400
    assert b"start_date" in resp.data


def test_schedule_fetch_surfaces_square_error(logged_in):
    app_module = logged_in.application_module
    with patch.object(app_module.square, "search_scheduled_shifts",
                      return_value={"success": False, "error": "Bad token"}):
        resp = logged_in.post("/api/schedule/fetch", json={
            "start_date": "2026-05-25",
            "end_date": "2026-05-30",
        })
    assert resp.status_code == 502
    assert b"Bad token" in resp.data


def test_schedule_view_renders(logged_in):
    _seed(logged_in.application_module)
    resp = logged_in.get("/schedule")
    assert resp.status_code == 200
    assert b"Current Schedule" in resp.data
    assert b"Main Street" in resp.data
