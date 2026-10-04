"""AI settings. The model and endpoint live in settings.json; the API key does not.

The key is kept in the desktop keyring (libsecret) when available, otherwise in a
file readable only by the user. OPENROUTER_API_KEY overrides both.
"""
import os
from pathlib import Path

from ..i18n import get_setting, set_setting

DEFAULT_BASE_URL = 'https://openrouter.ai/api/v1'
DEFAULT_MAX_STEPS = 40
_KEY_FILE = Path.home() / '.config' / 'pdflx' / 'openrouter.key'
_SCHEMA = None


def _secret():
    global _SCHEMA
    try:
        import gi
        gi.require_version('Secret', '1')
        from gi.repository import Secret
    except (ImportError, ValueError):
        return None, None
    if _SCHEMA is None:
        _SCHEMA = Secret.Schema.new('org.pdflx.Editor.AI', Secret.SchemaFlags.NONE,
                                    {'service': Secret.SchemaAttributeType.STRING})
    return Secret, _SCHEMA


def model():
    return (get_setting('ai_model') or '').strip()


def base_url():
    return (get_setting('ai_base_url') or DEFAULT_BASE_URL).strip().rstrip('/')


def max_steps():
    try:
        return max(1, int(get_setting('ai_max_steps') or DEFAULT_MAX_STEPS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_STEPS


def save(model_id, url, steps):
    set_setting('ai_model', model_id.strip())
    set_setting('ai_base_url', url.strip().rstrip('/') or DEFAULT_BASE_URL)
    set_setting('ai_max_steps', int(steps))


def api_key():
    env = os.environ.get('OPENROUTER_API_KEY', '').strip()
    if env:
        return env
    Secret, schema = _secret()
    if Secret:
        try:
            value = Secret.password_lookup_sync(schema, {'service': 'openrouter'}, None)
            if value:
                return value
        except Exception:
            pass
    try:
        return _KEY_FILE.read_text().strip()
    except OSError:
        return ''


def key_from_environment():
    return bool(os.environ.get('OPENROUTER_API_KEY', '').strip())


def set_api_key(value):
    """Store the key; returns where it went ('keyring' or 'file')."""
    value = value.strip()
    Secret, schema = _secret()
    if Secret:
        try:
            if value:
                Secret.password_store_sync(schema, {'service': 'openrouter'}, Secret.COLLECTION_DEFAULT,
                                           'pdfLX OpenRouter API key', value, None)
            else:
                Secret.password_clear_sync(schema, {'service': 'openrouter'}, None)
            _KEY_FILE.unlink(missing_ok=True)
            return 'keyring'
        except Exception:
            pass
    if not value:
        _KEY_FILE.unlink(missing_ok=True)
        return 'file'
    _KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write(value)
    return 'file'
