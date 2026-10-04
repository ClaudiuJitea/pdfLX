"""Extra shapes, shape options (corner radius, opacity, line style, sides, star points)
and the straight Line / Arrow tools."""
import copy
import math

from gi.repository import Gtk

from .i18n import _
from .models import EditableShape, EditableStroke
from .shape_geometry import PRESETS, DASHES

SHAPE_DEFAULTS = {'corner_radius': 0.0, 'opacity': 1.0, 'dash': 'solid', 'sides': 6, 'star_points': 5,
                  'star_inner': 0.5}
LINE_DEFAULTS = {'arrow_start': False, 'arrow_end': False, 'dash': 'solid'}
# Which options apply to which shape kinds.
APPLIES = {'corner_radius': ('rectangle', 'callout'), 'sides': ('polygon',), 'star_points': ('star',),
           'star_inner': ('star',)}


def option(shape, name):
    return getattr(shape, name, SHAPE_DEFAULTS.get(name, LINE_DEFAULTS.get(name)))


class ShapeTools:
    def __init__(self, window):
        self.window = window
        self.preset = 'rectangle'
        self.shape_defaults = dict(SHAPE_DEFAULTS)
        self.line_defaults = dict(LINE_DEFAULTS)
        self._syncing = False

        self.options_button = Gtk.MenuButton(icon_name='editor-shape-options-symbolic',
                                             tooltip_text=_("shape_options"))
        self.options_button.add_css_class('flat')
        grid = self._grid()
        self.radius = self._spin(grid, 0, _("shape_corner_radius"), 0, 200, 1, 0, 'pt')
        self.opacity = self._spin(grid, 1, _("shape_opacity"), 0, 100, 5, 100, '%')
        self.dash = self._dash(grid, 2)
        self.sides = self._spin(grid, 3, _("shape_sides"), 3, 12, 1, 6)
        self.points = self._spin(grid, 4, _("shape_star_points"), 3, 24, 1, 5)
        self.inner = self._spin(grid, 5, _("shape_star_depth"), 10, 95, 5, 50, '%')
        for widget in (self.radius, self.opacity, self.sides, self.points, self.inner):
            widget.connect('value-changed', self.on_shape_option)
        self.dash.connect('notify::selected', self.on_shape_option)
        self.options_button.set_popover(Gtk.Popover(child=grid))
        window.shape_toolbar_box.append(self.options_button)

        self.line_button = Gtk.MenuButton(icon_name='editor-arrow-line-symbolic', tooltip_text=_("line_options"))
        self.line_button.add_css_class('flat')
        grid = self._grid()
        self.arrow_start = Gtk.CheckButton(label=_("line_arrow_start"))
        self.arrow_end = Gtk.CheckButton(label=_("line_arrow_end"))
        grid.attach(self.arrow_start, 0, 0, 2, 1)
        grid.attach(self.arrow_end, 0, 1, 2, 1)
        self.line_dash = self._dash(grid, 2)
        self.arrow_start.connect('toggled', self.on_line_option)
        self.arrow_end.connect('toggled', self.on_line_option)
        self.line_dash.connect('notify::selected', self.on_line_option)
        self.line_button.set_popover(Gtk.Popover(child=grid))
        window.stroke_toolbar_box.append(self.line_button)
        # An outline width of 0 means "no outline".
        window.shape_stroke_width_spin.set_range(0, 20)

    # ------------------------------------------------------------ widgets
    @staticmethod
    def _grid():
        return Gtk.Grid(column_spacing=12, row_spacing=8, margin_top=10, margin_bottom=10,
                        margin_start=12, margin_end=12)

    @staticmethod
    def _spin(grid, row, label, low, high, step, value, unit=''):
        grid.attach(Gtk.Label(label=label, xalign=0, hexpand=True), 0, row, 1, 1)
        spin = Gtk.SpinButton.new_with_range(low, high, step)
        spin.set_value(value)
        box = Gtk.Box(spacing=4)
        box.append(spin)
        if unit:
            suffix = Gtk.Label(label=unit)
            suffix.add_css_class('dim-label')
            box.append(suffix)
        grid.attach(box, 1, row, 1, 1)
        spin.row_widgets = (grid.get_child_at(0, row), box)
        return spin

    @staticmethod
    def _dash(grid, row):
        grid.attach(Gtk.Label(label=_("shape_line_style"), xalign=0, hexpand=True), 0, row, 1, 1)
        dropdown = Gtk.DropDown.new_from_strings([_("line_solid"), _("line_dashed"), _("line_dotted")])
        grid.attach(dropdown, 1, row, 1, 1)
        return dropdown

    def populate_menu(self, grid, popover):
        """Shape picker: presets in a grid, then the Line and Arrow tools."""
        while child := grid.get_first_child():
            grid.remove(child)
        entries = [(key, label, icon) for key, label, icon, _extra in PRESETS]
        entries += [('line', 'tool_line', 'editor-line-symbolic'), ('arrow_line', 'tool_arrow_line', 'editor-arrow-line-symbolic')]
        w = self.window
        # The classic tool buttons stay in the picker; they already select their tool and close it.
        classic = {'rectangle': w.add_rectangle_tool_button, 'ellipse': w.add_ellipse_tool_button,
                   'checkmark': w.checkmark_tool_button, 'cross': w.cross_tool_button}
        self.menu_buttons = {}
        for index, (key, label, icon) in enumerate(entries):
            button = classic.get(key)
            if button is None:
                button = Gtk.Button(icon_name=icon, tooltip_text=_(label))
                button.add_css_class('flat')
                button.set_size_request(36, 36)
                button.connect('clicked', lambda _b, key=key: (popover.popdown(), self.choose(key)))
            grid.attach(button, index % 4, index // 4, 1, 1)
            self.menu_buttons[key] = button

    def choose(self, key):
        legacy = {'rectangle': 'add_rectangle', 'ellipse': 'add_ellipse', 'checkmark': 'add_checkmark', 'cross': 'add_cross'}
        if key in legacy:
            self.window.on_tool_selected(None, legacy[key])
        elif key in ('line', 'arrow_line'):
            self.preset = key
            self.window.on_tool_selected(None, 'add_line')
        else:
            self.preset = key
            self.window.on_tool_selected(None, 'add_shape')

    def tool_icon(self):
        return next((icon for key, _label, icon, _extra in PRESETS if key == self.preset), 'editor-shapes-symbolic')

    # ------------------------------------------------------------ shapes
    def new_shape(self, bbox):
        w = self.window
        extra = next((dict(extra) for key, _label, _icon, extra in PRESETS if key == self.preset), {})
        kind = extra.pop('shape_type', self.preset)
        shape = EditableShape(kind, bbox, fill_color=w.next_shape_fill, stroke_color=w.next_shape_stroke,
                              stroke_width=w.next_shape_stroke_width, page_number=w.current_page_index,
                              is_new=True, is_transparent=w.next_shape_transparent)
        for name, value in self.shape_defaults.items():
            setattr(shape, name, value)
        for name, value in extra.items():
            setattr(shape, name, value)
        return shape

    def apply_defaults(self, shape):
        """Give shapes from the classic tools the current options too."""
        for name, value in self.shape_defaults.items():
            if not hasattr(shape, name):
                setattr(shape, name, value)

    def _read_shape(self):
        return {'corner_radius': self.radius.get_value(), 'opacity': self.opacity.get_value() / 100,
                'dash': DASHES[self.dash.get_selected()], 'sides': int(self.sides.get_value()),
                'star_points': int(self.points.get_value()), 'star_inner': self.inner.get_value() / 100}

    def sync_shape(self, shape):
        """Show a shape's options (or the defaults for the next shape)."""
        self._syncing = True
        try:
            source = shape if shape is not None else None
            get = (lambda name: option(source, name)) if source else self.shape_defaults.get
            self.radius.set_value(get('corner_radius'))
            self.opacity.set_value(round(get('opacity') * 100))
            self.dash.set_selected(DASHES.index(get('dash')) if get('dash') in DASHES else 0)
            self.sides.set_value(get('sides'))
            self.points.set_value(get('star_points'))
            self.inner.set_value(round(get('star_inner') * 100))
            kind = shape.shape_type if shape is not None else self._preset_kind()
            for name, widget in (('corner_radius', self.radius), ('sides', self.sides),
                                 ('star_points', self.points), ('star_inner', self.inner)):
                visible = kind in APPLIES[name]
                for part in widget.row_widgets:
                    part.set_visible(visible)
        finally:
            self._syncing = False

    def _preset_kind(self):
        if self.window.tool_mode == 'add_rectangle':
            return 'rectangle'
        if self.window.tool_mode != 'add_shape':
            return None
        extra = next((extra for key, _label, _icon, extra in PRESETS if key == self.preset), {})
        return extra.get('shape_type', self.preset)

    def on_shape_option(self, *_args):
        if self._syncing:
            return
        values = self._read_shape()
        shape = self.window.selected_shape
        if shape is None:
            self.shape_defaults.update(values)
            return
        kind = shape.shape_type
        changes = {name: value for name, value in values.items()
                   if (name not in APPLIES or kind in APPLIES[name]) and option(shape, name) != value}
        if changes:
            self._edit(shape, changes)

    def _edit(self, obj, changes):
        from .undo_manager import EditObjectCommand
        w = self.window
        w.commit_pending_format_change()
        old = copy.deepcopy(obj.__dict__)
        new = copy.deepcopy(old)
        new.update(changes)
        command = EditObjectCommand(w, obj, old, new)
        command.execute()
        w.undo_manager.add_command(command)
        w.pdf_view.queue_draw()

    # ------------------------------------------------------------ lines
    def sync_line(self, stroke):
        self._syncing = True
        try:
            get = (lambda name: option(stroke, name)) if stroke is not None else self.line_defaults.get
            if stroke is None and self.window.tool_mode == 'add_line':
                self.line_defaults['arrow_end'] = self.preset == 'arrow_line'
            self.arrow_start.set_active(bool(get('arrow_start')))
            self.arrow_end.set_active(bool(get('arrow_end')))
            self.line_dash.set_selected(DASHES.index(get('dash')) if get('dash') in DASHES else 0)
        finally:
            self._syncing = False

    def on_line_option(self, *_args):
        if self._syncing:
            return
        values = {'arrow_start': self.arrow_start.get_active(), 'arrow_end': self.arrow_end.get_active(),
                  'dash': DASHES[self.line_dash.get_selected()]}
        stroke = getattr(self.window, 'selected_stroke', None)
        if stroke is None:
            self.line_defaults.update(values)
            return
        changes = {name: value for name, value in values.items() if option(stroke, name) != value}
        if changes:
            self._edit(stroke, changes)

    def new_line(self, x, y):
        w = self.window
        line = EditableStroke(points=[(x, y), (x, y)], stroke_color=w.pen_color, stroke_width=w.pen_width,
                              tool_type='line', page_number=w.current_page_index, is_new=True)
        for name, value in self.line_defaults.items():
            setattr(line, name, value)
        if self.preset == 'arrow_line':
            line.arrow_end = True
        return line

    @staticmethod
    def line_end(start, end, snap):
        """Line end point; with snap (Shift) the angle locks to 15 degree steps."""
        if not snap:
            return end
        dx, dy = end[0] - start[0], end[1] - start[1]
        length = math.hypot(dx, dy)
        angle = round(math.atan2(dy, dx) / (math.pi / 12)) * (math.pi / 12)
        return (start[0] + length * math.cos(angle), start[1] + length * math.sin(angle))
