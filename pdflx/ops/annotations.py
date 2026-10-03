"""Native PDF annotations: shapes, free text, attachments, replies, review states, interchange."""
import datetime
import html
import io
import json
import re

import pymupdf as fitz

from .common import OperationError

LINE_ENDS = (
    ('none', fitz.PDF_ANNOT_LE_NONE), ('open_arrow', fitz.PDF_ANNOT_LE_OPEN_ARROW),
    ('closed_arrow', fitz.PDF_ANNOT_LE_CLOSED_ARROW), ('circle', fitz.PDF_ANNOT_LE_CIRCLE),
    ('square', fitz.PDF_ANNOT_LE_SQUARE), ('diamond', fitz.PDF_ANNOT_LE_DIAMOND),
    ('butt', fitz.PDF_ANNOT_LE_BUTT), ('slash', fitz.PDF_ANNOT_LE_SLASH),
)
SHAPE_KINDS = ('rectangle', 'ellipse', 'line', 'arrow', 'double_arrow')
REVIEW_STATES = ('Accepted', 'Rejected', 'Cancelled', 'Completed', 'None')
FLAG_NAMES = (
    ('hidden', fitz.PDF_ANNOT_IS_HIDDEN), ('print', fitz.PDF_ANNOT_IS_PRINT),
    ('no_zoom', fitz.PDF_ANNOT_IS_NO_ZOOM), ('no_rotate', fitz.PDF_ANNOT_IS_NO_ROTATE),
    ('no_view', fitz.PDF_ANNOT_IS_NO_VIEW), ('read_only', fitz.PDF_ANNOT_IS_READ_ONLY),
    ('locked', fitz.PDF_ANNOT_IS_LOCKED), ('locked_contents', fitz.PDF_ANNOT_IS_LOCKED_CONTENTS),
)
ATTACHMENT_ICONS = ('PushPin', 'Paperclip', 'Graph', 'Tag')


def _check_rect(page, rect):
    rect = fitz.Rect(rect)
    if rect.is_empty or not rect.is_valid:
        raise OperationError('Select an area on the page first.')
    if not (page.rect * page.derotation_matrix).intersects(rect):
        raise OperationError('The selected area is outside the page.')
    return rect


def _finish(annot, author='', content='', opacity=1.0):
    annot.set_info(title=author.strip(), content=content.strip())
    if opacity < 1:
        annot.set_opacity(max(0.05, opacity))
    annot.update()
    return annot.xref


def _line_end(name):
    return dict(LINE_ENDS).get(name, fitz.PDF_ANNOT_LE_NONE)


def add_shape(doc, page_number, kind, rect, stroke=(0.85, 0.1, 0.1), fill=None, width=1.5,
              dashed=False, author='', content='', opacity=1.0, start=None, end=None):
    """Add a native square/circle/line annotation. Lines use the rectangle diagonal
    from ``start`` to ``end`` when given (unrotated points), else top-left→bottom-right."""
    page = doc[page_number]
    rect = _check_rect(page, rect)
    if kind == 'rectangle':
        annot = page.add_rect_annot(rect)
    elif kind == 'ellipse':
        annot = page.add_circle_annot(rect)
    elif kind in ('line', 'arrow', 'double_arrow'):
        p1 = fitz.Point(start) if start else rect.tl
        p2 = fitz.Point(end) if end else rect.br
        annot = page.add_line_annot(p1, p2)
        if kind != 'line':
            annot.set_line_ends(fitz.PDF_ANNOT_LE_OPEN_ARROW if kind == 'double_arrow' else fitz.PDF_ANNOT_LE_NONE,
                                fitz.PDF_ANNOT_LE_OPEN_ARROW)
        fill = fill if kind != 'line' else None
    else:
        raise OperationError(f'Unknown shape: {kind}.')
    annot.set_colors(stroke=stroke, fill=fill)
    annot.set_border(width=width, dashes=[3, 3] if dashed else None)
    return _finish(annot, author, content, opacity)


def add_polyline(doc, page_number, points, closed=False, stroke=(0.85, 0.1, 0.1), fill=None, width=1.5,
                 author='', content='', opacity=1.0):
    page = doc[page_number]
    points = [fitz.Point(p) for p in points]
    if len(points) < (3 if closed else 2):
        raise OperationError('Draw at least {} points.'.format(3 if closed else 2))
    annot = page.add_polygon_annot(points) if closed else page.add_polyline_annot(points)
    annot.set_colors(stroke=stroke, fill=fill if closed else None)
    annot.set_border(width=width)
    return _finish(annot, author, content, opacity)


def add_ink(doc, page_number, strokes, color=(0.1, 0.2, 0.85), width=2.0, author='', content='', opacity=1.0):
    """Add a native ink annotation from lists of unrotated points."""
    page = doc[page_number]
    strokes = [[tuple(fitz.Point(p)) for p in stroke] for stroke in strokes if len(stroke) >= 2]
    if not strokes:
        raise OperationError('Draw at least one stroke.')
    annot = page.add_ink_annot(strokes)
    annot.set_colors(stroke=color)
    annot.set_border(width=width)
    return _finish(annot, author, content, opacity)


def add_free_text(doc, page_number, rect, text, font_size=12, text_color=(0, 0, 0), fill=None,
                  border_width=0, align=0, author='', callout_to=None, opacity=1.0):
    """Add a FreeText (typewriter) annotation; ``callout_to`` adds a leader line to that point.

    Plain-text FreeText draws its border and callout line in the text colour.
    """
    page = doc[page_number]
    rect = _check_rect(page, rect)
    if not text.strip():
        raise OperationError('Enter the text to place.')
    kwargs = dict(fontsize=font_size, fontname='Helv', text_color=text_color, fill_color=fill,
                  border_width=border_width, align=align, rotate=page.rotation, opacity=opacity)
    if callout_to is not None:
        target = fitz.Point(callout_to)
        knee = fitz.Point(rect.x0 if target.x < rect.x0 else rect.x1, (rect.y0 + rect.y1) / 2)
        kwargs['callout'] = (target, knee)
        kwargs['line_end'] = fitz.PDF_ANNOT_LE_OPEN_ARROW
        kwargs['border_width'] = border_width or 1
    annot = page.add_freetext_annot(rect, text, **kwargs)
    annot.set_info(title=author.strip())
    annot.update()
    return annot.xref


def add_caret(doc, page_number, point, content='', author='', color=(0.1, 0.4, 0.9)):
    page = doc[page_number]
    annot = page.add_caret_annot(fitz.Point(point))
    annot.set_colors(stroke=color)
    return _finish(annot, author, content)


def add_file_attachment(doc, page_number, point, data, filename, description='', icon='PushPin', author=''):
    if not data:
        raise OperationError('The attachment is empty.')
    page = doc[page_number]
    annot = page.add_file_annot(fitz.Point(point), data, filename, desc=description or filename,
                                icon=icon if icon in ATTACHMENT_ICONS else 'PushPin')
    return _finish(annot, author, description)


def attachment_data(doc, page_number, xref):
    page = doc[page_number]
    annot = page.load_annot(xref)
    if annot is None or annot.type[0] != fitz.PDF_ANNOT_FILE_ATTACHMENT:
        raise OperationError('Select a file attachment.')
    info = annot.file_info
    return info.get('filename') or 'attachment', annot.get_file()


# ---------------------------------------------------------------- replies and states

def _now():
    return fitz.get_pdf_now()


def add_reply(doc, page_number, parent_xref, text, author=''):
    """Add a reply to an annotation as an IRT text annotation (hidden icon)."""
    if not text.strip():
        raise OperationError('Enter a reply.')
    page = doc[page_number]
    parent = page.load_annot(parent_xref)
    if parent is None:
        raise OperationError('The annotation no longer exists.')
    reply = page.add_text_annot(parent.rect.tl, text.strip(), icon='Comment')
    reply.set_irt_xref(parent.xref)
    reply.set_info(title=author.strip(), content=text.strip())
    reply.set_flags(fitz.PDF_ANNOT_IS_HIDDEN | fitz.PDF_ANNOT_IS_NO_VIEW)
    reply.update()
    return reply.xref


def set_review_state(doc, page_number, parent_xref, state, author=''):
    """Record a review state (Accepted, Rejected, …) as a state reply, per the PDF spec."""
    if state not in REVIEW_STATES:
        raise OperationError('Unknown review state.')
    xref = add_reply(doc, page_number, parent_xref, f'{author or "Reviewer"} set status to {state}', author)
    doc.xref_set_key(xref, 'State', fitz.get_pdf_str(state))
    doc.xref_set_key(xref, 'StateModel', fitz.get_pdf_str('Review'))
    doc._reset_page_refs()
    return xref


def thread(doc):
    """Return annotations with their replies and latest review state.

    Each item: page, xref, kind, content, author, modified, rect, replies[], state.
    Reply annotations are nested under their parent rather than listed separately.
    """
    items, by_xref, replies = [], {}, []
    for number in range(doc.page_count):
        for annot in doc[number].annots() or ():
            if annot.type[0] in (fitz.PDF_ANNOT_POPUP, fitz.PDF_ANNOT_WIDGET):
                continue
            info = annot.info
            entry = {'page': number, 'xref': annot.xref, 'kind': annot.type[1], 'content': info.get('content', ''),
                     'author': info.get('title', ''), 'modified': info.get('modDate', ''), 'rect': tuple(annot.rect),
                     'replies': [], 'state': None}
            irt = annot.irt_xref
            if irt:
                state = doc.xref_get_key(annot.xref, 'State')
                entry['state'] = state[1].strip('()') if state[0] == 'string' else None
                replies.append((irt, entry))
            else:
                items.append(entry)
                by_xref[annot.xref] = entry
    for parent_xref, entry in replies:
        parent = by_xref.get(parent_xref)
        if parent is None:
            continue
        if entry['state']:
            parent['state'] = entry['state']
        else:
            parent['replies'].append(entry)
    return items


def get_flags(doc, page_number, xref):
    page = doc[page_number]
    annot = page.load_annot(xref)
    return {name: bool(annot.flags & flag) for name, flag in FLAG_NAMES}


def set_flags(doc, page_number, xref, flags):
    page = doc[page_number]
    annot = page.load_annot(xref)
    if annot is None:
        raise OperationError('The annotation no longer exists.')
    value = 0
    for name, flag in FLAG_NAMES:
        if flags.get(name, bool(annot.flags & flag)):
            value |= flag
    annot.set_flags(value)
    annot.update()


# ---------------------------------------------------------------- flatten

def flatten_annotations(doc, keep_forms=True):
    """Burn review annotations into page content. Refuses signed documents."""
    from .common import signed_signature_fields
    if signed_signature_fields(doc):
        raise OperationError('Flatten an unsigned copy; signed documents must remain intact.')
    if not any(page.first_annot for page in doc):
        raise OperationError('This document has no annotations to flatten.')
    doc.bake(annots=True, widgets=not keep_forms)


# ---------------------------------------------------------------- interchange

_EXPORTABLE = {
    fitz.PDF_ANNOT_TEXT, fitz.PDF_ANNOT_FREE_TEXT, fitz.PDF_ANNOT_LINE, fitz.PDF_ANNOT_SQUARE,
    fitz.PDF_ANNOT_CIRCLE, fitz.PDF_ANNOT_POLYGON, fitz.PDF_ANNOT_POLY_LINE, fitz.PDF_ANNOT_HIGHLIGHT,
    fitz.PDF_ANNOT_UNDERLINE, fitz.PDF_ANNOT_SQUIGGLY, fitz.PDF_ANNOT_STRIKE_OUT, fitz.PDF_ANNOT_INK,
    fitz.PDF_ANNOT_CARET, fitz.PDF_ANNOT_STAMP,
}


def export_json(doc):
    """Serialize review annotations to a JSON string (a portable, pdfLX-specific format)."""
    records, index_of = [], {}
    for number in range(doc.page_count):
        for annot in doc[number].annots() or ():
            if annot.type[0] not in _EXPORTABLE:
                continue
            info = annot.info
            border = annot.border or {}
            record = {
                'page': number, 'type': annot.type[1], 'rect': list(annot.rect),
                'content': info.get('content', ''), 'author': info.get('title', ''),
                'subject': info.get('subject', ''), 'modified': info.get('modDate', ''),
                'colors': {k: list(v) if v else None for k, v in (annot.colors or {}).items()},
                'opacity': annot.opacity if annot.opacity is not None and annot.opacity >= 0 else 1,
                'width': border.get('width', 1), 'dashes': border.get('dashes') or None,
                'flags': annot.flags,
                'vertices': [list(v) for v in annot.vertices] if annot.vertices else None,
                'line_ends': list(annot.line_ends) if annot.line_ends else None,
                'irt': index_of.get(annot.irt_xref),
            }
            if annot.type[0] == fitz.PDF_ANNOT_INK:
                record['vertices'] = [[list(p) for p in stroke] for stroke in annot.vertices or ()]
            if annot.type[0] == fitz.PDF_ANNOT_STAMP:
                record['stamp'] = doc.xref_get_key(annot.xref, 'Name')[1].lstrip('/')
            state = doc.xref_get_key(annot.xref, 'State')
            if state[0] == 'string':
                record['state'] = state[1].strip('()')
            index_of[annot.xref] = len(records)
            records.append(record)
    return json.dumps({'format': 'pdflx-annotations', 'version': 1,
                       'exported': datetime.datetime.now().isoformat(timespec='seconds'),
                       'page_count': doc.page_count, 'annotations': records}, ensure_ascii=False, indent=1)


def import_json(doc, text):
    """Recreate annotations from ``export_json`` output. Returns (imported, skipped)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise OperationError(f'The file is not valid JSON: {error}.')
    if data.get('format') != 'pdflx-annotations':
        raise OperationError('The file is not a pdfLX annotation export.')
    created, imported, skipped = {}, 0, 0
    for index, record in enumerate(data.get('annotations', ())):
        number = record.get('page', -1)
        if not 0 <= number < doc.page_count:
            skipped += 1
            continue
        page = doc[number]
        rect = fitz.Rect(record['rect'])
        kind = record.get('type')
        vertices = record.get('vertices')
        try:
            if kind == 'Text':
                annot = page.add_text_annot(rect.tl, record.get('content', ''))
            elif kind == 'FreeText':
                annot = page.add_freetext_annot(rect, record.get('content', '') or ' ')
            elif kind == 'Square':
                annot = page.add_rect_annot(rect)
            elif kind == 'Circle':
                annot = page.add_circle_annot(rect)
            elif kind == 'Line':
                annot = page.add_line_annot(vertices[0], vertices[1])
                if record.get('line_ends'):
                    annot.set_line_ends(*record['line_ends'])
            elif kind == 'Polygon':
                annot = page.add_polygon_annot(vertices)
            elif kind == 'PolyLine':
                annot = page.add_polyline_annot(vertices)
            elif kind == 'Ink':
                annot = page.add_ink_annot(vertices)
            elif kind in ('Highlight', 'Underline', 'Squiggly', 'StrikeOut'):
                quads = [fitz.Quad(vertices[i:i + 4]) for i in range(0, len(vertices or ()), 4)] or [rect.quad]
                method = {'Highlight': page.add_highlight_annot, 'Underline': page.add_underline_annot,
                          'Squiggly': page.add_squiggly_annot, 'StrikeOut': page.add_strikeout_annot}[kind]
                annot = method(quads)
            elif kind == 'Caret':
                annot = page.add_caret_annot(rect.tl)
            elif kind == 'Stamp':
                annot = page.add_stamp_annot(rect)
            else:
                skipped += 1
                continue
        except Exception:
            skipped += 1
            continue
        colors = record.get('colors') or {}
        if kind not in ('FreeText',):
            try:
                annot.set_colors(stroke=colors.get('stroke'), fill=colors.get('fill') if kind in
                                 ('Square', 'Circle', 'Polygon', 'Line') else None)
            except Exception:
                pass
        try:
            annot.set_border(width=record.get('width', 1), dashes=record.get('dashes'))
        except Exception:
            pass
        annot.set_info(content=record.get('content', ''), title=record.get('author', ''),
                       subject=record.get('subject', ''))
        if record.get('opacity', 1) < 1:
            annot.set_opacity(record['opacity'])
        if record.get('flags'):
            annot.set_flags(record['flags'])
        parent = created.get(record.get('irt'))
        if parent:
            annot.set_irt_xref(parent)
        annot.update()
        if record.get('state'):
            doc.xref_set_key(annot.xref, 'State', fitz.get_pdf_str(record['state']))
            doc.xref_set_key(annot.xref, 'StateModel', fitz.get_pdf_str('Review'))
        created[index] = annot.xref
        imported += 1
    doc._reset_page_refs()
    return imported, skipped


def summary_markdown(doc, title=''):
    """Return a Markdown review summary grouped by page, with replies and states."""
    lines = [f'# Comment summary{": " + title if title else ""}', '']
    items = thread(doc)
    if not items:
        return '\n'.join(lines + ['No comments.'])
    by_page = {}
    for item in items:
        by_page.setdefault(item['page'], []).append(item)
    for page in sorted(by_page):
        label = doc[page].get_label() if hasattr(doc[page], 'get_label') else ''
        lines.append(f'## Page {page + 1}{" (" + label + ")" if label else ""}')
        lines.append('')
        for item in by_page[page]:
            state = f" — **{item['state']}**" if item['state'] else ''
            author = item['author'] or 'Unknown'
            content = (item['content'] or '').replace('\n', ' ').strip() or '(no text)'
            lines.append(f"- **{item['kind']}** by {author}{state}: {content}")
            for reply in item['replies']:
                text = (reply['content'] or '').replace('\n', ' ').strip()
                lines.append(f"  - ↳ {reply['author'] or 'Unknown'}: {text}")
        lines.append('')
    return '\n'.join(lines)


def summary_pdf(doc, title=''):
    """Render the comment summary into a new PDF document."""
    text = summary_markdown(doc, title)
    story = fitz.Story(html=_markdown_to_html(text), user_css='body{font-family:sans-serif;font-size:10pt}'
                       'h1{font-size:16pt}h2{font-size:12pt;margin-top:10pt}li{margin-bottom:3pt}')
    writer_buffer = io.BytesIO()
    writer = fitz.DocumentWriter(writer_buffer)
    mediabox = fitz.paper_rect('a4')
    where = mediabox + (48, 48, -48, -48)
    more = True
    while more:
        device = writer.begin_page(mediabox)
        more, _filled = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    return fitz.open('pdf', writer_buffer.getvalue())


def _markdown_to_html(markdown):
    out = []
    for line in markdown.splitlines():
        escaped = html.escape(line.strip())
        escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
        if line.startswith('# '):
            out.append(f'<h1>{escaped[2:]}</h1>')
        elif line.startswith('## '):
            out.append(f'<h2>{escaped[3:]}</h2>')
        elif line.startswith('  - '):
            out.append(f'<p style="margin-left:18pt;color:#444">{escaped[2:]}</p>')
        elif line.startswith('- '):
            out.append(f'<p>• {escaped[2:]}</p>')
        elif escaped:
            out.append(f'<p>{escaped}</p>')
    return '\n'.join(out)
