def test_login_required_redirects(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_success(client):
    resp = client.post("/login", data={"username": "admin", "password": "admin123"})
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/")


def test_login_bad_password(client):
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 200
    assert b"Invalid username or password" in resp.data


def test_dashboard_renders_when_logged_in(logged_in):
    resp = logged_in.get("/")
    assert resp.status_code == 200
    assert b"Dashboard" in resp.data


def test_logout_clears_session(logged_in):
    resp = logged_in.get("/logout")
    assert resp.status_code == 302
    follow = logged_in.get("/")
    assert follow.status_code == 302
    assert "/login" in follow.headers["Location"]
