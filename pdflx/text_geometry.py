"""Character positions from PDF glyph geometry, including rotated text."""
import pymupdf as fitz


def character_quads(doc, obj):
    def find(page):
        candidates = []
        for block in page.get_text('rawdict', flags=0)['blocks']:
            for line in block.get('lines', []):
                chars = [(char['c'], fitz.recover_char_quad(line['dir'], span, char))
                         for span in line.get('spans', []) for char in span.get('chars', [])]
                value = ''.join(char for char, _ in chars)
                offset = value.find(obj.text)
                if offset >= 0:
                    quads = [quad for _, quad in chars[offset:offset+len(obj.text)]]
                    bounds = fitz.Rect()
                    for quad in quads:
                        bounds |= quad.rect
                    center = (bounds.tl+bounds.br)/2
                    target = (fitz.Rect(obj.bbox).tl+fitz.Rect(obj.bbox).br)/2
                    candidates.append((abs(center-target), quads))
        if candidates:
            return min(candidates, key=lambda item: item[0])[1]
        return None
    result = find(doc[obj.page_number])
    if result is not None and '\n' not in obj.text:
        return result
    # Multiline/newly changed text: use the same PDF renderer as committed edits.
    from . import pdf_handler
    with fitz.open() as scratch:
        source = doc[obj.page_number]
        page = scratch.new_page(width=source.mediabox.width, height=source.mediabox.height)
        success, error = pdf_handler._apply_single_object_to_page(scratch, page, obj)
        if not success:
            raise ValueError(error)
        quads = []
        for block in page.get_text('rawdict', flags=0)['blocks']:
            for line in block.get('lines', []):
                if quads:
                    quads.append(None)
                quads.extend(fitz.recover_char_quad(line['dir'], span, char)
                             for span in line.get('spans', []) for char in span.get('chars', []))
        if len(quads) != len(obj.text):
            raise ValueError('Could not map this text selection to PDF characters.')
        return quads


def selection_quads(doc, obj, start=0, end=None):
    end = len(obj.text) if end is None else end
    return [quad for quad in character_quads(doc, obj)[max(0,start):min(end,len(obj.text))] if quad is not None]


def selection_bounds(doc, obj, start=0, end=None):
    bounds = fitz.Rect()
    for quad in selection_quads(doc, obj, start, end):
        bounds |= quad.rect
    return tuple(bounds)


def character_at_point(doc, obj, x, y):
    point = fitz.Point(x,y)
    items = [(index, quad) for index, quad in enumerate(character_quads(doc,obj)) if quad is not None]
    for index, quad in items:
        if point in quad.rect:
            return index
    return min(items, key=lambda item: abs(point-(item[1].rect.tl+item[1].rect.br)/2))[0]
