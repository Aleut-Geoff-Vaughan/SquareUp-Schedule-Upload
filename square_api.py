"""
Square API Module
Handles all API calls to Square Labor API
"""

import requests
import os
import time
import hashlib
from contextlib import contextmanager


SANDBOX_HOST = "https://connect.squareupsandbox.com"
PRODUCTION_HOST = "https://connect.squareup.com"

# Transient statuses worth retrying with backoff.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 0.5


def _format_square_error(response):
    """Build a human-readable error string from a Square error response.

    Square error objects include category, code, detail, and a field reference
    (sometimes named `field`, sometimes `field_name`). Including all of them
    in the message lets the user see WHICH field is blank when Square says
    "Field must not be blank".
    """
    try:
        errors = response.json().get('errors') or []
    except ValueError:
        return f"Square API Error (HTTP {response.status_code}): {response.text[:500]}"

    if not errors:
        return f"Square API Error (HTTP {response.status_code}): {response.text[:500]}"

    parts = []
    for err in errors:
        detail = err.get('detail') or err.get('code') or 'unknown error'
        field = err.get('field') or err.get('field_name')
        code = err.get('code')
        bits = [detail]
        if field:
            bits.append(f"field={field}")
        if code and code != detail:
            bits.append(f"code={code}")
        parts.append(' | '.join(bits))
    return 'Square API Error: ' + '; '.join(parts)


def get_environment():
    """Resolve the active environment (sandbox|production), default sandbox."""
    env = (os.environ.get('SQUARE_ENVIRONMENT') or 'sandbox').lower()
    return 'production' if env == 'production' else 'sandbox'


def resolve_credentials():
    """Pick the right access token + application id for the active environment.

    Falls back to the legacy SQUARE_ACCESS_TOKEN if the env-specific variable
    isn't set, so existing setups keep working.
    """
    env = get_environment()
    if env == 'production':
        token = os.environ.get('PRODUCTION_ACCESS_TOKEN') or os.environ.get('SQUARE_ACCESS_TOKEN', '')
        app_id = os.environ.get('PRODUCTION_APPLICATION_ID', '')
        host = PRODUCTION_HOST
    else:
        token = os.environ.get('SANDBOX_ACCESS_TOKEN') or os.environ.get('SQUARE_ACCESS_TOKEN', '')
        app_id = os.environ.get('SANDBOX_APPLICATION_ID', '')
        host = SANDBOX_HOST
    return {'environment': env, 'access_token': token, 'application_id': app_id, 'host': host}


class SquareAPI:
    def __init__(self):
        self._frozen = False
        self._apply(resolve_credentials())

    def _apply(self, creds):
        self.environment = creds['environment']
        self.access_token = creds['access_token']
        self.application_id = creds['application_id']
        self.base_url = f"{creds['host']}/v2/labor"
        self.headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/json',
            'Square-Version': '2026-01-22'
        }

    def _update_token(self):
        """Refresh creds from environment (handles env var changes at runtime).

        A no-op while credentials are frozen for a batch, so an admin flipping
        sandbox<->production mid-batch cannot silently reroute the remaining
        shifts (including to production)."""
        if self._frozen:
            return
        self._apply(resolve_credentials())

    @contextmanager
    def frozen_credentials(self):
        """Freeze credentials for the duration of a batch.

        Snapshots the currently-resolved environment/token and pins them so
        every call in the block targets the same environment even if the
        global env var changes. Restores the prior mode on exit.
        """
        self._apply(resolve_credentials())
        previous = self._frozen
        self._frozen = True
        try:
            yield {'environment': self.environment, 'host': self.base_url}
        finally:
            self._frozen = previous

    def token_missing(self):
        return not self.access_token or self.access_token.startswith('YOUR_')

    def _request_with_retry(self, method, url, **kwargs):
        """Perform a request, retrying transient 429/5xx with exponential
        backoff (honoring Retry-After when present). Raises the usual
        requests exceptions for the caller's try/except to handle.

        Dispatches to the module-level requests.post / requests.get so
        existing call sites and their test mocks keep working.
        """
        kwargs.setdefault('timeout', 15)
        http = requests.post if method.upper() == 'POST' else requests.get
        last_response = None
        for attempt in range(MAX_RETRIES + 1):
            response = http(url, headers=self.headers, **kwargs)
            status = getattr(response, 'status_code', None)
            if status not in RETRYABLE_STATUS:
                return response
            last_response = response
            if attempt == MAX_RETRIES:
                break
            retry_after = None
            headers = getattr(response, 'headers', None)
            if headers is not None:
                try:
                    retry_after = headers.get('Retry-After')
                except (AttributeError, TypeError):
                    retry_after = None
            if retry_after:
                try:
                    delay = float(retry_after)
                except (ValueError, TypeError):
                    delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
            else:
                delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
            time.sleep(delay)
        return last_response

    def _generate_idempotency_key(self, shift_data):
        """Deterministic idempotency key derived from the shift's content.

        Because it does NOT include a timestamp, a retry of the *same* shift
        reuses the same key, so Square recognizes the duplicate and returns
        the original shift instead of creating a second one. Two genuinely
        different shifts hash differently.
        """
        start_at = self._build_iso8601(
            shift_data['date'], shift_data['start_time'], shift_data['timezone']
        )
        end_at = self._build_iso8601(
            shift_data['date'], shift_data['end_time'], shift_data['timezone']
        )
        fingerprint = '|'.join([
            self.environment,
            str(shift_data.get('location_id') or ''),
            str(shift_data.get('job_id') or ''),
            str(shift_data.get('team_member_id') or ''),
            start_at,
            end_at,
        ])
        digest = hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()
        return f"SCHED_{digest[:48]}"

    def _build_iso8601(self, date_str, time_str, tz_offset):
        """Build ISO 8601 timestamp"""
        return f"{date_str}T{time_str}:00{tz_offset}"

    def create_shift(self, shift_data):
        """
        Create a draft scheduled shift

        Args:
            shift_data: {
                'location_id': str,
                'job_id': str,
                'team_member_id': str or None,
                'employee_name': str,
                'date': str (YYYY-MM-DD),
                'start_time': str (HH:MM),
                'end_time': str (HH:MM),
                'timezone': str (e.g., -04:00)
            }

        Returns:
            {'success': bool, 'shift_id': str, 'error': str}
        """
        try:
            self._update_token()

            if self.token_missing():
                return {
                    'success': False,
                    'error': 'Square API token not configured. Please set SQUARE_ACCESS_TOKEN.'
                }

            # Build request. Only include team_member_id when we actually have one;
            # Square's production API rejects an explicit null with "Field must not be blank".
            draft_details = {
                "location_id": shift_data['location_id'],
                "job_id": shift_data['job_id'],
                "start_at": self._build_iso8601(
                    shift_data['date'],
                    shift_data['start_time'],
                    shift_data['timezone']
                ),
                "end_at": self._build_iso8601(
                    shift_data['date'],
                    shift_data['end_time'],
                    shift_data['timezone']
                ),
            }
            tm_id = shift_data.get('team_member_id')
            if tm_id:
                draft_details['team_member_id'] = tm_id

            request_body = {
                "idempotency_key": self._generate_idempotency_key(shift_data),
                "scheduled_shift": {
                    "location_id": shift_data['location_id'],
                    "draft_shift_details": draft_details,
                }
            }

            response = self._request_with_retry(
                'POST',
                f'{self.base_url}/scheduled-shifts',
                json=request_body,
                timeout=10,
            )

            if response.ok:
                data = response.json()
                return {
                    'success': True,
                    'shift_id': data['scheduled_shift']['id']
                }
            else:
                return {
                    'success': False,
                    'error': _format_square_error(response),
                }

        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'API request timed out'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': 'Connection error. Check your internet connection.'}
        except Exception as e:
            return {'success': False, 'error': f"Unexpected error: {str(e)}"}

    def publish_shift(self, shift_id):
        """
        Publish a draft shift to make it live

        Returns:
            {'success': bool, 'error': str}
        """
        try:
            self._update_token()

            response = self._request_with_retry(
                'POST',
                f'{self.base_url}/scheduled-shifts/{shift_id}/publish',
                json={},
                timeout=10,
            )

            if response.ok:
                return {'success': True}
            else:
                return {'success': False, 'error': _format_square_error(response)}

        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'API request timed out'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': 'Connection error'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def create_and_publish_shift(self, shift_data):
        """
        Create and immediately publish a shift.

        Returns:
            {'success': bool, 'shift_id': str, 'error': str, 'step': 'CREATE'|'PUBLISH'}

        On a publish failure the created draft's id is returned under
        'shift_id' so the caller can record the orphaned draft rather than
        losing track of it.
        """
        create_result = self.create_shift(shift_data)
        if not create_result['success']:
            return {**create_result, 'step': 'CREATE'}

        shift_id = create_result['shift_id']

        publish_result = self.publish_shift(shift_id)
        if not publish_result['success']:
            return {**publish_result, 'shift_id': shift_id, 'step': 'PUBLISH'}

        return {'success': True, 'shift_id': shift_id}

    def search_scheduled_shifts(self, location_ids=None, start_at=None, end_at=None):
        """
        Search scheduled shifts via POST /v2/labor/scheduled-shifts/search.

        Returns:
            {'success': bool, 'scheduled_shifts': list, 'error': str}
        """
        try:
            self._update_token()
            if self.token_missing():
                return {'success': False, 'error': 'Square API token not configured.'}

            url = f'{self.base_url}/scheduled-shifts/search'
            query_filter = {}
            if location_ids:
                query_filter['location_ids'] = list(location_ids)
            if start_at and end_at:
                query_filter['start_at_range'] = {'start_at': start_at, 'end_at': end_at}

            shifts = []
            cursor = None
            while True:
                # Square caps the scheduled-shifts search at 50 per page. Pagination via cursor
                # is what gets us the full date range.
                body = {'limit': 50}
                if query_filter:
                    body['query'] = {'filter': query_filter}
                if cursor:
                    body['cursor'] = cursor

                response = self._request_with_retry('POST', url, json=body, timeout=15)
                if not response.ok:
                    return {'success': False, 'error': _format_square_error(response)}

                data = response.json()
                shifts.extend(data.get('scheduled_shifts', []))
                cursor = data.get('cursor')
                if not cursor:
                    break

            return {'success': True, 'scheduled_shifts': shifts}

        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'API request timed out'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': 'Connection error'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def list_locations(self):
        """
        List all locations from Square via GET /v2/locations.

        Returns:
            {'success': bool, 'locations': list, 'error': str}
        """
        try:
            self._update_token()
            if self.token_missing():
                return {'success': False, 'error': 'Square API token not configured.'}

            url = self.base_url.replace('/v2/labor', '/v2/locations')
            response = self._request_with_retry('GET', url, timeout=15)
            if not response.ok:
                return {'success': False, 'error': _format_square_error(response)}

            return {'success': True, 'locations': response.json().get('locations', [])}

        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'API request timed out'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': 'Connection error'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def list_jobs(self):
        """
        List all jobs from Square Team API, paginating through all results.

        Square moved the Jobs resource out of the Labor API into the Team API
        as of Square-Version 2024-12-18; the endpoint is now
        GET /v2/team-members/jobs.

        Returns:
            {'success': bool, 'jobs': list, 'error': str}
        """
        try:
            self._update_token()
            if self.token_missing():
                return {'success': False, 'error': 'Square API token not configured.'}

            url = self.base_url.replace('/v2/labor', '/v2/team-members/jobs')
            jobs = []
            cursor = None
            while True:
                params = {}
                if cursor:
                    params['cursor'] = cursor
                response = self._request_with_retry('GET', url, params=params, timeout=15)
                if not response.ok:
                    return {'success': False, 'error': _format_square_error(response)}

                data = response.json()
                jobs.extend(data.get('jobs', []))
                cursor = data.get('cursor')
                if not cursor:
                    break

            return {'success': True, 'jobs': jobs}

        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'API request timed out'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': 'Connection error'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def list_team_members(self, status='ACTIVE'):
        """
        List team members from Square, paginating through all results.

        Args:
            status: 'ACTIVE', 'INACTIVE', or None for all.

        Returns:
            {'success': bool, 'team_members': list, 'error': str}
        """
        try:
            self._update_token()
            if self.token_missing():
                return {'success': False, 'error': 'Square API token not configured.'}

            url = self.base_url.replace('/v2/labor', '/v2/team-members/search')
            members = []
            cursor = None
            while True:
                body = {'limit': 200}
                if status:
                    body['query'] = {'filter': {'status': status}}
                if cursor:
                    body['cursor'] = cursor

                response = self._request_with_retry('POST', url, json=body, timeout=15)
                if not response.ok:
                    return {'success': False, 'error': _format_square_error(response)}

                data = response.json()
                members.extend(data.get('team_members', []))
                cursor = data.get('cursor')
                if not cursor:
                    break

            return {'success': True, 'team_members': members}

        except requests.exceptions.Timeout:
            return {'success': False, 'error': 'API request timed out'}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': 'Connection error'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def test_connection(self):
        """
        Test if API token is valid by making a test request.

        Returns:
            {'success': bool, 'message': str}
        """
        try:
            self._update_token()

            if not self.access_token:
                return {'success': False, 'message': 'No API token configured'}

            url = self.base_url.replace('/v2/labor', '/v2/locations')
            response = self._request_with_retry('GET', url, timeout=5)

            if response.status_code == 200:
                return {'success': True, 'message': 'API connection successful'}
            else:
                return {'success': False, 'message': f'API returned status {response.status_code}'}

        except Exception as e:
            return {'success': False, 'message': str(e)}
