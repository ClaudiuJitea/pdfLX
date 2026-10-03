"""Shape primitives dialog and the on-page node editor for pen drawings."""
import copy

import pymupdf as fitz
from gi.repository import Gdk

from ..i18n import _
from ..dialogs import OperationDialog, render_preview, toast
from ..ops import drawing as ops
from ..ops.common import OperationError


def draw_shape(c):
    w = c.window
    doc = w.doc
    number = w.current_page_index
    selection = c.selection_rect()
    dialog = OperationDialog(w, _('menu_draw_shape'), _('btn_add'), width=600,
                             preview=lambda: _preview(c, number, area(), options()))
    group = dialog.group(_('shape_kind'), _('markup_hint_selection') if selection else _('rich_text_hint'))
    kind = dialog.combo(group, _('shape_kind'), [(k, _(f'shape_{k}')) for k in ops.PRIMITIVES], 'star')
    points = dialog.spin(group, _('shape_points'), 5, 3, 64)
    inner = dialog.spin(group, _('shape_inner'), 45, 5, 95, 5)
    start = dialog.spin(group, _('shape_start_angle'), 0, -360, 360, 5)
    sweep = dialog.spin(group, _('shape_sweep'), 90, 1, 360, 5)
    style = dialog.group(_('markup_style'))
    stroke = dialog.color(style, _('markup_stroke'), (0.1, 0.2, 0.6))
    use_fill = dialog.switch(style, _('markup_fill_enable'), True)
    fill = dialog.color(style, _('markup_fill'), (0.55, 0.7, 0.95))
    width = dialog.spin(style, _('markup_line_width'), 1.5, 0, 12, 0.25, 2)
    opacity = dialog.spin(style, _('tool_opacity'), 100, 5, 100, 5)
    page = doc[number]
    visible = page.rect
    area_group = dialog.group(_('markup_area'))
    coords = [dialog.spin(area_group, label, value, 0, limit)
              for label, value, limit in (('X (pt)', 100, visible.width), ('Y (pt)', 100, visible.height),
                                          (_('tool_width'), 120, visible.width), (_('tool_height'), 120, visible.height))]
    area_group.set_visible(selection is None)

    def update(*_args):
        key = kind.key
        points.set_visible(key in ('star', 'polygon'))
        inner.set_visible(key in ('star', 'rounded_rectangle'))
        start.set_visible(key in ('sector', 'arc'))
        sweep.set_visible(key in ('sector', 'arc'))
        dialog.refresh_preview()
    for widget, signal in ((kind, 'notify::selected'), (points, 'notify::value'), (inner, 'notify::value'),
                           (start, 'notify::value'), (sweep, 'notify::value')):
        widget.connect(signal, update)
    update()

    def area():
        if selection:
            return fitz.Rect(selection)
        x, y, wd, ht = (spin.get_value() for spin in coords)
        return fitz.Rect(x, y, x + wd, y + ht) * doc[number].derotation_matrix

    def options():
        return dict(kind=kind.key, stroke=stroke.rgb() if width.get_value() > 0 else None,
                    fill=fill.rgb() if use_fill.get_active() else None, width=width.get_value(),
                    opacity=opacity.get_value() / 100, points=points.get_value_as_int(),
                    inner=inner.get_value() / 100, start=start.get_value(), sweep=sweep.get_value())

    def apply(_dialog):
        rect = area()
        values = options()
        c.flush_edits()
        c.mutate(lambda: ops.draw_primitive(doc, number, rect=rect, **values), rebase_pages=(number,), page_num=number)
        w._refresh_thumbnail(number)
        toast(w, _('status_shape_added'))
    dialog.on_apply = apply
    dialog.show()


def _preview(c, number, rect, values):
    with fitz.open('pdf', c.snapshot_bytes()) as scratch:
        ops.draw_primitive(scratch, number, rect=rect, **values)
        return render_preview(scratch, number)


# ---------------------------------------------------------------- node editing

class NodeTool:
    """Drag nodes of a pen drawing. Ctrl+click inserts a node, Shift+click deletes one."""

    def __init__(self, window, stroke):
        self.window = window
        self.stroke = stroke
        self.before = copy.deepcopy(stroke.__dict__)
        self.index = None
        self.anchor = None

    def _point(self, visual_x, visual_y):
        return self.window._visual_to_unrotated_page_coords(visual_x, visual_y)

    def begin(self, page_x, page_y, state):
        point = self._point(page_x, page_y)
        tolerance = 7 / max(0.1, self.window.zoom_level)
        points = self.stroke.points
        index = ops.nearest_node(points, point, tolerance)
        if state & Gdk.ModifierType.SHIFT_MASK and index is not None and len(points) > 2:
            self._commit(points[:index] + points[index + 1:])
            return
        if state & Gdk.ModifierType.CONTROL_MASK:
            segment, distance, projection = ops.nearest_segment(points, point)
            if segment is not None and distance <= tolerance * 2:
                self._commit(points[:segment + 1] + [projection] + points[segment + 1:])
            return
        self.index = index
        self.anchor = (page_x, page_y)
        self.before = copy.deepcopy(self.stroke.__dict__)

    def update(self, dx, dy):
        if self.index is None:
            return
        x, y = self._point(self.anchor[0] + dx, self.anchor[1] + dy)
        self.stroke.points[self.index] = (x, y)
        self.window.pdf_view.queue_draw()

    def end(self, dx, dy):
        if self.index is None:
            return
        self.update(dx, dy)
        moved = list(self.stroke.points)
        self.stroke.__dict__.update(copy.deepcopy(self.before))
        self.index = None
        if moved != self.stroke.points:
            self._commit(moved)

    def _commit(self, points):
        from ..undo_manager import EditObjectCommand
        before = copy.deepcopy(self.stroke.__dict__)
        self.stroke.points = [tuple(p) for p in points]
        self.stroke.recalculate_bbox()
        after = copy.deepcopy(self.stroke.__dict__)
        self.stroke.__dict__.update(copy.deepcopy(before))
        command = EditObjectCommand(self.window, self.stroke, before, after)
        try:
            command.execute()
        except Exception as error:
            self.stroke.__dict__.update(before)
            toast(self.window, str(error))
            return
        self.window.undo_manager.add_command(command)
        self.window._update_ui_state()
        self.window.pdf_view.queue_draw()

    def draw(self, cr):
        if self.window.tool_mode != 'nodes':
            return
        zoom = max(0.1, self.window.zoom_level)
        points = self.stroke.points
        cr.save()
        cr.set_source_rgba(0.1, 0.45, 0.9, 0.6)
        cr.set_line_width(1 / zoom)
        for index, (x, y) in enumerate(points):
            if index == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()
        radius = 3.5 / zoom
        for index, (x, y) in enumerate(points):
            cr.rectangle(x - radius, y - radius, 2 * radius, 2 * radius)
            cr.set_source_rgba(1, 1, 1, 1)
            cr.fill_preserve()
            cr.set_source_rgba(0.1, 0.45, 0.9, 1 if index == self.index else 0.85)
            cr.stroke()
        cr.restore()


def node_editor(c):
    w = c.window
    stroke = w.selected_stroke
    if stroke is None:
        raise OperationError(_('nodes_need_stroke'))
    if abs(getattr(stroke, 'rotation', 0) or 0) > 0.01:
        raise OperationError(_('nodes_rotated'))
    dialog = OperationDialog(w, _('menu_node_editor'), _('nodes_edit_on_page'), width=520)
    group = dialog.group(_('nodes_title'), _('nodes_hint'))
    count = dialog.info(group, _('nodes_count'), str(len(stroke.points)))
    tolerance = dialog.spin(group, _('nodes_simplify_tolerance'), 1.0, 0.1, 20, 0.1, 1)
    iterations = dialog.spin(group, _('nodes_smooth_iterations'), 1, 1, 4)

    def commit(points):
        tool = NodeTool(w, stroke)
        tool._commit(points)
        count.set_subtitle(str(len(stroke.points)))

    from gi.repository import Gtk
    buttons = Gtk.Box(spacing=6, margin_top=6)
    simplify = Gtk.Button(label=_('nodes_simplify'))
    simplify.connect('clicked', lambda _b: commit(ops.simplify(stroke.points, tolerance.get_value())))
    smooth = Gtk.Button(label=_('nodes_smooth'))
    smooth.connect('clicked', lambda _b: commit(ops.smooth(stroke.points, iterations.get_value_as_int())))
    buttons.append(simplify)
    buttons.append(smooth)
    group.add(buttons)

    def apply(_dialog):
        w.node_tool = NodeTool(w, stroke)
        w.on_tool_selected(None, 'nodes')
        w.status_label.set_text(_('nodes_status'))
    dialog.on_apply = apply
    dialog.show()
