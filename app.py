"""
Square Schedule Manager
Main Flask Application
"""

from flask import Flask, render_template, request, jsonify, session, redirect, url_for, Response
from flask_cors import CORS
import os
import csv
import io
import json
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
import hashlib

# Import our custom modules
from database import Database
from square_api import SquareAPI

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
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
            db.add_location(data['name'], data['square_location_id'])
            return jsonify({'success': True, 'message': 'Location added'})
        elif action == 'update':
            db.update_location(data['id'], data['name'], data['square_location_id'])
            return jsonify({'success': True, 'message': 'Location updated'})
        elif action == 'delete':
            db.delete_location(data['id'])
            return jsonify({'success': True, 'message': 'Location deleted'})
    
    locations_list = db.get_locations()
    return render_template('settings_locations.html', locations=locations_list)

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

            required_columns = ['employee_name', 'job_title', 'location_name', 'shift_date', 'start_time', 'end_time', 'timezone_offset']
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
            
            # Build shift data
            shift_data = {
                'location_id': location['square_location_id'],
                'job_id': job['square_job_id'],
                'team_member_id': team_member['square_team_member_id'] if team_member else None,
                'employee_name': row.get('employee_name', 'Open Shift'),
                'date': row['shift_date'],
                'start_time': row['start_time'],
                'end_time': row['end_time'],
                'timezone': row['timezone_offset']
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
