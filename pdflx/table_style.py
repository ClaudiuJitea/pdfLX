"""Build reversible table styles without changing the live canvas during preview."""
import copy
import math
import pymupdf as fitz
from .models import EditableShape, EditableText
from . import pdf_handler
from .table_creation import _wrap

PRESETS = {
    'plain': dict(fill=False, header=True, striped=False, header_fill=(1,1,1), body_fill=(1,1,1),
                  alternate_fill=(.95,.96,.98), border_color=(.45,.47,.5), text_color=(.1,.1,.1), header_text=(.1,.1,.1)),
    'blue': dict(fill=True, header=True, striped=False, header_fill=(.12,.29,.48), body_fill=(1,1,1),
                 alternate_fill=(.93,.96,.99), border_color=(.65,.72,.8), text_color=(.12,.16,.22), header_text=(1,1,1)),
    'green': dict(fill=True, header=True, striped=False, header_fill=(.13,.39,.31), body_fill=(1,1,1),
                  alternate_fill=(.92,.97,.94), border_color=(.6,.73,.66), text_color=(.1,.2,.16), header_text=(1,1,1)),
    'striped': dict(fill=True, header=True, striped=True, header_fill=(.22,.25,.3), body_fill=(1,1,1),
                    alternate_fill=(.94,.95,.97), border_color=(.75,.77,.8), text_color=(.12,.14,.18), header_text=(1,1,1)),
}


def current_style(table):
    shapes = [obj for obj in table.objects if isinstance(obj, EditableShape)]
    texts = [obj for obj in table.objects if isinstance(obj, EditableText)]
    style = dict(PRESETS['plain'], border_width=shapes[0].stroke_width,
                 font_size=texts[0].font_size if texts else 10)
    style.update(getattr(shapes[0], 'table_style', {}))
    return style


def style_states(table, page, style):
    """Return all new object states; validate wrapping before a PDF is modified."""
    for key in ('header_fill','body_fill','alternate_fill','border_color','text_color','header_text'):
        if len(style[key]) != 3 or not all(math.isfinite(v) and 0 <= v <= 1 for v in style[key]):
            raise ValueError('Choose valid table colors.')
    if not math.isfinite(style['border_width']) or not .1 <= style['border_width'] <= 5:
        raise ValueError('Choose a border width between 0.1 and 5 pt.')
    if not math.isfinite(style['font_size']) or not 6 <= style['font_size'] <= 24:
        raise ValueError('Choose a font size between 6 and 24 pt.')
    shapes = [obj for obj in table.objects if isinstance(obj, EditableShape)]
    tops = sorted({round((fitz.Rect(obj.bbox)*page.rotation_matrix).y0, 2) for obj in shapes})
    cells = []
    for shape in shapes:
        visual = fitz.Rect(shape.bbox)*page.rotation_matrix
        row = getattr(shape, 'table_row', min(range(len(tops)), key=lambda i: abs(tops[i]-visual.y0)))
        cells.append((shape, visual, row))
    states = []
    for obj in table.objects:
        state = copy.deepcopy(obj.__dict__)
        if isinstance(obj, EditableShape):
            _, _, row = next(cell for cell in cells if cell[0] is obj)
            is_header = bool(style['header'] and row == 0)
            stripe = style['striped'] and (row-int(style['header'])) % 2 == 1
            state.update(fill_color=style['header_fill'] if is_header else
                         style['alternate_fill'] if stripe else style['body_fill'],
                         is_transparent=not style['fill'], stroke_color=style['border_color'],
                         stroke_width=style['border_width'], table_style=copy.deepcopy(style))
        elif isinstance(obj, EditableText):
            coordinates = (getattr(obj, 'table_row', None), getattr(obj, 'table_column', None))
            center = (fitz.Rect(obj.bbox).tl+fitz.Rect(obj.bbox).br)/2
            cell = next((cell for cell in cells if coordinates ==
                         (getattr(cell[0], 'table_row', -1), getattr(cell[0], 'table_column', -1))), None)
            if cell is None:
                cell = next((cell for cell in cells if center in fitz.Rect(cell[0].bbox)), None)
            if cell is None:
                raise ValueError('A table text object is outside its cell.')
            _, bounds, row = cell
            bold = bool(style['header'] and row == 0)
            clone = copy.deepcopy(obj)
            clone.is_bold = bold
            clone.font_size = style['font_size']
            args, error = pdf_handler._get_font_args_for_pymupdf(clone)
            if error:
                raise ValueError(error)
            font = fitz.Font(**args)
            value = obj.table_source_text if obj.text == getattr(obj, 'table_wrapped_text', None) else obj.text
            padding = min(6, bounds.width/8, bounds.height/3)
            lines = _wrap(value, font, clone.font_size, bounds.width-2*padding)
            height = (len(lines)-1)*clone.font_size*1.2+(font.ascender-font.descender)*clone.font_size
            if height > bounds.height-2*padding+.01:
                raise ValueError('The text does not fit at this font size. Choose a smaller size or enlarge the table first.')
            inner = bounds+(padding,padding,-padding,-padding)
            native_center = (inner.tl+inner.br)/2*page.derotation_matrix
            rect = fitz.Rect(native_center.x-inner.width/2, native_center.y-inner.height/2,
                             native_center.x+inner.width/2, native_center.y+inner.height/2)
            state.update(color=style['header_text'] if bold else style['text_color'], is_bold=bold,
                         font_size=clone.font_size, bbox=tuple(rect), x=rect.x0, y=rect.y0,
                         baseline=rect.y0+font.ascender*clone.font_size,
                         text='\n'.join(lines), table_source_text=value, table_wrapped_text='\n'.join(lines))
        states.append(state)
    return states
