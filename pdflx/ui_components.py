import gi
import io
import cairo
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GdkPixbuf, Adw, GLib, GObject, Gio, Pango, PangoCairo
from .i18n import _
from .symbol_catalog import SYMBOL_CATEGORIES, EMOJI_CATEGORIES


class ColorSwatchButton(Gtk.Button):
    """Compact, theme aware color chooser shared by all editing tools."""
    __gsignals__ = {"color-set": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, icon_name=None, compact=False):
        super().__init__()
        self._rgba = Gdk.RGBA()
        self._rgba.parse("black")
        self._title = None
        self.add_css_class("color-picker-btn")
        self.set_valign(Gtk.Align.CENTER)
        box = Gtk.Box(spacing=7, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        if icon_name and not compact:
            box.append(Gtk.Image(icon_name=icon_name, pixel_size=16))
        self.swatch = Gtk.DrawingArea(content_width=22, content_height=22,
                                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.swatch.set_draw_func(self._draw_swatch)
        box.append(self.swatch)
        if not compact:
            box.append(Gtk.Image(icon_name="pan-down-symbolic", pixel_size=10))
        self.set_child(box)
        self.connect("clicked", self._choose_color)

    def set_title(self, title):
        self._title = title

    def set_rgba(self, rgba):
        self._rgba = rgba.copy()
        self.swatch.queue_draw()

    def get_rgba(self):
        return self._rgba.copy()

    def _draw_swatch(self, area, cr, width, height):
        x, y = (width - 18) / 2, (height - 18) / 2
        radius = 4
        cr.new_sub_path()
        for cx, cy, start in ((x + 14, y + 4, -90), (x + 14, y + 14, 0),
                              (x + 4, y + 14, 90), (x + 4, y + 4, 180)):
            cr.arc(cx, cy, radius, start * 3.14159265 / 180,
                   (start + 90) * 3.14159265 / 180)
        cr.close_path()
        cr.set_source_rgba(self._rgba.red, self._rgba.green, self._rgba.blue, self._rgba.alpha)
        cr.fill_preserve()
        # A light inner edge and theme colored outer edge keep black and white visible.
        cr.set_source_rgba(1, 1, 1, 0.35)
        cr.set_line_width(2)
        cr.stroke_preserve()
        color = area.get_style_context().get_color()
        cr.set_source_rgba(color.red, color.green, color.blue, 0.4)
        cr.set_line_width(1)
        cr.stroke()

    def _choose_color(self, button):
        dialog = Gtk.ColorDialog()
        dialog.set_title(self._title or self.get_tooltip_text() or _("color_tip"))
        dialog.set_with_alpha(False)
        dialog.choose_rgba(self.get_root(), self._rgba, None, self._color_chosen)

    def _color_chosen(self, dialog, result):
        try:
            rgba = dialog.choose_rgba_finish(result)
        except GLib.Error:
            return
        self.set_rgba(rgba)
        self.emit("color-set")


class PagePreview(Gtk.Picture):
    """Measure preview height from its width so grid rows cannot collapse."""

    def __init__(self):
        self._page_ratio = 595 / 842
        super().__init__(can_shrink=True, keep_aspect_ratio=True, hexpand=True)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (0, 144, -1, -1)
        width = for_size if for_size >= 0 else 144
        height = max(1, round(width / self._page_ratio))
        return (height, height, -1, -1)

    def set_thumbnail(self, thumbnail):
        if thumbnail is None:
            self.set_paintable(None)
            return
        self._page_ratio = thumbnail.get_width() / max(1, thumbnail.get_height())
        self.set_paintable(Gdk.Texture.new_for_pixbuf(thumbnail))
        self.queue_resize()


class PageThumbnailFactory(Gtk.SignalListItemFactory):
    """Factory class to create and bind page thumbnail widgets in the sidebar."""
    def __init__(self, editor_window=None):
        """Initialise the factory with setup and bind signal handlers."""
        super().__init__()
        self.editor_window = editor_window
        self.connect("setup", self._on_setup)
        self.connect("bind", self._on_bind)

    def _on_setup(self, factory, list_item):
        """Set up initial layout for the thumbnail list item."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, hexpand=True)
        box.add_css_class("thumbnail-card")
        box.set_halign(Gtk.Align.FILL)
        
        # Paper frame with realistic drop shadow and border
        paper_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        paper_frame.add_css_class("thumbnail-paper-frame")
        paper_frame.set_halign(Gtk.Align.FILL)
        paper_frame.set_hexpand(True)

        image = PagePreview()
        image.add_css_class("thumbnail-image")
        paper_frame.append(image)

        box.append(paper_frame)

        badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, halign=Gtk.Align.CENTER)
        badge_box.add_css_class("thumbnail-badge-box")
        label = Gtk.Label()
        label.add_css_class("thumbnail-page-label")
        badge_box.append(label)

        box.append(badge_box)
        list_item.set_child(box)

    def _on_bind(self, factory, list_item):
        """Bind a PDF page object and its thumbnail image to the list item."""
        box = list_item.get_child()
        paper_frame = box.get_first_child()
        picture = paper_frame.get_first_child()
        badge_box = box.get_last_child()
        label = badge_box.get_first_child()
        pdf_page = list_item.get_item()

        if pdf_page and pdf_page.thumbnail:
            picture.set_thumbnail(pdf_page.thumbnail)
            picture.set_visible(True)
        else:
            picture.set_thumbnail(None)
            picture.set_visible(True)

        page_index = pdf_page.index
        label.set_text(_("page_info").format(page_index + 1))
        box.set_tooltip_text(_("page_info").format(page_index + 1))

        for ctrl in list(box.observe_controllers()):
            if isinstance(ctrl, Gtk.DragSource) or isinstance(ctrl, Gtk.DropTarget):
                box.remove_controller(ctrl)

        drag_source = Gtk.DragSource.new()
        drag_source.set_actions(Gdk.DragAction.MOVE)

        def on_prepare(source, x, y, idx=page_index):
            if self.editor_window and getattr(self.editor_window, 'view_mode', False):
                return None
            val = GObject.Value(GObject.TYPE_INT, idx)
            return Gdk.ContentProvider.new_for_value(val)

        def on_drag_begin(source, drag, idx=page_index, pic=picture):
            pdf_pg = list_item.get_item()
            if pdf_pg and pdf_pg.thumbnail:
                tex = Gdk.Texture.new_for_pixbuf(pdf_pg.thumbnail)
                Gtk.DragSource.set_icon(source, tex, 0, 0)

        drag_source.connect("prepare", on_prepare)
        drag_source.connect("drag-begin", on_drag_begin)
        box.add_controller(drag_source)

        drop_target = Gtk.DropTarget.new(GObject.TYPE_INT, Gdk.DragAction.MOVE)

        def on_drop(target, value, x, y, to_idx=page_index):
            if self.editor_window and getattr(self.editor_window, 'view_mode', False):
                return False
            from_idx = value
            if from_idx == to_idx:
                return False
            if self.editor_window:
                self.editor_window.on_page_reorder(from_idx, to_idx)
            return True

        drop_target.connect("drop", on_drop)
        box.add_controller(drop_target)

        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        def on_right_click(gesture, n_press, rx, ry, idx=page_index):
            if self.editor_window and hasattr(self.editor_window, 'show_thumbnail_context_menu'):
                self.editor_window.show_thumbnail_context_menu(box, rx, ry, idx)
        right_click.connect("pressed", on_right_click)
        box.add_controller(right_click)


def show_error_dialog(parent_window, message, title="Error"):
    """Show a simple error alert."""
    from .dialogs import alert
    alert(parent_window, title, str(message), [('close', _("btn_close"), None)], 'close', 'close')


def show_confirm_dialog(parent_window, message, title="Confirm", destructive=True, checkbox_label=None):
    """Ask for confirmation; blocks until answered. Optionally offers a checkbox."""
    from .dialogs import ask
    checkbox = None
    if checkbox_label:
        checkbox = Gtk.CheckButton(label=checkbox_label, halign=Gtk.Align.CENTER)
    response = ask(parent_window, title, message,
                   [('cancel', _("btn_cancel"), None),
                    ('accept', _("btn_confirm"), 'destructive' if destructive else 'suggested')],
                   default='cancel', close='cancel', extra=checkbox)
    accepted = response == 'accept'
    if checkbox_label:
        return accepted, checkbox.get_active()
    return accepted


def show_save_changes_dialog(parent_window):
    """Ask to save or discard changes; returns a Gtk.ResponseType."""
    from .dialogs import ask
    response = ask(parent_window, _("unsaved_title"), _("unsaved_changes"),
                   [('cancel', _("btn_cancel"), None), ('discard', _("btn_dont_save"), 'destructive'),
                    ('save', _("btn_save"), 'suggested')], default='save', close='cancel')
    return {'save': Gtk.ResponseType.ACCEPT, 'discard': Gtk.ResponseType.REJECT}.get(response, Gtk.ResponseType.CANCEL)


def show_open_file_dialog(parent_window, title, filters=None, default_filter=None, callback=None):
    """
    Show an open file dialog using Gtk.FileDialog if available (GTK >= 4.10),
    otherwise falling back to Gtk.FileChooserDialog (GTK < 4.10).
    Callback receives (gfile) or (gfile, selected_filter).
    """
    if hasattr(Gtk, "FileDialog"):
        dialog = Gtk.FileDialog(title=title)
        if filters:
            store = Gio.ListStore.new(Gtk.FileFilter)
            for f in filters:
                store.append(f)
            dialog.set_filters(store)
        if default_filter:
            dialog.set_default_filter(default_filter)

        def on_open_finish(d, result):
            try:
                gfile = d.open_finish(result)
            except GLib.Error:
                gfile = None
            if callback:
                import inspect
                sig = inspect.signature(callback)
                if len(sig.parameters) >= 2:
                    callback(gfile, None)
                else:
                    callback(gfile)

        dialog.open(parent_window, None, on_open_finish)
    else:
        dialog = Gtk.FileChooserDialog(
            title=title,
            transient_for=parent_window,
            action=Gtk.FileChooserAction.OPEN,
        )
        dialog.add_buttons(
            _("btn_cancel"), Gtk.ResponseType.CANCEL,
            _("btn_confirm"), Gtk.ResponseType.ACCEPT,
        )
        if parent_window:
            dialog.set_modal(True)
        if filters:
            for f in filters:
                dialog.add_filter(f)
        if default_filter:
            dialog.set_filter(default_filter)

        def on_response(d, response_id):
            gfile = None
            selected_filter = None
            if response_id == Gtk.ResponseType.ACCEPT:
                gfile = d.get_file()
                selected_filter = d.get_filter()
            d.destroy()
            if callback:
                import inspect
                sig = inspect.signature(callback)
                if len(sig.parameters) >= 2:
                    callback(gfile, selected_filter)
                else:
                    callback(gfile)

        dialog.connect("response", on_response)
        dialog.present()


def show_save_file_dialog(parent_window, title, initial_name=None, filters=None, default_filter=None, callback=None):
    """
    Show a save file dialog using Gtk.FileDialog if available (GTK >= 4.10),
    otherwise falling back to Gtk.FileChooserDialog (GTK < 4.10).
    Callback receives (gfile) or (gfile, selected_filter).
    """
    if hasattr(Gtk, "FileDialog"):
        dialog = Gtk.FileDialog(title=title)
        if initial_name:
            dialog.set_initial_name(initial_name)
        if filters:
            store = Gio.ListStore.new(Gtk.FileFilter)
            for f in filters:
                store.append(f)
            dialog.set_filters(store)
        if default_filter:
            dialog.set_default_filter(default_filter)

        def on_save_finish(d, result):
            try:
                gfile = d.save_finish(result)
            except GLib.Error:
                gfile = None
            if callback:
                import inspect
                sig = inspect.signature(callback)
                if len(sig.parameters) >= 2:
                    callback(gfile, None)
                else:
                    callback(gfile)

        dialog.save(parent_window, None, on_save_finish)
    else:
        dialog = Gtk.FileChooserDialog(
            title=title,
            transient_for=parent_window,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons(
            _("btn_cancel"), Gtk.ResponseType.CANCEL,
            _("btn_save") if "_" in _("btn_save") else _("btn_confirm"), Gtk.ResponseType.ACCEPT,
        )
        if parent_window:
            dialog.set_modal(True)
        if initial_name:
            dialog.set_current_name(initial_name)
        if filters:
            for f in filters:
                dialog.add_filter(f)
        if default_filter:
            dialog.set_filter(default_filter)

        def on_response(d, response_id):
            gfile = None
            selected_filter = None
            if response_id == Gtk.ResponseType.ACCEPT:
                gfile = d.get_file()
                selected_filter = d.get_filter()
            d.destroy()
            if callback:
                import inspect
                sig = inspect.signature(callback)
                if len(sig.parameters) >= 2:
                    callback(gfile, selected_filter)
                else:
                    callback(gfile)

        dialog.connect("response", on_response)
        dialog.present()


def render_emoji_to_png_bytes(emoji_char, size=96):
    """Render an emoji character into crisp high-resolution PNG bytes using Cairo and Pango."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    layout = PangoCairo.create_layout(cr)
    font_desc = Pango.FontDescription("Noto Color Emoji, Apple Color Emoji, Segoe UI Emoji, sans-serif")
    font_desc.set_absolute_size(int(size * 0.72 * Pango.SCALE))
    layout.set_font_description(font_desc)
    layout.set_text(emoji_char, -1)

    ink_rect, logical_rect = layout.get_pixel_extents()
    x = (size - logical_rect.width) / 2.0
    y = (size - logical_rect.height) / 2.0
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)

    bio = io.BytesIO()
    surface.write_to_png(bio)
    return bio.getvalue()


class SymbolsPopover(Gtk.Popover):
    """Searchable, categorized palette for inserting symbols and color emojis."""
    SYMBOLS_REVIEW = [char for _, items in SYMBOL_CATEGORIES for char, _ in items]
    EMOJIS_COMMON = [char for _, items in EMOJI_CATEGORIES for char, _ in items]

    def __init__(self, editor_window=None, **kwargs):
        super().__init__(**kwargs)
        self.editor_window = editor_window
        self.set_autohide(True)
        self._sections = []
        self._build_ui()

    def _build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                           margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        switcher = Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.FILL)
        main_box.append(switcher)
        self.search_entry = Gtk.SearchEntry(placeholder_text=_("palette_search"))
        self.search_entry.connect("search-changed", self._filter_items)
        main_box.append(self.search_entry)

        for tab_name, title, categories, is_emoji in (
            ("symbols", _("tab_symbols"), SYMBOL_CATEGORIES, False),
            ("emojis", _("tab_emojis"), EMOJI_CATEGORIES, True),
        ):
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            sections = []
            for key, items in categories:
                section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                heading = Gtk.Label(label=_(key), xalign=0)
                heading.add_css_class("heading")
                section.append(heading)
                flow = Gtk.FlowBox(valign=Gtk.Align.START, homogeneous=True,
                                   min_children_per_line=7, max_children_per_line=7,
                                   selection_mode=Gtk.SelectionMode.NONE,
                                   row_spacing=3, column_spacing=3)
                buttons = []
                for char, name in items:
                    label = Gtk.Label(label=char)
                    label.set_attributes(Pango.AttrList.from_string("0 -1 scale 1.5"))
                    button = Gtk.Button(child=label, tooltip_text=name)
                    button.add_css_class("flat")
                    button.set_size_request(40, 40)
                    button.connect("clicked", self._on_symbol_clicked, char, is_emoji)
                    flow.append(button)
                    buttons.append((flow.get_child_at_index(len(buttons)),
                                    f"{char} {name} {_(key)}".casefold()))
                section.append(flow)
                content.append(section)
                sections.append((section, buttons))
            empty_label = Gtk.Label(label=_("palette_no_results"), wrap=True,
                                    margin_top=24, margin_bottom=24, visible=False)
            empty_label.add_css_class("dim-label")
            content.append(empty_label)
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                        vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                        min_content_width=360, min_content_height=340)
            scroll.set_child(content)
            self.stack.add_titled(scroll, tab_name, title)
            self._sections.append((scroll, sections, empty_label))
        main_box.append(self.stack)
        self.set_child(main_box)

    def _filter_items(self, entry):
        words = entry.get_text().strip().casefold().split()
        for scroll, sections, empty_label in self._sections:
            total = 0
            for section, buttons in sections:
                count = 0
                for child, search_text in buttons:
                    visible = all(word in search_text for word in words)
                    child.set_visible(visible)
                    count += visible
                section.set_visible(count > 0)
                total += count
            empty_label.set_visible(total == 0)
            scroll.get_vadjustment().set_value(0)

    def _on_symbol_clicked(self, button, symbol, is_emoji):
        self.popdown()
        if self.editor_window and hasattr(self.editor_window, 'insert_symbol_or_emoji'):
            self.editor_window.insert_symbol_or_emoji(symbol, is_emoji=is_emoji)


from .new_document_dialog import show_new_document_dialog, NewDocumentDialog