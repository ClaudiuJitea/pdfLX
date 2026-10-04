"""Document tools the AI agent can call. Everything here runs on the GTK main thread.

Coordinates are PDF points on the unrotated page, origin top-left, y downwards.
Pages are numbered from 1 for the model and converted here.
"""
import copy
import json
import re

import pymupdf as fitz

from .. import pdf_handler, utils
from ..models import EditableText, EditableShape, EditableImage, EditableStroke
from ..undo_manager import DocumentMutationCommand, _perform_ghost_erasure
from ..pdf_state import PdfState

FIELD_KINDS = {fitz.PDF_WIDGET_TYPE_TEXT: 'text', fitz.PDF_WIDGET_TYPE_CHECKBOX: 'checkbox',
               fitz.PDF_WIDGET_TYPE_COMBOBOX: 'combo', fitz.PDF_WIDGET_TYPE_LISTBOX: 'list',
               fitz.PDF_WIDGET_TYPE_RADIOBUTTON: 'radio', fitz.PDF_WIDGET_TYPE_BUTTON: 'button',
               fitz.PDF_WIDGET_TYPE_SIGNATURE: 'signature'}
SHAPES = ('rectangle', 'ellipse', 'polygon', 'right_triangle', 'star', 'arrow', 'callout', 'checkmark', 'cross')
SHAPE_OPTIONS = ('corner_radius', 'opacity', 'dash', 'sides', 'star_points', 'star_inner')
RENDER_LONG_SIDE = 1600
IMAGE_CONTENT = ('logo', 'photo', 'signature', 'stamp', 'qr_code', 'barcode', 'illustration')
NOT_AN_IMAGE = ('Text, tables, table headers, colored bands and lines must be rebuilt as text, rectangle '
                'and line elements so they stay editable; only {} may be cropped from a picture.'
                .format(', '.join(IMAGE_CONTENT)))


class ToolError(Exception):
    pass


def _num(value, name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ToolError(f'"{name}" must be a number.') from None


def parse_color(value, name='color'):
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().lstrip('#')
        if len(text) == 3:
            text = ''.join(c * 2 for c in text)
        if not re.fullmatch(r'[0-9a-fA-F]{6}', text):
            raise ToolError(f'"{name}" must be a hex color like #1a2b3c.')
        return tuple(int(text[i:i + 2], 16) / 255 for i in (0, 2, 4))
    if isinstance(value, (list, tuple)) and len(value) == 3:
        parts = [_num(v, name) for v in value]
        scale = 255 if any(p > 1 for p in parts) else 1
        return tuple(max(0.0, min(1.0, p / scale)) for p in parts)
    raise ToolError(f'"{name}" must be a hex color like #1a2b3c.')


def hex_color(color):
    if color is None:
        return None
    return '#' + ''.join(f'{round(max(0, min(1, c)) * 255):02x}' for c in color[:3])


def _r(value):
    return round(float(value), 2)


def _font_file(family, bold=False, italic=False):
    try:
        return utils.find_specific_font_variant(family, bold, italic)
    except Exception:
        return None


def _font(family, bold=False, italic=False):
    path = _font_file(family, bold, italic)
    if path:
        try:
            return fitz.Font(fontfile=path)
        except Exception:
            pass
    return fitz.Font('helv')


def match_font(name):
    """Return (installed family, note) for a requested font name."""
    families = list(utils.FONT_FAMILY_LIST_SORTED or [])
    wanted = (name or '').strip()
    if not families:
        return wanted or 'Liberation Sans', None
    lower = {family.lower(): family for family in families}
    if wanted.lower() in lower:
        return lower[wanted.lower()], None
    squash = lambda text: re.sub(r'[^a-z0-9]', '', text.lower())
    for family in families:
        if wanted and squash(wanted) == squash(family):
            return family, None
    generic = {'sans': 'Liberation Sans', 'serif': 'Liberation Serif', 'mono': 'Liberation Mono',
               'arial': 'Liberation Sans', 'helvetica': 'Liberation Sans', 'times': 'Liberation Serif',
               'times new roman': 'Liberation Serif', 'courier': 'Liberation Mono', 'courier new': 'Liberation Mono'}
    fallback = generic.get(wanted.lower())
    if fallback and fallback.lower() in lower:
        return lower[fallback.lower()], f'"{wanted}" is not installed; used {lower[fallback.lower()]}.'
    for family in families:
        if wanted and squash(wanted) in squash(family):
            return family, f'"{wanted}" is not installed; used {family}.'
    default = lower.get('liberation sans', families[0])
    return default, f'"{wanted}" is not installed; used {default}.' if wanted else None


def wrap_text(text, font, size, width):
    lines = []
    for paragraph in text.split('\n'):
        words = paragraph.split(' ')
        line = ''
        for word in words:
            candidate = f'{line} {word}' if line else word
            if line and font.text_length(candidate, fontsize=size) > width:
                lines.append(line)
                line = word
            else:
                line = candidate
        lines.append(line)
    return '\n'.join(lines)


def text_metrics(obj):
    font = _font(obj.font_family_base, obj.is_bold, obj.is_italic)
    lines = (obj.text or ' ').split('\n')
    width = max(font.text_length(line, fontsize=obj.font_size) for line in lines)
    return font, width, len(lines)


def new_text_object(spec, index, notes):
    family, note = match_font(spec.get('font') or 'Liberation Sans')
    if note:
        notes.append(note)
    bold, italic = bool(spec.get('bold')), bool(spec.get('italic'))
    size = _num(spec.get('size', 11), 'size')
    if not 1 <= size <= 1000:
        raise ToolError('"size" must be between 1 and 1000.')
    x, y = _num(spec.get('x'), 'x'), _num(spec.get('y'), 'y')
    font = _font(family, bold, italic)
    text = str(spec.get('text', ''))
    width = spec.get('width')
    if width is not None:
        width = _num(width, 'width')
        if spec.get('wrap', True):
            text = wrap_text(text, font, size, width)
    obj = EditableText(x, y, text, font_size=size, font_family=family, color=parse_color(spec.get('color', '#000000')),
                       is_new=True, baseline=y + font.ascender * size, page_number=index,
                       alignment=spec.get('align', 'left'))
    obj.font_family_base = family
    obj.is_bold, obj.is_italic = bold, italic
    obj.is_underline, obj.is_strikethrough = bool(spec.get('underline')), bool(spec.get('strikethrough'))
    obj.rotation = float(spec.get('rotation', 0)) % 360
    _font_obj, natural, lines = text_metrics(obj)
    obj.bbox = (x, y, x + max(natural, width or 0), y + (lines - 1) * size * 1.2 + (font.ascender - font.descender) * size)
    obj.original_bbox = obj.bbox
    return obj


class AiTurnCommand(DocumentMutationCommand):
    """One undo step for everything the assistant changed in a request."""
    def __init__(self, window, before, after, page_num):
        super().__init__(window, None, page_num)
        self.before, self.after = before, after


class DocumentTools:
    def __init__(self, window, attachments):
        self.window = window
        self.attachments = attachments  # callable returning the conversation's attachments
        self._counter = 0
        self._turns = {}

    # ------------------------------------------------------------ helpers
    @property
    def doc(self):
        return self.window.doc

    def require_doc(self):
        if not self.doc:
            raise ToolError('No document is open. Call new_document first.')
        return self.doc

    def page_index(self, page):
        doc = self.require_doc()
        index = int(_num(page if page is not None else self.window.current_page_index + 1, 'page')) - 1
        if not 0 <= index < doc.page_count:
            raise ToolError(f'Page {index + 1} does not exist; the document has {doc.page_count} pages.')
        return index

    def goto(self, index):
        if index != self.window.current_page_index or self.window.continuous_view.enabled:
            self.window._load_page(index)

    def editable(self):
        doc = self.require_doc()
        if not getattr(self.window._active_session, 'can_edit', True):
            raise ToolError('This document is protected against editing.')
        if self.window.view_mode:
            self.window._toggle_view_edit_mode()
        self.window._apply_and_hide_editor(force_apply=True)
        self.window.commit_pending_format_change()
        key = id(doc)
        if key not in self._turns:
            # Taken before the first change so the whole request undoes at once.
            self._turns[key] = (doc, self.window.undo_manager, len(self.window.undo_manager.undo_stack),
                                PdfState(self.window))
        return doc

    def mutate(self, mutation, page_index, allow_form=False):
        if not self.window._mutate_document(mutation, page_num=page_index, allow_form=allow_form):
            raise ToolError('The document could not be changed.')

    def end_turn(self):
        """Collapse the request's undo entries into one step."""
        turns, self._turns = self._turns, {}
        for doc, manager, start, before in turns.values():
            if self.window.doc is not doc or self.window.undo_manager is not manager:
                continue
            added = manager.undo_stack[start:]
            if len(added) < 2:
                continue
            del manager.undo_stack[start:]
            # The starting page exists both before and after the request (undo and redo show it first).
            manager.undo_stack.append(AiTurnCommand(self.window, before, PdfState(self.window),
                                                    min(before.page_index, self.window.doc.page_count - 1)))
            manager.redo_stack.clear()
            manager._update_ui_callback()

    def ident(self, obj):
        if not getattr(obj, '_ai_id', None):
            self._counter += 1
            prefix = {EditableText: 't', EditableShape: 's', EditableImage: 'i', EditableStroke: 'l'}[type(obj)]
            obj._ai_id = f'{prefix}{self._counter}'
        return obj._ai_id

    def objects(self):
        w = self.window
        return list(w.editable_texts) + list(w.editable_shapes) + list(w.editable_images) + list(w.editable_strokes)

    def find(self, ids):
        by_id = {self.ident(obj): obj for obj in self.objects()}
        fields = {f'f{field["xref"]}': field for field in self.fields()}
        found, missing = [], []
        for ident in ids:
            target = by_id.get(ident) or fields.get(ident)
            (found if target is not None else missing).append((ident, target))
        if missing:
            raise ToolError('Unknown element ids on this page: ' + ', '.join(i for i, _ in missing)
                            + '. Call list_elements to get current ids.')
        return found

    def fields(self):
        from ..document_features import list_form_fields
        return list_form_fields(self.doc, [self.window.current_page_index])

    # ------------------------------------------------------------ description
    def describe(self, obj):
        x0, y0, x1, y1 = obj.bbox
        base = {'id': self.ident(obj), 'x': _r(x0), 'y': _r(y0), 'width': _r(x1 - x0), 'height': _r(y1 - y0)}
        if getattr(obj, 'rotation', 0):
            base['rotation'] = _r(obj.rotation)
        if getattr(obj, 'table_id', None):
            base['table'] = obj.table_id[:8]
        if isinstance(obj, EditableText):
            base.update(type='text', text=obj.text, font=obj.font_family_base, size=_r(obj.font_size),
                        color=hex_color(obj.color), baseline=_r(obj.baseline))
            for flag, attr in (('bold', 'is_bold'), ('italic', 'is_italic'), ('underline', 'is_underline'),
                               ('strikethrough', 'is_strikethrough')):
                if getattr(obj, attr, False):
                    base[flag] = True
            if getattr(obj, 'alignment', 'left') != 'left':
                base['align'] = obj.alignment
        elif isinstance(obj, EditableShape):
            base.update(type=obj.shape_type, stroke=hex_color(obj.stroke_color) if obj.stroke_width > 0 else None,
                        stroke_width=_r(obj.stroke_width), fill=None if obj.is_transparent else hex_color(obj.fill_color))
            from ..shape_tools import SHAPE_DEFAULTS, APPLIES
            for name in SHAPE_OPTIONS:
                value = getattr(obj, name, SHAPE_DEFAULTS[name])
                if value != SHAPE_DEFAULTS[name] and (name not in APPLIES or obj.shape_type in APPLIES[name]):
                    base[name] = _r(value) if isinstance(value, float) else value
        elif isinstance(obj, EditableImage):
            base['type'] = 'image'
        elif isinstance(obj, EditableStroke):
            points = [[_r(x), _r(y)] for x, y in obj.points]
            base.update(type='line' if len(points) == 2 else 'drawing', color=hex_color(obj.stroke_color),
                        stroke_width=_r(obj.stroke_width))
            if len(points) == 2:
                base['points'] = points
            else:
                base['point_count'] = len(points)
            if getattr(obj, 'opacity', 1.0) < 1:
                base['opacity'] = _r(obj.opacity)
            for name in ('arrow_start', 'arrow_end'):
                if getattr(obj, name, False):
                    base[name] = True
            if getattr(obj, 'dash', 'solid') != 'solid':
                base['dash'] = obj.dash
        return base

    # ------------------------------------------------------------ tools
    def get_document_info(self):
        doc = self.doc
        if not doc:
            return {'open': False, 'note': 'No document is open. new_document creates one.'}
        pages = [{'page': i + 1, 'width': _r(p.rect.width), 'height': _r(p.rect.height), 'rotation': p.rotation}
                 for i, p in enumerate(doc) if i < 200]
        session = self.window._active_session
        return {'open': True, 'name': getattr(session, 'suggested_name', None) or self.window.current_file_path,
                'page_count': doc.page_count, 'current_page': self.window.current_page_index + 1,
                'editable': bool(getattr(session, 'can_edit', True)), 'pages': pages}

    def list_elements(self, page=None):
        index = self.page_index(page)
        self.goto(index)
        page_obj = self.doc[index]
        from ..layering import draw_order
        elements = [self.describe(obj) for obj in draw_order(self.objects())]
        for field in self.fields():
            x0, y0, x1, y1 = field['rect']
            item = {'id': f'f{field["xref"]}', 'type': 'field', 'kind': FIELD_KINDS.get(field['type'], 'other'),
                    'name': field['name'], 'x': _r(x0), 'y': _r(y0), 'width': _r(x1 - x0), 'height': _r(y1 - y0)}
            if field.get('value') not in (None, ''):
                item['value'] = field['value']
            elements.append(item)
        return {'page': index + 1, 'width': _r(page_obj.rect.width), 'height': _r(page_obj.rect.height),
                'rotation': page_obj.rotation, 'elements': elements,
                'note': 'Elements are listed bottom to top (later ones are drawn over earlier ones). '
                        'Annotations and some vector art are not listed; use render_page to see the page.'}

    def _grid(self, page, step, scale=1.0, label='pt'):
        """Draw a labelled measuring grid on a scratch page; scale converts page units to labels."""
        width, height = page.rect.width, page.rect.height
        shape = page.new_shape()
        unit = step / scale
        x = 0.0
        while x <= width:
            shape.draw_line((x, 0), (x, height))
            x += unit
        y = 0.0
        while y <= height:
            shape.draw_line((0, y), (width, y))
            y += unit
        shape.finish(color=(0.9, 0.1, 0.1), width=max(0.3, width / 1500), stroke_opacity=0.35)
        shape.commit()
        size = max(5, min(width, height) / 90)
        x = unit
        while x < width:
            page.insert_text((x + 1, size + 1), f'{round(x * scale)}', fontsize=size, color=(0.85, 0, 0))
            x += unit
        y = unit
        while y < height:
            page.insert_text((1, y - 1), f'{round(y * scale)}', fontsize=size, color=(0.85, 0, 0))
            y += unit

    def _png(self, page, clip=None):
        area = fitz.Rect(clip) if clip else page.rect
        zoom = min(4.0, RENDER_LONG_SIDE / max(area.width, area.height, 1))
        return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=area, alpha=False).tobytes('png')

    def _region(self, region, bounds, scale=1.0):
        if region is None:
            return None
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ToolError('"region" must be [x0, y0, x1, y1].')
        rect = fitz.Rect(*[_num(v, 'region') / scale for v in region]) & bounds
        if rect.is_empty:
            raise ToolError('"region" is outside the page or image.')
        return rect

    def render_page(self, page=None, grid=False, grid_step=50, region=None):
        index = self.page_index(page)
        self.goto(index)
        scratch = fitz.open()
        scratch.insert_pdf(self.doc, from_page=index, to_page=index)
        target = scratch[0]
        target.set_rotation(0)
        if grid:
            self._grid(target, max(5, _num(grid_step, 'grid_step')))
        clip = self._region(region, target.rect)
        png = self._png(target, clip)
        scratch.close()
        note = f'Page {index + 1} rendered' + (f' with a {grid_step} pt grid' if grid else '') + '.'
        return {'result': note, 'images': [png]}

    def attachment(self, number):
        items = self.attachments()
        index = int(_num(number, 'attachment')) - 1
        if not 0 <= index < len(items):
            raise ToolError(f'There is no attachment {index + 1}; {len(items)} are available.')
        return items[index]

    def _attachment_page(self, item):
        source = fitz.open(stream=item['bytes'], filetype=item['type'])
        pdf = fitz.open('pdf', source.convert_to_pdf())
        source.close()
        page = pdf[0]
        return pdf, page, item['width'] / page.rect.width

    def view_attachment(self, attachment, region=None, grid=False, grid_step=100):
        item = self.attachment(attachment)
        pdf, page, scale = self._attachment_page(item)
        if grid:
            self._grid(page, max(5, _num(grid_step, 'grid_step')), scale)
        clip = self._region(region, page.rect, scale)
        png = self._png(page, clip)
        pdf.close()
        return {'result': f'Attachment {attachment} ({item["width"]}x{item["height"]} px)'
                          + (f', region {region}' if region else '') + '.', 'images': [png]}

    def list_fonts(self, query=''):
        families = list(utils.FONT_FAMILY_LIST_SORTED or [])
        if query:
            families = [f for f in families if query.lower() in f.lower()]
        return {'fonts': families[:200], 'total': len(families)}

    def measure_text(self, text, size, font='Liberation Sans', bold=False, italic=False):
        family, note = match_font(font)
        measured = _font(family, bool(bold), bool(italic))
        size = _num(size, 'size')
        result = {'font': family, 'widths': [_r(measured.text_length(line, fontsize=size)) for line in str(text).split('\n')],
                  'ascender': _r(measured.ascender * size), 'descender': _r(measured.descender * size),
                  'line_height': _r(size * 1.2)}
        if note:
            result['note'] = note
        return result

    def go_to_page(self, page):
        index = self.page_index(page)
        self.goto(index)
        return {'current_page': index + 1}

    def new_document(self, width=None, height=None, size=None, landscape=False, pages=1):
        width, height = self._page_size(width, height, size, landscape, (595.0, 842.0))
        doc, error = pdf_handler.create_new_pdf(width=width, height=height, num_pages=max(1, int(_num(pages, 'pages'))))
        if error:
            raise ToolError(error)
        self.window.open_generated_document(doc)
        if self.window.view_mode:
            self.window._toggle_view_edit_mode()
        return {'page_count': doc.page_count, 'width': _r(width), 'height': _r(height),
                'note': 'A new untitled document is open and active.'}

    def _page_size(self, width, height, size, landscape, default):
        if size:
            from ..ops.pages import paper_size
            try:
                return paper_size(str(size).lower(), bool(landscape))
            except Exception as error:
                raise ToolError(str(error)) from None
        if width is None and height is None:
            return default
        if width is None or height is None:
            raise ToolError('Give both width and height, or a paper size.')
        width, height = _num(width, 'width'), _num(height, 'height')
        if not (36 <= width <= 14400 and 36 <= height <= 14400):
            raise ToolError('Page sides must be between 36 and 14400 points.')
        return width, height

    def add_page(self, position=None, width=None, height=None, size=None, landscape=False):
        doc = self.editable()
        current = doc[self.window.current_page_index].rect
        width, height = self._page_size(width, height, size, landscape, (current.width, current.height))
        at = doc.page_count if position is None else int(_num(position, 'position')) - 1
        if not 0 <= at <= doc.page_count:
            raise ToolError(f'"position" must be between 1 and {doc.page_count + 1}.')
        success, message = self.window._change_pages(
            lambda: pdf_handler.insert_blank_page(doc, at, width, height),
            lambda page: page + 1 if page >= at else page)
        if not success:
            raise ToolError(message)
        self.window._load_thumbnails()
        self.window._load_page(at)
        return {'page': at + 1, 'width': _r(width), 'height': _r(height), 'page_count': doc.page_count}

    def delete_pages(self, pages):
        doc = self.editable()
        indexes = sorted({self.page_index(p) for p in pages})
        if len(indexes) >= doc.page_count:
            raise ToolError('A document must keep at least one page.')
        from ..ops.pages import delete_remap
        def operation():
            for index in reversed(indexes):
                ok, message = pdf_handler.delete_page(doc, index)
                if not ok:
                    return False, message
            return True, 'Pages deleted.'
        success, message = self.window._change_pages(operation, delete_remap(indexes))
        if not success:
            raise ToolError(message)
        self.window._load_thumbnails()
        return {'deleted': [i + 1 for i in indexes], 'page_count': doc.page_count}

    # ------------------------------------------------------------ element changes
    def _rebuild(self, index):
        w = self.window
        success, error = pdf_handler.rebuild_page(w.doc, index, w.editable_texts, w.editable_shapes,
                                                  w.editable_images, all_strokes=w.editable_strokes)
        if not success:
            raise ToolError(error)

    def _new_text(self, spec, index, notes):
        return new_text_object(spec, index, notes)

    @staticmethod
    def _dash(value):
        if value not in ('solid', 'dashed', 'dotted'):
            raise ToolError('"dash" must be solid, dashed or dotted')
        return value

    def _shape_options(self, shape, spec, target=None):
        """Copy shape options from spec onto a shape (or into a property dict)."""
        target = shape.__dict__ if target is None else target
        if target is shape.__dict__:
            from ..shape_tools import SHAPE_DEFAULTS
            for name, value in SHAPE_DEFAULTS.items():
                target.setdefault(name, value)
        if 'corner_radius' in spec:
            target['corner_radius'] = max(0.0, _num(spec['corner_radius'], 'corner_radius'))
        if 'opacity' in spec:
            target['opacity'] = max(0.0, min(1.0, _num(spec['opacity'], 'opacity')))
        if 'dash' in spec:
            target['dash'] = self._dash(spec['dash'])
        if 'sides' in spec:
            target['sides'] = max(3, min(24, int(_num(spec['sides'], 'sides'))))
        if 'star_points' in spec:
            target['star_points'] = max(3, min(24, int(_num(spec['star_points'], 'star_points'))))
        if 'star_inner' in spec:
            target['star_inner'] = max(0.1, min(0.95, _num(spec['star_inner'], 'star_inner')))

    def _box(self, spec):
        x, y = _num(spec.get('x'), 'x'), _num(spec.get('y'), 'y')
        width, height = _num(spec.get('width'), 'width'), _num(spec.get('height'), 'height')
        if width <= 0 or height <= 0:
            raise ToolError('"width" and "height" must be positive.')
        return (x, y, x + width, y + height)

    def _image_bytes(self, spec):
        content = spec.get('content')
        if content not in IMAGE_CONTENT:
            raise ToolError('images need "content" set to one of ' + ', '.join(IMAGE_CONTENT) + '. ' + NOT_AN_IMAGE)
        item = self.attachment(spec.get('attachment'))
        crop = spec.get('crop')
        if not crop:
            if content != 'photo':
                raise ToolError('give a "crop" around the ' + content + ' instead of placing the whole picture. '
                                + NOT_AN_IMAGE)
            return item['bytes']
        pdf, page, scale = self._attachment_page(item)
        clip = self._region(crop, page.rect, scale)
        # Keep the original pixel density of the cropped area.
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=clip, alpha=False)
        pdf.close()
        aspect = max(pix.width, pix.height) / max(1, min(pix.width, pix.height))
        if aspect > 8 and content != 'barcode':
            raise ToolError(f'the crop is a {pix.width}x{pix.height} px strip, which looks like a text line, '
                            f'table row or band, not a {content}. ' + NOT_AN_IMAGE)
        words = self._ocr_words(pix)
        if words is not None and len(words) >= 4 and content not in ('stamp', 'qr_code', 'barcode'):
            raise ToolError(f'the crop contains text ({" ".join(words[:8])}…). ' + NOT_AN_IMAGE)
        return pix.tobytes('png')

    @staticmethod
    def _ocr_words(pix):
        """Words Tesseract reads in a pixmap, or None when OCR is not installed."""
        from ..ops import ocr
        directory = ocr.tessdata_dir()
        languages = ocr.languages()
        if not directory or not languages:
            return None
        try:
            language = 'eng' if 'eng' in languages else languages[0]
            with fitz.open('pdf', pix.pdfocr_tobytes(language=language, tessdata=directory)) as scanned:
                text = scanned[0].get_text()
        except Exception:
            return None
        return [word for word in text.split() if sum(c.isalnum() for c in word) >= 2]

    def add_elements(self, elements, page=None):
        doc = self.editable()
        index = self.page_index(page)
        self.goto(index)
        if not isinstance(elements, list) or not elements:
            raise ToolError('"elements" must be a non-empty list.')
        created, fields, notes, layers = [], [], [], {}
        page_rect = doc[index].rect
        for number, spec in enumerate(elements, 1):
            if not isinstance(spec, dict):
                raise ToolError(f'Element {number} must be an object.')
            kind = spec.get('type')
            try:
                if kind == 'text':
                    created.append(self._new_text(spec, index, notes))
                elif kind in SHAPES:
                    fill, stroke = parse_color(spec.get('fill'), 'fill'), parse_color(spec.get('stroke'), 'stroke')
                    if fill is None and stroke is None:
                        raise ToolError('give a fill or a stroke color')
                    width = _num(spec.get('stroke_width', 1 if stroke else 0), 'stroke_width')
                    shape = EditableShape(kind, self._box(spec), fill_color=fill or (1, 1, 1),
                                          stroke_color=stroke or fill, stroke_width=width if stroke else 0,
                                          page_number=index, is_new=True, is_transparent=fill is None,
                                          rotation=float(spec.get('rotation', 0)))
                    self._shape_options(shape, spec)
                    created.append(shape)
                elif kind == 'line':
                    points = spec.get('points')
                    if not points:
                        points = [[spec.get('x1'), spec.get('y1')], [spec.get('x2'), spec.get('y2')]]
                    points = [(_num(px, 'points'), _num(py, 'points')) for px, py in points]
                    if len(points) < 2:
                        raise ToolError('a line needs at least two points')
                    line = EditableStroke(points, stroke_color=parse_color(spec.get('color', '#000000')),
                                          stroke_width=_num(spec.get('stroke_width', 1), 'stroke_width'),
                                          opacity=_num(spec.get('opacity', 1), 'opacity'), page_number=index, is_new=True,
                                          tool_type='line' if len(points) == 2 else 'pen')
                    line.arrow_start, line.arrow_end = bool(spec.get('arrow_start')), bool(spec.get('arrow_end'))
                    line.dash = self._dash(spec.get('dash', 'solid'))
                    line.recalculate_bbox()
                    created.append(line)
                elif kind == 'image':
                    image = EditableImage(self._box(spec), index, None, self._image_bytes(spec), is_new=True,
                                          rotation=float(spec.get('rotation', 0)))
                    created.append(image)
                elif kind == 'field':
                    fields.append(spec)
                else:
                    raise ToolError(f'unknown type "{kind}"')
                if kind != 'field':
                    layer = spec.get('layer')
                    if layer is None and kind in ('rectangle', 'ellipse') and spec.get('fill') is not None:
                        layer = 'back'  # Filled shapes are backgrounds unless asked otherwise.
                    if layer not in (None, 'back', 'front'):
                        raise ToolError('"layer" must be back or front')
                    if layer:
                        layers[id(created[-1])] = layer
            except ToolError as error:
                raise ToolError(f'Element {number}: {error}') from None
        for obj in created:
            if not fitz.Rect(obj.bbox).intersects(page_rect):
                notes.append(f'{self.ident(obj)} lies outside the page.')

        def mutation():
            w = self.window
            for obj in created:
                target = {EditableText: w.editable_texts, EditableShape: w.editable_shapes,
                          EditableImage: w.editable_images, EditableStroke: w.editable_strokes}[type(obj)]
                target.append(obj)
                obj.is_baked = True
            from ..layering import set_layer
            for layer in ('back', 'front'):
                group = [obj for obj in created if layers.get(id(obj)) == layer]
                if group:
                    set_layer(w, group, layer == 'front', index)
            self._rebuild(index)
        if created:
            self.mutate(mutation, index)
        field_ids = []
        for spec in fields:
            field_ids.append(self._add_field(spec, index))
        result = {'page': index + 1, 'added': [self.ident(obj) for obj in created] + field_ids}
        if notes:
            result['notes'] = notes
        return result

    def _add_field(self, spec, index):
        from .. import document_tools as tools, form_builder
        kind = spec.get('kind', 'text')
        if kind not in ('text', 'checkbox', 'combo', 'list', 'radio', 'button', 'signature'):
            raise ToolError(f'Unknown field kind "{kind}".')
        name = str(spec.get('name') or '').strip()
        if not name:
            raise ToolError('Form fields need a unique "name".')
        rect = self._box(spec)
        fmt = spec.get('format')
        if fmt not in (None, 'date', 'email', 'phone', 'number'):
            raise ToolError('"format" must be date, email, phone or number.')
        if kind == 'button':
            preset = 'submit' if spec.get('action', 'reset') == 'submit' else 'reset'
            if preset == 'submit' and not re.match(r'^(https?://|mailto:)', str(spec.get('url', ''))):
                raise ToolError('A submit button needs "url" (https://, http:// or mailto:).')
            options = form_builder.field_options(preset, {'caption': spec.get('label'), 'url': spec.get('url', ''),
                                                          'format': spec.get('submit_format', 'html'),
                                                          'button_color': parse_color(spec.get('fill_color') or '#149473')})
        else:
            preset = 'multiline' if spec.get('multiline') else (fmt or 'text')
            options = form_builder.field_options(preset if kind == 'text' else kind, {})
        # Explicit styling wins over the form style.
        for key in ('fill_color', 'border_color', 'text_color'):
            if key in spec and not (kind == 'button' and key == 'fill_color'):
                options[key] = parse_color(spec[key], key)
        for key in ('font_size', 'border_width'):
            if key in spec:
                options[key] = max(0.0, _num(spec[key], key))
        if kind == 'checkbox':
            options.pop('font', None)
            options.pop('font_size', None)
        behaviour = form_builder.behaviour(fmt) if kind == 'text' and fmt in ('date', 'number') else None
        created = []
        def mutation():
            xref = tools.create_form_field(self.doc, index, name, kind, rect, spec.get('choices') or (),
                                           bool(spec.get('required')), options=options,
                                           value=spec.get('value') if kind != 'checkbox' else bool(spec.get('value')))
            created.append(xref)
            if behaviour:
                from ..ops import formbehaviour
                formbehaviour.apply(self.doc, xref, behaviour)
            if kind == 'text' and fmt in ('email', 'phone'):
                form_builder.add_scripts(self.doc, xref, fmt)
        try:
            self.mutate(mutation, index)
        except ValueError as error:
            raise ToolError(str(error)) from None
        return f'f{created[0]}' if created and isinstance(created[0], int) else name

    def edit_elements(self, edits, page=None):
        self.editable()
        index = self.page_index(page)
        self.goto(index)
        if not isinstance(edits, list) or not edits:
            raise ToolError('"edits" must be a non-empty list.')
        targets = self.find([str(edit.get('id')) for edit in edits])
        notes, field_edits, plans = [], [], []
        for edit, (ident, target) in zip(edits, targets):
            if isinstance(target, dict):
                field_edits.append((edit, target))
                continue
            old = copy.deepcopy(target.__dict__)
            new = copy.deepcopy(old)
            try:
                self._plan_edit(target, new, edit, notes)
            except ToolError as error:
                raise ToolError(f'{ident}: {error}') from None
            plans.append((target, old, new))

        restack = [(target, edit['layer']) for edit, (_ident, target) in zip(edits, targets)
                   if not isinstance(target, dict) and edit.get('layer') is not None]
        for _target, layer in restack:
            if layer not in ('back', 'front'):
                raise ToolError('"layer" must be back or front.')

        def mutation():
            for target, old, new in plans:
                _perform_ghost_erasure(self.window, target, index, old)
                target.__dict__.update(new)
                target._ghost_redacted = True
                target.is_baked = True
                target.original_bbox = target.bbox
                target.modified = False
            from ..layering import set_layer
            for layer in ('back', 'front'):
                group = [target for target, wanted in restack if wanted == layer]
                if group:
                    set_layer(self.window, group, layer == 'front', index)
            self._rebuild(index)
        if plans:
            self.mutate(mutation, index)
        for edit, field in field_edits:
            self._edit_field(edit, field, index)
        result = {'page': index + 1, 'edited': [ident for ident, _ in targets]}
        if notes:
            result['notes'] = notes
        return result

    def _plan_edit(self, target, new, edit, notes):
        scratch = copy.copy(target)
        scratch.__dict__ = new
        x0, y0, x1, y1 = new['bbox']
        nx = _num(edit['x'], 'x') if 'x' in edit else x0
        ny = _num(edit['y'], 'y') if 'y' in edit else y0
        if isinstance(target, EditableText):
            if 'font' in edit:
                new['font_family_base'], note = match_font(edit['font'])
                if note:
                    notes.append(note)
            for key, attr in (('bold', 'is_bold'), ('italic', 'is_italic'), ('underline', 'is_underline'),
                              ('strikethrough', 'is_strikethrough')):
                if key in edit:
                    new[attr] = bool(edit[key])
            if 'size' in edit:
                new['font_size'] = _num(edit['size'], 'size')
            if 'color' in edit:
                new['color'] = parse_color(edit['color'])
            if 'align' in edit:
                if edit['align'] not in ('left', 'center', 'right', 'justify'):
                    raise ToolError('"align" must be left, center, right or justify.')
                new['alignment'] = edit['align']
            if 'rotation' in edit:
                new['rotation'] = float(edit['rotation']) % 360
            box_width = _num(edit['width'], 'width') if 'width' in edit else None
            if 'text' in edit:
                new['text'] = str(edit['text'])
            font, natural, lines = text_metrics(scratch)
            if box_width is not None and edit.get('wrap', True) and 'text' in edit:
                new['text'] = wrap_text(new['text'], font, new['font_size'], box_width)
                font, natural, lines = text_metrics(scratch)
            ascent = new['baseline'] - y0 if 'size' not in edit and 'font' not in edit else font.ascender * new['font_size']
            width = max(natural, box_width or (natural if 'text' in edit or 'size' in edit or 'font' in edit else x1 - x0))
            height = ascent + (lines - 1) * new['font_size'] * 1.2 - font.descender * new['font_size']
            new['x'] = nx + (new['x'] - x0)
            new['y'] = ny
            new['baseline'] = ny + ascent
            new['bbox'] = (nx, ny, nx + width, ny + height)
            return
        width = _num(edit['width'], 'width') if 'width' in edit else x1 - x0
        height = _num(edit['height'], 'height') if 'height' in edit else y1 - y0
        if width <= 0 or height <= 0:
            raise ToolError('"width" and "height" must be positive.')
        if 'rotation' in edit:
            new['rotation'] = float(edit['rotation']) % 360
        if isinstance(target, EditableStroke):
            if 'points' in edit:
                new['points'] = [(_num(px, 'points'), _num(py, 'points')) for px, py in edit['points']]
            else:
                sx, sy = width / max(x1 - x0, 1e-6), height / max(y1 - y0, 1e-6)
                new['points'] = [(nx + (px - x0) * sx, ny + (py - y0) * sy) for px, py in new['points']]
            if 'color' in edit:
                new['stroke_color'] = parse_color(edit['color'])
            if 'stroke_width' in edit:
                new['stroke_width'] = _num(edit['stroke_width'], 'stroke_width')
            if 'opacity' in edit:
                new['opacity'] = _num(edit['opacity'], 'opacity')
            for name in ('arrow_start', 'arrow_end'):
                if name in edit:
                    new[name] = bool(edit[name])
            if 'dash' in edit:
                new['dash'] = self._dash(edit['dash'])
            scratch.recalculate_bbox()
            return
        new['bbox'] = (nx, ny, nx + width, ny + height)
        if isinstance(target, EditableShape):
            if 'fill' in edit:
                fill = parse_color(edit['fill'], 'fill')
                new['is_transparent'] = fill is None
                if fill is not None:
                    new['fill_color'] = fill
            if 'stroke' in edit:
                stroke = parse_color(edit['stroke'], 'stroke')
                if stroke is None:
                    new['stroke_width'] = 0
                    stroke = new['fill_color']
                new['stroke_color'] = stroke
            if 'stroke_width' in edit:
                new['stroke_width'] = _num(edit['stroke_width'], 'stroke_width')
            self._shape_options(target, edit, new)

    def _edit_field(self, edit, field, index):
        from .. import document_tools as tools, document_features as features
        x0, y0, x1, y1 = field['rect']
        if any(key in edit for key in ('x', 'y', 'width', 'height', 'name')):
            nx = _num(edit.get('x', x0), 'x')
            ny = _num(edit.get('y', y0), 'y')
            rect = (nx, ny, nx + _num(edit.get('width', x1 - x0), 'width'), ny + _num(edit.get('height', y1 - y0), 'height'))
            name = str(edit.get('name', field['name']))
            try:
                self.mutate(lambda: tools.edit_form_field(self.doc, index, field['xref'], name, field['required'],
                                                          field['max_length'], rect=rect), index)
            except ValueError as error:
                raise ToolError(str(error)) from None
        if 'value' in edit:
            self.mutate(lambda: features.update_form_fields(self.doc, {(index, field['xref']): edit['value']}),
                        index, allow_form=True)

    def delete_elements(self, ids, page=None):
        self.editable()
        index = self.page_index(page)
        self.goto(index)
        if not isinstance(ids, list) or not ids:
            raise ToolError('"ids" must be a non-empty list.')
        targets = self.find([str(i) for i in ids])
        objects = [t for _, t in targets if not isinstance(t, dict)]
        fields = [t for _, t in targets if isinstance(t, dict)]

        def mutation():
            w = self.window
            for obj in objects:
                for group in (w.editable_texts, w.editable_shapes, w.editable_images, w.editable_strokes):
                    if obj in group:
                        group.remove(obj)
                _perform_ghost_erasure(w, obj, index)
            self._rebuild(index)
        if objects:
            self.mutate(mutation, index)
        from .. import document_tools as tools
        for field in fields:
            try:
                self.mutate(lambda field=field: tools.delete_form_field(self.doc, index, field['xref']), index)
            except ValueError as error:
                raise ToolError(str(error)) from None
        return {'page': index + 1, 'deleted': [ident for ident, _ in targets]}

    # ------------------------------------------------------------ dispatch
    def run(self, name, arguments):
        """Run one tool call; returns (text for the model, list of PNG images)."""
        handler = getattr(self, name, None) if name in TOOL_NAMES else None
        if handler is None:
            return json.dumps({'error': f'Unknown tool {name}.'}), []
        try:
            result = handler(**arguments)
        except ToolError as error:
            return json.dumps({'error': str(error)}), []
        except TypeError as error:
            return json.dumps({'error': f'Bad arguments for {name}: {error}'}), []
        except Exception as error:  # Report unexpected failures to the model instead of crashing the UI.
            return json.dumps({'error': f'{type(error).__name__}: {error}'}), []
        images = result.pop('images', []) if isinstance(result, dict) else []
        return json.dumps(result, ensure_ascii=False), images


def _schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required)}


_PAGE = {'type': 'integer', 'description': 'Page number starting at 1. Defaults to the page shown in the editor.'}
_REGION = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4,
           'description': '[x0, y0, x1, y1] area to zoom into.'}
_COLOR = {'type': ['string', 'null'], 'description': 'Hex color such as #1f2937; null for none.'}
_ELEMENT = {
    'type': 'object',
    'description': ('One element. Common: type, x, y (top-left corner). '
                    'text: text, size, font, bold, italic, underline, strikethrough, color, align, width (wraps and '
                    'aligns inside this width), rotation. Use \\n for line breaks; lines are 1.2 x size apart. '
                    'Shapes (rectangle, ellipse, polygon, right_triangle, star, arrow = block arrow pointing right, '
                    'callout = speech bubble, checkmark, cross): width, height, fill, stroke (null = no outline), '
                    'stroke_width, opacity, dash, rotation; corner_radius for rectangle/callout, sides for polygon, '
                    'star_points/star_inner for star. '
                    'line: points [[x,y],...] or x1,y1,x2,y2, color, stroke_width, opacity, dash, arrow_start, arrow_end. '
                    'image: only for logos, photos, signatures, stamps, QR codes, barcodes or illustrations - '
                    'never for text, tables or colored bands. attachment (number), content (what the image is), '
                    'crop [x0,y0,x1,y1] in attachment pixels tightly around it, width, height. '
                    'field: kind (text|checkbox|combo|list|radio|button|signature), name (unique snake_case), width, '
                    'height, choices (combo/list/radio), required, value, format (date|email|phone|number), multiline, '
                    'fill_color, border_color, text_color, font_size, border_width; buttons: action (submit|reset), '
                    'label, url, submit_format, fill_color. Fields use the form style unless colors are given.'),
    'properties': {
        'type': {'type': 'string', 'enum': ['text', *SHAPES, 'line', 'image', 'field']},
        'x': {'type': 'number'}, 'y': {'type': 'number'}, 'width': {'type': 'number'}, 'height': {'type': 'number'},
        'text': {'type': 'string'}, 'size': {'type': 'number'}, 'font': {'type': 'string'},
        'bold': {'type': 'boolean'}, 'italic': {'type': 'boolean'}, 'underline': {'type': 'boolean'},
        'strikethrough': {'type': 'boolean'}, 'color': _COLOR,
        'align': {'type': 'string', 'enum': ['left', 'center', 'right', 'justify']},
        'fill': _COLOR, 'stroke': _COLOR, 'stroke_width': {'type': 'number'}, 'rotation': {'type': 'number'},
        'points': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'number'}}},
        'x1': {'type': 'number'}, 'y1': {'type': 'number'}, 'x2': {'type': 'number'}, 'y2': {'type': 'number'},
        'opacity': {'type': 'number', 'description': '0 to 1.'}, 'attachment': {'type': 'integer'}, 'crop': _REGION,
        'corner_radius': {'type': 'number', 'description': 'Rounded corners in points (rectangle, callout).'},
        'dash': {'type': 'string', 'enum': ['solid', 'dashed', 'dotted']},
        'sides': {'type': 'integer', 'description': 'polygon: 3 = triangle, 4 = diamond, 5, 6, 8...'},
        'star_points': {'type': 'integer'}, 'star_inner': {'type': 'number', 'description': 'star inner radius 0.1-0.95'},
        'arrow_start': {'type': 'boolean'}, 'arrow_end': {'type': 'boolean'},
        'content': {'type': 'string', 'enum': list(IMAGE_CONTENT)},
        'kind': {'type': 'string'}, 'name': {'type': 'string'},
        'format': {'type': 'string', 'enum': ['date', 'email', 'phone', 'number'],
                   'description': 'Text field input format and validation.'},
        'multiline': {'type': 'boolean'}, 'fill_color': _COLOR, 'border_color': _COLOR, 'text_color': _COLOR,
        'font_size': {'type': 'number', 'description': 'Field text size; 0 = automatic.'},
        'border_width': {'type': 'number'},
        'action': {'type': 'string', 'enum': ['submit', 'reset'], 'description': 'For button fields.'},
        'url': {'type': 'string', 'description': 'Submit address for submit buttons.'},
        'submit_format': {'type': 'string', 'enum': ['html', 'fdf', 'xfdf', 'pdf']},
        'label': {'type': 'string', 'description': 'Button caption.'},
        'layer': {'type': 'string', 'enum': ['back', 'front'],
                  'description': 'Stacking: back puts it behind everything on the page, front above. '
                                 'Filled rectangles and ellipses default to back so text stays visible on them.'},
        'choices': {'type': 'array', 'items': {'type': 'string'}}, 'required': {'type': 'boolean'},
    },
    'required': ['type'],
}
_EDIT = copy.deepcopy(_ELEMENT)
_EDIT['description'] = ('Changes for one element; only the given properties change. id is required. '
                        'x/y move the element (top-left), width/height resize it. For text, setting text reflows it; '
                        'width with text wraps it. For fields: x, y, width, height, name, value.')
_EDIT['properties'].pop('type')
_EDIT['properties'].pop('content')
_EDIT['properties'].update(id={'type': 'string'}, value={'type': ['string', 'boolean']},
                           wrap={'type': 'boolean'})
_EDIT['required'] = ['id']

TOOLS = [
    ('get_document_info', 'Page count, page sizes in points, the current page, and whether editing is allowed.', _schema({})),
    ('list_elements', 'List the editable elements on a page with ids, positions (points), text and styles.',
     _schema({'page': _PAGE})),
    ('render_page', 'See a page as an image. Use grid to measure positions in points; use region to zoom in.',
     _schema({'page': _PAGE, 'grid': {'type': 'boolean'}, 'grid_step': {'type': 'number'}, 'region': _REGION})),
    ('view_attachment', 'See an attached image again, optionally zoomed to a region (pixels) or with a pixel grid.',
     _schema({'attachment': {'type': 'integer'}, 'region': _REGION, 'grid': {'type': 'boolean'},
              'grid_step': {'type': 'number'}}, ['attachment'])),
    ('add_elements', 'Add text, shapes, lines, images (from attachments) and form fields to a page in one step.',
     _schema({'page': _PAGE, 'elements': {'type': 'array', 'items': _ELEMENT}}, ['elements'])),
    ('edit_elements', 'Change one or many elements on a page at once (text, position, size, style, colors).',
     _schema({'page': _PAGE, 'edits': {'type': 'array', 'items': _EDIT}}, ['edits'])),
    ('delete_elements', 'Delete elements from a page by id.',
     _schema({'page': _PAGE, 'ids': {'type': 'array', 'items': {'type': 'string'}}}, ['ids'])),
    ('add_page', 'Insert a blank page. Size from width/height in points or a paper size (a4, letter, legal, a3, a5...); '
                 'defaults to the current page size. position is the new page number (default: at the end).',
     _schema({'position': {'type': 'integer'}, 'width': {'type': 'number'}, 'height': {'type': 'number'},
              'size': {'type': 'string'}, 'landscape': {'type': 'boolean'}})),
    ('delete_pages', 'Delete pages by number.', _schema({'pages': {'type': 'array', 'items': {'type': 'integer'}}}, ['pages'])),
    ('go_to_page', 'Show a page in the editor.', _schema({'page': _PAGE}, ['page'])),
    ('new_document', 'Open a new blank document in a new tab (default A4 portrait).',
     _schema({'width': {'type': 'number'}, 'height': {'type': 'number'}, 'size': {'type': 'string'},
              'landscape': {'type': 'boolean'}, 'pages': {'type': 'integer'}})),
    ('list_fonts', 'Installed font families usable for text, optionally filtered.', _schema({'query': {'type': 'string'}})),
    ('measure_text', 'Measure text width, ascender and descender in points for a font and size.',
     _schema({'text': {'type': 'string'}, 'size': {'type': 'number'}, 'font': {'type': 'string'},
              'bold': {'type': 'boolean'}, 'italic': {'type': 'boolean'}}, ['text', 'size'])),
]
TOOL_NAMES = {name for name, _description, _parameters in TOOLS}


def tool_definitions():
    return [{'type': 'function', 'function': {'name': name, 'description': description, 'parameters': parameters}}
            for name, description, parameters in TOOLS]
