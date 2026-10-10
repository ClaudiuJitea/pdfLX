"""Select, move, and proportionally resize native stamps with a drag preview.

Sticky notes use the same interaction for moving only, also in view mode.
"""
import pymupdf as fitz
import math
from . import document_tools as tools, pdf_handler
from .stamp_rotation import rotate_stamp, tilt_info, stamp_dimensions


class StampInteraction:
    def __init__(self, window):
        self.window = window
        self.selected = None
        self.drag = None
        self.preview_doc = None

    def editable(self):
        window = self.window
        return bool(window.doc and not window.view_mode and window._active_session.can_edit)

    def movable(self, note):
        window = self.window
        if not (window.doc and window._active_session.can_edit and note):
            return False
        return note['kind'] == 'Text' or (note['kind'] == 'Stamp' and not window.view_mode)

    def hit_note(self, x, y):
        """Movable stamp or sticky note under a visual page point."""
        window = self.window
        native = fitz.Point(x, y) * window.doc[window.current_page_index].derotation_matrix
        note = tools.note_at_point(window.doc, window.current_page_index, native, include_stamps=True)
        return note if self.movable(note) else None

    def hit(self, x, y):
        window = self.window
        native = fitz.Point(x, y) * window.doc[window.current_page_index].derotation_matrix
        note = tools.note_at_point(window.doc, window.current_page_index, native, include_stamps=True)
        return note if note and note['kind'] == 'Stamp' else None

    def select(self, note):
        self.selected = note
        window = self.window
        window.selected_text = window.selected_image = window.selected_shape = window.selected_stroke = None
        window.selected_table = None
        window.pdf_view.queue_draw()

    def handle_at(self, x, y):
        if not self.selected or not self.editable() or self.selected['kind'] != 'Stamp':
            return None
        note = self.selected
        if note['page'] != self.window.current_page_index:
            return None
        rect = fitz.Rect(note['rect']) * self.window.doc[note['page']].rotation_matrix
        tolerance = 7 / self.window.zoom_level
        rotate = fitz.Point((rect.x0 + rect.x1) / 2, rect.y0 - 24 / self.window.zoom_level)
        if abs(x - rotate.x) <= tolerance and abs(y - rotate.y) <= tolerance:
            return 'rotate'
        for name, point in (('nw', rect.tl), ('ne', rect.tr), ('se', rect.br), ('sw', rect.bl)):
            if abs(x - point.x) <= tolerance and abs(y - point.y) <= tolerance:
                return name
        return None

    def begin(self, x, y):
        handle = self.handle_at(x, y)
        note = self.selected if handle else self.hit_note(x, y)
        if not self.movable(note):
            return False
        self.select(note)
        window = self.window
        bounds = fitz.Rect(note['rect']) * window.doc[note['page']].rotation_matrix
        self.drag = dict(source=window.doc, note=note, original=bounds, target=bounds, handle=handle,
                         start=fitz.Point(x,y),
                         angle=tilt_info(window.doc,note['xref']) if note['kind'] == 'Stamp' else 0)
        return True

    def update(self, dx, dy):
        if not self.drag:
            return
        window = self.window
        state = self.drag
        if (not self.movable(state['note']) or window.doc is not state['source']
                or window.current_page_index != state['note']['page']):
            self.cancel()
            return
        bounds = state['original']
        page = window.doc[window.current_page_index].rect
        if state['handle']=='rotate':
            center = (bounds.tl + bounds.br) / 2
            start = state['start'] - center
            current = start + fitz.Point(dx,dy)
            delta = math.degrees(math.atan2(current.y,current.x)-math.atan2(start.y,start.x))
            state['new_angle'] = (state['angle'] + delta) % 360
            if self.preview_doc is None:
                self.preview_doc = fitz.open(stream=window.doc.tobytes(), filetype='pdf')
            size = stamp_dimensions(state['source'],state['note']['page'],state['note']['xref'])
            rotate_stamp(self.preview_doc, state['note']['page'], state['note']['xref'], state['new_angle'],size=size,center=center)
            preview_page = self.preview_doc[state['note']['page']]
            state['target'] = preview_page.load_annot(state['note']['xref']).rect * preview_page.rotation_matrix
            pdf_handler.invalidate_page_cache(self.preview_doc, state['note']['page'])
            window.pdf_view.queue_draw()
            return
        if state['handle']:
            handle = state['handle']
            sx = -1 if 'w' in handle else 1
            sy = -1 if 'n' in handle else 1
            anchor = dict(nw=bounds.br, ne=bounds.bl, se=bounds.tl, sw=bounds.tr)[handle]
            w, h = bounds.width, bounds.height
            scale = 1 + (sx * dx * w + sy * dy * h) / (w * w + h * h)
            max_width = page.width - anchor.x if sx > 0 else anchor.x
            max_height = page.height - anchor.y if sy > 0 else anchor.y
            maximum = min(max_width / w, max_height / h)
            scale = min(maximum, max(scale, 24 / w, 12 / h))
            corner = fitz.Point(max(0, min(page.width, anchor.x + sx * w * scale)),
                                max(0, min(page.height, anchor.y + sy * h * scale)))
            target = fitz.Rect(min(anchor.x, corner.x), min(anchor.y, corner.y),
                               max(anchor.x, corner.x), max(anchor.y, corner.y))
        else:
            x = max(0, min(bounds.x0 + dx, page.width - bounds.width))
            y = max(0, min(bounds.y0 + dy, page.height - bounds.height))
            target = fitz.Rect(x, y, x + bounds.width, y + bounds.height)
        state['target'] = target
        if target == bounds and self.preview_doc is None:
            return
        if self.preview_doc is None:
            self.preview_doc = fitz.open(stream=window.doc.tobytes(), filetype='pdf')
        transform = tools.resize_stamp if state['handle'] else tools.move_stamp
        transform(self.preview_doc, state['note']['page'], state['note']['xref'], target)
        pdf_handler.invalidate_page_cache(self.preview_doc, state['note']['page'])
        window.pdf_view.queue_draw()

    def end(self, dx, dy):
        if not self.drag:
            return
        self.update(dx, dy)
        if not self.drag:
            return
        state = self.drag
        self.cancel()
        unchanged=(abs((state.get('new_angle',state['angle'])-state['angle']+180)%360-180)<0.00001
                   if state['handle']=='rotate' else state['target']==state['original'])
        if unchanged:
            self.select(state['note'])
            if state['note']['kind'] == 'Text':
                # A click without movement opens the note.
                self.window.document_tools.open_note_bubble(state['note'])
            return
        note = state['note']
        if state['handle']=='rotate':
            mutation = lambda: rotate_stamp(state['source'], note['page'], note['xref'], state['new_angle'])
        else:
            transform = tools.resize_stamp if state['handle'] else tools.move_stamp
            mutation = lambda: transform(state['source'], note['page'], note['xref'], state['target'])
        success = self.window._mutate_document(
            mutation,
            page_num=note['page'], allow_view=note['kind'] == 'Text')
        if success:
            page = state['source'][note['page']]
            self.select(dict(note, rect=tuple(page.load_annot(note['xref']).rect)))

    def cancel(self):
        if self.preview_doc is not None:
            pdf_handler.invalidate_page_cache(self.preview_doc)
            self.preview_doc.close()
        self.preview_doc = None
        self.drag = None
        self.selected = None
        self.window.pdf_view.queue_draw()

    def draw(self, cr):
        if not self.selected or not self.movable(self.selected):
            return
        note = self.selected
        if note['page'] != self.window.current_page_index:
            return
        rect = fitz.Rect(note['rect'])
        if self.drag:
            rect = self.drag['target'] * self.window.doc[note['page']].derotation_matrix
        cr.save()
        cr.set_source_rgba(0.1, 0.45, 0.9, 0.9)
        cr.set_line_width(1.5 / self.window.zoom_level)
        cr.rectangle(rect.x0, rect.y0, rect.width, rect.height)
        cr.stroke()
        if note['kind'] != 'Stamp':
            # Notes keep a fixed icon size: no resize or rotate handles.
            cr.restore()
            return
        size = 8 / self.window.zoom_level
        for point in (rect.tl, rect.tr, rect.br, rect.bl):
            cr.rectangle(point.x - size / 2, point.y - size / 2, size, size)
            cr.set_source_rgb(1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(0.1, 0.45, 0.9, 0.9)
            cr.stroke()
        page = self.window.doc[note['page']]
        visual = rect * page.rotation_matrix
        top = fitz.Point((visual.x0+visual.x1)/2,visual.y0) * page.derotation_matrix
        rotate = fitz.Point((visual.x0+visual.x1)/2,visual.y0-24/self.window.zoom_level) * page.derotation_matrix
        cr.move_to(top.x,top.y)
        cr.line_to(rotate.x,rotate.y)
        cr.stroke()
        cr.arc(rotate.x,rotate.y,5/self.window.zoom_level,0,math.tau)
        cr.set_source_rgb(1,1,1)
        cr.fill_preserve()
        cr.set_source_rgba(0.1,0.45,0.9,0.9)
        cr.stroke()
        cr.restore()
