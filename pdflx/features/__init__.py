"""Feature controller: registers document-operation actions and builds menus.

Dialog implementations live in sibling modules grouped by menu area. This
keeps new features out of ``window.py``; the window only creates the
controller, calls ``update_state`` from ``_update_ui_state``, and asks it for
the main menu model.
"""
import os

from gi.repository import Gio, GLib

from ..i18n import _
from .. import pdf_handler
from ..dialogs import toast, choose_save, write_file_atomic, pdf_filter, message
from ..ops.common import OperationError, document_bytes, mark_new_document

# Enable rules: 'always', 'doc', 'pages', 'copy' (pages + copy permission),
# 'edit' (pages + Edit mode + modify permission), 'print'.
ACTIONS = (
    # File
    ('open_convert', 'always', 'convert', 'open_other_format'),
    ('images_to_pdf', 'always', 'convert', 'images_to_pdf'),
    ('save_incremental', 'doc', 'protect', 'save_incremental'),
    ('document_info', 'pages', 'document', 'document_info'),
    ('viewer_settings', 'edit', 'document', 'viewer_settings'),
    # Pages
    ('insert_pages', 'edit', 'pages', 'insert_pages'),
    ('extract_pages', 'copy', 'pages', 'extract_pages'),
    ('split_document', 'copy', 'pages', 'split_document'),
    ('reverse_pages', 'edit', 'pages', 'reverse_pages'),
    ('collate_pages', 'edit', 'pages', 'collate_pages'),
    ('remove_blank_pages', 'edit', 'pages', 'remove_blank_pages'),
    ('remove_rotation', 'edit', 'pages', 'remove_rotation'),
    ('resize_pages', 'copy', 'pages', 'resize_pages'),
    ('nup_pages', 'copy', 'pages', 'nup_pages'),
    ('overlay_pages', 'edit', 'pages', 'overlay_pages'),
    ('headers_footers', 'edit', 'pages', 'headers_footers'),
    ('page_labels', 'edit', 'pages', 'page_labels'),
    ('page_boxes', 'edit', 'pages', 'page_boxes'),
    ('form_structure', 'pages', 'forms', 'form_structure'),
    ('form_export_data', 'pages', 'forms', 'export_data'),
    ('form_import_data', 'pages', 'forms', 'import_data'),
    # Annotate
    ('annot_add', 'edit', 'review', 'add_markup'),
    ('annot_export', 'pages', 'review', 'export_annotations'),
    ('annot_import', 'edit', 'review', 'import_annotations'),
    ('comment_summary', 'pages', 'review', 'comment_summary'),
    ('flatten_annotations', 'edit', 'review', 'flatten_annotations'),
    # Navigation
    ('generate_bookmarks', 'edit', 'document', 'generate_bookmarks'),
    ('links', 'pages', 'document', 'links'),
    ('attachments', 'pages', 'document', 'attachments'),
    ('layers', 'pages', 'document', 'layers'),
    # Protect
    ('security', 'pages', 'protect', 'security'),
    ('sanitize', 'pages', 'protect', 'sanitize'),
    ('hidden_text', 'pages', 'protect', 'hidden_text'),
    ('rasterize', 'copy', 'protect', 'rasterize'),
    ('verify_signatures', 'doc', 'protect', 'verify_signatures'),
    # Tools
    ('optimize', 'copy', 'convert', 'optimize'),
    ('recolor', 'copy', 'convert', 'recolor'),
    ('ocr', 'pages', 'convert', 'ocr'),
    ('compare', 'copy', 'document', 'compare'),
    ('advanced_search', 'copy', 'document', 'advanced_search'),
    ('text_style', 'copy', 'document', 'text_style'),
    ('snapshot', 'copy', 'document', 'snapshot'),
    ('export_diagram', 'copy', 'graphics', 'export_diagram'),
    ('export_vector_svg', 'copy', 'graphics', 'export_vector_svg'),
    ('images_manager', 'pages', 'graphics', 'images_manager'),
    ('compose_document', 'always', 'authoring', 'compose_document'),
    ('rich_text', 'edit', 'authoring', 'rich_text'),
    ('watermark_text', 'edit', 'authoring', 'watermark_text'),
    ('mail_merge', 'copy', 'authoring', 'mail_merge'),
    ('batch_process', 'always', 'batch', 'batch_process'),
    ('draw_shape', 'edit', 'drawing', 'draw_shape'),
    ('node_editor', 'edit', 'drawing', 'node_editor'),
    ('attach_einvoice', 'edit', 'advanced', 'attach_einvoice'),
    ('create_portfolio', 'always', 'advanced', 'create_portfolio'),
    ('object_inspector', 'pages', 'advanced', 'object_inspector'),
    # View
    ('presentation', 'pages', 'viewer', 'presentation'),
    ('measure', 'pages', 'viewer', 'start_measure'),
    ('measure_settings', 'always', 'viewer', 'measure_settings'),
    ('side_viewer', 'pages', 'viewer', 'side_viewer'),
    # Export
    ('export_images', 'copy', 'convert', 'export_images'),
    ('export_structured', 'copy', 'convert', 'export_structured'),
    ('export_tables', 'copy', 'convert', 'export_tables'),
)


def _section(*items):
    menu = Gio.Menu()
    for label, action in items:
        menu.append(label, f'win.{action}')
    return menu


def _edit_mode_section():
    """'Switch to Edit Mode' entry shown only while editing actions are unavailable."""
    menu = Gio.Menu()
    item = Gio.MenuItem.new(_('menu_enter_edit_mode'), 'win.enter_edit_mode')
    item.set_attribute_value('hidden-when', GLib.Variant.new_string('action-disabled'))
    menu.append_item(item)
    return menu


def _submenu(*sections):
    menu = Gio.Menu()
    for section in sections:
        menu.append_section(None, section)
    return menu


class FeatureController:
    def __init__(self, window):
        self.window = window
        from . import (pages_ui, protect_ui, convert_ui, review_ui, document_ui, forms_ui, graphics_ui,
                       authoring_ui, batch_ui, viewer_ui, advanced_ui, drawing_ui)
        self.modules = {'pages': pages_ui, 'protect': protect_ui, 'convert': convert_ui,
                        'review': review_ui, 'document': document_ui, 'forms': forms_ui,
                        'graphics': graphics_ui, 'authoring': authoring_ui, 'batch': batch_ui,
                        'viewer': viewer_ui, 'advanced': advanced_ui, 'drawing': drawing_ui}
        self.rules = {}
        for name, rule, module, function in ACTIONS:
            action = Gio.SimpleAction.new(name, None)
            handler = getattr(self.modules[module], function)
            action.connect('activate', lambda _a, _p, handler=handler: self._run(handler))
            window.add_action(action)
            self.rules[name] = rule
        mode = Gio.SimpleAction.new_stateful('reader_mode', GLib.VariantType.new('s'),
                                             GLib.Variant.new_string(pdf_handler.get_reader_mode()))
        mode.connect('change-state', self._on_reader_mode)
        window.add_action(mode)
        from ..i18n import get_setting, set_setting
        scripts = Gio.SimpleAction.new_stateful('form_scripts', None,
                                                GLib.Variant.new_boolean(bool(get_setting('form_scripts', True))))

        def toggle_scripts(action, value):
            action.set_state(value)
            set_setting('form_scripts', value.get_boolean())
            if not value.get_boolean() and window.doc is not None and getattr(window.doc, 'editor_js_enabled', False):
                from ..ops import formjs
                formjs.disable(window.doc)
        scripts.connect('change-state', toggle_scripts)
        window.add_action(scripts)
        edit = Gio.SimpleAction.new('enter_edit_mode', None)
        edit.connect('activate', lambda *_a: window.view_mode and window._toggle_view_edit_mode())
        window.add_action(edit)
        layout = Gio.SimpleAction.new_stateful('page_layout', GLib.VariantType.new('s'),
                                               GLib.Variant.new_string('page'))
        layout.connect('change-state', self._on_page_layout)
        window.add_action(layout)
        app = window.get_application()
        if app:
            app.set_accels_for_action('win.advanced_search', ['<Control><Shift>f'])
            app.set_accels_for_action('win.document_info', ['<Control>i'])
            app.set_accels_for_action('win.open_convert', ['<Control><Shift>o'])
            app.set_accels_for_action('win.presentation', ['F5'])

    # -- glue used by dialog modules -------------------------------------
    @property
    def doc(self):
        return self.window.doc

    def editable(self):
        w = self.window
        return bool(w.doc and not w.view_mode and getattr(w._active_session, 'can_edit', True))

    def can_copy(self):
        return bool(self.window.doc and getattr(self.window._active_session, 'can_copy', True))

    def stem(self):
        path = self.window.current_file_path or getattr(self.window._active_session, 'suggested_name', None)
        return os.path.splitext(os.path.basename(path or 'document.pdf'))[0]

    def flush_edits(self):
        """Commit inline edits so operations see the latest document state."""
        w = self.window
        w.commit_pending_format_change()
        if w.inline_editor_widget is not None:
            w._apply_and_hide_editor(force_apply=True)
        if hasattr(w, 'form_tools') and not w.form_tools.save_values():
            raise OperationError(_('err_form_values'))

    def snapshot_bytes(self):
        """Serialized copy of the live document including unsaved editor objects."""
        self.flush_edits()
        from ..page_state import persist
        persist(self.window.doc)
        return document_bytes(self.window.doc)

    def mutate(self, mutation, rebase_pages=(), page_num=None, form_fill=False):
        """Apply an undoable in-place change. Raises on failure.

        ``form_fill`` allows the change in View mode when the document permits filling forms.
        """
        try:
            ok = self.window._mutate_document(mutation, page_num=page_num, rebase_pages=rebase_pages,
                                              allow_view=form_fill, allow_form=form_fill)
        except Exception as failure:
            raise OperationError(str(failure))
        if not ok:
            raise OperationError(_('err_edit_not_allowed'))
        self.window._update_ui_state()

    def change_pages(self, operation, remap, focus=None, message_text=''):
        """Apply an undoable page-structure change; ``operation`` mutates the doc."""
        w = self.window

        def run():
            operation()
            return True, message_text
        try:
            ok, text = w._change_pages(run, remap)
        except Exception as error:
            raise OperationError(str(error))
        if not ok:
            raise OperationError(text)
        w._clear_search()
        w.document_modified = True
        w._load_thumbnails()
        w._load_page(min(focus if focus is not None else w.current_page_index, w.doc.page_count - 1))
        w._update_ui_state()
        if message_text:
            toast(w, message_text)

    def open_new(self, doc, name, note=None):
        """Show a generated document in a new unsaved tab."""
        mark_new_document(doc)
        self.window.open_generated_document(doc, suggested_name=name)
        if note:
            toast(self.window, note)

    def save_data(self, data, initial_name, filters=None, done_text=None):
        """Ask for a destination and write bytes or text there atomically."""
        def chosen(path):
            current = self.window.current_file_path
            if current and os.path.realpath(path) == os.path.realpath(current):
                message(self.window, _('err_title'), _('err_overwrite_open_file'))
                return
            try:
                write_file_atomic(path, data)
                toast(self.window, done_text or _('saved_to', os.path.basename(path)))
            except OSError as error:
                message(self.window, _('err_title'), str(error))
        choose_save(self.window, _('save_as_title'), initial_name, filters, chosen)

    def save_pdf(self, data, initial_name, done_text=None):
        if not initial_name.lower().endswith('.pdf'):
            initial_name += '.pdf'
        self.save_data(data, initial_name, [pdf_filter()], done_text)

    def managed_pages(self, pages):
        """Pages carrying editable pdfLX objects (loaded or persisted in the PDF)."""
        import json
        from ..page_state import KEY
        doc = self.window.doc
        live = getattr(self.window._active_session, 'page_objects', {}) or {}
        found = []
        for number in pages:
            groups = live.get(number)
            if groups is not None:
                if any(getattr(obj, 'is_new', False) for group in groups for obj in group):
                    found.append(number)
                continue
            kind, value = doc.xref_get_key(doc[number].xref, KEY)
            if kind == 'xref':
                try:
                    payload = json.loads(doc.xref_stream(int(value.split()[0])))
                    if payload.get('objects'):
                        found.append(number)
                except (ValueError, TypeError, RuntimeError):
                    found.append(number)
        return found

    def require_plain_pages(self, pages):
        """Refuse geometry changes on pages whose editable objects would be misplaced."""
        from ..ops.common import format_page_ranges
        managed = self.managed_pages(pages)
        if managed:
            raise OperationError(_('err_pages_have_editor_objects', format_page_ranges(managed)))

    def selection_rect(self):
        tools = getattr(self.window, 'document_tools', None)
        return tools.selection() if tools else None

    # -- framework ---------------------------------------------------------
    def _run(self, handler):
        try:
            handler(self)
        except OperationError as error:
            message(self.window, _('err_title'), str(error))

    def _on_page_layout(self, action, value):
        if self._syncing_layout:
            action.set_state(value)
            return
        action.set_state(value)
        self.modules['viewer'].apply_layout(self.window, value.get_string())

    _syncing_layout = False

    def _on_reader_mode(self, action, value):
        action.set_state(value)
        pdf_handler.set_reader_mode(value.get_string())
        w = self.window
        if w.doc:
            w._load_page(w.current_page_index, preserve_scroll=True, reload_objects=False)
            w.pdf_view.queue_draw()
            view = getattr(w, 'continuous_view', None)
            if view is not None and hasattr(view, 'queue_draw'):
                view.queue_draw()

    def update_state(self):
        w = self.window
        session = w._active_session
        has_doc = w.doc is not None
        has_pages = has_doc and w.doc.page_count > 0
        can_copy = getattr(session, 'can_copy', True)
        can_print = getattr(session, 'can_print', True)
        in_edit = has_pages and not w.view_mode and getattr(session, 'can_edit', True)
        state = {'always': True, 'doc': has_doc, 'pages': has_pages, 'copy': has_pages and can_copy,
                 'edit': in_edit, 'print': has_doc and can_print}
        for name, rule in self.rules.items():
            action = w.lookup_action(name)
            if action:
                action.set_enabled(state[rule])
        edit = w.lookup_action('enter_edit_mode')
        if edit:
            edit.set_enabled(bool(has_pages and w.view_mode and getattr(session, 'can_edit', True)))
        layout = w.lookup_action('page_layout')
        if layout and session is not None:
            layout.set_enabled(has_pages)
            if layout.get_state().get_string() != session.scroll_mode:
                self._syncing_layout = True
                try:
                    layout.change_state(GLib.Variant.new_string(session.scroll_mode))
                finally:
                    self._syncing_layout = False
        incremental = w.lookup_action('save_incremental')
        if incremental:
            incremental.set_enabled(bool(has_doc and w.document_modified and
                                         pdf_handler.can_save_incrementally(w.doc, w.current_file_path)))

    # -- menus ---------------------------------------------------------------
    def build_main_menu(self):
        """The header-bar menu: everyday file actions first, then task submenus in order of use."""
        menu = Gio.Menu()
        create = _submenu(_section((_('menu_open_other_format'), 'open_convert'),
                                   (_('menu_images_to_pdf'), 'images_to_pdf'),
                                   (_('menu_compose'), 'compose_document')))
        files = _section((_('btn_new_doc'), 'new'), (_('btn_open_doc'), 'open'))
        files.append_submenu(_('menu_group_create'), create)
        menu.append_section(None, files)

        export = Gio.Menu()
        export.append_section(None, _section(
            (_('menu_export_as'), 'export_as'), (_('export_format_docx'), 'export_docx'),
            (_('export_format_txt'), 'export_txt'), (_('menu_export_structured'), 'export_structured')))
        export.append_section(None, _section(
            (_('menu_save_page_pdf'), 'save_page_pdf'), (_('menu_export_range'), 'export_range'),
            (_('menu_export_images'), 'export_images'), (_('menu_export_page_svg'), 'export_page_svg'),
            (_('menu_export_diagram'), 'export_diagram'), (_('menu_export_vector_svg'), 'export_vector_svg')))
        export.append_section(None, _section(
            (_('menu_extract_images'), 'extract_images'), (_('menu_extract_tables'), 'extract_tables'),
            (_('menu_export_tables'), 'export_tables')))
        export.append_section(None, _section((_('menu_save_incremental'), 'save_incremental')))
        saving = _section((_('menu_save_as'), 'save_as'), (_('print_tip').rsplit(' (', 1)[0], 'print'))
        saving.append_submenu(_('menu_export'), export)
        menu.append_section(None, saving)

        tasks = Gio.Menu()
        tasks.append_submenu(_('menu_group_edit'), _submenu(
            _section((_('replace_title') + '…', 'find_replace'), (_('menu_rich_text'), 'rich_text'),
                     (_('table_add'), 'add_table'), (_('table_paste'), 'paste_table')),
            _section((_('menu_draw_shape'), 'draw_shape'), (_('menu_node_editor'), 'node_editor')),
            _section((_('menu_text_style'), 'text_style'), (_('menu_images_manager'), 'images_manager'),
                     (_('menu_recolor'), 'recolor'))))
        tasks.append_submenu(_('menu_group_forms'), _submenu(
            _section((_('tool_fill_forms'), 'form_fields'), (_('tool_create_field'), 'create_field'),
                     (_('menu_form_structure'), 'form_structure')),
            _section((_('menu_form_import'), 'form_import_data'), (_('menu_form_export'), 'form_export_data')),
            _section((_('tool_flatten'), 'flatten_forms'))))
        tasks.append_submenu(_('menu_group_annotate'), _submenu(
            _section((_('tool_add_note'), 'sticky_note'), (_('tool_add_stamp'), 'stamp'),
                     (_('menu_add_markup'), 'annot_add'), (_('tool_add_review'), 'review'),
                     (_('tool_comments'), 'comments')),
            _section((_('menu_comment_summary'), 'comment_summary'), (_('menu_export_annotations'), 'annot_export'),
                     (_('menu_import_annotations'), 'annot_import')),
            _section((_('menu_flatten_annotations'), 'flatten_annotations'))))
        tasks.append_submenu(_('menu_group_pages'), self.pages_menu())
        tasks.append_submenu(_('menu_group_document'), _submenu(
            _section((_('menu_properties'), 'document_properties'), (_('menu_document_info'), 'document_info'),
                     (_('menu_viewer_settings'), 'viewer_settings')),
            _section((_('menu_bookmarks'), 'bookmarks'), (_('menu_generate_bookmarks'), 'generate_bookmarks'),
                     (_('menu_links'), 'links'), (_('menu_attachments'), 'attachments'), (_('menu_layers'), 'layers')),
            _section((_('menu_einvoice'), 'attach_einvoice'), (_('menu_portfolio'), 'create_portfolio'))))
        tasks.append_submenu(_('menu_group_protect'), self.protect_menu())
        tasks.append_submenu(_('menu_group_tools'), _submenu(
            _section((_('menu_advanced_search'), 'advanced_search'), (_('menu_compare'), 'compare'),
                     (_('menu_ocr'), 'ocr'), (_('menu_optimize'), 'optimize')),
            _section((_('menu_mail_merge'), 'mail_merge'), (_('menu_batch'), 'batch_process'),
                     (_('menu_snapshot'), 'snapshot'), (_('menu_object_inspector'), 'object_inspector'))))
        menu.append_section(None, tasks)

        view = Gio.Menu()
        modes = Gio.Menu()
        for key in pdf_handler.READER_MODES:
            item = Gio.MenuItem.new(_(f'reader_mode_{key}'), None)
            item.set_action_and_target_value('win.reader_mode', GLib.Variant.new_string(key))
            modes.append_item(item)
        view.append_section(_('menu_reader_mode'), modes)
        layouts = Gio.Menu()
        for key in ('page', 'continuous', 'spread', 'book'):
            item = Gio.MenuItem.new(_(f'layout_{key}'), None)
            item.set_action_and_target_value('win.page_layout', GLib.Variant.new_string(key))
            layouts.append_item(item)
        view.append_section(_('menu_page_layout'), layouts)
        guides = Gio.Menu()
        guides.append(_('menu_alignment_guides'), 'win.alignment_guides')
        view.append_section(None, guides)
        view.append_section(None, _section((_('menu_presentation'), 'presentation'),
                                           (_('menu_side_viewer'), 'side_viewer'),
                                           (_('menu_measure'), 'measure'),
                                           (_('menu_measure_settings'), 'measure_settings')))
        prefs = Gio.Menu()
        prefs.append(_('menu_confirm_delete'), 'win.confirm_delete')
        prefs.append(_('menu_form_scripts'), 'win.form_scripts')
        prefs.append(_('ai_settings') + '…', 'win.ai_settings')
        settings = Gio.Menu()
        settings.append_submenu(_('menu_view'), view)
        settings.append_submenu(_('menu_preferences'), prefs)
        menu.append_section(None, settings)

        about = Gio.Menu()
        about.append(_('menu_quick_guide'), 'win.quick_guide')
        about.append(_('menu_about'), 'win.about')
        about.append(_('menu_quit'), 'app.quit')
        menu.append_section(None, about)
        return menu

    def pages_menu(self):
        return _submenu(
            _edit_mode_section(),
            _section((_('organize_title') + '…', 'organize_pages'),),
            _section((_('menu_insert_pages'), 'insert_pages'), (_('menu_extract_pages'), 'extract_pages'),
                     (_('menu_split_document'), 'split_document')),
            _section((_('menu_reverse_pages'), 'reverse_pages'), (_('menu_collate_pages'), 'collate_pages'),
                     (_('menu_remove_blank_pages'), 'remove_blank_pages'),
                     (_('menu_remove_rotation'), 'remove_rotation')),
            _section((_('tool_crop'), 'crop'), (_('menu_resize_pages'), 'resize_pages'),
                     (_('menu_page_boxes'), 'page_boxes'), (_('menu_nup_pages'), 'nup_pages')),
            _section((_('tool_decorate'), 'decorate'), (_('menu_watermark'), 'watermark_text'),
                     (_('menu_headers_footers'), 'headers_footers'),
                     (_('menu_overlay_pages'), 'overlay_pages'), (_('menu_page_labels'), 'page_labels')))

    def protect_menu(self):
        return _submenu(
            _edit_mode_section(),
            _section((_('signature_add'), 'add_signature'), (_('signature_digital'), 'sign_certificate'),
                     (_('menu_verify_signatures'), 'verify_signatures')),
            _section((_('menu_security'), 'security'),),
            _section((_('tool_redact'), 'redact'), (_('menu_sanitize'), 'sanitize'),
                     (_('menu_hidden_text'), 'hidden_text'), (_('menu_rasterize'), 'rasterize')))
