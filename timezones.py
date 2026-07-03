"""Timezone helpers.

Shifts must carry the correct UTC offset *for the shift's own date*, because
a fixed offset like ``-04:00`` is wrong for half the year once DST flips. When
a location stores an IANA zone name (e.g. ``America/New_York``) we compute the
offset for each shift date; a bare offset string is used verbatim as a fallback
for legacy data or explicit CSV overrides.
"""

from __future__ import annotations

from datetime import datetime, date

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    ZoneInfo = None


# IANA zones offered in the UI, grouped by the common US business regions.
# (value, label) — value is the IANA name stored on the location.
TIMEZONE_CHOICES = [
    {'value': 'America/New_York', 'label': 'Eastern Time (New York)'},
    {'value': 'America/Chicago', 'label': 'Central Time (Chicago)'},
    {'value': 'America/Denver', 'label': 'Mountain Time (Denver)'},
    {'value': 'America/Phoenix', 'label': 'Arizona (no DST)'},
    {'value': 'America/Los_Angeles', 'label': 'Pacific Time (Los Angeles)'},
    {'value': 'America/Anchorage', 'label': 'Alaska Time'},
    {'value': 'Pacific/Honolulu', 'label': 'Hawaii (no DST)'},
    {'value': 'UTC', 'label': 'UTC'},
]

VALID_ZONE_NAMES = {c['value'] for c in TIMEZONE_CHOICES}


def is_offset(value: str | None) -> bool:
    """True if ``value`` looks like a fixed ±HH:MM offset."""
    if not value:
        return False
    value = value.strip()
    if len(value) != 6 or value[0] not in '+-' or value[3] != ':':
        return False
    return value[1:3].isdigit() and value[4:6].isdigit()


def offset_for_date(tz_name: str, on_date: date) -> str | None:
    """Return the ±HH:MM offset a zone uses on ``on_date`` (noon, to dodge the
    ambiguous hour around a DST transition), or None if unresolvable."""
    if ZoneInfo is None or not tz_name:
        return None
    try:
        zone = ZoneInfo(tz_name)
    except Exception:
        return None
    dt = datetime(on_date.year, on_date.month, on_date.day, 12, 0, tzinfo=zone)
    utc_offset = dt.utcoffset()
    if utc_offset is None:
        return None
    total_minutes = int(utc_offset.total_seconds() // 60)
    sign = '+' if total_minutes >= 0 else '-'
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def resolve_offset(shift_date: str, explicit_offset: str | None,
                   location: dict | None) -> tuple[str, str | None]:
    """Pick the offset for a shift.

    Priority:
      1. An explicit ±HH:MM override from the CSV/builder row.
      2. The location's IANA zone, evaluated for the shift's own date (DST-safe).
      3. The location's legacy fixed offset.
      4. ``-04:00`` as a last resort (the app's historical default).

    Returns (offset, warning) where warning is a human-readable note if we had
    to fall back in a way the user should know about, else None.
    """
    if is_offset(explicit_offset):
        return explicit_offset.strip(), None

    tz_name = (location or {}).get('timezone_name') if location else None
    legacy = (location or {}).get('timezone') if location else None

    if tz_name:
        try:
            parsed = datetime.strptime(shift_date.strip(), '%Y-%m-%d').date()
        except (ValueError, AttributeError):
            parsed = None
        if parsed is not None:
            computed = offset_for_date(tz_name, parsed)
            if computed:
                return computed, None
        if is_offset(legacy):
            return legacy.strip(), (
                f"Could not compute a DST-aware offset for {tz_name}; "
                f"used the stored fallback {legacy}."
            )

    if is_offset(legacy):
        return legacy.strip(), None

    return '-04:00', (
        "No timezone configured for this location; defaulted to -04:00 (US Eastern DST). "
        "Set the location's timezone to avoid this."
    )
