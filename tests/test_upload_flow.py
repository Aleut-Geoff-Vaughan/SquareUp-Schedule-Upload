import io


SAMPLE_CSV = (
    "employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset\n"
    "Jane Doe,Barista,Main Street,2026-06-01,09:00,17:00,-04:00\n"
    "John Smith,Manager,Main Street,2026-06-01,08:00,16:00,-04:00\n"
    ",Barista,Main Street,2026-06-02,06:00,10:00,-04:00\n"
)


def _seed_lookups(app_module, include_john=True):
    app_module.db.add_location("Main Street", "L_MAIN")
    app_module.db.add_job("Barista", "J_BAR")
    app_module.db.add_job("Manager", "J_MGR")
    app_module.db.add_team_member("Jane Doe", "T_JANE")
    if include_john:
        app_module.db.add_team_member("John Smith", "T_JOHN")


def _upload_sample(client):
    return client.post(
        "/upload",
        data={"file": (io.BytesIO(SAMPLE_CSV.encode("utf-8")), "schedule.csv")},
        content_type="multipart/form-data",
    )


def test_upload_rejects_missing_columns(logged_in):
    bad_csv = "employee_name,job_title\nJane,Barista\n"
    resp = logged_in.post(
        "/upload",
        data={"file": (io.BytesIO(bad_csv.encode("utf-8")), "bad.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert b"Missing required column" in resp.data


def test_upload_rejects_empty_csv(logged_in):
    empty = "employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset\n"
    resp = logged_in.post(
        "/upload",
        data={"file": (io.BytesIO(empty.encode("utf-8")), "empty.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_upload_rejects_non_csv(logged_in):
    resp = logged_in.post(
        "/upload",
        data={"file": (io.BytesIO(b"hi"), "notes.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_full_upload_and_verify_flow(logged_in):
    _seed_lookups(logged_in.application_module)

    upload = _upload_sample(logged_in)
    assert upload.status_code == 200
    assert upload.get_json()["row_count"] == 3

    preview = logged_in.get("/api/verify-preview")
    assert preview.status_code == 200
    data = preview.get_json()
    assert data["total_rows"] == 3
    # Jane Doe + John Smith both map to team members; the blank-employee row is
    # a valid open shift. All three are valid.
    assert data["valid_rows"] == 3


def test_named_but_unmatched_employee_is_invalid(logged_in):
    """A named employee that doesn't match any team member must be flagged
    invalid, NOT silently published as an open shift (regression guard)."""
    _seed_lookups(logged_in.application_module, include_john=False)
    assert _upload_sample(logged_in).status_code == 200
    data = logged_in.get("/api/verify-preview").get_json()
    # Jane + open shift are valid; John Smith (no mapping) is invalid.
    assert data["valid_rows"] == 2
    john = next(r for r in data["rows"] if r["employee_name"] == "John Smith")
    assert john["is_valid"] is False
    assert any("John Smith" in e for e in john["errors"])


def test_invalid_rows_are_not_sent_to_square(logged_in, monkeypatch):
    """process-schedules must skip invalid rows entirely rather than publish
    them (invalid = missing lookup or unmatched named employee)."""
    _seed_lookups(logged_in.application_module, include_john=False)
    calls = []
    monkeypatch.setattr(
        logged_in.application_module.square, "create_and_publish_shift",
        lambda shift_data: calls.append(shift_data) or {"success": True, "shift_id": f"S{len(calls)}"},
    )
    assert _upload_sample(logged_in).status_code == 200
    body = logged_in.post("/api/process-schedules", json={"approve": True}).get_json()
    # Only Jane + the open shift are published; John Smith is recorded as an error.
    assert body["success_count"] == 2
    assert body["error_count"] == 1
    assert len(calls) == 2


def test_verify_preview_flags_missing_lookups(logged_in):
    # Don't seed anything — every row should fail with "Location not found" / "Job not found"
    upload = _upload_sample(logged_in)
    assert upload.status_code == 200

    preview = logged_in.get("/api/verify-preview").get_json()
    assert preview["valid_rows"] == 0
    assert preview["invalid_rows"] == 3
    first = preview["rows"][0]
    assert any("Location" in e for e in first["errors"])
    assert any("Job" in e for e in first["errors"])


def test_process_uses_square_api(logged_in, monkeypatch):
    app_module = logged_in.application_module
    _seed_lookups(app_module)

    calls = []

    def fake_create_and_publish(shift_data):
        calls.append(shift_data)
        return {"success": True, "shift_id": f"SHIFT_{len(calls)}"}

    monkeypatch.setattr(
        app_module.square, "create_and_publish_shift", fake_create_and_publish
    )

    assert _upload_sample(logged_in).status_code == 200
    resp = logged_in.post("/api/process-schedules", json={"approve": True})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["success_count"] == 3
    assert body["error_count"] == 0
    assert len(calls) == 3
    assert all(c["location_id"] == "L_MAIN" for c in calls)

    # Pending upload should be cleared
    follow = logged_in.get("/api/verify-preview")
    assert follow.status_code == 400


def test_process_requires_approval(logged_in):
    _seed_lookups(logged_in.application_module)
    _upload_sample(logged_in)
    resp = logged_in.post("/api/process-schedules", json={"approve": False})
    assert resp.status_code == 400


def test_lookups_tolerate_whitespace_and_case(logged_in):
    """Real-world bug: CSV had 'DOMINION HILLS POOL' but lookups failed because
    of trailing space / case difference. The lookup methods should normalize."""
    app_module = logged_in.application_module
    app_module.db.add_location("DOMINION HILLS POOL", "L_POOL", timezone="-04:00")
    app_module.db.add_job("Lifeguard", "J_LG")
    app_module.db.add_team_member("Jane Doe", "T_JANE")

    # Trailing whitespace
    assert app_module.db.get_location_by_name("DOMINION HILLS POOL ")["square_location_id"] == "L_POOL"
    # Different case
    assert app_module.db.get_location_by_name("dominion hills pool")["square_location_id"] == "L_POOL"
    # Both
    assert app_module.db.get_location_by_name("  Dominion Hills Pool  ")["square_location_id"] == "L_POOL"
    # Jobs
    assert app_module.db.get_job_by_name("LIFEGUARD")["square_job_id"] == "J_LG"
    assert app_module.db.get_job_by_name("Lifeguard ")["square_job_id"] == "J_LG"
    # Team members
    assert app_module.db.get_team_member_by_name("jane doe")["square_team_member_id"] == "T_JANE"
    # Blank / None returns None (not a SQL error)
    assert app_module.db.get_location_by_name(None) is None
    assert app_module.db.get_team_member_by_name("") is None


def test_upload_strips_cell_whitespace(logged_in):
    """A CSV with trailing spaces in cells should still validate cleanly."""
    _seed_lookups(logged_in.application_module)
    messy_csv = (
        "employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset\n"
        "Jane Doe , Barista ,Main Street ,2026-06-01,09:00,17:00,-04:00\n"
    )
    resp = logged_in.post(
        "/upload",
        data={"file": (io.BytesIO(messy_csv.encode("utf-8")), "messy.csv")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    preview = logged_in.get("/api/verify-preview").get_json()
    assert preview["valid_rows"] == 1
    assert preview["invalid_rows"] == 0


def test_build_stages_manual_rows(logged_in):
    _seed_lookups(logged_in.application_module)
    payload = {
        "rows": [
            {
                "location_name": "Main Street",
                "job_title": "Barista",
                "employee_name": "Jane Doe",
                "shift_date": "2026-07-04",
                "start_time": "09:00",
                "end_time": "17:00",
                "timezone_offset": "-04:00",
            },
            {
                "location_name": "Main Street",
                "job_title": "Manager",
                "employee_name": "",
                "shift_date": "2026-07-04",
                "start_time": "10:00",
                "end_time": "18:00",
            },
        ]
    }
    resp = logged_in.post("/upload/build", json=payload)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["success"] is True
    assert body["row_count"] == 2

    preview = logged_in.get("/api/verify-preview")
    assert preview.status_code == 200
    pdata = preview.get_json()
    assert pdata["total_rows"] == 2
    assert pdata["valid_rows"] == 2


def test_build_rejects_empty_rows(logged_in):
    resp = logged_in.post("/upload/build", json={"rows": []})
    assert resp.status_code == 400


def test_build_rejects_missing_fields(logged_in):
    resp = logged_in.post("/upload/build", json={"rows": [{"location_name": "Main Street"}]})
    assert resp.status_code == 400
    assert b"missing" in resp.data.lower()
