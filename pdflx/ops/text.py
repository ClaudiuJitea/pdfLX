"""Search, text inspection, statistics, automatic bookmarks, and comparison."""
import difflib
import re

import numpy
import pymupdf as fitz

from .common import OperationError, check_cancel, report


# ---------------------------------------------------------------- search

def _word_items(page):
    """Return (text, rect) for each word, in reading order, with line breaks marked."""
    items = []
    for x0, y0, x1, y1, word, block, line, _n in page.get_text('words', sort=True):
        items.append((word, fitz.Rect(x0, y0, x1, y1), (block, line)))
    return items


def search_page(page, pattern, regex=False, case_sensitive=False, whole_word=False):
    """Return a list of (matched text, [quads]) for ``pattern`` on ``page``.

    Matching runs over the page's words joined by single spaces, so phrases can
    span line breaks; geometry is reassembled from the matched words' boxes,
    which keeps it correct on rotated pages (word rects are unrotated).
    """
    if not pattern:
        return []
    flags = 0 if case_sensitive else re.IGNORECASE
    try:
        expression = re.compile(pattern if regex else re.escape(pattern), flags | re.UNICODE)
    except re.error as error:
        raise OperationError(f'Invalid regular expression: {error}.')
    if whole_word:
        expression = re.compile(r'(?<!\w)(?:' + expression.pattern + r')(?!\w)', expression.flags)
    words = _word_items(page)
    text, spans = '', []
    for word, rect, _key in words:
        if text:
            text += ' '
        spans.append((len(text), len(text) + len(word), word, rect))
        text += word
    results = []
    for match in expression.finditer(text):
        start, end = match.span()
        if start == end:
            continue
        quads = []
        for w_start, w_end, word, rect in spans:
            if w_end <= start or w_start >= end:
                continue
            # Partially matched words get a proportional sub-rectangle.
            a = max(start, w_start) - w_start
            b = min(end, w_end) - w_start
            width = rect.width / max(1, len(word))
            quads.append(fitz.Rect(rect.x0 + a * width, rect.y0, rect.x0 + b * width, rect.y1).quad)
        results.append((match.group(0), quads))
    return results


def search_document(doc, pattern, regex=False, case_sensitive=False, whole_word=False, pages=None,
                    context=40, progress=None, cancel=None, limit=5000):
    """Search pages and return hit dicts: page, text, context, quads."""
    hits = []
    numbers = list(range(doc.page_count) if pages is None else pages)
    for index, number in enumerate(numbers):
        check_cancel(cancel)
        page = doc[number]
        matches = search_page(page, pattern, regex, case_sensitive, whole_word)
        if matches:
            words = ' '.join(item[0] for item in _word_items(page))
            for matched, quads in matches:
                position = words.lower().find(matched.lower())
                snippet = words[max(0, position - context): position + len(matched) + context] if position >= 0 else matched
                hits.append({'page': number, 'text': matched, 'context': snippet.strip(), 'quads': quads})
                if len(hits) >= limit:
                    return hits
        report(progress, index + 1, len(numbers))
    return hits


# ---------------------------------------------------------------- inspection

def text_style_at(page, point):
    """Return font details of the span under ``point`` (unrotated coordinates)."""
    point = fitz.Point(point)
    for span in page.get_texttrace():
        if fitz.Rect(span['bbox']).contains(point):
            text = ''.join(chr(c[0]) for c in span['chars'] if c[0] > 0)
            colour = span.get('color') or (0,)
            if len(colour) == 1:
                colour = colour * 3
            return {'font': span['font'], 'size': round(span['size'], 2), 'color': tuple(colour[:3]),
                    'opacity': span.get('opacity', 1), 'render_mode': span.get('type', 0),
                    'bold': bool(span['flags'] & 16) or 'bold' in span['font'].lower(),
                    'italic': bool(span['flags'] & 2) or 'italic' in span['font'].lower(),
                    'text': text, 'bbox': tuple(span['bbox'])}
    return None


def spans_with_style(doc, font, size, tolerance=0.25, pages=None):
    """Find spans that share a font and size; returns (page, rect, text)."""
    results = []
    for number in (range(doc.page_count) if pages is None else pages):
        for block in doc[number].get_text('dict')['blocks']:
            for line in block.get('lines', ()):
                for span in line['spans']:
                    if span['font'] == font and abs(span['size'] - size) <= tolerance and span['text'].strip():
                        results.append((number, tuple(span['bbox']), span['text']))
    return results


def statistics(doc, progress=None, cancel=None):
    """Count words, characters, images, links, annotations, and fields."""
    stats = {'pages': doc.page_count, 'words': 0, 'characters': 0, 'images': 0, 'links': 0,
             'annotations': 0, 'form_fields': 0, 'fonts': set(), 'page_sizes': {}}
    for number in range(doc.page_count):
        check_cancel(cancel)
        page = doc[number]
        text = page.get_text('text')
        stats['words'] += len(text.split())
        stats['characters'] += len(re.sub(r'\s', '', text))
        stats['images'] += len(page.get_images(full=False))
        stats['links'] += len(page.get_links())
        stats['annotations'] += sum(1 for _ in page.annots() or ())
        stats['form_fields'] += sum(1 for _ in page.widgets() or ())
        for font in page.get_fonts():
            stats['fonts'].add(font[3] or font[4])
        size = (round(page.rect.width), round(page.rect.height))
        stats['page_sizes'][size] = stats['page_sizes'].get(size, 0) + 1
        report(progress, number + 1, doc.page_count)
    stats['fonts'] = sorted(stats['fonts'])
    return stats


# ---------------------------------------------------------------- automatic bookmarks

def heading_candidates(doc, min_ratio=1.15, max_levels=3, max_length=120, progress=None, cancel=None):
    """Propose a TOC from text larger than body text: [level, title, page] lists.

    Body size is the most common span size by character count. Distinct larger
    sizes become levels 1..max_levels, largest first.
    """
    usage, lines = {}, []
    for number in range(doc.page_count):
        check_cancel(cancel)
        for block in doc[number].get_text('dict', sort=True)['blocks']:
            for line in block.get('lines', ()):
                spans = [s for s in line['spans'] if s['text'].strip()]
                if not spans:
                    continue
                text = ' '.join(s['text'].strip() for s in spans).strip()
                size = round(max(s['size'] for s in spans), 1)
                bold = all(s['flags'] & 16 for s in spans)
                for span in spans:
                    usage[round(span['size'], 1)] = usage.get(round(span['size'], 1), 0) + len(span['text'])
                lines.append((number, size, bold, text, line['bbox'][1]))
        report(progress, number + 1, doc.page_count)
    if not usage:
        raise OperationError('The document has no extractable text. Run OCR first.')
    body = max(usage, key=usage.get)
    heading_sizes = sorted({size for _n, size, _b, text, _y in lines
                            if size >= body * min_ratio and len(text) <= max_length}, reverse=True)[:max_levels]
    level_of = {size: index + 1 for index, size in enumerate(heading_sizes)}
    toc, previous = [], None
    for number, size, _bold, text, _y in lines:
        level = level_of.get(size)
        if level is None or len(text) > max_length or not re.search(r'\w', text):
            previous = None
            continue
        # Merge consecutive lines of the same heading (wrapped titles).
        if previous is not None and previous[0] == number and previous[1] == size and toc:
            toc[-1][1] = f'{toc[-1][1]} {text}'
            continue
        # Levels may not skip downward; clamp to previous level + 1.
        level = min(level, (toc[-1][0] + 1) if toc else 1)
        toc.append([level, text, number + 1])
        previous = (number, size)
    return toc


# ---------------------------------------------------------------- comparison

def _page_words(page):
    return [(w[4], fitz.Rect(w[:4])) for w in page.get_text('words', sort=True)]


def compare_text(doc_a, doc_b, progress=None, cancel=None):
    """Word-level comparison. Returns a summary and per-page changed rectangles.

    Result: {'changes': [...], 'rects_a': {page: [rect]}, 'rects_b': {page: [rect]},
    'equal': bool}. Each change has kind (insert/delete/replace), old, new,
    page_a, page_b.
    """
    words_a, words_b = [], []
    for number in range(doc_a.page_count):
        check_cancel(cancel)
        words_a.extend((text, number, rect) for text, rect in _page_words(doc_a[number]))
    for number in range(doc_b.page_count):
        check_cancel(cancel)
        words_b.extend((text, number, rect) for text, rect in _page_words(doc_b[number]))
    matcher = difflib.SequenceMatcher(a=[w[0] for w in words_a], b=[w[0] for w in words_b], autojunk=False)
    changes, rects_a, rects_b = [], {}, {}
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        old = words_a[a0:a1]
        new = words_b[b0:b1]
        for _text, page, rect in old:
            rects_a.setdefault(page, []).append(rect)
        for _text, page, rect in new:
            rects_b.setdefault(page, []).append(rect)
        changes.append({'kind': tag, 'old': ' '.join(w[0] for w in old), 'new': ' '.join(w[0] for w in new),
                        'page_a': old[0][1] if old else (words_a[a0 - 1][1] if a0 else 0),
                        'page_b': new[0][1] if new else (words_b[b0 - 1][1] if b0 else 0)})
    report(progress, 1, 1)
    return {'changes': changes, 'rects_a': rects_a, 'rects_b': rects_b, 'equal': not changes,
            'ratio': matcher.ratio()}


def compare_visual(doc_a, doc_b, dpi=60, threshold=24, progress=None, cancel=None):
    """Pixel comparison of page pairs. Returns {page: changed_fraction} for differing pages."""
    differences = {}
    count = max(doc_a.page_count, doc_b.page_count)
    for number in range(count):
        check_cancel(cancel)
        if number >= doc_a.page_count or number >= doc_b.page_count:
            differences[number] = 1.0
            continue
        pix_a = doc_a[number].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        pix_b = doc_b[number].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        if (pix_a.width, pix_a.height) != (pix_b.width, pix_b.height):
            differences[number] = 1.0
            continue
        a = numpy.frombuffer(pix_a.samples, dtype=numpy.uint8).astype(numpy.int16)
        b = numpy.frombuffer(pix_b.samples, dtype=numpy.uint8).astype(numpy.int16)
        changed = int(numpy.count_nonzero(numpy.abs(a - b) > threshold))
        if changed:
            differences[number] = changed / a.size
        report(progress, number + 1, count)
    return differences


def annotate_differences(doc, rects_by_page, color=(1, 0.3, 0.3), author='pdfLX compare'):
    """Add highlight annotations marking changed words on a (copy of a) document."""
    for page_number, rects in rects_by_page.items():
        page = doc[page_number]
        annot = page.add_highlight_annot([fitz.Rect(r).quad for r in rects])
        annot.set_colors(stroke=color)
        annot.set_info(title=author, content='Changed text')
        annot.update()
