"""Security helpers: password hashing and CSRF protection.

Password hashing uses Werkzeug's salted PBKDF2. Legacy accounts created with
the old unsalted single-round SHA-256 scheme are detected on login, verified
against the old hash, and transparently upgraded to the new scheme.

CSRF protection is a session-token scheme: a per-session token is minted and
exposed to templates (as a hidden form field and a <meta> tag). Unsafe
requests must echo it back via the ``X-CSRFToken`` header (fetch/XHR) or a
``csrf_token`` form field (classic form posts). Same-origin fetches are
patched to send the header automatically by static/js/csrf.js.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
CSRF_SESSION_KEY = '_csrf_token'
CSRF_HEADER = 'X-CSRFToken'
CSRF_FORM_FIELD = 'csrf_token'


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Salted PBKDF2 hash suitable for storage."""
    return generate_password_hash(password)


def _looks_like_legacy_sha256(stored_hash: str) -> bool:
    """Old scheme was a bare 64-char hex SHA-256 digest (no method prefix)."""
    if not stored_hash or len(stored_hash) != 64:
        return False
    try:
        int(stored_hash, 16)
        return True
    except ValueError:
        return False


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against either a modern or legacy hash."""
    if not stored_hash:
        return False
    if _looks_like_legacy_sha256(stored_hash):
        candidate = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(candidate, stored_hash)
    try:
        return check_password_hash(stored_hash, password)
    except (ValueError, TypeError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True if the stored hash uses the legacy scheme and should be upgraded."""
    return _looks_like_legacy_sha256(stored_hash)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
def ensure_csrf_token(session) -> str:
    """Return the session's CSRF token, minting one if needed."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def _submitted_token(request) -> str | None:
    token = request.headers.get(CSRF_HEADER)
    if token:
        return token
    if request.form:
        token = request.form.get(CSRF_FORM_FIELD)
        if token:
            return token
    # JSON bodies may carry it too (defensive; header is the normal path).
    if request.is_json:
        data = request.get_json(silent=True) or {}
        if isinstance(data, dict):
            return data.get(CSRF_FORM_FIELD)
    return None


def validate_csrf(session, request) -> bool:
    """True if the request carries a token matching the session token."""
    expected = session.get(CSRF_SESSION_KEY)
    submitted = _submitted_token(request)
    if not expected or not submitted:
        return False
    return hmac.compare_digest(str(expected), str(submitted))
