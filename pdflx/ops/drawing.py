"""Vector primitives (stars, polygons, sectors, arrows, arcs) and path node utilities."""
import math

import pymupdf as fitz

from .common import OperationError

PRIMITIVES = ('star', 'polygon', 'triangle', 'arrow', 'sector', 'arc', 'rounded_rectangle')


def _ellipse_point(rect, angle):
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    return fitz.Point(cx + rect.width / 2 * math.cos(angle), cy - rect.height / 2 * math.sin(angle))


def primitive_points(kind, rect, points=5, inner=0.45, start=0.0, sweep=90.0, segments=64):
    """Return (closed, [points]) outlining a primitive fitted to ``rect`` (unrotated coordinates).

    Angles are in degrees, counter-clockwise from 3 o'clock as displayed.
    """
    rect = fitz.Rect(rect).normalize()
    if rect.is_empty:
        raise OperationError('Select an area for the shape.')
    if kind == 'star':
        count = max(3, int(points))
        outline = [_ellipse_point(rect if i % 2 == 0 else _scaled(rect, inner), math.pi / 2 + i * math.pi / count)
                   for i in range(2 * count)]
        return True, outline
    if kind in ('polygon', 'triangle'):
        count = 3 if kind == 'triangle' else max(3, int(points))
        return True, [_ellipse_point(rect, math.pi / 2 + i * 2 * math.pi / count) for i in range(count)]
    if kind == 'arrow':
        shaft = rect.height * 0.35
        head = min(rect.width * 0.4, rect.height)
        cy = (rect.y0 + rect.y1) / 2
        return True, [fitz.Point(rect.x0, cy - shaft / 2), fitz.Point(rect.x1 - head, cy - shaft / 2),
                      fitz.Point(rect.x1 - head, rect.y0), fitz.Point(rect.x1, cy),
                      fitz.Point(rect.x1 - head, rect.y1), fitz.Point(rect.x1 - head, cy + shaft / 2),
                      fitz.Point(rect.x0, cy + shaft / 2)]
    if kind in ('sector', 'arc'):
        if not 0 < abs(sweep) <= 360:
            raise OperationError('The sweep angle must be between 0 and 360 degrees.')
        steps = max(4, int(segments * abs(sweep) / 360))
        arc = [_ellipse_point(rect, math.radians(start + sweep * i / steps)) for i in range(steps + 1)]
        if kind == 'arc':
            return False, arc
        centre = fitz.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
        return True, [centre] + arc
    if kind == 'rounded_rectangle':
        radius = min(rect.width, rect.height) * max(0.0, min(0.5, inner))
        corners = ((rect.x1 - radius, rect.y0 + radius, 90), (rect.x0 + radius, rect.y0 + radius, 180),
                   (rect.x0 + radius, rect.y1 - radius, 270), (rect.x1 - radius, rect.y1 - radius, 0))
        outline = []
        for cx, cy, base in corners:
            for i in range(9):
                angle = math.radians(base - 90 + i * 90 / 8)
                outline.append(fitz.Point(cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
        return True, outline
    raise OperationError(f'Unknown shape: {kind}.')


def _scaled(rect, factor):
    cx, cy = (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2
    return fitz.Rect(cx - rect.width * factor / 2, cy - rect.height * factor / 2,
                     cx + rect.width * factor / 2, cy + rect.height * factor / 2)


def draw_primitive(doc, page_number, kind, rect, stroke=(0, 0, 0), fill=None, width=1.5, opacity=1.0, **params):
    """Draw a primitive as native page content (vector path)."""
    closed, outline = primitive_points(kind, rect, **params)
    page = doc[page_number]
    shape = page.new_shape()
    shape.draw_polyline(outline)
    shape.finish(color=stroke, fill=fill if closed else None, width=width, closePath=closed,
                 lineJoin=1, lineCap=1, stroke_opacity=opacity, fill_opacity=opacity)
    shape.commit()
    return outline


# ---------------------------------------------------------------- node utilities

def nearest_node(points, point, tolerance):
    best, best_distance = None, tolerance
    for index, (x, y) in enumerate(points):
        distance = math.hypot(x - point[0], y - point[1])
        if distance <= best_distance:
            best, best_distance = index, distance
    return best


def nearest_segment(points, point):
    """Index after which a new node at ``point`` should be inserted, and its projection."""
    best, best_distance, projection = None, float('inf'), None
    for index in range(len(points) - 1):
        (x0, y0), (x1, y1) = points[index], points[index + 1]
        dx, dy = x1 - x0, y1 - y0
        length = dx * dx + dy * dy
        t = 0 if length == 0 else max(0, min(1, ((point[0] - x0) * dx + (point[1] - y0) * dy) / length))
        px, py = x0 + t * dx, y0 + t * dy
        distance = math.hypot(point[0] - px, point[1] - py)
        if distance < best_distance:
            best, best_distance, projection = index, distance, (px, py)
    return best, best_distance, projection


def simplify(points, tolerance=1.0):
    """Ramer–Douglas–Peucker simplification; keeps end points."""
    if len(points) < 3:
        return list(points)

    def distance(point, start, end):
        if start == end:
            return math.hypot(point[0] - start[0], point[1] - start[1])
        (x0, y0), (x1, y1) = start, end
        return abs((y1 - y0) * point[0] - (x1 - x0) * point[1] + x1 * y0 - y1 * x0) / math.hypot(x1 - x0, y1 - y0)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        index, worst = None, tolerance
        for i in range(first + 1, last):
            d = distance(points[i], points[first], points[last])
            if d > worst:
                index, worst = i, d
        if index is not None:
            keep[index] = True
            stack.extend(((first, index), (index, last)))
    return [point for point, kept in zip(points, keep) if kept]


def smooth(points, iterations=1):
    """Chaikin corner cutting; end points are preserved."""
    result = list(points)
    for _ in range(max(0, int(iterations))):
        if len(result) < 3:
            break
        smoothed = [result[0]]
        for (x0, y0), (x1, y1) in zip(result, result[1:]):
            smoothed.append((0.75 * x0 + 0.25 * x1, 0.75 * y0 + 0.25 * y1))
            smoothed.append((0.25 * x0 + 0.75 * x1, 0.25 * y0 + 0.75 * y1))
        smoothed.append(result[-1])
        result = smoothed
    return result
