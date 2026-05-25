"""
Square Schedule Manager
Main Flask Application
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response, send_file
from flask_cors import CORS
import os
import csv
import io
import json
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
import hashlib

# Import our custom modules
from database import Database
from square_api import SquareAPI
from ollama_client import OllamaClient, DEFAULT_HOST as OLLAMA_DEFAULT_HOST, DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL

# Initialize Flask app
_secret_key = os.environ.get('SECRET_KEY', '').strip()
if not _secret_key or _secret_key == 'dev-secret-key-change-in-production':
    raise RuntimeError(
        "SECRET_KEY environment variable is required and must not be the default value. "
        "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\" "
        "and pass it to the container via --env-file or -e SECRET_KEY=..."
    )

app = Flask(__name__)
app.secret_key = _secret_key
CORS(app)

# Initialize database and Square API
db = Database()
db.init_db()


def _apply_persisted_environment():
    """If admin previously toggled environment via the UI, propagate to env."""
    persisted = db.get_setting('square_environment')
    if persisted in ('sandbox', 'production'):
        os.environ['SQUARE_ENVIRONMENT'] = persisted


_apply_persisted_environment()
square = SquareAPI()

# Ensure uploads directory exists
UPLOAD_FOLDER = 'uploads'
Path(UPLOAD_FOLDER).mkdir(exist_ok=True)


@app.context_processor
def inject_environment():
    """Expose Square environment + token-configured flag to all templates."""
    from square_api import resolve_credentials
    creds = resolve_credentials()
    return {
        'square_environment': creds['environment'],
        'square_token_configured': bool(creds['access_token']),
    }

# ==================== AUTHENTICATION ====================

def login_required(f):
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Get user from database
        user = db.get_user(username)
        
        if user and verify_password(password, user['password_hash']):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = user['is_admin']
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid username or password')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout"""
    session.clear()
    return redirect(url_for('login'))

def verify_password(password, password_hash):
    """Verify password against hash"""
    return hashlib.sha256(password.encode()).hexdigest() == password_hash

def hash_password(password):
    """Hash a password"""
    return hashlib.sha256(password.encode()).hexdigest()

# ==================== DASHBOARD ====================

@app.route('/')
@login_required
def dashboard():
    """Main dashboard"""
    stats = {
        'total_locations': len(db.get_locations()),
        'total_jobs': len(db.get_jobs()),
        'total_team_members': len(db.get_team_members()),
        'recent_uploads': len(db.get_recent_uploads(limit=10)),
        'pending_approvals': len(db.get_pending_approvals())
    }
    return render_template('dashboard.html', stats=stats)

# ==================== SETTINGS / CONFIGURATION ====================

TIMEZONE_CHOICES = [
    {'value': '-04:00', 'label': 'Eastern Time — EDT (-04:00)'},
    {'value': '-05:00', 'label': 'Eastern Time — EST (-05:00) / Central — CDT'},
    {'value': '-06:00', 'label': 'Central Time — CST (-06:00) / Mountain — MDT'},
    {'value': '-07:00', 'label': 'Mountain Time — MST (-07:00) / Pacific — PDT / Arizona'},
    {'value': '-08:00', 'label': 'Pacific Time — PST (-08:00) / Alaska — AKDT'},
    {'value': '-09:00', 'label': 'Alaska — AKST (-09:00)'},
    {'value': '-10:00', 'label': 'Hawaii (-10:00)'},
    {'value': '+00:00', 'label': 'UTC (+00:00)'},
]


@app.route('/settings/locations', methods=['GET', 'POST'])
@login_required
def locations():
    """Manage locations"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    if request.method == 'POST':
        data = request.json
        action = data.get('action')

        if action == 'add':
            db.add_location(
                data['name'],
                data['square_location_id'],
                timezone=data.get('timezone') or '-04:00',
            )
            return jsonify({'success': True, 'message': 'Location added'})
        elif action == 'update':
            db.update_location(
                data['id'],
                data['name'],
                data['square_location_id'],
                timezone=data.get('timezone'),
            )
            return jsonify({'success': True, 'message': 'Location updated'})
        elif action == 'delete':
            db.delete_location(data['id'])
            return jsonify({'success': True, 'message': 'Location deleted'})

    locations_list = db.get_locations()
    return render_template(
        'settings_locations.html',
        locations=locations_list,
        timezone_choices=TIMEZONE_CHOICES,
    )


@app.route('/settings/locations/sync', methods=['POST'])
@login_required
def locations_sync():
    """Pull all locations from Square and replace the local locations table.

    All imported locations default to Eastern Time (-04:00); admins can edit
    each location's timezone afterward.
    """
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    result = square.list_locations()
    if not result.get('success'):
        return jsonify({'error': f'Could not fetch locations from Square: {result.get("error")}'}), 502

    rows = []
    for loc in result['locations']:
        name = (loc.get('name') or '').strip()
        lid = loc.get('id')
        if not name or not lid:
            continue
        rows.append((name, lid, '-04:00'))

    if not rows:
        return jsonify({'error': 'Square returned no locations. Existing list was not modified.'}), 400

    db.replace_locations(rows)

    return jsonify({
        'success': True,
        'imported': len(rows),
        'message': f'Replaced local locations with {len(rows)} entries from Square. All defaulted to Eastern Time; edit individually if needed.',
    })

@app.route('/settings/jobs', methods=['GET', 'POST'])
@login_required
def jobs():
    """Manage jobs"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    if request.method == 'POST':
        data = request.json
        action = data.get('action')
        
        if action == 'add':
            db.add_job(data['name'], data['square_job_id'])
            return jsonify({'success': True, 'message': 'Job added'})
        elif action == 'update':
            db.update_job(data['id'], data['name'], data['square_job_id'])
            return jsonify({'success': True, 'message': 'Job updated'})
        elif action == 'delete':
            db.delete_job(data['id'])
            return jsonify({'success': True, 'message': 'Job deleted'})
    
    jobs_list = db.get_jobs()
    return render_template('settings_jobs.html', jobs=jobs_list)

@app.route('/settings/jobs/sync', methods=['POST'])
@login_required
def jobs_sync():
    """Pull all jobs from Square Labor API and replace the local jobs table."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    result = square.list_jobs()
    if not result.get('success'):
        return jsonify({'error': f'Could not fetch jobs from Square: {result.get("error")}'}), 502

    rows = []
    skipped = []
    for j in result['jobs']:
        title = (j.get('title') or '').strip()
        jid = j.get('id')
        if not title or not jid:
            skipped.append(j)
            continue
        rows.append((title, jid))

    if not rows:
        return jsonify({'error': 'Square returned no jobs. Existing list was not modified.'}), 400

    db.replace_jobs(rows)

    return jsonify({
        'success': True,
        'imported': len(rows),
        'skipped': len(skipped),
        'jobs': [{'name': r[0], 'square_job_id': r[1]} for r in rows],
        'message': f'Replaced local jobs with {len(rows)} entries from Square.',
    })

@app.route('/settings/team-members', methods=['GET', 'POST'])
@login_required
def team_members():
    """Manage team members"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    if request.method == 'POST':
        data = request.json
        action = data.get('action')
        
        if action == 'add':
            db.add_team_member(data['name'], data['square_team_member_id'])
            return jsonify({'success': True, 'message': 'Team member added'})
        elif action == 'update':
            db.update_team_member(data['id'], data['name'], data['square_team_member_id'])
            return jsonify({'success': True, 'message': 'Team member updated'})
        elif action == 'delete':
            db.delete_team_member(data['id'])
            return jsonify({'success': True, 'message': 'Team member deleted'})
    
    team_members_list = db.get_team_members()
    return render_template('settings_team_members.html', team_members=team_members_list)

@app.route('/settings/team-members/import', methods=['POST'])
@login_required
def team_members_import():
    """Import team members from a Square export CSV.

    Skips Inactive rows, de-duplicates by email, and matches each row to a
    Square Team Member ID by email via the Square API. Overwrites the
    existing team_members table on success.
    """
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    if not file.filename.lower().endswith('.csv'):
        return jsonify({'error': 'File must be CSV'}), 400

    try:
        stream = file.stream.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(stream))
        required = {'First Name', 'Last Name', 'Email', 'Status'}
        header = set(reader.fieldnames or [])
        missing = required - header
        if missing:
            return jsonify({'error': f'CSV is missing required column(s): {", ".join(sorted(missing))}'}), 400

        seen_emails = set()
        candidates = []
        skipped_inactive = 0
        skipped_no_email = 0
        for row in reader:
            if (row.get('Status') or '').strip().lower() != 'active':
                skipped_inactive += 1
                continue
            email = (row.get('Email') or '').strip().lower()
            if not email:
                skipped_no_email += 1
                continue
            if email in seen_emails:
                continue
            seen_emails.add(email)
            first = (row.get('First Name') or '').strip()
            last = (row.get('Last Name') or '').strip()
            name = f'{first} {last}'.strip()
            candidates.append({'name': name, 'email': email})

        if not candidates:
            return jsonify({'error': 'No active rows with an email found in the CSV.'}), 400

        result = square.list_team_members(status='ACTIVE')
        if not result.get('success'):
            return jsonify({'error': f'Could not fetch team members from Square: {result.get("error")}'}), 502

        square_by_email = {}
        for m in result['team_members']:
            mail = (m.get('email_address') or '').strip().lower()
            if mail and mail not in square_by_email:
                square_by_email[mail] = m['id']

        matched = []
        unmatched = []
        for c in candidates:
            sid = square_by_email.get(c['email'])
            if sid:
                matched.append((c['name'], sid))
            else:
                unmatched.append({'name': c['name'], 'email': c['email']})

        if not matched:
            return jsonify({
                'error': 'No CSV rows matched any Square team member by email. Existing team members were NOT modified.',
                'unmatched': unmatched,
            }), 400

        db.replace_team_members(matched)

        return jsonify({
            'success': True,
            'imported': len(matched),
            'skipped_inactive': skipped_inactive,
            'skipped_no_email': skipped_no_email,
            'unmatched': unmatched,
            'message': f'Replaced team members with {len(matched)} active entries from Square.',
        })
    except Exception as e:
        return jsonify({'error': f'Import failed: {e}'}), 400

# ==================== CSV UPLOAD & PROCESSING ====================

@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    """CSV Upload page"""
    if request.method == 'POST':
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        
        if not file.filename.endswith('.csv'):
            return jsonify({'error': 'File must be CSV'}), 400
        
        try:
            # Read CSV
            stream = file.stream.read().decode("UTF8")
            reader = csv.DictReader(stream.splitlines())
            csv_data = list(reader)

            if not csv_data:
                return jsonify({'error': 'CSV is empty or has no data rows'}), 400

            required_columns = ['employee_name', 'job_title', 'location_name', 'shift_date', 'start_time', 'end_time']
            header = reader.fieldnames or []
            missing = [c for c in required_columns if c not in header]
            if missing:
                return jsonify({'error': f"Missing required column(s): {', '.join(missing)}"}), 400

            db.set_pending_upload(session['user_id'], csv_data)
            session['upload_timestamp'] = datetime.now().isoformat()

            return jsonify({
                'success': True,
                'message': f'{len(csv_data)} rows ready for verification',
                'row_count': len(csv_data)
            })
        except Exception as e:
            return jsonify({'error': str(e)}), 400
    
    return render_template('upload.html')

@app.route('/upload/template', methods=['GET'])
@login_required
def upload_template():
    """Download a CSV template with required headers and sample rows."""
    headers = ['employee_name', 'job_title', 'location_name', 'shift_date', 'start_time', 'end_time', 'timezone_offset']
    sample_rows = [
        ['Jane Doe', 'Barista', 'Main Street', '2026-06-01', '09:00', '17:00', '-04:00'],
        ['John Smith', 'Manager', 'Main Street', '2026-06-01', '08:00', '16:00', '-04:00'],
        ['Alex Johnson', 'Barista', 'Downtown', '2026-06-01', '12:00', '20:00', '-04:00'],
    ]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(sample_rows)

    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename="schedule_template.csv"'},
    )


def _build_master_data_markdown():
    """Build a Markdown export of master data for use as LLM context."""
    locations = db.get_locations()
    jobs = db.get_jobs()
    members = db.get_team_members()

    lines = []
    lines.append('# Square Schedule Master Data')
    lines.append('')
    lines.append('Use this file as context when asking an LLM (Claude, GPT, local model) to convert a messy schedule into the import CSV format expected by Square Schedule Manager.')
    lines.append('')
    lines.append('## Required output format')
    lines.append('')
    lines.append('Output must be a CSV with these exact column headers (order is not enforced, but headers must match exactly):')
    lines.append('')
    lines.append('| Column | Required | Format | Notes |')
    lines.append('| --- | --- | --- | --- |')
    lines.append('| employee_name | optional | full name | blank for an open shift; when present must match a team member name exactly |')
    lines.append('| job_title | yes | text | must match a job name exactly |')
    lines.append('| location_name | yes | text | must match a location name exactly |')
    lines.append('| shift_date | yes | YYYY-MM-DD | ISO 8601 calendar date |')
    lines.append('| start_time | yes | HH:MM | 24-hour, local time at the location |')
    lines.append('| end_time | yes | HH:MM | 24-hour, local time at the location |')
    lines.append('| timezone_offset | optional | ±HH:MM | e.g. -04:00; if blank, the location\'s configured timezone is used |')
    lines.append('')
    lines.append('Sample row (header omitted):')
    lines.append('')
    lines.append('```')
    lines.append('Jane Doe,Barista,Main Street,2026-06-01,09:00,17:00,-04:00')
    lines.append('```')
    lines.append('')
    lines.append('Rules to follow when normalizing:')
    lines.append('')
    lines.append('- Match employee, job, and location names exactly to the lists below. If unsure, leave employee_name blank (open shift) rather than guessing.')
    lines.append('- Convert any time format (12-hour, AM/PM, dotted, etc.) to 24-hour HH:MM.')
    lines.append('- Convert any date format to YYYY-MM-DD.')
    lines.append('- Output the CSV only — no Markdown code fences, no commentary, no extra columns.')
    lines.append('')

    lines.append('## Locations')
    lines.append('')
    if locations:
        lines.append('| Name | Timezone |')
        lines.append('| --- | --- |')
        for l in locations:
            lines.append(f"| {l['name']} | {l.get('timezone') or '-04:00'} |")
    else:
        lines.append('_(none yet — sync from Square in Settings → Locations)_')
    lines.append('')

    lines.append('## Jobs')
    lines.append('')
    if jobs:
        lines.append('| Name |')
        lines.append('| --- |')
        for j in jobs:
            lines.append(f"| {j['name']} |")
    else:
        lines.append('_(none yet — sync from Square in Settings → Jobs)_')
    lines.append('')

    lines.append('## Team Members')
    lines.append('')
    if members:
        lines.append('| Name |')
        lines.append('| --- |')
        for m in members:
            lines.append(f"| {m['name']} |")
    else:
        lines.append('_(none yet — import from a Square export in Settings → Team Members)_')
    lines.append('')

    return '\n'.join(lines)


@app.route('/admin/master-data.md', methods=['GET'])
@login_required
def master_data_md():
    """Download the master data as Markdown for use as LLM context."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    content = _build_master_data_markdown()
    filename = f"square-schedule-master-{datetime.now().strftime('%Y%m%d')}.md"
    return Response(
        content,
        mimetype='text/markdown',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ==================== AI CONVERT (OLLAMA) ====================


def _ollama_config():
    """Read Ollama host + model from settings, falling back to defaults."""
    host = db.get_setting('ollama_host') or OLLAMA_DEFAULT_HOST
    model = db.get_setting('ollama_model') or OLLAMA_DEFAULT_MODEL
    return host, model


@app.route('/admin/ai-convert', methods=['GET'])
@login_required
def ai_convert_page():
    """Page that hosts Ollama settings, the messy-file uploader, and the result preview."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    host, model = _ollama_config()
    return render_template(
        'ai_convert.html',
        ollama_host=host,
        ollama_model=model,
        ollama_default_host=OLLAMA_DEFAULT_HOST,
        ollama_default_model=OLLAMA_DEFAULT_MODEL,
    )


@app.route('/api/settings/ollama', methods=['GET', 'POST'])
@login_required
def ollama_settings():
    """Read or update Ollama host + model in the settings table."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        host = (data.get('host') or '').strip()
        model = (data.get('model') or '').strip()
        if host:
            db.set_setting('ollama_host', host)
        if model:
            db.set_setting('ollama_model', model)
        return jsonify({'success': True, 'host': host or db.get_setting('ollama_host'), 'model': model or db.get_setting('ollama_model')})

    host, model = _ollama_config()
    return jsonify({'host': host, 'model': model})


@app.route('/api/ollama/test', methods=['POST'])
@login_required
def ollama_test():
    """Ping Ollama and list installed models."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    host, model = _ollama_config()
    result = OllamaClient(host=host, model=model).ping()
    return jsonify(result), (200 if result.get('success') else 502)


@app.route('/admin/ai-convert/upload', methods=['POST'])
@login_required
def ai_convert_upload():
    """Send an uploaded messy schedule file to Ollama and return its CSV output.

    Does NOT stage anything yet — the user reviews/edits the output, then
    posts to /admin/ai-convert/stage to commit it as a pending upload.
    """
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    allowed = ('.csv', '.tsv', '.txt')
    if not file.filename.lower().endswith(allowed):
        return jsonify({'error': f'Only {", ".join(allowed)} files are accepted. For Excel, use the Markdown export with Claude.ai.'}), 400

    raw = file.stream.read()
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:
            text = raw.decode('latin-1')
        except Exception as e:
            return jsonify({'error': f'Could not decode file: {e}'}), 400

    if len(text) > 100_000:
        return jsonify({'error': 'File is larger than 100k characters. Try splitting it.'}), 400

    master = _build_master_data_markdown()
    system_prompt = (
        'You are a strict schedule normalizer. Your only output is a CSV that exactly matches '
        'the required header and rules in the master data document the user provides as context. '
        'Do not wrap the output in Markdown code fences. Do not add commentary. Do not add columns. '
        'If you cannot determine a field, leave it blank rather than guessing.'
    )
    user_prompt = (
        'CONTEXT (master data and required format):\n\n'
        f'{master}\n\n'
        '---\n\n'
        'INPUT (messy schedule to convert):\n\n'
        f'{text}\n\n'
        '---\n\n'
        'Output the CSV now. Begin with the header row.'
    )

    host, model = _ollama_config()
    result = OllamaClient(host=host, model=model).chat(system_prompt, user_prompt)
    if not result.get('success'):
        return jsonify({'error': result.get('error')}), 502

    content = (result.get('content') or '').strip()
    # Strip code fences if the model added them anyway.
    if content.startswith('```'):
        lines = content.splitlines()
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        content = '\n'.join(lines).strip()

    return jsonify({'success': True, 'csv': content, 'model': model})


@app.route('/admin/ai-convert/stage', methods=['POST'])
@login_required
def ai_convert_stage():
    """Take an (optionally edited) CSV body and stage it as a pending upload."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.get_json(silent=True) or {}
    csv_text = (data.get('csv') or '').strip()
    if not csv_text:
        return jsonify({'error': 'CSV is empty.'}), 400

    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)
        if not rows:
            return jsonify({'error': 'No data rows found.'}), 400

        required = ['employee_name', 'job_title', 'location_name', 'shift_date', 'start_time', 'end_time']
        header = set(reader.fieldnames or [])
        missing = [c for c in required if c not in header]
        if missing:
            return jsonify({'error': f'Missing required column(s): {", ".join(missing)}'}), 400

        db.set_pending_upload(session['user_id'], rows)
        session['upload_timestamp'] = datetime.now().isoformat()
        return jsonify({
            'success': True,
            'row_count': len(rows),
            'redirect': url_for('upload') + '?staged=1',
            'message': f'{len(rows)} rows staged for verification.',
        })
    except Exception as e:
        return jsonify({'error': f'Could not parse CSV: {e}'}), 400


@app.route('/upload/build', methods=['GET', 'POST'])
@login_required
def upload_build():
    """Manually compose a schedule from master data, then stage it for verification.

    GET renders the builder UI with searchable dropdowns populated from the
    locations, jobs, and team_members tables. POST accepts a JSON list of rows
    and writes them to the same pending_uploads staging table the CSV flow uses,
    so the existing /api/verify-preview and /api/process-schedules endpoints
    handle the rest with no changes.
    """
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        rows = data.get('rows') or []
        if not isinstance(rows, list) or not rows:
            return jsonify({'error': 'No rows submitted'}), 400

        required = ['location_name', 'job_title', 'shift_date', 'start_time', 'end_time']
        csv_rows = []
        for idx, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                return jsonify({'error': f'Row {idx}: not an object'}), 400
            for f in required:
                if not (row.get(f) or '').strip():
                    return jsonify({'error': f'Row {idx}: missing {f}'}), 400
            csv_rows.append({
                'employee_name': (row.get('employee_name') or '').strip(),
                'job_title': row['job_title'].strip(),
                'location_name': row['location_name'].strip(),
                'shift_date': row['shift_date'].strip(),
                'start_time': row['start_time'].strip(),
                'end_time': row['end_time'].strip(),
                'timezone_offset': (row.get('timezone_offset') or '').strip(),
            })

        db.set_pending_upload(session['user_id'], csv_rows)
        session['upload_timestamp'] = datetime.now().isoformat()
        return jsonify({
            'success': True,
            'row_count': len(csv_rows),
            'message': f'{len(csv_rows)} rows staged for verification.',
        })

    return render_template(
        'build_schedule.html',
        locations=db.get_locations(),
        jobs=db.get_jobs(),
        team_members=db.get_team_members(),
    )

@app.route('/api/verify-preview', methods=['GET'])
@login_required
def verify_preview():
    """Get preview of uploaded CSV for verification"""
    csv_data = db.get_pending_upload(session['user_id'])
    if not csv_data:
        return jsonify({'error': 'No pending upload'}), 400
    
    # Enrich with lookups and detect changes
    enriched_data = []
    for idx, row in enumerate(csv_data):
        enriched_row = row.copy()
        
        # Lookup IDs
        location = db.get_location_by_name(row['location_name'])
        job = db.get_job_by_name(row['job_title'])
        team_member = db.get_team_member_by_name(row['employee_name'])
        
        enriched_row['location_id'] = location['square_location_id'] if location else None
        enriched_row['job_id'] = job['square_job_id'] if job else None
        enriched_row['team_member_id'] = team_member['square_team_member_id'] if team_member else None
        
        # Validate
        errors = []
        if not enriched_row['location_id']:
            errors.append(f"Location '{row['location_name']}' not found")
        if not enriched_row['job_id']:
            errors.append(f"Job '{row['job_title']}' not found")
        # team_member_id is optional (open shifts)
        
        enriched_row['errors'] = errors
        enriched_row['is_valid'] = len(errors) == 0
        enriched_row['row_number'] = idx + 2  # +2 for header and 1-indexing
        
        enriched_data.append(enriched_row)
    
    # Check for duplicates in this upload
    seen = {}
    for row in enriched_data:
        key = (row.get('location_id'), row.get('job_id'), row.get('team_member_id'), row['shift_date'], row['start_time'])
        if key in seen:
            row['errors'].append(f"Duplicate of row {seen[key]}")
            row['is_valid'] = False
        else:
            seen[key] = row['row_number']
    
    valid_count = sum(1 for r in enriched_data if r['is_valid'])
    
    return jsonify({
        'total_rows': len(enriched_data),
        'valid_rows': valid_count,
        'invalid_rows': len(enriched_data) - valid_count,
        'rows': enriched_data
    })

@app.route('/api/process-schedules', methods=['POST'])
@login_required
def process_schedules():
    """Process and publish schedules to Square"""
    csv_data = db.get_pending_upload(session['user_id'])
    if not csv_data:
        return jsonify({'error': 'No pending upload'}), 400

    data = request.json or {}
    if not data.get('approve'):
        return jsonify({'error': 'Approval required'}), 400

    upload_id = db.create_upload_record(len(csv_data), session.get('username'))
    
    results = []
    success_count = 0
    error_count = 0
    
    for idx, row in enumerate(csv_data):
        try:
            # Lookup IDs
            location = db.get_location_by_name(row['location_name'])
            job = db.get_job_by_name(row['job_title'])
            team_member = db.get_team_member_by_name(row['employee_name'])
            
            if not location or not job:
                raise ValueError(f"Invalid location or job")

            tz = (row.get('timezone_offset') or '').strip() or location.get('timezone') or '-04:00'

            # Build shift data
            shift_data = {
                'location_id': location['square_location_id'],
                'job_id': job['square_job_id'],
                'team_member_id': team_member['square_team_member_id'] if team_member else None,
                'employee_name': row.get('employee_name', 'Open Shift'),
                'date': row['shift_date'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'timezone': tz
            }
            
            # Call Square API to create and publish
            api_result = square.create_and_publish_shift(shift_data)
            
            if api_result['success']:
                success_count += 1
                # Store in database
                db.add_schedule(
                    upload_id,
                    api_result['shift_id'],
                    shift_data['location_id'],
                    shift_data['job_id'],
                    shift_data['team_member_id'],
                    shift_data['date'],
                    shift_data['start_time'],
                    shift_data['end_time']
                )
                results.append({
                    'row': idx + 2,
                    'status': 'SUCCESS',
                    'shift_id': api_result['shift_id'],
                    'message': 'Shift created and published'
                })
            else:
                error_count += 1
                results.append({
                    'row': idx + 2,
                    'status': 'ERROR',
                    'message': api_result.get('error', 'Unknown error')
                })
        except Exception as e:
            error_count += 1
            results.append({
                'row': idx + 2,
                'status': 'ERROR',
                'message': str(e)
            })
    
    # Update upload record
    db.update_upload_status(upload_id, 'COMPLETED', success_count, error_count)

    # Clear staged upload
    db.clear_pending_upload(session['user_id'])
    session.pop('upload_timestamp', None)
    
    return jsonify({
        'success': True,
        'upload_id': upload_id,
        'total_processed': len(csv_data),
        'success_count': success_count,
        'error_count': error_count,
        'results': results
    })

# ==================== HISTORY & AUDIT ====================

@app.route('/history')
@login_required
def history():
    """View upload history"""
    uploads = db.get_upload_history(limit=50)
    return render_template('history.html', uploads=uploads)

@app.route('/api/upload/<int:upload_id>', methods=['GET'])
@login_required
def get_upload_details(upload_id):
    """Get details of a specific upload"""
    upload = db.get_upload(upload_id)
    if not upload:
        return jsonify({'error': 'Upload not found'}), 404
    
    schedules = db.get_schedules_by_upload(upload_id)
    
    return jsonify({
        'upload': upload,
        'schedules': schedules
    })

@app.route('/api/changes', methods=['GET'])
@login_required
def detect_changes():
    """Detect changes in recent uploads"""
    # Compare last two uploads
    recent_uploads = db.get_recent_uploads(limit=2)
    
    if len(recent_uploads) < 2:
        return jsonify({'changes': []})
    
    upload1 = recent_uploads[0]
    upload2 = recent_uploads[1]
    
    schedules1 = db.get_schedules_by_upload(upload1['id'])
    schedules2 = db.get_schedules_by_upload(upload2['id'])
    
    changes = {
        'added': [],
        'removed': [],
        'modified': []
    }
    
    # Convert to sets for comparison
    set1 = {(s['date'], s['start_time'], s['location_id']) for s in schedules1}
    set2 = {(s['date'], s['start_time'], s['location_id']) for s in schedules2}
    
    # Find additions
    for s in schedules1:
        key = (s['date'], s['start_time'], s['location_id'])
        if key not in set2:
            changes['added'].append(s)
    
    # Find removals
    for s in schedules2:
        key = (s['date'], s['start_time'], s['location_id'])
        if key not in set1:
            changes['removed'].append(s)
    
    return jsonify(changes)

# ==================== ADMIN ====================

@app.route('/admin/users', methods=['GET', 'POST'])
@login_required
def manage_users():
    """Manage users"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    if request.method == 'POST':
        data = request.json
        action = data.get('action')
        
        if action == 'add':
            password_hash = hash_password(data['password'])
            db.add_user(data['username'], password_hash, data.get('is_admin', False))
            return jsonify({'success': True, 'message': 'User created'})
        elif action == 'delete':
            db.delete_user(data['id'])
            return jsonify({'success': True, 'message': 'User deleted'})
    
    users = db.get_all_users()
    return render_template('admin_users.html', users=users)

@app.route('/api/settings/square', methods=['GET', 'POST'])
@login_required
def square_settings():
    """Get/Update Square API settings"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    
    if request.method == 'POST':
        data = request.json
        os.environ['SQUARE_ACCESS_TOKEN'] = data['square_token']
        # Update in database as well
        db.set_setting('square_access_token', data['square_token'])
        return jsonify({'success': True, 'message': 'Settings updated'})
    
    token = db.get_setting('square_access_token')
    return jsonify({
        'square_token': token[:20] + '...' if token else 'Not set'
    })


@app.route('/api/settings/environment', methods=['POST'])
@login_required
def set_environment():
    """Admin-only: switch between sandbox and production at runtime.

    Persisted in the settings table; each Square API call re-reads the
    environment via _update_token() so the change is picked up immediately.
    """
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json or {}
    env = (data.get('environment') or '').lower()
    if env not in ('sandbox', 'production'):
        return jsonify({'error': "environment must be 'sandbox' or 'production'"}), 400

    db.set_setting('square_environment', env)
    os.environ['SQUARE_ENVIRONMENT'] = env

    from square_api import resolve_credentials
    creds = resolve_credentials()
    return jsonify({
        'success': True,
        'environment': creds['environment'],
        'token_configured': bool(creds['access_token']),
    })


@app.route('/admin/backup', methods=['GET'])
@login_required
def backup_page():
    """Admin-only backup & restore page."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    return render_template('admin_backup.html')


@app.route('/admin/backup/download', methods=['GET'])
@login_required
def backup_download():
    """Stream a consistent snapshot of the SQLite database as a download."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    from database import DB_PATH
    tmp = tempfile.NamedTemporaryFile(prefix='schedules-backup-', suffix='.db', delete=False)
    tmp.close()
    try:
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(tmp.name)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()

        filename = f"schedules-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        return send_file(
            tmp.name,
            mimetype='application/octet-stream',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return jsonify({'error': f'Backup failed: {e}'}), 500


@app.route('/admin/backup/restore', methods=['POST'])
@login_required
def backup_restore():
    """Replace the current SQLite database with an uploaded backup file."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    tmp = tempfile.NamedTemporaryFile(prefix='schedules-restore-', suffix='.db', delete=False)
    try:
        file.save(tmp.name)
        tmp.close()

        with open(tmp.name, 'rb') as f:
            header = f.read(16)
        if not header.startswith(b'SQLite format 3'):
            return jsonify({'error': 'File is not a valid SQLite database'}), 400

        try:
            check = sqlite3.connect(tmp.name)
            cursor = check.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            check.close()
        except sqlite3.DatabaseError as e:
            return jsonify({'error': f'Could not open backup: {e}'}), 400

        required_tables = {'users', 'locations', 'jobs', 'team_members', 'uploads', 'schedules', 'settings'}
        missing = required_tables - tables
        if missing:
            return jsonify({'error': f'Backup is missing expected tables: {", ".join(sorted(missing))}'}), 400

        from database import DB_PATH
        if os.path.exists(DB_PATH):
            shutil.copy2(DB_PATH, DB_PATH + '.pre-restore.bak')
        shutil.move(tmp.name, DB_PATH)

        return jsonify({'success': True, 'message': 'Database restored. Please log in again.'})
    except Exception as e:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return jsonify({'error': f'Restore failed: {e}'}), 500


@app.route('/api/health', methods=['GET'])
@login_required
def health():
    """Tiny health/status endpoint so testers can confirm the active env."""
    from square_api import resolve_credentials
    creds = resolve_credentials()
    return jsonify({
        'status': 'ok',
        'environment': creds['environment'],
        'token_configured': bool(creds['access_token']),
        'application_id_configured': bool(creds['application_id']),
    })

# ==================== ERROR HANDLERS ====================

@app.errorhandler(404)
def not_found(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

# ==================== INITIALIZATION ====================

if __name__ == '__main__':
    # Create default admin user if none exists
    if not db.get_all_users():
        admin_pass = hash_password('admin123')
        db.add_user('admin', admin_pass, is_admin=True)
        print("Default admin user created: admin / admin123")
        print("PLEASE CHANGE THIS PASSWORD IMMEDIATELY!")
    
    # Run app
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
