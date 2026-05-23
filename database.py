"""
Database Module
SQLite database management for Square Schedule Manager
"""

import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.environ.get('DB_PATH', 'schedules.db')

class Database:
    def __init__(self):
        self.db_path = DB_PATH
    
    @contextmanager
    def get_connection(self):
        """Context manager for database connections"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def init_db(self):
        """Initialize database tables"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Locations table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    square_location_id TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Jobs table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    square_job_id TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Team Members table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS team_members (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    square_team_member_id TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Uploads table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS uploads (
                    id INTEGER PRIMARY KEY,
                    row_count INTEGER,
                    success_count INTEGER DEFAULT 0,
                    error_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'PENDING',
                    uploaded_by TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            ''')
            
            # Schedules table (stores created schedules)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS schedules (
                    id INTEGER PRIMARY KEY,
                    upload_id INTEGER,
                    square_shift_id TEXT,
                    location_id TEXT,
                    job_id TEXT,
                    team_member_id TEXT,
                    shift_date DATE,
                    start_time TIME,
                    end_time TIME,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(upload_id) REFERENCES uploads(id)
                )
            ''')
            
            # Settings table (for storing configuration)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
    
    # ==================== USERS ====================
    
    def get_user(self, username):
        """Get user by username"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def add_user(self, username, password_hash, is_admin=False):
        """Add new user"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)',
                (username, password_hash, is_admin)
            )
    
    def get_all_users(self):
        """Get all users"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, username, is_admin, created_at FROM users ORDER BY created_at DESC')
            return [dict(row) for row in cursor.fetchall()]
    
    def delete_user(self, user_id):
        """Delete user"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    
    # ==================== LOCATIONS ====================
    
    def add_location(self, name, square_location_id):
        """Add location"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO locations (name, square_location_id) VALUES (?, ?)',
                (name, square_location_id)
            )
    
    def get_locations(self):
        """Get all locations"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM locations ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_location_by_name(self, name):
        """Get location by name"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM locations WHERE name = ?', (name,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_location(self, location_id, name, square_location_id):
        """Update location"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE locations SET name = ?, square_location_id = ? WHERE id = ?',
                (name, square_location_id, location_id)
            )
    
    def delete_location(self, location_id):
        """Delete location"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM locations WHERE id = ?', (location_id,))
    
    # ==================== JOBS ====================
    
    def add_job(self, name, square_job_id):
        """Add job"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO jobs (name, square_job_id) VALUES (?, ?)',
                (name, square_job_id)
            )
    
    def get_jobs(self):
        """Get all jobs"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM jobs ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_job_by_name(self, name):
        """Get job by name"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM jobs WHERE name = ?', (name,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_job(self, job_id, name, square_job_id):
        """Update job"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE jobs SET name = ?, square_job_id = ? WHERE id = ?',
                (name, square_job_id, job_id)
            )
    
    def delete_job(self, job_id):
        """Delete job"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
    
    # ==================== TEAM MEMBERS ====================
    
    def add_team_member(self, name, square_team_member_id):
        """Add team member"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO team_members (name, square_team_member_id) VALUES (?, ?)',
                (name, square_team_member_id)
            )
    
    def get_team_members(self):
        """Get all team members"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM team_members ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]
    
    def get_team_member_by_name(self, name):
        """Get team member by name"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM team_members WHERE name = ?', (name,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_team_member(self, member_id, name, square_team_member_id):
        """Update team member"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE team_members SET name = ?, square_team_member_id = ? WHERE id = ?',
                (name, square_team_member_id, member_id)
            )
    
    def delete_team_member(self, member_id):
        """Delete team member"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM team_members WHERE id = ?', (member_id,))
    
    # ==================== UPLOADS ====================
    
    def create_upload_record(self, row_count, uploaded_by):
        """Create new upload record"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO uploads (row_count, uploaded_by, status) VALUES (?, ?, ?)',
                (row_count, uploaded_by, 'PENDING')
            )
            return cursor.lastrowid
    
    def update_upload_status(self, upload_id, status, success_count, error_count):
        """Update upload status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE uploads SET status = ?, success_count = ?, error_count = ?, processed_at = CURRENT_TIMESTAMP WHERE id = ?',
                (status, success_count, error_count, upload_id)
            )
    
    def get_upload(self, upload_id):
        """Get upload by ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM uploads WHERE id = ?', (upload_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_upload_history(self, limit=50):
        """Get upload history"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM uploads ORDER BY created_at DESC LIMIT ?',
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_recent_uploads(self, limit=5):
        """Get recent uploads"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM uploads WHERE status = "COMPLETED" ORDER BY created_at DESC LIMIT ?',
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_pending_approvals(self):
        """Get uploads pending approval"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM uploads WHERE status = "PENDING" ORDER BY created_at DESC'
            )
            return [dict(row) for row in cursor.fetchall()]
    
    # ==================== SCHEDULES ====================
    
    def add_schedule(self, upload_id, square_shift_id, location_id, job_id, team_member_id, shift_date, start_time, end_time):
        """Add schedule record"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO schedules 
                   (upload_id, square_shift_id, location_id, job_id, team_member_id, shift_date, start_time, end_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (upload_id, square_shift_id, location_id, job_id, team_member_id, shift_date, start_time, end_time)
            )
    
    def get_schedules_by_upload(self, upload_id):
        """Get schedules for an upload"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM schedules WHERE upload_id = ?', (upload_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    # ==================== SETTINGS ====================
    
    def set_setting(self, key, value):
        """Set a setting"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                (key, value)
            )
    
    def get_setting(self, key):
        """Get a setting"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row[0] if row else None
