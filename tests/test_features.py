"""Tests for the new automation features and correctness fixes:
team-member direct sync, retry-failed, report download, password change/reset,
DST timezones, schedule-window widening, and IntegrityError handling."""

import io

import timezones


SAMPLE_CSV = (
    "employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset\n"
    "Jane Doe,Barista,Main Street,2026-06-01,09:00,17:00,-04:00\n"
    "John Smith,Manager,Main Street,2026-06-01,08:00,16:00,-04:00\n"
    ",Barista,Main Street,2026-06-02,06:00,10:00,-04:00\n"
)


def _seed(app_module):
    app_module.db.add_location("Main Street", "L_MAIN", timezone_name="America/New_York")
    app_module.db.add_job("Barista", "J_BAR")
    app_module.db.add_job("Manager", "J_MGR")
    app_module.db.add_team_member("Jane Doe", "T_JANE")
    app_module.db.add_team_member("John Smith", "T_JOHN")


def _upload(client):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(SAMPLE_CSV.encode()), "s.csv")},
        content_type="multipart/form-data",
    )


# ---------------------------------------------------------------------------
# Timezones (DST-aware)
# ---------------------------------------------------------------------------
def test_resolve_offset_dst_aware():
    loc = {"timezone_name": "America/New_York", "timezone": "-05:00"}
    summer, w1 = timezones.resolve_offset("2026-07-01", None, loc)
    winter, w2 = timezones.resolve_offset("2026-01-15", None, loc)
    assert summer == "-04:00"  # EDT
    assert winter == "-05:00"  # EST
    assert w1 is None and w2 is None


def test_resolve_offset_explicit_override_wins():
    loc = {"timezone_name": "America/New_York"}
    offset, warning = timezones.resolve_offset("2026-07-01", "-08:00", loc)
    assert offset == "-08:00"
    assert warning is None


def test_resolve_offset_falls_back_with_warning():
    offset, warning = timezones.resolve_offset("2026-07-01", None, None)
    assert offset == "-04:00"
    assert warning is not None


# ---------------------------------------------------------------------------
# Team-member direct sync
# ---------------------------------------------------------------------------
def test_team_member_sync_from_square(logged_in, monkeypatch):
    app_module = logged_in.application_module
    monkeypatch.setattr(app_module.square, "list_team_members", lambda status=None: {
        "success": True,
        "team_members": [
            {"id": "TM1", "given_name": "Ada", "family_name": "Lovelace"},
            {"id": "TM2", "given_name": "Alan", "family_name": "Turing"},
            {"id": "TM3", "given_name": "", "family_name": ""},  # skipped (no name)
        ],
    })
    resp = logged_in.post("/settings/team-members/sync")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["imported"] == 2
    assert body["skipped"] == 1
    assert app_module.db.get_team_member_by_name("Ada Lovelace")["square_team_member_id"] == "TM1"


def test_team_member_sync_requires_admin(client):
    app_module = client.application_module
    app_module.db.add_user("reg", app_module.hash_password("password1"), is_admin=False)
    client.post("/login", data={"username": "reg", "password": "password1"})
    assert client.post("/settings/team-members/sync").status_code == 403


# ---------------------------------------------------------------------------
# Partial failure -> per-row results, report, retry
# ---------------------------------------------------------------------------
def _process_with_one_failure(logged_in, monkeypatch):
    app_module = logged_in.application_module
    _seed(app_module)
    calls = {"n": 0}

    def fake(shift_data):
        calls["n"] += 1
        if calls["n"] == 2:  # second row fails at publish
            return {"success": False, "error": "publish boom", "step": "PUBLISH", "shift_id": "DRAFT_2"}
        return {"success": True, "shift_id": f"S{calls['n']}"}

    monkeypatch.setattr(app_module.square, "create_and_publish_shift", fake)
    assert _upload(logged_in).status_code == 200
    return logged_in.post("/api/process-schedules", json={"approve": True}).get_json()


def test_partial_failure_records_results_and_orphan(logged_in, monkeypatch):
    body = _process_with_one_failure(logged_in, monkeypatch)
    assert body["status"] == "PARTIAL"
    assert body["success_count"] == 2
    assert body["error_count"] == 1
    upload_id = body["upload_id"]

    details = logged_in.get(f"/api/upload/{upload_id}").get_json()
    results = details["results"]
    assert len(results) == 3
    # The orphaned draft is tracked in schedules so it isn't lost.
    orphan = [r for r in results if r["status"] == "ERROR"][0]
    assert orphan["square_shift_id"] == "DRAFT_2"


def test_report_download(logged_in, monkeypatch):
    body = _process_with_one_failure(logged_in, monkeypatch)
    resp = logged_in.get(f"/api/upload/{body['upload_id']}/report.csv")
    assert resp.status_code == 200
    text = resp.data.decode()
    assert "row_number,status" in text
    assert "SUCCESS" in text and "ERROR" in text


def test_retry_failed_restages_only_failures(logged_in, monkeypatch):
    body = _process_with_one_failure(logged_in, monkeypatch)
    resp = logged_in.post(f"/api/upload/{body['upload_id']}/retry-failed")
    assert resp.status_code == 200
    assert resp.get_json()["row_count"] == 1
    # The re-staged row is the failed one, ready in the verify preview.
    preview = logged_in.get("/api/verify-preview").get_json()
    assert preview["total_rows"] == 1


def test_double_submit_is_rejected(logged_in, monkeypatch):
    app_module = logged_in.application_module
    _seed(app_module)
    monkeypatch.setattr(app_module.square, "create_and_publish_shift",
                        lambda s: {"success": True, "shift_id": "S1"})
    assert _upload(logged_in).status_code == 200
    first = logged_in.post("/api/process-schedules", json={"approve": True})
    assert first.status_code == 200
    # Second identical click finds nothing staged -> rejected, no double publish.
    second = logged_in.post("/api/process-schedules", json={"approve": True})
    assert second.status_code == 400


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def test_self_service_password_change(logged_in):
    # Wrong current password rejected.
    bad = logged_in.post("/api/account/password",
                         json={"current_password": "wrong", "new_password": "newpass12"})
    assert bad.status_code == 400
    # Correct current password accepted.
    ok = logged_in.post("/api/account/password",
                        json={"current_password": "admin123", "new_password": "newpass12"})
    assert ok.status_code == 200
    # New password now works for login.
    logged_in.get("/logout")
    relog = logged_in.post("/login", data={"username": "admin", "password": "newpass12"})
    assert relog.status_code == 302


def test_admin_reset_password(logged_in):
    app_module = logged_in.application_module
    uid = app_module.db.add_user("bob", app_module.hash_password("oldpass12"), is_admin=False)
    resp = logged_in.post("/admin/users",
                          json={"action": "reset_password", "id": uid, "password": "resetpass1"})
    assert resp.status_code == 200
    assert app_module.security.verify_password(
        "resetpass1", app_module.db.get_user_by_id(uid)["password_hash"])


def test_upload_endpoints_enforce_ownership(client):
    """A non-admin cannot read, export, or retry another user's upload."""
    app_module = client.application_module
    db = app_module.db
    # An upload created by 'admin'.
    upload_id = db.create_upload_record(1, "admin")
    db.add_upload_result(upload_id, 2, "ERROR", None, "boom",
                         {"employee_name": "Secret Person", "job_title": "X",
                          "location_name": "Y", "shift_date": "2026-06-01",
                          "start_time": "09:00", "end_time": "17:00"})
    # Log in as a different, non-admin user.
    db.add_user("mallory", app_module.hash_password("password1"), is_admin=False)
    client.post("/login", data={"username": "mallory", "password": "password1"})

    assert client.get(f"/api/upload/{upload_id}").status_code == 403
    assert client.get(f"/api/upload/{upload_id}/report.csv").status_code == 403
    assert client.post(f"/api/upload/{upload_id}/retry-failed").status_code == 403


def test_cannot_delete_last_admin(logged_in):
    app_module = logged_in.application_module
    admin = app_module.db.get_user("admin")
    resp = logged_in.post("/admin/users", json={"action": "delete", "id": admin["id"]})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Correctness: IntegrityError -> 409, schedule window widening
# ---------------------------------------------------------------------------
def test_duplicate_location_returns_409_not_500(logged_in):
    payload = {"action": "add", "name": "Dup", "square_location_id": "L_DUP"}
    assert logged_in.post("/settings/locations", json=payload).status_code == 200
    second = logged_in.post("/settings/locations", json=payload)
    assert second.status_code == 409
    assert "conflicts" in second.get_json()["error"]


def test_verify_duplicates_flags_existing_square_shifts(logged_in, monkeypatch):
    app_module = logged_in.application_module
    _seed(app_module)
    assert _upload(logged_in).status_code == 200

    # Square already has Jane's 2026-06-01 09:00 shift at Main Street.
    def fake_search(location_ids=None, start_at=None, end_at=None):
        return {"success": True, "scheduled_shifts": [
            {"id": "SB_EXIST", "draft_shift_details": {
                "location_id": "L_MAIN", "job_id": "J_BAR", "team_member_id": "T_JANE",
                "start_at": "2026-06-01T09:00:00-04:00"}},
        ]}

    monkeypatch.setattr(app_module.square, "search_scheduled_shifts", fake_search)
    resp = logged_in.post("/api/verify-preview/duplicates")
    assert resp.status_code == 200
    dupes = resp.get_json()["duplicates"]
    # Row 2 in the CSV (row_number 2) is Jane's already-existing shift.
    assert 2 in dupes


def test_schedule_fetch_uses_wide_timezone_window(logged_in, monkeypatch):
    captured = {}

    def fake_search(location_ids=None, start_at=None, end_at=None):
        captured["start_at"] = start_at
        captured["end_at"] = end_at
        return {"success": True, "scheduled_shifts": []}

    monkeypatch.setattr(logged_in.application_module.square,
                        "search_scheduled_shifts", fake_search)
    resp = logged_in.post("/api/schedule/fetch",
                          json={"start_date": "2026-06-01", "end_date": "2026-06-07"})
    assert resp.status_code == 200
    # Widest window so no local-day shift is cut off by a UTC boundary:
    # start at UTC+14 (earliest local midnight), end at UTC-14 (latest local
    # end-of-day). The range must be well-ordered (start UTC < end UTC).
    assert captured["start_at"] == "2026-06-01T00:00:00+14:00"
    assert captured["end_at"] == "2026-06-07T23:59:59-14:00"


def test_schedule_fetch_window_is_well_ordered_single_day(logged_in, monkeypatch):
    """Regression: the window must be a valid (start < end) UTC range even for a
    single-day query, or Square returns nothing."""
    from datetime import datetime
    captured = {}

    def fake_search(location_ids=None, start_at=None, end_at=None):
        captured["start_at"] = start_at
        captured["end_at"] = end_at
        return {"success": True, "scheduled_shifts": []}

    monkeypatch.setattr(logged_in.application_module.square,
                        "search_scheduled_shifts", fake_search)
    logged_in.post("/api/schedule/fetch",
                   json={"start_date": "2026-07-03", "end_date": "2026-07-03"})
    start = datetime.fromisoformat(captured["start_at"])
    end = datetime.fromisoformat(captured["end_at"])
    assert start < end, f"inverted window: {captured['start_at']} !< {captured['end_at']}"
    # And a normal Eastern 9am shift that day must fall inside the window.
    shift = datetime.fromisoformat("2026-07-03T09:00:00-04:00")
    assert start <= shift <= end
