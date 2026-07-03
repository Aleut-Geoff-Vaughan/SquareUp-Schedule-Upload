# Reliability & Automation

This document describes the reliability hardening and automation features added
to Square Schedule Manager, plus the configuration that controls them.

## Security

- **SECRET_KEY is enforced.** The app refuses to start unless `SECRET_KEY` is a
  strong value (≥ 32 chars, not a known placeholder). There is no insecure
  default in `docker-compose.yml` or `.env.example` — an unconfigured deploy
  fails closed. Generate one with
  `python -c "import secrets; print(secrets.token_hex(32))"`.
- **No shipped default credentials.** On first run an initial admin is created
  with a password from `INITIAL_ADMIN_PASSWORD`, or a random one printed once to
  the logs. The login page no longer advertises any password.
- **Salted password hashing.** Passwords use Werkzeug PBKDF2/scrypt. Accounts
  created under the old unsalted SHA‑256 scheme are verified and transparently
  upgraded on their next login.
- **CSRF protection.** All state-changing requests require a per-session token
  (sent as `X-CSRFToken` for fetch/XHR — added automatically by
  `static/js/csrf.js` — or a `csrf_token` form field). Disable only for tests
  via `CSRF_ENABLED=0`.
- **Hardened session cookies:** `HttpOnly`, `SameSite=Lax`, and `Secure` (set
  `SESSION_COOKIE_SECURE=0` only for local HTTP).
- **Self-service password change** at *Account*, and admin **password reset** in
  *Admin → Users*. The last admin cannot be deleted.
- **Backups are secrets.** Download/restore require admin; temporary snapshot
  files are cleaned up after the response.

## Correctness

- **Invalid rows are never published.** Rows failing a lookup — including a
  *named but unmatched* employee — are recorded as errors instead of being
  silently sent to Square as open shifts. Only genuinely blank employee names
  become open shifts.
- **DST-aware timezones.** Locations store an IANA zone (e.g.
  `America/New_York`); the correct UTC offset is computed per shift date, so
  shifts stay correct across daylight-saving changes. Location sync imports the
  real timezone from Square. A `timezone_offset` column in the CSV still works
  as an explicit override.
- **Schedule view window.** The live view queries Square with the widest
  timezone window (±14:00) so evening shifts on the last day of a range are
  never cut off by a UTC boundary.
- **Constraint violations return JSON 409** instead of an HTML 500 that broke
  the frontend.

## Square API reliability

- **Stable idempotency keys.** The key is derived from shift content (no
  timestamp), so a retry of the same shift reuses the key and Square dedupes it
  — re-uploading a corrected file no longer creates duplicates.
- **Retry with backoff.** Transient 429/5xx responses are retried with
  exponential backoff (honoring `Retry-After`).
- **Credential freeze per batch.** A publish batch snapshots the environment and
  token up front, so flipping sandbox↔production mid-batch cannot reroute the
  remaining shifts.
- **Double-submit guard.** Approving clears the staged upload up front, so a
  duplicate click can't publish the batch twice.
- **Orphan tracking.** If a shift is created but publish fails, the draft's id is
  recorded so it isn't lost and a retry can dedupe it.

## Database

- **WAL + busy timeout** for better concurrency; `foreign_keys` enabled.
- **In-place migrations** via `PRAGMA user_version` — upgrades add the
  `timezone_name` column and the `upload_results` table without data loss.
- **Per-row results persisted** to `upload_results`, enabling report download
  and retry-of-failed-rows.
- **Safer restore.** Uploaded backups are integrity-checked and validated for
  required tables, swapped atomically (`os.replace`) with stale WAL sidecars
  removed, then migrated to the current schema. Restore during a maintenance
  window (no concurrent writers).
- **`delete_user` cleans up** the user's staged upload.

## Automation features (minimize manual work)

- **Direct team-member sync** (*Settings → Team Members → Sync from Square*) —
  no CSV export round-trip.
- **Timezone import** during location sync.
- **Retry failed rows** and **download a per-row report** from *History*.
- **Drift detection** — *History → Show changes* compares the last two uploads.
- **Pre-publish duplicate check** against shifts already in Square
  (`/api/verify-preview/duplicates`).
- **Native `.xlsx`** support in AI Convert (requires `openpyxl`).
- **Scheduled background jobs** (off by default): master-data sync and rotating
  database backups.

## Configuration reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | — (required) | Session signing key; ≥ 32 chars, no placeholder. |
| `INITIAL_ADMIN_USERNAME` / `INITIAL_ADMIN_PASSWORD` | `admin` / random | First-run admin. |
| `SESSION_COOKIE_SECURE` | `1` | Set `0` only for local HTTP. |
| `CSRF_ENABLED` | `1` | Set `0` to disable CSRF (tests only). |
| `FLASK_ENV` | `production` | `development` enables the debugger (never expose). |
| `GUNICORN_WORKERS` | `2` | WSGI workers. Use `1` if enabling auto jobs. |
| `AUTO_SYNC_HOURS` | `0` | Hours between background master-data syncs (0 = off). |
| `AUTO_BACKUP_HOURS` | `0` | Hours between background DB backups (0 = off). |
| `BACKUP_DIR` | `data/backups` | Where scheduled backups are written. |
| `BACKUP_RETAIN` | `14` | Number of scheduled backups to keep. |
| `CORS_ORIGINS` | (none) | Comma-separated allowed origins for `/api/*`. |

> **Auto jobs and workers:** background jobs run inside each gunicorn worker.
> Enable `AUTO_SYNC_HOURS` / `AUTO_BACKUP_HOURS` only with `GUNICORN_WORKERS=1`
> to avoid running them once per worker.
