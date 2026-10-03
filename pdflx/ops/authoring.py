"""Authoring: Markdown/HTML documents (Story), rich text boxes, watermarks, and mail merge."""
import csv
import html
import io
import re

import pymupdf as fitz

from .common import OperationError, check_cancel, report

BASE_CSS = """
body { font-family: sans-serif; font-size: 11pt; line-height: 1.35; }
h1 { font-size: 20pt; margin: 0 0 8pt 0; }
h2 { font-size: 15pt; margin: 14pt 0 6pt 0; }
h3 { font-size: 12.5pt; margin: 12pt 0 4pt 0; }
p { margin: 0 0 6pt 0; }
li { margin-bottom: 2pt; }
code, pre { font-family: monospace; font-size: 9.5pt; }
pre { background-color: #f2f2f2; padding: 6pt; }
table { border-collapse: collapse; }
td, th { border: 1px solid #888; padding: 3pt 5pt; }
th { background-color: #eee; }
.toc-entry { margin: 0 0 3pt 0; }
"""


# ---------------------------------------------------------------- Markdown

def _inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])', r'<i>\1</i>', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)\s]+)\)', r'<a href="\2">\1</a>', text)
    return text


def markdown_to_html(markdown):
    """Convert a practical Markdown subset: headings, emphasis, code, lists, links,
    tables, block quotes, horizontal rules, and page breaks (a line with '\\pagebreak')."""
    lines = markdown.replace('\r\n', '\n').split('\n')
    out, paragraph, index = [], [], 0

    def flush():
        if paragraph:
            out.append('<p>' + _inline(' '.join(paragraph)) + '</p>')
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith('```'):
            flush()
            block = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith('```'):
                block.append(html.escape(lines[index]))
                index += 1
            out.append('<pre>' + '\n'.join(block) + '</pre>')
        elif not stripped:
            flush()
        elif stripped == '\\pagebreak':
            flush()
            out.append('<div style="page-break-before: always"></div>')
        elif re.match(r'^#{1,6}\s', stripped):
            flush()
            level = len(stripped) - len(stripped.lstrip('#'))
            out.append(f'<h{level}>{_inline(stripped[level:].strip())}</h{level}>')
        elif re.match(r'^(-{3,}|\*{3,})$', stripped):
            flush()
            out.append('<hr/>')
        elif re.match(r'^[-*+]\s', stripped) or re.match(r'^\d+[.)]\s', stripped):
            flush()
            ordered = bool(re.match(r'^\d', stripped))
            tag = 'ol' if ordered else 'ul'
            items = []
            while index < len(lines) and (re.match(r'^\s*[-*+]\s', lines[index]) or re.match(r'^\s*\d+[.)]\s', lines[index])):
                items.append('<li>' + _inline(re.sub(r'^\s*([-*+]|\d+[.)])\s', '', lines[index])) + '</li>')
                index += 1
            out.append(f'<{tag}>' + ''.join(items) + f'</{tag}>')
            continue
        elif stripped.startswith('>'):
            flush()
            quote = []
            while index < len(lines) and lines[index].strip().startswith('>'):
                quote.append(lines[index].strip()[1:].strip())
                index += 1
            out.append('<blockquote><p>' + _inline(' '.join(quote)) + '</p></blockquote>')
            continue
        elif stripped.startswith('|') and index + 1 < len(lines) and re.match(r'^\|?\s*:?-{2,}', lines[index + 1].strip()):
            flush()
            header = [cell.strip() for cell in stripped.strip('|').split('|')]
            index += 2
            rows = []
            while index < len(lines) and lines[index].strip().startswith('|'):
                rows.append([cell.strip() for cell in lines[index].strip().strip('|').split('|')])
                index += 1
            table = '<table><tr>' + ''.join(f'<th>{_inline(c)}</th>' for c in header) + '</tr>'
            for row in rows:
                table += '<tr>' + ''.join(f'<td>{_inline(c)}</td>' for c in row) + '</tr>'
            out.append(table + '</table>')
            continue
        else:
            paragraph.append(stripped)
        index += 1
    flush()
    return '\n'.join(out)


# ---------------------------------------------------------------- Story documents

def compose(content, is_markdown=True, paper=(595, 842), margins=(56, 56, 56, 56), css='', toc=False,
            toc_title='Contents', page_numbers=True, title=''):
    """Lay out HTML/Markdown across as many pages as needed and return a new document.

    With ``toc`` a linked table of contents is generated from h1–h3 headings.
    The layout is repeated until the page numbers it lists are stable, and
    headings also become PDF bookmarks.
    """
    body = markdown_to_html(content) if is_markdown else content
    if not body.strip():
        raise OperationError('Enter some content first.')
    width, height = paper
    left, top, right, bottom = margins
    if left + right >= width - 36 or top + bottom >= height - 36:
        raise OperationError('The margins are too large for the paper size.')
    mediabox = fitz.Rect(0, 0, width, height)
    where = fitz.Rect(left, top, width - right, height - bottom)
    user_css = BASE_CSS + (css or '')

    def rectfn(rect_num, filled):
        return mediabox, where, None

    if toc:
        def contentfn(positions):
            headings = [p for p in positions if p.heading in (1, 2, 3) and p.id and p.text and not
                        p.id.startswith('pdflx-toc')]
            entries = ''.join(
                f'<p class="toc-entry" style="margin-left:{(p.heading - 1) * 14}pt">'
                f'<a href="#{html.escape(p.id)}">{html.escape(p.text)}</a> … {p.page_num}</p>'
                for p in headings)
            return (f'<h1 id="pdflx-toc">{html.escape(toc_title)}</h1>{entries}'
                    f'<div style="page-break-before: always"></div>{body}')
        output = fitz.Story.write_stabilized_with_links(contentfn, rectfn, user_css=user_css, add_header_ids=True)
    else:
        story = fitz.Story(html=body, user_css=user_css)
        story.add_header_ids()
        output = story.write_with_links(rectfn)
    _bookmarks_from_headings(output)
    if page_numbers:
        for page in output:
            label = f'{page.number + 1} / {output.page_count}'
            size = 9
            length = fitz.get_text_length(label, fontsize=size)
            page.insert_text(((width - length) / 2, height - bottom / 2), label, fontsize=size, color=(0.35,) * 3)
    if title:
        output.set_metadata({'title': title})
    return output


def _bookmarks_from_headings(doc):
    toc = []
    for page in doc:
        for block in page.get_text('dict')['blocks']:
            for line in block.get('lines', ()):
                spans = [s for s in line['spans'] if s['text'].strip()]
                if not spans:
                    continue
                size = max(s['size'] for s in spans)
                level = 1 if size >= 19 else 2 if size >= 14.5 else 3 if size >= 12.4 and all(
                    s['flags'] & 16 for s in spans) else None
                if level:
                    text = ''.join(s['text'] for s in spans).strip()
                    if toc and toc[-1][2] == page.number + 1 and toc[-1][0] == level and len(text) < 3:
                        continue
                    toc.append([min(level, (toc[-1][0] + 1) if toc else 1), text, page.number + 1])
    if toc:
        doc.set_toc(toc)


# ---------------------------------------------------------------- rich text box

def insert_rich_text(doc, page_number, rect, content, is_markdown=True, css='', opacity=1.0, overlay=True):
    """Place formatted text into an area, shrinking it to fit when necessary."""
    page = doc[page_number]
    rect = fitz.Rect(rect)
    if rect.is_empty:
        raise OperationError('Select an area on the page first.')
    body = markdown_to_html(content) if is_markdown else content
    if not body.strip():
        raise OperationError('Enter some text first.')
    # The rectangle is in unrotated coordinates; rotate= keeps the text upright as displayed.
    spare, scale = page.insert_htmlbox(rect, body,
                                       css=BASE_CSS + (css or ''), scale_low=0, rotate=page.rotation,
                                       opacity=opacity, overlay=overlay)
    if spare < 0:
        raise OperationError('The text does not fit in the selected area.')
    return scale


# ---------------------------------------------------------------- watermarks

def watermark(doc, pages, text, font_size=48, color=(0.6, 0.6, 0.6), opacity=0.2, angle=45, tile=False,
              behind=False, fontfile=None):
    """Write rotated (any angle) text, centred or tiled, using TextWriter morphing."""
    if not text.strip():
        raise OperationError('Enter the watermark text.')
    font = fitz.Font(fontfile=fontfile) if fontfile else fitz.Font('helv')
    if not fontfile and any(ord(ch) > 255 for ch in text):
        try:
            from ..utils import get_default_unicode_font_path
            path = get_default_unicode_font_path()
            if path:
                font = fitz.Font(fontfile=path)
        except Exception:
            pass
    length = font.text_length(text, fontsize=font_size)
    for number in pages:
        page = doc[number]
        # TextWriter.write_text maps visual coordinates onto rotated pages itself, so
        # text is laid out in visual space and turned about the visible centre.
        bounds = page.rect
        pivot = fitz.Point((bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2)
        writer = fitz.TextWriter(bounds, opacity=opacity, color=color)
        if tile:
            span = max(bounds.width, bounds.height) * 1.5
            step_x, step_y = length + font_size * 2, font_size * 4
            centres = [fitz.Point(pivot.x + dx, pivot.y + dy) for dy in _frange(-span, span, step_y)
                       for dx in _frange(-span, span, step_x)]
        else:
            centres = [pivot]
        for centre in centres:
            writer.append(fitz.Point(centre.x - length / 2, centre.y + font_size * 0.35), text,
                          font=font, fontsize=font_size)
        turn = fitz.Matrix(angle)  # write_text already compensates for page rotation
        matrix = fitz.Matrix(1, 0, 0, 1, -pivot.x, -pivot.y) * turn * fitz.Matrix(1, 0, 0, 1, pivot.x, pivot.y)
        writer.write_text(page, overlay=not behind, matrix=matrix)


def _frange(start, stop, step):
    value = start
    while value < stop:
        yield value
        value += step


# ---------------------------------------------------------------- mail merge

def read_csv(path_or_text, from_text=False):
    if from_text:
        text = path_or_text
    else:
        with open(path_or_text, encoding='utf-8-sig') as handle:
            text = handle.read()
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=',;\t')
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        raise OperationError('The CSV file has no data rows.')
    return rows


def merge_targets(doc):
    """Fields and {{placeholders}} present in a template document."""
    fields = sorted({w.field_name for page in doc for w in page.widgets() or () if w.field_name})
    placeholders = sorted({match for page in doc for match in re.findall(r'\{\{\s*([\w .-]+?)\s*\}\}', page.get_text())})
    return fields, placeholders


def merge_record(template_bytes, record, flatten=False, password=''):
    """Fill one record into a fresh copy of the template; returns a Document."""
    doc = fitz.open('pdf', template_bytes)
    if doc.needs_pass:
        doc.authenticate(password)
    for page in doc:
        for widget in page.widgets() or ():
            if widget.field_name in record:
                value = record[widget.field_name]
                if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX, fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                    widget.field_value = widget.on_state() if str(value).strip().lower() in (
                        '1', 'true', 'yes', 'x', 'on', str(widget.on_state()).lower()) else 'Off'
                elif widget.field_type in (fitz.PDF_WIDGET_TYPE_TEXT, fitz.PDF_WIDGET_TYPE_COMBOBOX,
                                           fitz.PDF_WIDGET_TYPE_LISTBOX):
                    widget.field_value = str(value)
                else:
                    continue
                widget.update()
        for match in set(re.findall(r'\{\{\s*([\w .-]+?)\s*\}\}', page.get_text())):
            if match not in record:
                continue
            value = str(record[match])
            for pattern in {'{{' + match + '}}', '{{ ' + match + ' }}'}:
                for rect in page.search_for(pattern):
                    size = max(5, rect.height * 0.78)
                    while size > 5 and fitz.get_text_length(value, fontsize=size) > rect.width * 3:
                        size -= 0.5
                    page.add_redact_annot(rect, text=value, fontname='helv', fontsize=size, align=0,
                                          fill=False, cross_out=False)
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)
    if flatten:
        doc.bake(annots=False, widgets=True)
    return doc


def mail_merge(template_bytes, rows, flatten=False, password='', progress=None, cancel=None):
    """Yield (index, record, Document) for each CSV row."""
    for index, record in enumerate(rows):
        check_cancel(cancel)
        yield index, record, merge_record(template_bytes, record, flatten, password)
        report(progress, index + 1, len(rows))


def merge_filename(pattern, record, index):
    try:
        name = pattern.format(n=index + 1, **{re.sub(r'\W', '_', k): v for k, v in record.items()})
    except (KeyError, IndexError, ValueError):
        name = f'merged-{index + 1}'
    name = re.sub(r'[\\/:*?"<>|]+', '-', name).strip() or f'merged-{index + 1}'
    return name if name.lower().endswith('.pdf') else name + '.pdf'


def combine(documents):
    output = fitz.open()
    for doc in documents:
        output.insert_pdf(doc)
    return output
