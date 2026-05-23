def test_add_and_list_location(logged_in):
    resp = logged_in.post(
        "/settings/locations",
        json={"action": "add", "name": "Main Street", "square_location_id": "L_MAIN"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    page = logged_in.get("/settings/locations")
    assert page.status_code == 200
    assert b"Main Street" in page.data
    assert b"L_MAIN" in page.data


def test_add_job_and_team_member(logged_in):
    j = logged_in.post(
        "/settings/jobs",
        json={"action": "add", "name": "Barista", "square_job_id": "J_BAR"},
    )
    assert j.get_json()["success"] is True

    t = logged_in.post(
        "/settings/team-members",
        json={
            "action": "add",
            "name": "Jane Doe",
            "square_team_member_id": "T_JANE",
        },
    )
    assert t.get_json()["success"] is True


def test_settings_require_admin(client):
    app_module = client.application_module
    non_admin_hash = app_module.hash_password("pw")
    app_module.db.add_user("regular", non_admin_hash, is_admin=False)
    client.post("/login", data={"username": "regular", "password": "pw"})

    resp = client.post(
        "/settings/locations",
        json={"action": "add", "name": "X", "square_location_id": "Y"},
    )
    assert resp.status_code == 403
