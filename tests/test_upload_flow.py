import io


SAMPLE_CSV = (
    "employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset\n"
    "Jane Doe,Barista,Main Street,2026-06-01,09:00,17:00,-04:00\n"
    "John Smith,Manager,Main Street,2026-06-01,08:00,16:00,-04:00\n"
    ",Barista,Main Street,2026-06-02,06:00,10:00,-04:00\n"
)


def _seed_lookups(app_module):
    app_module.db.add_location("Main Street", "L_MAIN")
    app_module.db.add_job("Barista", "J_BAR")
    app_module.db.add_job("Manager", "J_MGR")
    app_module.db.add_team_member("Jane Doe", "T_JANE")
    # John Smith intentionally NOT added — to exercise the "unknown team member" case
    # (open shifts are allowed but a named team member with no mapping is still ok per app logic)


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
    # Jane Doe + Main Street + Barista all map; John Smith has no team_member mapping
    # but app logic treats unknown team member as an open shift (valid).
    # Both first rows should be valid; the row with empty employee_name is also valid
    # because team_member is optional.
    assert data["valid_rows"] == 3


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
