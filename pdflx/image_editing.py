"""Non-destructive image transforms and vector PDF framing."""
import math
import pymupdf as fitz

DEFAULTS = dict(crop=(0., 0., 0., 0.), flip_horizontal=False, flip_vertical=False,
                opacity=1., corner_radius=0., border_width=0., border_color=(0.,0.,0.),
                shadow=False, shadow_offset=5., shadow_opacity=.2)


def settings(obj):
    return {key: getattr(obj, key, value) for key, value in DEFAULTS.items()}


def validate(obj):
    s = settings(obj)
    values = (*obj.bbox, getattr(obj, 'rotation', 0), *s['crop'], s['opacity'],
              s['corner_radius'], s['border_width'], *s['border_color'],
              s['shadow_offset'], s['shadow_opacity'])
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError('Image settings must contain finite numbers.')
    left, top, right, bottom = s['crop']
    if min(s['crop']) < 0 or left+right >= .99 or top+bottom >= .99:
        raise ValueError('Crop must leave at least 1% of the image visible.')
    if not 0 <= s['opacity'] <= 1 or not 0 <= s['shadow_opacity'] <= 1:
        raise ValueError('Opacity must be between 0 and 100%.')
    if min(s['corner_radius'], s['border_width'], s['shadow_offset']) < 0:
        raise ValueError('Corner, border and shadow sizes cannot be negative.')
    if len(s['border_color']) != 3 or any(not 0 <= c <= 1 for c in s['border_color']):
        raise ValueError('Invalid border color.')
    if fitz.Rect(obj.bbox).is_empty:
        raise ValueError('Image width and height must be positive.')
    return s


def matrix_text(matrix):
    return ' '.join(f'{v:.9f}' for v in matrix)+' cm\n'


def rounded_path(rect, radius):
    """PDF path operators in the caller's coordinate system."""
    x,y,X,Y = rect
    r = min(radius, (X-x)/2, (Y-y)/2)
    k = .5522847498*r
    return (f'{x+r} {y} m {X-r} {y} l '
            f'{X-r+k} {y} {X} {y+r-k} {X} {y+r} c {X} {Y-r} l '
            f'{X} {Y-r+k} {X-r+k} {Y} {X-r} {Y} c {x+r} {Y} l '
            f'{x+r-k} {Y} {x} {Y-r+k} {x} {Y-r} c {x} {y+r} l '
            f'{x} {y+r-k} {x+r-k} {y} {x+r} {y} c h\n')


def alpha_resource(doc, page, value):
    kind, resource = doc.xref_get_key(page.xref, 'Resources')
    if kind != 'xref':
        xref = doc.get_new_xref()
        doc.update_object(xref, resource if kind == 'dict' else '<<>>')
        doc.xref_set_key(page.xref, 'Resources', f'{xref} 0 R')
    else:
        xref = int(resource.split()[0])
    # Resource dictionaries may be shared; clone before adding our state.
    new = doc.get_new_xref()
    doc.update_object(new, doc.xref_object(xref))
    doc.xref_set_key(page.xref, 'Resources', f'{new} 0 R')
    kind, states = doc.xref_get_key(new, 'ExtGState')
    if kind == 'xref':
        doc.xref_set_key(new, 'ExtGState', doc.xref_object(int(states.split()[0])))
    name = f'ImageAlpha{new}'
    doc.xref_set_key(new, f'ExtGState/{name}', f'<</Type/ExtGState/ca {value}/CA {value}>>')
    return f'/{name} gs\n'


def insert_image(doc, page, obj, overlay=True):
    s = validate(obj)
    rect = fitz.Rect(obj.bbox)
    rotation = page.rotation
    try:
        page.set_rotation(0)
        p = page.transformation_matrix
        cx,cy = (rect.x0+rect.x1)/2, (rect.y0+rect.y1)/2
        angle = getattr(obj, 'rotation', 0)%360
        rotate = fitz.Matrix(1,0,0,1,-cx,-cy)*fitz.Matrix(angle)*fitz.Matrix(1,0,0,1,cx,cy)
        left,top,right,bottom = s['crop']
        full = fitz.Rect(rect.x0-left*rect.width/(1-left-right),
                         rect.y0-top*rect.height/(1-top-bottom),
                         rect.x1+right*rect.width/(1-left-right),
                         rect.y1+bottom*rect.height/(1-top-bottom))
        page.insert_image(full, stream=obj.image_bytes, keep_proportion=False, overlay=overlay)
        content = page.get_contents()[-1 if overlay else 0]
        original = doc.xref_stream(content)
        # All decoration and clipping coordinates below use the same y-down
        # page coordinates as the model. Source pixels and alpha stay intact.
        prefix = 'q\n'+matrix_text(p*rotate*~p)+matrix_text(~p)
        path = rounded_path(rect, s['corner_radius'])
        if s['shadow']:
            offset = s['shadow_offset']
            prefix += 'q\n'+alpha_resource(doc,page,s['shadow_opacity']*s['opacity'])
            prefix += '0 0 0 rg\n'+rounded_path(rect+(offset,offset,offset,offset),s['corner_radius'])+'f\nQ\n'
        prefix += 'q\n'+path+'W n\n'+alpha_resource(doc,page,s['opacity'])
        if s['flip_horizontal'] or s['flip_vertical']:
            sx = -1 if s['flip_horizontal'] else 1
            sy = -1 if s['flip_vertical'] else 1
            prefix += matrix_text(fitz.Matrix(sx,0,0,sy,(1-sx)*cx,(1-sy)*cy))
        prefix += matrix_text(p)
        suffix = '\nQ\n'
        if s['border_width']:
            suffix += alpha_resource(doc,page,s['opacity'])
            suffix += ' '.join(str(c) for c in s['border_color'])+' RG\n'
            # Inside border, so the frame does not change selection geometry.
            half = min(s['border_width'],rect.width/2,rect.height/2)/2
            suffix += f'{half*2} w\n'+rounded_path(rect+(half,half,-half,-half),max(0,s['corner_radius']-half))+'S\n'
        suffix += 'Q\n'
        doc.update_stream(content, prefix.encode('ascii')+original+suffix.encode('ascii'))
    finally:
        page.set_rotation(rotation)


def preview(obj, max_size=460):
    """Render the same PDF operators as export, with room for tilt and shadow."""
    import copy
    clone = copy.deepcopy(obj)
    rect = fitz.Rect(obj.bbox)
    scale = min(1., max_size/max(rect.width,rect.height))
    clone.bbox = (30,30,30+rect.width*scale,30+rect.height*scale)
    for key in ('corner_radius','border_width','shadow_offset'):
        setattr(clone,key,settings(obj)[key]*scale)
    w,h = rect.width*scale,rect.height*scale
    side = math.hypot(w,h)+80
    clone.bbox = ((side-w)/2,(side-h)/2,(side+w)/2,(side+h)/2)
    with fitz.open() as doc:
        page = doc.new_page(width=side,height=side)
        insert_image(doc,page,clone)
        return page.get_pixmap(alpha=True).tobytes('png')


def drag_preview(obj):
    """Small image-only preview; the canvas applies tilt itself."""
    s = settings(obj)
    rect = fitz.Rect(obj.bbox)
    padding = max(2., s['shadow_offset']+2 if s['shadow'] else 2.)
    style = tuple((key,tuple(value) if isinstance(value,list) else value) for key,value in s.items())
    return _drag_png(obj.image_bytes,rect.width,rect.height,style,padding),padding


from functools import lru_cache


@lru_cache(maxsize=4)
def _drag_png(data,width,height,style,padding):
    from types import SimpleNamespace
    clone=SimpleNamespace(image_bytes=data,rotation=0,
                          bbox=(padding,padding,padding+width,padding+height),**dict(style))
    with fitz.open() as doc:
        page=doc.new_page(width=width+2*padding,height=height+2*padding)
        insert_image(doc,page,clone)
        scale=min(1.,700/max(page.rect.width,page.rect.height))
        return page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=True).tobytes('png')
