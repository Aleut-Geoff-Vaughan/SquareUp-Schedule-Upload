"""Backup/restore, migrations, and per-user cleanup."""

import io
import sqlite3


def test_backup_download_returns_sqlite_and_cleans_temp(logged_in, tmp_path):
    resp = logged_in.get("/admin/backup/download")
    assert resp.status_code == 200
    assert resp.data.startswith(b"SQLite format 3")
    # A valid snapshot opens and contains the users table.
    out = tmp_path / "b.db"
    out.write_bytes(resp.data)
    conn = sqlite3.connect(out)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "users" in tables and "schedules" in tables


def test_backup_download_requires_admin(client):
    app_module = client.application_module
    app_module.db.add_user("reg", app_module.hash_password("password1"), is_admin=False)
    client.post("/login", data={"username": "reg", "password": "password1"})
    assert client.get("/admin/backup/download").status_code == 403


def test_restore_rejects_non_sqlite(logged_in):
    resp = logged_in.post(
        "/admin/backup/restore",
        data={"file": (io.BytesIO(b"not a database"), "x.db")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "not a valid SQLite" in resp.get_json()["error"]


def test_restore_rejects_sqlite_missing_required_tables(logged_in, tmp_path):
    bad = tmp_path / "partial.db"
    conn = sqlite3.connect(bad)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    resp = logged_in.post(
        "/admin/backup/restore",
        data={"file": (io.BytesIO(bad.read_bytes()), "partial.db")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "missing expected tables" in resp.get_json()["error"]


def test_backup_then_restore_round_trip(logged_in, tmp_path):
    app_module = logged_in.application_module
    app_module.db.add_location("Roundtrip Loc", "L_RT")

    backup = logged_in.get("/admin/backup/download").data

    # Mutate after the snapshot.
    app_module.db.add_location("After Backup", "L_AFTER")
    assert app_module.db.get_location_by_name("After Backup") is not None

    resp = logged_in.post(
        "/admin/backup/restore",
        data={"file": (io.BytesIO(backup), "backup.db")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    # Restored state: the pre-snapshot location exists, the later one is gone.
    assert app_module.db.get_location_by_name("Roundtrip Loc") is not None
    assert app_module.db.get_location_by_name("After Backup") is None


def test_delete_user_clears_their_pending_upload(client):
    app_module = client.application_module
    db = app_module.db
    uid = db.add_user("temp", app_module.hash_password("password1"), is_admin=False)
    db.set_pending_upload(uid, [{"employee_name": "x"}])
    assert db.get_pending_upload(uid) is not None
    db.delete_user(uid)
    assert db.get_pending_upload(uid) is None


def test_schema_version_is_current(client):
    app_module = client.application_module
    import database
    with app_module.db.get_connection() as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    assert version == database.SCHEMA_VERSION


def test_migration_upgrades_legacy_db_in_place(tmp_path, monkeypatch):
    """A pre-existing DB without the newer columns/tables is upgraded by
    init_db without data loss."""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    # Minimal legacy schema: locations without timezone_name, user_version 0.
    conn.executescript(
        """
        CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, is_admin BOOLEAN, created_at TIMESTAMP);
        CREATE TABLE locations (id INTEGER PRIMARY KEY, name TEXT, square_location_id TEXT UNIQUE, timezone TEXT, created_at TIMESTAMP);
        CREATE TABLE jobs (id INTEGER PRIMARY KEY, name TEXT, square_job_id TEXT UNIQUE, created_at TIMESTAMP);
        CREATE TABLE team_members (id INTEGER PRIMARY KEY, name TEXT, square_team_member_id TEXT UNIQUE, created_at TIMESTAMP);
        CREATE TABLE uploads (id INTEGER PRIMARY KEY, row_count INTEGER, success_count INTEGER, error_count INTEGER, status TEXT, uploaded_by TEXT, created_at TIMESTAMP, processed_at TIMESTAMP);
        CREATE TABLE schedules (id INTEGER PRIMARY KEY, upload_id INTEGER, square_shift_id TEXT, location_id TEXT, job_id TEXT, team_member_id TEXT, shift_date DATE, start_time TIME, end_time TIME, created_at TIMESTAMP);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP);
        CREATE TABLE pending_uploads (user_id INTEGER PRIMARY KEY, csv_data TEXT, created_at TIMESTAMP);
        INSERT INTO locations (name, square_location_id, timezone) VALUES ('Old', 'L_OLD', '-05:00');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DB_PATH", str(db_file))
    import importlib
    import database
    importlib.reload(database)
    fresh_db = database.Database()
    fresh_db.init_db()

    with fresh_db.get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == database.SCHEMA_VERSION
        cols = {r[1] for r in conn.execute("PRAGMA table_info(locations)")}
        assert "timezone_name" in cols
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "upload_results" in tables
        # -05:00 was backfilled to a Central IANA name.
        row = conn.execute("SELECT timezone_name FROM locations WHERE square_location_id='L_OLD'").fetchone()
        assert row[0] == "America/Chicago"

    # Restore the module for other tests.
    importlib.reload(database)
