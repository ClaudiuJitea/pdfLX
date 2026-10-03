"""Form data interchange: FDF, XFDF, JSON, and CSV.

Values are keyed by fully qualified field names (parent.child). Checkboxes
export their on-state name or "Off", radio groups export the chosen option,
and multi-select lists export a list of values.
"""
import csv
import html
import io
import json
import re
import xml.etree.ElementTree as ET

import pymupdf as fitz

from .common import OperationError

FORMATS = (('fdf', 'FDF'), ('xfdf', 'XFDF'), ('json', 'JSON'), ('csv', 'CSV'))


def _fields(doc):
    """Yield (page, widget xref, full name, type, field dict) for fillable widgets."""
    from .forms import full_names
    from ..document_features import list_form_fields
    names = full_names(doc)
    for field in list_form_fields(doc):
        if field['type'] in (fitz.PDF_WIDGET_TYPE_BUTTON, fitz.PDF_WIDGET_TYPE_SIGNATURE):
            continue
        name = names.get(field['xref'])
        if not name:
            # A widget of a field with several widgets: the parent holds the name.
            kind, parent = doc.xref_get_key(field['xref'], 'Parent')
            name = names.get(int(parent.split()[0])) if kind == 'xref' else field['name']
        yield field, name or field['name']


def collect(doc):
    """Return {full name: value} for every fillable field."""
    values = {}
    for field, name in _fields(doc):
        kind = field['type']
        if kind == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            if field['value'] == field['on_state']:
                values[name] = field['on_state']
            else:
                values.setdefault(name, 'Off')
        elif kind == fitz.PDF_WIDGET_TYPE_CHECKBOX:
            values[name] = field['on_state'] if field['value'] == field['on_state'] else 'Off'
        elif isinstance(field['value'], (list, tuple)):
            values[name] = list(field['value'])
        else:
            values[name] = '' if field['value'] is None else str(field['value'])
    return values


# ---------------------------------------------------------------- serializers

def _pdf_string(value):
    return fitz.get_pdf_str(str(value))


def to_fdf(values, source_name=''):
    """Hierarchical FDF (fields nested by name parts)."""
    tree = {}
    for name, value in values.items():
        node = tree
        parts = name.split('.')
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                node = {}
        node[parts[-1]] = value

    def render(node):
        items = []
        for key, value in node.items():
            if isinstance(value, dict):
                items.append(f'<</T {_pdf_string(key)} /Kids [{render(value)}]>>')
            elif isinstance(value, list):
                items.append(f'<</T {_pdf_string(key)} /V [{" ".join(_pdf_string(v) for v in value)}]>>')
            elif value == 'Off' or (isinstance(value, str) and value and not value.isspace()
                                    and re.fullmatch(r'[A-Za-z0-9_#.-]+', value) and value in _NAME_VALUES):
                items.append(f'<</T {_pdf_string(key)} /V /{value}>>')
            else:
                items.append(f'<</T {_pdf_string(key)} /V {_pdf_string(value)}>>')
        return ' '.join(items)
    source = f'/F {_pdf_string(source_name)} ' if source_name else ''
    body = f'1 0 obj\n<</FDF <<{source}/Fields [{render(tree)}]>>>>\nendobj\n'
    header = '%FDF-1.2\n%\xe2\xe3\xcf\xd3\n'
    offset = len(header.encode('latin-1'))
    xref = f'xref\n0 2\n0000000000 65535 f \n{offset:010d} 00000 n \n'
    return (header + body + xref + 'trailer\n<</Root 1 0 R /Size 2>>\n%%EOF\n').encode('latin-1', 'replace')


# Button state names written as PDF names (/Yes, /Off) rather than strings.
_NAME_VALUES = set()


def to_xfdf(values, source_name=''):
    root = ET.Element('xfdf', {'xmlns': 'http://ns.adobe.com/xfdf/', 'xml:space': 'preserve'})
    fields = ET.SubElement(root, 'fields')
    for name, value in values.items():
        node = fields
        for part in name.split('.'):
            child = next((c for c in node.findall('field') if c.get('name') == part), None)
            if child is None:
                child = ET.SubElement(node, 'field', {'name': part})
            node = child
        for item in (value if isinstance(value, list) else [value]):
            ET.SubElement(node, 'value').text = str(item)
    if source_name:
        ET.SubElement(root, 'f', {'href': source_name})
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding='utf-8')


def to_json(values):
    return json.dumps({'format': 'pdflx-form-data', 'version': 1, 'fields': values}, ensure_ascii=False, indent=1)


def to_csv(values):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    names = list(values)
    writer.writerow(names)
    writer.writerow([';'.join(v) if isinstance(v, list) else v for v in values.values()])
    return buffer.getvalue()


def export(doc, fmt, source_name=''):
    values = collect(doc)
    if not values:
        raise OperationError('This document has no fillable form fields.')
    _NAME_VALUES.clear()
    for field, _name in _fields(doc):
        if field.get('on_state'):
            _NAME_VALUES.add(field['on_state'])
    _NAME_VALUES.add('Off')
    if fmt == 'fdf':
        return to_fdf(values, source_name)
    if fmt == 'xfdf':
        return to_xfdf(values, source_name)
    if fmt == 'json':
        return to_json(values)
    if fmt == 'csv':
        return to_csv(values)
    raise OperationError(f'Unknown form data format: {fmt}.')


# ---------------------------------------------------------------- parsers

def _decode(token):
    from ..form_appearance import _decode_pdf_string
    return _decode_pdf_string(token)


_STRING = r'\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>'


def parse_fdf(data):
    """Parse FDF field values with MuPDF-independent tokenizing (handles /Kids nesting)."""
    text = data.decode('latin-1') if isinstance(data, bytes) else data
    match = re.search(r'/Fields\s*\[', text)
    if not match:
        raise OperationError('The FDF file contains no /Fields array.')
    values, index = {}, match.end()

    def parse_array(index, prefix):
        while index < len(text):
            index = _skip(text, index)
            if text.startswith(']', index):
                return index + 1
            if text.startswith('<<', index):
                index = parse_dict(index + 2, prefix)
                continue
            index += 1
        return index

    def parse_dict(index, prefix):
        name, value, has_value = None, None, False
        while index < len(text):
            index = _skip(text, index)
            if text.startswith('>>', index):
                if name is not None and has_value:
                    values[f'{prefix}{name}'] = value
                return index + 2
            key = re.match(r'/(\w+)', text[index:])
            if not key:
                index += 1
                continue
            index += key.end()
            index = _skip(text, index)
            if key.group(1) == 'T':
                token = re.match(_STRING, text[index:])
                name = _decode(token.group(0)) if token else ''
                index += token.end() if token else 0
            elif key.group(1) == 'V':
                if text.startswith('[', index):
                    end = _matching(text, index, '[', ']')
                    value = [_decode(t) for t in re.findall(_STRING, text[index:end])] or \
                            [n for n in re.findall(r'/([^\s/\]]+)', text[index:end])]
                    index = end
                elif text.startswith('/', index):
                    token = re.match(r'/([^\s/>\]]+)', text[index:])
                    value = token.group(1).replace('#20', ' ')
                    index += token.end()
                else:
                    token = re.match(_STRING, text[index:])
                    value = _decode(token.group(0)) if token else ''
                    index += token.end() if token else 0
                has_value = True
            elif key.group(1) == 'Kids' and text.startswith('[', index):
                index = parse_array(index + 1, f'{prefix}{name}.' if name else prefix)
            elif text.startswith('<<', index):
                index = _matching(text, index, '<<', '>>')
            elif text.startswith('[', index):
                index = _matching(text, index, '[', ']')
        return index

    parse_array(index, '')
    return values


def _skip(text, index):
    while index < len(text) and text[index] in ' \t\r\n\f\0':
        index += 1
    return index


def _matching(text, index, opening, closing):
    depth = 0
    while index < len(text):
        if text.startswith(opening, index):
            depth += 1
            index += len(opening)
        elif text.startswith(closing, index):
            depth -= 1
            index += len(closing)
            if depth == 0:
                return index
        elif text[index] == '(':
            token = re.match(_STRING, text[index:])
            index += token.end() if token else 1
        else:
            index += 1
    return index


def parse_xfdf(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise OperationError(f'The XFDF file is not valid XML: {error}.')
    namespace = root.tag.split('}')[0] + '}' if root.tag.startswith('{') else ''
    values = {}

    def walk(node, prefix):
        for field in node.findall(f'{namespace}field'):
            name = f"{prefix}{field.get('name', '')}"
            items = [value.text or '' for value in field.findall(f'{namespace}value')]
            if items:
                values[name] = items if len(items) > 1 else items[0]
            walk(field, name + '.')
    fields = root.find(f'{namespace}fields')
    if fields is None:
        raise OperationError('The XFDF file contains no <fields> element.')
    walk(fields, '')
    return values


def parse_json(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise OperationError(f'The file is not valid JSON: {error}.')
    fields = data.get('fields', data) if isinstance(data, dict) else None
    if not isinstance(fields, dict):
        raise OperationError('Expected a JSON object of field names and values.')
    return fields


def parse_csv(text, row=0):
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise OperationError('The CSV file has a header but no values.')
    if not 0 <= row < len(rows):
        raise OperationError(f'The CSV file has only {len(rows)} row(s).')
    return {key: value for key, value in rows[row].items() if key}


def parse(data, fmt, row=0):
    text = data.decode('utf-8-sig', 'replace') if isinstance(data, bytes) else data
    if fmt == 'fdf':
        return parse_fdf(data)
    if fmt == 'xfdf':
        return parse_xfdf(data if isinstance(data, bytes) else data.encode('utf-8'))
    if fmt == 'json':
        return parse_json(text)
    if fmt == 'csv':
        return parse_csv(text, row)
    raise OperationError(f'Unknown form data format: {fmt}.')


def detect_format(filename, data):
    lower = filename.lower()
    for key, _label in FORMATS:
        if lower.endswith('.' + key):
            return key
    head = (data[:200] if isinstance(data, bytes) else data[:200].encode('utf-8', 'replace')).lstrip()
    if head.startswith(b'%FDF'):
        return 'fdf'
    if head.startswith(b'<'):
        return 'xfdf'
    if head.startswith(b'{'):
        return 'json'
    return 'csv'


def plan_import(doc, values):
    """Map imported values to update_form_fields() keys. Returns (updates, unmatched names)."""
    updates, matched = {}, set()
    for field, name in _fields(doc):
        if name not in values:
            continue
        value = values[name]
        matched.add(name)
        if field['readonly']:
            continue
        key = (field['page'], field['xref'])
        kind = field['type']
        if kind == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            if str(value) == str(field['on_state']):
                updates[key] = True
        elif kind == fitz.PDF_WIDGET_TYPE_CHECKBOX:
            text = str(value).strip().lower()
            updates[key] = text not in ('', 'off', 'false', 'no', '0') and (
                text in ('yes', 'on', 'true', '1', 'x') or str(value) == str(field['on_state']))
        elif field.get('multi_select'):
            items = value if isinstance(value, list) else [part for part in str(value).split(';') if part]
            updates[key] = [str(item) for item in items]
        else:
            updates[key] = ';'.join(value) if isinstance(value, list) else str(value)
    unmatched = sorted(name for name in values if name not in matched)
    return updates, unmatched


def import_values(doc, values, run_scripts=None):
    """Apply imported values; returns (changed field count, unmatched names)."""
    from ..document_features import update_form_fields
    updates, unmatched = plan_import(doc, values)
    if not updates:
        raise OperationError('None of the imported field names match this form.')
    update_form_fields(doc, updates, run_scripts)
    return len(updates), unmatched


def escape(value):
    return html.escape(str(value))
