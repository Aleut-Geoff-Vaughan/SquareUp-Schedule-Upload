"""
Database Module
SQLite database management for Square Schedule Manager
"""

import sqlite3
import os
import json
from contextlib import contextmanager

DB_PATH = os.environ.get('DB_PATH', 'schedules.db')

# Bump this whenever a migration is added below. init_db() applies every
# migration whose version is greater than the value stored in the DB, so an
# existing deployment upgrades in place without losing data.
SCHEMA_VERSION = 3

# Tables a valid backup must contain (checked on restore).
REQUIRED_TABLES = {
    'users', 'locations', 'jobs', 'team_members',
    'uploads', 'schedules', 'settings', 'pending_uploads',
}


class Database:
    def __init__(self):
        self.db_path = DB_PATH

    @contextmanager
    def get_connection(self):
        """Context manager for database connections.

        A fresh connection per operation keeps things simple under Flask's
        threaded server. WAL mode plus a busy timeout let concurrent readers
        and a writer coexist instead of immediately raising 'database is
        locked'.
        """
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout=30000')
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Schema + migrations
    # ------------------------------------------------------------------
    def init_db(self):
        """Create tables if missing, then run pending migrations."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_admin BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    square_location_id TEXT UNIQUE NOT NULL,
                    timezone TEXT DEFAULT '-04:00',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    square_job_id TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS team_members (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    square_team_member_id TEXT UNIQUE NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

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

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS pending_uploads (
                    user_id INTEGER PRIMARY KEY,
                    csv_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            self._run_migrations(cursor)

    def _run_migrations(self, cursor):
        """Apply migrations newer than the DB's recorded schema version.

        Uses SQLite's user_version pragma so upgrades are idempotent and
        need no separate bookkeeping table.
        """
        current = cursor.execute('PRAGMA user_version').fetchone()[0]

        if current < 1:
            # v1: per-location IANA timezone name (DST-aware), alongside the
            # legacy fixed-offset `timezone` column which stays as a fallback.
            # Also (defensively) add the base `timezone` column for databases
            # old enough to predate it, so later INSERTs don't fail.
            if not self._has_column(cursor, 'locations', 'timezone'):
                cursor.execute("ALTER TABLE locations ADD COLUMN timezone TEXT DEFAULT '-04:00'")
            if not self._has_column(cursor, 'locations', 'timezone_name'):
                cursor.execute("ALTER TABLE locations ADD COLUMN timezone_name TEXT")

        if current < 2:
            # v2: persist every per-row result so failed rows can be retried
            # and a detailed report can be downloaded later.
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS upload_results (
                    id INTEGER PRIMARY KEY,
                    upload_id INTEGER NOT NULL,
                    row_number INTEGER,
                    status TEXT,
                    square_shift_id TEXT,
                    message TEXT,
                    row_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(upload_id) REFERENCES uploads(id)
                )
            ''')
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_upload_results_upload '
                'ON upload_results(upload_id)'
            )
            cursor.execute(
                'CREATE INDEX IF NOT EXISTS idx_schedules_shift '
                'ON schedules(square_shift_id)'
            )

        if current < 3:
            # v3: backfill legacy fixed offsets to IANA names ONLY where the
            # offset is unambiguous. Most US offsets are shared by two zones
            # (e.g. -05:00 is Eastern EST or Central CDT), and guessing wrong
            # would shift a location by an hour across DST — so those are left
            # NULL and keep using their stored fixed offset until an admin
            # re-syncs from Square or picks a zone. Only '-04:00' (the app's
            # historical Eastern default, applied to every synced/added
            # location) and '+00:00' (UTC) are safe to map.
            offset_to_iana = {
                '-04:00': 'America/New_York',
                '+00:00': 'UTC',
            }
            for row in cursor.execute(
                'SELECT id, timezone, timezone_name FROM locations'
            ).fetchall():
                if row['timezone_name']:
                    continue
                iana = offset_to_iana.get((row['timezone'] or '').strip())
                if iana:
                    cursor.execute(
                        'UPDATE locations SET timezone_name = ? WHERE id = ?',
                        (iana, row['id']),
                    )

        if current < SCHEMA_VERSION:
            cursor.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')

    @staticmethod
    def _has_column(cursor, table, column):
        cursor.execute(f"PRAGMA table_info({table})")
        return column in {row[1] for row in cursor.fetchall()}

    # ==================== USERS ====================

    def get_user(self, username):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_user(self, username, password_hash, is_admin=False):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)',
                (username, password_hash, is_admin)
            )
            return cursor.lastrowid

    def get_all_users(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT id, username, is_admin, created_at FROM users ORDER BY created_at DESC')
            return [dict(row) for row in cursor.fetchall()]

    def count_admins(self):
        """Number of admin users — used to refuse deleting the last admin."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT COUNT(*) FROM users WHERE is_admin = 1')
            return cursor.fetchone()[0]

    def delete_user(self, user_id):
        """Delete a user and clean up their staged upload so a reused rowid
        can never inherit someone else's pending CSV."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM pending_uploads WHERE user_id = ?', (user_id,))
            cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))

    def update_user_password(self, user_id, password_hash):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE users SET password_hash = ? WHERE id = ?',
                (password_hash, user_id)
            )
            return cursor.rowcount

    # ==================== LOCATIONS ====================

    def add_location(self, name, square_location_id, timezone='-04:00', timezone_name=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO locations (name, square_location_id, timezone, timezone_name) '
                'VALUES (?, ?, ?, ?)',
                (name, square_location_id, timezone, timezone_name)
            )

    def get_locations(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM locations ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def get_location_by_name(self, name):
        """Get location by name. Match is whitespace- and case-insensitive
        so a CSV value like 'dominion hills pool ' still matches the stored
        'DOMINION HILLS POOL'."""
        if name is None:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM locations WHERE TRIM(name) = TRIM(?) COLLATE NOCASE',
                (name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_location(self, location_id, name, square_location_id, timezone=None,
                        timezone_name=None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            sets = ['name = ?', 'square_location_id = ?']
            params = [name, square_location_id]
            if timezone is not None:
                sets.append('timezone = ?')
                params.append(timezone)
            if timezone_name is not None:
                sets.append('timezone_name = ?')
                params.append(timezone_name)
            params.append(location_id)
            cursor.execute(
                f'UPDATE locations SET {", ".join(sets)} WHERE id = ?',
                params
            )

    def delete_location(self, location_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM locations WHERE id = ?', (location_id,))

    def replace_locations(self, rows):
        """Atomically replace all locations.

        Args:
            rows: iterable of (name, square_location_id, timezone, timezone_name)
                or (name, square_location_id, timezone) tuples.
        """
        normalized = []
        for row in rows:
            if len(row) == 4:
                normalized.append(tuple(row))
            else:
                name, lid, tz = row
                normalized.append((name, lid, tz, None))
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM locations')
            cursor.executemany(
                'INSERT INTO locations (name, square_location_id, timezone, timezone_name) '
                'VALUES (?, ?, ?, ?)',
                normalized,
            )

    # ==================== JOBS ====================

    def add_job(self, name, square_job_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO jobs (name, square_job_id) VALUES (?, ?)',
                (name, square_job_id)
            )

    def get_jobs(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM jobs ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def get_job_by_name(self, name):
        """Get job by name (whitespace- and case-insensitive match)."""
        if name is None:
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM jobs WHERE TRIM(name) = TRIM(?) COLLATE NOCASE',
                (name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_job(self, job_id, name, square_job_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE jobs SET name = ?, square_job_id = ? WHERE id = ?',
                (name, square_job_id, job_id)
            )

    def delete_job(self, job_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM jobs WHERE id = ?', (job_id,))

    def replace_jobs(self, rows):
        """Atomically replace all jobs with the provided rows.

        Args:
            rows: iterable of (name, square_job_id) tuples.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM jobs')
            cursor.executemany(
                'INSERT INTO jobs (name, square_job_id) VALUES (?, ?)',
                list(rows),
            )

    # ==================== TEAM MEMBERS ====================

    def add_team_member(self, name, square_team_member_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO team_members (name, square_team_member_id) VALUES (?, ?)',
                (name, square_team_member_id)
            )

    def get_team_members(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM team_members ORDER BY name')
            return [dict(row) for row in cursor.fetchall()]

    def get_team_member_by_name(self, name):
        """Get team member by name (whitespace- and case-insensitive match)."""
        if name is None or not str(name).strip():
            return None
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM team_members WHERE TRIM(name) = TRIM(?) COLLATE NOCASE',
                (name,)
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_team_member(self, member_id, name, square_team_member_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE team_members SET name = ?, square_team_member_id = ? WHERE id = ?',
                (name, square_team_member_id, member_id)
            )

    def delete_team_member(self, member_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM team_members WHERE id = ?', (member_id,))

    def replace_team_members(self, rows):
        """Atomically replace all team members with the provided rows.

        Args:
            rows: iterable of (name, square_team_member_id) tuples.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM team_members')
            cursor.executemany(
                'INSERT INTO team_members (name, square_team_member_id) VALUES (?, ?)',
                list(rows),
            )

    # ==================== UPLOADS ====================

    def create_upload_record(self, row_count, uploaded_by):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO uploads (row_count, uploaded_by, status) VALUES (?, ?, ?)',
                (row_count, uploaded_by, 'PROCESSING')
            )
            return cursor.lastrowid

    def update_upload_status(self, upload_id, status, success_count, error_count):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE uploads SET status = ?, success_count = ?, error_count = ?, '
                'processed_at = CURRENT_TIMESTAMP WHERE id = ?',
                (status, success_count, error_count, upload_id)
            )

    def get_upload(self, upload_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM uploads WHERE id = ?', (upload_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_upload_history(self, limit=50):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM uploads ORDER BY created_at DESC LIMIT ?',
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_recent_uploads(self, limit=5):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM uploads WHERE status = "COMPLETED" ORDER BY created_at DESC LIMIT ?',
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_pending_approvals(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM uploads WHERE status IN ("PENDING", "PROCESSING") '
                'ORDER BY created_at DESC'
            )
            return [dict(row) for row in cursor.fetchall()]

    # ==================== UPLOAD RESULTS (per-row) ====================

    def add_upload_result(self, upload_id, row_number, status, square_shift_id,
                          message, row_data):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO upload_results '
                '(upload_id, row_number, status, square_shift_id, message, row_data) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (upload_id, row_number, status, square_shift_id, message,
                 json.dumps(row_data) if row_data is not None else None)
            )

    def get_upload_results(self, upload_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM upload_results WHERE upload_id = ? ORDER BY row_number',
                (upload_id,)
            )
            results = []
            for row in cursor.fetchall():
                item = dict(row)
                if item.get('row_data'):
                    try:
                        item['row_data'] = json.loads(item['row_data'])
                    except (ValueError, TypeError):
                        item['row_data'] = None
                results.append(item)
            return results

    def get_failed_rows(self, upload_id):
        """Original row payloads for every ERROR result of an upload — used to
        re-stage just the failures for another attempt."""
        rows = []
        for result in self.get_upload_results(upload_id):
            if result.get('status') == 'ERROR' and result.get('row_data'):
                rows.append(result['row_data'])
        return rows

    # ==================== SCHEDULES ====================

    def add_schedule(self, upload_id, square_shift_id, location_id, job_id, team_member_id, shift_date, start_time, end_time):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''INSERT INTO schedules
                   (upload_id, square_shift_id, location_id, job_id, team_member_id, shift_date, start_time, end_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (upload_id, square_shift_id, location_id, job_id, team_member_id, shift_date, start_time, end_time)
            )

    def get_schedules_by_upload(self, upload_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM schedules WHERE upload_id = ?', (upload_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_all_shift_ids(self):
        """Every Square shift id this app has created (for drift detection)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT square_shift_id FROM schedules WHERE square_shift_id IS NOT NULL'
            )
            return {row[0] for row in cursor.fetchall()}

    # ==================== SETTINGS ====================

    def set_setting(self, key, value):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
                (key, value)
            )

    def get_setting(self, key):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
            row = cursor.fetchone()
            return row[0] if row else None

    # ==================== PENDING UPLOADS ====================

    def set_pending_upload(self, user_id, csv_rows):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT OR REPLACE INTO pending_uploads (user_id, csv_data, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)',
                (user_id, json.dumps(csv_rows))
            )

    def get_pending_upload(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT csv_data FROM pending_uploads WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            return json.loads(row[0]) if row else None

    def clear_pending_upload(self, user_id):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM pending_uploads WHERE user_id = ?', (user_id,))

    def claim_pending_upload(self, user_id):
        """Atomically read AND remove a user's staged upload.

        Returns the rows to exactly one caller; a concurrent/duplicate call
        gets None. SQLite serializes the DELETE writes, so only the caller
        whose DELETE actually removes the row (rowcount == 1) wins — this is
        what prevents a double-click from publishing the same batch twice,
        even across processes/threads.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT csv_data FROM pending_uploads WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            if not row:
                return None
            cursor.execute('DELETE FROM pending_uploads WHERE user_id = ?', (user_id,))
            if cursor.rowcount != 1:
                return None
            return json.loads(row[0])
