"""Tests for the Markdown master-data export and Ollama AI-convert flow."""

import io
from unittest.mock import patch


def _seed(app_module):
    app_module.db.add_location("Main Street", "L_MAIN", timezone="-04:00")
    app_module.db.add_location("Downtown", "L_DOWN", timezone="-05:00")
    app_module.db.add_job("Barista", "J_BAR")
    app_module.db.add_job("Manager", "J_MGR")
    app_module.db.add_team_member("Jane Doe", "T_JANE")


def test_master_data_md_requires_admin(client):
    resp = client.get("/admin/master-data.md")
    # Not logged in -> redirect to login
    assert resp.status_code in (302, 401, 403)


def test_master_data_md_lists_master_data(logged_in):
    _seed(logged_in.application_module)
    resp = logged_in.get("/admin/master-data.md")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "# Square Schedule Master Data" in body
    assert "## Locations" in body
    assert "Main Street" in body
    assert "Downtown" in body
    assert "## Jobs" in body
    assert "Barista" in body
    assert "## Team Members" in body
    assert "Jane Doe" in body
    # Should not leak Square IDs (only human-readable names)
    assert "L_MAIN" not in body
    assert "T_JANE" not in body


def test_ollama_settings_round_trip(logged_in):
    resp = logged_in.post(
        "/api/settings/ollama",
        json={"host": "http://example.local:11434", "model": "mistral:7b"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    got = logged_in.get("/api/settings/ollama").get_json()
    assert got["host"] == "http://example.local:11434"
    assert got["model"] == "mistral:7b"


def test_ai_convert_upload_calls_ollama_and_strips_fences(logged_in):
    _seed(logged_in.application_module)
    fake_csv_with_fences = (
        "```csv\n"
        "employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset\n"
        "Jane Doe,Barista,Main Street,2026-06-01,09:00,17:00,-04:00\n"
        "```"
    )

    def fake_chat(self, system_prompt, user_prompt, timeout=180):
        assert "Square Schedule Master Data" in user_prompt
        assert "messy" in user_prompt.lower() or "INPUT" in user_prompt
        return {"success": True, "content": fake_csv_with_fences}

    with patch("ollama_client.OllamaClient.chat", new=fake_chat):
        resp = logged_in.post(
            "/admin/ai-convert/upload",
            data={"file": (io.BytesIO(b"jane,barista,main,6/1/26 9a-5p"), "messy.txt")},
            content_type="multipart/form-data",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    # Fences stripped
    assert not data["csv"].startswith("```")
    assert not data["csv"].endswith("```")
    assert "Jane Doe,Barista,Main Street" in data["csv"]


def test_ai_convert_upload_rejects_xlsx(logged_in):
    resp = logged_in.post(
        "/admin/ai-convert/upload",
        data={"file": (io.BytesIO(b"fakebinary"), "messy.xlsx")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_ai_convert_stage_validates_csv_and_stages(logged_in):
    _seed(logged_in.application_module)
    csv_text = (
        "employee_name,job_title,location_name,shift_date,start_time,end_time,timezone_offset\n"
        "Jane Doe,Barista,Main Street,2026-06-01,09:00,17:00,-04:00\n"
    )
    resp = logged_in.post("/admin/ai-convert/stage", json={"csv": csv_text})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["row_count"] == 1
    assert data["redirect"].endswith("staged=1")

    # Should now be in pending_uploads, surfaced via verify-preview.
    preview = logged_in.get("/api/verify-preview").get_json()
    assert preview["total_rows"] == 1


def test_ai_convert_stage_rejects_missing_columns(logged_in):
    resp = logged_in.post("/admin/ai-convert/stage", json={"csv": "foo,bar\n1,2\n"})
    assert resp.status_code == 400
    assert b"Missing required column" in resp.data
