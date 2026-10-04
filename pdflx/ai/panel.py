"""Floating AI bar: conversation, attachments and the prompt box over the document."""
import json
import os
import threading

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from ..i18n import _, get_setting, set_setting
from . import client, config
from .agent import AgentRun, Conversation, load_attachment
from .tools import DocumentTools

ACCEPTED = ('*.png', '*.jpg', '*.jpeg', '*.webp', '*.gif', '*.bmp', '*.tif', '*.tiff', '*.pdf')


def plural(key, count, page):
    return _(key + '_one', page) if count == 1 else _(key, count, page)


def tool_summary(name, arguments, result):
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        data = {}
    page = data.get('page') or arguments.get('page')
    count = lambda key: len(data.get(key) or arguments.get(key) or [])
    text = {
        'get_document_info': _("ai_tool_info"),
        'list_elements': _("ai_tool_list", page),
        'render_page': _("ai_tool_render", page),
        'view_attachment': _("ai_tool_attachment", arguments.get('attachment')),
        'add_elements': plural("ai_tool_add", count('added'), page),
        'edit_elements': plural("ai_tool_edit", count('edited'), page),
        'delete_elements': plural("ai_tool_delete", count('deleted'), page),
        'add_page': _("ai_tool_add_page", page),
        'delete_pages': _("ai_tool_delete_pages", ', '.join(str(p) for p in data.get('deleted', []))),
        'go_to_page': _("ai_tool_goto", data.get('current_page')),
        'new_document': _("ai_tool_new_document"),
        'list_fonts': _("ai_tool_fonts"),
        'measure_text': _("ai_tool_measure"),
    }.get(name, name)
    if data.get('error'):
        return text, data['error']
    return text, None


def selection_context(window, tools):
    if not window.doc:
        return 'No document is open.'
    parts = [f'The editor shows page {window.current_page_index + 1} of {window.doc.page_count}.']
    selected = []
    table = getattr(window, 'selected_table', None)
    for obj in (window.selected_text, window.selected_shape, window.selected_image,
                getattr(window, 'selected_stroke', None), *(table.objects if table else ())):
        if obj is not None and obj not in selected:
            selected.append(obj)
    ids = [tools.ident(obj) for obj in selected]
    forms = getattr(window, 'form_tools', None)
    if forms and not window.view_mode:
        ids += [f'f{field["xref"]}' for field in forms.interaction.fields()]
    if ids:
        parts.append('Selected elements on this page: ' + ', '.join(ids) + '.')
    return ' '.join(parts)


class AiBar:
    def __init__(self, window, surface):
        self.window = window
        self.conversation = Conversation()
        self.tools = DocumentTools(window, lambda: self.conversation.attachments)
        self.pending = []
        self.run = None
        self.steps = None          # (expander, box, count, issues) for the running request
        self.pending_reply = None  # latest assistant text; shown as the answer when the request ends
        self.collapsed = False
        self.docked = bool(get_setting('ai_docked', False))

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, halign=Gtk.Align.CENTER,
                            valign=Gtk.Align.END, margin_bottom=18, margin_start=16, margin_end=16, visible=False)
        self.root.add_css_class('pdflx-inspector')
        self.root.add_css_class('pdflx-ai-bar')
        self.root.set_size_request(640, -1)

        header = Gtk.Box(spacing=6, margin_start=14, margin_end=8, margin_top=8, margin_bottom=4)
        icon = Gtk.Image.new_from_icon_name('editor-ai-symbolic')
        icon.add_css_class('pdflx-ai-accent')
        header.append(icon)
        title = Gtk.Label(label=_("ai_title"), xalign=0)
        title.add_css_class('heading')
        header.append(title)
        self.spinner = Gtk.Spinner(visible=False)
        header.append(self.spinner)
        self.model_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self.model_label.add_css_class('dim-label')
        self.model_label.add_css_class('caption')
        header.append(self.model_label)
        self.header_buttons = {}
        for key, icon_name, tip, callback in (
                ('collapse', 'pan-down-symbolic', _("ai_collapse"), self.toggle_collapsed),
                ('dock', 'editor-dock-right-symbolic', _("ai_dock"), self.toggle_docked),
                ('new', 'list-add-symbolic', _("ai_new_chat"), self.new_chat),
                ('settings', 'emblem-system-symbolic', _("ai_settings"), self.open_settings),
                ('close', 'window-close-symbolic', _("btn_close"), self.hide)):
            button = Gtk.Button(icon_name=icon_name, tooltip_text=tip)
            button.add_css_class('flat')
            button.add_css_class('circular')
            button.connect('clicked', lambda _b, cb=callback: cb())
            header.append(button)
            self.header_buttons[key] = button
        self.root.append(header)

        self.log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                           margin_start=14, margin_end=14, margin_top=4, margin_bottom=8)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                           max_content_height=240, visible=False)
        self.scroller.set_child(self.log)
        self.root.append(self.scroller)

        self.strip = Gtk.Box(spacing=6, margin_start=14, margin_end=14, margin_bottom=6, visible=False)
        self.root.append(self.strip)

        row = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_bottom=8)
        attach = Gtk.Button(icon_name='mail-attachment-symbolic', tooltip_text=_("ai_attach"),
                            valign=Gtk.Align.END)
        attach.add_css_class('flat')
        attach.connect('clicked', lambda *_: self.choose_files())
        row.append(attach)
        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                  top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.input.add_css_class('pdflx-ai-input')
        self.input.update_property([Gtk.AccessibleProperty.LABEL], [_("ai_placeholder")])
        overlay = Gtk.Overlay()
        input_scroll = Gtk.ScrolledWindow(hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          propagate_natural_height=True, max_content_height=140)
        input_scroll.add_css_class('pdflx-textarea')
        input_scroll.set_child(self.input)
        overlay.set_child(input_scroll)
        self.placeholder = Gtk.Label(label=_("ai_placeholder"), xalign=0, valign=Gtk.Align.START,
                                     margin_start=12, margin_end=12, margin_top=8, can_target=False,
                                     wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, width_chars=10)
        self.placeholder.add_css_class('dim-label')
        overlay.add_overlay(self.placeholder)
        row.append(overlay)
        self.input.get_buffer().connect('changed', lambda *_: self._sync())
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.input.add_controller(keys)
        self.send = Gtk.Button(icon_name='go-up-symbolic', tooltip_text=_("ai_send"), valign=Gtk.Align.END)
        self.send.add_css_class('suggested-action')
        self.send.add_css_class('circular')
        self.send.connect('clicked', lambda *_: self.stop() if self.run else self.submit())
        row.append(self.send)
        self.root.append(row)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect('drop', self._on_drop)
        self.root.add_controller(drop)
        surface.add_overlay(self.root)
        surface.connect('notify::height', lambda *_: self._layout())
        self._layout()
        self._sync()

    # ------------------------------------------------------------ visibility
    def toggle(self, *args):
        self.hide() if self.root.get_visible() else self.show()

    def show(self):
        self.root.set_visible(True)
        self.window.ai_button.add_css_class('active')
        self._sync()
        self.input.grab_focus()
        if not config.api_key() or not config.model():
            self.open_settings()

    def hide(self):
        self.root.set_visible(False)
        self.window.ai_button.remove_css_class('active')
        self.window.pdf_view.grab_focus()

    def _sync(self):
        model = config.model()
        self.model_label.set_text(model or _("ai_no_model"))
        if self.conversation.tokens:
            cost = f' · ${self.conversation.cost:.4f}' if self.conversation.cost else ''
            self.model_label.set_tooltip_text(_("ai_usage", f'{self.conversation.tokens:,}') + cost)
        buffer = self.input.get_buffer()
        has_text = buffer.get_char_count() > 0
        self.placeholder.set_visible(not has_text)
        busy = self.run is not None
        self.send.set_icon_name('media-playback-stop-symbolic' if busy else 'go-up-symbolic')
        self.send.set_tooltip_text(_("ai_stop") if busy else _("ai_send"))
        self.send.set_sensitive(busy or has_text or bool(self.pending))
        self.spinner.set_visible(busy)
        self.spinner.set_spinning(busy)
        if not busy:
            self.model_label.set_text(model or _("ai_no_model"))
        self._layout()

    def _layout(self):
        """Floating bar at the bottom, or a side panel; the conversation never takes over the page."""
        surface = self.window.document_surface
        width, height = surface.get_width(), surface.get_height()
        has_log = self.log.get_first_child() is not None
        self.scroller.set_visible(has_log and not self.collapsed)
        if self.docked:
            self.root.set_halign(Gtk.Align.END)
            self.root.set_valign(Gtk.Align.FILL)
            self.root.set_margin_top(12)
            self.root.set_margin_bottom(12)
            self.root.set_margin_end(12)
            self.root.set_size_request(min(380, max(300, width - 32)) if width > 0 else 380, -1)
            self.scroller.set_vexpand(True)
            self.scroller.set_max_content_height(-1)
            self.scroller.set_propagate_natural_height(False)
        else:
            self.root.set_halign(Gtk.Align.CENTER)
            self.root.set_valign(Gtk.Align.END)
            self.root.set_margin_top(0)
            self.root.set_margin_bottom(18)
            self.root.set_margin_end(16)
            self.root.set_size_request(min(560, max(320, width - 32)) if width > 0 else 560, -1)
            self.scroller.set_vexpand(False)
            self.scroller.set_propagate_natural_height(True)
            # At most about a third of the view, so the page stays visible.
            self.scroller.set_max_content_height(max(120, int(height * 0.33)) if height > 0 else 240)
        collapse = self.header_buttons['collapse']
        collapse.set_visible(has_log)
        collapse.set_icon_name('pan-up-symbolic' if self.collapsed else 'pan-down-symbolic')
        collapse.set_tooltip_text(_("ai_expand") if self.collapsed else _("ai_collapse"))
        dock = self.header_buttons['dock']
        dock.set_icon_name('editor-undock-symbolic' if self.docked else 'editor-dock-right-symbolic')
        dock.set_tooltip_text(_("ai_undock") if self.docked else _("ai_dock"))

    def toggle_collapsed(self):
        self.collapsed = not self.collapsed
        self._layout()

    def toggle_docked(self):
        self.docked = not self.docked
        set_setting('ai_docked', self.docked)
        self._layout()

    # ------------------------------------------------------------ input
    def _on_key(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            if not self.run:
                self.submit()
            return True
        if keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and state & Gdk.ModifierType.CONTROL_MASK:
            clipboard = self.window.get_clipboard()
            if clipboard.get_formats().contain_gtype(Gdk.Texture):
                clipboard.read_texture_async(None, self._on_pasted_texture)
                return True
        return False

    def _on_pasted_texture(self, clipboard, result):
        try:
            texture = clipboard.read_texture_finish(result)
        except GLib.Error:
            return
        if texture:
            self.add_files([('pasted-image.png', texture.save_to_png_bytes().get_data())])

    def _on_drop(self, target, value, x, y):
        files = []
        for file in value.get_files():
            path = file.get_path()
            if path and os.path.isfile(path):
                files.append(path)
        self.add_paths(files)
        return True

    def choose_files(self):
        dialog = Gtk.FileDialog(title=_("ai_attach"))
        filters = Gio.ListStore.new(Gtk.FileFilter)
        accepted = Gtk.FileFilter(name=_("ai_attach_filter"))
        for pattern in ACCEPTED:
            accepted.add_pattern(pattern)
            accepted.add_pattern(pattern.upper())
        filters.append(accepted)
        dialog.set_filters(filters)
        def done(dialog, result):
            try:
                files = dialog.open_multiple_finish(result)
            except GLib.Error:
                return
            self.add_paths([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dialog.open_multiple(self.window, None, done)

    def add_paths(self, paths):
        items = []
        for path in paths:
            try:
                with open(path, 'rb') as handle:
                    items.append((os.path.basename(path), handle.read()))
            except OSError as error:
                self._message('error', str(error))
        self.add_files(items)

    def add_files(self, files):
        for name, data in files:
            try:
                self.pending.extend(load_attachment(name, data))
            except client.AiError as error:
                self._message('error', str(error))
        self._show_pending()
        self._sync()

    def _thumbnail(self, item, size=44):
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(item['bytes']))
        image = Gtk.Image.new_from_paintable(texture)
        image.set_pixel_size(size)
        image.add_css_class('pdflx-ai-thumb')
        image.set_tooltip_text(f'{item["name"]} · {item["width"]}x{item["height"]}')
        return image

    def _show_pending(self):
        while child := self.strip.get_first_child():
            self.strip.remove(child)
        for item in self.pending:
            chip = Gtk.Overlay()
            chip.set_child(self._thumbnail(item))
            remove = Gtk.Button(icon_name='window-close-symbolic', halign=Gtk.Align.END, valign=Gtk.Align.START,
                                tooltip_text=_("ai_remove_attachment"))
            remove.add_css_class('circular')
            remove.add_css_class('osd')
            remove.add_css_class('pdflx-ai-chip-remove')
            remove.connect('clicked', lambda _b, item=item: (self.pending.remove(item), self._show_pending(), self._sync()))
            chip.add_overlay(remove)
            self.strip.append(chip)
        self.strip.set_visible(bool(self.pending))

    # ------------------------------------------------------------ conversation
    def _message(self, kind, text, attachments=()):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                      halign=Gtk.Align.END if kind == 'user' else Gtk.Align.FILL)
        if attachments:
            thumbs = Gtk.Box(spacing=4, halign=Gtk.Align.END)
            for item in attachments:
                thumbs.append(self._thumbnail(item, 56))
            box.append(thumbs)
        if text:
            label = Gtk.Label(label=text, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                              selectable=kind in ('user', 'assistant', 'error'), max_width_chars=70, width_chars=10)
            label.add_css_class(f'pdflx-ai-{kind}')
            if kind == 'tool':
                label.add_css_class('dim-label')
                label.add_css_class('caption')
            if kind == 'error':
                label.add_css_class('error')
            box.append(label)
        self.log.append(box)
        self._layout()
        GLib.idle_add(self._scroll_to_end)
        return box

    def _scroll_to_end(self):
        adjustment = self.scroller.get_vadjustment()
        adjustment.set_value(adjustment.get_upper())
        return False

    def submit(self):
        buffer = self.input.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip()
        if self.run or not (text or self.pending):
            return
        if not config.api_key() or not config.model():
            self.open_settings()
            return
        attachments, self.pending = self.pending, []
        self._show_pending()
        buffer.set_text('')
        self._message('user', text, attachments)
        context = selection_context(self.window, self.tools)
        self.conversation.add_user(text or 'Use the attached reference.', attachments, context)
        self.run = AgentRun(self.conversation, self.tools, self._on_event)
        self.steps = None
        self.pending_reply = None
        self.collapsed = False
        self.run.start()
        self._sync()
        self.model_label.set_text(_("ai_working"))

    def _step(self, text, error=None, note=False):
        """Add a line to this request's collapsible step list."""
        if self.steps is None:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=18, margin_top=4)
            expander = Gtk.Expander(child=box)
            expander.add_css_class('pdflx-ai-steps')
            self.log.append(expander)
            self.steps = [expander, box, 0, 0]
            self._layout()
        expander, box, count, issues = self.steps
        label = Gtk.Label(label=(text if note else f'• {text}') + (f' — {error}' if error else ''), xalign=0,
                          wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=70, width_chars=10)
        label.add_css_class('caption')
        label.add_css_class('error' if error else 'dim-label')
        box.append(label)
        if not note:
            count += 1
        issues += bool(error)
        self.steps[2:] = [count, issues]
        title = _("ai_steps", count) if count != 1 else _("ai_steps_one")
        if issues:
            title += ' · ' + (_("ai_issues", issues) if issues != 1 else _("ai_issues_one"))
        expander.set_label(title)
        GLib.idle_add(self._scroll_to_end)

    def _on_event(self, kind, data):
        if kind == 'thinking':
            self.model_label.set_text(_("ai_working"))
        elif kind == 'tool':
            text, _error = tool_summary(data[0], data[1], '{}')
            self.model_label.set_text(text + '…')
        elif kind == 'tool_result':
            if self.pending_reply:
                # Text between tool calls is progress narration; keep it with the steps.
                self._step(self.pending_reply, note=True)
                self.pending_reply = None
            text, error = tool_summary(*data)
            self._step(text, error)
        elif kind == 'assistant':
            if self.pending_reply:
                self._step(self.pending_reply, note=True)
            self.pending_reply = data
        elif kind == 'error':
            self._message('error', data)
        elif kind == 'done':
            self.tools.end_turn()
            if self.pending_reply:
                self._message('assistant', self.pending_reply)
            self.pending_reply = None
            self.steps = None
            self.run = None
            self._sync()

    def stop(self):
        if self.run:
            self.run.cancel()
            self.model_label.set_text(_("ai_stopping"))

    def new_chat(self):
        if self.run:
            return
        self.conversation = Conversation()
        self.tools.attachments = lambda: self.conversation.attachments
        while child := self.log.get_first_child():
            self.log.remove(child)
        self.collapsed = False
        self.model_label.set_tooltip_text(None)
        self._sync()

    def open_settings(self):
        AiSettingsDialog(self.window, on_saved=self._sync).present(self.window)


class AiSettingsDialog:
    """API key, model id, endpoint and step limit."""
    def __new__(cls, window, on_saved=None):
        from ..dialogs import SheetDialog, FormRows

        class Dialog(SheetDialog):
            def __init__(self):
                super().__init__(window, _("ai_settings"), _("btn_save"), width=520)
                rows = FormRows(margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
                rows.heading(_("ai_settings_service"))
                self.key = Adw.PasswordEntryRow(title=_("ai_api_key"))
                if config.key_from_environment():
                    self.key.set_title(_("ai_api_key_env"))
                    self.key.set_sensitive(False)
                else:
                    self.key.set_text(config.api_key())
                rows.add(self.key)
                self.model = Adw.EntryRow(title=_("ai_model"), text=config.model())
                browse = Gtk.MenuButton(icon_name='view-list-symbolic', valign=Gtk.Align.CENTER,
                                        tooltip_text=_("ai_browse_models"))
                browse.add_css_class('flat')
                browse.set_popover(self._model_popover(browse))
                self.model.add_suffix(browse)
                rows.add(self.model)
                hint = Gtk.Label(label=_("ai_model_hint"), xalign=0, wrap=True, margin_start=4)
                hint.add_css_class('dim-label')
                hint.add_css_class('caption')
                rows.append(hint)
                rows.heading(_("ai_settings_advanced"))
                self.url = Adw.EntryRow(title=_("ai_base_url"), text=config.base_url())
                rows.add(self.url)
                self.steps = Adw.SpinRow.new_with_range(1, 200, 1)
                self.steps.set_title(_("ai_max_steps"))
                self.steps.set_subtitle(_("ai_max_steps_hint"))
                self.steps.set_value(config.max_steps())
                rows.add(self.steps)
                scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                            child=rows)
                self.set_body(scroll)

            def _model_popover(self, button):
                popover = Gtk.Popover()
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=6, margin_end=6,
                              margin_top=6, margin_bottom=6)
                search = Gtk.SearchEntry(placeholder_text=_("ai_search_models"))
                box.append(search)
                status = Gtk.Label(label=_("ai_loading_models"), xalign=0, wrap=True)
                status.add_css_class('dim-label')
                box.append(status)
                listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                listing.add_css_class('boxed-list')
                scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=320,
                                            min_content_width=420, child=listing)
                box.append(scroll)
                popover.set_child(box)
                state = {'loaded': False}

                def fill(models):
                    status.set_text(_("ai_models_hint"))
                    for model_id, name, prompt, completion in models:
                        price = (f'${prompt:.2f} / ${completion:.2f} per M tokens'
                                 if prompt is not None and completion is not None else '')
                        row = Adw.ActionRow(title=GLib.markup_escape_text(name),
                                            subtitle=GLib.markup_escape_text(f'{model_id}  {price}'), activatable=True)
                        row.model_id = model_id
                        row.search_text = f'{name} {model_id}'.lower()
                        listing.append(row)
                    return False

                def failed(message):
                    status.set_text(message)
                    return False

                def load():
                    try:
                        models = client.list_models(self.url.get_text().strip().rstrip('/') or config.DEFAULT_BASE_URL)
                    except client.AiError as error:
                        GLib.idle_add(failed, str(error))
                        return
                    GLib.idle_add(fill, models)

                def opened(*_args):
                    if not state['loaded']:
                        state['loaded'] = True
                        threading.Thread(target=load, daemon=True).start()
                    search.grab_focus()
                popover.connect('show', opened)
                listing.set_filter_func(lambda row: search.get_text().lower() in getattr(row, 'search_text', ''))
                search.connect('search-changed', lambda *_: listing.invalidate_filter())
                def chosen(_list, row):
                    self.model.set_text(row.model_id)
                    popover.popdown()
                listing.connect('row-activated', chosen)
                return popover

            def _on_apply_clicked(self, _button):
                model = self.model.get_text().strip()
                if not model:
                    self.error(_("ai_model_required"))
                    return
                config.save(model, self.url.get_text(), self.steps.get_value())
                if not config.key_from_environment():
                    config.set_api_key(self.key.get_text())
                if on_saved:
                    on_saved()
                self.force_close()

        return Dialog()
