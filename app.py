"""
Square Schedule Manager
Main Flask Application
"""

from flask import (
    Flask, render_template, request, jsonify, session, redirect, url_for,
    Response, send_file, after_this_request,
)
from flask_cors import CORS
import os
import csv
import io
import logging
import secrets
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

# Import our custom modules
from database import Database, DB_PATH, REQUIRED_TABLES
from square_api import SquareAPI
from ollama_client import OllamaClient, DEFAULT_HOST as OLLAMA_DEFAULT_HOST, DEFAULT_MODEL as OLLAMA_DEFAULT_MODEL
import security
import timezones
from timezones import TIMEZONE_CHOICES
from scheduler import BackgroundScheduler

logging.basicConfig(
    level=os.environ.get('LOG_LEVEL', 'INFO'),
    format='%(asctime)s %(levelname)-7s %(name)s: %(message)s',
)
log = logging.getLogger('app')

# ---------------------------------------------------------------------------
# Secret key: fail closed on a missing or well-known placeholder value.
# ---------------------------------------------------------------------------
_BANNED_SECRET_KEYS = {
    '',
    'dev-secret-key-change-in-production',
    'change-this-secret-key-in-production',
    'changeme',
    'secret',
    'your-secret-key',
}
_secret_key = os.environ.get('SECRET_KEY', '').strip()
if _secret_key in _BANNED_SECRET_KEYS or len(_secret_key) < 32:
    raise RuntimeError(
        "SECRET_KEY is required, must not be a known placeholder, and must be "
        "at least 32 characters. Generate one with: "
        "python -c \"import secrets; print(secrets.token_hex(32))\" "
        "and pass it via --env-file or -e SECRET_KEY=..."
    )

app = Flask(__name__)
app.secret_key = _secret_key

# Session cookie hardening. Secure is on unless explicitly disabled for local
# HTTP testing (set SESSION_COOKIE_SECURE=0 when running without TLS).
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '1') != '0',
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=int(os.environ.get('MAX_CONTENT_LENGTH', 25 * 1024 * 1024)),
)
# CSRF is enforced unless disabled (tests set app.testing / this flag).
app.config['CSRF_ENABLED'] = os.environ.get('CSRF_ENABLED', '1') != '0'

# CORS: same-origin app; do not reflect arbitrary origins with credentials.
CORS(app, resources={r"/api/*": {"origins": os.environ.get('CORS_ORIGINS', '').split(',')
                                 if os.environ.get('CORS_ORIGINS') else []}})

# Initialize database and Square API
db = Database()
db.init_db()


def _apply_persisted_environment():
    """If admin previously toggled environment via the UI, propagate to env."""
    persisted = db.get_setting('square_environment')
    if persisted in ('sandbox', 'production'):
        os.environ['SQUARE_ENVIRONMENT'] = persisted


def _apply_persisted_tokens():
    """Load any admin-configured Square tokens from the settings table into the
    environment so they survive a container restart. Only fills in a token that
    isn't already provided via the environment (env wins)."""
    mapping = {
        'sandbox_access_token': 'SANDBOX_ACCESS_TOKEN',
        'production_access_token': 'PRODUCTION_ACCESS_TOKEN',
        'square_access_token': 'SQUARE_ACCESS_TOKEN',
    }
    for setting_key, env_var in mapping.items():
        if os.environ.get(env_var):
            continue
        stored = db.get_setting(setting_key)
        if stored:
            os.environ[env_var] = stored


_apply_persisted_environment()
_apply_persisted_tokens()
square = SquareAPI()

# Ensure uploads directory exists
UPLOAD_FOLDER = 'uploads'
Path(UPLOAD_FOLDER).mkdir(exist_ok=True)

# Where scheduled backups are written (inside the persisted data volume).
BACKUP_DIR = Path(os.environ.get('BACKUP_DIR', str(Path(DB_PATH).resolve().parent / 'backups')))


# ==================== CSRF + CONTEXT ====================

def _csrf_enabled():
    return app.config.get('CSRF_ENABLED', True) and not app.testing


@app.before_request
def _csrf_guard():
    if not _csrf_enabled():
        return
    if request.method in security.SAFE_METHODS:
        return
    if not security.validate_csrf(session, request):
        return jsonify({'error': 'CSRF token missing or invalid. Reload the page and retry.'}), 400


@app.context_processor
def inject_globals():
    """Expose Square environment, token-configured flag, and CSRF token to all
    templates."""
    from square_api import resolve_credentials
    creds = resolve_credentials()
    return {
        'square_environment': creds['environment'],
        'square_token_configured': bool(creds['access_token']),
        'csrf_token': security.ensure_csrf_token(session),
    }


# ==================== AUTHENTICATION ====================

def login_required(f):
    """Decorator to require login"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/') or request.is_json:
                return jsonify({'error': 'Authentication required'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


# Password helpers re-exported for tests/back-compat.
hash_password = security.hash_password
verify_password = security.verify_password


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password') or ''

        user = db.get_user(username)

        if user and verify_password(password, user['password_hash']):
            # Transparently upgrade legacy unsalted SHA-256 hashes.
            if security.needs_rehash(user['password_hash']):
                db.update_user_password(user['id'], security.hash_password(password))
            session.clear()
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['is_admin'] = bool(user['is_admin'])
            security.ensure_csrf_token(session)
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error='Invalid username or password')

    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    """Logout (POST + CSRF so it can't be triggered cross-site via an <img>)."""
    session.clear()
    return redirect(url_for('login'))


# ==================== HEALTH ====================

@app.route('/healthz', methods=['GET'])
def healthz():
    """Unauthenticated liveness probe for Docker/monitoring."""
    return jsonify({'status': 'ok'}), 200


@app.route('/api/health', methods=['GET'])
@login_required
def health():
    """Authenticated status endpoint so testers can confirm the active env."""
    from square_api import resolve_credentials
    creds = resolve_credentials()
    return jsonify({
        'status': 'ok',
        'environment': creds['environment'],
        'token_configured': bool(creds['access_token']),
        'application_id_configured': bool(creds['application_id']),
        'scheduler': scheduler.status(),
    })


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


@app.route('/settings/locations', methods=['GET', 'POST'])
@login_required
def locations():
    """Manage locations"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'POST':
        data = request.json or {}
        action = data.get('action')

        if action == 'add':
            db.add_location(
                data['name'],
                data['square_location_id'],
                timezone=data.get('timezone') or '-04:00',
                timezone_name=data.get('timezone_name') or _tz_name_from_choice(data.get('timezone')),
            )
            return jsonify({'success': True, 'message': 'Location added'})
        elif action == 'update':
            db.update_location(
                data['id'],
                data['name'],
                data['square_location_id'],
                timezone=data.get('timezone'),
                timezone_name=data.get('timezone_name'),
            )
            return jsonify({'success': True, 'message': 'Location updated'})
        elif action == 'delete':
            db.delete_location(data['id'])
            return jsonify({'success': True, 'message': 'Location deleted'})
        return jsonify({'error': 'Unknown action'}), 400

    locations_list = db.get_locations()
    return render_template(
        'settings_locations.html',
        locations=locations_list,
        timezone_choices=TIMEZONE_CHOICES,
    )


def _tz_name_from_choice(value):
    """Accept either an IANA name or a legacy offset from the UI."""
    if value in timezones.VALID_ZONE_NAMES:
        return value
    return None


@app.route('/settings/locations/sync', methods=['POST'])
@login_required
def locations_sync():
    """Pull all locations from Square and replace the local locations table,
    importing each location's real IANA timezone so shifts stay DST-correct."""
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
        tz_name = (loc.get('timezone') or '').strip() or None
        # Compute a current fallback offset from the IANA name for legacy use.
        offset = None
        if tz_name:
            offset = timezones.offset_for_date(tz_name, datetime.utcnow().date())
        rows.append((name, lid, offset or '-04:00', tz_name))

    if not rows:
        return jsonify({'error': 'Square returned no locations. Existing list was not modified.'}), 400

    db.replace_locations(rows)
    imported_tz = sum(1 for r in rows if r[3])

    return jsonify({
        'success': True,
        'imported': len(rows),
        'timezones_imported': imported_tz,
        'message': (
            f'Replaced local locations with {len(rows)} entries from Square. '
            f'{imported_tz} had a timezone imported (DST-aware).'
        ),
    })


@app.route('/settings/jobs', methods=['GET', 'POST'])
@login_required
def jobs():
    """Manage jobs"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'POST':
        data = request.json or {}
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
        return jsonify({'error': 'Unknown action'}), 400

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
        data = request.json or {}
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
        return jsonify({'error': 'Unknown action'}), 400

    team_members_list = db.get_team_members()
    return render_template('settings_team_members.html', team_members=team_members_list)


def _sync_team_members_from_square():
    """Shared logic: pull ACTIVE team members from Square and replace the local
    table. Returns (payload_dict, status_code)."""
    result = square.list_team_members(status='ACTIVE')
    if not result.get('success'):
        return {'error': f'Could not fetch team members from Square: {result.get("error")}'}, 502

    rows = []
    skipped = 0
    for m in result['team_members']:
        given = (m.get('given_name') or '').strip()
        family = (m.get('family_name') or '').strip()
        name = f'{given} {family}'.strip()
        mid = m.get('id')
        if not name or not mid:
            skipped += 1
            continue
        rows.append((name, mid))

    if not rows:
        return {'error': 'Square returned no active team members. Existing list was not modified.'}, 400

    db.replace_team_members(rows)
    return {
        'success': True,
        'imported': len(rows),
        'skipped': skipped,
        'message': f'Replaced team members with {len(rows)} active entries from Square.',
    }, 200


@app.route('/settings/team-members/sync', methods=['POST'])
@login_required
def team_members_sync():
    """Direct API sync — no CSV round-trip through the Square dashboard."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    payload, status = _sync_team_members_from_square()
    return jsonify(payload), status


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

REQUIRED_COLUMNS = ['employee_name', 'job_title', 'location_name', 'shift_date', 'start_time', 'end_time']


def _parse_schedule_csv(text):
    """Parse schedule CSV text into a list of stripped-cell row dicts.

    Returns (rows, error). error is a user-facing string when parsing fails.
    """
    reader = csv.DictReader(text.splitlines())
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        return None, f"Missing required column(s): {', '.join(missing)}"

    rows = []
    for row in reader:
        rows.append({
            (k.strip() if k else k): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
        })
    if not rows:
        return None, 'CSV is empty or has no data rows'
    return rows, None


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

        if not file.filename.lower().endswith('.csv'):
            return jsonify({'error': 'File must be CSV'}), 400

        try:
            # Decode with utf-8-sig to swallow any BOM Excel prepends.
            stream = file.stream.read().decode("utf-8-sig")
            csv_data, error = _parse_schedule_csv(stream)
            if error:
                return jsonify({'error': error}), 400

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
            tz = l.get('timezone_name') or l.get('timezone') or '-04:00'
            lines.append(f"| {l['name']} | {tz} |")
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


def _read_uploaded_text(file):
    """Decode an uploaded messy-schedule file to text, converting .xlsx to CSV.

    Returns (text, error)."""
    name = (file.filename or '').lower()
    if name.endswith('.xlsx'):
        try:
            from openpyxl import load_workbook
        except ImportError:
            return None, ('.xlsx support needs the optional openpyxl package. '
                          'Install it, or save the sheet as CSV first.')
        try:
            wb = load_workbook(io.BytesIO(file.stream.read()), read_only=True, data_only=True)
            sheet = wb.active
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for row in sheet.iter_rows(values_only=True):
                writer.writerow(['' if c is None else c for c in row])
            return buffer.getvalue(), None
        except Exception as e:
            return None, f'Could not read .xlsx: {e}'

    raw = file.stream.read()
    try:
        return raw.decode('utf-8-sig'), None
    except UnicodeDecodeError:
        try:
            return raw.decode('latin-1'), None
        except Exception as e:
            return None, f'Could not decode file: {e}'


@app.route('/admin/ai-convert/upload', methods=['POST'])
@login_required
def ai_convert_upload():
    """Send an uploaded messy schedule file to Ollama and return its CSV output."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400
    allowed = ('.csv', '.tsv', '.txt', '.xlsx')
    if not file.filename.lower().endswith(allowed):
        return jsonify({'error': f'Only {", ".join(allowed)} files are accepted.'}), 400

    text, error = _read_uploaded_text(file)
    if error:
        return jsonify({'error': error}), 400

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
        rows, error = _parse_schedule_csv(csv_text)
        if error:
            return jsonify({'error': error}), 400

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
    """Manually compose a schedule from master data, then stage it for verification."""
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

    # Provide a location->current-offset map for the builder UI.
    locs = db.get_locations()
    tz_map = {}
    for l in locs:
        offset, _ = timezones.resolve_offset(
            datetime.utcnow().strftime('%Y-%m-%d'), None, l
        )
        tz_map[l['name']] = offset
    return render_template(
        'build_schedule.html',
        locations=locs,
        jobs=db.get_jobs(),
        team_members=db.get_team_members(),
        location_timezones=tz_map,
    )


def _enrich_rows(csv_data):
    """Attach lookup IDs, validation errors, and warnings to each staged row."""
    enriched_data = []
    for idx, row in enumerate(csv_data):
        enriched_row = dict(row)

        location = db.get_location_by_name(row.get('location_name'))
        job = db.get_job_by_name(row.get('job_title'))
        employee_name = (row.get('employee_name') or '').strip()
        team_member = db.get_team_member_by_name(employee_name) if employee_name else None

        enriched_row['location_id'] = location['square_location_id'] if location else None
        enriched_row['job_id'] = job['square_job_id'] if job else None
        enriched_row['team_member_id'] = team_member['square_team_member_id'] if team_member else None

        errors = []
        warnings = []
        if not enriched_row['location_id']:
            errors.append(f"Location '{row.get('location_name')}' not found")
        if not enriched_row['job_id']:
            errors.append(f"Job '{row.get('job_title')}' not found")
        # A NAMED employee that doesn't match is an error, not a silent open shift.
        if employee_name and not team_member:
            errors.append(
                f"Employee '{employee_name}' not found — fix the name or clear it for an open shift"
            )
        # Time sanity.
        if not _valid_time(row.get('start_time')) or not _valid_time(row.get('end_time')):
            errors.append("start_time/end_time must be HH:MM (24-hour)")
        elif row.get('start_time') >= row.get('end_time'):
            warnings.append("end_time is not after start_time (overnight shift?)")
        if not _valid_date(row.get('shift_date')):
            errors.append("shift_date must be YYYY-MM-DD")

        enriched_row['errors'] = errors
        enriched_row['warnings'] = warnings
        enriched_row['is_valid'] = len(errors) == 0
        enriched_row['is_open_shift'] = not employee_name
        enriched_row['row_number'] = idx + 2

        enriched_data.append(enriched_row)

    # Duplicate detection within this upload.
    seen = {}
    for row in enriched_data:
        key = (row.get('location_id'), row.get('job_id'), row.get('team_member_id'),
               row.get('shift_date'), row.get('start_time'))
        if key in seen:
            row['errors'].append(f"Duplicate of row {seen[key]}")
            row['is_valid'] = False
        else:
            seen[key] = row['row_number']
    return enriched_data


def _valid_time(value):
    if not value or not isinstance(value, str):
        return False
    parts = value.split(':')
    if len(parts) != 2:
        return False
    try:
        h, m = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return 0 <= h <= 23 and 0 <= m <= 59


def _valid_date(value):
    if not value or not isinstance(value, str):
        return False
    try:
        datetime.strptime(value.strip(), '%Y-%m-%d')
        return True
    except ValueError:
        return False


@app.route('/api/verify-preview', methods=['GET'])
@login_required
def verify_preview():
    """Get preview of uploaded CSV for verification"""
    csv_data = db.get_pending_upload(session['user_id'])
    if not csv_data:
        return jsonify({'error': 'No pending upload'}), 400

    enriched_data = _enrich_rows(csv_data)
    valid_count = sum(1 for r in enriched_data if r['is_valid'])

    return jsonify({
        'total_rows': len(enriched_data),
        'valid_rows': valid_count,
        'invalid_rows': len(enriched_data) - valid_count,
        'rows': enriched_data
    })


@app.route('/api/verify-preview/duplicates', methods=['POST'])
@login_required
def verify_duplicates():
    """Cross-check staged rows against shifts already in Square for the same
    date range, so the user can avoid re-publishing duplicates. Optional step —
    the verify page calls it on demand."""
    csv_data = db.get_pending_upload(session['user_id'])
    if not csv_data:
        return jsonify({'error': 'No pending upload'}), 400

    enriched = _enrich_rows(csv_data)
    dates = [r.get('shift_date') for r in enriched if _valid_date(r.get('shift_date'))]
    if not dates:
        return jsonify({'success': True, 'duplicates': []})

    start_at = f'{min(dates)}T00:00:00+14:00'
    end_at = f'{max(dates)}T23:59:59-14:00'
    result = square.search_scheduled_shifts(start_at=start_at, end_at=end_at)
    if not result.get('success'):
        return jsonify({'error': result.get('error')}), 502

    existing = set()
    for s in result['scheduled_shifts']:
        details = s.get('draft_shift_details') or s.get('shift_details') or {}
        start = (details.get('start_at') or '')[:16]  # YYYY-MM-DDTHH:MM
        existing.add((details.get('location_id'), details.get('job_id'),
                      details.get('team_member_id'), start))

    duplicates = []
    for r in enriched:
        if not r['is_valid']:
            continue
        start = f"{r.get('shift_date')}T{r.get('start_time')}"
        key = (r.get('location_id'), r.get('job_id'), r.get('team_member_id'), start)
        if key in existing:
            duplicates.append(r['row_number'])
    return jsonify({'success': True, 'duplicates': duplicates})


def _process_rows(csv_data, username, source='upload'):
    """Shared publish routine used by both the normal approve flow and retry.

    Skips rows that fail validation (they are recorded as ERROR, never sent to
    Square as a silent open shift), snapshots credentials for the whole batch,
    records every per-row result, and tracks orphaned drafts on publish
    failure. Returns the results payload dict.
    """
    enriched = _enrich_rows(csv_data)
    upload_id = db.create_upload_record(len(enriched), username)

    results = []
    success_count = 0
    error_count = 0

    with square.frozen_credentials():
        for row in enriched:
            row_number = row['row_number']
            if not row['is_valid']:
                error_count += 1
                message = '; '.join(row['errors']) or 'Invalid row'
                db.add_upload_result(upload_id, row_number, 'ERROR', None, message, row)
                results.append({'row': row_number, 'status': 'ERROR', 'message': message})
                continue

            offset, tz_warning = timezones.resolve_offset(
                row.get('shift_date'),
                row.get('timezone_offset'),
                db.get_location_by_name(row.get('location_name')),
            )
            shift_data = {
                'location_id': row['location_id'],
                'job_id': row['job_id'],
                'team_member_id': row.get('team_member_id'),
                'employee_name': row.get('employee_name') or 'Open Shift',
                'date': row.get('shift_date'),
                'start_time': row.get('start_time'),
                'end_time': row.get('end_time'),
                'timezone': offset,
            }

            try:
                api_result = square.create_and_publish_shift(shift_data)
            except Exception as e:  # defensive: never let one row abort the batch
                api_result = {'success': False, 'error': f'Unexpected error: {e}'}

            if api_result.get('success'):
                success_count += 1
                db.add_schedule(
                    upload_id, api_result['shift_id'], shift_data['location_id'],
                    shift_data['job_id'], shift_data['team_member_id'],
                    shift_data['date'], shift_data['start_time'], shift_data['end_time'],
                )
                msg = 'Shift created and published'
                if tz_warning:
                    msg += f' ({tz_warning})'
                db.add_upload_result(upload_id, row_number, 'SUCCESS',
                                     api_result['shift_id'], msg, row)
                results.append({'row': row_number, 'status': 'SUCCESS',
                                'shift_id': api_result['shift_id'], 'message': msg})
            else:
                error_count += 1
                orphan_id = api_result.get('shift_id')  # set when publish failed post-create
                message = api_result.get('error', 'Unknown error')
                if api_result.get('step') == 'PUBLISH' and orphan_id:
                    # Record the orphaned draft so it isn't lost and a retry can
                    # dedupe (idempotency key is content-based).
                    db.add_schedule(
                        upload_id, orphan_id, shift_data['location_id'],
                        shift_data['job_id'], shift_data['team_member_id'],
                        shift_data['date'], shift_data['start_time'], shift_data['end_time'],
                    )
                    message = f'Created but publish failed (draft {orphan_id}): {message}'
                db.add_upload_result(upload_id, row_number, 'ERROR', orphan_id, message, row)
                results.append({'row': row_number, 'status': 'ERROR',
                                'shift_id': orphan_id, 'message': message})

    if error_count == 0:
        status = 'COMPLETED'
    elif success_count == 0:
        status = 'FAILED'
    else:
        status = 'PARTIAL'
    db.update_upload_status(upload_id, status, success_count, error_count)

    return {
        'success': True,
        'upload_id': upload_id,
        'status': status,
        'total_processed': len(enriched),
        'success_count': success_count,
        'error_count': error_count,
        'results': results,
    }


@app.route('/api/process-schedules', methods=['POST'])
@login_required
def process_schedules():
    """Process and publish schedules to Square."""
    data = request.json or {}
    if not data.get('approve'):
        return jsonify({'error': 'Approval required'}), 400

    # Atomically claim the staged rows. A second concurrent/duplicate click
    # gets nothing back and is rejected, so the batch can't be published twice
    # (the claim's DELETE-rowcount guard makes this safe across processes).
    csv_data = db.claim_pending_upload(session['user_id'])
    if not csv_data:
        return jsonify({'error': 'No pending upload'}), 400
    session.pop('upload_timestamp', None)

    try:
        payload = _process_rows(csv_data, session.get('username'), source='upload')
    except Exception:
        # Infrastructure failure around the batch (e.g. DB error): re-stage the
        # claimed rows so the work isn't lost. Content-based idempotency keys
        # make a later retry safe against double-publishing.
        db.set_pending_upload(session['user_id'], csv_data)
        raise
    return jsonify(payload)


# ==================== CURRENT SCHEDULE (LIVE FROM SQUARE) ====================

@app.route('/schedule', methods=['GET'])
@login_required
def schedule_view():
    """Live view of scheduled shifts in Square, cross-referenced with local
    records so users see which shifts came from this app vs elsewhere."""
    return render_template('schedule.html', locations=db.get_locations())


@app.route('/api/schedule/fetch', methods=['POST'])
@login_required
def schedule_fetch():
    """Pull scheduled shifts from Square for the given date range."""
    data = request.get_json(silent=True) or {}
    start_date = (data.get('start_date') or '').strip()
    end_date = (data.get('end_date') or '').strip()
    location_id = (data.get('location_id') or '').strip()
    if not _valid_date(start_date) or not _valid_date(end_date):
        return jsonify({'error': 'start_date and end_date are required (YYYY-MM-DD)'}), 400

    # Widen the window to cover every timezone so a shift late on the last
    # local day of the range is never cut off by a UTC 'Z' boundary. The
    # earliest a local start-of-day occurs is in UTC+14; the latest a local
    # end-of-day occurs is in UTC-14 — so start uses +14:00 and end uses -14:00
    # (using the opposite signs would produce a too-narrow, inverted window).
    start_at = f'{start_date}T00:00:00+14:00'
    end_at = f'{end_date}T23:59:59-14:00'

    location_ids = [location_id] if location_id else None
    result = square.search_scheduled_shifts(
        location_ids=location_ids,
        start_at=start_at,
        end_at=end_at,
    )
    if not result.get('success'):
        return jsonify({'error': result.get('error')}), 502

    locs_by_id = {l['square_location_id']: l['name'] for l in db.get_locations()}
    jobs_by_id = {j['square_job_id']: j['name'] for j in db.get_jobs()}
    members_by_id = {m['square_team_member_id']: m['name'] for m in db.get_team_members()}
    local_shift_ids = db.get_all_shift_ids()

    rows = []
    for s in result['scheduled_shifts']:
        details = s.get('draft_shift_details') or s.get('shift_details') or {}
        sid = s.get('id')
        location_sid = details.get('location_id')
        job_sid = details.get('job_id')
        member_sid = details.get('team_member_id')
        rows.append({
            'square_shift_id': sid,
            'start_at': details.get('start_at'),
            'end_at': details.get('end_at'),
            'timezone': details.get('timezone'),
            'location_id': location_sid,
            'location_name': locs_by_id.get(location_sid, '(unknown)'),
            'job_id': job_sid,
            'job_title': jobs_by_id.get(job_sid, '(unknown)'),
            'team_member_id': member_sid,
            'team_member_name': members_by_id.get(member_sid, '(open / unknown)') if member_sid else '(open)',
            'source': 'App' if sid in local_shift_ids else 'External',
            'is_deleted': details.get('is_deleted', False),
            'version': s.get('version'),
        })

    rows.sort(key=lambda r: r.get('start_at') or '')

    return jsonify({
        'success': True,
        'count': len(rows),
        'app_count': sum(1 for r in rows if r['source'] == 'App'),
        'external_count': sum(1 for r in rows if r['source'] == 'External'),
        'shifts': rows,
    })


# ==================== HISTORY & AUDIT ====================

@app.route('/history')
@login_required
def history():
    """View upload history"""
    uploads = db.get_upload_history(limit=50)
    return render_template('history.html', uploads=uploads)


def _load_owned_upload(upload_id):
    """Fetch an upload the current user is allowed to see.

    Returns (upload, None) on success or (None, (response, status)) if the
    upload is missing or belongs to another (non-admin) user. Non-admins can
    only touch uploads they created; admins can see all. This keeps one user's
    staff PII (and their failed rows) from being read, exported, or re-published
    by another logged-in user.
    """
    upload = db.get_upload(upload_id)
    if not upload:
        return None, (jsonify({'error': 'Upload not found'}), 404)
    if not session.get('is_admin') and upload.get('uploaded_by') != session.get('username'):
        return None, (jsonify({'error': 'Unauthorized'}), 403)
    return upload, None


@app.route('/api/upload/<int:upload_id>', methods=['GET'])
@login_required
def get_upload_details(upload_id):
    """Get details of a specific upload (including per-row results)."""
    upload, error = _load_owned_upload(upload_id)
    if error:
        return error

    return jsonify({
        'upload': upload,
        'schedules': db.get_schedules_by_upload(upload_id),
        'results': db.get_upload_results(upload_id),
    })


@app.route('/api/upload/<int:upload_id>/report.csv', methods=['GET'])
@login_required
def upload_report(upload_id):
    """Download a per-row CSV report for an upload."""
    upload, error = _load_owned_upload(upload_id)
    if error:
        return error

    results = db.get_upload_results(upload_id)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['row_number', 'status', 'square_shift_id', 'message',
                     'employee_name', 'job_title', 'location_name',
                     'shift_date', 'start_time', 'end_time'])
    for r in results:
        row_data = r.get('row_data') or {}
        writer.writerow([
            r.get('row_number'), r.get('status'), r.get('square_shift_id') or '',
            r.get('message') or '', row_data.get('employee_name', ''),
            row_data.get('job_title', ''), row_data.get('location_name', ''),
            row_data.get('shift_date', ''), row_data.get('start_time', ''),
            row_data.get('end_time', ''),
        ])
    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="upload-{upload_id}-report.csv"'},
    )


@app.route('/api/upload/<int:upload_id>/retry-failed', methods=['POST'])
@login_required
def retry_failed(upload_id):
    """Re-stage just the failed rows of a previous upload for another attempt."""
    upload, error = _load_owned_upload(upload_id)
    if error:
        return error

    failed_rows = db.get_failed_rows(upload_id)
    if not failed_rows:
        return jsonify({'error': 'No failed rows with saved data to retry.'}), 400

    # Strip the enrichment fields back to the raw staged shape.
    clean = []
    for r in failed_rows:
        clean.append({
            'employee_name': r.get('employee_name', ''),
            'job_title': r.get('job_title', ''),
            'location_name': r.get('location_name', ''),
            'shift_date': r.get('shift_date', ''),
            'start_time': r.get('start_time', ''),
            'end_time': r.get('end_time', ''),
            'timezone_offset': r.get('timezone_offset', ''),
        })

    db.set_pending_upload(session['user_id'], clean)
    session['upload_timestamp'] = datetime.now().isoformat()
    return jsonify({
        'success': True,
        'row_count': len(clean),
        'redirect': url_for('upload') + '?staged=1',
        'message': f'{len(clean)} failed row(s) re-staged for verification.',
    })


@app.route('/api/changes', methods=['GET'])
@login_required
def detect_changes():
    """Detect changes between the last two completed uploads."""
    recent_uploads = db.get_recent_uploads(limit=2)

    if len(recent_uploads) < 2:
        return jsonify({'changes': {'added': [], 'removed': []}})

    upload1, upload2 = recent_uploads[0], recent_uploads[1]
    schedules1 = db.get_schedules_by_upload(upload1['id'])
    schedules2 = db.get_schedules_by_upload(upload2['id'])

    changes = {'added': [], 'removed': []}
    set1 = {(s['shift_date'], s['start_time'], s['location_id']) for s in schedules1}
    set2 = {(s['shift_date'], s['start_time'], s['location_id']) for s in schedules2}

    for s in schedules1:
        if (s['shift_date'], s['start_time'], s['location_id']) not in set2:
            changes['added'].append(s)
    for s in schedules2:
        if (s['shift_date'], s['start_time'], s['location_id']) not in set1:
            changes['removed'].append(s)

    return jsonify({'changes': changes})


# ==================== ADMIN ====================

@app.route('/admin/users', methods=['GET', 'POST'])
@login_required
def manage_users():
    """Manage users"""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    if request.method == 'POST':
        data = request.json or {}
        action = data.get('action')

        if action == 'add':
            username = (data.get('username') or '').strip()
            password = data.get('password') or ''
            if not username or len(password) < 8:
                return jsonify({'error': 'Username required and password must be at least 8 characters'}), 400
            db.add_user(username, security.hash_password(password), bool(data.get('is_admin', False)))
            return jsonify({'success': True, 'message': 'User created'})
        elif action == 'delete':
            target = db.get_user_by_id(data.get('id'))
            if not target:
                return jsonify({'error': 'User not found'}), 404
            if target['id'] == session['user_id']:
                return jsonify({'error': 'You cannot delete your own account'}), 400
            if target['is_admin'] and db.count_admins() <= 1:
                return jsonify({'error': 'Cannot delete the last admin'}), 400
            db.delete_user(target['id'])
            return jsonify({'success': True, 'message': 'User deleted'})
        elif action == 'reset_password':
            target = db.get_user_by_id(data.get('id'))
            if not target:
                return jsonify({'error': 'User not found'}), 404
            new_password = data.get('password') or ''
            if len(new_password) < 8:
                return jsonify({'error': 'Password must be at least 8 characters'}), 400
            db.update_user_password(target['id'], security.hash_password(new_password))
            return jsonify({'success': True, 'message': 'Password reset'})
        return jsonify({'error': 'Unknown action'}), 400

    users = db.get_all_users()
    return render_template('admin_users.html', users=users)


@app.route('/account', methods=['GET'])
@login_required
def account():
    """Self-service account page (password change)."""
    return render_template('account.html')


@app.route('/api/account/password', methods=['POST'])
@login_required
def change_password():
    """Self-service password change for the logged-in user."""
    data = request.json or {}
    current = data.get('current_password') or ''
    new_password = data.get('new_password') or ''
    if len(new_password) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    user = db.get_user_by_id(session['user_id'])
    if not user or not verify_password(current, user['password_hash']):
        return jsonify({'error': 'Current password is incorrect'}), 400

    db.update_user_password(user['id'], security.hash_password(new_password))
    return jsonify({'success': True, 'message': 'Password changed'})


@app.route('/api/settings/square', methods=['GET', 'POST'])
@login_required
def square_settings():
    """Get/Update Square API settings.

    The token is persisted per-environment in the settings table AND pushed to
    the environment, so it survives a container restart (loaded at startup by
    _apply_persisted_tokens)."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    from square_api import get_environment
    env = get_environment()
    setting_key = 'production_access_token' if env == 'production' else 'sandbox_access_token'
    env_var = 'PRODUCTION_ACCESS_TOKEN' if env == 'production' else 'SANDBOX_ACCESS_TOKEN'

    if request.method == 'POST':
        data = request.json or {}
        token = (data.get('square_token') or '').strip()
        if not token:
            return jsonify({'error': 'Token must not be empty'}), 400
        os.environ[env_var] = token
        db.set_setting(setting_key, token)
        square._update_token()
        return jsonify({'success': True, 'environment': env,
                        'message': f'{env} token updated'})

    token = os.environ.get(env_var) or db.get_setting(setting_key) or ''
    return jsonify({
        'environment': env,
        'configured': bool(token),
        'hint': ('****' + token[-4:]) if len(token) >= 4 else '',
    })


@app.route('/api/settings/environment', methods=['POST'])
@login_required
def set_environment():
    """Admin-only: switch between sandbox and production at runtime."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    data = request.json or {}
    env = (data.get('environment') or '').lower()
    if env not in ('sandbox', 'production'):
        return jsonify({'error': "environment must be 'sandbox' or 'production'"}), 400

    db.set_setting('square_environment', env)
    os.environ['SQUARE_ENVIRONMENT'] = env
    square._update_token()

    from square_api import resolve_credentials
    creds = resolve_credentials()
    return jsonify({
        'success': True,
        'environment': creds['environment'],
        'token_configured': bool(creds['access_token']),
    })


# ==================== BACKUP & RESTORE ====================

@app.route('/admin/backup', methods=['GET'])
@login_required
def backup_page():
    """Admin-only backup & restore page."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403
    return render_template('admin_backup.html')


def _make_backup(dest_path):
    """Write a consistent snapshot of the live DB to dest_path."""
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(str(dest_path))
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()


@app.route('/admin/backup/download', methods=['GET'])
@login_required
def backup_download():
    """Stream a consistent snapshot of the SQLite database as a download."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    tmp = tempfile.NamedTemporaryFile(prefix='schedules-backup-', suffix='.db', delete=False)
    tmp.close()

    @after_this_request
    def _cleanup(response):
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return response

    try:
        _make_backup(tmp.name)
    except Exception as e:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return jsonify({'error': f'Backup failed: {e}'}), 500

    filename = f"schedules-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    return send_file(
        tmp.name,
        mimetype='application/octet-stream',
        as_attachment=True,
        download_name=filename,
    )


@app.route('/admin/backup/restore', methods=['POST'])
@login_required
def backup_restore():
    """Replace the current SQLite database with an uploaded backup file."""
    if not session.get('is_admin'):
        return jsonify({'error': 'Unauthorized'}), 403

    # A database backup can legitimately be larger than the modest cap applied
    # to CSV/xlsx uploads, so lift the per-request limit here — otherwise a
    # backup you could download might be too big to restore.
    request.max_content_length = None

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected'}), 400

    tmp = tempfile.NamedTemporaryFile(prefix='schedules-restore-', suffix='.db', delete=False)
    tmp_name = tmp.name
    tmp.close()
    try:
        file.save(tmp_name)

        with open(tmp_name, 'rb') as f:
            header = f.read(16)
        if not header.startswith(b'SQLite format 3'):
            return jsonify({'error': 'File is not a valid SQLite database'}), 400

        try:
            check = sqlite3.connect(tmp_name)
            cursor = check.cursor()
            # Integrity check catches truncated/corrupt uploads before we swap.
            integrity = cursor.execute('PRAGMA integrity_check').fetchone()
            if not integrity or integrity[0] != 'ok':
                check.close()
                return jsonify({'error': 'Backup failed integrity check'}), 400
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}
            check.close()
        except sqlite3.DatabaseError as e:
            return jsonify({'error': f'Could not open backup: {e}'}), 400

        missing = REQUIRED_TABLES - tables
        if missing:
            return jsonify({'error': f'Backup is missing expected tables: {", ".join(sorted(missing))}'}), 400

        # Snapshot current DB, then swap atomically via os.replace.
        if os.path.exists(DB_PATH):
            try:
                _make_backup(DB_PATH + '.pre-restore.bak')
            except Exception:
                shutil.copy2(DB_PATH, DB_PATH + '.pre-restore.bak')
        os.replace(tmp_name, DB_PATH)
        tmp_name = None  # consumed by os.replace
        # Drop stale WAL/SHM sidecars from the previous database so SQLite
        # doesn't try to replay the old write-ahead log against the new file.
        for sidecar in (DB_PATH + '-wal', DB_PATH + '-shm'):
            if os.path.exists(sidecar):
                try:
                    os.unlink(sidecar)
                except OSError:
                    pass

        # Bring the restored DB up to the current schema (older backups may
        # predate a migration) so the app keeps working without a restart.
        try:
            db.init_db()
        except Exception as e:
            return jsonify({'error': f'Restored but migration failed: {e}'}), 500

        return jsonify({'success': True, 'message': 'Database restored. Please log in again.'})
    except Exception as e:
        return jsonify({'error': f'Restore failed: {e}'}), 500
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


# ==================== SCHEDULED BACKGROUND JOBS ====================

scheduler = BackgroundScheduler()


def _scheduled_sync():
    """Refresh master data from Square if a token is configured."""
    if square.token_missing():
        return
    with app.app_context():
        loc = square.list_locations()
        if loc.get('success') and loc.get('locations'):
            rows = []
            for l in loc['locations']:
                name = (l.get('name') or '').strip()
                lid = l.get('id')
                if not name or not lid:
                    continue
                tz_name = (l.get('timezone') or '').strip() or None
                offset = timezones.offset_for_date(tz_name, datetime.utcnow().date()) if tz_name else None
                rows.append((name, lid, offset or '-04:00', tz_name))
            if rows:
                db.replace_locations(rows)
        jobs_result = square.list_jobs()
        if jobs_result.get('success') and jobs_result.get('jobs'):
            rows = [((j.get('title') or '').strip(), j.get('id'))
                    for j in jobs_result['jobs'] if (j.get('title') or '').strip() and j.get('id')]
            if rows:
                db.replace_jobs(rows)
        _sync_team_members_from_square()
        db.set_setting('last_auto_sync', datetime.utcnow().isoformat())
        log.info('scheduled master-data sync complete')


def _scheduled_backup():
    """Write a dated DB snapshot into the backup directory, keeping the newest N."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    dest = BACKUP_DIR / f'schedules-{stamp}.db'
    _make_backup(dest)
    retain = int(os.environ.get('BACKUP_RETAIN', 14))
    backups = sorted(BACKUP_DIR.glob('schedules-*.db'), reverse=True)
    for old in backups[retain:]:
        try:
            old.unlink()
        except OSError:
            pass
    log.info('scheduled backup written: %s', dest)


def _configure_scheduler():
    """Register background jobs based on env config. Off by default."""
    sync_hours = float(os.environ.get('AUTO_SYNC_HOURS', 0) or 0)
    backup_hours = float(os.environ.get('AUTO_BACKUP_HOURS', 0) or 0)
    if (sync_hours > 0 or backup_hours > 0):
        try:
            workers = int(os.environ.get('GUNICORN_WORKERS', '1') or '1')
        except ValueError:
            workers = 1
        if workers > 1:
            log.warning(
                'Auto sync/backup jobs are enabled but GUNICORN_WORKERS=%d: each '
                'worker runs its own scheduler, so jobs will run %d times. Set '
                'GUNICORN_WORKERS=1 to run them once.', workers, workers,
            )
    if sync_hours > 0:
        scheduler.add_job('master-data-sync', sync_hours * 3600, _scheduled_sync)
        log.info('auto master-data sync enabled every %.1fh', sync_hours)
    if backup_hours > 0:
        scheduler.add_job('database-backup', backup_hours * 3600, _scheduled_backup)
        log.info('auto backup enabled every %.1fh', backup_hours)
    scheduler.start()


# ==================== ERROR HANDLERS ====================

@app.errorhandler(sqlite3.IntegrityError)
def handle_integrity_error(error):
    """A duplicate/constraint violation should be a clean JSON 409, not an
    HTML 500 that breaks the frontend's res.json()."""
    log.info('integrity error: %s', error)
    return jsonify({'error': 'That record conflicts with an existing one '
                             '(duplicate name or ID).'}), 409


@app.errorhandler(413)
def too_large(error):
    return jsonify({'error': 'Uploaded file is too large.'}), 413


@app.errorhandler(404)
def not_found(error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Not found'}), 404
    return render_template('404.html'), 404


@app.errorhandler(500)
def internal_error(error):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Internal server error'}), 500
    return render_template('500.html'), 500


# ==================== INITIALIZATION ====================

def _bootstrap_admin():
    """Create an initial admin if none exists.

    Uses INITIAL_ADMIN_PASSWORD if provided, else generates a random password
    and logs it once. Never ships a hardcoded default credential.
    """
    if db.get_all_users():
        return
    username = os.environ.get('INITIAL_ADMIN_USERNAME', 'admin')
    password = os.environ.get('INITIAL_ADMIN_PASSWORD')
    generated = False
    if not password:
        password = secrets.token_urlsafe(16)
        generated = True
    db.add_user(username, security.hash_password(password), is_admin=True)
    if generated:
        log.warning('=' * 68)
        log.warning('Initial admin created: username=%s', username)
        log.warning('Generated password (shown once): %s', password)
        log.warning('Log in and change it immediately via Account settings.')
        log.warning('=' * 68)
    else:
        log.info('Initial admin %r created from INITIAL_ADMIN_PASSWORD.', username)


if __name__ == '__main__':
    _bootstrap_admin()
    _configure_scheduler()
    debug_mode = os.environ.get('FLASK_ENV') == 'development'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
