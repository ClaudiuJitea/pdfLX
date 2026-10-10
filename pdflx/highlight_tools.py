"""Native highlight annotations: hit-testing, partial erasing and recoloring.

All geometry is in unrotated (native) page coordinates.
"""
import pymupdf as fitz

from . import pdf_handler

# Swatches offered by the highlight context menus.
COLORS=((1.0,0.9,0.0),(0.55,0.9,0.35),(0.45,0.8,1.0),(1.0,0.6,0.8),(1.0,0.65,0.2))
MIN_PIECE=1.0
# Same translucency as the freehand highlighter stroke, so both look alike.
OPACITY=0.35


def _highlights(page):
    return [annot for annot in page.annots(types=[fitz.PDF_ANNOT_HIGHLIGHT]) or ()]


def _quads(annot):
    vertices=annot.vertices or []
    quads=[fitz.Quad(*vertices[i:i+4]) for i in range(0,len(vertices)-3,4)]
    return quads or [annot.rect.quad]


def _is_axis_aligned(quad):
    return abs(quad.ul.y-quad.ur.y)<0.5 and abs(quad.ul.x-quad.ll.x)<0.5


def _subtract(quad,erasers):
    """Return the parts of ``quad`` not covered by any eraser rectangle."""
    pieces=[quad]
    for eraser in erasers:
        remaining=[]
        for piece in pieces:
            rect=piece.rect
            overlap=min(rect.y1,eraser.y1)-max(rect.y0,eraser.y0)
            middle=(rect.y0+rect.y1)/2
            # Touching the line above or below must not erase this line, but a
            # thin sweep through the middle of the line does.
            if not rect.intersects(eraser) or (overlap<rect.height*0.4 and not eraser.y0<=middle<=eraser.y1):
                remaining.append(piece)
                continue
            if not _is_axis_aligned(piece):
                continue
            if eraser.x0-rect.x0>=MIN_PIECE:
                remaining.append(fitz.Rect(rect.x0,rect.y0,eraser.x0,rect.y1).quad)
            if rect.x1-eraser.x1>=MIN_PIECE:
                remaining.append(fitz.Rect(eraser.x1,rect.y0,rect.x1,rect.y1).quad)
        pieces=remaining
    return pieces


def highlight_at(doc,page_number,point,tolerance=1.5):
    """Return the topmost highlight annotation info under ``point``."""
    page=doc[page_number]
    point=fitz.Point(point)
    for annot in reversed(_highlights(page)):
        for quad in _quads(annot):
            if point in quad.rect+(-tolerance,-tolerance,tolerance,tolerance):
                return {'xref':annot.xref,'color':tuple(annot.colors.get('stroke') or COLORS[0]),
                        'rect':tuple(annot.rect)}
    return None


def _erased(annot,erasers):
    """Remaining quads after erasing, or None when the highlight is untouched."""
    quads=_quads(annot)
    kept=[piece for quad in quads for piece in _subtract(quad,erasers)]
    # Untouched quads are passed through as the same objects.
    if len(kept)==len(quads) and all(a is b for a,b in zip(kept,quads)):
        return None
    return kept


def overlaps(doc,page_number,rects):
    """Whether erasing ``rects`` would change any highlight."""
    erasers=[fitz.Rect(rect) for rect in rects]
    page=doc[page_number]  # annotations are only usable while their page is alive
    return any(_erased(annot,erasers) is not None for annot in _highlights(page))


def erase(doc,page_number,rects):
    """Remove highlighted areas under ``rects``, splitting highlights as needed.

    Returns the number of highlight annotations changed or removed.
    """
    page=doc[page_number]
    erasers=[fitz.Rect(rect) for rect in rects if not fitz.Rect(rect).is_empty]
    if not erasers:
        return 0
    changed=0
    for annot in _highlights(page):
        kept=_erased(annot,erasers)
        if kept is None:
            continue
        changed+=1
        if kept:
            _clone(page,annot,kept)
        page.delete_annot(annot)
    if changed:
        doc._reset_page_refs()
        pdf_handler.invalidate_page_cache(doc,page_number)
    return changed


def add(page,quads,color=COLORS[0],opacity=OPACITY,**info):
    """Add a highlight annotation drawn like the freehand highlighter."""
    annot=page.add_highlight_annot(quads=quads)
    annot.set_colors(stroke=color)
    if info:
        annot.set_info(**info)
    annot.update()
    style(page.parent,annot,opacity)
    return annot


def _transpose(rect):
    return fitz.Rect(rect.y0,rect.x0,rect.y1,rect.x1)


def _merge(rects):
    """Join rectangles on the same line into continuous marker bars."""
    bars=[]
    for rect in sorted(rects,key=lambda r:(round(-r.y1),r.x0)):
        for index,bar in enumerate(bars):
            overlap=min(bar.y1,rect.y1)-max(bar.y0,rect.y0)
            height=max(bar.height,rect.height)
            if overlap>=0.5*min(bar.height,rect.height) and rect.x0-bar.x1<=0.6*height and bar.x0-rect.x1<=0.6*height:
                bars[index]=bar|rect
                break
        else:
            bars.append(fitz.Rect(rect))
    # Pad like the square-capped marker stroke.
    return [bar+(-0.2*bar.height,-0.1*bar.height,0.2*bar.height,0.1*bar.height) for bar in bars]


def style(doc,annot,opacity=OPACITY):
    """Replace MuPDF's rounded, opaque appearance with a flat translucent marker.

    Geometry comes from /QuadPoints, which are in PDF user space like the
    appearance stream, so no page transform is involved.
    """
    kind,value=doc.xref_get_key(annot.xref,'QuadPoints')
    numbers=[float(item) for item in value.strip('[]').split()] if kind=='array' else []
    points=[fitz.Point(numbers[i],numbers[i+1]) for i in range(0,len(numbers)-1,2)]
    quads=[points[i:i+4] for i in range(0,len(points)-3,4)]
    if not quads:
        return
    color=annot.colors.get('stroke') or COLORS[0]
    horizontal,vertical,polygons=[],[],[]
    for quad in quads:
        box=fitz.Rect(min(p.x for p in quad),min(p.y for p in quad),max(p.x for p in quad),max(p.y for p in quad))
        if not all(min(abs(p.x-box.x0),abs(p.x-box.x1))<0.5 and min(abs(p.y-box.y0),abs(p.y-box.y1))<0.5 for p in quad):
            polygons.append(quad)
        elif abs(quad[1].y-quad[0].y)>abs(quad[1].x-quad[0].x):
            vertical.append(box)  # text running up or down the page
        else:
            horizontal.append(box)
    bars=_merge(horizontal)+[_transpose(bar) for bar in _merge([_transpose(box) for box in vertical])]
    paths=[f'{bar.x0:.3f} {bar.y0:.3f} {bar.width:.3f} {bar.height:.3f} re' for bar in bars]
    paths+=[f'{ul.x:.3f} {ul.y:.3f} m {ur.x:.3f} {ur.y:.3f} l {lr.x:.3f} {lr.y:.3f} l {ll.x:.3f} {ll.y:.3f} l h'
            for ul,ur,ll,lr in polygons]
    corners=[point for bar in bars for point in (bar.tl,bar.br)]+[point for quad in polygons for point in quad]
    bounds=fitz.Rect(min(p.x for p in corners),min(p.y for p in corners),
                     max(p.x for p in corners),max(p.y for p in corners))
    # One fill paints overlapping bars once, so overlaps do not darken.
    content=f'q /H gs {color[0]:g} {color[1]:g} {color[2]:g} rg\n'+'\n'.join(paths)+'\nf Q\n'
    kind,value=doc.xref_get_key(annot.xref,'AP/N')
    if kind!='xref':
        return
    stream=int(value.split()[0])
    box=f'[{bounds.x0:.3f} {bounds.y0:.3f} {bounds.x1:.3f} {bounds.y1:.3f}]'
    gstate=f'<</ExtGState<</H<</CA {opacity:g}/ca {opacity:g}/BM/Multiply>>>>>>'
    doc.update_stream(stream,content.encode())
    doc.xref_set_key(stream,'BBox',box)
    doc.xref_set_key(stream,'Matrix','[1 0 0 1 0 0]')
    doc.xref_set_key(stream,'Resources',gstate)
    doc.xref_set_key(annot.xref,'Rect',box)
    doc.xref_set_key(annot.xref,'CA',f'{opacity:g}')
    doc.xref_set_key(annot.xref,'BM','/Multiply')


def _clone(page,annot,quads):
    colors=annot.colors
    info=annot.info
    opacity=annot.opacity
    opacity=opacity if opacity is not None and 0<opacity<1 else OPACITY
    return add(page,quads,colors.get('stroke') or COLORS[0],opacity,
               title=info.get('title',''),content=info.get('content',''),subject=info.get('subject',''))


def delete(doc,page_number,xref):
    page=doc[page_number]
    page.delete_annot(page.load_annot(xref))
    doc._reset_page_refs()
    pdf_handler.invalidate_page_cache(doc,page_number)


def recolor(doc,page_number,xref,color):
    page=doc[page_number]
    annot=page.load_annot(xref)
    opacity=annot.opacity
    annot.set_colors(stroke=color)
    annot.update()
    style(doc,annot,opacity if opacity is not None and 0<opacity<1 else OPACITY)
    doc._reset_page_refs()
    pdf_handler.invalidate_page_cache(doc,page_number)


def _resample(points,step=1.0):
    result=[fitz.Point(points[0])]
    for a,b in zip(points,points[1:]):
        a,b=fitz.Point(a),fitz.Point(b)
        count=max(1,int(abs(b-a)/step))
        result+=[a+(b-a)*(i/count) for i in range(1,count+1)]
    return result


def split_stroke(points,rects):
    """Point runs of a freehand stroke left outside ``rects``, or None if untouched."""
    if len(points)<2:
        return None if not any(fitz.Point(points[0]) in fitz.Rect(r) for r in rects) else []
    erasers=[fitz.Rect(rect) for rect in rects]
    runs,current,touched=[],[],False
    for point in _resample(points):
        if any(point in rect for rect in erasers):
            touched=True
            if len(current)>=2:
                runs.append(current)
            current=[]
        else:
            current.append((point.x,point.y))
    if not touched:
        return None
    if len(current)>=2:
        runs.append(current)
    return runs


def stroke_hit(points,width,point):
    """Whether ``point`` lies on a freehand stroke of the given width."""
    point=fitz.Point(point)
    reach=width/2+1.5
    if len(points)==1:
        return abs(point-fitz.Point(points[0]))<=reach
    for a,b in zip(points,points[1:]):
        a,b=fitz.Point(a),fitz.Point(b)
        length=abs(b-a)**2
        t=0 if not length else max(0,min(1,((point.x-a.x)*(b.x-a.x)+(point.y-a.y)*(b.y-a.y))/length))
        if abs(point-(a+(b-a)*t))<=reach:
            return True
    return False


_cursor=None


ERASER_ICON='icons/hicolor/scalable/actions/editor-erase-highlight-symbolic.svg'


def _draw_eraser(cr,size=32,margin=4):
    """Paint the eraser toolbar icon with a thin white outline for contrast."""
    import math
    import os
    import cairo
    import gi
    gi.require_version('GdkPixbuf','2.0')
    gi.require_version('Gdk','4.0')
    from gi.repository import Gdk,GdkPixbuf
    glyph=size-2*margin
    pixbuf=GdkPixbuf.Pixbuf.new_from_file_at_size(
        os.path.join(os.path.dirname(__file__),ERASER_ICON),glyph,glyph)
    icon=cairo.ImageSurface(cairo.FORMAT_ARGB32,glyph,glyph)
    icon_cr=cairo.Context(icon)
    Gdk.cairo_set_source_pixbuf(icon_cr,pixbuf,0,0)
    icon_cr.paint()
    cr.set_source_rgb(1,1,1)
    for step in range(16):
        angle=step*math.pi/8
        cr.mask_surface(icon,margin+1.5*math.cos(angle),margin+1.5*math.sin(angle))
    # Same colour as the symbolic icon in the light theme.
    cr.set_source_rgb(0.18,0.2,0.21)
    cr.mask_surface(icon,margin,margin)


def eraser_cursor():
    """Pointer shaped like the highlight eraser's toolbar icon."""
    global _cursor
    if _cursor is None:
        import cairo
        from gi.repository import Gdk,GLib
        fallback=Gdk.Cursor.new_from_name('crosshair')
        try:
            size=32
            surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,size,size)
            _draw_eraser(cairo.Context(surface),size)
            surface.flush()
            texture=Gdk.MemoryTexture.new(size,size,Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                          GLib.Bytes.new(bytes(surface.get_data())),surface.get_stride())
            # The hotspot is where the eraser's tip meets the baseline.
            _cursor=Gdk.Cursor.new_from_texture(texture,10,23,fallback)
        except Exception:
            _cursor=fallback
    return _cursor
