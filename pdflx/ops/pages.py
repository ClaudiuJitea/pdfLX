"""Page-level workflows: split, collate, blank detection, overlays, imposition, labels, boxes."""
import math
import re

import pymupdf as fitz

from .common import OperationError, check_cancel, report

# Common sheet sizes in points (portrait).
PAPER_SIZES = (
    ('a4', 'A4', fitz.paper_size('a4')),
    ('letter', 'US Letter', fitz.paper_size('letter')),
    ('legal', 'US Legal', fitz.paper_size('legal')),
    ('a3', 'A3', fitz.paper_size('a3')),
    ('a5', 'A5', fitz.paper_size('a5')),
    ('tabloid', 'Tabloid', fitz.paper_size('tabloid')),
)


def paper_size(key, landscape=False):
    size = dict((k, s) for k, _label, s in PAPER_SIZES).get(key)
    if size is None:
        raise OperationError(f'Unknown paper size: {key}.')
    width, height = size
    return (height, width) if landscape else (width, height)


# ---------------------------------------------------------------- split / extract

def split_plan(doc, mode, value=None):
    """Return a list of (label, [page indices]) describing output parts.

    mode: 'every' (value = pages per part), 'ranges' (value = list of index lists),
    'bookmarks' (top-level bookmarks), 'single' (one file per page).
    """
    count = doc.page_count
    if mode == 'single':
        return [(f'page-{n + 1}', [n]) for n in range(count)]
    if mode == 'every':
        size = int(value or 0)
        if size < 1:
            raise OperationError('Enter how many pages each part should contain.')
        return [(f'pages-{s + 1}-{min(s + size, count)}', list(range(s, min(s + size, count))))
                for s in range(0, count, size)]
    if mode == 'ranges':
        parts = [list(p) for p in (value or ()) if p]
        if not parts:
            raise OperationError('Enter at least one page range.')
        return [(f'part-{i + 1}', p) for i, p in enumerate(parts)]
    if mode == 'bookmarks':
        starts = []
        for level, title, page, *_rest in doc.get_toc(simple=True):
            if level == 1 and 1 <= page <= count and (not starts or starts[-1][1] != page - 1):
                starts.append((title, page - 1))
        if not starts:
            raise OperationError('This document has no top-level bookmarks to split on.')
        if starts[0][1] > 0:
            starts.insert(0, ('front-matter', 0))
        plan = []
        for index, (title, start) in enumerate(starts):
            end = starts[index + 1][1] if index + 1 < len(starts) else count
            if end > start:
                plan.append((safe_filename(title) or f'part-{index + 1}', list(range(start, end))))
        return plan
    raise OperationError(f'Unknown split mode: {mode}.')


def safe_filename(text, limit=60):
    text = re.sub(r'[^\w\s.-]', '', str(text), flags=re.UNICODE).strip()
    return re.sub(r'\s+', '-', text)[:limit]


def extract_pages(doc, pages):
    """Return a new document containing ``pages`` (in the given order)."""
    if not pages:
        raise OperationError('Select at least one page.')
    output = fitz.open()
    for number in pages:
        output.insert_pdf(doc, from_page=number, to_page=number)
    _copy_labels_and_meta(doc, output)
    return output


def _copy_labels_and_meta(source, target):
    meta = {k: v for k, v in (source.metadata or {}).items()
            if k in ('title', 'author', 'subject', 'keywords', 'creator')}
    if meta:
        target.set_metadata(meta)


def write_parts(doc, plan, directory, stem, progress=None, cancel=None):
    """Save each part of a split plan into ``directory``. Returns written paths."""
    import os
    written = []
    for index, (label, pages) in enumerate(plan):
        check_cancel(cancel)
        path = os.path.join(directory, f'{stem}-{label}.pdf')
        suffix = 2
        while os.path.exists(path):
            path = os.path.join(directory, f'{stem}-{label}-{suffix}.pdf')
            suffix += 1
        with extract_pages(doc, pages) as part:
            part.save(path, garbage=3, deflate=True)
        written.append(path)
        report(progress, index + 1, len(plan))
    return written


# ---------------------------------------------------------------- ordering

def reverse_order(count):
    return list(range(count - 1, -1, -1))


def collate_order(count, odd_count, evens_reversed=True):
    """Order for interleaving a scan of all odd sides followed by all even sides.

    The first ``odd_count`` pages are fronts; the rest are backs, usually scanned
    in reverse when the stack was flipped.
    """
    if not 0 < odd_count < count:
        raise OperationError('The document must contain both front and back pages.')
    fronts = list(range(odd_count))
    backs = list(range(odd_count, count))
    if evens_reversed:
        backs.reverse()
    order = []
    for index in range(max(len(fronts), len(backs))):
        if index < len(fronts):
            order.append(fronts[index])
        if index < len(backs):
            order.append(backs[index])
    return order


def apply_order(doc, order):
    """Reorder pages in place. ``order`` lists old indices in their new positions."""
    if sorted(order) != list(range(doc.page_count)):
        raise OperationError('The new order must contain every page exactly once.')
    doc.select(list(order))


def order_remap(order):
    """Build the old→new page index mapping for editor page models."""
    mapping = {old: new for new, old in enumerate(order)}
    return lambda page: mapping.get(page)


def insert_document(doc, source, at, pages=None):
    """Insert pages from ``source`` before position ``at``; returns inserted count."""
    pages = list(range(source.page_count)) if pages is None else list(pages)
    if not pages:
        raise OperationError('Select at least one page to insert.')
    position = max(0, min(at, doc.page_count))
    if source.is_pdf:
        for offset, number in enumerate(pages):
            doc.insert_pdf(source, from_page=number, to_page=number, start_at=position + offset)
    else:
        with fitz.open('pdf', source.convert_to_pdf()) as converted:
            for offset, number in enumerate(pages):
                doc.insert_pdf(converted, from_page=number, to_page=number, start_at=position + offset)
    return len(pages)


def insert_remap(at, count):
    return lambda page: page + count if page >= at else page


def delete_remap(deleted):
    deleted = sorted(set(deleted))
    def remap(page):
        if page in deleted:
            return None
        return page - sum(1 for d in deleted if d < page)
    return remap


# ---------------------------------------------------------------- blank pages

def blank_pages(doc, threshold=0.995, pages=None, progress=None, cancel=None):
    """Return indices of pages whose dominant colour covers at least ``threshold``.

    Pages with annotations or form fields are never reported as blank.
    """
    candidates = range(doc.page_count) if pages is None else pages
    blank = []
    total = len(list(candidates)) if pages is not None else doc.page_count
    for done, number in enumerate(candidates):
        check_cancel(cancel)
        page = doc[number]
        if page.first_annot is not None or page.first_widget is not None:
            continue
        pix = page.get_pixmap(dpi=36, colorspace=fitz.csGRAY, alpha=False, annots=False)
        ratio, colour = pix.color_topusage()
        if ratio >= threshold and colour[0] >= 0xE0:
            blank.append(number)
        report(progress, done + 1, total)
    return blank


# ---------------------------------------------------------------- geometry

def remove_rotation(doc, pages):
    """Bake /Rotate into page content so pages read upright with rotation 0."""
    changed = []
    for number in pages:
        page = doc[number]
        if page.rotation:
            page.remove_rotation()
            changed.append(number)
    return changed


def normalize_sizes(doc, width, height, pages=None, keep_proportion=True, margin=0):
    """Return a new document with selected pages placed onto uniform sheets.

    Unselected pages are copied unchanged. Links and annotations are not carried
    over to rescaled pages because ``show_pdf_page`` places page appearances.
    """
    selected = set(range(doc.page_count) if pages is None else pages)
    output = fitz.open()
    target = fitz.Rect(margin, margin, width - margin, height - margin)
    if target.is_empty:
        raise OperationError('The margin is too large for the sheet size.')
    for number in range(doc.page_count):
        if number in selected:
            page = output.new_page(width=width, height=height)
            page.show_pdf_page(target, doc, number, keep_proportion=keep_proportion)
        else:
            output.insert_pdf(doc, from_page=number, to_page=number)
    _copy_labels_and_meta(doc, output)
    return output


def nup(doc, columns, rows, sheet=(595, 842), margin=18, gap=6, order='rows', borders=False,
        progress=None, cancel=None):
    """Return a new document with ``columns × rows`` pages per sheet."""
    if columns < 1 or rows < 1 or columns * rows < 2:
        raise OperationError('Choose at least two pages per sheet.')
    width, height = sheet
    cell_w = (width - 2 * margin - (columns - 1) * gap) / columns
    cell_h = (height - 2 * margin - (rows - 1) * gap) / rows
    if cell_w <= 10 or cell_h <= 10:
        raise OperationError('The margins and gaps leave no room for pages.')
    per_sheet = columns * rows
    output = fitz.open()
    for start in range(0, doc.page_count, per_sheet):
        check_cancel(cancel)
        sheet_page = output.new_page(width=width, height=height)
        for slot in range(per_sheet):
            number = start + slot
            if number >= doc.page_count:
                break
            col, row = (slot % columns, slot // columns) if order == 'rows' else (slot // rows, slot % rows)
            x0 = margin + col * (cell_w + gap)
            y0 = margin + row * (cell_h + gap)
            cell = fitz.Rect(x0, y0, x0 + cell_w, y0 + cell_h)
            sheet_page.show_pdf_page(cell, doc, number)
            if borders:
                sheet_page.draw_rect(cell, color=(0.6, 0.6, 0.6), width=0.5)
        report(progress, min(start + per_sheet, doc.page_count), doc.page_count)
    return output


def booklet_order(count):
    """Return sheet-side slots for saddle-stitch printing; None marks a blank page."""
    total = int(math.ceil(count / 4.0) * 4) or 4
    pages = list(range(count)) + [None] * (total - count)
    sides = []
    for sheet in range(total // 4):
        outer_left, outer_right = pages[total - 1 - 2 * sheet], pages[2 * sheet]
        inner_left, inner_right = pages[2 * sheet + 1], pages[total - 2 - 2 * sheet]
        sides.append((outer_left, outer_right))
        sides.append((inner_left, inner_right))
    return sides


def booklet(doc, sheet=(842, 595), margin=12, progress=None, cancel=None):
    """Return a two-up saddle-stitch booklet for duplex printing (flip on short edge)."""
    width, height = sheet
    if width < height:
        width, height = height, width
    half = (width - 2 * margin) / 2
    output = fitz.open()
    sides = booklet_order(doc.page_count)
    for index, (left, right) in enumerate(sides):
        check_cancel(cancel)
        page = output.new_page(width=width, height=height)
        for slot, number in ((0, left), (1, right)):
            if number is not None:
                x0 = margin + slot * half
                page.show_pdf_page(fitz.Rect(x0, margin, x0 + half, height - margin), doc, number)
        report(progress, index + 1, len(sides))
    return output


def overlay(doc, source, source_page, pages, behind=True, keep_proportion=True, opacity_ok=True):
    """Stamp a page of ``source`` behind (letterhead) or above (overlay) each page."""
    if not 0 <= source_page < source.page_count:
        raise OperationError('Choose a page that exists in the overlay document.')
    if source is doc:
        raise OperationError('Choose a different document for the overlay.')
    for number in pages:
        page = doc[number]
        page.show_pdf_page(page.rect, source, source_page, keep_proportion=keep_proportion,
                           overlay=not behind, rotate=-page.rotation)


# ---------------------------------------------------------------- headers / footers / Bates

POSITIONS = ('top-left', 'top-center', 'top-right', 'bottom-left', 'bottom-center', 'bottom-right')


def expand_template(template, page_number, total, bates=None, filename='', date=''):
    """Expand {page}, {total}, {bates}, {file}, and {date} placeholders."""
    values = {'page': page_number, 'total': total, 'bates': bates or '', 'file': filename, 'date': date}
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError) as error:
        raise OperationError(f'Invalid placeholder in "{template}": {error}.')


def bates_number(prefix, number, digits, suffix=''):
    return f'{prefix}{number:0{max(1, int(digits))}d}{suffix}'


def _font_for(texts, fontfile=None):
    """Use Helvetica for Latin-1 text and a Unicode TrueType font otherwise."""
    if fontfile is None and any(ord(char) > 255 for text in texts for char in text):
        try:
            from ..utils import get_default_unicode_font_path
            fontfile = get_default_unicode_font_path()
        except Exception:
            fontfile = None
    if fontfile:
        return 'pdflxheader', fontfile, fitz.Font(fontfile=fontfile)
    return 'helv', None, fitz.Font('helv')


def add_headers_footers(doc, pages, texts, font_size=9, margin=24, color=(0, 0, 0),
                        fontfile=None, bates=None, filename='', date=''):
    """Write header/footer text on each page in visual (rotated) orientation.

    ``texts`` maps POSITIONS to templates. ``bates`` is a dict with prefix,
    start, digits, and suffix, or None.
    """
    texts = {key: value for key, value in texts.items() if value and value.strip()}
    if not texts:
        raise OperationError('Enter text for at least one header or footer position.')
    if any(key not in POSITIONS for key in texts):
        raise OperationError('Unknown header/footer position.')
    total = doc.page_count
    fontname, fontfile, font = _font_for(list(texts.values()) + [filename], fontfile)
    for index, number in enumerate(pages):
        page = doc[number]
        visual = page.rect
        number_text = None
        if bates:
            number_text = bates_number(bates.get('prefix', ''), int(bates.get('start', 1)) + index,
                                       bates.get('digits', 6), bates.get('suffix', ''))
        for position, template in texts.items():
            text = expand_template(template, number + 1, total, number_text, filename, date)
            vertical, horizontal = position.split('-')
            width = font.text_length(text, fontsize=font_size)
            if horizontal == 'left':
                x = margin
            elif horizontal == 'right':
                x = visual.width - margin - width
            else:
                x = (visual.width - width) / 2
            y = margin + font_size if vertical == 'top' else visual.height - margin
            point = fitz.Point(x, y) * page.derotation_matrix
            page.insert_text(point, text, fontsize=font_size, fontname=fontname, fontfile=fontfile,
                             color=color, rotate=page.rotation)


# ---------------------------------------------------------------- page labels

LABEL_STYLES = (
    ('D', 'Arabic (1, 2, 3)'),
    ('r', 'Roman lowercase (i, ii, iii)'),
    ('R', 'Roman uppercase (I, II, III)'),
    ('a', 'Letters lowercase (a, b, c)'),
    ('A', 'Letters uppercase (A, B, C)'),
    ('', 'Prefix only'),
)


def get_page_labels(doc):
    """Return label rules as dicts: startpage (0-based), prefix, style, firstpagenum."""
    rules = []
    for rule in doc.get_page_labels() or ():
        rules.append({'startpage': int(rule.get('startpage', 0)), 'prefix': rule.get('prefix', ''),
                      'style': rule.get('style', ''), 'firstpagenum': int(rule.get('firstpagenum', 1))})
    return sorted(rules, key=lambda r: r['startpage'])


def set_page_labels(doc, rules):
    """Replace all page-label rules. An empty list removes labels."""
    seen = set()
    clean = []
    for rule in rules:
        start = int(rule['startpage'])
        if not 0 <= start < doc.page_count:
            raise OperationError(f'Label rule starts on page {start + 1}, outside the document.')
        if start in seen:
            raise OperationError(f'Two label rules start on page {start + 1}.')
        if rule.get('style', '') not in dict(LABEL_STYLES):
            raise OperationError('Unknown label style.')
        seen.add(start)
        clean.append({'startpage': start, 'prefix': rule.get('prefix', ''),
                      'style': rule.get('style', ''), 'firstpagenum': max(1, int(rule.get('firstpagenum', 1)))})
    doc.set_page_labels(sorted(clean, key=lambda r: r['startpage']))


def page_label(doc, number):
    try:
        return doc[number].get_label() or ''
    except Exception:
        return ''


# ---------------------------------------------------------------- page boxes

BOXES = ('mediabox', 'cropbox', 'bleedbox', 'trimbox', 'artbox')


def get_boxes(page):
    return {name: tuple(round(v, 2) for v in getattr(page, name)) for name in BOXES}


def set_boxes(doc, pages, boxes):
    """Set page boxes (unrotated PDF coordinates, y-up as stored in the PDF).

    Validates the PDF containment rules: every box must lie within MediaBox,
    and Bleed/Trim/Art boxes should lie within CropBox.
    """
    for number in pages:
        page = doc[number]
        media = fitz.Rect(boxes.get('mediabox') or page.mediabox)
        if media.is_empty:
            raise OperationError('MediaBox must have a positive size.')
        crop = fitz.Rect(boxes.get('cropbox') or page.cropbox)
        for name in BOXES[1:]:
            rect = boxes.get(name)
            if rect is None:
                continue
            rect = fitz.Rect(rect)
            if rect.is_empty:
                raise OperationError(f'{name} must have a positive size.')
            if not media.contains(rect):
                raise OperationError(f'{name} must lie within the MediaBox.')
            if name != 'cropbox' and not crop.contains(rect):
                raise OperationError(f'{name} must lie within the CropBox.')
        if 'mediabox' in boxes and boxes['mediabox'] is not None:
            page.set_mediabox(media)
        for name in BOXES[1:]:
            if boxes.get(name) is not None:
                getattr(page, 'set_' + name)(fitz.Rect(boxes[name]))
