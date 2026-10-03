"""Rotate stamp appearance vectors without regenerating their text or style."""
import math
import pymupdf as fitz


def _number(value):
    # PDF numbers do not allow exponent notation.
    return f'{value:.9f}'


def tilt_info(doc, xref):
    kind, value = doc.xref_get_key(xref, 'PdfLXStampTilt/Angle')
    return float(value) if kind in ('int', 'float') else 0.0


def stamp_dimensions(doc, page_number, xref):
    page = doc[page_number]
    visual = page.load_annot(xref).rect * page.rotation_matrix
    kind, value = doc.xref_get_key(xref, 'PdfLXStampTilt/Width')
    if kind not in ('int', 'float'):
        return visual.width, visual.height
    width = float(value)
    height = float(doc.xref_get_key(xref, 'PdfLXStampTilt/Height')[1])
    theta = math.radians(tilt_info(doc,xref))
    scale = visual.width / (abs(width*math.cos(theta))+abs(height*math.sin(theta)))
    return width*scale, height*scale


def rotate_stamp(doc, page_number, xref, angle, *, size=None, center=None):
    if not math.isfinite(angle):
        raise ValueError('Choose a finite stamp angle.')
    page = doc[page_number]
    annot = page.load_annot(xref)
    if annot.type[0] != fitz.PDF_ANNOT_STAMP:
        raise ValueError('Select a stamp to rotate.')
    visual = annot.rect * page.rotation_matrix
    kind, value = doc.xref_get_key(xref, 'PdfLXStampTilt/Base')
    if kind == 'xref':
        base = int(value.split()[0])
        width, height = stamp_dimensions(doc,page_number,xref)
    else:
        appearance = int(doc.xref_get_key(xref, 'AP/N')[1].split()[0])
        base = doc.get_new_xref()
        doc.update_object(base, doc.xref_object(appearance))
        doc.update_stream(base, doc.xref_stream(appearance))
        doc.xref_set_key(base, 'Matrix', '[1 0 0 1 0 0]')
        width, height = visual.width, visual.height
    if size is not None:
        width,height=size
    angle %= 360
    theta = math.radians(angle)
    cosine, sine = math.cos(theta), math.sin(theta)
    bw = abs(width * cosine) + abs(height * sine)
    bh = abs(width * sine) + abs(height * cosine)
    scale = min(1, page.rect.width / bw, page.rect.height / bh)
    width *= scale
    height *= scale
    bw *= scale
    bh *= scale
    center = center if center is not None else (visual.tl + visual.br) / 2
    cx = max(bw / 2, min(center.x, page.rect.width - bw / 2))
    cy = max(bh / 2, min(center.y, page.rect.height - bh / 2))
    target = fitz.Rect(cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
    bbox = doc.xref_get_key(base, 'BBox')[1]
    bounds = fitz.Rect([float(part) for part in bbox.strip('[] ').split()])
    sx, sy = width / bounds.width, height / bounds.height
    a, b, c, d = sx * cosine, -sx * sine, sy * sine, sy * cosine
    bx, by = (bounds.x0 + bounds.x1) / 2, (bounds.y0 + bounds.y1) / 2
    e, f = bw / 2 - a * bx - c * by, bh / 2 - b * bx - d * by
    # The page's rotation is cancelled by the appearance's matrix, as for
    # upright stamps. The content matrix supplies the independent user tilt.
    rotation = fitz.Matrix(page.rotation)
    matrix = f'[{rotation.a} {rotation.b} {rotation.c} {rotation.d} 0 0]'
    appearance = doc.get_new_xref()
    doc.update_object(appearance, f'<< /Type /XObject /Subtype /Form /BBox [0 0 {_number(bw)} {_number(bh)}] '
                                 f'/Matrix {matrix} /Resources << /XObject << /Stamp {base} 0 R >> >> >>')
    operands = ' '.join(_number(value) for value in (a,b,c,d,e,f))
    doc.update_stream(appearance, f'q {operands} cm /Stamp Do Q'.encode())
    annot.set_rect(target * page.derotation_matrix)
    doc.xref_set_key(xref, 'AP', f'<< /N {appearance} 0 R >>')
    doc.xref_set_key(xref, 'PdfLXStampTilt', f'<< /Base {base} 0 R /Width {_number(width)} /Height {_number(height)} /Angle {_number(angle)} >>')
    doc._reset_page_refs()
