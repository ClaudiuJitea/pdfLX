"""Per-file reading position, recent-file thumbnails, and crash recovery.

Recovery copies of modified, unencrypted documents are written periodically to
``$XDG_STATE_HOME/pdflx/recovery`` and removed when the document is saved or
closed normally. Whatever is left over at start-up is offered for recovery.
"""
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path

from gi.repository import GLib

from .i18n import get_setting, set_setting

logger = logging.getLogger(__name__)

POSITIONS_KEY = 'file_positions'
MAX_POSITIONS = 200
AUTOSAVE_SECONDS = 120


def _state_dir():
    base = os.environ.get('XDG_STATE_HOME') or os.path.join(Path.home(), '.local', 'state')
    path = Path(base) / 'pdflx'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_dir():
    base = os.environ.get('XDG_CACHE_HOME') or os.path.join(Path.home(), '.cache')
    path = Path(base) / 'pdflx' / 'thumbnails'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _key(path):
    return os.path.abspath(os.path.normpath(str(path)))


# ---------------------------------------------------------------- reading position

def remember_position(path, page, zoom=None, scroll_mode=None):
    if not path:
        return
    positions = get_setting(POSITIONS_KEY, {}) or {}
    if not isinstance(positions, dict):
        positions = {}
    key = _key(path)
    positions.pop(key, None)
    positions[key] = {'page': int(page), 'zoom': float(zoom) if zoom else None, 'mode': scroll_mode,
                      'time': int(time.time())}
    if len(positions) > MAX_POSITIONS:
        for old in sorted(positions, key=lambda k: positions[k].get('time', 0))[:len(positions) - MAX_POSITIONS]:
            positions.pop(old, None)
    set_setting(POSITIONS_KEY, positions)


def recall_position(path):
    if not path:
        return None
    positions = get_setting(POSITIONS_KEY, {}) or {}
    return positions.get(_key(path)) if isinstance(positions, dict) else None


# ---------------------------------------------------------------- thumbnails

def thumbnail_path(path):
    digest = hashlib.sha1(_key(path).encode('utf-8')).hexdigest()
    return _cache_dir() / f'{digest}.png'


def store_thumbnail(doc, path, width=96):
    """Cache a first-page thumbnail for the recent-files list (not for encrypted files)."""
    try:
        if not path or getattr(doc, 'needs_pass', False) or doc.is_encrypted or not doc.page_count:
            return
        import pymupdf as fitz
        page = doc[0]
        scale = width / max(1, page.rect.width)
        page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=True).save(str(thumbnail_path(path)))
    except Exception as error:
        logger.debug('Thumbnail cache failed for %s: %s', path, error)


def cached_thumbnail(path):
    candidate = thumbnail_path(path)
    try:
        if candidate.is_file() and candidate.stat().st_mtime >= os.path.getmtime(path) - 1:
            return str(candidate)
    except OSError:
        pass
    return None


# ---------------------------------------------------------------- recovery

def recovery_dir():
    path = _state_dir() / 'recovery'
    path.mkdir(parents=True, exist_ok=True)
    return path


def pending_recoveries():
    """Recovery entries left by a previous run: list of dicts with path/meta."""
    entries = []
    for meta_path in sorted(recovery_dir().glob('*.json')):
        pdf = meta_path.with_suffix('.pdf')
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            meta = {}
        if pdf.is_file() and meta.get('pid') != os.getpid():
            meta['recovery_file'] = str(pdf)
            meta['meta_file'] = str(meta_path)
            entries.append(meta)
        elif not pdf.is_file():
            meta_path.unlink(missing_ok=True)
    return entries


def discard_recovery(entry):
    for key in ('recovery_file', 'meta_file'):
        try:
            Path(entry[key]).unlink(missing_ok=True)
        except (OSError, KeyError):
            pass


class RecoveryManager:
    """Periodically snapshot modified sessions so work survives a crash."""

    def __init__(self, window, interval=AUTOSAVE_SECONDS):
        self.window = window
        self.enabled = bool(get_setting('autosave_recovery', True))
        self.source = GLib.timeout_add_seconds(interval, self.tick) if self.enabled else None

    def _paths(self, session):
        base = recovery_dir() / session.session_id
        return base.with_suffix('.pdf'), base.with_suffix('.json')

    def tick(self):
        if not self.enabled:
            return GLib.SOURCE_REMOVE
        for session in list(getattr(self.window, 'sessions', ())):
            try:
                self.snapshot(session)
            except Exception as error:
                logger.warning('Recovery snapshot failed: %s', error)
        return GLib.SOURCE_CONTINUE

    def snapshot(self, session):
        doc = session.doc
        if doc is None or not session.is_modified:
            return
        if doc.is_encrypted or getattr(doc, 'editor_password', ''):
            # Never write decrypted copies of protected documents to disk.
            return
        from .page_state import persist
        import pymupdf as fitz
        persist(doc)
        data = doc.tobytes(garbage=0, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)
        meta = {'original': session.pdf_path, 'title': session.title, 'time': int(time.time()),
                'page': session.current_page_index, 'pid': os.getpid()}
        pdf_path, meta_path = self._paths(session)

        def write():
            try:
                temporary = pdf_path.with_suffix('.part')
                temporary.write_bytes(data)
                os.replace(temporary, pdf_path)
                meta_path.write_text(json.dumps(meta), encoding='utf-8')
            except OSError as error:
                logger.warning('Could not write recovery copy: %s', error)
        threading.Thread(target=write, daemon=True).start()

    def discard(self, session):
        for path in self._paths(session):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def stop(self):
        if self.source:
            GLib.source_remove(self.source)
            self.source = None
