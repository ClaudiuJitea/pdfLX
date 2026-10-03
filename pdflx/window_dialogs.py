"""Dialogs moved out of window.py; each takes the PdfEditorWindow as ``window``."""
import os
import threading

from gi.repository import Adw, Gtk, GLib

from . import document_features, pdf_handler
from .i18n import _
from .ui_components import show_error_dialog
from .dialogs import OperationDialog, SheetDialog, alert, dialog_title


def _prompt_pdf_password(window, filepath, target_page, session, incorrect=False):
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    rows.add_css_class('boxed-list')
    entry = Adw.PasswordEntryRow(title=_("pdf_password_title"))
    rows.append(entry)
    dialog = alert(window, _("pdf_password_title"),
                   _("pdf_password_wrong") if incorrect else _("pdf_password_prompt"),
                   [('cancel', _("btn_cancel"), None), ('open', _("btn_open_doc"), 'suggested')],
                   default='open', close='cancel', extra=rows,
                   callback=lambda response: on_response(response))
    entry.connect("entry-activated", lambda widget: (on_response('open'), dialog.close()))
    entry.grab_focus()
    answered = []

    def on_response(response):
        if answered:
            return
        answered.append(response)
        password = entry.get_text() if response == 'open' else None
        if password is None:
            if len(window.sessions) > 1 and session:
                window.remove_session(session)
            else:
                window.close_document()
            window.open_button.set_sensitive(True)
            window._update_ui_state()
            return

        def load_with_password():
            opened_doc, error = pdf_handler.load_pdf_document(filepath, password)
            GLib.idle_add(window._finish_loading, opened_doc, error, filepath, target_page, session)

        threading.Thread(target=load_with_password, daemon=True).start()


def _offer_merge_or_open(window, filepath):
    """Prompt whether to append dropped PDF pages or open as a separate document."""
    def on_response(response):
        if response == 'merge':
            window._merge_pdf_at_position(filepath, window.current_page_index + 1)
        elif response == 'open':
            GLib.idle_add(window.load_document, filepath, 0, True)

    alert(window, _("import_pdf_title"), _("import_pdf_confirm", os.path.basename(filepath)),
          [('open', _("btn_cancel"), None), ('merge', _("btn_confirm"), 'suggested')],
          default='merge', close='close', callback=on_response)


def on_document_properties(window, action=None, param=None):
    if not window.doc or window.view_mode or not window._active_session.can_edit:
        return
    doc = window.doc

    def save(dialog):
        if doc is not window.doc:
            return
        values = {key: entry.get_text() for key, entry in entries.items()}
        window._mutate_document(lambda: document_features.update_metadata(doc, values))
        window._update_ui_state()

    dialog = OperationDialog(window, _("menu_properties"), _("btn_save"), on_apply=save)
    group = dialog.group(_("metadata_group"))
    entries = {key: dialog.entry(group, _(f"metadata_{key}"), doc.metadata.get(key) or "")
               for key in document_features.METADATA_FIELDS}
    clear_group = dialog.group()
    clear = Adw.ButtonRow(title=_("clear_metadata")) if hasattr(Adw, 'ButtonRow') else None
    if clear is None:
        clear = Adw.ActionRow(title=_("clear_metadata"), activatable=True)
    clear.add_css_class('destructive-action')

    def clear_all(*_args):
        if doc is window.doc:
            window._mutate_document(lambda: document_features.clear_metadata(doc))
            window._update_ui_state()
        dialog.force_close()
    clear.connect('activated', clear_all)
    clear_group.add(clear)
    dialog.show()


def on_bookmarks(window, action=None, param=None):
    if not window.doc:
        return
    doc = window.doc
    editable = not window.view_mode and window._active_session.can_edit
    dialog = SheetDialog(window, _("menu_bookmarks"), width=520, height=560)
    dialog.set_informational()
    dialog.cancel_button.set_label(_("btn_done"))
    page = Adw.PreferencesPage()
    dialog.set_body(page)

    add_group = Adw.PreferencesGroup(title=_("bookmark_new"), sensitive=editable)
    title_entry = Adw.EntryRow(title=_("bookmark_title_placeholder"))
    title_entry.set_text(_("form_page_label", window.current_page_index + 1))
    add_group.add(title_entry)
    page_spin = Adw.SpinRow.new_with_range(1, doc.page_count, 1)
    page_spin.set_title(_("bookmark_page_label"))
    page_spin.set_value(window.current_page_index + 1)
    page_spin.set_tooltip_text(_("bookmark_page_tip"))
    add_group.add(page_spin)
    add_button = Gtk.Button(label=_("bookmark_add"), valign=Gtk.Align.CENTER)
    add_button.add_css_class('suggested-action')
    add_group.set_header_suffix(add_button)
    page.add(add_group)

    list_group = Adw.PreferencesGroup(title=dialog_title(_("menu_bookmarks")), description=_("bookmark_hint"))
    page.add(list_group)
    tools_group = Adw.PreferencesGroup(sensitive=editable)
    generate = Adw.ActionRow(title=dialog_title(_("menu_generate_bookmarks")), activatable=True)
    generate.add_prefix(Gtk.Image(icon_name='editor-outline-symbolic'))
    generate.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
    generate.connect('activated', lambda row: (dialog.force_close(),
                                              window.lookup_action('generate_bookmarks').activate(None)))
    tools_group.add(generate)
    page.add(tools_group)
    shown = []

    def refresh():
        dialog.set_focus(None)
        for row in shown:
            list_group.remove(row)
        shown.clear()
        toc = doc.get_toc()
        if not toc:
            empty = Adw.ActionRow(title=_("bookmark_none"))
            empty.add_css_class('dim-label')
            list_group.add(empty)
            shown.append(empty)
        for index, (level, title, target, *details) in enumerate(toc):
            row = Adw.ActionRow(title=title, subtitle=_("form_page_label", target), use_markup=False,
                                activatable=target > 0)
            row.add_prefix(Gtk.Image(icon_name='editor-bookmark-symbolic', margin_start=(level - 1) * 18))

            def navigate(_row, target=target):
                if doc is window.doc and target > 0:
                    window._load_page(target - 1)
                    dialog.force_close()
            row.connect('activated', navigate)
            tools = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER, visible=editable)
            for icon, tip, change in (('go-previous-symbolic', 'bookmark_outdent', ('level', -1)),
                                      ('go-next-symbolic', 'bookmark_indent', ('level', 1)),
                                      ('go-up-symbolic', 'bookmark_move_up', ('move', -1)),
                                      ('go-down-symbolic', 'bookmark_move_down', ('move', 1))):
                arrange = Gtk.Button(icon_name=icon, tooltip_text=_(tip))
                arrange.add_css_class('flat')
                arrange.connect('clicked', lambda button, index=index, change=change: restructure(index, *change))
                tools.append(arrange)
            rename = Gtk.Button(icon_name='document-edit-symbolic', tooltip_text=_("bookmark_rename"))
            rename.add_css_class('flat')
            rename.connect('clicked', lambda button, index=index, title=title: rename_bookmark(index, title))
            tools.append(rename)
            delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text=_("bookmark_remove"))
            delete.add_css_class('flat')
            delete.add_css_class('error')
            delete.connect("clicked", lambda button, index=index: remove(index))
            tools.append(delete)
            row.add_suffix(tools)
            list_group.add(row)
            shown.append(row)

    def rename_bookmark(index, title):
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class('boxed-list')
        entry = Adw.EntryRow(title=_("bookmark_title_placeholder"))
        entry.set_text(title)
        rows.append(entry)

        def response(answer):
            value = entry.get_text().strip()
            if answer == 'save' and value and doc is window.doc:
                window._mutate_document(lambda: doc.set_toc_item(index, title=value))
                refresh()
        alert(dialog, _("bookmark_rename"), '', [('cancel', _("btn_cancel"), None), ('save', _("btn_save"), 'suggested')],
              default='save', close='cancel', extra=rows, callback=response)

    def restructure(index, kind, delta):
        if doc is not window.doc:
            return
        try:
            if kind == 'level':
                window._mutate_document(lambda: document_features.shift_bookmark_level(doc, index, delta))
            else:
                window._mutate_document(lambda: document_features.move_bookmark(doc, index, delta))
            refresh()
        except (ValueError, IndexError) as error:
            from .dialogs import toast
            toast(window, str(error))

    def remove(index):
        if doc is not window.doc:
            return
        try:
            window._mutate_document(lambda: document_features.remove_bookmark(doc, index))
            window._update_ui_state()
            refresh()
        except Exception as error:
            show_error_dialog(window, str(error), _("menu_bookmarks"))

    def add(button):
        if doc is not window.doc:
            return
        try:
            title, page = title_entry.get_text(), int(page_spin.get_value()) - 1
            window._mutate_document(lambda: document_features.add_bookmark(doc, title, page))
            title_entry.set_text("")
            window._update_ui_state()
            refresh()
        except Exception as error:
            show_error_dialog(window, str(error), _("menu_bookmarks"))

    add_button.connect("clicked", add)
    title_entry.connect("entry-activated", add)
    refresh()
    dialog.present(window)
