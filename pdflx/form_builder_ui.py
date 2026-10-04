"""Add fields, Style and Tools sections of the Form Fields sidebar."""
import pymupdf as fitz
from gi.repository import Adw, Gtk, Gdk, GLib, Pango

from . import document_tools as tools, document_features as features
from . import form_builder as builder
from .i18n import _

CHOICE_PRESETS = ('dropdown', 'list', 'radio')


def _section(text):
    label = Gtk.Label(label=text, xalign=0, margin_top=8)
    label.add_css_class('pdflx-section-title')
    return label


def _list():
    listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    listing.add_css_class('boxed-list')
    listing.connect('row-activated', lambda _list, row: getattr(row, 'callback', lambda: None)())
    return listing


class BuilderPanel:
    def __init__(self, controller):
        self.controller = controller
        self.window = controller.window
        self.preset = None
        self.show_tab_numbers = False
        self.guides = []

        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # ---- Add fields
        self.widget.append(_section(_("qf_title")))
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class('pdflx-card')
        top = Gtk.Box(spacing=6)
        self.label = Gtk.Entry(placeholder_text=_("qf_label_hint"), hexpand=True)
        top.append(self.label)
        self.required = Gtk.ToggleButton(label='*', tooltip_text=_("qf_required_tip"), valign=Gtk.Align.CENTER)
        self.required.add_css_class('pdflx-required-toggle')
        top.append(self.required)
        card.append(top)
        self.buttons = {}
        for title, keys in ((_("qf_group_inputs"), ('text', 'multiline', 'date', 'email', 'phone', 'number')),
                            (_("qf_group_choices"), ('dropdown', 'list', 'checkbox', 'radio')),
                            (_("qf_group_buttons"), ('submit', 'reset', 'signature'))):
            caption = Gtk.Label(label=title, xalign=0)
            caption.add_css_class('pdflx-card-caption')
            card.append(caption)
            grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3, min_children_per_line=3,
                               column_spacing=6, row_spacing=6, homogeneous=True)
            for key in keys:
                label = builder.PRESET_BY_KEY[key][0]
                tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                tile.append(Gtk.Image(icon_name=f'editor-qf-{key if key != "multiline" else "paragraph"}-symbolic',
                                      pixel_size=18))
                text = Gtk.Label(label=_(label), ellipsize=Pango.EllipsizeMode.END)
                text.add_css_class('caption')
                tile.append(text)
                button = Gtk.ToggleButton(child=tile, tooltip_text=_(label + '_tip'))
                button.add_css_class('pdflx-qf-tile')
                button.connect('toggled', self._on_toggle, key)
                grid.append(button)
                self.buttons[key] = button
            card.append(grid)
        self.choices = Gtk.Entry(placeholder_text=_("qf_choices_hint"), visible=False)
        card.append(self.choices)
        self.url = Gtk.Entry(placeholder_text=_("qf_url_hint"), visible=False)
        card.append(self.url)
        self.hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=32, visible=False)
        self.hint.add_css_class('dim-label')
        self.hint.add_css_class('caption')
        card.append(self.hint)
        self.widget.append(card)

        # ---- Style
        self.widget.append(_section(_("qf_section_style")))
        style_list = _list()
        style_row = Adw.ActionRow(title=_("qf_style").rstrip('…'), subtitle=_("qf_style_sub"), activatable=True)
        style_row.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
        style_row.callback = self.style_dialog
        style_list.append(style_row)
        self.position = Adw.ComboRow(title=_("qf_label_position"),
                                     model=Gtk.StringList.new([_("qf_label_above"), _("qf_label_left"), _("qf_label_none")]))
        self.position.set_selected(('above', 'left', 'none').index(builder.style()['label_position']))
        self.position.connect('notify::selected', lambda *_: builder.save_style(
            {'label_position': ('above', 'left', 'none')[self.position.get_selected()]}))
        style_list.append(self.position)
        self.snap = Adw.SwitchRow(title=_("qf_snap"), subtitle=_("qf_snap_sub"), active=builder.style()['snap'])
        def snap_changed(row, *_args):
            builder.save_style({'snap': row.get_active()})
            action = self.window.lookup_action('alignment_guides')
            if action and action.get_state().get_boolean() != row.get_active():
                action.set_state(GLib.Variant.new_boolean(row.get_active()))
        self.snap.connect('notify::active', snap_changed)
        style_list.append(self.snap)
        self.widget.append(style_list)

        # ---- Tools
        self.widget.append(_section(_("qf_section_tools")))
        tools_list = _list()
        self.tool_rows = {}
        for key, icon, title, subtitle, callback in (
                ('copies', 'editor-qf-copies-symbolic', _("qf_copies"), _("qf_copies_sub"), self.copies_dialog),
                ('style', 'editor-match-style-symbolic', _("qf_apply_style"), _("qf_apply_style_sub"), self.apply_style),
                ('detect', 'editor-qf-detect-symbolic', _("qf_detect"), _("qf_detect_sub"), self.detect),
                ('tabs', 'editor-qf-tab-order-symbolic', _("qf_tab_order"), _("qf_tab_order_sub"), None),
                ('validate', 'editor-qf-validate-symbolic', _("qf_validate"), _("qf_validate_sub"),
                 lambda: controller.validate_fields()),
                ('scripts', 'editor-qf-scripts-symbolic', _("qf_scripts"), _("qf_scripts_sub"), self.scripts),
                ('create', 'editor-form-symbolic', _("qf_full_field"), _("qf_full_field_sub"),
                 lambda: controller.create_field())):
            row = Adw.ActionRow(title=title, subtitle=subtitle, activatable=callback is not None)
            row.add_prefix(Gtk.Image(icon_name=icon))
            if callback is None:
                menu = Gtk.MenuButton(icon_name='view-more-symbolic', valign=Gtk.Align.CENTER,
                                      popover=self._tab_popover(), tooltip_text=title)
                menu.add_css_class('flat')
                row.add_suffix(menu)
                row.set_activatable_widget(menu)
            else:
                row.callback = callback
            tools_list.append(row)
            self.tool_rows[key] = row
        self.widget.append(tools_list)
        self.label.connect('activate', lambda *_: self.window.pdf_view.grab_focus())

    def open(self):
        """Put the cursor in the label box, ready for the first field."""
        self.label.grab_focus()
        self.window.status_label.set_text(_("qf_open_hint"))

    # ------------------------------------------------------------ presets
    def _on_toggle(self, button, key):
        if self._syncing():
            return
        if button.get_active():
            self._set_buttons(key)
            self.start(key)
        elif self.preset == key:
            self.stop()

    def _syncing(self):
        return getattr(self, '_sync_flag', False)

    def _set_buttons(self, active):
        self._sync_flag = True
        for key, button in self.buttons.items():
            button.set_active(key == active)
        self._sync_flag = False

    def start(self, preset):
        c = self.controller
        if not c.editable() or not c.window.doc:
            self._set_buttons(None)
            return
        self.preset = preset
        c.draw_settings = dict(source=c.window.doc, builder=True, preset=preset)
        self.choices.set_visible(preset in CHOICE_PRESETS)
        self.url.set_visible(preset == 'submit')
        self.hint.set_text(_("qf_place_hint"))
        self.hint.set_visible(True)
        c.window.on_tool_selected(None, 'form_create')
        c.window.status_label.set_text(_("qf_place_hint"))

    def stop(self):
        self.preset = None
        self._set_buttons(None)
        self.choices.set_visible(False)
        self.url.set_visible(False)
        self.hint.set_visible(False)
        settings = getattr(self.controller, 'draw_settings', None)
        if settings and settings.get('builder'):
            self.controller.draw_settings = None
        if self.window.tool_mode == 'form_create':
            self.window.on_tool_selected(None, 'select')

    def sync_tool(self):
        """Release the preset buttons when another tool is chosen."""
        if self.preset and self.window.tool_mode != 'form_create':
            self.preset = None
            self._set_buttons(None)
            self.choices.set_visible(False)
            self.url.set_visible(False)
            self.hint.set_visible(False)

    def _choice_list(self):
        values = [part.strip() for part in self.choices.get_text().split(',') if part.strip()]
        return values or ['Option 1', 'Option 2']

    def place(self, bounds, start):
        """Create the preset field in visual bounds (a click places the default size)."""
        c = self.controller
        w = c.window
        source = w.doc
        number = w.current_page_index
        page = source[number]
        preset = self.preset
        _label, kind, (width, height) = builder.PRESET_BY_KEY[preset]
        choices = self._choice_list() if preset in CHOICE_PRESETS else ()
        if preset == 'radio':
            height = max(height, 18 * len(choices))
        if bounds.width < 10 or bounds.height < 6:
            x, y = start
            bounds = fitz.Rect(x, y, x + width, y + height)
        visual = fitz.Rect(bounds) & page.rect
        if visual.is_empty:
            return
        native = visual * page.derotation_matrix
        label_text = builder.clean_label(self.label.get_text())
        is_button = kind == 'button'
        existing = {f['name'] for f in features.list_form_fields(source)}
        guess = label_text or builder.nearby_label(page, native, right=preset in ('checkbox', 'radio'))
        name = builder.field_name(guess, existing, fallback=preset)
        required = self.required.get_active() and kind not in ('button', 'signature')
        values = {'caption': label_text, 'url': self.url.get_text().strip()}
        if preset == 'submit' and not values['url']:
            w.status_label.set_text(_("qf_need_url"))
            self.url.grab_focus()
            return
        options = builder.field_options(preset, values)
        if guess and not is_button:
            options['tooltip'] = builder.clean_label(guess)  # Screen readers announce fields by tooltip.
        behaviour = builder.behaviour(preset)
        label = None if is_button else builder.label_object(label_text, native, number, required)
        created = []

        def mutate():
            xref = tools.create_form_field(source, number, name, kind, native, choices, required, options=options,
                                           value=choices[0] if preset == 'radio' else None)
            created.append(xref)
            if behaviour:
                from .ops import formbehaviour
                formbehaviour.apply(source, xref, behaviour)
            builder.add_scripts(source, xref, preset)
            if label is not None:
                from . import pdf_handler
                w.editable_texts.append(label)
                label.is_baked = True
                ok, error = pdf_handler.rebuild_page(source, number, w.editable_texts, w.editable_shapes,
                                                     w.editable_images, all_strokes=w.editable_strokes)
                if not ok:
                    raise ValueError(error)
        try:
            done = w._mutate_document(mutate, page_num=number)
        except ValueError as error:
            w.status_label.set_text(str(error))
            return
        if not done:
            return
        field = next((f for f in features.list_form_fields(source, [number]) if f['xref'] == created[0]), None)
        if field:
            c.interaction.select(field)
        self.label.set_text('')
        self.required.set_active(False)
        w.status_label.set_text(_("qf_placed", name))
        self.label.grab_focus()

    # ------------------------------------------------------------ style
    def style_dialog(self):
        from .document_tool_ui import ToolDialog
        current = builder.style()
        colors = {}

        def apply():
            values = {'font': fonts[font.get_selected()], 'font_size': size.get_value(),
                      'border_width': border.get_value(), 'label_size': label_size.get_value(),
                      'date_format': dates[date.get_selected()]}
            for key, button in colors.items():
                rgba = button.get_rgba()
                values[key] = (rgba.red, rgba.green, rgba.blue)
            if transparent.get_active():
                values['fill_color'] = None
            builder.save_style(values)
        dialog = ToolDialog(self.window, _("qf_style_title"), apply)
        fonts = ['Helv', 'HeBo', 'TiRo', 'Cour']
        font = dialog.dropdown(_("qf_style_font"), fonts)
        font.set_selected(fonts.index(current['font']) if current['font'] in fonts else 0)
        size = dialog.spin(_("qf_style_size"), current['font_size'], 0, 72, 0.5)
        border = dialog.spin(_("qf_style_border"), current['border_width'], 0, 10, 0.5)
        for key, label in (('text_color', _("qf_style_text_color")), ('border_color', _("qf_style_border_color")),
                           ('fill_color', _("qf_style_fill_color")), ('label_color', _("qf_style_label_color"))):
            button = Gtk.ColorButton()
            rgba = Gdk.RGBA()
            rgba.red, rgba.green, rgba.blue = current[key] or (1, 1, 1)
            rgba.alpha = 1
            button.set_rgba(rgba)
            dialog.field(label, button)
            colors[key] = button
        transparent = dialog.check(_("qf_style_transparent"), current['fill_color'] is None)
        label_size = dialog.spin(_("qf_style_label_size"), current['label_size'], 5, 36, 0.5)
        from .ops.formbehaviour import DATE_FORMATS
        dates = list(DATE_FORMATS)
        date = dialog.dropdown(_("qf_style_date"), dates)
        date.set_selected(dates.index(current['date_format']) if current['date_format'] in dates else 0)
        dialog.present()

    def apply_style(self):
        """Give the selected fields (or all fields on the page) the form style."""
        c = self.controller
        if not c.editable():
            return
        fields = c.interaction.fields() or [f for f in features.list_form_fields(c.window.doc, [c.window.current_page_index])
                                            if f['type'] != fitz.PDF_WIDGET_TYPE_SIGNATURE]
        s = builder.style()
        options = {key: s[key] for key in builder.APPEARANCE_KEYS}
        c.restyle(fields, options)

    def scripts(self):
        """Scripts for the selected field, or the document's script overview."""
        field = self.controller.interaction.current()
        if field and self.controller.editable():
            from .form_scripts_ui import FieldScriptsDialog
            FieldScriptsDialog(self.controller, field).present()
        else:
            self.window.lookup_action('form_structure').activate(None)

    # ------------------------------------------------------------ tools
    def _tab_popover(self):
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8)
        for label, mode in ((_("qf_tab_rows"), 'rows'), (_("qf_tab_columns"), 'columns')):
            button = Gtk.Button(label=label)
            button.add_css_class('flat')
            button.connect('clicked', lambda _b, mode=mode: (popover.popdown(), self.set_tab_order(mode)))
            box.append(button)
        show = Gtk.CheckButton(label=_("qf_tab_show"))
        show.connect('toggled', lambda b: (setattr(self, 'show_tab_numbers', b.get_active()),
                                           self.window.pdf_view.queue_draw()))
        box.append(show)
        popover.set_child(box)
        return popover

    def set_tab_order(self, mode):
        c = self.controller
        if not c.editable():
            return
        number = c.window.current_page_index
        counted = []
        if c.window._mutate_document(lambda: counted.append(builder.tab_order(c.window.doc, number, mode)),
                                     page_num=number):
            self.show_tab_numbers = True
            c.window.status_label.set_text(_("qf_tab_done", counted[0]))
            c.refresh()

    def draw_tab_numbers(self, cr):
        if not self.show_tab_numbers or not self.window.doc:
            return
        zoom = self.window.zoom_level
        cr.save()
        for index, field in enumerate(features.list_form_fields(self.window.doc, [self.window.current_page_index]), 1):
            x0, y0, _x1, _y1 = field['rect']
            radius = 7 / zoom
            cr.arc(x0, y0, radius, 0, 6.2832)
            cr.set_source_rgba(0.93, 0.2, 0.55, 0.95)
            cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.set_font_size(8 / zoom)
            text = str(index)
            extents = cr.text_extents(text)
            cr.move_to(x0 - extents.width / 2 - extents.x_bearing, y0 - extents.height / 2 - extents.y_bearing)
            cr.show_text(text)
        cr.restore()

    def detect(self):
        from .dialogs import alert
        c = self.controller
        w = c.window
        if not c.editable() or not w.doc:
            return
        number = w.current_page_index
        page = w.doc[number]
        found = builder.detect_fields(page)
        if not found:
            alert(w, _("qf_detect"), _("qf_detect_none"), [('close', _("btn_close"), 'suggested')], 'close', 'close')
            return
        boxes = sum(1 for kind, _r in found if kind == 'checkbox')

        # Read the labels now: creating fields invalidates page wrappers.
        existing = {f['name'] for f in features.list_form_fields(w.doc)}
        names, tips = [], []
        for kind, rect in found:
            label = builder.nearby_label(page, rect, right=kind == 'checkbox')
            names.append(builder.field_name(label, existing, fallback='checkbox' if kind == 'checkbox' else 'text'))
            tips.append(label)
            existing.add(names[-1])

        def create():
            source = w.doc
            for (kind, rect), name, tip in zip(found, names, tips):
                # The detected box is already drawn on the page; the field stays invisible on top of it.
                options = {'border_width': 0, 'fill_color': None, 'font_size': 0 if kind != 'checkbox' else None}
                options = {k: v for k, v in options.items() if v is not None}
                if kind == 'multiline':
                    options['multiline'] = True
                if tip:
                    options['tooltip'] = tip
                tools.create_form_field(source, number, name, 'checkbox' if kind == 'checkbox' else 'text',
                                        rect, options=options)

        def answer(response):
            if response == 'create' and w._mutate_document(create, page_num=number):
                w.status_label.set_text(_("qf_detect_done", len(found)))
                c.refresh()
        alert(w, _("qf_detect"), _("qf_detect_found", len(found) - boxes, boxes),
              [('cancel', _("btn_cancel"), None), ('create', _("qf_detect_create"), 'suggested')], 'create', 'cancel',
              answer)

    def copies_dialog(self):
        from .document_tool_ui import ToolDialog
        c = self.controller
        fields = c.interaction.fields() or ([c.interaction.current()] if c.interaction.current() else [])
        if not c.editable() or not fields:
            c.window.status_label.set_text(_("qf_copies_select"))
            return

        def apply():
            c.multiple_copies(fields, int(rows.get_value()), int(columns.get_value()),
                              gap_x.get_value(), gap_y.get_value())
        dialog = ToolDialog(self.window, _("qf_copies_title"), apply)
        rows = dialog.spin(_("qf_copies_rows"), 3, 1, 50)
        columns = dialog.spin(_("qf_copies_columns"), 1, 1, 20)
        gap_x = dialog.spin(_("qf_copies_gap_x"), 8, 0, 200, 1)
        gap_y = dialog.spin(_("qf_copies_gap_y"), 8, 0, 200, 1)
        dialog.present()
