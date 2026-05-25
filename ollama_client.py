"""
Ollama Client
Minimal wrapper around the local Ollama HTTP API used by the AI-convert feature.
"""

import requests


DEFAULT_HOST = 'http://host.docker.internal:11434'
DEFAULT_MODEL = 'llama3.1:8b'


class OllamaClient:
    def __init__(self, host=None, model=None):
        self.host = (host or DEFAULT_HOST).rstrip('/')
        self.model = model or DEFAULT_MODEL

    def ping(self):
        """Check that the Ollama server is reachable and report installed models."""
        try:
            r = requests.get(f'{self.host}/api/tags', timeout=5)
            if not r.ok:
                return {'success': False, 'error': f'Ollama returned HTTP {r.status_code}'}
            tags = r.json().get('models', [])
            return {
                'success': True,
                'host': self.host,
                'models': [m.get('name') for m in tags if m.get('name')],
            }
        except requests.exceptions.ConnectionError:
            return {
                'success': False,
                'error': (
                    f'Could not connect to Ollama at {self.host}. '
                    'If Ollama is running on the host, the container reaches it via '
                    'http://host.docker.internal:11434 on Docker Desktop. '
                    'On Linux you may also need `--add-host host.docker.internal:host-gateway`.'
                ),
            }
        except requests.exceptions.Timeout:
            return {'success': False, 'error': f'Ollama at {self.host} timed out.'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def chat(self, system_prompt, user_prompt, timeout=180):
        """Send a single-turn chat request, return the assistant's reply text."""
        try:
            body = {
                'model': self.model,
                'stream': False,
                'options': {'temperature': 0.1},
                'messages': [
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt},
                ],
            }
            r = requests.post(f'{self.host}/api/chat', json=body, timeout=timeout)
            if not r.ok:
                try:
                    detail = r.json().get('error', r.text)
                except ValueError:
                    detail = r.text
                return {'success': False, 'error': f'Ollama HTTP {r.status_code}: {detail}'}
            data = r.json()
            content = (data.get('message') or {}).get('content', '')
            return {'success': True, 'content': content}
        except requests.exceptions.ConnectionError:
            return {'success': False, 'error': f'Could not connect to Ollama at {self.host}.'}
        except requests.exceptions.Timeout:
            return {'success': False, 'error': f'Ollama at {self.host} timed out after {timeout}s.'}
        except Exception as e:
            return {'success': False, 'error': str(e)}
