"""Adwaita dialog toolkit shared by document operations.

``OperationDialog`` is a modal sheet with a header bar (Cancel / primary
action), grouped preference rows, an inline error banner, an optional preview,
and a progress bar with cancellation for work that runs in a thread.
"""
import os
import threading

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk

from .i18n import _
from .ops.common import Cancelled, OperationError, parse_page_ranges, format_page_ranges


def toast(window, text, timeout=4, button_label=None, callback=None):
    """Show a transient notification, falling back to the status bar."""
    overlay = getattr(window, 'toast_overlay', None)
    if overlay is None:
        label = getattr(window, 'status_label', None)
        if label is not None:
            label.set_text(text)
        return
    item = Adw.Toast(title=GLib.markup_escape_text(text), timeout=timeout)
    if button_label and callback:
        item.set_button_label(button_label)
        item.connect('button-clicked', lambda *_args: callback())
    overlay.add_toast(item)


def alert(window, heading, body, responses, default=None, close='cancel', callback=None, extra=None):
    """Asynchronous Adw.AlertDialog. ``responses`` is a list of (id, label, appearance)
    where appearance is None, 'suggested', or 'destructive'."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    for response_id, label, appearance in responses:
        dialog.add_response(response_id, label)
        if appearance == 'suggested':
            dialog.set_response_appearance(response_id, Adw.ResponseAppearance.SUGGESTED)
        elif appearance == 'destructive':
            dialog.set_response_appearance(response_id, Adw.ResponseAppearance.DESTRUCTIVE)
    if default:
        dialog.set_default_response(default)
    if close:
        dialog.set_close_response(close)
    if extra is not None:
        dialog.set_extra_child(extra)
    if callback:
        dialog.connect('response', lambda _dialog, response: callback(response))
    dialog.present(window)
    return dialog


def ask(window, heading, body, responses, default=None, close='cancel', extra=None):
    """Blocking variant of ``alert`` for call sites that need an answer inline."""
    result = []
    alert(window, heading, body, responses, default, close, callback=result.append, extra=extra)
    context = GLib.MainContext.default()
    while not result:
        context.iteration(True)
    return result[0]


def message(window, heading, body):
    alert(window, heading, body, [('close', _('btn_close'), None)], 'close', 'close')


def file_filter(name, patterns=(), mime_types=()):
    item = Gtk.FileFilter(name=name)
    for pattern in patterns:
        item.add_pattern(pattern)
        if pattern.startswith('*.') and pattern[2:].isalpha():
            item.add_pattern('*.' + pattern[2:].upper())
    for mime in mime_types:
        item.add_mime_type(mime)
    return item


def pdf_filter():
    return file_filter(_('filter_pdf'), ('*.pdf',), ('application/pdf',))


def _filters_model(filters):
    store = Gio.ListStore.new(Gtk.FileFilter)
    for item in filters or ():
        store.append(item)
    return store


def choose_files(window, title, filters=None, multiple=False, callback=None):
    """Open one or more files; callback receives a list of paths (empty if cancelled)."""
    dialog = Gtk.FileDialog(title=title, modal=True)
    if filters:
        dialog.set_filters(_filters_model(filters))
        dialog.set_default_filter(filters[0])

    def finished(source, result):
        paths = []
        try:
            if multiple:
                files = source.open_multiple_finish(result)
                paths = [files.get_item(i).get_path() for i in range(files.get_n_items())]
            else:
                item = source.open_finish(result)
                paths = [item.get_path()] if item else []
        except GLib.Error:
            paths = []
        if callback:
            callback([p for p in paths if p])

    if multiple:
        dialog.open_multiple(window, None, finished)
    else:
        dialog.open(window, None, finished)


def choose_save(window, title, initial_name, filters=None, callback=None):
    dialog = Gtk.FileDialog(title=title, modal=True, initial_name=initial_name)
    if filters:
        dialog.set_filters(_filters_model(filters))
        dialog.set_default_filter(filters[0])

    def finished(source, result):
        try:
            item = source.save_finish(result)
        except GLib.Error:
            item = None
        if callback and item is not None and item.get_path():
            callback(item.get_path())

    dialog.save(window, None, finished)


def choose_folder(window, title, callback=None):
    dialog = Gtk.FileDialog(title=title, modal=True)

    def finished(source, result):
        try:
            item = source.select_folder_finish(result)
        except GLib.Error:
            item = None
        if callback and item is not None and item.get_path():
            callback(item.get_path())

    dialog.select_folder(window, None, finished)


def write_file_atomic(path, data):
    temporary = f'{path}.pdflx-part'
    try:
        mode = 'w' if isinstance(data, str) else 'wb'
        with open(temporary, mode, **({'encoding': 'utf-8'} if mode == 'w' else {})) as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


class ComboRow(Adw.ComboRow):
    """ComboRow over (key, label) pairs exposing the selected key."""

    def __init__(self, title, options, selected=None, subtitle=None):
        super().__init__(title=title)
        if subtitle:
            self.set_subtitle(subtitle)
        self.keys = [key for key, _label in options]
        self.set_model(Gtk.StringList.new([label for _key, label in options]))
        if selected in self.keys:
            self.set_selected(self.keys.index(selected))

    @property
    def key(self):
        index = self.get_selected()
        return self.keys[index] if 0 <= index < len(self.keys) else None

    def set_key(self, key):
        if key in self.keys:
            self.set_selected(self.keys.index(key))


class PageRangeRow(Adw.EntryRow):
    """Entry for '1-3, 5' style ranges with All / Current shortcuts and validation."""

    def __init__(self, title, page_count, current=0, default='all'):
        super().__init__(title=title)
        self.page_count = page_count
        self.current = current
        for label, value in ((_('range_all'), ''), (_('range_current'), str(current + 1))):
            button = Gtk.Button(label=label, valign=Gtk.Align.CENTER)
            button.add_css_class('flat')
            button.connect('clicked', lambda _b, value=value: self.set_text(value))
            self.add_suffix(button)
        self.set_text('' if default == 'all' else str(current + 1) if default == 'current' else default)
        self.connect('changed', self._validate)
        self._validate()

    def _validate(self, *_args):
        try:
            pages = self.pages()
            self.remove_css_class('error')
            self.set_tooltip_text(_('range_selected', len(pages)))
        except OperationError as error:
            self.add_css_class('error')
            self.set_tooltip_text(str(error))

    def pages(self):
        return parse_page_ranges(self.get_text(), self.page_count)


class FileRow(Adw.ActionRow):
    """Row with a button that picks one or more files and shows the choice."""

    def __init__(self, window, title, filters=None, multiple=False, on_change=None, subtitle=None):
        super().__init__(title=title, subtitle=subtitle or _('file_none_selected'))
        self.window = window
        self.filters = filters
        self.multiple = multiple
        self.paths = []
        self.on_change = on_change
        button = Gtk.Button(label=_('btn_choose'), valign=Gtk.Align.CENTER)
        button.connect('clicked', self._choose)
        self.add_suffix(button)
        self.set_activatable_widget(button)

    def _choose(self, _button):
        def chosen(paths):
            if not paths:
                return
            self.paths = paths
            names = ', '.join(os.path.basename(p) for p in paths[:3])
            if len(paths) > 3:
                names += f' (+{len(paths) - 3})'
            self.set_subtitle(names)
            if self.on_change:
                self.on_change(paths)
        choose_files(self.window, self.get_title(), self.filters, self.multiple, chosen)

    @property
    def path(self):
        return self.paths[0] if self.paths else None


def dialog_title(title):
    """Menu labels end with an ellipsis; dialog titles should not."""
    return str(title).rstrip('…').rstrip('.').strip()


class ToggleSwitch(Gtk.Switch):
    """Switch that also emits ``toggled`` so it can replace a Gtk.CheckButton."""
    __gsignals__ = {'toggled': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, **kwargs):
        super().__init__(valign=Gtk.Align.CENTER, **kwargs)
        self.connect('notify::active', lambda *_args: self.emit('toggled'))


class PreviewPane(Gtk.Box):
    """Live preview column shown beside a dialog's settings."""

    def __init__(self, refresh=None, width=320):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=width, hexpand=False)
        self.add_css_class('pdflx-preview-pane')
        header = Gtk.Box(spacing=6)
        title = Gtk.Label(label=_('tool_preview'), xalign=0, hexpand=True)
        title.add_css_class('pdflx-section-title')
        header.append(title)
        self.refresh_button = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text=_('preview_refresh'),
                                         visible=refresh is not None)
        self.refresh_button.add_css_class('flat')
        self.refresh_button.add_css_class('circular')
        if refresh is not None:
            self.refresh_button.connect('clicked', lambda _button: refresh())
        header.append(self.refresh_button)
        self.append(header)
        self.stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.stage.add_css_class('pdflx-preview-stage')
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, vexpand=True,
                                   height_request=220)
        self.stage.append(self.picture)
        self.append(self.stage)
        self.caption = Gtk.Label(xalign=0, wrap=True, visible=False)
        self.caption.add_css_class('caption')
        self.caption.add_css_class('dim-label')
        self.append(self.caption)

    def add(self, widget):
        """Show a custom widget in the stage instead of the default picture."""
        self.picture.set_visible(False)
        self.stage.append(widget)

    def set_caption(self, text):
        self.caption.set_text(text or '')
        self.caption.set_visible(bool(text))


def watch_changes(widget, callback):
    """Call ``callback`` whenever an input widget's value changes."""
    for kind, signal in ((Gtk.SpinButton, 'value-changed'), (Adw.SpinRow, 'notify::value'),
                         (Adw.ComboRow, 'notify::selected'), (Gtk.DropDown, 'notify::selected'),
                         (Gtk.Switch, 'notify::active'), (Adw.SwitchRow, 'notify::active'),
                         (Gtk.CheckButton, 'toggled'), (Gtk.ToggleButton, 'toggled'),
                         (Gtk.ColorDialogButton, 'notify::rgba'), (Gtk.ColorButton, 'color-set'),
                         (Gtk.TextBuffer, 'changed'), (Gtk.Editable, 'changed'), (Gtk.Range, 'value-changed')):
        if isinstance(widget, kind):
            widget.connect(signal, lambda *_args: callback())
            return True
    return False


def _follow(control, row):
    """Hide or disable the whole row together with its control."""
    if control is row:
        return
    row.set_visible(control.get_visible())
    row.set_sensitive(control.get_sensitive())
    control.connect('notify::visible', lambda widget, _pspec: row.set_visible(widget.get_visible()))
    control.connect('notify::sensitive', lambda widget, _pspec: row.set_sensitive(widget.get_sensitive()))


class FormRows(Gtk.Box):
    """Label/control pairs rendered as Adwaita boxed-list rows.

    Keeps the small part of the Gtk.Grid API older dialogs relied on:
    ``attach`` for free-form widgets and ``get_child_at`` where column 0 is the
    row (hiding it hides the caption and control) and column 1 the control.
    """

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, **kwargs)
        self.add_css_class('pdflx-form')
        self.cells = {}
        self.list = None
        self.count = 0

    def _list(self):
        if self.list is None:
            self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            self.list.add_css_class('boxed-list')
            self.append(self.list)
        return self.list

    def heading(self, text):
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class('pdflx-section-title')
        if self.get_first_child() is not None:
            label.set_margin_top(14)
        self.append(label)
        self.list = None
        return label

    def add(self, row, control=None, index=None):
        """Append a row; register it at ``index`` (default: the next grid row)."""
        index = self.count if index is None else index
        self._list().append(row)
        control = row if control is None else control
        self.cells[(0, index)] = row
        self.cells[(1, index)] = control
        self.count = max(self.count, index + 1)
        _follow(control, row)
        return row

    def attach(self, widget, column, row, width=1, height=1):
        if isinstance(widget, Gtk.Label):
            widget.set_wrap(True)
            widget.set_xalign(0)
            widget.add_css_class('dim-label')
            widget.add_css_class('caption')
            widget.set_margin_start(4)
            self.append(widget)
            self.list = None
        elif isinstance(widget, Gtk.Button):
            widget.set_halign(Gtk.Align.START)
            widget.add_css_class('pill')
            widget.set_margin_top(4)
            self.append(widget)
            self.list = None
        else:
            holder = Gtk.ListBoxRow(activatable=False, child=widget)
            for side in ('start', 'end', 'top', 'bottom'):
                getattr(widget, 'set_margin_' + side)(12 if side in ('start', 'end') else 8)
            self._list().append(holder)
            _follow(widget, holder)
        for cell in range(column, column + width):
            self.cells[(cell, row)] = widget
        self.count = max(self.count, row + 1)

    def get_child_at(self, column, row):
        return self.cells.get((column, row))


class SheetDialog(Adw.Dialog):
    """The one dialog shape used across pdfLX.

    A header bar with Cancel on the left, the title in the middle and the
    primary action on the right; an inline error banner; scrollable content.
    """

    def __init__(self, window, title, apply_label=None, destructive=False, width=560, height=-1):
        super().__init__(title=dialog_title(title), content_width=width)
        if height > 0:
            self.set_content_height(height)
        self.window = window
        self.add_css_class('pdflx-sheet')
        self.view = Adw.ToolbarView()
        self.header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        self.cancel_button = Gtk.Button(label=_('btn_cancel'))
        self.cancel_button.connect('clicked', self._on_cancel)
        self.header.pack_start(self.cancel_button)
        self.apply_button = Gtk.Button(label=apply_label or _('btn_apply'))
        self.apply_button.add_css_class('destructive-action' if destructive else 'suggested-action')
        self.apply_button.connect('clicked', self._on_apply_clicked)
        self.header.pack_end(self.apply_button)
        self.view.add_top_bar(self.header)
        self.banner = Adw.Banner(revealed=False)
        self.banner.set_button_label(_('btn_dismiss'))
        self.banner.connect('button-clicked', lambda banner: banner.set_revealed(False))
        self.view.add_top_bar(self.banner)
        self.set_child(self.view)
        self.set_default_widget(self.apply_button)

    def set_body(self, widget, preview=None):
        """Place ``widget`` as content, with an optional preview column beside it."""
        self.view.set_content(None)
        for child in (widget, preview):
            parent = child.get_parent() if child is not None else None
            if parent is not None:
                parent.remove(child)
        if preview is None:
            self.view.set_content(widget)
            return
        split = Gtk.Box()
        widget.set_hexpand(True)
        split.append(widget)
        split.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        split.append(preview)
        self.view.set_content(split)

    def set_informational(self):
        """Dialogs without an action offer Close instead of Cancel."""
        self.apply_button.set_visible(False)
        self.cancel_button.set_label(_('btn_close'))

    def error(self, text):
        self.banner.set_title(GLib.markup_escape_text(str(text)))
        self.banner.set_revealed(True)

    def clear_error(self):
        self.banner.set_revealed(False)

    def _on_apply_clicked(self, _button):
        self.force_close()

    def _on_cancel(self, _button):
        self.force_close()


class OperationDialog(SheetDialog):
    def __init__(self, window, title, apply_label=None, destructive=False, width=560, height=-1,
                 on_apply=None, preview=None):
        super().__init__(window, title, apply_label, destructive, width + (340 if preview else 0), height)
        self.on_apply = on_apply
        self.preview_callback = preview
        self.preview_timer = None
        self.cancel_requested = False
        self.busy = False

        self.page = Adw.PreferencesPage()
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.page)
        self.page.set_vexpand(True)

        self.progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                    margin_start=18, margin_end=18, margin_bottom=12, visible=False)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress_box.append(self.progress)
        body.append(self.progress_box)

        self.preview_pane = None
        if preview is not None:
            self.preview_pane = PreviewPane(self.refresh_preview)
            self.picture = self.preview_pane.picture
        self.set_body(body, self.preview_pane)
        self.connect('closed', self._on_closed)

    def _track(self, widget):
        """Refresh the live preview shortly after any setting changes."""
        if self.preview_callback is not None:
            watch_changes(widget, self.schedule_preview)
        return widget

    def schedule_preview(self):
        if self.preview_callback is None:
            return
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)

        def fire():
            self.preview_timer = None
            self.refresh_preview()
            return False
        self.preview_timer = GLib.timeout_add(450, fire)

    # -- rows ---------------------------------------------------------------
    def group(self, title=None, description=None):
        group = Adw.PreferencesGroup()
        if title:
            group.set_title(GLib.markup_escape_text(title))
        if description:
            group.set_description(GLib.markup_escape_text(description))
        # Keep the preview group last.
        self.page.add(group)
        return group

    def entry(self, group, title, text=''):
        row = Adw.EntryRow(title=title)
        row.set_text(text or '')
        group.add(row)
        return self._track(row)

    def password(self, group, title):
        row = Adw.PasswordEntryRow(title=title)
        group.add(row)
        return row

    def spin(self, group, title, value, low, high, step=1, digits=0, subtitle=None):
        row = Adw.SpinRow.new_with_range(low, high, step)
        row.set_title(title)
        row.set_digits(digits)
        row.set_value(value)
        if subtitle:
            row.set_subtitle(subtitle)
        # Match Gtk.SpinButton so callers can use either widget.
        row.get_value_as_int = lambda: int(round(row.get_value()))
        group.add(row)
        return self._track(row)

    def switch(self, group, title, active=False, subtitle=None):
        row = Adw.SwitchRow(title=title, active=active)
        if subtitle:
            row.set_subtitle(subtitle)
        group.add(row)
        return self._track(row)

    def check(self, group, title, active=False, subtitle=None):
        row = Adw.ActionRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        button = ToggleSwitch(active=active)
        row.add_suffix(button)
        row.set_activatable_widget(button)
        group.add(row)
        return self._track(button)

    def combo(self, group, title, options, selected=None, subtitle=None):
        row = ComboRow(title, options, selected, subtitle)
        group.add(row)
        return self._track(row)

    def pages(self, group, title=None, default='all'):
        doc = self.window.doc
        row = PageRangeRow(title or _('range_pages'), doc.page_count, self.window.current_page_index, default)
        group.add(row)
        return self._track(row)

    def file(self, group, title, filters=None, multiple=False, on_change=None, subtitle=None):
        row = FileRow(self.window, title, filters, multiple, on_change, subtitle)
        group.add(row)
        return row

    def color(self, group, title, rgb=(0, 0, 0)):
        row = Adw.ActionRow(title=title)
        button = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue, rgba.alpha = rgb[0], rgb[1], rgb[2], 1
        button.set_rgba(rgba)
        row.add_suffix(button)
        group.add(row)
        button.rgb = lambda: (button.get_rgba().red, button.get_rgba().green, button.get_rgba().blue)
        button.row = row
        return self._track(button)

    def info(self, group, title, value='', copyable=False):
        row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(str(value)))
        row.add_css_class('property')
        row.set_subtitle_selectable(True)
        group.add(row)
        return row

    def text_view(self, group, text='', height=160, editable=False, monospace=False):
        view = Gtk.TextView(editable=editable, cursor_visible=editable, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=10, right_margin=10, monospace=monospace)
        view.get_buffer().set_text(text)
        scroll = Gtk.ScrolledWindow(min_content_height=height, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class('pdflx-textarea')
        scroll.set_child(view)
        group.add(scroll)
        self._track(view.get_buffer())
        return view

    # -- behaviour ------------------------------------------------------------
    def refresh_preview(self):
        if self.preview_callback is None or self.cancel_requested:
            return
        try:
            png = self.preview_callback()
            if png:
                self.picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(png)))
            self.clear_error()
        except Exception as error:  # a preview failure must never break the dialog
            self.error(error)

    def _on_apply_clicked(self, _button):
        if self.busy or self.on_apply is None:
            return
        self.clear_error()
        try:
            if self.on_apply(self) is not False and not self.busy:
                self.force_close()
        except (OperationError, ValueError, RuntimeError, OSError) as error:
            self.error(error)

    def _on_cancel(self, _button):
        if self.busy:
            self.cancel_requested = True
            self.cancel_button.set_sensitive(False)
            self.progress.set_text(_('progress_cancelling'))
            return
        self.force_close()

    def _on_closed(self, *_args):
        self.cancel_requested = True
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
            self.preview_timer = None

    def run(self, work, done=None, label=None):
        """Run ``work(progress, cancel)`` in a thread; ``done(result)`` runs on the main loop.

        ``work`` must not touch GTK or the live document unless it owns a copy.
        The dialog closes when ``done`` returns anything but False.
        """
        self.busy = True
        self.cancel_requested = False
        self.apply_button.set_sensitive(False)
        self.page.set_sensitive(False)
        self.progress_box.set_visible(True)
        self.progress.set_fraction(0)
        self.progress.set_text(label or _('progress_working'))

        def progress(done_count, total):
            fraction = done_count / total if total else 0
            GLib.idle_add(self._progress, fraction, done_count, total)

        def cancel():
            return self.cancel_requested

        def thread():
            try:
                result, failure = work(progress, cancel), None
            except Cancelled:
                result, failure = None, 'cancelled'
            except Exception as error:  # reported in the dialog, not swallowed
                result, failure = None, error
            GLib.idle_add(self._finish, result, failure, done)

        threading.Thread(target=thread, daemon=True).start()

    def _progress(self, fraction, done_count, total):
        self.progress.set_fraction(fraction)
        if not self.cancel_requested:
            self.progress.set_text(_('progress_count', done_count, total))
        return False

    def _finish(self, result, failure, done):
        self.busy = False
        self.apply_button.set_sensitive(True)
        self.cancel_button.set_sensitive(True)
        self.page.set_sensitive(True)
        self.progress_box.set_visible(False)
        if failure == 'cancelled':
            self.error(_('progress_cancelled'))
            return False
        if failure is not None:
            self.error(failure)
            return False
        try:
            if done is None or done(result) is not False:
                self.force_close()
        except (OperationError, ValueError, RuntimeError, OSError) as error:
            self.error(error)
        return False

    def show(self):
        # Dialogs without an action are informational: offer Close instead of Cancel.
        self.apply_button.set_visible(self.on_apply is not None)
        if self.on_apply is None:
            self.set_informational()
        self.present(self.window)
        if self.preview_callback is not None:
            GLib.idle_add(lambda: (self.refresh_preview(), False)[1])
        return self


def render_preview(doc, page_number, scale=0.5):
    """PNG bytes for a page preview of a scratch document."""
    import pymupdf as fitz
    page_number = max(0, min(page_number, doc.page_count - 1))
    pix = doc[page_number].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return pix.tobytes('png')


def selected_range_text(pages):
    return format_page_ranges(pages)
