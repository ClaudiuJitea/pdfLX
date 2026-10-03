"""Form behaviours MuPDF does not cover: multi-select values and faithful appearances.

* Multi-select list boxes store an array in /V and selected indices in /I.
  PyMuPDF only writes string values, so these are written as PDF objects here.
* MuPDF's list-box appearance does not highlight the selection, and its
  password-field appearance shows the value in clear text. Both appearances
  are regenerated here.
"""
import re

import pymupdf as fitz

HIGHLIGHT = (0.6, 0.75, 0.88)
_STRING = re.compile(r'\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>')


def _decode_pdf_string(token):
    if token.startswith('<'):
        data = bytes.fromhex(re.sub(r'\s', '', token[1:-1]))
        if data.startswith(b'\xfe\xff'):
            return data[2:].decode('utf-16-be', 'replace')
        return data.decode('latin-1')
    body = token[1:-1]
    out, index = [], 0
    escapes = {'n': '\n', 'r': '\r', 't': '\t', 'b': '\b', 'f': '\f', '(': '(', ')': ')', '\\': '\\'}
    while index < len(body):
        char = body[index]
        if char == '\\' and index + 1 < len(body):
            nxt = body[index + 1]
            if nxt in escapes:
                out.append(escapes[nxt])
                index += 2
                continue
            octal = re.match(r'[0-7]{1,3}', body[index + 1:])
            if octal:
                out.append(chr(int(octal.group(0), 8)))
                index += 1 + len(octal.group(0))
                continue
            index += 1
            continue
        out.append(char)
        index += 1
    text = ''.join(out)
    if text.startswith('\xfe\xff'):
        return text[2:].encode('latin-1').decode('utf-16-be', 'replace')
    return text


def _field_holder(doc, xref, key):
    """Return the xref holding ``key`` (the widget or an ancestor field)."""
    current, seen = xref, set()
    while current and current not in seen:
        seen.add(current)
        if doc.xref_get_key(current, key)[0] != 'null':
            return current
        kind, value = doc.xref_get_key(current, 'Parent')
        current = int(value.split()[0]) if kind == 'xref' else None
    return xref


def is_multi_select(widget):
    return (widget.field_type == fitz.PDF_WIDGET_TYPE_LISTBOX
            and bool(widget.field_flags & fitz.PDF_CH_FIELD_IS_MULTI_SELECT))


def read_values(doc, xref):
    """Selected export values of a list box as a list (handles arrays and strings)."""
    kind, value = doc.xref_get_key(_field_holder(doc, xref, 'V'), 'V')
    if kind == 'array':
        return [_decode_pdf_string(token) for token in _STRING.findall(value)]
    if kind == 'string':
        return [value] if value else []
    if kind == 'name':
        return [value.lstrip('/')]
    return []


def _options(doc, xref):
    """(export, label) pairs from /Opt, including inherited options."""
    kind, value = doc.xref_get_key(_field_holder(doc, xref, 'Opt'), 'Opt')
    if kind != 'array':
        return []
    pairs = []
    for match in re.finditer(r'\[\s*(' + _STRING.pattern + r')\s*(' + _STRING.pattern + r')\s*\]|(' +
                             _STRING.pattern + ')', value):
        if match.group(1):
            pairs.append((_decode_pdf_string(match.group(1)), _decode_pdf_string(match.group(2))))
        else:
            text = _decode_pdf_string(match.group(3))
            pairs.append((text, text))
    return pairs


def write_values(doc, xref, values):
    """Store a multi-select value array (/V) and the matching indices (/I)."""
    pairs = _options(doc, xref)
    exports = [export for export, _label in pairs]
    unknown = [value for value in values if value not in exports]
    if unknown:
        raise ValueError(f'Choose listed values only (not {", ".join(unknown)}).')
    ordered = [export for export in exports if export in values]
    holder = _field_holder(doc, xref, 'V')
    if ordered:
        doc.xref_set_key(holder, 'V', '[' + ' '.join(fitz.get_pdf_str(value) for value in ordered) + ']')
        doc.xref_set_key(holder, 'I', '[' + ' '.join(str(exports.index(value)) for value in ordered) + ']')
    else:
        doc.xref_set_key(holder, 'V', 'null')
        doc.xref_set_key(holder, 'I', 'null')
    refresh_appearance(doc, xref)


def _pdf_text(text):
    """Encode text for a simple Helvetica (WinAnsi) string; unsupported characters become '?'."""
    data = text.encode('cp1252', 'replace')
    escaped = data.replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')
    return '(' + escaped.decode('latin-1') + ')'


def _colour(values, operator):
    if not values:
        return ''
    if len(values) == 1:
        return f'{values[0]:g} {"g" if operator == "rg" else "G"}\n'
    if len(values) == 4:
        return f'{" ".join(f"{v:g}" for v in values)} {"k" if operator == "rg" else "K"}\n'
    return f'{" ".join(f"{v:g}" for v in values[:3])} {operator}\n'


def _appearance_xref(doc, xref):
    kind, value = doc.xref_get_key(xref, 'AP/N')
    return int(value.split()[0]) if kind == 'xref' else None


def listbox_appearance(doc, xref):
    """Draw options with the selected rows highlighted."""
    _page, page_widget = _widget(doc, xref)
    if page_widget is None:
        return
    ap = _appearance_xref(doc, xref)
    if ap is None:
        return
    width, height = page_widget.rect.width, page_widget.rect.height
    if page_widget.field_flags is not None and doc.xref_get_key(xref, 'MK/R')[0] != 'null':
        rotation = int(doc.xref_get_key(xref, 'MK/R')[1] or 0)
        if rotation in (90, 270):
            width, height = height, width
    size = page_widget.text_fontsize or 12
    line = size * 1.116
    border = page_widget.border_width or 0
    selected = set(read_values(doc, xref))
    pairs = _options(doc, xref)
    content = ['/Tx BMC', 'q']
    if page_widget.fill_color:
        content.append(_colour(page_widget.fill_color, 'rg') + f'0 0 {width:g} {height:g} re f')
    inset = border + 1
    content.append(f'{inset:g} {inset:g} {width - 2 * inset:g} {height - 2 * inset:g} re W n')
    y = height - inset
    for export, _label in pairs:
        y -= line
        if export in selected:
            content.append(_colour(HIGHLIGHT, 'rg') + f'{inset:g} {y:g} {width - 2 * inset:g} {line:g} re f')
    content.append('BT')
    content.append(_colour(page_widget.text_color or (0,), 'rg').strip())
    content.append(f'/Helv {size:g} Tf')
    y = height - inset
    for _export, label in pairs:
        y -= line
        content.append(f'1 0 0 1 {inset + 2:g} {y + (line - size) / 2 + size * 0.22:g} Tm {_pdf_text(label)} Tj')
    content.append('ET')
    content.append('Q')
    if border and page_widget.border_color:
        content.append('q ' + _colour(page_widget.border_color, 'RG').strip() +
                       f' {border:g} w {border / 2:g} {border / 2:g} {width - border:g} {height - border:g} re S Q')
    content.append('EMC')
    _ensure_helv(doc, ap)
    doc.update_stream(ap, '\n'.join(part for part in content if part).encode('latin-1'))


def password_appearance(doc, xref):
    """Replace the clear-text value in a password field's appearance with asterisks."""
    _page, widget = _widget(doc, xref)
    if widget is None:
        return
    value = widget.field_value or ''
    if not value:
        return
    widget.field_value = '*' * len(value)
    widget.update()
    # Restore the real value without regenerating the appearance.
    holder = _field_holder(doc, xref, 'V')
    doc.xref_set_key(holder, 'V', fitz.get_pdf_str(value))


def _ensure_helv(doc, ap):
    kind, _value = doc.xref_get_key(ap, 'Resources/Font/Helv')
    if kind == 'null':
        font = doc.get_new_xref()
        doc.update_object(font, '<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>')
        if doc.xref_get_key(ap, 'Resources')[0] == 'null':
            doc.xref_set_key(ap, 'Resources', '<<>>')
        if doc.xref_get_key(ap, 'Resources/Font')[0] == 'null':
            doc.xref_set_key(ap, 'Resources/Font', '<<>>')
        doc.xref_set_key(ap, 'Resources/Font/Helv', f'{font} 0 R')


def _widget(doc, xref):
    """Return (page, widget); the page must stay referenced while the widget is used."""
    for page in doc:
        for widget in page.widgets() or ():
            if widget.xref == xref:
                return page, widget
    return None, None


def refresh_appearance(doc, xref):
    """Regenerate appearances MuPDF gets wrong; call after any widget update."""
    _page, widget = _widget(doc, xref)
    if widget is None:
        return
    if widget.field_type == fitz.PDF_WIDGET_TYPE_LISTBOX:
        listbox_appearance(doc, xref)
    elif widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT and widget.field_flags & fitz.PDF_TX_FIELD_IS_PASSWORD:
        password_appearance(doc, xref)
    doc._reset_page_refs()
