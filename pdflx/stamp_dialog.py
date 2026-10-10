"""Stamp designer: template gallery, live preview and saved custom stamps."""
import json
import os

import pymupdf as fitz
from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from . import document_tools as tools
from .dialogs import SheetDialog, alert
from .i18n import _, get_setting, set_setting
from .stamp_shapes import BORDERS, SHAPES

FAMILIES = ('DejaVu Sans', 'DejaVu Serif', 'DejaVu Sans Mono')
RED, GREEN, BLUE, ORANGE, PURPLE, GRAY, BLACK = ((0.8, 0.1, 0.12), (0.1, 0.52, 0.24), (0.13, 0.32, 0.72),
                                                 (0.86, 0.44, 0.0), (0.45, 0.2, 0.62), (0.4, 0.42, 0.46),
                                                 (0.1, 0.1, 0.12))
# Extra template colours (not in the swatch row; any colour can be picked).
TEAL, NAVY, BROWN = (0.0, 0.47, 0.47), (0.1, 0.2, 0.45), (0.5, 0.32, 0.14)
SWATCHES = (('red', RED), ('green', GREEN), ('blue', BLUE), ('orange', ORANGE),
            ('purple', PURPLE), ('gray', GRAY), ('black', BLACK))


def _template(text, color, shape='rectangle', border='Solid', details='', fill=False, italic=False):
    return dict(text=text, color=color, shape=shape, border=border, details=details, fill=fill,
                italic=italic, bold=True, font_family='DejaVu Sans', font_size=24, opacity=1, angle=0)


# (id used as the stamp name, style). Standard PDF stamp names come first.
TEMPLATES = (
    ('Approved', _template('APPROVED', GREEN, 'rounded', 'Double')),
    ('Not approved', _template('NOT APPROVED', RED, 'rectangle', 'Double')),
    ('Draft', _template('DRAFT', GRAY, 'rectangle', 'Dashed')),
    ('Confidential', _template('CONFIDENTIAL', RED, 'rectangle', 'Solid', fill=True)),
    ('Final', _template('FINAL', BLUE, 'rounded', 'Solid')),
    ('For comment', _template('FOR COMMENT', ORANGE, 'oval', 'Solid')),
    ('Reviewed', _template('REVIEWED', BLUE, 'seal', 'Double', details='{author}')),
    ('Received', _template('RECEIVED', PURPLE, 'rounded', 'Solid', details='{date}')),
    ('Paid', _template('PAID', GREEN, 'circle', 'Double', details='{date}')),
    ('Completed', _template('COMPLETED', GREEN, 'badge', 'Solid', fill=True)),
    ('Urgent', _template('URGENT', RED, 'badge', 'Solid', fill=True)),
    ('Void', _template('VOID', RED, 'rectangle', 'Double', italic=True)),
    ('Copy', _template('COPY', GRAY, 'rectangle', 'Dashed', italic=True)),
    ('Verified', _template('VERIFIED', TEAL, 'hexagon', 'Double', details='{date}')),
    ('Top priority', _template('TOP PRIORITY', RED, 'burst', 'Solid', fill=True)),
    ('Thank you', _template('THANK YOU', GREEN, 'burst', 'Solid', fill=True, italic=True)),
    ('On hold', _template('ON HOLD', ORANGE, 'tag', 'Solid', fill=True)),
    ('Archived', _template('ARCHIVED', BROWN, 'ticket', 'Solid', details='{date}')),
    ('Scanned', _template('SCANNED', GRAY, 'ticket', 'Dashed', details='{datetime}')),
    ('Original', _template('ORIGINAL', NAVY, 'seal', 'Double', details='{date}')),
)
FIELDS = (('{date}', 'stamp_field_date'), ('{time}', 'stamp_field_time'),
          ('{datetime}', 'stamp_field_datetime'), ('{author}', 'stamp_field_author'))


def render_stamp(stamp, style, width=160, author='', scale=2.0):
    """PNG bytes of a stamp rendered alone on a transparent background."""
    with fitz.open() as sample:
        sample.new_page(width=max(540, width + 40), height=540)
        xref = tools.place_stamp(sample, 0, (20, 20), stamp, min(width, 500), author, style)
        page = sample[0]  # keep the page alive while its annotation renders
        annot = page.load_annot(xref)
        pix = annot.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
        return pix.tobytes('png'), (pix.width / scale, pix.height / scale)


def _texture(png):
    return Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))


def _rgba(rgb):
    rgba = Gdk.RGBA()
    rgba.red, rgba.green, rgba.blue = rgb
    rgba.alpha = 1
    return rgba


def _shape_icon(shape):
    """Small grey outline of a stamp shape for the shape picker."""
    from . import stamp_shapes
    width, height = (30, 30) if shape in ('circle', 'seal') else (
        44, 26 if shape in ('oval', 'ribbon', 'badge', 'burst') else 20 if shape in ('hexagon', 'tag', 'ticket') else 18)
    with fitz.open() as sample:
        page = sample.new_page(width=width, height=height)
        stamp_shapes.draw_border(page, shape, width, height, (0.5, 0.5, 0.5), 'Solid')
        return page.get_pixmap(matrix=fitz.Matrix(0.8, 0.8), alpha=True).tobytes('png')


def _custom_templates():
    stored = get_setting('stamp_templates', []) or []
    return [item for item in stored if isinstance(item, dict) and isinstance(item.get('style'), dict)]


class StampDialog(SheetDialog):
    def __init__(self, controller, info=None):
        window = controller.window
        super().__init__(window, _("tool_edit_stamp") if info else _("tool_add_stamp"),
                         _("btn_apply") if info else _("stamp_place"), width=940, height=680)
        self.controller = controller
        self.info = info
        self.source = window.doc
        self.loading = True
        self.timer = None
        self.stamp = 'Approved'
        self.custom = None
        self.tiles = []
        initial, width, angle = self._initial()

        split = Gtk.Box()
        settings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24,
                           margin_top=18, margin_bottom=24, margin_start=20, margin_end=20)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=520,
                                    child=settings)
        split.append(scroll)
        split.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        split.append(self._build_preview())
        self.view.set_content(split)

        settings.append(self._build_gallery())
        settings.append(self._build_text(initial))
        settings.append(self._build_appearance(initial))
        settings.append(self._build_size(width, angle))
        settings.append(self._build_author())

        self.stamp = initial.get('stamp', 'Approved')
        self._select_tile(self.stamp, initial.get('template'))
        self.loading = False
        self.connect('closed', lambda *_args: self.timer and GLib.source_remove(self.timer))
        GLib.idle_add(self._render_gallery)
        self.refresh()

    # -- initial state ----------------------------------------------------------
    def _initial(self):
        info, source = self.info, self.source
        if not info:
            last = get_setting('stamp_last', None)
            if isinstance(last, dict) and isinstance(last.get('style'), dict):
                style = dict(TEMPLATES[0][1], **last['style'])
                style['stamp'] = last.get('stamp', 'Approved')
                style['template'] = last.get('template')
                return style, float(last.get('width', 170)), 0
            return dict(TEMPLATES[0][1], stamp='Approved'), 170, 0
        names = dict(TEMPLATES)
        initial = dict(names.get(info['content'], TEMPLATES[0][1]), stamp=info['content'] if info['content'] in names
                       else 'Approved', text=info['content'] or 'APPROVED')
        key, value = source.xref_get_key(info['xref'], 'PdfLXStampStyle')
        if key == 'string':
            try:
                initial.update(json.loads(value))
            except (ValueError, TypeError):
                pass
        self.visual = fitz.Rect(info['rect']) * source[info['page']].rotation_matrix
        from .stamp_rotation import tilt_info, stamp_dimensions
        width, _height = stamp_dimensions(source, info['page'], info['xref'])
        angle = tilt_info(source, info['xref'])
        if angle > 180:
            angle -= 360
        if 'font_size' in initial:
            key, value = source.xref_get_key(info['xref'], 'PdfLXStampTilt/Base')
            base = int(value.split()[0]) if key == 'xref' else int(source.xref_get_key(info['xref'], 'AP/N')[1].split()[0])
            bounds = fitz.Rect([float(part) for part in source.xref_get_key(base, 'BBox')[1].strip('[] ').split()])
            initial['font_size'] = max(6, min(96, initial['font_size'] * width / bounds.width))
        return initial, width, angle

    # -- sections ---------------------------------------------------------------
    def _group(self, title, description=None):
        group = Adw.PreferencesGroup(title=GLib.markup_escape_text(title))
        if description:
            group.set_description(GLib.markup_escape_text(description))
        return group

    def _build_gallery(self):
        group = self._group(_("stamp_templates"))
        actions = Gtk.Box(spacing=6)
        self.remove_button = Gtk.Button(icon_name='user-trash-symbolic', tooltip_text=_("stamp_remove_template"),
                                        visible=False, valign=Gtk.Align.CENTER)
        self.remove_button.add_css_class('flat')
        self.remove_button.connect('clicked', self._remove_template)
        save = Gtk.Button(label=_("stamp_save_template"), valign=Gtk.Align.CENTER)
        save.add_css_class('flat')
        save.connect('clicked', self._save_template)
        actions.append(self.remove_button)
        actions.append(save)
        group.set_header_suffix(actions)
        self.gallery = Gtk.FlowBox(homogeneous=True, min_children_per_line=4, max_children_per_line=4,
                                   selection_mode=Gtk.SelectionMode.SINGLE, column_spacing=8, row_spacing=8,
                                   activate_on_single_click=True)
        self.gallery.add_css_class('pdflx-stamp-gallery')
        self.gallery.connect('child-activated', self._tile_activated)
        for stamp, style in TEMPLATES:
            self._add_tile(stamp, style, _(f"stamp_tpl_{stamp.lower().replace(' ', '_')}"))
        for item in _custom_templates():
            self._add_tile(item.get('stamp', 'Approved'), item['style'], item.get('name', ''), item)
        group.add(self.gallery)
        return group

    def _add_tile(self, stamp, style, name, custom=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                      margin_start=6, margin_end=6)
        picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, height_request=40)
        box.append(picture)
        caption = Gtk.Label(label=name or stamp, ellipsize=Pango.EllipsizeMode.END, max_width_chars=12)
        caption.add_css_class('caption')
        box.append(caption)
        child = Gtk.FlowBoxChild(child=box)
        child.add_css_class('pdflx-stamp-tile')
        if custom is not None:
            child.add_css_class('custom')
        child.stamp, child.style, child.custom, child.picture = stamp, style, custom, picture
        self.gallery.append(child)
        self.tiles.append(child)
        return child

    def _render_gallery(self):
        for child in self.tiles:
            if child.picture.get_paintable() is None:
                try:
                    png, _size = render_stamp(child.stamp, child.style, 150, self._author(), 1.4)
                    child.picture.set_paintable(_texture(png))
                except (ValueError, RuntimeError):
                    pass
                return True  # one tile per idle cycle keeps the dialog responsive
        return False

    def _build_text(self, initial):
        group = self._group(_("props_text"), _("stamp_fields_hint"))
        self.text_row = Adw.EntryRow(title=_("tool_stamp_text"))
        self.text_row.set_text(initial.get('text', 'APPROVED'))
        group.add(self.text_row)
        self.text_row.get_delegate().set_max_length(tools.STAMP_TEXT_LIMIT)
        self.details_row = Adw.EntryRow(title=_("stamp_details"))
        self.details_row.get_delegate().set_max_length(tools.STAMP_DETAILS_LIMIT)
        self.details_row.set_text(initial.get('details', '') or '')
        insert = Gtk.MenuButton(icon_name='list-add-symbolic', tooltip_text=_("stamp_insert_field"),
                                valign=Gtk.Align.CENTER)
        insert.add_css_class('flat')
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                       margin_start=6, margin_end=6)
        popover = Gtk.Popover(child=menu)
        for token, key in FIELDS:
            button = Gtk.Button(label=_(key))
            button.add_css_class('flat')
            button.get_child().set_xalign(0)
            button.connect('clicked', lambda _b, token=token: (self._insert_field(token), popover.popdown()))
            menu.append(button)
        insert.set_popover(popover)
        self.details_row.add_suffix(insert)
        group.add(self.details_row)
        self.family = Adw.ComboRow(title=_("tool_stamp_font"), model=Gtk.StringList.new(list(FAMILIES)))
        family = initial.get('font_family', FAMILIES[0])
        self.family.set_selected(FAMILIES.index(family) if family in FAMILIES else 0)
        group.add(self.family)
        style_row = Adw.ActionRow(title=_("props_style"))
        styles = Gtk.Box(valign=Gtk.Align.CENTER)
        styles.add_css_class('linked')
        self.bold = Gtk.ToggleButton(icon_name='format-text-bold-symbolic', tooltip_text=_("tool_stamp_bold"),
                                     active=initial.get('bold', True))
        self.italic = Gtk.ToggleButton(icon_name='format-text-italic-symbolic', tooltip_text=_("element_italic"),
                                       active=initial.get('italic', False))
        styles.append(self.bold)
        styles.append(self.italic)
        style_row.add_suffix(styles)
        group.add(style_row)
        self.size = Adw.SpinRow.new_with_range(6, 96, 1)
        self.size.set_title(_("stamp_text_size"))
        self.size.set_subtitle(_("stamp_text_size_hint"))
        self.size.set_value(initial.get('font_size', 24))
        group.add(self.size)
        for widget, signal in ((self.text_row, 'changed'), (self.details_row, 'changed'),
                               (self.family, 'notify::selected'), (self.bold, 'toggled'),
                               (self.italic, 'toggled'), (self.size, 'notify::value')):
            widget.connect(signal, self._changed)
        return group

    def _build_appearance(self, initial):
        group = self._group(_("props_appearance"))
        color_row = Adw.ActionRow(title=_("tool_stamp_color"))
        swatches = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER)
        self.swatches = []
        first = None
        for name, rgb in SWATCHES:
            button = Gtk.ToggleButton(tooltip_text=_(f"stamp_color_{name}"), valign=Gtk.Align.CENTER)
            button.add_css_class('pdflx-swatch')
            button.add_css_class(f'swatch-{name}')
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.rgb = rgb
            button.connect('toggled', self._swatch_toggled)
            swatches.append(button)
            self.swatches.append(button)
        self.color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER,
                                           tooltip_text=_("stamp_color_custom"))
        self.color.set_rgba(_rgba(initial.get('color', RED)))
        self.color.connect('notify::rgba', self._color_changed)
        swatches.append(self.color)
        color_row.add_suffix(swatches)
        group.add(color_row)

        # The picker sits under its title so all shapes fit without squeezing it.
        shape_row = Adw.PreferencesRow(activatable=False, title=_("tool_stamp_shape"))
        shape_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                            margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
        shape_box.append(Gtk.Label(label=_("tool_stamp_shape"), xalign=0))
        shapes = Gtk.Box(halign=Gtk.Align.START)
        shapes.add_css_class('linked')
        self.shape_buttons = {}
        first = None
        for shape in SHAPES:
            button = Gtk.ToggleButton(tooltip_text=_(f'tool_stamp_shape_{shape}'))
            button.add_css_class('pdflx-shape-button')
            picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, width_request=26,
                                  height_request=18)
            picture.set_paintable(_texture(_shape_icon(shape)))
            button.set_child(picture)
            if first is None:
                first = button
            else:
                button.set_group(first)
            button.connect('toggled', self._changed)
            shapes.append(button)
            self.shape_buttons[shape] = button
        self.set_shape(initial.get('shape', 'rectangle'))
        shape_box.append(shapes)
        shape_row.set_child(shape_box)
        group.add(shape_row)

        self.border = Adw.ComboRow(title=_("tool_stamp_border"),
                                   model=Gtk.StringList.new([_(f'stamp_border_{key.lower()}') for key in BORDERS]))
        border = initial.get('border', 'Solid')
        self.border.set_selected(BORDERS.index(border) if border in BORDERS else 1)
        group.add(self.border)
        self.fill = Adw.SwitchRow(title=_("stamp_fill"), subtitle=_("stamp_fill_hint"),
                                  active=initial.get('fill', False))
        group.add(self.fill)
        opacity_row = Adw.ActionRow(title=_("image_opacity"))
        self.opacity = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 5, 100, 1)
        self.opacity.set_draw_value(False)
        self.opacity.set_size_request(160, -1)
        self.opacity.set_valign(Gtk.Align.CENTER)
        self.opacity.set_value(initial.get('opacity', 1) * 100)
        percent = Gtk.Label(width_chars=4, xalign=1)
        percent.add_css_class('numeric')
        self.opacity.connect('value-changed', lambda scale: percent.set_text(f'{scale.get_value():.0f}%'))
        percent.set_text(f'{self.opacity.get_value():.0f}%')
        opacity_row.add_suffix(self.opacity)
        opacity_row.add_suffix(percent)
        group.add(opacity_row)
        for widget, signal in ((self.border, 'notify::selected'), (self.fill, 'notify::active'),
                               (self.opacity, 'value-changed')):
            widget.connect(signal, self._changed)
        self._sync_swatches()
        return group

    def _build_size(self, width, angle):
        group = self._group(_("props_size"))
        self.width = Adw.SpinRow.new_with_range(24, 500, 2)
        self.width.set_title(_("stamp_width"))
        self.width.set_value(width)
        group.add(self.width)
        self.angle = Adw.SpinRow.new_with_range(-180, 180, 1)
        self.angle.set_title(_("stamp_tilt"))
        self.angle.set_value(angle)
        tilts = Gtk.Box(valign=Gtk.Align.CENTER, margin_end=6)
        tilts.add_css_class('linked')
        for value in (-15, 0, 15):
            button = Gtk.Button(label=f'{value:+d}°' if value else '0°')
            button.add_css_class('flat')
            button.connect('clicked', lambda _b, value=value: self.angle.set_value(value))
            tilts.append(button)
        self.angle.add_suffix(tilts)
        group.add(self.angle)
        for widget in (self.width, self.angle):
            widget.connect('notify::value', self._changed)
        return group

    def _build_author(self):
        group = self._group(_("tool_author"))
        self.author = Adw.EntryRow(title=_("stamp_author_name"))
        author = self.info['author'] if self.info else (get_setting('review_author', '') or
                                                         os.environ.get('USER', ''))
        self.author.set_text(author or '')
        self.author.connect('changed', self._changed)
        group.add(self.author)
        return group

    def _build_preview(self):
        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, hexpand=True)
        pane.add_css_class('pdflx-preview-pane')
        title = Gtk.Label(label=_("tool_preview"), xalign=0)
        title.add_css_class('pdflx-section-title')
        pane.append(title)
        paper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        paper.add_css_class('pdflx-stamp-paper')
        self.preview = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.SCALE_DOWN, vexpand=True,
                                   hexpand=True, margin_start=24, margin_end=24, margin_top=24, margin_bottom=24)
        paper.append(self.preview)
        pane.append(paper)
        self.dimensions = Gtk.Label(xalign=0)
        self.dimensions.add_css_class('caption')
        self.dimensions.add_css_class('numeric')
        pane.append(self.dimensions)
        hint = Gtk.Label(label=_("stamp_edit_hint") if self.info else _("stamp_place_hint"), xalign=0, wrap=True)
        hint.add_css_class('caption')
        hint.add_css_class('dim-label')
        pane.append(hint)
        return pane

    # -- state ------------------------------------------------------------------
    def set_shape(self, shape):
        button = self.shape_buttons.get(shape) or self.shape_buttons['rectangle']
        button.set_active(True)

    def _shape(self):
        return next((shape for shape, button in self.shape_buttons.items() if button.get_active()), 'rectangle')

    def _author(self):
        return self.author.get_text() if hasattr(self, 'author') else ''

    def style(self):
        rgba = self.color.get_rgba()
        return dict(text=self.text_row.get_text(), details=self.details_row.get_text(), shape=self._shape(),
                    font_family=FAMILIES[self.family.get_selected()], font_size=self.size.get_value(),
                    bold=self.bold.get_active(), italic=self.italic.get_active(),
                    color=(rgba.red, rgba.green, rgba.blue), border=BORDERS[self.border.get_selected()],
                    fill=self.fill.get_active(), opacity=self.opacity.get_value() / 100,
                    angle=self.angle.get_value())

    def apply_style(self, stamp, style, custom=None):
        self.loading = True
        self.stamp, self.custom = stamp, custom
        self.text_row.set_text(style.get('text', stamp.upper()))
        self.details_row.set_text(style.get('details', '') or '')
        family = style.get('font_family', FAMILIES[0])
        self.family.set_selected(FAMILIES.index(family) if family in FAMILIES else 0)
        self.bold.set_active(style.get('bold', True))
        self.italic.set_active(style.get('italic', False))
        self.size.set_value(style.get('font_size', 24))
        self.color.set_rgba(_rgba(style.get('color', RED)))
        self.set_shape(style.get('shape', 'rectangle'))
        border = style.get('border', 'Solid')
        self.border.set_selected(BORDERS.index(border) if border in BORDERS else 1)
        self.fill.set_active(style.get('fill', False))
        self.opacity.set_value(style.get('opacity', 1) * 100)
        if custom is not None:
            self.width.set_value(custom.get('width', self.width.get_value()))
            self.angle.set_value(style.get('angle', 0))
        self._sync_swatches()
        self.loading = False
        self.remove_button.set_visible(custom is not None)
        self.refresh()

    def _select_tile(self, stamp, custom_name=None):
        for child in self.tiles:
            if (custom_name and child.custom and child.custom.get('name') == custom_name) or \
                    (not custom_name and child.custom is None and child.stamp == stamp):
                self.gallery.select_child(child)
                self.custom = child.custom
                self.remove_button.set_visible(child.custom is not None)
                return

    def _tile_activated(self, _box, child):
        self.apply_style(child.stamp, child.style, child.custom)

    def _insert_field(self, token):
        position = self.details_row.get_position()
        text = self.details_row.get_text()
        if position < 0 or position > len(text):
            position = len(text)
        spacer = ' · ' if text and position == len(text) and not text.endswith(' ') else ''
        self.details_row.insert_text(spacer + token, position)
        self.details_row.set_position(position + len(spacer + token))

    def _swatch_toggled(self, button):
        if button.get_active() and not self.loading:
            self.color.set_rgba(_rgba(button.rgb))

    def _color_changed(self, *_args):
        self._sync_swatches()
        self._changed()

    def _sync_swatches(self):
        rgba = self.color.get_rgba()
        current = (rgba.red, rgba.green, rgba.blue)
        loading, self.loading = self.loading, True
        for button in self.swatches:
            button.set_active(all(abs(a - b) < 0.01 for a, b in zip(button.rgb, current)))
        if not any(button.get_active() for button in self.swatches):
            # No grouped toggle can be "none"; clear them all through the group leader.
            for button in self.swatches:
                button.set_active(False)
        self.loading = loading

    def _changed(self, *_args):
        if self.loading:
            return
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(120, self.refresh)

    def refresh(self):
        self.timer = None
        try:
            png, (width, height) = render_stamp(self.stamp, self.style(), self.width.get_value(), self._author())
            self.preview.set_paintable(_texture(png))
            self.dimensions.set_text(_("stamp_dimensions", f'{width:.0f}', f'{height:.0f}'))
            self.clear_error()
            self.apply_button.set_sensitive(True)
        except (ValueError, RuntimeError) as error:
            self.error(error)
            self.apply_button.set_sensitive(False)
        return False

    # -- templates ----------------------------------------------------------------
    def _save_template(self, _button):
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class('boxed-list')
        entry = Adw.EntryRow(title=_("stamp_template_name"))
        entry.set_text(self.text_row.get_text().title())
        rows.append(entry)

        def response(answer):
            name = entry.get_text().strip()
            if answer != 'save' or not name:
                return
            item = dict(name=name, stamp=self.stamp, width=self.width.get_value(), style=self.style())
            stored = [entry for entry in _custom_templates() if entry.get('name') != name] + [item]
            set_setting('stamp_templates', stored)
            for child in [child for child in self.tiles if child.custom and child.custom.get('name') == name]:
                self.gallery.remove(child)
                self.tiles.remove(child)
            child = self._add_tile(self.stamp, item['style'], name, item)
            self.gallery.select_child(child)
            self.custom = item
            self.remove_button.set_visible(True)
            GLib.idle_add(self._render_gallery)
        alert(self, _("stamp_save_template"), _("stamp_save_template_body"),
              [('cancel', _("btn_cancel"), None), ('save', _("btn_save"), 'suggested')],
              default='save', close='cancel', extra=rows, callback=response)

    def _remove_template(self, _button):
        if self.custom is None:
            return
        name = self.custom.get('name')
        set_setting('stamp_templates', [item for item in _custom_templates() if item.get('name') != name])
        for child in [child for child in self.tiles if child.custom is self.custom]:
            self.gallery.remove(child)
            self.tiles.remove(child)
        self.custom = None
        self.remove_button.set_visible(False)

    # -- actions ------------------------------------------------------------------
    def response(self, response):
        if response == Gtk.ResponseType.APPLY:
            self._on_apply_clicked(None)
        else:
            self.force_close()

    def _on_apply_clicked(self, _button):
        window = self.window
        if self.source is not window.doc:
            self.error(_("tool_document_changed"))
            return
        style, width, author = self.style(), self.width.get_value(), self.author.get_text()
        set_setting('review_author', author)
        if not self.info:
            # Remember the look of new stamps, but start each one upright.
            set_setting('stamp_last', dict(stamp=self.stamp, width=width, style=dict(style, angle=0),
                                           template=self.custom.get('name') if self.custom else None))
        source, info, stamp = self.source, self.info, self.stamp
        if info:
            visual = self.visual

            def replace():
                tools.delete_annotation(source, info['page'], info['xref'])
                xref = tools.place_stamp(source, info['page'], (visual.x0, visual.y0), stamp, width, author, style)
                page = source[info['page']]
                bounds = page.load_annot(xref).rect * page.rotation_matrix
                center = (visual.tl + visual.br) / 2
                x = max(0, min(center.x - bounds.width / 2, page.rect.width - bounds.width))
                y = max(0, min(center.y - bounds.height / 2, page.rect.height - bounds.height))
                tools.move_stamp(source, info['page'], xref, fitz.Rect(x, y, x + bounds.width, y + bounds.height))
            try:
                if window._mutate_document(replace, page_num=info['page']) is False:
                    return
            except (ValueError, RuntimeError) as error:
                self.error(error)
                return
        else:
            self.controller.stamp_settings = (source, stamp, width, author, style)
            window.on_tool_selected(None, 'stamp')
            window.status_label.set_text(_("tool_stamp_place"))
        self.force_close()
