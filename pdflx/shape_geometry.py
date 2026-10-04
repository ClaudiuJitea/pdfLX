"""Outlines of the editor's vector shapes, shared by the PDF renderer and the canvas preview.

A shape's outline is a list of subpaths. Each subpath is (closed, segments) where a
segment is ('M', p), ('L', p) or ('C', c1, c2, p) with points as (x, y) tuples.
"""
import math

KAPPA = 0.5522847498307936
CLOSED_KINDS = ('rectangle', 'ellipse', 'polygon', 'right_triangle', 'star', 'arrow', 'callout')
OPEN_KINDS = ('checkmark', 'cross')
DASHES = ('solid', 'dashed', 'dotted')

# Tool presets: (kind, label key, icon, extra properties for the new shape).
PRESETS = (
    ('rectangle', 'tool_rectangle_tip', 'editor-rectangle-symbolic', {}),
    ('rounded', 'shape_rounded', 'editor-rounded-rect-symbolic', {'shape_type': 'rectangle', 'corner_radius': 10.0}),
    ('ellipse', 'tool_ellipse_tip', 'editor-ellipse-symbolic', {}),
    ('triangle', 'shape_triangle', 'editor-triangle-symbolic', {'shape_type': 'polygon', 'sides': 3}),
    ('right_triangle', 'shape_right_triangle', 'editor-right-triangle-symbolic', {}),
    ('diamond', 'shape_diamond', 'editor-diamond-symbolic', {'shape_type': 'polygon', 'sides': 4}),
    ('pentagon', 'shape_pentagon', 'editor-pentagon-symbolic', {'shape_type': 'polygon', 'sides': 5}),
    ('hexagon', 'shape_hexagon', 'editor-hexagon-symbolic', {'shape_type': 'polygon', 'sides': 6}),
    ('star', 'shape_star', 'editor-star-symbolic', {'star_points': 5, 'star_inner': 0.5}),
    ('arrow', 'shape_arrow', 'editor-block-arrow-symbolic', {}),
    ('callout', 'shape_callout', 'editor-callout-symbolic', {'corner_radius': 8.0}),
    ('checkmark', 'tool_checkmark_tip', 'editor-check-symbolic', {}),
    ('cross', 'tool_cross_tip', 'editor-cross-symbolic', {}),
)


def _bbox(bbox):
    x0, y0, x1, y1 = bbox
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _polyline(points, closed=True):
    segments = [('M', points[0])] + [('L', p) for p in points[1:]]
    return [(closed, segments)]


def _ellipse(x0, y0, x1, y1):
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    k = KAPPA
    return [(True, [
        ('M', (cx + rx, cy)),
        ('C', (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry)),
        ('C', (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy)),
        ('C', (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry)),
        ('C', (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy)),
    ])]


def _rounded(x0, y0, x1, y1, radius, tail=None):
    """Rounded rectangle; tail=(base_left, base_right, tip) adds a callout pointer on the bottom edge."""
    r = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    k = KAPPA * r
    s = [('M', (x0 + r, y0)), ('L', (x1 - r, y0))]
    if r:
        s.append(('C', (x1 - r + k, y0), (x1, y0 + r - k), (x1, y0 + r)))
    s.append(('L', (x1, y1 - r)))
    if r:
        s.append(('C', (x1, y1 - r + k), (x1 - r + k, y1), (x1 - r, y1)))
    if tail:
        left, right, tip = tail
        s += [('L', (right, y1)), ('L', tip), ('L', (left, y1))]
    s.append(('L', (x0 + r, y1)))
    if r:
        s.append(('C', (x0 + r - k, y1), (x0, y1 - r + k), (x0, y1 - r)))
    s.append(('L', (x0, y0 + r)))
    if r:
        s.append(('C', (x0, y0 + r - k), (x0 + r - k, y0), (x0 + r, y0)))
    return [(True, s)]


def _fit(points, x0, y0, x1, y1):
    """Scale points so their extent fills the box (regular shapes stretch with it)."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    sx = (x1 - x0) / max(maxx - minx, 1e-9)
    sy = (y1 - y0) / max(maxy - miny, 1e-9)
    return [(x0 + (x - minx) * sx, y0 + (y - miny) * sy) for x, y in points]


def regular_points(sides):
    sides = max(3, int(sides))
    return [(math.sin(2 * math.pi * i / sides), -math.cos(2 * math.pi * i / sides)) for i in range(sides)]


def star_points(points, inner):
    points = max(3, int(points))
    inner = max(0.1, min(0.95, float(inner)))
    result = []
    for i in range(points * 2):
        radius = 1.0 if i % 2 == 0 else inner
        angle = math.pi * i / points
        result.append((radius * math.sin(angle), -radius * math.cos(angle)))
    return result


def outline(shape, bbox=None):
    """Subpaths for a shape (an EditableShape or anything with the same attributes)."""
    x0, y0, x1, y1 = _bbox(bbox or shape.bbox)
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    kind = shape.shape_type
    radius = float(getattr(shape, 'corner_radius', 0) or 0)
    if kind == 'rectangle':
        return _rounded(x0, y0, x1, y1, radius) if radius > 0 else _polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    if kind == 'ellipse':
        return _ellipse(x0, y0, x1, y1)
    if kind == 'polygon':
        return _polyline(_fit(regular_points(getattr(shape, 'sides', 6) or 6), x0, y0, x1, y1))
    if kind == 'right_triangle':
        return _polyline([(x0, y0), (x1, y1), (x0, y1)])
    if kind == 'star':
        return _polyline(_fit(star_points(getattr(shape, 'star_points', 5) or 5,
                                          getattr(shape, 'star_inner', 0.5) or 0.5), x0, y0, x1, y1))
    if kind == 'arrow':
        head = min(w * 0.45, h * 0.9)
        shaft_top, shaft_bottom = y0 + h * 0.28, y1 - h * 0.28
        neck = x1 - head
        return _polyline([(x0, shaft_top), (neck, shaft_top), (neck, y0), (x1, (y0 + y1) / 2),
                          (neck, y1), (neck, shaft_bottom), (x0, shaft_bottom)])
    if kind == 'callout':
        body = y0 + h * 0.78
        return _rounded(x0, y0, x1, body, radius,
                        tail=(x0 + w * 0.18, x0 + w * 0.34, (x0 + w * 0.12, y1)))
    if kind == 'checkmark':
        points = [(x0 + 0.15 * w, y0 + 0.50 * h), (x0 + 0.38 * w, y0 + 0.85 * h), (x0 + 0.85 * w, y0 + 0.18 * h)]
        return _polyline(points, closed=False)
    if kind == 'cross':
        a = _polyline([(x0 + 0.18 * w, y0 + 0.18 * h), (x1 - 0.18 * w, y1 - 0.18 * h)], closed=False)
        b = _polyline([(x1 - 0.18 * w, y0 + 0.18 * h), (x0 + 0.18 * w, y1 - 0.18 * h)], closed=False)
        return a + b
    return _polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def transform(paths, function):
    """Apply a point function (e.g. rotation) to every point."""
    result = []
    for closed, segments in paths:
        moved = []
        for segment in segments:
            moved.append((segment[0], *[function(p) for p in segment[1:]]))
        result.append((closed, moved))
    return result


def dash_pattern(style, width):
    """(on, off) lengths in points for a dash style, or None for solid."""
    width = max(float(width), 0.5)
    if style == 'dashed':
        return (width * 3, width * 2)
    if style == 'dotted':
        return (0.01, width * 2)
    return None


def cairo_path(cr, paths):
    for closed, segments in paths:
        for segment in segments:
            if segment[0] == 'M':
                cr.move_to(*segment[1])
            elif segment[0] == 'L':
                cr.line_to(*segment[1])
            else:
                cr.curve_to(*segment[1], *segment[2], *segment[3])
        if closed:
            cr.close_path()


def draw_cairo(cr, shape, bbox=None, alpha=1.0, rotate=True):
    """Preview a shape on the canvas with its fill, outline, dash and opacity."""
    import cairo
    paths = outline(shape, bbox)
    x0, y0, x1, y1 = _bbox(bbox or shape.bbox)
    opacity = float(getattr(shape, 'opacity', 1.0) or 1.0) * alpha
    cr.save()
    rotation = getattr(shape, 'rotation', 0.0) % 360.0 if rotate else 0.0
    if rotation:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        cr.translate(cx, cy)
        cr.rotate(math.radians(rotation))
        cr.translate(-cx, -cy)
    closed = any(c for c, _segments in paths)
    if closed and not shape.is_transparent:
        cr.set_source_rgba(*shape.fill_color[:3], opacity)
        cairo_path(cr, [(c, s) for c, s in paths if c])
        cr.fill()
    if shape.stroke_width > 0:
        cr.set_source_rgba(*shape.stroke_color[:3], opacity)
        cr.set_line_width(shape.stroke_width)
        style = getattr(shape, 'dash', 'solid')
        pattern = dash_pattern(style, shape.stroke_width)
        if pattern:
            cr.set_dash(list(pattern), 0)
        round_ends = style == 'dotted' or shape.shape_type in OPEN_KINDS
        cr.set_line_cap(cairo.LINE_CAP_ROUND if round_ends else cairo.LINE_CAP_BUTT)
        cr.set_line_join(cairo.LINE_JOIN_ROUND if shape.shape_type in OPEN_KINDS else cairo.LINE_JOIN_MITER)
        cairo_path(cr, paths)
        cr.stroke()
    cr.restore()


def arrowhead(tip, tail, width):
    """Triangle points for an arrowhead at tip, pointing away from tail."""
    dx, dy = tip[0] - tail[0], tip[1] - tail[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    size = max(6.0, width * 4.0)
    base = (tip[0] - ux * size, tip[1] - uy * size)
    half = size * 0.45
    return [tip, (base[0] - uy * half, base[1] + ux * half), (base[0] + uy * half, base[1] - ux * half)]


def stroke_ends(points, width, arrow_start=False, arrow_end=False):
    """Shortened line points plus arrowhead triangles, so line caps do not poke through the tips."""
    points = list(points)
    heads = []
    if len(points) < 2:
        return points, heads
    size = max(6.0, width * 4.0)

    def pull(tip, toward):
        dx, dy = toward[0] - tip[0], toward[1] - tip[1]
        length = math.hypot(dx, dy) or 1.0
        step = min(size * 0.8, length * 0.5)
        return (tip[0] + dx / length * step, tip[1] + dy / length * step)
    if arrow_end:
        heads.append(arrowhead(points[-1], points[-2], width))
        points[-1] = pull(points[-1], points[-2])
    if arrow_start:
        heads.append(arrowhead(points[0], points[1], width))
        points[0] = pull(points[0], points[1])
    return points, heads
