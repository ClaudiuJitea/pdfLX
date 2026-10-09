"""Erase original vector drawings from a page without disturbing their neighbours.

Redaction (line art "remove if touched") deletes every subpath whose bounding box
touches the redaction area, so a large rectangle would also take adjacent cells,
table borders and background bars with it. We instead redact one tiny probe per
subpath of the target, placed where it touches as little else as possible, then
compare the page's subpaths before and after and give back anything else that
went missing: whole editable shapes are handed to the editor (which redraws them
in their original order), stray pieces are redrawn exactly as they were.
"""
from collections import Counter

import pymupdf as fitz

PROBE = 0.25
STEPS = 8


def _round(values, digits):
    return None if values is None else tuple(round(float(v), digits) for v in values)


def _style(drawing):
    return (drawing.get('type'), _round(drawing.get('fill'), 3), _round(drawing.get('color'), 3),
            round(float(drawing.get('width') or 0), 2))


def drawing_key(drawing):
    """Identity of a drawing that survives a redraw of the same path."""
    rect = drawing['rect']
    return _style(drawing)[:1] + (_round((rect.x0, rect.y0, rect.x1, rect.y1), 1),) + _style(drawing)[1:] + (
        len(drawing.get('items') or ()),)


def _points(item):
    if item[0] == 're':
        return [tuple(item[1].tl), tuple(item[1].br)]
    if item[0] == 'qu':
        return [tuple(p) for p in item[1]]
    return [tuple(p) for p in item[1:] if not isinstance(p, (int, float))]


def subpaths(drawing):
    """Item index groups that redaction treats as separate subpaths."""
    groups, last = [], None
    for index, item in enumerate(drawing.get('items') or ()):
        if item[0] in ('re', 'qu'):
            groups.append([index])
            last = None
            continue
        start, end = tuple(item[1]), tuple(item[-1])
        if last is None or max(abs(start[0] - last[0]), abs(start[1] - last[1])) > 1e-3:
            groups.append([])
        groups[-1].append(index)
        last = end
    return groups


def _bounds(drawing, group):
    points = [p for index in group for p in _points(drawing['items'][index])]
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return fitz.Rect(min(xs), min(ys), max(xs), max(ys))


def _reach(drawing, rect):
    half = float(drawing.get('width') or 0) / 2 if drawing.get('color') is not None else 0
    return fitz.Rect(rect) + (-half - 0.5, -half - 0.5, half + 0.5, half + 0.5)


def reach(drawing):
    """Area a drawing covers, including half of its line width."""
    return _reach(drawing, drawing['rect'])


def _signature(drawing, group):
    def item_sig(item):
        return (item[0],) + tuple(round(v, 2) if isinstance(v, float) else v
                                  for p in item[1:] for v in (tuple(p) if not isinstance(p, (int, float)) else (p,)))
    return _style(drawing) + tuple(item_sig(drawing['items'][index]) for index in group)


def replay(page, drawing, overlay=True, only=None):
    """Redraw a drawing reported by get_drawings(), optionally only the given item indices."""
    shape = page.new_shape()
    drew = False
    for index, item in enumerate(drawing.get('items') or ()):
        if only is not None and index not in only:
            continue
        op = item[0]
        if op == 'l':
            shape.draw_line(item[1], item[2])
        elif op == 'c':
            shape.draw_bezier(item[1], item[2], item[3], item[4])
        elif op == 're':
            shape.draw_rect(item[1])
        elif op == 'qu':
            shape.draw_quad(item[1])
        else:
            continue
        drew = True
    if not drew:
        return
    color = drawing.get('color')
    cap = drawing.get('lineCap') or (0,)
    dashes = drawing.get('dashes')
    fill_opacity = drawing.get('fill_opacity')
    stroke_opacity = drawing.get('stroke_opacity')
    shape.finish(fill=drawing.get('fill'), color=color,
                 width=float(drawing.get('width') or 1.0) if color is not None else 0,
                 dashes=dashes if dashes and dashes != '[] 0' else None,
                 even_odd=bool(drawing.get('even_odd')), closePath=bool(drawing.get('closePath')),
                 lineCap=int(cap[0] if isinstance(cap, (tuple, list)) else cap),
                 lineJoin=int(drawing.get('lineJoin') or 0),
                 fill_opacity=1 if fill_opacity is None else fill_opacity,
                 stroke_opacity=1 if stroke_opacity is None else stroke_opacity)
    shape.commit(overlay)


def find(drawings, key=None, rect=None):
    """The drawing a model object was extracted from."""
    if key is not None:
        key = tuple(tuple(k) if isinstance(k, list) else k for k in key)
        for drawing in drawings:
            if drawing_key(drawing) == key:
                return drawing
    if rect is not None:
        rect = fitz.Rect(rect)
        for drawing in drawings:
            r = drawing['rect']
            if max(abs(r.x0 - rect.x0), abs(r.y0 - rect.y0), abs(r.x1 - rect.x1), abs(r.y1 - rect.y1)) <= 1.0:
                return drawing
    return None


def _probe(area, obstacles):
    """A tiny rect inside ``area`` touching as few obstacle rects as possible."""
    best, best_hits = None, None
    for i in range(STEPS + 1):
        for j in range(STEPS + 1):
            # Interior points first: edges are where neighbours meet.
            x = area.x0 + area.width * ((i + STEPS // 2) % (STEPS + 1)) / STEPS
            y = area.y0 + area.height * ((j + STEPS // 2) % (STEPS + 1)) / STEPS
            probe = fitz.Rect(x - PROBE, y - PROBE, x + PROBE, y + PROBE)
            hits = sum(1 for rect in obstacles if rect.intersects(probe))
            if best_hits is None or hits < best_hits:
                best, best_hits = probe, hits
                if hits == 0:
                    return best
    return best


def _link_id(link):
    return (link.get('kind'), _round(tuple(link.get('from', ())), 1), link.get('uri'), link.get('page'), link.get('name'))


def _redact(page, rects):
    # Redaction also drops links in the area; erasing a drawing must not.
    links = page.get_links()
    for rect in rects:
        page.add_redact_annot(rect)
    try:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=2, text=1)
    except TypeError:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    remaining = Counter(_link_id(link) for link in page.get_links())
    for link in links:
        if remaining[_link_id(link)]:
            remaining[_link_id(link)] -= 1
        else:
            page.insert_link(link)


def _missing(before, after):
    """{id(drawing): set of item indices} for subpaths that are gone after redaction."""
    remaining = Counter(_signature(d, group) for d in after for group in subpaths(d))
    missing = {}
    for drawing in before:
        for group in subpaths(drawing):
            sig = _signature(drawing, group)
            if remaining[sig]:
                remaining[sig] -= 1
            else:
                missing.setdefault(id(drawing), set()).update(group)
    return missing


def _covered_later(log, seqno, rect, ignore):
    """True when something drawn after ``seqno`` overlaps it (text, images or paths)."""
    return any(index > seqno and index not in ignore and fitz.Rect(box).intersects(rect)
               for index, (_kind, box) in enumerate(log))


def backdrop_images(log, drawing):
    """Original images drawn underneath a drawing."""
    seqno, rect = drawing.get('seqno', -1), reach(drawing)
    return [fitz.Rect(box) for index, (kind, box) in enumerate(log)
            if index < seqno and kind == 'fill-image' and fitz.Rect(box).intersects(rect)]


def _give_back(page, log, before, missing, intended, adopt=None):
    """Return collateral pieces to the page; ``intended`` maps id(drawing) to items meant to go."""
    ignore = {d.get('seqno') for d in before if id(d) in intended}
    pieces, adopted = [], []
    for drawing in before:
        lost = missing.get(id(drawing), set()) - intended.get(id(drawing), set())
        if not lost:
            continue
        whole = id(drawing) not in intended and len(lost) == len(drawing.get('items') or ())
        if whole and adopt is not None and adopt(drawing):
            adopted.append(drawing)
            continue
        pieces.append((drawing, lost))
    under = [p for p in pieces if _covered_later(log, p[0].get('seqno', -1), reach(p[0]), ignore)]
    over = [p for p in pieces if all(p is not item for item in under)]
    # Each underlay is prepended, so the topmost goes in first.
    for drawing, items in sorted(under, key=lambda p: -p[0].get('seqno', 0)):
        replay(page, drawing, overlay=False, only=items)
    for drawing, items in sorted(over, key=lambda p: p[0].get('seqno', 0)):
        replay(page, drawing, overlay=True, only=items)
    return adopted


def erase(page, key=None, rect=None, fallback=None, only_items=None, adopt=None):
    """Remove one drawing, identified by ``key`` or ``rect``, and restore collateral damage.

    ``only_items`` limits the erase to the target's own item indices when it is one
    subpath of a larger drawing. ``adopt(drawing)`` may claim a wholly removed
    collateral drawing for the editor model instead of redrawing it.
    Returns (removed target drawings, adopted drawings, bbox log before erasing).
    """
    before = page.get_drawings()
    log = page.get_bboxlog()
    target = find(before, key, rect)
    intended = {}
    if target is not None:
        groups = [g for g in subpaths(target) if only_items is None or set(g) <= set(only_items)]
        keep = [_reach(d, _bounds(d, g)) for d in before for g in subpaths(d)
                if d is not target or g not in groups]
        probes = [_probe(_bounds(target, g), keep) for g in groups]
        if probes:
            _redact(page, probes)
            intended[id(target)] = {i for g in groups for i in g}
            # A curve's true bounds can be tighter than its control points: retry on the path itself.
            left = intended[id(target)] - _missing([target], page.get_drawings()).get(id(target), set())
            retry = [g for g in groups if set(g) & left]
            if retry:
                _redact(page, [fitz.Rect(p[0] - PROBE, p[1] - PROBE, p[0] + PROBE, p[1] + PROBE)
                               for g in retry for p in [_points(target['items'][g[0]])[0]]])
    if not intended:
        if fallback is None:
            return [], [], log
        box = fitz.Rect(fallback)
        _redact(page, [box])
        target = None
        # Subpaths fully inside the erased area were the target; touching ones are collateral.
        for drawing in before:
            inside = {i for g in subpaths(drawing) if box.contains(_bounds(drawing, g)) for i in g}
            if inside:
                intended[id(drawing)] = inside
    missing = _missing(before, page.get_drawings())
    adopted = _give_back(page, log, before, missing, intended, adopt)
    removed = [d for d in before if id(d) in intended]
    return removed, adopted, log


def redact_strips(page, rects):
    """Remove underline/strikethrough strips while keeping borders that cross them."""
    before = page.get_drawings()
    log = page.get_bboxlog()
    _redact(page, rects)
    boxes = [fitz.Rect(rect) + (-2, -2, 2, 2) for rect in rects]
    intended = {}
    for drawing in before:
        inside = {i for g in subpaths(drawing) for i in g
                  if any(box.contains(_bounds(drawing, g)) for box in boxes)}
        if inside:
            intended[id(drawing)] = inside
    _give_back(page, log, before, _missing(before, page.get_drawings()), intended)
