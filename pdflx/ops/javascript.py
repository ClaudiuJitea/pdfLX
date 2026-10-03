"""Inspect and edit JavaScript actions. pdfLX never executes them.

Each script has a location (xref + key path) so it can be edited or removed in
place. Field event semantics follow PDF 32000-1 §12.6.3 (trigger events).
"""
import re

import pymupdf as fitz

from .common import OperationError

FIELD_EVENTS = (
    ('K', 'Keystroke', 'Runs while the user types; can reject or change input.'),
    ('F', 'Format', 'Runs before the value is displayed; changes only how it looks.'),
    ('V', 'Validate', 'Runs when the value is committed; can reject the new value.'),
    ('C', 'Calculate', 'Runs when another field changes; computes this field\'s value.'),
    ('Fo', 'Focus', 'Runs when the field receives the keyboard focus.'),
    ('Bl', 'Blur', 'Runs when the field loses the keyboard focus.'),
    ('E', 'Mouse enter', 'Runs when the pointer enters the field.'),
    ('X', 'Mouse exit', 'Runs when the pointer leaves the field.'),
    ('D', 'Mouse down', 'Runs when a mouse button is pressed on the field.'),
    ('U', 'Mouse up', 'Runs when a mouse button is released (a click).'),
)
PAGE_EVENTS = (('O', 'Page open'), ('C', 'Page close'))
DOCUMENT_EVENTS = (('WC', 'Before closing'), ('WS', 'Before saving'), ('DS', 'After saving'),
                   ('WP', 'Before printing'), ('DP', 'After printing'))
_NAME_PAIR = re.compile(r'(\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f]*>)\s*(\d+)\s+0\s+R')


def _code(doc, xref, path):
    """Read a JS entry that may be a string or a stream reference."""
    kind, value = doc.xref_get_key(xref, path)
    if kind == 'string':
        return value
    if kind == 'xref':
        try:
            return doc.xref_stream(int(value.split()[0])).decode('utf-8', 'replace')
        except Exception:
            return ''
    return None


def _is_js(doc, xref, path):
    return doc.xref_get_key(xref, f'{path}/S') == ('name', '/JavaScript')


def _action_target(doc, xref, path):
    """Resolve an action stored indirectly ('n 0 R') to (xref, '') for editing."""
    kind, value = doc.xref_get_key(xref, path)
    if kind == 'xref':
        return int(value.split()[0]), ''
    return xref, path


def _entry(doc, xref, path, location, event, description=''):
    target, base = _action_target(doc, xref, path)
    prefix = f'{base}/' if base else ''
    if doc.xref_get_key(target, f'{prefix}S') != ('name', '/JavaScript'):
        return None
    code = _code(doc, target, f'{prefix}JS')
    return {'location': location, 'event': event, 'description': description, 'xref': target,
            'path': f'{prefix}JS', 'owner': xref, 'owner_path': path, 'code': code or ''}


def scripts(doc):
    """List every JavaScript action found in the document."""
    found = []
    catalog = doc.pdf_catalog()
    # Document-level named scripts (run when the document opens).
    for xref, name in _named_scripts(doc):
        entry = _entry_direct(doc, xref, f'Document script "{name}"', 'Document open',
                              'Runs once when the document is opened.')
        if entry:
            found.append(entry)
    open_entry = _entry(doc, catalog, 'OpenAction', 'Document', 'Open action', 'Runs when the document is opened.')
    if open_entry:
        found.append(open_entry)
    for key, label in DOCUMENT_EVENTS:
        entry = _entry(doc, catalog, f'AA/{key}', 'Document', label)
        if entry:
            found.append(entry)
    for page in doc:
        for key, label in PAGE_EVENTS:
            entry = _entry(doc, page.xref, f'AA/{key}', f'Page {page.number + 1}', label)
            if entry:
                found.append(entry)
        for widget in page.widgets() or ():
            name = widget.field_name or f'Field {widget.xref}'
            entry = _entry(doc, widget.xref, 'A', f'Field "{name}" (page {page.number + 1})', 'Activate',
                           'Runs when the field is clicked.')
            if entry:
                found.append(entry)
            for key, label, description in FIELD_EVENTS:
                entry = _entry(doc, widget.xref, f'AA/{key}', f'Field "{name}" (page {page.number + 1})',
                               label, description)
                if entry:
                    found.append(entry)
        for link in page.get_links():
            xref = link.get('xref')
            if xref:
                entry = _entry(doc, xref, 'A', f'Link (page {page.number + 1})', 'Click')
                if entry:
                    found.append(entry)
    return found


def _entry_direct(doc, xref, location, event, description):
    if doc.xref_get_key(xref, 'S') != ('name', '/JavaScript'):
        return None
    return {'location': location, 'event': event, 'description': description, 'xref': xref, 'path': 'JS',
            'owner': None, 'owner_path': None, 'code': _code(doc, xref, 'JS') or ''}


def _named_scripts(doc):
    kind, value = doc.xref_get_key(doc.pdf_catalog(), 'Names/JavaScript')
    if kind == 'null':
        return []
    root = int(value.split()[0]) if kind == 'xref' else None
    result, pending, seen = [], [root] if root else [], set()
    if kind == 'dict':
        pending, inline = [], value
        result.extend(_pairs(doc, inline))
    while pending:
        node = pending.pop()
        if node in seen:
            continue
        seen.add(node)
        names_kind, names = doc.xref_get_key(node, 'Names')
        if names_kind == 'array':
            result.extend(_pairs(doc, names))
        kids_kind, kids = doc.xref_get_key(node, 'Kids')
        if kids_kind == 'array':
            pending.extend(int(ref) for ref in re.findall(r'(\d+)\s+0\s+R', kids))
    return result


def _pairs(doc, text):
    from ..form_appearance import _decode_pdf_string
    return [(int(ref), _decode_pdf_string(name)) for name, ref in _NAME_PAIR.findall(text)]


def set_code(doc, entry, code):
    """Replace the JavaScript source of an existing action."""
    kind, value = doc.xref_get_key(entry['xref'], entry['path'])
    if kind == 'xref':
        doc.update_stream(int(value.split()[0]), code.encode('utf-8'))
    else:
        doc.xref_set_key(entry['xref'], entry['path'], fitz.get_pdf_str(code))


def remove(doc, entry):
    """Remove a script's action (the field/page/link itself is kept)."""
    if entry['owner'] is None:
        # Named document script: blank it rather than restructure the name tree.
        set_code(doc, entry, '')
        return
    doc.xref_set_key(entry['owner'], entry['owner_path'], 'null')
    doc._reset_page_refs()


def set_field_script(doc, widget_xref, event, code):
    """Create or replace a field trigger-event script (event is a FIELD_EVENTS key)."""
    if event not in {key for key, _l, _d in FIELD_EVENTS}:
        raise OperationError('Unknown field event.')
    if doc.xref_get_key(widget_xref, 'AA')[0] == 'null':
        doc.xref_set_key(widget_xref, 'AA', '<<>>')
    if code.strip():
        doc.xref_set_key(widget_xref, f'AA/{event}', f'<</S/JavaScript/JS {fitz.get_pdf_str(code)}>>')
    else:
        doc.xref_set_key(widget_xref, f'AA/{event}', 'null')
    doc._reset_page_refs()
