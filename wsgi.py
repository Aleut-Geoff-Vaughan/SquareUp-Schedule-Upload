"""WSGI entrypoint for production servers (gunicorn/uwsgi).

Importing this module bootstraps an initial admin user if none exists, then
exposes ``application`` for the WSGI server. Tests import ``app`` directly and
never touch this module, so the bootstrap never interferes with test fixtures.

    gunicorn --bind 0.0.0.0:5000 --workers 2 wsgi:application
"""

from app import app, _bootstrap_admin, _configure_scheduler

_bootstrap_admin()
# Background jobs (auto sync/backup) are off unless AUTO_SYNC_HOURS /
# AUTO_BACKUP_HOURS are set. With multiple gunicorn workers each worker would
# run its own scheduler, so enable auto-jobs only with GUNICORN_WORKERS=1.
_configure_scheduler()

application = app
