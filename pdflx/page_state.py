"""Persist managed canvas objects and their original page stream inside the PDF."""
import base64
import json
import math

import pymupdf as fitz
from .models import EditableText, EditableShape, EditableImage, EditableStroke

KINDS = (EditableText, EditableShape, EditableImage, EditableStroke)
KEY = 'PdfLXEditor'


def _encode(value):
    if isinstance(value, bytes):
        return {'bytes': base64.b64encode(value).decode('ascii')}
    if isinstance(value, tuple):
        return {'tuple': [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError('Unsupported canvas state value.')


def _decode(value):
    if isinstance(value, dict):
        if set(value) == {'bytes'}:
            return base64.b64decode(value['bytes'], validate=True)
        if set(value) == {'tuple'}:
            return tuple(_decode(item) for item in value['tuple'])
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    return value


def persist(doc):
    from . import pdf_handler
    for page_number, groups in getattr(doc, 'editor_page_models', {}).items():
        if not 0 <= page_number < doc.page_count:
            continue
        managed = [obj for group in groups for obj in group
                   if getattr(obj, 'is_new', False) or getattr(obj, '_ghost_redacted', False)]
        page = doc[page_number]
        baseline = pdf_handler._page_snapshots.get((id(doc), page_number))
        if baseline is None:
            continue
        payload = {'version': 1, 'baseline': base64.b64encode(baseline).decode('ascii'),
                   'objects': [{'kind': KINDS.index(type(obj)), 'state': _encode(obj.__dict__)} for obj in managed]}
        kind, value = doc.xref_get_key(page.xref, KEY)
        xref = int(value.split()[0]) if kind == 'xref' else doc.get_new_xref()
        doc.update_object(xref, '<<>>')
        doc.update_stream(xref, json.dumps(payload, ensure_ascii=False).encode('utf-8'), compress=True)
        doc.xref_set_key(page.xref, KEY, f'{xref} 0 R')


def load(doc, page_number):
    """Restore our own overlays; the PDF remains normally readable elsewhere."""
    from . import pdf_handler
    page = doc[page_number]
    kind, value = doc.xref_get_key(page.xref, KEY)
    if kind != 'xref':
        return None
    raw = doc.xref_stream(int(value.split()[0]))
    if not raw or len(raw) > 32 * 1024 * 1024:
        raise ValueError('Invalid or oversized saved editor state.')
    payload = json.loads(raw)
    if payload.get('version') != 1:
        return None
    groups = [[], [], [], []]
    for record in payload['objects']:
        kind = record['kind']
        if not isinstance(kind, int) or not 0 <= kind < len(KINDS):
            raise ValueError('Invalid canvas object type.')
        state = _decode(record['state'])
        bbox = state.get('bbox')
        if bbox is None or len(bbox) != 4 or not all(math.isfinite(v) for v in bbox):
            raise ValueError('Invalid saved canvas bounds.')
        obj = KINDS[kind].__new__(KINDS[kind])
        obj.__dict__.update(state)
        obj.page_number = page_number
        obj.is_baked = True
        groups[kind].append(obj)
    baseline = base64.b64decode(payload['baseline'], validate=True)
    pdf_handler._page_snapshots[id(doc), page_number] = baseline
    pdf_handler._page_original_links[id(doc), page_number] = page.get_links()
    # Extract untouched content from the baseline, then restore the visible PDF.
    from types import SimpleNamespace
    from .pdf_state import PdfState
    holder = SimpleNamespace(doc=doc, current_page_index=page_number)
    backup = PdfState(holder)
    try:
        if not pdf_handler.restore_page_from_snapshot(doc, page_number):
            raise ValueError('Could not restore the saved page baseline.')
        for index, extractor in enumerate((pdf_handler.extract_editable_text,
                pdf_handler.extract_editable_shapes, pdf_handler.extract_editable_images,
                pdf_handler.extract_editable_strokes)):
            originals, error = extractor(doc, page_number)
            if error:
                raise ValueError(error)
            groups[index][:0] = originals
    finally:
        backup.restore(holder)
    return tuple(groups)
