"""Vector diagrams and embedded images: cluster export, SVG paths, image inventory and replacement."""
import html
import io
import zipfile

import pymupdf as fitz

from .common import OperationError


# ---------------------------------------------------------------- vector diagrams

def diagram_regions(page, min_size=12):
    """Bounding boxes of clustered vector drawings (unrotated coordinates), largest first."""
    try:
        rects = page.cluster_drawings()
    except Exception:
        rects = []
    rects = [fitz.Rect(r) for r in rects if fitz.Rect(r).width >= min_size and fitz.Rect(r).height >= min_size]
    return sorted(rects, key=lambda r: r.width * r.height, reverse=True)


def region_for_selection(page, selection, padding=2):
    """Union of diagram clusters intersecting ``selection``, else the selection itself."""
    selection = fitz.Rect(selection)
    hits = [r for r in diagram_regions(page, 1) if r.intersects(selection)]
    region = fitz.Rect(selection)
    for rect in hits:
        region |= rect
    return (region + (-padding, -padding, padding, padding)) & page.cropbox


def export_region(doc, page_number, rect, fmt='svg', dpi=200):
    """Export a page region as vector PDF/SVG or raster PNG bytes (text and images included)."""
    page = doc[page_number]
    rect = fitz.Rect(rect) & page.mediabox
    if rect.is_empty:
        raise OperationError('The selected area is empty.')
    if fmt == 'png':
        return page.get_pixmap(dpi=dpi, clip=rect * page.rotation_matrix, alpha=True).tobytes('png')
    with fitz.open() as single:
        target = single.new_page(width=rect.width, height=rect.height)
        target.show_pdf_page(target.rect, doc, page_number, clip=rect, rotate=0)
        if fmt == 'pdf':
            return single.tobytes(garbage=3, deflate=True)
        if fmt == 'svg':
            return single[0].get_svg_image(text_as_path=False).encode('utf-8')
    raise OperationError(f'Unknown format: {fmt}.')


def _colour(value):
    if not value:
        return 'none'
    rgb = value if len(value) == 3 else (value[0],) * 3
    return '#%02x%02x%02x' % tuple(int(max(0, min(1, c)) * 255) for c in rgb)


def drawings_svg(page, clip=None):
    """SVG of the page's vector paths only (no text or images), in visual orientation."""
    clip = fitz.Rect(clip) if clip else None
    matrix = page.rotation_matrix
    width, height = page.rect.width, page.rect.height
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" '
             f'viewBox="0 0 {width:g} {height:g}">']
    count = 0
    for drawing in page.get_drawings():
        if clip is not None and not fitz.Rect(drawing['rect']).intersects(clip):
            continue
        commands = []
        for item in drawing['items']:
            kind = item[0]
            if kind == 'l':
                a, b = item[1] * matrix, item[2] * matrix
                commands.append(f'M{a.x:.2f},{a.y:.2f} L{b.x:.2f},{b.y:.2f}')
            elif kind == 'c':
                points = [p * matrix for p in item[1:5]]
                commands.append(f'M{points[0].x:.2f},{points[0].y:.2f} C' +
                                ' '.join(f'{p.x:.2f},{p.y:.2f}' for p in points[1:]))
            elif kind == 're':
                quad = fitz.Rect(item[1]).quad * matrix
                commands.append('M' + ' L'.join(f'{p.x:.2f},{p.y:.2f}' for p in (quad.ul, quad.ur, quad.lr, quad.ll)) + ' Z')
            elif kind == 'qu':
                quad = item[1] * matrix
                commands.append('M' + ' L'.join(f'{p.x:.2f},{p.y:.2f}' for p in (quad.ul, quad.ur, quad.lr, quad.ll)) + ' Z')
        if not commands:
            continue
        if drawing.get('closePath'):
            commands[-1] += ' Z'
        fill = _colour(drawing.get('fill')) if drawing.get('fill') is not None else 'none'
        stroke = _colour(drawing.get('color')) if drawing.get('color') is not None else 'none'
        width_attr = drawing.get('width') or 1
        opacity = drawing.get('fill_opacity') if drawing.get('fill') is not None else drawing.get('stroke_opacity')
        dashes = drawing.get('dashes') or ''
        dash = ''
        if dashes and dashes.strip() not in ('[] 0', '[] 0.0'):
            numbers = dashes.split(']')[0].strip('[ ')
            if numbers:
                dash = f' stroke-dasharray="{html.escape(numbers)}"'
        parts.append(f'<path d="{" ".join(commands)}" fill="{fill}" stroke="{stroke}" '
                     f'stroke-width="{width_attr:g}" opacity="{opacity if opacity is not None else 1:g}"'
                     f'{" fill-rule=\"evenodd\"" if drawing.get("even_odd") else ""}{dash}/>')
        count += 1
    parts.append('</svg>')
    if not count:
        raise OperationError('No vector drawings were found in this area.')
    return '\n'.join(parts)


# ---------------------------------------------------------------- images

def image_inventory(doc):
    """Unique image XObjects with their placements: xref, size, format, pages, count, smask."""
    inventory = {}
    for page in doc:
        for info in page.get_image_info(xrefs=True):
            xref = info.get('xref', 0)
            if not xref:
                continue
            entry = inventory.setdefault(xref, {'xref': xref, 'width': info.get('width'), 'height': info.get('height'),
                                                'pages': [], 'placements': 0, 'smask': 0, 'ext': ''})
            entry['placements'] += 1
            if page.number not in entry['pages']:
                entry['pages'].append(page.number)
    for xref, entry in inventory.items():
        try:
            data = doc.extract_image(xref)
            entry['ext'] = data.get('ext', '')
            entry['smask'] = data.get('smask', 0)
            entry['bytes'] = len(data.get('image') or b'')
        except Exception:
            entry['bytes'] = 0
    return sorted(inventory.values(), key=lambda e: (e['pages'][0] if e['pages'] else 0, e['xref']))


def image_bytes(doc, xref, with_transparency=True):
    """Return (extension, bytes) for an image, merging its soft mask into a PNG if requested."""
    data = doc.extract_image(xref)
    if not data or not data.get('image'):
        raise OperationError('The image data could not be read.')
    if with_transparency and data.get('smask'):
        base = fitz.Pixmap(doc, xref)
        mask = fitz.Pixmap(doc, data['smask'])
        if base.alpha:
            base = fitz.Pixmap(base, 0)
        if base.colorspace and base.colorspace.n not in (1, 3):
            base = fitz.Pixmap(fitz.csRGB, base)
        combined = fitz.Pixmap(base, mask)
        return 'png', combined.tobytes('png')
    return data['ext'], data['image']


def thumbnail(doc, xref, size=96):
    pix = fitz.Pixmap(doc, xref)
    if pix.colorspace and pix.colorspace.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if pix.alpha:
        pix = fitz.Pixmap(pix, 0)
    scale = max(pix.width, pix.height) / size
    if scale > 1:
        pix.shrink(int(min(6, max(1, scale.bit_length() - 1))))
    return pix.tobytes('png')


def replace_image_everywhere(doc, xref, path=None, stream=None):
    """Replace a shared image XObject so every placement shows the new picture."""
    numbers = [page.number for page in doc
               if any(info.get('xref') == xref for info in page.get_image_info(xrefs=True))]
    if not numbers:
        raise OperationError('The image is not placed on any page.')
    if stream is None:
        with open(path, 'rb') as handle:
            stream = handle.read()
    try:
        fitz.Pixmap(stream)
    except Exception:
        raise OperationError('The file is not a supported image.')
    first = doc[numbers[0]]
    # MuPDF updates the shared XObject, so every placement shows the new image.
    first.replace_image(xref, stream=stream)
    doc._reset_page_refs()
    return numbers


def export_images_archive(doc, include_inline=True, merge_transparency=True):
    """ZIP of every image: XObjects (with soft masks merged) and inline images."""
    buffer = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for entry in image_inventory(doc):
            try:
                ext, data = image_bytes(doc, entry['xref'], merge_transparency)
            except Exception:
                continue
            archive.writestr(f"image-{entry['xref']}.{ext}", data)
            count += 1
        if include_inline:
            for page in doc:
                for index, block in enumerate(page.get_text('dict', flags=fitz.TEXT_PRESERVE_IMAGES)['blocks']):
                    if block.get('type') != 1 or not block.get('image'):
                        continue
                    # Blocks for XObject images repeat data already exported; inline
                    # images have no xref and appear only here.
                    if _is_xobject_block(page, block):
                        continue
                    archive.writestr(f"inline-page-{page.number + 1}-{index + 1}.{block.get('ext', 'png')}",
                                     block['image'])
                    count += 1
    if not count:
        raise OperationError('This document contains no images.')
    return buffer.getvalue(), count


def _is_xobject_block(page, block):
    bbox = fitz.Rect(block['bbox'])
    for info in page.get_image_info(xrefs=True):
        if info.get('xref') and fitz.Rect(info['bbox']) == bbox:
            return True
    return False
