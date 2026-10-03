"""Viewer features: page layouts, presentation mode, measurement, and a side-by-side viewer."""
import math

import pymupdf as fitz
from gi.repository import Gdk, GLib, Gtk

from ..i18n import _, get_setting, set_setting
from ..dialogs import OperationDialog, toast
from ..ops.common import OperationError

LAYOUTS = ('page', 'continuous', 'spread', 'book')


# ---------------------------------------------------------------- layouts

def apply_layout(window, mode):
    if mode not in LAYOUTS or not window.doc:
        return
    if window.inline_editor_widget is not None:
        window._apply_and_hide_editor(force_apply=True)
    window._active_session.scroll_mode = mode
    window._page_scroll_delta = 0
    window.continuous_view.sync()
    window._update_ui_state()


# ---------------------------------------------------------------- presentation

def presentation(c):
    """Full-screen, one page at a time, fitted to the screen; Esc returns."""
    w = c.window
    session = w._active_session
    if getattr(w, '_document_only_view', False):
        w._exit_document_view()
        return
    state = {'mode': session.scroll_mode, 'zoom': w.zoom_level, 'fit': session.fit_to_view}
    apply_layout(w, 'page')
    w._presentation_state = state
    session.fit_to_view = True
    w._toggle_fullscreen()
    w._schedule_fit_document(session)
    w.pdf_view.set_cursor(Gdk.Cursor.new_from_name('none'))
    toast(w, _('presentation_hint'), timeout=3)

    def restore(*_args):
        if getattr(w, '_document_only_view', False):
            return GLib.SOURCE_CONTINUE
        saved = getattr(w, '_presentation_state', None)
        if saved is not None and w._active_session is session:
            w._presentation_state = None
            session.fit_to_view = saved['fit']
            w.pdf_view.set_cursor(None)
            apply_layout(w, saved['mode'])
            w._set_zoom(saved['zoom'])
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(400, restore)


# ---------------------------------------------------------------- measurement

UNITS = (('mm', 'mm', 25.4 / 72), ('cm', 'cm', 2.54 / 72), ('in', 'in', 1 / 72), ('pt', 'pt', 1.0))


class MeasureTool:
    """Drag to measure a distance; hold Ctrl while dragging to measure a rectangle."""

    def __init__(self, window):
        self.window = window
        self.start = self.end = None
        self.area = False
        self.last = None

    @staticmethod
    def factor():
        unit = get_setting('measure_unit', 'mm')
        per_point = dict((key, factor) for key, _label, factor in UNITS).get(unit, UNITS[0][2])
        return unit, per_point * float(get_setting('measure_scale', 1.0) or 1.0)

    def _point(self, visual_x, visual_y):
        x, y = self.window._visual_to_unrotated_page_coords(visual_x, visual_y)
        return fitz.Point(x, y)

    def begin(self, page_x, page_y, area=False):
        self.start = self.end = self._point(page_x, page_y)
        self.area = area
        self.anchor = (page_x, page_y)

    def update(self, dx, dy):
        if self.start is None:
            return
        self.end = self._point(self.anchor[0] + dx, self.anchor[1] + dy)
        self.window.pdf_view.queue_draw()

    def describe(self):
        unit, factor = self.factor()
        if self.area:
            rect = fitz.Rect(self.start, self.end).normalize()
            width, height = rect.width * factor, rect.height * factor
            return (f'{width:.2f} × {height:.2f} {unit} · {_("measure_area")} {width * height:.2f} {unit}² · '
                    f'{_("measure_perimeter")} {2 * (width + height):.2f} {unit}')
        length = math.dist(self.start, self.end) * factor
        angle = math.degrees(math.atan2(-(self.end.y - self.start.y), self.end.x - self.start.x))
        return f'{length:.2f} {unit} · {angle:.1f}°'

    def end_drag(self, dx, dy):
        if self.start is None:
            return
        self.update(dx, dy)
        if math.dist(self.start, self.end) < 0.5:
            self.start = self.end = None
            return
        self.last = (self.start, self.end, self.area, self.describe())
        text = self.last[3]
        self.window.status_label.set_text(text)
        editable = self.window.features.editable()
        toast(self.window, text, timeout=8, button_label=_('measure_keep') if editable else None,
              callback=self.keep if editable else None)

    def keep(self):
        """Store the last measurement as a native line or square annotation."""
        if not self.last:
            return
        start, end, area, text = self.last
        from ..ops import annotations
        w = self.window
        doc, number = w.doc, w.current_page_index

        def mutation():
            if area:
                annotations.add_shape(doc, number, 'rectangle', fitz.Rect(start, end).normalize(),
                                      stroke=(0.1, 0.45, 0.9), width=1, dashed=True, content=text)
            else:
                annotations.add_shape(doc, number, 'double_arrow', fitz.Rect(start, end).normalize(),
                                      stroke=(0.1, 0.45, 0.9), width=1, content=text, start=start, end=end)
        try:
            w.features.mutate(mutation, page_num=number)
        except OperationError as error:
            toast(w, str(error))

    def draw(self, cr):
        if self.start is None or self.window.tool_mode != 'measure':
            return
        cr.save()
        cr.set_source_rgba(0.1, 0.45, 0.9, 0.95)
        cr.set_line_width(1.5 / max(0.1, self.window.zoom_level))
        if self.area:
            rect = fitz.Rect(self.start, self.end).normalize()
            cr.set_dash([4 / self.window.zoom_level])
            cr.rectangle(rect.x0, rect.y0, rect.width, rect.height)
            cr.stroke()
        else:
            cr.move_to(self.start.x, self.start.y)
            cr.line_to(self.end.x, self.end.y)
            cr.stroke()
            for point in (self.start, self.end):
                cr.arc(point.x, point.y, 2.5 / self.window.zoom_level, 0, 2 * math.pi)
                cr.fill()
        cr.restore()


def start_measure(c):
    w = c.window
    if not hasattr(w, 'measure_tool'):
        w.measure_tool = MeasureTool(w)
    w.on_tool_selected(None, 'measure')
    unit, factor = MeasureTool.factor()
    w.status_label.set_text(_('measure_hint', f'1 pt = {factor:.4g} {unit}'))


def measure_settings(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_measure_settings'), _('btn_save'))
    group = dialog.group(_('measure_units'), _('measure_units_hint'))
    unit = dialog.combo(group, _('measure_unit'), [(key, label) for key, label, _f in UNITS],
                        get_setting('measure_unit', 'mm'))
    scale = dialog.spin(group, _('measure_scale'), float(get_setting('measure_scale', 1.0) or 1.0),
                        0.0001, 1000000, 1, 4, _('measure_scale_hint'))

    def apply(_dialog):
        set_setting('measure_unit', unit.key)
        set_setting('measure_scale', scale.get_value())
        toast(w, _('status_measure_saved'))
    dialog.on_apply = apply
    dialog.show()


# ---------------------------------------------------------------- side-by-side viewer

class SideViewer:
    """A read-only pane showing any open document (or another place in this one)."""

    def __init__(self, window):
        self.window = window
        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT, reveal_child=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=8, margin_end=8,
                      margin_top=8, margin_bottom=8)
        box.set_size_request(380, -1)
        header = Gtk.Box(spacing=6)
        title = Gtk.Label(label=_('side_viewer_title'), xalign=0, hexpand=True)
        title.add_css_class('heading')
        header.append(title)
        refresh = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text=_('side_viewer_refresh'))
        refresh.add_css_class('flat')
        refresh.connect('clicked', lambda _b: self.populate())
        header.append(refresh)
        close = Gtk.Button(icon_name='window-close-symbolic', tooltip_text=_('btn_close'))
        close.add_css_class('flat')
        close.connect('clicked', lambda _b: self.revealer.set_reveal_child(False))
        header.append(close)
        box.append(header)
        self.documents = Gtk.DropDown.new_from_strings([])
        self.documents.connect('notify::selected', lambda *_a: self.populate())
        box.append(self.documents)
        self.scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.pages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.scroll.set_child(self.pages)
        box.append(self.scroll)
        self.revealer.set_child(box)
        self.sessions = []
        self.scroll.get_vadjustment().connect('value-changed', lambda *_a: self.render_visible())

    def toggle(self):
        showing = not self.revealer.get_reveal_child()
        self.revealer.set_reveal_child(showing)
        if showing:
            self.refresh_documents()

    def refresh_documents(self):
        self.sessions = [s for s in self.window.sessions if s.doc is not None]
        labels = [s.title for s in self.sessions]
        current = self.window._active_session
        self.documents.set_model(Gtk.StringList.new(labels))
        if current in self.sessions:
            self.documents.set_selected(self.sessions.index(current))
        self.populate()

    def populate(self):
        while child := self.pages.get_first_child():
            self.pages.remove(child)
        self.pictures = []
        index = self.documents.get_selected()
        if not 0 <= index < len(self.sessions):
            return
        doc = self.sessions[index].doc
        self.doc = doc
        width = 360
        for page in doc:
            height = int(width * page.rect.height / max(1, page.rect.width))
            picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
            picture.set_size_request(width, height)
            picture.add_css_class('card')
            label = Gtk.Label(label=_('form_page_label', page.number + 1))
            label.add_css_class('dim-label')
            label.add_css_class('caption')
            self.pages.append(picture)
            self.pages.append(label)
            self.pictures.append((picture, False))
        GLib.idle_add(lambda: (self.render_visible(), False)[1])

    def render_visible(self):
        doc = getattr(self, 'doc', None)
        if doc is None or doc.is_closed:
            return
        adjustment = self.scroll.get_vadjustment()
        top, bottom = adjustment.get_value() - 400, adjustment.get_value() + adjustment.get_page_size() + 400
        for number, (picture, done) in enumerate(self.pictures):
            if done:
                continue
            allocation = picture.compute_bounds(self.pages)
            y = allocation[1].get_y() if allocation[0] else number * 500
            if top <= y <= bottom or number < 2:
                page = doc[number]
                scale = 360 / max(1, page.rect.width) * 1.5
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                from .. import pdf_handler
                pdf_handler.apply_reader_mode(pix)
                picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(pix.tobytes('png'))))
                self.pictures[number] = (picture, True)


def side_viewer(c):
    w = c.window
    if not hasattr(w, 'side_viewer'):
        w.side_viewer = SideViewer(w)
        w.editor_container.append(w.side_viewer.revealer)
    w.side_viewer.toggle()
