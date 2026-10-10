import copy
from typing import Optional, List, Dict, Tuple, Any
from .undo_manager import UndoManager, EditObjectCommand, AddObjectCommand, AddTableCommand, EditTableCommand, DocumentMutationCommand, DeleteObjectCommand, CompositeCommand, RotatePageCommand, RotateObjectCommand
from .i18n import _, get_setting, set_setting

import gi
import os
from pathlib import Path
import cairo
import threading
import math
import re

# Screen pixels the pointer must travel before a press on an object becomes a drag.
DRAG_THRESHOLD = 5.0

try:
    import pymupdf as fitz
except ImportError:
    import fitz

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gio, GLib, Adw, Gdk, GdkPixbuf, Pango, GObject, PangoCairo

from . import constants
from . import pdf_handler
from . import document_features
from . import print_handler
from .welcome_view import WelcomeView 
from .models import PdfPage, EditableText, BASE14_FALLBACK_MAP, EditableImage, EditableShape, EditableStroke, DocumentSession
from .ui_components import (
    ColorSwatchButton, PageThumbnailFactory, show_error_dialog, show_confirm_dialog,
    show_save_changes_dialog, show_open_file_dialog, show_save_file_dialog,
    SymbolsPopover, render_emoji_to_png_bytes, show_new_document_dialog
)
from .quick_guide_dialog import QuickGuideDialog
from .about_window import AboutWindow
from .signature_dialog import VisibleSignatureDialog, CertificateSignatureDialog
from . import signatures
from .table_dialog import TableDialog
from .table_creation import create_table_objects, TableSelection
from . import utils
from . import text_geometry
from . import highlight_tools
from .document_tool_ui import DocumentToolsController
from .form_ui import FormController
from . import session_memory
import logging
logger = logging.getLogger(__name__)

class PdfEditorWindow(Adw.ApplicationWindow):
    """Main application window providing PDF viewing, editing, annotation, and exporting capabilities."""
    _active_session = None
    sessions = None
    tab_view = None
    tab_bar = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.set_title(constants.APP_NAME)
        self.set_default_size(1200, 800)
        self.set_icon_name(constants.APP_ICON)
        try:
            display = Gdk.Display.get_default()
            if display:
                icon_theme = Gtk.IconTheme.get_for_display(display)
                icons_dir = os.path.join(os.path.dirname(__file__), "icons")
                if os.path.exists(icons_dir) and icons_dir not in icon_theme.get_search_path():
                    icon_theme.add_search_path(icons_dir)
                img_dir = os.path.join(os.path.dirname(__file__), "img")
                if os.path.exists(img_dir) and img_dir not in icon_theme.get_search_path():
                    icon_theme.add_search_path(img_dir)
        except Exception:
            pass

        self.sessions = []
        self._active_session = DocumentSession(undo_manager=UndoManager(self))
        self.sessions.append(self._active_session)
        self.tab_view = None
        self.tab_bar = None
        self._is_switching_tabs = False

        self.current_file_path = None
        self.original_file_path = None
        self.allow_incremental_save = True
        self.doc = None 
        self.current_page_index = 0
        self.zoom_level = 1.0
        self.search_results = []
        self.search_current_result = -1
        self._search_generation = 0
        self._search_document = None
        self._search_resetting = False
        self._last_pointer_pos = None
        self.pages_model = Gio.ListStore(item_type=PdfPage)
        self.editable_texts = [] 
        self.editable_images = []
        self.editable_shapes = []
        self.editable_strokes = []
        self.selected_text = None
        self.selected_image = None
        self.selected_shape = None
        self.selected_table = None
        self.table_drag_state = None
        self.table_drag_table = None
        self.selected_stroke = None
        self.text_edit_popover = None
        self.text_edit_view = None
        self.is_saving = False
        self.dragged_object = None
        self.drag_start_pos = (0, 0)
        self.drag_object_start_pos = (0, 0)
        self.resize_handle = None  
        self.resize_start_bbox = None  
        self.dragging_to_create = False  
        self.temp_shape = None  
        self.temp_stroke = None
        self.temp_image_bbox = None  
        self._pending_signature = None
        self.temp_image_path = None  
        self.drag_start_page_pos = None  
        self.next_shape_fill = (255, 255, 255)
        self.next_shape_stroke = (0, 0, 0)
        self.next_shape_stroke_width = 2.0
        self.next_shape_transparent = True
        self.pen_color = (0.0, 0.0, 0.0)
        self.pen_width = 2.0
        self.highlighter_color = (1.0, 0.9, 0.0)
        self.highlighter_width = 14.0
        self.highlighter_opacity = 0.35
        self.document_modified = False 
        self.tool_mode = "select" 
        self._pan_start = None
        self.current_pdf_page_width = 0
        self.current_pdf_page_height = 0
        self.bold_button = None
        self.italic_button = None
        self.font_scan_in_progress = True
        self.undo_manager = UndoManager(self)
        self.pending_format_change_obj = None
        self.before_format_change_state = None
        self.is_repaired_file = False
        self._last_font_family = None
        self._last_font_size = 11.0
        self._last_is_bold = False
        self._last_is_italic = False
        self._last_is_strikethrough = False
        self._last_alignment = 'left'
        self._last_color = (0.0, 0.0, 0.0)

        self.view_mode = True
        self.view_sel_start = None
        self.view_sel_rect = None
        self.view_selected_text = ""
        self.view_drag_active = False
        
        self.selected_word = None
        self.selected_word_start_char = None
        self.selected_word_end_char = None
        self.word_selection_mode = False

        self.inline_editor_widget = None
        self.inline_editor_text_obj = None

        self._build_ui()
        self._setup_controllers()
        self._connect_actions()
        self._apply_css()
        self.recovery = session_memory.RecoveryManager(self)
        app = self.get_application()
        if app is not None and app.get_application_id() == constants.APP_ID:
            GLib.timeout_add(600, self._offer_recovery)
        self._update_ui_state() 

        self.status_label.set_text(_("scan_fonts"))
        utils.scan_system_fonts_async(callback_on_done=self._on_font_scan_complete)

    def _on_font_scan_complete(self):
        """Callback when background system font scan finishes, populating font combo."""
        self.font_scan_in_progress = False
        utils.get_default_unicode_font_path()
        self._populate_font_combo()

        if not utils.UNICODE_FONT_PATH:
             show_error_dialog(self, _("font_warning_msg"), _("font_warning_title"))

        if not self.doc:
            self.status_label.set_text(_("fonts_loaded_open"))
        elif self.current_file_path:
            self.status_label.set_text(_("loaded").format(os.path.basename(self.current_file_path)))
        else:
            self.status_label.set_text(_("new_doc_loaded"))
        self._update_ui_state()

    def _populate_font_combo(self):
        """Populate font selector dropdown with discovered system fonts."""
        self.font_store.clear() 
        if utils.FONT_FAMILY_LIST_SORTED:
            for family_name in utils.FONT_FAMILY_LIST_SORTED:
                self.font_store.append([family_name, family_name])
            if len(self.font_store) > 0:
                self.font_combo.set_active(0)
            else:
                 self.font_store.append([_("font_none_error"), ""])
                 self.font_combo.set_active(0)
        else:
            self.font_store.append([_("font_none"), ""])
            self.font_combo.set_active(0)
            print("WARNING: utils.FONT_FAMILY_LIST_SORTED is empty.")
        
        self._update_ui_state()

    def _apply_css(self):
        """Apply the application stylesheet (pdflx/style.css) for toolbars, canvas, sidebar, and popovers."""
        css_provider = Gtk.CssProvider()
        css_provider.load_from_path(str(Path(__file__).with_name('style.css')))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    @property
    def active_session(self) -> DocumentSession:
        """Get current active DocumentSession."""
        return self._active_session

    @active_session.setter
    def active_session(self, session: DocumentSession):
        """Set active DocumentSession."""
        self.set_active_session(session)

    def set_active_session(self, session: DocumentSession):
        """Switch the active document session with clean state isolation and tab synchronization."""
        if session is None:
            return

        if getattr(self,'_document_only_view',False) and self._active_session is not session:
            self._exit_document_view()
        if self._active_session is session:
            # Ensure editor_container is parented correctly even if session unchanged
            if getattr(session, 'bin_widget', None) and hasattr(self, 'editor_container'):
                if self.editor_container.get_parent() != session.bin_widget:
                    old_parent = self.editor_container.get_parent()
                    if old_parent:
                        old_parent.set_child(None)
                    session.bin_widget.set_child(self.editor_container)
            return

        self._cancel_table_drag()
        self._pan_start = None
        if hasattr(self,"stamp_interaction"):
            self.stamp_interaction.cancel()
        if hasattr(self,"form_tools"):
            self.form_tools.cancel_drag()
        self.selected_table = None

        if self._active_session and self._active_session.doc:
            try:
                self.commit_pending_format_change()
                if self.inline_editor_widget is not None:
                    self._apply_and_hide_editor(force_apply=True)
            except Exception:
                pass

        self._active_session = session
        self._clear_search()
        if session not in self.sessions:
            self.sessions.append(session)

        # Ensure tab exists in TabView
        if hasattr(self, 'tab_view') and self.tab_view:
            if getattr(session, 'tab_page', None) is None:
                self._create_tab_for_session(session)

            self._is_switching_tabs = True
            try:
                if session.tab_page and self.tab_view.get_selected_page() != session.tab_page:
                    self.tab_view.set_selected_page(session.tab_page)
            finally:
                self._is_switching_tabs = False

        # Attach self.editor_container to active session's bin_widget
        if getattr(session, 'bin_widget', None) is not None and hasattr(self, 'editor_container'):
            current_parent = self.editor_container.get_parent()
            if current_parent != session.bin_widget:
                if current_parent:
                    current_parent.set_child(None)
                session.bin_widget.set_child(self.editor_container)

        if hasattr(self, 'thumbnail_selection_model') and self.thumbnail_selection_model is not None:
            if session.pages_model is not None:
                self._syncing_thumb = True
                try:
                    self.thumbnail_selection_model.set_model(session.pages_model)
                finally:
                    self._syncing_thumb = False

        if session.doc is not None:
            self.set_title(f"{constants.APP_NAME} - {session.display_title}")
            if hasattr(self, 'stack') and self.stack:
                self.stack.set_visible_child_name("editor")
            if hasattr(self, 'tab_bar') and self.tab_bar:
                self.tab_bar.set_visible(True)
            if hasattr(self, 'pdf_scroll'):
                has_objects = bool(session.editable_texts or session.editable_shapes or session.editable_strokes or session.editable_images or session.is_modified)
                self._load_page(session.current_page_index, reload_objects=(not has_objects))
                if session.fit_on_load or session.fit_to_view:
                    self._schedule_fit_document(session)
            if hasattr(self, 'thumbnail_selection_model') and self.thumbnail_selection_model:
                try:
                    self.thumbnail_selection_model.set_selected(session.current_page_index)
                except Exception:
                    pass
        else:
            self.set_title(constants.APP_NAME)
            if hasattr(self, 'stack') and self.stack:
                any_docs = any(s.doc is not None for s in self.sessions)
                if not any_docs:
                    self.stack.set_visible_child_name("welcome")
                    if hasattr(self, 'tab_bar') and self.tab_bar:
                        self.tab_bar.set_visible(False)
                else:
                    self.stack.set_visible_child_name("editor")
                    if hasattr(self, 'tab_bar') and self.tab_bar:
                        self.tab_bar.set_visible(True)

        self._update_tab_title(session)
        self._update_ui_state()
        if hasattr(self, 'pdf_view') and self.pdf_view:
            self.pdf_view.queue_draw()

    def _create_tab_for_session(self, session: DocumentSession):
        """Create and bind an Adw.TabPage in tab_view for the given DocumentSession."""
        if not hasattr(self, 'tab_view') or self.tab_view is None:
            return None
        if getattr(session, 'tab_page', None) is not None:
            return session.tab_page

        bin_widget = Adw.Bin()
        bin_widget.set_vexpand(True)
        bin_widget.set_hexpand(True)
        page = self.tab_view.append(bin_widget)
        session.tab_page = page
        session.bin_widget = bin_widget

        if hasattr(self, 'editor_container') and self.editor_container is not None:
            if session == self._active_session or self.editor_container.get_parent() is None:
                old_parent = self.editor_container.get_parent()
                if old_parent:
                    old_parent.set_child(None)
                bin_widget.set_child(self.editor_container)

        self._update_tab_title(session)
        return page

    def _update_tab_title(self, session: DocumentSession):
        """Sync tab title, tooltip, and dirty indicators for the given session."""
        if not session or not getattr(session, 'tab_page', None):
            return
        try:
            page = session.tab_page
            display_title = session.display_title
            page.set_title(display_title)
            page.set_tooltip(session.pdf_path or _("untitled_document") if "_" in dir() else (session.pdf_path or "Untitled Document"))
            page.set_icon(Gio.ThemedIcon.new("application-pdf-symbolic"))
            page.set_needs_attention(session.is_modified)
        except Exception:
            pass

    def get_session_by_tab_page(self, page) -> Optional[DocumentSession]:
        """Find the DocumentSession associated with the specified Adw.TabPage."""
        if not page or not self.sessions:
            return None
        page_child = page.get_child() if hasattr(page, 'get_child') else None
        for s in self.sessions:
            if getattr(s, 'tab_page', None) == page:
                return s
            if page_child is not None and getattr(s, 'bin_widget', None) == page_child:
                return s
        return None

    def _on_tab_selected_page_changed(self, tab_view, pspec):
        """Handle user tab selection changes from Adw.TabBar or keyboard navigation."""
        if getattr(self, '_is_switching_tabs', False):
            return
        if not hasattr(self, 'tab_view') or self.tab_view is None:
            return
        selected_page = self.tab_view.get_selected_page()
        if not selected_page:
            return
        target_session = self.get_session_by_tab_page(selected_page)
        if target_session and target_session != self._active_session:
            self.set_active_session(target_session)

    def _on_tab_close_page(self, tab_view, page) -> bool:
        """Handle tab close request, prompting to save unsaved modifications."""
        target_session = self.get_session_by_tab_page(page)
        if target_session:
            if target_session.is_modified:
                self.set_active_session(target_session)
                response = show_save_changes_dialog(self)
                if response == Gtk.ResponseType.ACCEPT:
                    if target_session.pdf_path:
                        self.save_document(target_session.pdf_path, incremental=False)
                    else:
                        self.on_save_as(None, None)
                        if target_session.is_modified:
                            self.tab_view.close_page_finish(page, False)
                            return True
                elif response == Gtk.ResponseType.REJECT:
                    pass
                else:  # Cancel
                    self.tab_view.close_page_finish(page, False)
                    return True

            self.tab_view.close_page_finish(page, True)
            target_session.tab_page = None
            self.remove_session(target_session)
            return True

        self.tab_view.close_page_finish(page, True)
        return True

    def _on_tab_page_reordered(self, tab_view, page, position):
        """Update internal session list order when user drags and reorders tabs."""
        session = self.get_session_by_tab_page(page)
        if session and session in self.sessions:
            self.sessions.remove(session)
            self.sessions.insert(position, session)

    def _on_tab_extra_drag_drop(self, tab_bar, page, value):
        """Handle dropping a PDF file onto the TabBar to open it in a new tab."""
        if isinstance(value, Gio.File):
            filepath = value.get_path()
            if filepath and filepath.lower().endswith('.pdf'):
                GLib.idle_add(self.load_document, filepath, 0, True)
                return True
        return False

    def create_session(self, doc=None, filepath=None) -> DocumentSession:
        """Create a new DocumentSession configured for this window."""
        session = DocumentSession(
            doc=doc,
            pdf_path=filepath,
            original_file_path=filepath,
            undo_manager=UndoManager(self)
        )
        return session

    def add_session(self, session: DocumentSession, switch_to: bool = True):
        """Add a session to the session pool and create a tab for it."""
        if session not in self.sessions:
            self.sessions.append(session)
        if hasattr(self, 'tab_view') and self.tab_view:
            if getattr(session, 'tab_page', None) is None:
                self._create_tab_for_session(session)
        if switch_to:
            self.set_active_session(session)

    def remove_session(self, session_or_id):
        """Remove a session from the session pool, close its tab, and cleanly close it."""
        target_session = None
        if isinstance(session_or_id, DocumentSession):
            target_session = session_or_id
        elif isinstance(session_or_id, str):
            target_session = self.get_session_by_id(session_or_id)

        if not target_session or target_session not in self.sessions:
            return

        self.sessions.remove(target_session)

        if hasattr(self, 'editor_container') and self.editor_container.get_parent() == getattr(target_session, 'bin_widget', None):
            if target_session.bin_widget:
                target_session.bin_widget.set_child(None)

        if hasattr(self, 'tab_view') and self.tab_view and getattr(target_session, 'tab_page', None):
            page = target_session.tab_page
            target_session.tab_page = None
            try:
                self.tab_view.close_page(page)
            except Exception:
                pass

        if target_session.doc is not None and target_session.pdf_path:
            session_memory.remember_position(target_session.pdf_path, target_session.current_page_index,
                                             target_session.zoom_level, target_session.scroll_mode)
        if hasattr(self, 'recovery'):
            self.recovery.discard(target_session)
        target_session.close()

        if self._active_session == target_session or self._active_session not in self.sessions:
            selected_page = self.tab_view.get_selected_page() if hasattr(self, 'tab_view') and self.tab_view else None
            candidate = self.get_session_by_tab_page(selected_page) if selected_page else None
            if candidate and candidate != target_session and candidate in self.sessions:
                self.set_active_session(candidate)
            else:
                remaining_with_doc = [s for s in self.sessions if s.doc is not None]
                if remaining_with_doc:
                    self.set_active_session(remaining_with_doc[-1])
                elif self.sessions:
                    self.set_active_session(self.sessions[-1])
                else:
                    new_session = self.create_session()
                    self._active_session = new_session
                    self.sessions = [new_session]
                    if hasattr(self, 'tab_view') and self.tab_view:
                        self._create_tab_for_session(new_session)
                    self.close_document()
                    if hasattr(self, 'stack') and self.stack:
                        self.stack.set_visible_child_name("welcome")
                    if hasattr(self, 'tab_bar') and self.tab_bar:
                        self.tab_bar.set_visible(False)

    def get_session_by_id(self, session_id: str):
        """Retrieve session by UUID."""
        for s in self.sessions:
            if s.session_id == session_id:
                return s
        return None

    def get_session_by_path(self, filepath: str):
        """Retrieve session by canonical file path."""
        if not filepath:
            return None
        try:
            norm_target = os.path.realpath(filepath)
        except Exception:
            norm_target = filepath
        for s in self.sessions:
            if s.pdf_path:
                try:
                    if os.path.realpath(s.pdf_path) == norm_target:
                        return s
                except Exception:
                    if s.pdf_path == filepath:
                        return s
        return None

    @property
    def doc(self):
        """Active document object."""
        return self._active_session.doc if self._active_session else None

    @doc.setter
    def doc(self, val):
        if self._active_session:
            self._active_session.doc = val

    @property
    def current_file_path(self):
        """Active document file path."""
        return self._active_session.pdf_path if self._active_session else None

    @current_file_path.setter
    def current_file_path(self, val):
        if self._active_session:
            self._active_session.pdf_path = val
            self._update_tab_title(self._active_session)

    @property
    def original_file_path(self):
        """Active document original file path."""
        return self._active_session.original_file_path if self._active_session else None

    @original_file_path.setter
    def original_file_path(self, val):
        if self._active_session:
            self._active_session.original_file_path = val

    @property
    def current_page_index(self):
        """Active document current page index."""
        return self._active_session.current_page_index if self._active_session else 0

    @current_page_index.setter
    def current_page_index(self, val):
        if self._active_session:
            self._active_session.current_page_index = val

    @property
    def zoom_level(self):
        """Active document zoom level."""
        return self._active_session.zoom_level if self._active_session else 1.0

    @zoom_level.setter
    def zoom_level(self, val):
        if self._active_session:
            self._active_session.zoom_level = val

    @property
    def view_mode(self):
        """Active document view mode flag."""
        return self._active_session.view_mode if self._active_session else True

    @view_mode.setter
    def view_mode(self, val):
        if self._active_session:
            self._active_session.view_mode = val

    @property
    def document_modified(self):
        """Active document modified flag."""
        return self._active_session.is_modified if self._active_session else False

    @document_modified.setter
    def document_modified(self, val):
        if self._active_session:
            self._active_session.is_modified = val
            self._update_tab_title(self._active_session)
            if val and hasattr(self, 'get_title'):
                title = self.get_title()
                if not title.endswith("*"):
                    self.set_title(title + "*")
            elif not val and hasattr(self, 'get_title'):
                title = self.get_title()
                if title.endswith("*"):
                    self.set_title(title[:-1])

    @property
    def allow_incremental_save(self):
        """Active document allow incremental save flag."""
        return self._active_session.allow_incremental_save if self._active_session else True

    @allow_incremental_save.setter
    def allow_incremental_save(self, val):
        if self._active_session:
            self._active_session.allow_incremental_save = val

    @property
    def is_repaired_file(self):
        """Active document repaired file flag."""
        return self._active_session.is_repaired_file if self._active_session else False

    @is_repaired_file.setter
    def is_repaired_file(self, val):
        if self._active_session:
            self._active_session.is_repaired_file = val

    @property
    def undo_manager(self):
        """Active document undo manager."""
        return self._active_session.undo_manager if self._active_session else None

    @undo_manager.setter
    def undo_manager(self, val):
        if self._active_session:
            self._active_session.undo_manager = val

    @property
    def pages_model(self):
        """Active document pages model."""
        return self._active_session.pages_model if self._active_session else None

    @pages_model.setter
    def pages_model(self, val):
        if self._active_session:
            self._active_session.pages_model = val

    @property
    def editable_texts(self):
        """Active document editable texts list."""
        return self._active_session.editable_texts if self._active_session else []

    @editable_texts.setter
    def editable_texts(self, val):
        if self._active_session:
            self._active_session.editable_texts = val

    @property
    def editable_images(self):
        """Active document editable images list."""
        return self._active_session.editable_images if self._active_session else []

    @editable_images.setter
    def editable_images(self, val):
        if self._active_session:
            self._active_session.editable_images = val

    @property
    def editable_shapes(self):
        """Active document editable shapes list."""
        return self._active_session.editable_shapes if self._active_session else []

    @editable_shapes.setter
    def editable_shapes(self, val):
        if self._active_session:
            self._active_session.editable_shapes = val

    @property
    def editable_strokes(self):
        """Active document editable strokes list."""
        return self._active_session.editable_strokes if self._active_session else []

    @editable_strokes.setter
    def editable_strokes(self, val):
        if self._active_session:
            self._active_session.editable_strokes = val

    @property
    def selected_text(self):
        """Active document selected text."""
        return self._active_session.selected_text if self._active_session else None

    @selected_text.setter
    def selected_text(self, val):
        if self._active_session:
            self._active_session.selected_text = val

    @property
    def selected_image(self):
        """Active document selected image."""
        return self._active_session.selected_image if self._active_session else None

    @selected_image.setter
    def selected_image(self, val):
        if self._active_session:
            tools=getattr(self,'image_tools',None)
            if tools and tools.panel and tools.panel.image is not val:
                tools.panel.finish()
            self._active_session.selected_image = val
            if tools: GLib.idle_add(tools.sync)

    @property
    def selected_shape(self):
        """Active document selected shape."""
        return self._active_session.selected_shape if self._active_session else None

    @selected_shape.setter
    def selected_shape(self, val):
        if self._active_session:
            self._active_session.selected_shape = val

    @property
    def selected_stroke(self):
        """Active document selected stroke."""
        return self._active_session.selected_stroke if self._active_session else None

    @selected_stroke.setter
    def selected_stroke(self, val):
        if self._active_session:
            self._active_session.selected_stroke = val

    @property
    def view_sel_start(self):
        """Active document view mode selection start."""
        return self._active_session.view_sel_start if self._active_session else None

    @view_sel_start.setter
    def view_sel_start(self, val):
        if self._active_session:
            self._active_session.view_sel_start = val

    @property
    def view_sel_rect(self):
        """Active document view mode selection rectangle."""
        return self._active_session.view_sel_rect if self._active_session else None

    @view_sel_rect.setter
    def view_sel_rect(self, val):
        if self._active_session:
            self._active_session.view_sel_rect = val

    @property
    def view_selected_text(self):
        """Active document view mode selected text."""
        return self._active_session.view_selected_text if self._active_session else ""

    @view_selected_text.setter
    def view_selected_text(self, val):
        if self._active_session:
            self._active_session.view_selected_text = val

    @property
    def view_drag_active(self):
        """Active document view drag active flag."""
        return self._active_session.view_drag_active if self._active_session else False

    @view_drag_active.setter
    def view_drag_active(self, val):
        if self._active_session:
            self._active_session.view_drag_active = val

    @property
    def selected_word(self):
        """Active document selected word."""
        return self._active_session.selected_word if self._active_session else None

    @selected_word.setter
    def selected_word(self, val):
        if self._active_session:
            self._active_session.selected_word = val

    @property
    def selected_word_start_char(self):
        """Active document selected word start char index."""
        return self._active_session.selected_word_start_char if self._active_session else None

    @selected_word_start_char.setter
    def selected_word_start_char(self, val):
        if self._active_session:
            self._active_session.selected_word_start_char = val

    @property
    def selected_word_end_char(self):
        """Active document selected word end char index."""
        return self._active_session.selected_word_end_char if self._active_session else None

    @selected_word_end_char.setter
    def selected_word_end_char(self, val):
        if self._active_session:
            self._active_session.selected_word_end_char = val

    @property
    def word_selection_mode(self):
        """Active document word selection mode flag."""
        return self._active_session.word_selection_mode if self._active_session else False

    @word_selection_mode.setter
    def word_selection_mode(self, val):
        if self._active_session:
            self._active_session.word_selection_mode = val

    def _build_ui(self):
        """Build UI."""
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.document_view_stack=Gtk.Stack()
        self.document_view_stack.add_named(self.main_box,'normal')
        self.document_only_box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.document_view_stack.add_named(self.document_only_box,'document')
        self.toast_overlay = Adw.ToastOverlay(child=self.document_view_stack)
        self.set_content(self.toast_overlay)
        self.connect('notify::fullscreened',self._on_reader_fullscreen_changed)

        header = Adw.HeaderBar()
        header.add_css_class("top-headerbar")
        self.main_box.append(header)

        def icon_button(button):
            button.add_css_class("header-icon-btn")
            button.set_size_request(34, 34)
            button.set_valign(Gtk.Align.CENTER)
            child = button.get_child()
            if isinstance(child, Gtk.Image):
                child.set_pixel_size(16)
            return button

        left_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4,
                               valign=Gtk.Align.CENTER)
        left_actions.add_css_class("header-actions")
        header.pack_start(left_actions)

        # Navigation and file entry point.
        self.home_button = Gtk.Button.new_from_icon_name("go-home-symbolic")
        self.home_button.set_tooltip_text(_("home_button_tip"))
        self.home_button.connect("clicked", lambda w: self.go_to_welcome())
        self.home_button.add_css_class("flat")
        icon_button(self.home_button)
        left_actions.append(self.home_button)

        self.toggle_sidebar_button = Gtk.Button.new_from_icon_name("sidebar-show-symbolic")
        self.toggle_sidebar_button.set_tooltip_text("Toggle Page Preview (F9)")
        self.toggle_sidebar_button.add_css_class("flat")
        self.toggle_sidebar_button.add_css_class("active")
        icon_button(self.toggle_sidebar_button)
        self.toggle_sidebar_button.connect("clicked", self._toggle_sidebar)
        left_actions.append(self.toggle_sidebar_button)

        self.open_button = Gtk.Button(valign=Gtk.Align.CENTER)
        open_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        open_icon = Gtk.Image.new_from_icon_name("document-open-symbolic")
        open_icon.set_pixel_size(16)
        open_box.append(open_icon)
        open_box.append(Gtk.Label(label=_("btn_open_doc")))
        self.open_button.set_child(open_box)
        self.open_button.set_tooltip_text(f"{_('btn_open_doc')} (Ctrl+O)")
        self.open_button.add_css_class("flat")
        self.open_button.add_css_class("header-text-btn")
        self.open_button.connect("clicked", self.on_open_clicked)
        left_actions.append(self.open_button)

        # Quiet history controls, separated from navigation.
        undo_redo_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        undo_redo_box.add_css_class("header-history")
        self.header_history = undo_redo_box

        self.undo_button = Gtk.Button.new_from_icon_name("editor-undo-symbolic")
        self.undo_button.set_tooltip_text(_("undo_tip"))
        self.undo_button.add_css_class("flat")
        icon_button(self.undo_button)
        self.undo_button.connect("clicked", lambda w: self.undo_manager.undo())
        undo_redo_box.append(self.undo_button)

        self.redo_button = Gtk.Button.new_from_icon_name("editor-redo-symbolic")
        self.redo_button.set_tooltip_text(_("redo_tip"))
        self.redo_button.add_css_class("flat")
        icon_button(self.redo_button)
        self.redo_button.connect("clicked", lambda w: self.undo_manager.redo())
        undo_redo_box.append(self.redo_button)

        left_actions.append(undo_redo_box)

        # Document identity stays centered.
        self.window_title = Adw.WindowTitle()
        self.window_title.add_css_class("header-document-title")
        self.window_title.set_title(constants.APP_NAME)
        self.window_title.set_subtitle(_("app_subtitle"))
        header.set_title_widget(self.window_title)

        # Document actions, ordered from search to editing.
        right_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                                valign=Gtk.Align.CENTER)
        right_actions.add_css_class("header-actions")
        header.pack_end(right_actions)

        self.ai_button = Gtk.Button.new_from_icon_name("editor-ai-symbolic")
        self.ai_button.set_tooltip_text(f"{_('ai_title')} (Ctrl+K)")
        self.ai_button.add_css_class("flat")
        icon_button(self.ai_button)
        self.ai_button.connect("clicked", lambda *_: self.ai_bar.toggle())
        right_actions.append(self.ai_button)

        self.search_button = Gtk.Button.new_from_icon_name("edit-find-symbolic")
        self.search_button.set_tooltip_text(_("search_document_tip"))
        self.search_button.add_css_class("flat")
        icon_button(self.search_button)
        self.search_button.connect("clicked", self._toggle_search)
        right_actions.append(self.search_button)

        self.image_tools_button=Gtk.Button(icon_name='editor-image-tools-symbolic',visible=False)
        self.image_tools_button.set_tooltip_text('Image tools — select an image to edit it')
        self.image_tools_button.add_css_class('flat')
        icon_button(self.image_tools_button)
        self.image_tools_button.connect('clicked',lambda *_:self.image_tools.toggle())
        right_actions.append(self.image_tools_button)

        self.save_button = Gtk.Button(valign=Gtk.Align.CENTER)
        save_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        save_icon = Gtk.Image.new_from_icon_name("document-save-symbolic")
        save_icon.set_pixel_size(16)
        save_box.append(save_icon)
        save_box.append(Gtk.Label(label=_("btn_save")))
        self.save_button.set_child(save_box)
        self.save_button.set_tooltip_text(f"{_('btn_save')} (Ctrl+S)")
        self.save_button.add_css_class("header-save-btn")
        self.save_button.add_css_class("header-text-btn")
        self.save_button.connect("clicked", self.on_save_clicked)
        right_actions.append(self.save_button)

        self.mode_toggle_button = Gtk.Button(valign=Gtk.Align.CENTER)
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.mode_toggle_icon = Gtk.Image.new_from_icon_name("editor-pen-symbolic")
        self.mode_toggle_icon.set_pixel_size(16)
        self.mode_toggle_label = Gtk.Label(label=_("mode_edit"))
        mode_box.append(self.mode_toggle_icon)
        mode_box.append(self.mode_toggle_label)
        self.mode_toggle_button.set_child(mode_box)
        self.mode_toggle_button.set_tooltip_text(_("mode_toggle_tip"))
        self.mode_toggle_button.add_css_class("suggested-action")
        self.mode_toggle_button.add_css_class("header-action-btn")
        self.mode_toggle_button.add_css_class("header-text-btn")
        self.mode_toggle_button.connect("clicked", self._toggle_view_edit_mode)
        right_actions.append(self.mode_toggle_button)

        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.add_css_class("flat")
        icon_button(menu_button)
        right_actions.append(menu_button)
        menu_button.set_tooltip_text(_("menu_main_tip"))
        # The grouped menu model is attached once the feature controller exists.
        self.main_menu_button = menu_button

        self.search_revealer = Gtk.Revealer()
        self.search_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        search_toolbar = Gtk.Box()
        search_toolbar.add_css_class("search-toolbar")
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        search_clamp = Adw.Clamp(maximum_size=960, tightening_threshold=720, hexpand=True)
        search_clamp.set_child(search_box)
        search_toolbar.append(search_clamp)
        search_field = Gtk.Box(spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        search_field.add_css_class("search-field")
        self.search_entry = Gtk.SearchEntry(hexpand=True)
        clear_icon = self.search_entry.get_last_child()
        if isinstance(clear_icon, Gtk.Image):
            clear_icon.set_from_icon_name('window-close-symbolic')
            clear_icon.set_pixel_size(14)
        self.search_entry.set_placeholder_text(_("search_placeholder"))
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda entry: self._step_search(1))
        self.search_entry.connect("stop-search", lambda entry: self._hide_search())
        search_field.append(self.search_entry)
        self.search_count_label = Gtk.Label(label="", valign=Gtk.Align.CENTER,
                                          ellipsize=Pango.EllipsizeMode.END, max_width_chars=22,
                                          margin_start=8, margin_end=10)
        self.search_count_label.add_css_class("search-count")
        search_field.append(self.search_count_label)
        search_box.append(search_field)

        def search_control(button):
            button.add_css_class('flat')
            button.add_css_class('search-control')
            button.set_valign(Gtk.Align.CENTER)
            button.update_property([Gtk.AccessibleProperty.LABEL], [button.get_tooltip_text()])
            return button

        # Match options; any active option routes search through ops.text.
        options_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                              margin_top=12, margin_bottom=8, margin_start=12, margin_end=12)
        options_popover = Gtk.Popover(child=options_box)
        options_button = search_control(Gtk.MenuButton(icon_name='view-more-horizontal-symbolic',
                                                       tooltip_text=_("search_options"),
                                                       popover=options_popover))
        self.search_option_buttons = {}

        def search_options_changed(_button):
            active = any(button.get_active() for button in self.search_option_buttons.values())
            if active:
                options_button.add_css_class('search-options-active')
            else:
                options_button.remove_css_class('search-options-active')
            self._on_search_changed(self.search_entry)

        for key in ('case', 'word', 'regex'):
            toggle = Gtk.CheckButton(label=_(f"search_option_{key}"))
            toggle.connect('toggled', search_options_changed)
            self.search_option_buttons[key] = toggle
            options_box.append(toggle)
        options_box.append(Gtk.Separator(margin_top=4, margin_bottom=2))
        for title, action in ((_("replace_title"), 'win.find_replace'),
                              (_("menu_advanced_search"), 'win.advanced_search')):
            button = Gtk.Button(label=title, action_name=action)
            button.add_css_class('flat')
            button.connect('clicked', lambda _button: options_popover.popdown())
            options_box.append(button)
        search_field.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL,
                                          margin_top=10, margin_bottom=10, margin_end=4))
        navigation = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.search_prev_button = Gtk.Button.new_from_icon_name("go-up-symbolic")
        self.search_prev_button.set_tooltip_text(_("search_previous_tip"))
        search_control(self.search_prev_button)
        self.search_prev_button.set_sensitive(False)
        self.search_prev_button.connect("clicked", lambda button: self._step_search(-1))
        navigation.append(self.search_prev_button)
        self.search_next_button = Gtk.Button.new_from_icon_name("go-down-symbolic")
        self.search_next_button.set_tooltip_text(_("search_next_tip"))
        search_control(self.search_next_button)
        self.search_next_button.set_sensitive(False)
        self.search_next_button.connect("clicked", lambda button: self._step_search(1))
        navigation.append(self.search_next_button)
        search_field.append(navigation)
        search_field.append(options_button)
        close_search = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close_search.set_tooltip_text(_("search_close_tip"))
        search_control(close_search)
        close_search.connect("clicked", lambda button: self._hide_search())
        search_field.append(close_search)
        self.search_revealer.set_child(search_toolbar)
        self.main_box.append(self.search_revealer)

        self.tab_view = Adw.TabView()
        self.tab_bar = Adw.TabBar()
        self.tab_bar.add_css_class('document-tabbar')
        self.tab_bar.set_view(self.tab_view)
        self.tab_bar.set_autohide(True)
        self.tab_bar.set_visible(False)

        new_tab_btn = Gtk.Button.new_from_icon_name("tab-new-symbolic")
        new_tab_btn.add_css_class("flat")
        new_tab_btn.set_tooltip_text(_("btn_new_doc"))
        new_tab_btn.connect("clicked", lambda b: self.on_new_clicked())
        self.tab_bar.set_end_action_widget(new_tab_btn)

        try:
            self.tab_bar.setup_extra_drop_target(Gdk.DragAction.COPY, [Gio.File])
            self.tab_bar.connect("extra-drag-drop", self._on_tab_extra_drag_drop)
        except Exception:
            pass

        self.tab_view.connect("notify::selected-page", self._on_tab_selected_page_changed)
        self.tab_view.connect("close-page", self._on_tab_close_page)
        self.tab_view.connect("page-reordered", self._on_tab_page_reordered)

        self.main_box.append(self.tab_bar)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.main_box.append(self.stack)

        welcome_view = WelcomeView(parent_window=self)
        self.stack.add_named(welcome_view, "welcome")

        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=False, vexpand=True, hexpand=True, shrink_start_child=False)
        
        self._create_sidebar()

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0, vexpand=True)
        self._create_main_toolbar()
        content_box.append(self.main_toolbar)
        
        self.pdf_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True,
                                            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.pdf_view = Gtk.DrawingArea(content_width=1, content_height=1,
                                        hexpand=True, vexpand=True)
        self.pdf_view.set_focusable(True)
        self.pdf_view.set_draw_func(self.draw_pdf_page)
        self.pdf_view.connect("resize", self._on_fit_canvas_resize)
        self.pdf_view.add_css_class('pdf-view')

        self.pdf_overlay = Gtk.Overlay()
        self.pdf_overlay.set_child(self.pdf_view)

        self.pdf_viewport = Gtk.Viewport()
        self.pdf_viewport.set_child(self.pdf_overlay)
        self.pdf_scroll.set_child(self.pdf_viewport)
        from .continuous_view import ContinuousView
        self.continuous_view=ContinuousView(self)
        for adjustment in (self.pdf_scroll.get_hadjustment(), self.pdf_scroll.get_vadjustment()):
            adjustment.connect('value-changed', self._on_canvas_scrolled)
        
        self.document_surface=Gtk.Overlay(hexpand=True,vexpand=True)
        self.document_surface.set_child(self.pdf_scroll)
        content_box.append(self.document_surface)
        self._create_statusbar()
        content_box.append(self.status_bar_box)

        self.paned.set_end_child(content_box)
        self.paned.set_resize_start_child(False)
        self.paned.set_resize_end_child(True)
        self.paned.set_position(188)

        # Vertical toolbar on the side + dedicated Page Preview sidebar in paned
        self._create_tools_sidebar()
        self.editor_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, vexpand=True, hexpand=True)
        self.editor_container.append(self.tools_sidebar)
        self.editor_container.append(self.paned)
        self.document_tools = DocumentToolsController(self)
        from .stamp_interaction import StampInteraction
        self.stamp_interaction = StampInteraction(self)
        self.editor_container.append(self.document_tools.sidebar)
        self.form_tools = FormController(self)
        self.editor_container.append(self.form_tools.sidebar)
        from .features import FeatureController
        self.features = FeatureController(self)
        self.main_menu_button.set_menu_model(self.features.build_main_menu())
        for button, model in ((self.pages_tool_button, self.features.pages_menu()),
                              (self.protect_tool_button, self.features.protect_menu())):
            popover = Gtk.PopoverMenu.new_from_model(model)
            button.set_popover(popover)
        # MenuButton positions its popover from its own direction (default DOWN), which
        # overrides the popover's position; in the vertical sidebar every menu opens to the
        # right so tall menus are not clipped below the window.
        child = self.tools_sidebar.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.MenuButton):
                child.set_direction(Gtk.ArrowType.RIGHT)
            child = child.get_next_sibling()
        from .image_editor_dialog import ImageToolsController
        self.image_tools=ImageToolsController(self,self.document_surface)
        from .organize_pages import OrganizePages
        self.organize_pages=OrganizePages(self,self.document_surface)
        from .shape_tools import ShapeTools
        self.shape_tools=ShapeTools(self)
        self.shape_tools.populate_menu(self._shapes_grid,self._shapes_popover)
        from .ai.panel import AiBar
        self.ai_bar=AiBar(self,self.document_surface)

        self.stack.add_named(self.tab_view, "editor")

        if self._active_session:
            self._create_tab_for_session(self._active_session)
            if getattr(self._active_session, 'bin_widget', None) is not None:
                if self.editor_container.get_parent() != self._active_session.bin_widget:
                    old_parent = self.editor_container.get_parent()
                    if old_parent:
                        old_parent.set_child(None)
                    self._active_session.bin_widget.set_child(self.editor_container)

    def _create_statusbar(self):
        """Keep status and page navigation aligned with the document canvas."""
        self.status_bar_box = Gtk.CenterBox(orientation=Gtk.Orientation.HORIZONTAL)
        self.status_bar_box.add_css_class('statusbar')

        self.status_label = Gtk.Label(label=_('new_doc_loaded'), xalign=0.0)
        self.status_label.add_css_class('status-doc-label')
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.status_label.set_max_width_chars(48)
        self.status_label.set_valign(Gtk.Align.CENTER)
        self.status_label.set_tooltip_text(self.status_label.get_text())
        self.status_label.connect("notify::label", lambda label, pspec:
                                  label.set_tooltip_text(label.get_text()))
        status_info = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        status_icon = Gtk.Image(icon_name="text-x-generic-symbolic", pixel_size=14)
        status_icon.add_css_class("status-doc-icon")
        status_info.append(status_icon)
        status_info.append(self.status_label)
        self.status_bar_box.set_start_widget(status_info)

        center_nav_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        center_nav_box.add_css_class("status-control-group")
        
        self.prev_button = Gtk.Button.new_from_icon_name("go-previous-symbolic")
        self.prev_button.set_tooltip_text(_("prev_page_tip"))
        self.prev_button.connect("clicked", self.on_prev_page)
        self.prev_button.add_css_class("flat")
        self.prev_button.add_css_class("status-btn")

        self.page_label = Gtk.Label(label=_("page_info_count").format(0, 0))
        self.page_label.add_css_class("status-page-label")
        self.page_label.set_valign(Gtk.Align.CENTER)

        self.page_slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1, 2, 1)
        self.page_slider.set_draw_value(False)
        self.page_slider.set_size_request(110, -1)
        self.page_slider.add_css_class("compact-page-slider")
        self.page_slider.connect("value-changed", self._on_page_slider_changed)
        self.page_slider.set_visible(False)

        self.next_button = Gtk.Button.new_from_icon_name("go-next-symbolic")
        self.next_button.set_tooltip_text(_("next_page_tip"))
        self.next_button.connect("clicked", self.on_next_page)
        self.next_button.add_css_class("flat")
        self.next_button.add_css_class("status-btn")

        center_nav_box.append(self.prev_button)
        center_nav_box.append(self.page_label)
        center_nav_box.append(self.page_slider)
        center_nav_box.append(self.next_button)
        self.status_bar_box.set_center_widget(center_nav_box)

        zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, halign=Gtk.Align.END, valign=Gtk.Align.CENTER)
        zoom_box.add_css_class("status-control-group")

        zoom_out = Gtk.Button.new_from_icon_name("editor-zoom-out-symbolic")
        zoom_out.set_tooltip_text(_("zoom_out_tip"))
        zoom_out.connect("clicked", self.on_zoom_out)
        zoom_out.add_css_class("flat")
        zoom_out.add_css_class("status-btn")

        self.zoom_label = Gtk.Label(label="100%")
        self.zoom_label.add_css_class("status-zoom-label")
        self.zoom_label.set_width_chars(5)
        self.zoom_label.set_valign(Gtk.Align.CENTER)

        zoom_in = Gtk.Button.new_from_icon_name("editor-zoom-in-symbolic")
        zoom_in.set_tooltip_text(_("zoom_in_tip"))
        zoom_in.connect("clicked", self.on_zoom_in)
        zoom_in.add_css_class("flat")
        zoom_in.add_css_class("status-btn")

        self.scroll_mode_button=Gtk.ToggleButton(icon_name='editor-page-scroll-symbolic')
        self.scroll_mode_button.add_css_class('flat')
        self.scroll_mode_button.add_css_class('status-btn')
        self.scroll_mode_button.set_valign(Gtk.Align.CENTER)
        self.scroll_mode_button.get_child().set_pixel_size(20)
        self.scroll_mode_button.connect('toggled',self._on_scroll_mode_changed)
        zoom_box.append(self.scroll_mode_button)
        zoom_box.append(zoom_out)
        zoom_box.append(self.zoom_label)
        zoom_box.append(zoom_in)
        self.status_bar_box.set_end_widget(zoom_box)

        for button in (self.prev_button, self.next_button, zoom_out, zoom_in):
            button.set_valign(Gtk.Align.CENTER)
            image = button.get_child()
            if isinstance(image, Gtk.Image):
                image.set_pixel_size(16)

    def _toggle_search(self, button=None):
        if not self.doc:
            return
        if self.search_revealer.get_reveal_child():
            self._hide_search()
        else:
            self.search_revealer.set_reveal_child(True)
            self.search_entry.grab_focus()

    def _hide_search(self):
        self.search_revealer.set_reveal_child(False)
        self._clear_search()

    def _clear_search(self):
        self._search_generation += 1
        self._search_document = None
        self.search_results = []
        self.search_current_result = -1
        if hasattr(self, 'search_entry'):
            self._search_resetting = True
            self.search_entry.set_text("")
            self._search_resetting = False
            self.search_count_label.set_text("")
            self.search_revealer.set_reveal_child(False)
            self.search_prev_button.set_sensitive(False)
            self.search_next_button.set_sensitive(False)
        if hasattr(self, 'pdf_view'):
            self.pdf_view.queue_draw()

    def _on_search_changed(self, entry):
        if self._search_resetting:
            return
        self._search_generation += 1
        generation = self._search_generation
        query = entry.get_text().strip()
        self.search_results = []
        self.search_current_result = -1
        self._search_document = self.doc
        self.search_prev_button.set_sensitive(False)
        self.search_next_button.set_sensitive(False)
        self.search_count_label.set_text(_("search_scanning") if query else "")
        self.pdf_view.queue_draw()
        if query and self.doc:
            GLib.timeout_add(200, self._start_search, generation, self.doc, query)

    def _start_search(self, generation, doc, query):
        if generation != self._search_generation or doc is not self.doc:
            return GLib.SOURCE_REMOVE
        GLib.idle_add(self._search_next_page, generation, doc, query, 0)
        return GLib.SOURCE_REMOVE

    def _search_next_page(self, generation, doc, query, page_index):
        if generation != self._search_generation or doc is not self.doc:
            return GLib.SOURCE_REMOVE
        options = {key: button.get_active() for key, button in getattr(self, 'search_option_buttons', {}).items()}
        try:
            if any(options.values()):
                from .ops import text as text_ops
                matches = text_ops.search_page(doc[page_index], query, regex=options['regex'],
                                               case_sensitive=options['case'], whole_word=options['word'])
                self.search_results.extend((page_index, quad.rect) for _match, quads in matches for quad in quads)
            else:
                self.search_results.extend((page_index, rect) for rect in pdf_handler.search_page(doc, page_index, query))
        except Exception as error:
            if page_index == 0:
                self.search_count_label.set_text(str(error))
            logger.warning("Search failed on page %d: %s", page_index + 1, error)
        if page_index + 1 < doc.page_count:
            GLib.idle_add(self._search_next_page, generation, doc, query, page_index + 1)
        elif self.search_results:
            self._go_to_search_result(0)
        else:
            self.search_count_label.set_text(_("search_no_results"))
        return GLib.SOURCE_REMOVE

    def _step_search(self, direction):
        if self.search_results:
            self._go_to_search_result((self.search_current_result + direction) % len(self.search_results))

    def _go_to_search_result(self, index):
        if not self.search_results or self._search_document is not self.doc:
            return
        self.search_current_result = index
        page_index, rect = self.search_results[index]
        if page_index != self.current_page_index:
            self._load_page(page_index)
        self.search_count_label.set_text(_("search_count", index + 1, len(self.search_results)))
        self.search_prev_button.set_sensitive(True)
        self.search_next_button.set_sensitive(True)
        self.pdf_view.queue_draw()

        def scroll_to_result():
            if self.doc is not self._search_document or self.current_page_index != page_index:
                return GLib.SOURCE_REMOVE
            page = self.doc.load_page(page_index)
            visual_rect = rect * page.rotation_matrix
            h_adj = self.pdf_scroll.get_hadjustment()
            v_adj = self.pdf_scroll.get_vadjustment()
            h_adj.set_value(visual_rect.x0 * self.zoom_level - h_adj.get_page_size() / 2)
            v_adj.set_value(visual_rect.y0 * self.zoom_level - v_adj.get_page_size() / 2)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(scroll_to_result)

    def _toggle_sidebar(self, button=None):
        """Toggle sidebar visibility."""
        if hasattr(self, 'sidebar_box') and self.sidebar_box:
            is_vis = self.sidebar_box.get_visible()
            self.sidebar_box.set_visible(not is_vis)
            if hasattr(self, 'toggle_sidebar_button') and self.toggle_sidebar_button:
                if not is_vis:
                    self.toggle_sidebar_button.add_css_class("active")
                else:
                    self.toggle_sidebar_button.remove_css_class("active")

    def _update_window_title(self):
        """Update window title and Adw.WindowTitle widget."""
        if hasattr(self, 'window_title') and self.window_title:
            if self._active_session and self._active_session.doc:
                title = self._active_session.display_title
                if self.document_modified:
                    title = "* " + title
                self.window_title.set_title(title)
                self.window_title.set_tooltip_text(title)
                page_count = pdf_handler.get_page_count(self._active_session.doc)
                self.window_title.set_subtitle(f"Page {self.current_page_index + 1} of {page_count}")
                if 0 <= self.current_page_index < page_count:
                    page = self.doc.load_page(self.current_page_index)
                    self.preview_page_name.set_text(_("page_info").format(self.current_page_index + 1))
                    details = f"{page.rect.width:.0f} × {page.rect.height:.0f} pt · {page.rotation}°"
                    self.preview_page_details.set_text(details)
                    self.preview_page_details.set_tooltip_text(details)
                self.set_title(f"{constants.APP_NAME} - {title}")
                if hasattr(self, 'page_count_badge') and self.page_count_badge:
                    self.page_count_badge.set_text(str(page_count))
            else:
                self.window_title.set_title(constants.APP_NAME)
                self.window_title.set_tooltip_text(None)
                self.preview_page_name.set_text(_("pages_label"))
                self.preview_page_details.set_text("—")
                self.preview_page_details.set_tooltip_text(None)
                self.window_title.set_subtitle(_("app_subtitle"))
                self.set_title(constants.APP_NAME)
                if hasattr(self, 'page_count_badge') and self.page_count_badge:
                    self.page_count_badge.set_text("0")

    def _sync_tool_buttons_active(self):
        """Synchronize active state on sidebar tool buttons with current tool mode."""
        if hasattr(self, '_tool_buttons'):
            curr_mode = getattr(self, 'tool_mode', 'select')
            for t_id, btn in self._tool_buttons.items():
                if t_id == curr_mode:
                    btn.add_css_class("active")
                else:
                    btn.remove_css_class("active")

    def _create_sidebar(self):
        """Create a dedicated sidebar exclusively for document page previews and thumbnails."""
        self.sidebar_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            margin_start=0,
            margin_end=0,
            margin_top=0,
            margin_bottom=0
        )
        self.sidebar_box.set_size_request(172, -1)
        self.sidebar_box.add_css_class("editor-sidebar")
        self.sidebar_box.add_css_class("page-preview-sidebar")

        # 1. Pages / Document Preview Header
        pages_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        pages_header.add_css_class("sidebar-section-header")

        pages_icon = Gtk.Image.new_from_icon_name("editor-pages-symbolic")
        pages_icon.set_pixel_size(16)
        pages_icon.add_css_class("dim-label")
        pages_header.append(pages_icon)

        thumbnails_label = Gtk.Label(label=_("pages_label"), xalign=0.0, hexpand=True)
        thumbnails_label.add_css_class('sidebar-section-title')
        pages_header.append(thumbnails_label)

        self.page_count_badge = Gtk.Label(label="0")
        self.page_count_badge.add_css_class("sidebar-count-badge")
        pages_header.append(self.page_count_badge)

        self.hide_pages_button = Gtk.Button.new_from_icon_name("sidebar-hide-symbolic")
        self.hide_pages_button.set_tooltip_text("Hide Pages sidebar (F9)")
        self.hide_pages_button.add_css_class("flat")
        self.hide_pages_button.add_css_class("pages-hide-button")
        self.hide_pages_button.connect("clicked", self._toggle_sidebar)
        pages_header.append(self.hide_pages_button)

        self.sidebar_box.append(pages_header)

        # Page action buttons row
        page_actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, halign=Gtk.Align.FILL)
        page_actions_box.add_css_class("sidebar-actions-row")

        self.organize_pages_button = Gtk.Button(icon_name="editor-organize-symbolic", action_name="win.organize_pages",
                                                tooltip_text=_("organize_title"))
        page_actions_box.append(self.organize_pages_button)

        self.sidebar_add_page_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.sidebar_add_page_button.set_tooltip_text(_("add_page_tip"))
        self.sidebar_add_page_button.connect("clicked", self.on_add_page)
        page_actions_box.append(self.sidebar_add_page_button)

        self.sidebar_duplicate_page_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        self.sidebar_duplicate_page_button.set_tooltip_text(_("duplicate_page_tip"))
        self.sidebar_duplicate_page_button.connect("clicked", self.on_duplicate_page)
        page_actions_box.append(self.sidebar_duplicate_page_button)

        self.rotate_page_ccw_button = Gtk.Button.new_from_icon_name("editor-rotate-left-symbolic")
        self.rotate_page_ccw_button.set_tooltip_text(_("rotate_page_ccw_tip"))
        self.rotate_page_ccw_button.connect('clicked', lambda b: self.rotate_current_page(-90))
        self.rotate_page_ccw_button.add_css_class('flat')
        self.rotate_page_ccw_button.add_css_class('sidebar-action-btn')
        page_actions_box.append(self.rotate_page_ccw_button)

        self.rotate_page_cw_button = Gtk.Button.new_from_icon_name("editor-rotate-right-symbolic")
        self.rotate_page_cw_button.set_tooltip_text(_("rotate_page_cw_tip"))
        self.rotate_page_cw_button.connect('clicked', lambda b: self.rotate_current_page(90))
        self.rotate_page_cw_button.add_css_class('flat')
        self.rotate_page_cw_button.add_css_class('sidebar-action-btn')
        page_actions_box.append(self.rotate_page_cw_button)

        self.delete_page_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self.delete_page_button.set_tooltip_text(_("delete_page_tip"))
        self.delete_page_button.connect('clicked', self.on_delete_page)
        self.delete_page_button.add_css_class('flat')
        self.delete_page_button.add_css_class('sidebar-action-btn')
        self.delete_page_button.add_css_class("page-delete-btn")
        page_actions_box.append(self.delete_page_button)

        for button in (self.sidebar_add_page_button, self.sidebar_duplicate_page_button,
                       self.rotate_page_ccw_button, self.rotate_page_cw_button,
                       self.delete_page_button):
            button.add_css_class("flat")
            button.add_css_class("sidebar-action-btn")
            button.set_hexpand(True)
            button.set_valign(Gtk.Align.CENTER)
            image = button.get_child()
            if isinstance(image, Gtk.Image):
                image.set_pixel_size(16)

        self.sidebar_box.append(page_actions_box)
        self.sidebar_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # 2. Thumbnails GridView inside ScrolledWindow (occupies full height of the sidebar)
        factory = PageThumbnailFactory(editor_window=self)
        self.thumbnails_list = Gtk.GridView.new(None, factory)
        self.thumbnails_list.set_max_columns(1)
        self.thumbnails_list.set_min_columns(1)
        self.thumbnails_list.set_vexpand(True)
        self.thumbnails_list.add_css_class("thumbnail-gridview")

        self.thumbnail_selection_model = Gtk.SingleSelection(model=self.pages_model)
        self.thumbnails_list.set_model(self.thumbnail_selection_model)
        self.thumbnail_selection_model.connect("selection-changed", self.on_thumbnail_selected)

        thumbnails_scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        thumbnails_scroll.set_child(self.thumbnails_list)
        thumbnails_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sidebar_box.append(thumbnails_scroll)

        page_details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        page_details.add_css_class("page-preview-footer")
        self.preview_page_name = Gtk.Label(xalign=0)
        self.preview_page_name.add_css_class("page-preview-name")
        self.preview_page_details = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        self.preview_page_details.add_css_class("page-preview-details")
        page_details.append(self.preview_page_name)
        page_details.append(self.preview_page_details)
        self.sidebar_box.append(page_details)
        self.paned.set_start_child(self.sidebar_box)

    def _create_tools_sidebar(self):
        """Create a sleek vertical toolbar on the side with icon-only tools and hover tooltips."""
        self.tools_sidebar = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=2,
            margin_start=2,
            margin_end=2,
            margin_top=4,
            margin_bottom=4
        )
        self.tools_sidebar.set_size_request(48, -1)
        self.tools_sidebar.add_css_class("tools-side-toolbar")

        self._tool_buttons = {}

        def _size_tool_btn(button):
            button.add_css_class("side-tool-btn")
            button.set_size_request(36, 36)
            button.set_halign(Gtk.Align.CENTER)
            icon = button.get_child()
            if isinstance(icon, Gtk.Image):
                icon.set_pixel_size(20)

        def _make_tool_btn(icon_name, tooltip_key, tool_id):
            btn = Gtk.Button.new_from_icon_name(icon_name)
            btn.set_tooltip_text(_(tooltip_key))
            btn.add_css_class("flat")
            _size_tool_btn(btn)
            btn.connect('clicked', self.on_tool_selected, tool_id)
            self._tool_buttons[tool_id] = btn
            return btn

        # 1. Navigation / Pointer Tools (Icon-only, hover tooltip)
        self.select_tool_button = _make_tool_btn("editor-pointer-symbolic", "tool_select_tip", "select")
        self.drag_tool_button = _make_tool_btn("editor-pan-symbolic", "tool_drag_tip", "drag")
        self.tools_sidebar.append(self.select_tool_button)
        self.tools_sidebar.append(self.drag_tool_button)

        self.tools_sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=2, margin_bottom=2))

        # 2. Content & Insert Tools (Icon-only, hover tooltip)
        self.add_text_tool_button = _make_tool_btn("editor-text-symbolic", "tool_add_text_tip", "add_text")
        self.add_image_tool_button = _make_tool_btn("editor-image-symbolic", "tool_add_image_tip", "add_image")

        self.symbols_button = Gtk.MenuButton()
        self.symbols_button.set_child(Gtk.Image(icon_name="editor-symbols-symbolic", pixel_size=20))
        self.symbols_button.set_tooltip_text(_("tooltip_symbols"))
        self.symbols_button.add_css_class("flat")
        _size_tool_btn(self.symbols_button)
        self.symbols_popover = SymbolsPopover(editor_window=self)
        self.symbols_button.set_popover(self.symbols_popover)

        self.tools_sidebar.append(self.add_text_tool_button)
        self.tools_sidebar.append(self.add_image_tool_button)
        self.table_tool_button = Gtk.Button.new_from_icon_name("editor-table-symbolic")
        self.table_tool_button.set_tooltip_text(_("table_add"))
        self.table_tool_button.add_css_class("flat")
        _size_tool_btn(self.table_tool_button)
        self.table_tool_button.connect("clicked", self.on_add_table)
        self.tools_sidebar.append(self.table_tool_button)
        self.tools_sidebar.append(self.symbols_button)
        self.signature_tool_button = Gtk.Button.new_from_icon_name("editor-signature-symbolic")
        self.signature_tool_button.set_tooltip_text(_("signature_add"))
        self.signature_tool_button.add_css_class("flat")
        _size_tool_btn(self.signature_tool_button)
        self.signature_tool_button.connect("clicked", self.on_add_signature)
        self._tool_buttons["signature"] = self.signature_tool_button
        self.tools_sidebar.append(self.signature_tool_button)

        self.forms_tool_button = Gtk.MenuButton(icon_name='editor-form-symbolic',
                                                tooltip_text=_("menu_form_fields"))
        self.forms_tool_button.add_css_class('flat')
        _size_tool_btn(self.forms_tool_button)
        forms_popover = Gtk.Popover(position=Gtk.PositionType.RIGHT)
        forms_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                            margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        for action, key, icon in (
            ('form_fields', 'tool_fill_forms', 'editor-match-style-symbolic'),
            ('create_field', 'tool_create_field', 'editor-form-symbolic'),
            ('flatten_forms', 'tool_flatten', 'editor-flatten-symbolic'),
        ):
            button = Gtk.Button(action_name=f'win.{action}', tooltip_text=_(key + '_tip'))
            button.add_css_class('flat')
            row = Gtk.Box(spacing=10)
            row.append(Gtk.Image(icon_name=icon, pixel_size=20))
            row.append(Gtk.Label(label=_(key), xalign=0, hexpand=True))
            button.set_child(row)
            button.connect('clicked', lambda button: forms_popover.popdown())
            forms_box.append(button)
        forms_popover.set_child(forms_box)
        self.forms_tool_button.set_popover(forms_popover)
        self.tools_sidebar.append(self.forms_tool_button)
        self.bookmarks_tool_button = Gtk.Button(icon_name='editor-bookmark-symbolic',
                                                action_name='win.bookmarks',tooltip_text=_("menu_bookmarks"))
        self.bookmarks_tool_button.add_css_class('flat')
        _size_tool_btn(self.bookmarks_tool_button)
        self.tools_sidebar.append(self.bookmarks_tool_button)

        self.document_tools_button = Gtk.MenuButton(icon_name='editor-document-tools-symbolic',
                                                    tooltip_text=_("menu_more_tools"))
        self.document_tools_button.add_css_class('flat')
        _size_tool_btn(self.document_tools_button)
        tools_popover = Gtk.Popover(position=Gtk.PositionType.RIGHT)
        tools_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                            margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        for entry in (
            'menu_section_review',
            ('review', 'tool_add_review', 'editor-review-symbolic'),
            ('comments', 'tool_comments', 'editor-comments-symbolic'),
            'menu_section_page_look',
            ('decorate', 'tool_decorate', 'editor-watermark-symbolic'),
            ('crop', 'tool_crop', 'editor-crop-symbolic'),
            'menu_section_protect',
            ('redact', 'tool_redact', 'editor-redact-symbolic'),
        ):
            if isinstance(entry, str):
                header = Gtk.Label(label=_(entry), xalign=0, margin_start=8, margin_top=6 if tools_box.get_first_child() else 0)
                header.add_css_class('pdflx-menu-section')
                tools_box.append(header)
                continue
            action, label, icon = entry
            button = Gtk.Button(action_name=f'win.{action}')
            button.add_css_class('flat')
            row = Gtk.Box(spacing=10)
            row.append(Gtk.Image(icon_name=icon, pixel_size=20))
            row.append(Gtk.Label(label=_(label), xalign=0, hexpand=True))
            button.set_child(row)
            button.connect('clicked', lambda button: tools_popover.popdown())
            tools_box.append(button)
        tools_popover.set_child(tools_box)
        self.document_tools_button.set_popover(tools_popover)
        self.tools_sidebar.append(self.document_tools_button)

        # Page and protection workflows share the header menu's models; the
        # models are attached once the feature controller exists.
        self.pages_tool_button = Gtk.MenuButton(icon_name='editor-pages-symbolic',
                                                tooltip_text=_("menu_group_pages"))
        self.protect_tool_button = Gtk.MenuButton(icon_name='channel-secure-symbolic',
                                                  tooltip_text=_("menu_group_protect"))
        for button in (self.pages_tool_button, self.protect_tool_button):
            button.add_css_class('flat')
            _size_tool_btn(button)
            self.tools_sidebar.append(button)

        self.tools_sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=2, margin_bottom=2))

        # 3. Drawing Tools (Icon-only, hover tooltip)
        self.pen_tool_button = _make_tool_btn("editor-pen-symbolic", "tool_pen_tip", "pen")
        self.tools_sidebar.append(self.pen_tool_button)

        self.tools_sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=2, margin_bottom=2))

        # 4. Shapes & Stamp Tools (Icon-only, hover tooltip)
        self.add_rectangle_tool_button = _make_tool_btn("editor-rectangle-symbolic", "tool_rectangle_tip", "add_rectangle")
        self.add_ellipse_tool_button = _make_tool_btn("editor-ellipse-symbolic", "tool_ellipse_tip", "add_ellipse")
        self.checkmark_tool_button = _make_tool_btn("editor-check-symbolic", "tool_checkmark_tip", "add_checkmark")
        self.cross_tool_button = _make_tool_btn("editor-cross-symbolic", "tool_cross_tip", "add_cross")

        self.shapes_tool_button = Gtk.MenuButton(icon_name='editor-shapes-symbolic',
                                                tooltip_text=_("tool_shapes_tip"))
        self.shapes_tool_button.add_css_class('flat')
        _size_tool_btn(self.shapes_tool_button)
        shapes_popover = Gtk.Popover(position=Gtk.PositionType.RIGHT)
        shapes_grid = Gtk.Grid(column_spacing=6, row_spacing=6,
                              margin_top=8, margin_bottom=8, margin_start=8, margin_end=8)
        for index, button in enumerate((self.add_rectangle_tool_button, self.add_ellipse_tool_button,
                                        self.checkmark_tool_button, self.cross_tool_button)):
            shapes_grid.attach(button, index % 2, index // 2, 1, 1)
            button.connect('clicked', lambda _button: shapes_popover.popdown())
        shapes_popover.set_child(shapes_grid)
        self._shapes_grid, self._shapes_popover = shapes_grid, shapes_popover
        self.shapes_tool_button.set_popover(shapes_popover)
        self.tools_sidebar.append(self.shapes_tool_button)
        self.stamp_tool_button = Gtk.Button(icon_name='editor-stamp-symbolic',action_name='win.stamp',
                                           tooltip_text=_("tool_add_stamp"))
        self.stamp_tool_button.add_css_class('flat')
        _size_tool_btn(self.stamp_tool_button)
        self.tools_sidebar.append(self.stamp_tool_button)
        self._tool_buttons['stamp'] = self.stamp_tool_button

        self.tools_sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL, margin_top=2, margin_bottom=2))

        # 5. Highlight & Annotation Tools (Icon-only, hover tooltip)
        hl_icon = "editor-highlight-symbolic"
        self.highlight_button = Gtk.Button.new_from_icon_name(hl_icon)
        self.highlight_button.set_tooltip_text(_("highlight_tip"))
        self.highlight_button.add_css_class("flat")
        _size_tool_btn(self.highlight_button)
        self.highlight_button.connect("clicked", self.on_highlight_clicked)
        self._tool_buttons["highlighter"] = self.highlight_button
        self.tools_sidebar.append(self.highlight_button)

        rm_icon = "editor-erase-highlight-symbolic"
        self.remove_highlight_button = Gtk.Button.new_from_icon_name(rm_icon)
        self.remove_highlight_button.set_tooltip_text(_("erase_highlight_tip"))
        self.remove_highlight_button.add_css_class("flat")
        _size_tool_btn(self.remove_highlight_button)
        self.remove_highlight_button.connect("clicked", self.on_remove_highlight_clicked)
        self._tool_buttons["erase_highlight"] = self.remove_highlight_button
        self.tools_sidebar.append(self.remove_highlight_button)

        self.highlight_color_rgba = Gdk.RGBA()
        self.highlight_color_rgba.red, self.highlight_color_rgba.green, self.highlight_color_rgba.blue = self.highlighter_color
        self.highlight_color_rgba.alpha = 1.0
        self.highlight_color_button = ColorSwatchButton(compact=True)
        self.highlight_color_button.set_tooltip_text(_("highlight_color_tip"))
        _size_tool_btn(self.highlight_color_button)
        self.highlight_color_button.set_rgba(self.highlight_color_rgba)
        self.highlight_color_button.connect("color-set", self._on_highlight_color_changed)
        self.tools_sidebar.append(self.highlight_color_button)

        self.note_tool_button = Gtk.Button(icon_name='editor-sticky-note-symbolic', action_name='win.sticky_note',
                                           tooltip_text=_("tool_add_note"))
        self.note_tool_button.add_css_class('flat')
        _size_tool_btn(self.note_tool_button)

        # Final order: pointer, edit content, annotate, fill & sign, document.
        while child := self.tools_sidebar.get_first_child():
            self.tools_sidebar.remove(child)
        groups = (
            (self.select_tool_button, self.drag_tool_button),
            (self.add_text_tool_button, self.add_image_tool_button, self.shapes_tool_button,
             self.pen_tool_button, self.table_tool_button, self.symbols_button),
            (self.highlight_button, self.remove_highlight_button, self.highlight_color_button,
             self.note_tool_button, self.stamp_tool_button),
            (self.signature_tool_button, self.forms_tool_button),
            (self.pages_tool_button, self.bookmarks_tool_button, self.protect_tool_button,
             self.document_tools_button),
        )
        for index, group in enumerate(groups):
            if index:
                self.tools_sidebar.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL,
                                                        margin_top=3, margin_bottom=3))
            for button in group:
                self.tools_sidebar.append(button)

    def _create_main_toolbar(self):
        """Create main formatting toolbar for active tools."""
        self.main_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.main_toolbar.add_css_class('toolbar')

        page_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        page_actions.add_css_class('linked')
        self.add_page_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
        self.add_page_button.set_tooltip_text(_("add_page_tip"))
        self.add_page_button.connect("clicked", self.on_add_page)
        page_actions.append(self.add_page_button)

        self.duplicate_page_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        self.duplicate_page_button.set_tooltip_text(_("duplicate_page_tip"))
        self.duplicate_page_button.connect("clicked", self.on_duplicate_page)
        page_actions.append(self.duplicate_page_button)
        self.main_toolbar.append(page_actions)
        self.main_toolbar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        self.tool_context_label = Gtk.Label(xalign=0.0)
        self.tool_context_label.add_css_class('tool-context-label')
        self.main_toolbar.append(self.tool_context_label)

        # Formatting controls row (visible during text/shape/stroke editing)
        self.toolbar_row2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, hexpand=True)
        self.main_toolbar.append(self.toolbar_row2)

        self.toolbar_hint = Gtk.Label(xalign=0.0, hexpand=True)
        self.toolbar_hint.add_css_class('toolbar-hint')
        self.toolbar_row2.append(self.toolbar_hint)

        self.text_format_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=4, margin_end=4)
        self.toolbar_row2.append(self.text_format_sep)
        self.text_format_sep.set_visible(False)

        self.text_format_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.font_store = Gtk.ListStore(str, str)
        self.font_store.append([_("font_loading"), ""])
        self.font_combo = Gtk.ComboBox(model=self.font_store)
        cell = Gtk.CellRendererText()
        self.font_combo.pack_start(cell, True)
        self.font_combo.add_attribute(cell, "text", 0)
        self.font_combo.set_active(0)
        self.font_combo.set_tooltip_text(_("font_tip"))
        self.font_combo.connect("changed", self.on_text_format_changed)
        self.font_combo.set_sensitive(False)
        self.text_format_box.append(self.font_combo)

        self.font_size_spin = Gtk.SpinButton.new_with_range(6, 96, 1)
        self.font_size_spin.set_value(11)
        self.font_size_spin.set_tooltip_text(_("font_size_tip"))
        self.font_size_spin.connect("value-changed", self.on_text_format_changed)
        self.text_format_box.append(self.font_size_spin)

        self.bold_button = Gtk.ToggleButton(icon_name="format-text-bold-symbolic")
        self.bold_button.set_tooltip_text(_("bold_tip"))
        self.bold_button.connect("toggled", self.on_text_format_changed)
        self.text_format_box.append(self.bold_button)

        self.italic_button = Gtk.ToggleButton(icon_name="format-text-italic-symbolic")
        self.italic_button.set_tooltip_text(_("italic_tip"))
        self.italic_button.connect("toggled", self.on_text_format_changed)
        self.text_format_box.append(self.italic_button)

        self.underline_button = Gtk.ToggleButton(icon_name="format-text-underline-symbolic")
        self.underline_button.set_tooltip_text(_("underline_tip"))
        self.underline_button.connect("toggled", self.on_text_format_changed)
        self.text_format_box.append(self.underline_button)

        self.strikethrough_button = Gtk.ToggleButton(icon_name="format-text-strikethrough-symbolic")
        self.strikethrough_button.set_tooltip_text(_("strikethrough_tip"))
        self.strikethrough_button.connect("toggled", self.on_text_format_changed)
        self.text_format_box.append(self.strikethrough_button)

        align_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        align_box.add_css_class("linked")

        self.align_left_button = Gtk.ToggleButton(icon_name="format-justify-left-symbolic")
        self.align_left_button.set_tooltip_text(_("align_left_tip"))
        self.align_left_button.set_active(True)
        self.align_left_button.connect("toggled", self.on_text_format_changed)
        align_box.append(self.align_left_button)

        self.align_center_button = Gtk.ToggleButton(icon_name="format-justify-center-symbolic")
        self.align_center_button.set_tooltip_text(_("align_center_tip"))
        self.align_center_button.set_group(self.align_left_button)
        self.align_center_button.connect("toggled", self.on_text_format_changed)
        align_box.append(self.align_center_button)

        self.align_right_button = Gtk.ToggleButton(icon_name="format-justify-right-symbolic")
        self.align_right_button.set_tooltip_text(_("align_right_tip"))
        self.align_right_button.set_group(self.align_left_button)
        self.align_right_button.connect("toggled", self.on_text_format_changed)
        align_box.append(self.align_right_button)

        self.align_justify_button = Gtk.ToggleButton(icon_name="format-justify-fill-symbolic")
        self.align_justify_button.set_tooltip_text(_("align_justify_tip"))
        self.align_justify_button.set_group(self.align_left_button)
        self.align_justify_button.connect("toggled", self.on_text_format_changed)
        align_box.append(self.align_justify_button)

        self.text_format_box.append(align_box)

        self.color_button = ColorSwatchButton("editor-text-symbolic")
        default_rgba = Gdk.RGBA()
        default_rgba.parse("black")
        self.color_button.set_rgba(default_rgba)
        self.color_button.set_tooltip_text(_("color_tip"))
        self.color_button.connect("color-set", self.on_text_format_changed)
        self.text_format_box.append(self.color_button)
        self.toolbar_row2.append(self.text_format_box)

        self.shape_toolbar_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=4, margin_end=4)
        self.toolbar_row2.append(self.shape_toolbar_sep)
        self.shape_toolbar_sep.set_visible(False)

        self.shape_toolbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.shape_fill_button = ColorSwatchButton("editor-fill-symbolic")
        self.shape_fill_button.set_title(_("shape_fill_dialog_title"))
        fill_rgba = Gdk.RGBA()
        fill_rgba.parse("white")
        self.shape_fill_button.set_rgba(fill_rgba)
        self.shape_fill_button.set_tooltip_text(_("shape_fill_tip"))
        self.shape_fill_button.connect("color-set", self.on_shape_format_changed)
        self.shape_toolbar_box.append(self.shape_fill_button)

        self.shape_transparent_toggle = Gtk.ToggleButton(label=_("transparent_label"))
        self.shape_transparent_toggle.set_active(True)
        self.shape_transparent_toggle.set_tooltip_text(_("shape_transparent_tip"))
        self.shape_transparent_toggle.connect("toggled", self.on_shape_format_changed)
        self.shape_toolbar_box.append(self.shape_transparent_toggle)

        self.shape_stroke_button = ColorSwatchButton("editor-outline-symbolic")
        self.shape_stroke_button.set_title(_("shape_stroke_dialog_title"))
        stroke_rgba = Gdk.RGBA()
        stroke_rgba.parse("black")
        self.shape_stroke_button.set_rgba(stroke_rgba)
        self.shape_stroke_button.set_tooltip_text(_("shape_stroke_tip"))
        self.shape_stroke_button.connect("color-set", self.on_shape_format_changed)
        self.shape_toolbar_box.append(self.shape_stroke_button)

        self.shape_stroke_width_spin = Gtk.SpinButton.new_with_range(0.5, 10, 0.5)
        self.shape_stroke_width_spin.set_value(2.0)
        self.shape_stroke_width_spin.set_tooltip_text(_("shape_width_tip"))
        self.shape_stroke_width_spin.connect("value-changed", self.on_shape_format_changed)
        self.shape_toolbar_box.append(self.shape_stroke_width_spin)
        self.toolbar_row2.append(self.shape_toolbar_box)
        self.shape_toolbar_box.set_visible(False)

        self.stroke_toolbar_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=4, margin_end=4)
        self.toolbar_row2.append(self.stroke_toolbar_sep)
        self.stroke_toolbar_sep.set_visible(False)

        self.stroke_toolbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.stroke_color_button = ColorSwatchButton("editor-pen-symbolic")
        self.stroke_color_button.set_tooltip_text(_("stroke_color_tip"))
        stroke_color_rgba = Gdk.RGBA()
        stroke_color_rgba.parse("black")
        self.stroke_color_button.set_rgba(stroke_color_rgba)
        self.stroke_color_button.connect("color-set", self.on_stroke_format_changed)
        self.stroke_toolbar_box.append(self.stroke_color_button)

        self.stroke_width_spin = Gtk.SpinButton.new_with_range(0.5, 40, 0.5)
        self.stroke_width_spin.set_value(2.0)
        self.stroke_width_spin.set_tooltip_text(_("stroke_width_tip"))
        self.stroke_width_spin.connect("value-changed", self.on_stroke_format_changed)
        self.stroke_toolbar_box.append(self.stroke_width_spin)
        self.toolbar_row2.append(self.stroke_toolbar_box)
        self.stroke_toolbar_box.set_visible(False)

        self.rotation_toolbar_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=4, margin_end=4)
        self.toolbar_row2.append(self.rotation_toolbar_sep)
        self.rotation_toolbar_sep.set_visible(False)

        self.rotation_toolbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.rotate_obj_ccw_button = Gtk.Button.new_from_icon_name("editor-rotate-left-symbolic")
        self.rotate_obj_ccw_button.set_tooltip_text(_("rotate_obj_ccw_tip"))
        self.rotate_obj_ccw_button.connect("clicked", self.on_rotate_object_ccw_clicked)
        self.rotation_toolbar_box.append(self.rotate_obj_ccw_button)

        self.rotate_obj_cw_button = Gtk.Button.new_from_icon_name("editor-rotate-right-symbolic")
        self.rotate_obj_cw_button.set_tooltip_text(_("rotate_obj_cw_tip"))
        self.rotate_obj_cw_button.connect("clicked", self.on_rotate_object_cw_clicked)
        self.rotation_toolbar_box.append(self.rotate_obj_cw_button)

        rot_adj = Gtk.Adjustment.new(0.0, 0.0, 360.0, 1.0, 15.0, 0.0)
        self.rotation_spin = Gtk.SpinButton(adjustment=rot_adj, climb_rate=1.0, digits=0)
        self.rotation_spin.set_wrap(True)
        self.rotation_spin.set_tooltip_text(_("rotation_angle_tip"))
        self.rotation_spin.connect("value-changed", self.on_object_rotation_spin_changed)
        self.rotation_toolbar_box.append(self.rotation_spin)

        rot_deg_lbl = Gtk.Label(label="°")
        rot_deg_lbl.add_css_class("dim-label")
        self.rotation_toolbar_box.append(rot_deg_lbl)

        self.rotate_obj_reset_button = Gtk.Button(label="0°")
        self.rotate_obj_reset_button.set_tooltip_text(_("rotation_reset_tip"))
        self.rotate_obj_reset_button.connect("clicked", self.on_rotate_object_reset_clicked)
        self.rotation_toolbar_box.append(self.rotate_obj_reset_button)

        self.toolbar_row2.append(self.rotation_toolbar_box)
        self.rotation_toolbar_box.set_visible(False)

    def _setup_controllers(self):
        """Setup controllers."""
        drop_target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_target.connect('drop', self.on_drop)
        self.add_controller(drop_target)

        scroll_controller = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_controller.connect('scroll', self.on_scroll_zoom)
        self.pdf_scroll.add_controller(scroll_controller)
        self.page_scroll_controller = scroll_controller

        motion_controller = Gtk.EventControllerMotion.new()
        motion_controller.connect('motion', self._on_pointer_motion)
        self.pdf_view.add_controller(motion_controller)

        click_controller = Gtk.GestureClick.new()
        click_controller.set_button(1)
        click_controller.connect('pressed', self.on_pdf_view_pressed)
        self.pdf_view.add_controller(click_controller)

        right_click_controller = Gtk.GestureClick.new()
        right_click_controller.set_button(3)
        right_click_controller.connect('pressed', self._on_right_click)
        self.pdf_view.add_controller(right_click_controller)

        middle_click_controller = Gtk.GestureClick.new()
        middle_click_controller.set_button(2)
        middle_click_controller.connect('pressed', self._on_middle_click)
        self.pdf_view.add_controller(middle_click_controller)

        key_controller = Gtk.EventControllerKey.new()
        key_controller.connect('key-pressed', self.on_key_pressed)
        self.add_controller(key_controller)

        drag_controller = Gtk.GestureDrag.new()
        drag_controller.set_button(Gdk.BUTTON_PRIMARY)
        drag_controller.connect("drag-begin", self.on_drag_begin)
        drag_controller.connect("drag-update", self.on_drag_update)
        drag_controller.connect("drag-end", self.on_drag_end)
        drag_controller.connect("cancel", lambda gesture, sequence: self.stamp_interaction.cancel())
        self.pdf_view.add_controller(drag_controller)
        # Keep click selection and double-click text editing active when the
        # drag gesture claims the same primary-button sequence.
        drag_controller.group(click_controller)

        thumbnail_drop = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        thumbnail_drop.connect('drop', self.on_thumbnail_drop)
        self.thumbnails_list.add_controller(thumbnail_drop)

    def _connect_actions(self):
        """Connect actions."""
        action_save_as = Gio.SimpleAction.new('save_as', None)
        action_save_as.connect('activate', self.on_save_as)
        self.add_action(action_save_as)

        action_export_as = Gio.SimpleAction.new('export_as', None)
        action_export_as.connect('activate', self.on_export_as)
        self.add_action(action_export_as)

        for fmt in ("docx", "txt"):
            act = Gio.SimpleAction.new(f"export_{fmt}", None)
            act.connect("activate", getattr(self, f"on_export_{fmt}"))
            self.add_action(act)

        action_print = Gio.SimpleAction.new('print', None)
        action_print.connect('activate', self.on_print_activated)
        self.add_action(action_print)

        action_about = Gio.SimpleAction.new('about', None)
        action_about.connect('activate', self.on_about_activated)
        self.add_action(action_about)

        action_updates = Gio.SimpleAction.new('check_updates', None)
        action_updates.connect('activate', self.on_check_updates)
        self.add_action(action_updates)

        action_new = Gio.SimpleAction.new('new', None)
        action_new.connect('activate', lambda a, p: self.on_new_clicked(None))
        self.add_action(action_new)

        action_open = Gio.SimpleAction.new('open', None)
        action_open.connect('activate', lambda a, p: self.on_open_clicked(None))
        self.add_action(action_open)

        action_quick_guide = Gio.SimpleAction.new('quick_guide', None)
        action_quick_guide.connect('activate', self._on_quick_guide_activated)
        self.add_action(action_quick_guide)

        action_save = Gio.SimpleAction.new('save', None)
        action_save.connect('activate', lambda a, p: self.on_save_clicked(None))
        self.add_action(action_save)

        action_search = Gio.SimpleAction.new('search', None)
        action_search.connect('activate', lambda a, p: self._toggle_search())
        self.add_action(action_search)

        action_save_page = Gio.SimpleAction.new('save_page_pdf', None)
        action_save_page.connect('activate', self.on_save_page_pdf)
        self.add_action(action_save_page)

        for action_name, handler in (
            ('document_properties', self.on_document_properties),
            ('bookmarks', self.on_bookmarks),
            ('form_fields', self.on_form_fields),
            ('find_replace', self.on_find_replace),
            ('organize_pages', lambda a,p:self.organize_pages.open()),
            ('redact', lambda a,p:self.document_tools.redaction()),
            ('review', lambda a,p:self.document_tools.review()),
            ('sticky_note', lambda a,p:self.document_tools.start_note()),
            ('stamp', lambda a,p:self.document_tools.stamp()),
            ('comments', lambda a,p:self.document_tools.show_comments()),
            ('decorate', lambda a,p:self.document_tools.decorations()),
            ('crop', lambda a,p:self.document_tools.crop()),
            ('create_field', lambda a,p:self.form_tools.create_field()),
            ('flatten_forms', lambda a,p:self.document_tools.flatten()),
            ('add_table', self.on_add_table),
            ('paste_table', self.on_paste_table),
            ('add_signature', self.on_add_signature),
            ('sign_certificate', self.on_sign_certificate),
            ('export_range', self.on_export_range),
            ('export_page_png', lambda action, param: self.on_export_page_visual('png')),
            ('export_page_svg', lambda action, param: self.on_export_page_visual('svg')),
            ('extract_images', self.on_extract_images),
            ('extract_tables', self.on_extract_tables),
        ):
            action = Gio.SimpleAction.new(action_name, None)
            action.connect('activate', handler)
            self.add_action(action)

        action_undo = Gio.SimpleAction.new("undo", None)
        action_undo.connect("activate", lambda a, p: self.undo_manager.undo())
        self.add_action(action_undo)

        action_redo = Gio.SimpleAction.new("redo", None)
        action_redo.connect("activate", lambda a, p: self.undo_manager.redo())
        self.add_action(action_redo)

        current_confirm = bool(get_setting("confirm_delete_objects", True))
        self.action_confirm_delete = Gio.SimpleAction.new_stateful(
            'confirm_delete', None, GLib.Variant.new_boolean(current_confirm)
        )
        def on_confirm_delete_change(action, value):
            new_val = value.get_boolean()
            action.set_state(value)
            set_setting("confirm_delete_objects", new_val)
        self.action_confirm_delete.connect('change-state', on_confirm_delete_change)
        self.add_action(self.action_confirm_delete)

        action_close_tab = Gio.SimpleAction.new('close_tab', None)
        action_close_tab.connect('activate', self.on_close_tab)
        self.add_action(action_close_tab)

        from .form_builder import style as form_style, save_style as save_form_style
        action_guides = Gio.SimpleAction.new_stateful('alignment_guides', None,
                                                     GLib.Variant.new_boolean(form_style()['snap']))
        def on_guides(action, value):
            action.set_state(value)
            save_form_style({'snap': value.get_boolean()})
            forms = getattr(self, 'form_tools', None)
            if forms and forms.builder.snap.get_active() != value.get_boolean():
                forms.builder.snap.set_active(value.get_boolean())
        action_guides.connect('change-state', on_guides)
        self.add_action(action_guides)

        action_ai_settings = Gio.SimpleAction.new('ai_settings', None)
        action_ai_settings.connect('activate', lambda *_: self.ai_bar.open_settings())
        self.add_action(action_ai_settings)

        action_ai = Gio.SimpleAction.new('ai_assistant', None)
        action_ai.connect('activate', lambda *_: self.ai_bar.toggle())
        self.add_action(action_ai)

        action_next_tab = Gio.SimpleAction.new('next_tab', None)
        action_next_tab.connect('activate', self.on_next_tab)
        self.add_action(action_next_tab)

        action_prev_tab = Gio.SimpleAction.new('prev_tab', None)
        action_prev_tab.connect('activate', self.on_prev_tab)
        self.add_action(action_prev_tab)

        app = self.get_application()
        if app:
            app.set_accels_for_action("win.new", ["<Control>n"])
            app.set_accels_for_action("win.open", ["<Control>o"])
            app.set_accels_for_action("win.close_tab", ["<Control>w"])
            app.set_accels_for_action("win.next_tab", ["<Control>Page_Down", "<Control>Tab"])
            app.set_accels_for_action("win.prev_tab", ["<Control>Page_Up", "<Control><Shift>Tab", "<Control><Shift>ISO_Left_Tab"])
            app.set_accels_for_action("win.save", ["<Control>s"])
            app.set_accels_for_action("win.ai_assistant", ["<Control>k"])
            app.set_accels_for_action("win.find_replace", ["<Control>h"])
            app.set_accels_for_action("win.search", ["<Control>f"])
            app.set_accels_for_action("win.save_as", ["<Control><Shift>s"])
            app.set_accels_for_action("win.undo", ["<Control>z"])
            app.set_accels_for_action("win.redo", ["<Control>y", "<Control><Shift>z"])
            app.set_accels_for_action("win.print", ["<Control>p"])
            app.set_accels_for_action("win.quick_guide", ["F1"])
            app.set_accels_for_action("win.rotate_page_cw", ["<Control><Shift>r"])
            app.set_accels_for_action("win.rotate_page_ccw", ["<Control><Shift>l"])

        action_rotate_cw = Gio.SimpleAction.new('rotate_page_cw', None)
        action_rotate_cw.connect('activate', lambda a, p: self.rotate_current_page(90))
        self.add_action(action_rotate_cw)

        action_rotate_ccw = Gio.SimpleAction.new('rotate_page_ccw', None)
        action_rotate_ccw.connect('activate', lambda a, p: self.rotate_current_page(-90))
        self.add_action(action_rotate_ccw)

    def _update_ui_state(self):
        """Update UI state."""
        placement=getattr(self,'_certificate_placement',None)
        if placement and (placement.source is not self.doc or self.view_mode):placement.cancel(False)
        if hasattr(self,'image_tools'):self.image_tools.sync()
        has_doc = self.doc is not None
        if hasattr(self,'document_tools'):
            context=getattr(self.document_tools,'_bubble_context',None)
            if context and context!=(self.doc,self.current_page_index,self.document_tools.editable()):
                self.document_tools.close_note_bubble()
        page_count = pdf_handler.get_page_count(self.doc) if self.doc else 0
        has_pages = page_count > 0
        can_go_prev = has_pages and self.current_page_index > 0
        can_go_next = has_pages and self.current_page_index < page_count - 1

        self.save_button.set_sensitive(has_doc and self.document_modified)
        if self.lookup_action("save"):
            self.lookup_action("save").set_enabled(has_doc and self.document_modified)
        self.lookup_action("save_as").set_enabled(has_doc)
        can_copy = getattr(self._active_session, 'can_copy', True)
        can_print = getattr(self._active_session, 'can_print', True)
        self.lookup_action("search").set_enabled(has_doc and can_copy)
        self.lookup_action("save_page_pdf").set_enabled(has_pages and can_copy)
        can_edit = getattr(self._active_session, 'can_edit', True)
        in_edit = not self.view_mode and can_edit
        for action_name in ('document_properties',):
            self.lookup_action(action_name).set_enabled(has_pages and in_edit)
        self.lookup_action('bookmarks').set_enabled(has_pages)
        for action_name in ('export_range', 'export_page_png', 'export_page_svg',
                            'extract_images', 'extract_tables'):
            self.lookup_action(action_name).set_enabled(has_pages and can_copy)
        for action_name in ("add_signature", "add_table", "paste_table", "redact", "review", "sticky_note", "decorate", "crop", "create_field", "flatten_forms"):
            self.lookup_action(action_name).set_enabled(has_pages and in_edit)
        self.lookup_action("sign_certificate").set_enabled(has_pages and in_edit and can_copy)
        self.lookup_action("comments").set_enabled(has_pages)
        self.lookup_action("stamp").set_enabled(has_pages and in_edit)
        self.lookup_action('form_fields').set_enabled(has_pages and in_edit)
        self.lookup_action('find_replace').set_enabled(has_pages and in_edit)
        self.lookup_action('organize_pages').set_enabled(has_pages)
        self.forms_tool_button.set_sensitive(has_pages and in_edit)
        self.document_tools_button.set_sensitive(has_pages and in_edit)
        self.signature_tool_button.set_sensitive(has_pages and in_edit)
        self.table_tool_button.set_sensitive(has_pages and in_edit)
        self.search_button.set_sensitive(has_doc and can_copy)
        for control in (self.search_button, self.save_button, self.mode_toggle_button,
                        self.toggle_sidebar_button, self.header_history):
            control.set_visible(has_doc)
        for act_name in ("export_as", "export_docx", "export_txt"):
            act = self.lookup_action(act_name)
            if act:
                act.set_enabled(has_doc and can_copy)
        self.lookup_action("print").set_enabled(has_doc and can_print)
        if hasattr(self, 'features'):
            self.features.update_state()
            for button in (self.pages_tool_button, self.protect_tool_button):
                button.set_sensitive(has_pages)
        self.prev_button.set_sensitive(can_go_prev)
        self.next_button.set_sensitive(can_go_next)

        self.sidebar_add_page_button.set_sensitive(in_edit and has_doc)
        self.sidebar_duplicate_page_button.set_sensitive(in_edit and has_pages)
        if hasattr(self, 'mode_toggle_button'):
            self.mode_toggle_button.set_sensitive(has_doc and can_edit)
            target_label = _("mode_edit") if self.view_mode else _("mode_view")
            if hasattr(self, 'mode_toggle_label') and self.mode_toggle_label:
                self.mode_toggle_label.set_text(target_label)
                if hasattr(self, 'mode_toggle_icon') and self.mode_toggle_icon:
                    if self.view_mode:
                        self.mode_toggle_icon.set_from_icon_name("editor-pen-symbolic")
                    else:
                        self.mode_toggle_icon.set_from_icon_name("view-reveal-symbolic")
                if self.view_mode:
                    self.mode_toggle_button.add_css_class("suggested-action")
                    self.mode_toggle_button.remove_css_class("flat")
                else:
                    self.mode_toggle_button.remove_css_class("suggested-action")
                    self.mode_toggle_button.add_css_class("flat")
            else:
                self.mode_toggle_button.set_label(target_label)

        sidebar_tools = [self.select_tool_button, self.add_text_tool_button,
                         self.add_image_tool_button, self.drag_tool_button,
                         self.add_ellipse_tool_button, self.add_rectangle_tool_button,
                         self.pen_tool_button,
                         getattr(self, 'checkmark_tool_button', None),
                         getattr(self, 'cross_tool_button', None)]
        sidebar_tools.append(getattr(self, 'shapes_tool_button', None))
        for btn in sidebar_tools:
            if btn:
                btn.set_sensitive(in_edit and has_doc)
        self.select_tool_button.set_sensitive(has_pages)
        self.drag_tool_button.set_sensitive(has_pages)

        self._sync_tool_buttons_active()
        if hasattr(self, 'shapes_tool_button'):
            shape_icons = {'add_rectangle': 'editor-rectangle-symbolic',
                           'add_ellipse': 'editor-ellipse-symbolic',
                           'add_checkmark': 'editor-check-symbolic', 'add_cross': 'editor-cross-symbolic'}
            if self.tool_mode in shape_icons or self.tool_mode in ('add_shape', 'add_line'):
                self.shapes_tool_button.add_css_class('active')
            else:
                self.shapes_tool_button.remove_css_class('active')
        self._update_window_title()
            
        if hasattr(self, 'add_page_button'):
            self.add_page_button.set_sensitive(in_edit and has_doc)
        if hasattr(self, 'duplicate_page_button'):
            self.duplicate_page_button.set_sensitive(in_edit and has_pages)

        if hasattr(self, 'rotate_page_cw_button'):
            self.rotate_page_cw_button.set_sensitive(in_edit and has_pages)
        if hasattr(self, 'rotate_page_ccw_button'):
            self.rotate_page_ccw_button.set_sensitive(in_edit and has_pages)
        if self.lookup_action("rotate_page_cw"):
            self.lookup_action("rotate_page_cw").set_enabled(in_edit and has_pages)
        if self.lookup_action("rotate_page_ccw"):
            self.lookup_action("rotate_page_ccw").set_enabled(in_edit and has_pages)

        if hasattr(self, 'delete_page_button'):
            self.delete_page_button.set_sensitive(in_edit and has_doc and page_count > 1)

        if hasattr(self, 'symbols_button'):
            self.symbols_button.set_sensitive(in_edit and has_doc)

        shape_selected = self.selected_shape is not None
        stroke_selected = getattr(self, 'selected_stroke', None) is not None
        text_selected = self.selected_text is not None
        shape_controls_active = in_edit and (shape_selected or self.tool_mode in ("add_ellipse", "add_rectangle", "add_checkmark", "add_cross", "add_shape"))
        stroke_controls_active = in_edit and (stroke_selected or self.tool_mode in ("pen", "highlighter", "add_line"))
        view_text_selected = self.view_mode and (getattr(self, 'view_sel_rect', None) is not None or getattr(self, 'selected_word', None) is not None)
        format_enabled_base = in_edit and ((text_selected or self.tool_mode == "add_text") and
                               self.selected_image is None and not shape_selected and not stroke_selected)

        if hasattr(self, 'toolbar_row2'):
            self.toolbar_row2.set_visible(in_edit and has_doc)

        if hasattr(self, 'text_format_box'):
            self.text_format_box.set_visible(in_edit and (self.tool_mode == "add_text" or text_selected) and
                                             not shape_controls_active and not stroke_controls_active and self.selected_image is None)
            self.text_format_sep.set_visible(False)

        self.font_combo.set_sensitive(format_enabled_base and not self.font_scan_in_progress)
        self.font_size_spin.set_sensitive(format_enabled_base)
        self.color_button.set_sensitive(format_enabled_base)
        if self.bold_button: self.bold_button.set_sensitive(format_enabled_base)
        if self.italic_button: self.italic_button.set_sensitive(format_enabled_base)
        if hasattr(self, 'underline_button') and self.underline_button:
            self.underline_button.set_sensitive(format_enabled_base)
        if hasattr(self, 'strikethrough_button') and self.strikethrough_button:
            self.strikethrough_button.set_sensitive(format_enabled_base)
        for btn in [getattr(self, 'align_left_button', None), getattr(self, 'align_center_button', None),
                    getattr(self, 'align_right_button', None), getattr(self, 'align_justify_button', None)]:
            if btn: btn.set_sensitive(format_enabled_base)

        if hasattr(self, 'shape_toolbar_box'):
            self.shape_toolbar_box.set_visible(shape_controls_active)
            self.shape_toolbar_sep.set_visible(False)

        self.shape_fill_button.set_sensitive(shape_controls_active)
        self.shape_stroke_button.set_sensitive(shape_controls_active)
        self.shape_stroke_width_spin.set_sensitive(shape_controls_active)
        self.shape_transparent_toggle.set_sensitive(shape_controls_active)

        if hasattr(self, 'stroke_toolbar_box'):
            self.stroke_toolbar_box.set_visible(stroke_controls_active)
            self.stroke_toolbar_sep.set_visible(False)

        if hasattr(self, 'stroke_color_button'):
            self.stroke_color_button.set_sensitive(stroke_controls_active)
        if hasattr(self, 'stroke_width_spin'):
            self.stroke_width_spin.set_sensitive(stroke_controls_active)

        selected_obj = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
        has_selected_obj = in_edit and (selected_obj is not None)

        if hasattr(self, 'rotation_toolbar_box'):
            self.rotation_toolbar_box.set_visible(has_selected_obj)
            self.rotation_toolbar_sep.set_visible(has_selected_obj)
            self.rotation_toolbar_box.set_sensitive(has_selected_obj)
            self._update_rotation_controls(selected_obj)

        if hasattr(self, 'tool_context_label'):
            tool_labels = {
                'select': 'tool_select', 'drag': 'tool_drag',
                'add_text': 'tool_add_text', 'add_image': 'tool_add_image',
                'pen': 'tool_pen', 'highlighter': 'tool_highlighter',
                'erase_highlight': 'tool_erase_highlight',
                'signature': 'signature_add',
                'form_create': 'tool_create_field', 'form_reposition': 'form_redraw_bounds',
                'add_rectangle': 'tool_rectangle', 'add_ellipse': 'tool_ellipse',
                'add_checkmark': 'tool_checkmark', 'add_cross': 'tool_cross',
                'add_shape': 'tool_shape', 'add_line': 'tool_line_label',
            }
            self.tool_context_label.set_text(_(tool_labels.get(self.tool_mode, 'tool_select')))
            active_controls = (self.text_format_box.get_visible() or
                               self.shape_toolbar_box.get_visible() or
                               self.stroke_toolbar_box.get_visible() or
                               self.rotation_toolbar_box.get_visible())
            self.toolbar_hint.set_text(_("toolbar_select_hint") if self.tool_mode == 'select' else
                                       _("toolbar_drag_hint") if self.tool_mode == 'drag' else
                                       _("toolbar_image_hint") if self.tool_mode == 'add_image' else
                               _("toolbar_erase_highlight_hint") if self.tool_mode == 'erase_highlight' else
                                       _("signature_click_hint") if self.tool_mode == "signature" else "")
            self.toolbar_hint.set_visible(not active_controls and bool(self.toolbar_hint.get_text()))

        if hasattr(self, 'highlight_button'):
            self.highlight_button.set_sensitive(can_edit and has_doc)
        if hasattr(self, 'remove_highlight_button'):
            self.remove_highlight_button.set_sensitive(can_edit and has_doc)
        if hasattr(self, 'highlight_color_button'):
            self.highlight_color_button.set_sensitive(can_edit and has_doc)

        if hasattr(self, 'page_slider'):
            self.page_slider.set_sensitive(has_pages and page_count > 1)
            self.page_slider.set_visible(has_pages and page_count > 1)

        if hasattr(self, 'main_toolbar'):
            self.main_toolbar.set_visible(in_edit and has_doc)

        if shape_selected:
            self.shape_transparent_toggle.handler_block_by_func(self.on_shape_format_changed)
            self.shape_transparent_toggle.set_active(self.selected_shape.is_transparent)
            self.shape_transparent_toggle.handler_unblock_by_func(self.on_shape_format_changed)

        if text_selected:
            self._update_text_format_controls(self.selected_text)
        elif not self.inline_editor_widget:
            self._update_text_format_controls(None)

        if shape_selected:
            self._update_shape_format_controls(self.selected_shape)
        else:
            self._update_shape_format_controls(None)

        if stroke_selected:
            self._update_stroke_format_controls(self.selected_stroke)
        elif stroke_controls_active:
            self._update_stroke_format_controls(None)

        self.select_tool_button.get_style_context().remove_class('active')
        self.add_text_tool_button.get_style_context().remove_class('active')
        self.add_image_tool_button.get_style_context().remove_class('active')
        self.drag_tool_button.get_style_context().remove_class('active')
        self.add_ellipse_tool_button.get_style_context().remove_class('active')
        self.add_rectangle_tool_button.get_style_context().remove_class('active')
        if hasattr(self, 'pen_tool_button'):
            self.pen_tool_button.get_style_context().remove_class('active')
        if hasattr(self, 'highlight_button'):
            self.highlight_button.get_style_context().remove_class('active')
        if hasattr(self, 'checkmark_tool_button'):
            self.checkmark_tool_button.get_style_context().remove_class('active')
        if hasattr(self, 'cross_tool_button'):
            self.cross_tool_button.get_style_context().remove_class('active')

        if self.tool_mode == 'drag' and has_pages:
            self.drag_tool_button.add_css_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name('grab'))
        elif self.view_mode:
            if self.tool_mode == 'select':
                self.select_tool_button.add_css_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("text"))
        elif self.tool_mode == "select":
            self.select_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(None)
        elif self.tool_mode == "add_text":
            self.add_text_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == "add_image":
            self.add_image_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("cell"))
        elif self.tool_mode == "drag":
            self.drag_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("move"))
        elif self.tool_mode == "add_ellipse":
            self.add_ellipse_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == "add_rectangle":
            self.add_rectangle_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == "add_checkmark":
            if hasattr(self, 'checkmark_tool_button'):
                self.checkmark_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == "add_cross":
            if hasattr(self, 'cross_tool_button'):
                self.cross_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == "pen":
            if hasattr(self, 'pen_tool_button'):
                self.pen_tool_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == "highlighter":
            if hasattr(self, 'highlight_button'):
                self.highlight_button.get_style_context().add_class('active')
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode in ('form_create','form_reposition','stamp','sticky_note','certificate_signature'):
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name('crosshair'))

        if has_doc:
            self.stack.set_visible_child_name("editor")
        else:
            self.stack.set_visible_child_name("welcome")

        if has_doc:
            self.update_page_label()
            if self.document_modified and not self.get_title().endswith("*"):
                self.set_title(self.get_title() + "*")
            elif not self.document_modified and self.get_title().endswith("*"):
                self.set_title(self.get_title()[:-1])
        else:
            self.page_label.set_text(_("page_info_count").format(0, 0))
            self.zoom_label.set_text("100%")
            self.status_label.set_text(_("status_open_or_drop"))
            self.set_title(constants.APP_NAME)
            self.document_modified = False

        self._update_undo_redo_buttons()
        if hasattr(self, "document_tools"):
            self.document_tools.refresh_comments()
        if hasattr(self,"form_tools"):
            self.form_tools.refresh()
        if hasattr(self,'scroll_mode_button'):
            self._sync_scroll_mode=True
            continuous=self._active_session.scroll_mode!='page'
            self.scroll_mode_button.set_active(continuous)
            self._update_scroll_mode_icon(continuous)
            self.scroll_mode_button.set_visible(has_doc)
            self._sync_scroll_mode=False
            self.continuous_view.sync()

    def _update_scroll_mode_icon(self,continuous):
        self.scroll_mode_button.set_icon_name('editor-continuous-scroll-symbolic' if continuous else 'editor-page-scroll-symbolic')
        self.scroll_mode_button.get_child().set_pixel_size(20)
        current=_("scroll_continuous") if continuous else _("scroll_page")
        next_mode=_("scroll_page") if continuous else _("scroll_continuous")
        self.scroll_mode_button.set_tooltip_text(_("scroll_switch_tip").format(current,next_mode))

    def _on_scroll_mode_changed(self,button,param=None):
        if getattr(self,'_sync_scroll_mode',False) or not self.doc:
            return
        if self.inline_editor_widget is not None:
            self._apply_and_hide_editor(force_apply=True)
        self._active_session.scroll_mode='continuous' if button.get_active() else 'page'
        self._update_scroll_mode_icon(button.get_active())
        self._page_scroll_delta=0
        self.continuous_view.sync()

    def on_check_updates(self, action=None, param=None):
        self.on_about_activated(None, None)
        self._about_window.check_updates()

    def on_about_activated(self, action, param):
        """Show pdfLX product information."""
        existing = getattr(self, '_about_window', None)
        if existing is None or existing._closed:
            self._about_window = AboutWindow(self)
        self._about_window.present()

    def _record_recent_file(self, filepath):
        """Prepend filepath to recent_opened_files setting, deduplicate, and limit to 15."""
        if not filepath:
            return
        try:
            filepath_str = str(filepath)
            norm_path = os.path.abspath(os.path.normpath(filepath_str))
            recents = get_setting("recent_opened_files", [])
            if not isinstance(recents, list):
                recents = []
            updated = [
                p for p in recents
                if isinstance(p, str) and os.path.abspath(os.path.normpath(p)) != norm_path
            ]
            updated.insert(0, norm_path)
            updated = updated[:15]
            set_setting("recent_opened_files", updated)
        except Exception as e:
            print(f"Warning: Failed to record recent file {filepath}: {e}")

    def load_document(self, filepath, target_page=0, in_new_tab=None):
        """Load document into active session or a new tab."""
        existing_session = self.get_session_by_path(filepath)
        if existing_session:
            self.set_active_session(existing_session)
            if target_page != 0:
                self._load_page(target_page)
            return

        if in_new_tab is None:
            in_new_tab = (self._active_session is not None and self._active_session.doc is not None)

        if in_new_tab:
            new_session = self.create_session(filepath=filepath)
            self.add_session(new_session, switch_to=True)
            target_sess = new_session
        else:
            if self.check_unsaved_changes():
                return
            self.close_document()
            if self._active_session is None:
                self._active_session = self.create_session(filepath=filepath)
                self.sessions = [self._active_session]
            target_sess = self._active_session
            target_sess.pdf_path = filepath
            target_sess.original_file_path = filepath
            if hasattr(self, 'tab_view') and self.tab_view:
                if getattr(target_sess, 'tab_page', None) is None:
                    self._create_tab_for_session(target_sess)
            self._update_tab_title(target_sess)

        self._record_recent_file(filepath)
        self.status_label.set_text(_("loading").format(os.path.basename(filepath)))
        GLib.idle_add(self._show_loading_state)

        def _load_async():
            doc, error_msg = pdf_handler.load_pdf_document(filepath)
            GLib.idle_add(self._finish_loading, doc, error_msg, filepath, target_page, target_sess)

        thread = threading.Thread(target=_load_async)
        thread.daemon = True
        thread.start()

    def _show_loading_state(self):
        """Show loading state."""
        self.open_button.set_sensitive(False)
        self.save_button.set_sensitive(False)
        self.lookup_action("save_as").set_enabled(False)
        for act_name in ("export_as", "export_docx", "export_txt"):
            act = self.lookup_action(act_name)
            if act:
                act.set_enabled(False)
        self.prev_button.set_sensitive(False)
        self.next_button.set_sensitive(False)
        self.font_combo.set_sensitive(False)
        self.font_size_spin.set_sensitive(False)
        self.color_button.set_sensitive(False)
        self.select_tool_button.set_sensitive(False)
        self.add_text_tool_button.set_sensitive(False)
        if not any(s.doc is not None for s in self.sessions):
            if hasattr(self, 'stack') and self.stack:
                self.stack.set_visible_child_name("welcome")


    def _finish_loading(self, doc, error_msg, filepath, target_page=0, target_session=None):
        """Finalize document loading, set active session state, and start thumbnail generation."""
        sess = target_session or getattr(self, '_active_session', None)
        if error_msg in ("password_required", "incorrect_password"):
            self._prompt_pdf_password(filepath, target_page, sess, error_msg == "incorrect_password")
            return
        if error_msg:
            show_error_dialog(self, error_msg)
            self.status_label.set_text(_("doc_load_failed"))
            if len(self.sessions) > 1 and sess:
                self.remove_session(sess)
            else:
                self.close_document()
            self.open_button.set_sensitive(True)
            self.select_tool_button.set_sensitive(True)
            self.add_text_tool_button.set_sensitive(True)
            self._update_ui_state()
            return
        elif doc and sess:
            if target_page == 0:
                remembered = session_memory.recall_position(filepath)
                if remembered and 0 < remembered.get('page', 0) < doc.page_count:
                    target_page = remembered['page']
            session_memory.store_thumbnail(doc, filepath)
            sess.doc = doc
            sess.can_edit = getattr(doc, 'editor_can_edit', True)
            sess.can_copy = getattr(doc, 'editor_can_copy', True)
            sess.can_print = getattr(doc, 'editor_can_print', True)
            if not sess.can_edit:
                sess.view_mode = True
            sess.is_repaired_file = getattr(doc, 'is_repaired', False)
            sess.pdf_path = filepath
            sess.original_file_path = filepath
            sess.allow_incremental_save = True
            sess.is_modified = False
            sess.current_page_index = target_page
            sess.fit_on_load = True
            sess.fit_to_view = True
            self._update_tab_title(sess)

            if getattr(sess, 'is_repaired_file', False):
                logger.debug(_("dbg_repaired_while_opening"))
                from .dialogs import toast
                toast(self, _("repaired_notice"), timeout=0, button_label=_("repaired_save_copy"),
                      callback=lambda: self.on_save_as(None, None))
            self._record_recent_file(filepath)

            self.set_active_session(sess)

            self.target_page_after_load = target_page
            self._load_page(target_page)
            self._schedule_fit_document(sess)
            GLib.idle_add(self._load_thumbnails, sess)

        self.open_button.set_sensitive(True)
        self.select_tool_button.set_sensitive(True)
        self.add_text_tool_button.set_sensitive(True)
        self._update_ui_state()

    def _prompt_pdf_password(self, filepath, target_page, session, incorrect=False):
        from .window_dialogs import _prompt_pdf_password as dialog
        return dialog(self, filepath, target_page, session, incorrect)

    def _load_thumbnails(self, session=None):
        """Asynchronously generate and populate sidebar thumbnails for each document page."""
        sess = session or self._active_session
        if not sess or not sess.doc:
            return

        target_doc = sess.doc
        target_model = sess.pages_model
        if target_model is not None:
            target_model.remove_all()
        page_count = pdf_handler.get_page_count(target_doc)
        if self._active_session == sess and hasattr(self, 'page_count_badge') and self.page_count_badge:
            self.page_count_badge.set_text(str(page_count))

        thumb_iter = [0]
        def _load_next_thumb():
            if sess.doc != target_doc:
                return GLib.SOURCE_REMOVE
            if thumb_iter[0] < page_count:
                index = thumb_iter[0]
                thumb = pdf_handler.generate_thumbnail(target_doc, index, target_width=240)

                if thumb and target_model is not None:
                    pdf_page_obj = PdfPage(index=index, thumbnail=thumb)
                    target_model.append(pdf_page_obj)
                thumb_iter[0] += 1
                if self._active_session == sess:
                    if index % 5 == 0 or index == page_count - 1:
                        self.status_label.set_text(_("thumbnail_loaded").format(index + 1, page_count))
                return GLib.SOURCE_CONTINUE 
            else:
                if self._active_session == sess:
                    if sess.pdf_path:
                        self.status_label.set_text(_("loaded").format(os.path.basename(sess.pdf_path)))
                    else:
                        self.status_label.set_text(_("new_doc_loaded"))                
                    if page_count > 0:
                        target = getattr(self, 'target_page_after_load', 0)
                        if target >= page_count: target = 0
                        self._load_page(target)
                    else:
                        self._update_ui_state()
                return GLib.SOURCE_REMOVE

        GLib.idle_add(_load_next_thumb)


    def _load_page(self, page_index, preserve_scroll=False, reload_objects=True):
        """Extract editable objects, dimensions, and render page at given index."""
        forms=getattr(self,'form_tools',None)
        finish=getattr(forms,'finish_inline',None)
        if finish and not finish():return
        current_v_scroll = 0
        current_h_scroll = 0
        if preserve_scroll:
            v_adj = self.pdf_scroll.get_vadjustment()
            h_adj = self.pdf_scroll.get_hadjustment()
            if v_adj:
                current_v_scroll = v_adj.get_value()
            if h_adj:
                current_h_scroll = h_adj.get_value()

        if not self.doc or not (0 <= page_index < pdf_handler.get_page_count(self.doc)):
            print(f"Warning: Invalid attempt to load page {page_index}.")
            return

        self.commit_pending_format_change()
        if hasattr(self,"document_tools"):
            self.document_tools.close_note_bubble()
        self._cancel_table_drag()
        if hasattr(self,"stamp_interaction"):
            self.stamp_interaction.cancel()
        if hasattr(self,"form_tools"):
            self.form_tools.cancel_drag()

        cache = self._active_session.page_objects
        old_page_idx = self.current_page_index
        if old_page_idx in cache:
            cache[old_page_idx] = (self.editable_texts, self.editable_shapes,
                                   self.editable_images, self.editable_strokes)
        if page_index not in cache:
            from .page_state import load as load_saved_state
            saved = load_saved_state(self.doc, page_index)
            if saved is not None:
                cache[page_index] = saved
        extract_objects = page_index not in cache
        if not extract_objects:
            (self.editable_texts, self.editable_shapes,
             self.editable_images, self.editable_strokes) = cache[page_index]

        self.current_page_index = page_index
        self.view_sel_start=None
        self.view_sel_rect=None
        self.view_selected_text=''
        self.view_drag_active=False
        self._active_session.view_selection_quads=[]
        self.selected_text = None
        self.selected_image = None
        self.selected_shape = None
        self.selected_stroke = None
        self.selected_table = None
        self.table_drag_state = None
        self.table_drag_table = None
        self.hide_text_editor()

        if extract_objects:
            texts, error = pdf_handler.extract_editable_text(self.doc, page_index)
            if error:
                show_error_dialog(self, _("text_extract_error", page_index + 1, error))
                self.editable_texts = []
            else:
                self.editable_texts = texts
                
            images, error = pdf_handler.extract_editable_images(self.doc, page_index)
            if error:
                show_error_dialog(self, _("image_extract_error").format(page_index + 1, error))
                self.editable_images = []
            else:
                self.editable_images = images

            shapes, shapes_error = pdf_handler.extract_editable_shapes(self.doc, page_index)
            if shapes_error:
                print(f"Warning: Could not extract shapes from page {page_index + 1}: {shapes_error}")
                self.editable_shapes = []
            else:
                self.editable_shapes = shapes

            strokes, strokes_error = pdf_handler.extract_editable_strokes(self.doc, page_index)
            if strokes_error:
                print(f"Warning: Could not extract strokes from page {page_index + 1}: {strokes_error}")
                self.editable_strokes = []
            else:
                self.editable_strokes = strokes

        cache[page_index] = (self.editable_texts, self.editable_shapes,
                             self.editable_images, self.editable_strokes)
        self.doc.editor_page_models = cache

        page = self.doc.load_page(page_index)
        self.current_pdf_page_width = int(page.rect.width * self.zoom_level)
        self.current_pdf_page_height = int(page.rect.height * self.zoom_level)

        logger.debug(f"Setting pdf_view content size: {self.current_pdf_page_width} x {self.current_pdf_page_height}")
        self.pdf_view.set_content_width(self.current_pdf_page_width)
        self.pdf_view.set_content_height(self.current_pdf_page_height)

        self.pdf_view.queue_draw()
        
        if preserve_scroll:
            GLib.idle_add(self.pdf_scroll.get_vadjustment().set_value, current_v_scroll)
            GLib.idle_add(self.pdf_scroll.get_hadjustment().set_value, current_h_scroll)

        self._sync_thumbnail_selection()
        self._update_ui_state()
        
        fallback_font = None
        for text_obj in self.editable_texts:
            if getattr(text_obj, 'font_fallback_used', False):
                fallback_font = text_obj.font_fallback_used
                break
        if fallback_font:
            self.status_label.set_text(f"font cannot be determinated, using {fallback_font}")
        
        if self.doc and extract_objects:
            pdf_handler.save_page_snapshot(self.doc, page_index)

    def close_document(self):
        """Close document."""
        if getattr(self,'_document_only_view',False):
            self._exit_document_view()
        self._cancel_table_drag()
        if hasattr(self,"stamp_interaction"):
            self.stamp_interaction.cancel()
        if hasattr(self,"form_tools"):
            self.form_tools.cancel_drag()
        self._clear_search()
        self._pending_signature = None
        if hasattr(self, '_active_session') and self._active_session:
            if self._active_session.undo_manager:
                self._active_session.undo_manager.clear()
            self._active_session.is_repaired_file = False
            if self._active_session.doc:
                pdf_handler.release_page_snapshots(self._active_session.doc)
                pdf_handler.close_pdf_document(self._active_session.doc)
            self._active_session.close()
            self._active_session.pdf_path = None
            self._active_session.original_file_path = None
            self._active_session.current_page_index = 0
            self._active_session.is_modified = False
            if self._active_session.pages_model:
                self._active_session.pages_model.remove_all()
        self.temp_stroke = None
        self.hide_text_editor()
        self.pdf_view.set_content_width(1)
        self.pdf_view.set_content_height(1)
        self.pdf_view.queue_draw()
        self._update_ui_state()

    def go_to_welcome(self):
        """Navigate to welcome hub view."""
        if hasattr(self, 'stack') and self.stack:
            if self.stack.get_visible_child_name() == "welcome":
                # Toggle back to editor if any document is open
                if any(s.doc is not None for s in self.sessions):
                    self.stack.set_visible_child_name("editor")
                    if hasattr(self, 'tab_bar') and self.tab_bar:
                        self.tab_bar.set_visible(True)
                    if self._active_session:
                        self.set_title(f"{constants.APP_NAME} - {self._active_session.display_title}")
                    return

        if self.check_unsaved_changes():
            return

        old_welcome = self.stack.get_child_by_name("welcome")
        if old_welcome:
            self.stack.remove(old_welcome)
        new_welcome = WelcomeView(parent_window=self)
        self.stack.add_named(new_welcome, "welcome")

        self.stack.set_visible_child_name("welcome")
        if hasattr(self, 'tab_bar') and self.tab_bar:
            self.tab_bar.set_visible(False)
        self.set_title(constants.APP_NAME)

    def on_close_tab(self, action=None, param=None):
        """Close the currently active tab or document."""
        if hasattr(self, 'tab_view') and self.tab_view:
            selected_page = self.tab_view.get_selected_page()
            if selected_page:
                self.tab_view.close_page(selected_page)
                return
        if self.check_unsaved_changes():
            return
        self.close_document()

    def on_next_tab(self, action=None, param=None):
        """Switch to next document tab."""
        if hasattr(self, 'tab_view') and self.tab_view and self.tab_view.get_n_pages() > 1:
            self.tab_view.select_next_page()

    def on_prev_tab(self, action=None, param=None):
        """Switch to previous document tab."""
        if hasattr(self, 'tab_view') and self.tab_view and self.tab_view.get_n_pages() > 1:
            self.tab_view.select_previous_page()

    def save_document(self, save_path, incremental=False):
        """Save document."""
        if not self.doc or self.is_saving:
            return
        if hasattr(self,'form_tools') and not self.form_tools.save_values():return
        if not incremental:
            from .features.protect_ui import confirm_signed_save
            choice = confirm_signed_save(self, save_path)
            if choice is None:
                return
            incremental = choice == 'incremental'
        page_to_restore = self.current_page_index
        self.is_saving = True
        self.status_label.set_text(_("saving").format(os.path.basename(save_path)))
        if self.inline_editor_widget is not None:
            self._apply_and_hide_editor(force_apply=True)

        success, error_msg = pdf_handler.save_document(self.doc, save_path, incremental=incremental)
        self.is_saving = False
        
        if success:
            logger.debug(_("dbg_save_success", save_path))
            if hasattr(self, 'recovery') and self._active_session:
                self.recovery.discard(self._active_session)
            session_memory.store_thumbnail(self.doc, save_path)
            self.document_modified = False 
            if self._active_session:
                self._active_session.pdf_path = save_path
                self._active_session.original_file_path = save_path
                self._update_tab_title(self._active_session)
                self.set_title(f"{constants.APP_NAME} - {self._active_session.display_title}")
            self.load_document(save_path, target_page=page_to_restore, in_new_tab=False)
            self.status_label.set_text(_("saved").format(os.path.basename(save_path)))
        else:
            show_error_dialog(self, _("err_pdf_save", error_msg))
            self.status_label.set_text(_("save_failed"))

        self._update_ui_state()

    def _on_canvas_scrolled(self, adjustment):
        # Region-rendered pages only hold the tiles around the old viewport.
        if self.doc and pdf_handler.page_is_large(self.doc, self.current_page_index, self.zoom_level):
            self.pdf_view.queue_draw()

    def _visible_page_pixels(self, page_offset_x, page_offset_y):
        """Visible part of the displayed page, in page device pixels, or None."""
        ok, bounds = self.pdf_view.compute_bounds(self.pdf_scroll)
        if not ok:
            return None
        left, top = -bounds.get_x() - page_offset_x, -bounds.get_y() - page_offset_y
        width, height = self.pdf_scroll.get_width(), self.pdf_scroll.get_height()
        if width <= 0 or height <= 0:
            return None
        return (max(0, left), max(0, top), max(0, left + width), max(0, top + height))

    def draw_pdf_page(self, area, cr, width, height):
        """Draw PDF page."""
        if not self.doc or self.current_pdf_page_width <= 0:
            cr.set_source_rgb(0.42, 0.42, 0.42)
            cr.paint()
            return

        page_w = self.current_pdf_page_width
        page_h = self.current_pdf_page_height
        page_offset_x = max(0, (width - page_w) / 2.0)
        page_offset_y = max(0, (height - page_h) / 2.0)

        cr.set_source_rgb(0.42, 0.42, 0.42)
        cr.paint()

        cr.save()
        cr.set_source_rgba(0, 0, 0, 0.15)
        cr.rectangle(page_offset_x + 4.0, page_offset_y + 4.0, page_w, page_h)
        cr.fill()
        cr.restore()

        cr.save()
        cr.translate(page_offset_x, page_offset_y)
        preview_doc = getattr(self, '_inline_editor_preview_doc', None)
        stamp_preview = self.stamp_interaction.preview_doc
        render_doc = (stamp_preview or getattr(self, '_table_preview_doc', None) or
                      (preview_doc if preview_doc and self.inline_editor_widget else self.doc))
        render_page = 0 if render_doc is preview_doc else self.current_page_index
        region = None
        if pdf_handler.page_is_large(render_doc, render_page, self.zoom_level):
            # Very large zoomed pages: rasterize only the tiles around the viewport.
            visible = self._visible_page_pixels(page_offset_x, page_offset_y)
            if visible:
                region = pdf_handler.get_page_region_surface(render_doc, render_page, self.zoom_level, visible)
        if region:
            surface, region_x, region_y = region
            cr.set_source_surface(surface, region_x, region_y)
            cr.paint()
        else:
            cached_surf = pdf_handler.get_page_cairo_surface(render_doc, render_page, self.zoom_level)
            if cached_surf:
                cr.set_source_surface(cached_surf, 0, 0)
                cr.paint()
            else:
                pdf_handler.draw_page_to_cairo(cr, render_doc, render_page, self.zoom_level)
        cr.restore()

        page = self.doc[self.current_page_index]
        page_rot = page.rotation % 360
        cr.save()
        cr.translate(page_offset_x, page_offset_y)
        cr.scale(self.zoom_level, self.zoom_level)
        if page_rot != 0:
            mat = page.rotation_matrix
            cr.transform(cairo.Matrix(mat.a, mat.b, mat.c, mat.d, mat.e, mat.f))

        if self.dragged_object:
            if self.dragged_object.original_bbox:
                orig_x1, orig_y1, orig_x2, orig_y2 = self.dragged_object.original_bbox
                cr.save()
                cr.set_source_rgba(0.85, 0.85, 0.85, 0.55)
                cr.rectangle(orig_x1, orig_y1, orig_x2 - orig_x1, orig_y2 - orig_y1)
                cr.fill()
                cr.set_source_rgba(0.5, 0.5, 0.5, 0.7)
                cr.set_line_width(1.5 / self.zoom_level)
                cr.set_dash([4.0 / self.zoom_level, 3.0 / self.zoom_level])
                cr.rectangle(orig_x1, orig_y1, orig_x2 - orig_x1, orig_y2 - orig_y1)
                cr.stroke()
                cr.set_dash([])
                cr.restore()

            x1, y1, x2, y2 = self.dragged_object.bbox
            ghost_x = x1
            ghost_y = y1
            ghost_w = x2 - x1
            ghost_h = y2 - y1

            cr.save()
            rot = getattr(self.dragged_object, 'rotation', 0.0) % 360.0
            if rot != 0.0:
                cx = ghost_x + ghost_w / 2.0
                cy = ghost_y + ghost_h / 2.0
                cr.translate(cx, cy)
                cr.rotate(math.radians(rot))
                cr.translate(-cx, -cy)
            if isinstance(self.dragged_object, EditableImage) and self.dragged_object.image_bytes:
                try:
                    from .image_editing import drag_preview
                    png, padding = drag_preview(self.dragged_object)
                    loader = GdkPixbuf.PixbufLoader.new()
                    loader.write(png)
                    loader.close()
                    pixbuf = loader.get_pixbuf()
                    if pixbuf and ghost_w > 0 and ghost_h > 0:
                        cr.save()
                        cr.translate(ghost_x-padding, ghost_y-padding)
                        cr.scale((ghost_w+2*padding) / pixbuf.get_width(),
                                 (ghost_h+2*padding) / pixbuf.get_height())
                        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
                        cr.paint_with_alpha(0.6)
                        cr.restore()
                except Exception as e:
                    cr.set_source_rgba(0.2, 0.5, 0.8, 0.5)
                    cr.rectangle(ghost_x, ghost_y, ghost_w, ghost_h)
                    cr.fill()
            elif isinstance(self.dragged_object, EditableText):
                layout = PangoCairo.create_layout(cr)
                font_desc_str = f"{self.dragged_object.font_family_base} {self.dragged_object.font_size}"
                if self.dragged_object.is_bold: font_desc_str += " Bold"
                if self.dragged_object.is_italic: font_desc_str += " Italic"

                font_desc = Pango.FontDescription(font_desc_str)
                font_desc.set_absolute_size(int(self.dragged_object.font_size * Pango.SCALE))
                layout.set_font_description(font_desc)
                layout.set_text(self.dragged_object.text, -1)
                align = getattr(self.dragged_object, 'alignment', 'left')
                if ghost_w > 0:
                    nat_pw, _ = layout.get_size()
                    w_to_set = max(ghost_w, nat_pw / Pango.SCALE)
                    layout.set_width(int(w_to_set * Pango.SCALE))
                    if align == 'center':
                        layout.set_alignment(Pango.Alignment.CENTER)
                    elif align == 'right':
                        layout.set_alignment(Pango.Alignment.RIGHT)
                    elif align == 'justify':
                        layout.set_justify(True)

                r, g, b = self.dragged_object.color
                cr.set_source_rgba(r, g, b, 0.6)
                
                attr_list = Pango.AttrList()
                
                for match in re.finditer(r'(https?://[^\s]+|www\.[^\s]+)', self.dragged_object.text):
                    start_byte = len(self.dragged_object.text[:match.start()].encode('utf-8'))
                    end_byte = len(self.dragged_object.text[:match.end()].encode('utf-8'))
                    
                    color_attr = Pango.attr_foreground_new(0, int(0.33*65535), int(0.8*65535))
                    color_attr.start_index = start_byte
                    color_attr.end_index = end_byte
                    attr_list.insert(color_attr)
                    
                    underline_attr = Pango.attr_underline_new(Pango.Underline.SINGLE)
                    underline_attr.start_index = start_byte
                    underline_attr.end_index = end_byte
                    attr_list.insert(underline_attr)
                
                if getattr(self.dragged_object, 'is_underline', False):
                    attr_list.insert(Pango.attr_underline_new(Pango.Underline.SINGLE))
                if getattr(self.dragged_object, 'is_strikethrough', False):
                    attr_list.insert(Pango.attr_strikethrough_new(True))
                
                layout.set_attributes(attr_list)
                
                cr.move_to(ghost_x, ghost_y)
                PangoCairo.show_layout(cr, layout)
            elif isinstance(self.dragged_object, EditableShape):
                from .shape_geometry import draw_cairo
                draw_cairo(cr, self.dragged_object, (ghost_x, ghost_y, ghost_x + ghost_w, ghost_y + ghost_h),
                           alpha=0.6, rotate=False)
            elif isinstance(self.dragged_object, EditableStroke):
                r, g, b = self.dragged_object.stroke_color
                cr.set_source_rgba(r, g, b, 0.6)
                cr.set_line_width(self.dragged_object.stroke_width)
                is_hl = getattr(self.dragged_object, 'tool_type', None) in ("highlighter", EditableStroke.TOOL_HIGHLIGHTER) or self.dragged_object.stroke_width >= 8.0
                if is_hl:
                    cr.set_line_cap(cairo.LINE_CAP_SQUARE)
                    cr.set_line_join(cairo.LINE_JOIN_BEVEL)
                else:
                    cr.set_line_cap(cairo.LINE_CAP_ROUND)
                    cr.set_line_join(cairo.LINE_JOIN_ROUND)
                if len(self.dragged_object.points) == 1:
                    px, py = self.dragged_object.points[0]
                    rad = max(self.dragged_object.stroke_width / 2.0, 1.0 / self.zoom_level)
                    if is_hl:
                        cr.rectangle(px - rad, py - rad, rad * 2.0, rad * 2.0)
                    else:
                        cr.arc(px, py, rad, 0, 2 * math.pi)
                    cr.fill()
                elif self.dragged_object.points:
                    p0 = self.dragged_object.points[0]
                    cr.move_to(p0[0], p0[1])
                    for pt in self.dragged_object.points[1:]:
                        cr.line_to(pt[0], pt[1])
                    cr.stroke()
            cr.restore()
            
        for text_obj in self.editable_texts:
            if text_obj.page_number != self.current_page_index:
                continue
            if text_obj is self.inline_editor_text_obj:
                continue
            if not text_obj.is_new:
                continue 
            if getattr(text_obj, 'is_baked', False) and not self._is_table_preview_member(text_obj):
                continue
            if text_obj is self.dragged_object:
                continue
            if not text_obj.bbox or not text_obj.text:
                continue
            x1, y1, x2, y2 = text_obj.bbox
            draw_x = x1
            draw_y = y1
            cr.save()
            rot = getattr(text_obj, 'rotation', 0.0) % 360.0
            if rot != 0.0:
                cx = draw_x + (x2 - x1) / 2.0
                cy = draw_y + (y2 - y1) / 2.0
                cr.translate(cx, cy)
                cr.rotate(math.radians(rot))
                cr.translate(-cx, -cy)
            layout = PangoCairo.create_layout(cr)
            font_family = f"{text_obj.font_family_base}, DejaVu Sans, FreeSans, sans-serif"
            font_desc = Pango.FontDescription.from_string(font_family)
            if text_obj.is_bold: font_desc.set_weight(Pango.Weight.BOLD)
            if text_obj.is_italic: font_desc.set_style(Pango.Style.ITALIC)
            font_desc.set_absolute_size(int(text_obj.font_size * Pango.SCALE))
            layout.set_font_description(font_desc)
            layout.set_text(text_obj.text, -1)
            align = getattr(text_obj, 'alignment', 'left')
            box_w = (x2 - x1)
            if box_w > 0:
                nat_pw, _ = layout.get_size()
                w_to_set = max(box_w, nat_pw / Pango.SCALE)
                layout.set_width(int(w_to_set * Pango.SCALE))
                if align == 'center':
                    layout.set_alignment(Pango.Alignment.CENTER)
                elif align == 'right':
                    layout.set_alignment(Pango.Alignment.RIGHT)
                elif align == 'justify':
                    layout.set_justify(True)
            r, g, b = text_obj.color
            cr.set_source_rgba(r, g, b, 1.0)
            
            attr_list = Pango.AttrList()
            
            for match in re.finditer(r'(https?://[^\s]+|www\.[^\s]+)', text_obj.text):
                start_byte = len(text_obj.text[:match.start()].encode('utf-8'))
                end_byte = len(text_obj.text[:match.end()].encode('utf-8'))
                
                color_attr = Pango.attr_foreground_new(0, int(0.33*65535), int(0.8*65535))
                color_attr.start_index = start_byte
                color_attr.end_index = end_byte
                attr_list.change(color_attr)
                
                underline_attr = Pango.attr_underline_new(Pango.Underline.SINGLE)
                underline_attr.start_index = start_byte
                underline_attr.end_index = end_byte
                attr_list.change(underline_attr)
            
            if getattr(text_obj, 'is_underline', False):
                u_attr = Pango.attr_underline_new(Pango.Underline.SINGLE)
                u_attr.start_index = 0
                u_attr.end_index = 65535 
                attr_list.change(u_attr)
            if getattr(text_obj, 'is_strikethrough', False):
                s_attr = Pango.attr_strikethrough_new(True)
                s_attr.start_index = 0
                s_attr.end_index = 65535
                attr_list.change(s_attr)
            
            if not self.view_mode and self.selected_text == text_obj and getattr(self, 'word_selection_mode', False):
                if hasattr(self, 'selected_word_start_char') and hasattr(self, 'selected_word_end_char'):
                    start_byte = len(text_obj.text[:self.selected_word_start_char].encode('utf-8'))
                    end_byte = len(text_obj.text[:self.selected_word_end_char].encode('utf-8'))
                    
                    bg_attr = Pango.attr_background_new(int(0.2*65535), int(0.6*65535), int(1.0*65535))
                    bg_attr.start_index = start_byte
                    bg_attr.end_index = end_byte
                    attr_list.insert(bg_attr)
                    
                    fg_attr = Pango.attr_foreground_new(65535, 65535, 65535)
                    fg_attr.start_index = start_byte
                    fg_attr.end_index = end_byte
                    attr_list.insert(fg_attr)
            
            layout.set_attributes(attr_list)
            cr.move_to(draw_x, draw_y)
            PangoCairo.show_layout(cr, layout)
            cr.restore()

        for shape in self.editable_shapes:
            if shape.page_number != self.current_page_index:
                continue
            if getattr(shape, 'is_baked', False) and not self._is_table_preview_member(shape):
                continue
            if shape is self.dragged_object:
                continue
            
            x1, y1, x2, y2 = shape.bbox
            draw_x = x1
            draw_y = y1
            draw_w = x2 - x1
            draw_h = y2 - y1
            
            if abs(draw_w) < 1.0 or abs(draw_h) < 1.0:
                continue
            from .shape_geometry import draw_cairo
            draw_cairo(cr, shape)

        if hasattr(self,"form_tools"):
            self.form_tools.draw_fields(cr)
        if getattr(self,'_object_guides',None) and self.dragged_object is not None:
            from .form_builder import draw_guides
            draw_guides(cr,self._object_guides,self.doc[self.current_page_index].rect,self.zoom_level)
        if hasattr(self,'measure_tool'):
            self.measure_tool.draw(cr)
        if getattr(self,'node_tool',None):
            self.node_tool.draw(cr)
        self.stamp_interaction.draw(cr)
        placement=getattr(self,'_certificate_placement',None)
        if placement:placement.draw(cr)
        if self.temp_shape:
            from .shape_geometry import draw_cairo
            draw_cairo(cr, self.temp_shape, alpha=0.7)

        if self.temp_image_bbox:
            x1, y1, x2, y2 = self.temp_image_bbox
            draw_x = x1
            draw_y = y1
            draw_w = x2 - x1
            draw_h = y2 - y1

            style_context = area.get_style_context()
            found_img, img_rgba = style_context.lookup_color("accent_color")
            if not found_img:
                found_img, img_rgba = style_context.lookup_color("theme_selected_bg_color")
            if not found_img:
                img_rgba = Gdk.RGBA()
                img_rgba.parse("#3584e4")

            cr.set_source_rgba(img_rgba.red, img_rgba.green, img_rgba.blue, 0.25)
            cr.rectangle(draw_x, draw_y, draw_w, draw_h)
            cr.fill()

            cr.set_source_rgba(img_rgba.red, img_rgba.green, img_rgba.blue, 0.85)
            cr.set_line_width(2.0 / self.zoom_level)
            cr.set_dash([5.0 / self.zoom_level, 4.0 / self.zoom_level])
            cr.rectangle(draw_x, draw_y, draw_w, draw_h)
            cr.stroke()
            cr.set_dash([])

        # Render editable strokes on current page
        for stroke in getattr(self, 'editable_strokes', []):
            if getattr(stroke, 'page_number', None) != self.current_page_index:
                continue
            if getattr(stroke, 'is_baked', False):
                continue
            if stroke is self.dragged_object:
                continue
            cr.save()
            rot = getattr(stroke, 'rotation', 0.0) % 360.0
            if rot != 0.0 and stroke.bbox:
                sx1, sy1, sx2, sy2 = stroke.bbox
                cx = (sx1 + sx2) / 2.0
                cy = (sy1 + sy2) / 2.0
                cr.translate(cx, cy)
                cr.rotate(math.radians(rot))
                cr.translate(-cx, -cy)
            r, g, b = stroke.stroke_color
            opacity = getattr(stroke, 'opacity', 1.0)
            cr.set_source_rgba(r, g, b, opacity)
            cr.set_line_width(stroke.stroke_width)
            is_hl = getattr(stroke, 'tool_type', None) in ("highlighter", EditableStroke.TOOL_HIGHLIGHTER) or stroke.stroke_width >= 8.0
            if is_hl:
                cr.set_line_cap(cairo.LINE_CAP_SQUARE)
                cr.set_line_join(cairo.LINE_JOIN_BEVEL)
            else:
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)

            if len(stroke.points) == 1:
                px, py = stroke.points[0]
                rad = max(stroke.stroke_width / 2.0, 1.0 / self.zoom_level)
                if is_hl:
                    cr.rectangle(px - rad, py - rad, rad * 2.0, rad * 2.0)
                else:
                    cr.arc(px, py, rad, 0, 2 * math.pi)
                cr.fill()
            else:
                p0 = stroke.points[0]
                cr.move_to(p0[0], p0[1])
                for pt in stroke.points[1:]:
                    cr.line_to(pt[0], pt[1])
                cr.stroke()
            cr.restore()

        # Render live drawing temp stroke
        if getattr(self, 'temp_stroke', None) and self.temp_stroke.points:
            cr.save()
            r, g, b = self.temp_stroke.stroke_color
            opacity = getattr(self.temp_stroke, 'opacity', 1.0)
            cr.set_source_rgba(r, g, b, opacity)
            cr.set_line_width(self.temp_stroke.stroke_width)
            is_hl = getattr(self.temp_stroke, 'tool_type', None) in ("highlighter", EditableStroke.TOOL_HIGHLIGHTER) or self.tool_mode == "highlighter" or self.temp_stroke.stroke_width >= 8.0
            if is_hl:
                cr.set_line_cap(cairo.LINE_CAP_SQUARE)
                cr.set_line_join(cairo.LINE_JOIN_BEVEL)
            else:
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)

            if len(self.temp_stroke.points) == 1:
                px, py = self.temp_stroke.points[0]
                rad = max(self.temp_stroke.stroke_width / 2.0, 1.0 / self.zoom_level)
                if is_hl:
                    cr.rectangle(px - rad, py - rad, rad * 2.0, rad * 2.0)
                else:
                    cr.arc(px, py, rad, 0, 2 * math.pi)
                cr.fill()
            else:
                p0 = self.temp_stroke.points[0]
                cr.move_to(p0[0], p0[1])
                for pt in self.temp_stroke.points[1:]:
                    cr.line_to(pt[0], pt[1])
                cr.stroke()
                from .shape_geometry import stroke_ends
                _points, heads = stroke_ends(self.temp_stroke.points, self.temp_stroke.stroke_width,
                                             getattr(self.temp_stroke, 'arrow_start', False),
                                             getattr(self.temp_stroke, 'arrow_end', False))
                for head in heads:
                    cr.move_to(*head[0])
                    for point in head[1:]:
                        cr.line_to(*point)
                    cr.close_path()
                    cr.fill()
            cr.restore()

        if getattr(self, '_erase_drag', None):
            (sx, sy), (ex, ey) = self._erase_drag
            corners = [self._visual_to_unrotated_page_coords(x, y) for x, y in ((sx, sy), (ex, sy), (ex, ey), (sx, ey))]
            cr.save()
            cr.move_to(*corners[0])
            for corner in corners[1:]:
                cr.line_to(*corner)
            cr.close_path()
            cr.set_source_rgba(0.88, 0.11, 0.14, 0.12)
            cr.fill_preserve()
            cr.set_source_rgba(0.88, 0.11, 0.14, 0.85)
            cr.set_line_width(1.5 / self.zoom_level)
            cr.set_dash([4.0 / self.zoom_level, 3.0 / self.zoom_level])
            cr.stroke()
            cr.restore()

        selected_obj = getattr(self, 'selected_table', None) or self.selected_text or self.selected_image or self.selected_shape or self.selected_stroke
        if selected_obj and not self.dragged_object:
            is_image = isinstance(selected_obj, EditableImage)
            style_context = area.get_style_context()
            color_name = "accent_color"
            default_color = "#3584e4"

            found, rgba = style_context.lookup_color("accent_color")
            if not found:
                found, rgba = style_context.lookup_color("theme_selected_bg_color")
            if not found:
                rgba = Gdk.RGBA()
                rgba.parse("#3584e4")

            x1, y1, x2, y2 = selected_obj.bbox
            editing_text = selected_obj is self.inline_editor_text_obj
            if editing_text and getattr(self,'_inline_editor_bounds',None):
                x1,y1,x2,y2=self._inline_editor_bounds
            if getattr(self, 'word_selection_mode', False) and hasattr(self, 'selected_word_start_char') and isinstance(selected_obj, EditableText):
                x1, y1, x2, y2 = text_geometry.selection_bounds(
                    self.doc, selected_obj, self.selected_word_start_char, self.selected_word_end_char)

            padding = 3.0 / self.zoom_level
            rect_x = x1 - padding
            rect_y = y1 - padding
            rect_w = (x2 - x1) + (2 * padding)
            rect_h = (y2 - y1) + (2 * padding)

            cr.save()
            rot = getattr(selected_obj, 'rotation', 0.0) % 360.0
            if editing_text:
                rot=0
            if rot != 0.0:
                cx = rect_x + rect_w / 2.0
                cy = rect_y + rect_h / 2.0
                cr.translate(cx, cy)
                cr.rotate(math.radians(rot))
                cr.translate(-cx, -cy)

            cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.95)
            cr.set_line_width((2.5 if is_image else 2.0) / self.zoom_level)
            if is_image:
                cr.set_dash([4.0 / self.zoom_level, 4.0 / self.zoom_level])

            radius = min(5.0 / self.zoom_level, rect_w / 2.0, rect_h / 2.0)
            cr.new_sub_path()
            cr.arc(rect_x + radius, rect_y + radius, radius, math.pi, 1.5 * math.pi)
            cr.arc(rect_x + rect_w - radius, rect_y + radius, radius, 1.5 * math.pi, 2.0 * math.pi)
            cr.arc(rect_x + rect_w - radius, rect_y + rect_h - radius, radius, 0, 0.5 * math.pi)
            cr.arc(rect_x + radius, rect_y + rect_h - radius, radius, 0.5 * math.pi, math.pi)
            cr.close_path()
            cr.stroke()

            if not isinstance(selected_obj, TableSelection):
                # Stalk rotation handle
                stalk_len = 22.0 / self.zoom_level
                stalk_x = rect_x + rect_w / 2.0
                stalk_base_y = rect_y
                stalk_tip_y = rect_y - stalk_len
                rot_handle_r = 5.0 / self.zoom_level

                cr.save()
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 0.9)
                cr.set_line_width(1.5 / self.zoom_level)
                cr.set_dash([])
                cr.move_to(stalk_x, stalk_base_y)
                cr.line_to(stalk_x, stalk_tip_y)
                cr.stroke()

                cr.arc(stalk_x, stalk_tip_y, rot_handle_r, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                cr.fill_preserve()
                cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, 1.0)
                cr.set_line_width(1.5 / self.zoom_level)
                cr.stroke()
                cr.restore()

            if not editing_text:
                handle_size = 8.0 / self.zoom_level
                handle_color_rgba = rgba
                
                handles = [
                    ("nw", rect_x, rect_y),                                   # top-left
                    ("ne", rect_x + rect_w, rect_y),                          # top-right
                    ("sw", rect_x, rect_y + rect_h),                          # bottom-left
                    ("se", rect_x + rect_w, rect_y + rect_h),                 # bottom-right
                    ("n", rect_x + rect_w / 2.0, rect_y),                     # top
                    ("s", rect_x + rect_w / 2.0, rect_y + rect_h),            # bottom
                    ("w", rect_x, rect_y + rect_h / 2.0),                     # left
                    ("e", rect_x + rect_w, rect_y + rect_h / 2.0),            # right
                ]
                
                if isinstance(selected_obj, EditableText):
                    handles = handles[:4]
                for handle_name, handle_x, handle_y in handles:
                    cr.set_source_rgba(handle_color_rgba.red, handle_color_rgba.green, handle_color_rgba.blue, 1.0)
                    cr.rectangle(handle_x - handle_size / 2.0, handle_y - handle_size / 2.0, handle_size, handle_size)
                    cr.fill()
                    cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
                    cr.rectangle(handle_x - handle_size / 2.0, handle_y - handle_size / 2.0, handle_size, handle_size)
                    cr.set_line_width(1.0 / self.zoom_level)
                    cr.stroke()
            cr.restore()

        cr.restore()

        if self.view_mode and self.view_sel_rect:
            cr.save()
            cr.set_source_rgba(0.12,0.47,0.9,0.28)
            quads=getattr(self._active_session,'view_selection_quads',[])
            if quads:
                for quad in quads:
                    points=(quad.ul,quad.ur,quad.lr,quad.ll)
                    cr.move_to(page_offset_x+points[0].x*self.zoom_level,page_offset_y+points[0].y*self.zoom_level)
                    for point in points[1:]:
                        cr.line_to(page_offset_x+point.x*self.zoom_level,page_offset_y+point.y*self.zoom_level)
                    cr.close_path()
            else:
                x0,y0,x1,y1=self.view_sel_rect
                cr.rectangle(page_offset_x+x0*self.zoom_level,page_offset_y+y0*self.zoom_level,
                             (x1-x0)*self.zoom_level,(y1-y0)*self.zoom_level)
            cr.fill()
            cr.restore()

        if self._search_document is self.doc:
            page = self.doc[self.current_page_index]
            for result_index, (result_page, rect) in enumerate(self.search_results):
                if result_page != self.current_page_index:
                    continue
                visual_rect = rect * page.rotation_matrix
                cr.save()
                cr.set_source_rgba(1.0, 0.72, 0.12, 0.48 if result_index == self.search_current_result else 0.27)
                cr.rectangle(page_offset_x + visual_rect.x0 * self.zoom_level,
                             page_offset_y + visual_rect.y0 * self.zoom_level,
                             visual_rect.width * self.zoom_level,
                             visual_rect.height * self.zoom_level)
                cr.fill()
                cr.restore()

    def _visual_to_unrotated_page_coords(self, vis_x, vis_y):
        """Convert visual page coordinates to unrotated PDF cropbox coordinates."""
        if not self.doc or not (0 <= self.current_page_index < len(self.doc)):
            return vis_x, vis_y
        page = self.doc[self.current_page_index]
        if (page.rotation % 360) == 0:
            return vis_x, vis_y
        p = fitz.Point(vis_x, vis_y) * (~page.rotation_matrix)
        return p.x, p.y

    def _unrotated_to_visual_page_coords(self, unrot_x, unrot_y):
        """Convert unrotated PDF cropbox coordinates to visual page coordinates."""
        if not self.doc or not (0 <= self.current_page_index < len(self.doc)):
            return unrot_x, unrot_y
        page = self.doc[self.current_page_index]
        if (page.rotation % 360) == 0:
            return unrot_x, unrot_y
        p = fitz.Point(unrot_x, unrot_y) * page.rotation_matrix
        return p.x, p.y

    def _visual_to_unrotated_delta(self, dx, dy):
        """Convert a visual delta vector to an unrotated page delta vector."""
        if not self.doc or not (0 <= self.current_page_index < len(self.doc)):
            return dx, dy
        page = self.doc[self.current_page_index]
        if (page.rotation % 360) == 0:
            return dx, dy
        inv_mat = ~page.rotation_matrix
        p0 = fitz.Point(0, 0) * inv_mat
        p1 = fitz.Point(dx, dy) * inv_mat
        return p1.x - p0.x, p1.y - p0.y

    def _find_text_at_pos(self, page_x, page_y):
        """Find text at pos."""
        try:
            coords = self._visual_to_unrotated_page_coords(page_x, page_y)
            if isinstance(coords, (tuple, list)) and len(coords) == 2:
                page_x, page_y = coords
        except Exception:
            pass
        for text_obj in reversed(self.editable_texts):
            if getattr(text_obj, 'page_number', self.current_page_index) != self.current_page_index:
                continue
            if not text_obj.bbox: continue
            x1, y1, x2, y2 = text_obj.bbox
            rot = getattr(text_obj, 'rotation', 0.0) % 360.0
            px, py = page_x, page_y
            if rot != 0.0:
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                px, py = pdf_handler.rotate_point(page_x, page_y, cx, cy, -rot)
            tolerance = 2 / self.zoom_level
            if (x1 - tolerance) <= px <= (x2 + tolerance) and \
               (y1 - tolerance) <= py <= (y2 + tolerance):
                return text_obj
        return None

    def _find_image_at_pos(self, page_x, page_y):
        """Find image at pos."""
        try:
            coords = self._visual_to_unrotated_page_coords(page_x, page_y)
            if isinstance(coords, (tuple, list)) and len(coords) == 2:
                page_x, page_y = coords
        except Exception:
            pass
        for img_obj in reversed(self.editable_images):
            if getattr(img_obj, 'page_number', self.current_page_index) != self.current_page_index:
                continue
            if not img_obj.bbox: continue
            x1, y1, x2, y2 = img_obj.bbox
            rot = getattr(img_obj, 'rotation', 0.0) % 360.0
            px, py = page_x, page_y
            if rot != 0.0:
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                px, py = pdf_handler.rotate_point(page_x, page_y, cx, cy, -rot)
            if x1 <= px <= x2 and y1 <= py <= y2:
                return img_obj
        return None

    def _find_shape_at_pos(self, page_x, page_y):
        """Find shape at pos."""
        try:
            coords = self._visual_to_unrotated_page_coords(page_x, page_y)
            if isinstance(coords, (tuple, list)) and len(coords) == 2:
                page_x, page_y = coords
        except Exception:
            pass
        for shape_obj in reversed(self.editable_shapes):
            if shape_obj.page_number != self.current_page_index:
                continue
            if not shape_obj.bbox: 
                continue
            x1, y1, x2, y2 = shape_obj.bbox
            rot = getattr(shape_obj, 'rotation', 0.0) % 360.0
            px, py = page_x, page_y
            if rot != 0.0:
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                px, py = pdf_handler.rotate_point(page_x, page_y, cx, cy, -rot)
            tolerance = 3 / self.zoom_level
            if (x1 - tolerance) <= px <= (x2 + tolerance) and \
               (y1 - tolerance) <= py <= (y2 + tolerance):
                return shape_obj
        return None

    def _find_stroke_at_pos(self, page_x, page_y):
        """Find stroke at pos."""
        try:
            coords = self._visual_to_unrotated_page_coords(page_x, page_y)
            if isinstance(coords, (tuple, list)) and len(coords) == 2:
                page_x, page_y = coords
        except Exception:
            pass
        for stroke in reversed(getattr(self, 'editable_strokes', [])):
            if stroke.page_number != self.current_page_index:
                continue
            if not stroke.bbox:
                continue
            x1, y1, x2, y2 = stroke.bbox
            rot = getattr(stroke, 'rotation', 0.0) % 360.0
            px, py = page_x, page_y
            if rot != 0.0:
                cx = (x1 + x2) / 2.0
                cy = (y1 + y2) / 2.0
                px, py = pdf_handler.rotate_point(page_x, page_y, cx, cy, -rot)
            tolerance = max(stroke.stroke_width, 8.0) / self.zoom_level
            if (x1 - tolerance) <= px <= (x2 + tolerance) and \
               (y1 - tolerance) <= py <= (y2 + tolerance):
                return stroke
        return None

    def _is_table_preview_member(self, obj):
        table = getattr(self, 'table_drag_table', None)
        return (getattr(self, 'table_drag_state', None) is not None and table is not None
                and any(obj is member for member in table.objects))

    def _cancel_table_drag(self):
        states=getattr(self,'table_drag_state',None)
        table=getattr(self,'table_drag_table',None)
        if states is not None and table is not None:
            for obj,state in zip(table.objects,states):
                obj.__dict__.update(copy.deepcopy(state))
        self._clear_table_preview()
        self.table_drag_state=None
        self.table_drag_table=None
        self.table_resize_handle=None

    def _clear_table_preview(self):
        preview = getattr(self, '_table_preview_doc', None)
        if preview is not None:
            pdf_handler.release_page_snapshots(preview)
            pdf_handler.invalidate_page_cache(preview)
            preview.close()
        self._table_preview_doc = None

    def _report_command_error(self, message):
        self.status_label.set_text(message)
        self.pdf_view.queue_draw()
        show_error_dialog(self, message, _("err_title"))

    def _change_pages(self, operation, remap):
        result = []
        def mutate():
            outcome = operation()
            if not outcome[0]:
                raise ValueError(outcome[1])
            result.extend(outcome)
            session = self._active_session
            new_cache = {}
            for old_page,groups in session.page_objects.items():
                new_page = remap(old_page)
                if new_page is not None:
                    for group in groups:
                        for obj in group:
                            obj.page_number = new_page
                    new_cache[new_page] = groups
            current = remap(self.current_page_index)
            self.current_page_index = min(self.doc.page_count-1,current if current is not None else self.current_page_index)
            session.page_objects = new_cache
            self.doc.editor_page_models = new_cache
            groups = new_cache.get(self.current_page_index,([],[],[],[]))
            (self.editable_texts,self.editable_shapes,self.editable_images,self.editable_strokes) = groups
        success = self._mutate_document(mutate)
        return tuple(result) if success else (False,_("err_during_op",'Page operation failed'))

    def _mutate_document(self, mutation, page_num=None, rebase_pages=(), allow_view=False, allow_form=False):
        can_edit=getattr(self._active_session,'can_edit',True)
        can_fill=bool(self.doc and getattr(self.doc,'editor_can_fill_forms',can_edit))
        if not self.doc or (self.view_mode and not allow_view) or not (can_edit or (allow_form and can_fill)):
            return False
        self._apply_and_hide_editor(force_apply=True)
        self.commit_pending_format_change()
        command = DocumentMutationCommand(self, mutation, page_num, rebase_pages)
        command.form_fill_allowed=allow_form
        command.view_mode_allowed=allow_view
        try:
            if command.execute() is False:
                return False
        except Exception:
            # Roll back a partially applied mutation so the document and its
            # page models stay consistent with the undo history.
            if command.before is not None:
                command.before.restore(self)
                command._refresh()
            raise
        self.undo_manager.add_command(command)
        return True

    def _find_table_at_pos(self, page_x, page_y):
        obj = (self._find_image_at_pos(page_x, page_y) or self._find_text_at_pos(page_x, page_y)
               or self._find_shape_at_pos(page_x, page_y))
        table_id = getattr(obj, 'table_id', None)
        if not table_id:
            return None
        members = [item for item in self.editable_shapes + self.editable_texts
                   if getattr(item, 'table_id', None) == table_id
                   and item.page_number == self.current_page_index]
        if any(isinstance(item, EditableShape) for item in members):
            return TableSelection(members)
        return None

    def _select_table(self, table):
        self.selected_table = table
        self.selected_text = self.selected_image = self.selected_shape = self.selected_stroke = None
        self.word_selection_mode = False
        self._update_ui_state()
        self.pdf_view.queue_draw()

    def _update_table_drag(self, offset_x, offset_y, keep_ratio=False):
        # The gesture owns its table; the selection may change mid-drag (double-click).
        table = self.table_drag_table
        x0, y0, x1, y1 = self.table_drag_bbox
        dx, dy = self._visual_to_unrotated_delta(offset_x/self.zoom_level, offset_y/self.zoom_level)
        handle = self.table_resize_handle
        # Keep the table on the page (unrotated page coordinates).
        page = self.doc[table.page_number].cropbox
        page_w, page_h = page.width, page.height
        if handle:
            width, height = x1-x0, y1-y0
            sx = (width + (dx if 'e' in handle else -dx))/width if 'e' in handle or 'w' in handle else 1
            sy = (height + (dy if 's' in handle else -dy))/height if 's' in handle or 'n' in handle else 1
            max_sx = ((page_w-x0) if 'e' in handle else x1)/width if 'e' in handle or 'w' in handle else float('inf')
            max_sy = ((page_h-y0) if 's' in handle else y1)/height if 's' in handle or 'n' in handle else float('inf')
            # Corners resize freely; with Shift they keep the table's proportions.
            if len(handle) == 2 and keep_ratio:
                scale = sx if abs(sx-1) >= abs(sy-1) else sy
                sx = sy = max(min(scale, max_sx, max_sy), 10/min(width, height))
            else:
                sx = max(min(sx, max_sx), 10/width) if max_sx != float('inf') else 1
                sy = max(min(sy, max_sy), 10/height) if max_sy != float('inf') else 1
            nx0 = x1-width*sx if 'w' in handle else x0
            ny0 = y1-height*sy if 'n' in handle else y0
            bounds = (nx0, ny0, nx0+width*sx, ny0+height*sy)
        else:
            # Tables larger than the page keep their position on that axis.
            if x1-x0 <= page_w:
                dx = min(max(dx, -x0), page_w-x1)
            if y1-y0 <= page_h:
                dy = min(max(dy, -y0), page_h-y1)
            bounds = (x0+dx, y0+dy, x1+dx, y1+dy)
        table.transform(self.table_drag_state, self.table_drag_bbox, bounds)
        self.pdf_view.queue_draw()

    def _find_resize_handle_at_pos(self, drawn_x, drawn_y, selected_obj):
        """Find resize handle or rotation stalk handle at pos."""
        if not selected_obj or not selected_obj.bbox:
            return None
        
        x1, y1, x2, y2 = selected_obj.bbox
        page_offset_x = max(0, (self.pdf_view.get_allocated_width() - self.current_pdf_page_width) / 2)
        page_offset_y = max(0, (self.pdf_view.get_allocated_height() - self.current_pdf_page_height) / 2)
        
        vis_x = (drawn_x - page_offset_x) / self.zoom_level
        vis_y = (drawn_y - page_offset_y) / self.zoom_level
        unrot_x, unrot_y = vis_x, vis_y
        try:
            coords = self._visual_to_unrotated_page_coords(vis_x, vis_y)
            if isinstance(coords, (tuple, list)) and len(coords) == 2:
                unrot_x, unrot_y = coords
        except Exception:
            pass

        padding = 3.0 / self.zoom_level
        rect_x = x1 - padding
        rect_y = y1 - padding
        rect_w = (x2 - x1) + (2 * padding)
        rect_h = (y2 - y1) + (2 * padding)

        rot = getattr(selected_obj, 'rotation', 0.0) % 360.0
        cx = rect_x + rect_w / 2.0
        cy = rect_y + rect_h / 2.0

        if rot != 0.0:
            px, py = pdf_handler.rotate_point(unrot_x, unrot_y, cx, cy, -rot)
        else:
            px, py = unrot_x, unrot_y

        stalk_len = 22.0 / self.zoom_level
        rot_hx = cx
        rot_hy = rect_y - stalk_len
        rot_tolerance = 8.0 / self.zoom_level
        if not isinstance(selected_obj, TableSelection) and math.hypot(px - rot_hx, py - rot_hy) <= rot_tolerance:
            return "rotate"
        
        if isinstance(selected_obj, EditableText) and self.inline_editor_widget is not None:
            return None
        
        handle_size = 8.0 / self.zoom_level
        handle_tolerance = (4.0 if getattr(self, 'tool_mode', None) in ("pen", "highlighter") else 4.5) / self.zoom_level
        
        handles = [
            ("nw", rect_x, rect_y),
            ("ne", rect_x + rect_w, rect_y),
            ("sw", rect_x, rect_y + rect_h),
            ("se", rect_x + rect_w, rect_y + rect_h),
            ("n", rect_x + rect_w / 2.0, rect_y),
            ("s", rect_x + rect_w / 2.0, rect_y + rect_h),
            ("w", rect_x, rect_y + rect_h / 2.0),
            ("e", rect_x + rect_w, rect_y + rect_h / 2.0),
        ]
        
        if isinstance(selected_obj, EditableText):
            handles = handles[:4]
        for handle_name, handle_x, handle_y in handles:
            if abs(px - handle_x) <= handle_tolerance and abs(py - handle_y) <= handle_tolerance:
                return handle_name
        
        return None

    def _handle_add_image_action(self, page_x_unzoomed, page_y_unzoomed):
        """Handle add image action."""
        filter_img = Gtk.FileFilter(name=_("image_filter_label"))
        for mime in ["image/png", "image/jpeg", "image/gif", "image/bmp"]:
            filter_img.add_mime_type(mime)

        def on_open_finish(file):
            if file:
                image_path = file.get_path()
                try:
                    with open(image_path, 'rb') as f:
                        image_bytes = f.read()

                    pixbuf = GdkPixbuf.Pixbuf.new_from_file(image_path)
                    img_w, img_h = pixbuf.get_width(), pixbuf.get_height()

                    target_w = 150.0
                    target_h = (img_h / img_w) * target_w if img_w > 0 else 150.0
                    unrot_x, unrot_y = self._visual_to_unrotated_page_coords(page_x_unzoomed, page_y_unzoomed)
                    rect = (unrot_x, unrot_y,
                            unrot_x + target_w, unrot_y + target_h)

                    new_image_obj = EditableImage(
                        bbox=rect,
                        page_number=self.current_page_index,
                        xref=None,
                        image_bytes=image_bytes
                    )

                    command = AddObjectCommand(self, new_image_obj)
                    command.execute()
                    self.undo_manager.add_command(command)

                except Exception as e:
                    show_error_dialog(self, _("image_add_error", e), _("image_error_title"))

        show_open_file_dialog(self, _("image_select_title"), filters=[filter_img], callback=on_open_finish)

    def _update_text_format_controls(self, text_obj):
        """Update text format controls."""
        if not text_obj or self.font_scan_in_progress:
            if self.tool_mode == "add_text":
                return
            if not self.font_scan_in_progress and self.font_combo.get_sensitive():
                self.font_combo.handler_block_by_func(self.on_text_format_changed)
                self.font_combo.set_active(0)
                self.font_combo.handler_unblock_by_func(self.on_text_format_changed)

            self.font_size_spin.handler_block_by_func(self.on_text_format_changed)
            self.font_size_spin.set_value(11)
            self.font_size_spin.handler_unblock_by_func(self.on_text_format_changed)

            default_rgba = Gdk.RGBA(); default_rgba.parse("black")
            self.color_button.handler_block_by_func(self.on_text_format_changed)
            self.color_button.set_rgba(default_rgba)
            self.color_button.handler_unblock_by_func(self.on_text_format_changed)

            if self.bold_button:
                self.bold_button.handler_block_by_func(self.on_text_format_changed)
                self.bold_button.set_active(False)
                self.bold_button.handler_unblock_by_func(self.on_text_format_changed)
            if self.italic_button:
                self.italic_button.handler_block_by_func(self.on_text_format_changed)
                self.italic_button.set_active(False)
                self.italic_button.handler_unblock_by_func(self.on_text_format_changed)
            if hasattr(self, 'underline_button') and self.underline_button:
                self.underline_button.handler_block_by_func(self.on_text_format_changed)
                self.underline_button.set_active(False)
                self.underline_button.handler_unblock_by_func(self.on_text_format_changed)
            if hasattr(self, 'strikethrough_button') and self.strikethrough_button:
                self.strikethrough_button.handler_block_by_func(self.on_text_format_changed)
                self.strikethrough_button.set_active(False)
                self.strikethrough_button.handler_unblock_by_func(self.on_text_format_changed)
            align_btns = [getattr(self, 'align_left_button', None), getattr(self, 'align_center_button', None),
                          getattr(self, 'align_right_button', None), getattr(self, 'align_justify_button', None)]
            for b in align_btns:
                if b:
                    b.handler_block_by_func(self.on_text_format_changed)
            cur_align = getattr(self, '_last_alignment', 'left')
            target_btn = getattr(self, 'align_left_button', None)
            if cur_align == 'center' and getattr(self, 'align_center_button', None):
                target_btn = self.align_center_button
            elif cur_align == 'right' and getattr(self, 'align_right_button', None):
                target_btn = self.align_right_button
            elif cur_align == 'justify' and getattr(self, 'align_justify_button', None):
                target_btn = self.align_justify_button

            for b in align_btns:
                if b and b != target_btn and b.get_active():
                    b.set_active(False)
            if target_btn and not target_btn.get_active():
                target_btn.set_active(True)

            for b in align_btns:
                if b:
                    b.handler_unblock_by_func(self.on_text_format_changed)
            return

        signals_blocked = False
        align_btns = [getattr(self, 'align_left_button', None), getattr(self, 'align_center_button', None),
                      getattr(self, 'align_right_button', None), getattr(self, 'align_justify_button', None)]
        try:
            widgets_to_block = [self.font_combo, self.font_size_spin, self.color_button, self.bold_button,
                                self.italic_button, getattr(self, 'underline_button', None),
                                getattr(self, 'strikethrough_button', None)] + [b for b in align_btns if b]
            for widget in widgets_to_block:
                if widget: widget.handler_block_by_func(self.on_text_format_changed)
            signals_blocked = True

            active_font_index = -1
            target_family_base = text_obj.font_family_base 
            normalized_target_family_base = target_family_base.replace(" ", "").lower() if target_family_base else ""

            if target_family_base and utils.FONT_FAMILY_LIST_SORTED:
                model = self.font_combo.get_model()
                if model:
                    for i, row in enumerate(model):
                        combo_family_key = row[1]
                        normalized_combo_key = combo_family_key.replace(" ", "").lower()
                        if normalized_combo_key == normalized_target_family_base:
                            active_font_index = i
                            break
                
                if active_font_index == -1:
                    print(f"Warning: Normalized font '{normalized_target_family_base}' from selected text not directly in combo. Trying partial on original names.")
                    for i, row in enumerate(model):
                        combo_family_key = row[1]
                        if target_family_base and combo_family_key and target_family_base.lower() in combo_family_key.lower():
                            active_font_index = i
                            break
                        elif target_family_base and combo_family_key and combo_family_key.lower() in target_family_base.lower():
                            active_font_index = i
                            break
                    if active_font_index == -1 and len(model) > 0:
                         active_font_index = 0 
                         print(f"Warning: No good match for '{target_family_base}' even with normalization/partial, defaulting combo to index 0.")


            if active_font_index != -1 and active_font_index < len(self.font_store):
                 self.font_combo.set_active(active_font_index)
            elif len(self.font_store) > 0:
                 self.font_combo.set_active(0)
            
            self.font_combo.set_tooltip_text(_("font_tip_original", text_obj.font_family_original))

            self.font_size_spin.set_value(text_obj.font_size)
            rgba = Gdk.RGBA(); rgba.red, rgba.green, rgba.blue = text_obj.color; rgba.alpha = 1.0
            self.color_button.set_rgba(rgba)
            if self.bold_button: self.bold_button.set_active(text_obj.is_bold)
            if self.italic_button: self.italic_button.set_active(text_obj.is_italic)
            if hasattr(self, 'underline_button') and self.underline_button:
                self.underline_button.set_active(getattr(text_obj, 'is_underline', False))
            if hasattr(self, 'strikethrough_button') and self.strikethrough_button:
                self.strikethrough_button.set_active(getattr(text_obj, 'is_strikethrough', False))

            cur_align = getattr(text_obj, 'alignment', 'left')
            target_btn = getattr(self, 'align_left_button', None)
            if cur_align == 'center' and getattr(self, 'align_center_button', None):
                target_btn = self.align_center_button
            elif cur_align == 'right' and getattr(self, 'align_right_button', None):
                target_btn = self.align_right_button
            elif cur_align == 'justify' and getattr(self, 'align_justify_button', None):
                target_btn = self.align_justify_button

            for b in align_btns:
                if b and b != target_btn and b.get_active():
                    b.set_active(False)
            if target_btn and not target_btn.get_active():
                target_btn.set_active(True)

            self._last_font_family = target_family_base
            self._last_font_size = text_obj.font_size
            self._last_color = text_obj.color
            self._last_is_bold = text_obj.is_bold
            self._last_is_italic = text_obj.is_italic
            self._last_is_strikethrough = getattr(text_obj, 'is_strikethrough', False)
            self._last_alignment = cur_align

        finally:
            if signals_blocked:
                widgets_to_unblock = [self.font_combo, self.font_size_spin, self.color_button, self.bold_button,
                                      self.italic_button, getattr(self, 'underline_button', None),
                                      getattr(self, 'strikethrough_button', None)] + [b for b in align_btns if b]
                for widget in widgets_to_unblock:
                    if widget: widget.handler_unblock_by_func(self.on_text_format_changed)

    def _get_current_alignment(self):
        """Get the current alignment string."""
        if hasattr(self, 'align_center_button') and self.align_center_button and self.align_center_button.get_active():
            return 'center'
        if hasattr(self, 'align_right_button') and self.align_right_button and self.align_right_button.get_active():
            return 'right'
        if hasattr(self, 'align_justify_button') and self.align_justify_button and self.align_justify_button.get_active():
            return 'justify'
        return 'left'

    def _get_current_format_settings(self):
        """Get the current format settings."""
        font_family_display = "Sans"
        font_pdf_name = "helv"
        iter = self.font_combo.get_active_iter()
        if iter:
            font_family_display = self.font_store[iter][0]
            font_pdf_name = self.font_store[iter][1]

        font_size = self.font_size_spin.get_value()

        rgba = self.color_button.get_rgba()
        color = (rgba.red, rgba.green, rgba.blue)

        is_bold = self.bold_button.get_active() if self.bold_button else False
        is_italic = self.italic_button.get_active() if self.italic_button else False
        is_underline = self.underline_button.get_active() if hasattr(self, 'underline_button') and self.underline_button else False
        is_strikethrough = self.strikethrough_button.get_active() if hasattr(self, 'strikethrough_button') and self.strikethrough_button else False
        alignment = self._get_current_alignment()

        return font_family_display, font_pdf_name, font_size, color, is_bold, is_italic, is_underline, is_strikethrough, alignment

    def _update_shape_format_controls(self, shape_obj):
        """Update shape format controls."""
        try:
            self.shape_fill_button.handler_block_by_func(self.on_shape_format_changed)
            self.shape_stroke_button.handler_block_by_func(self.on_shape_format_changed)
            self.shape_stroke_width_spin.handler_block_by_func(self.on_shape_format_changed)

            if not shape_obj:
                fill_rgba = Gdk.RGBA()
                fill_rgba.parse("white")
                self.shape_fill_button.set_rgba(fill_rgba)

                stroke_rgba = Gdk.RGBA()
                stroke_rgba.parse("black")
                self.shape_stroke_button.set_rgba(stroke_rgba)

                self.shape_stroke_width_spin.set_value(2.0)
            else:
                fill_r, fill_g, fill_b = shape_obj.fill_color
                fill_rgba = Gdk.RGBA()
                fill_rgba.red, fill_rgba.green, fill_rgba.blue = fill_r, fill_g, fill_b
                fill_rgba.alpha = 1.0
                self.shape_fill_button.set_rgba(fill_rgba)

                stroke_r, stroke_g, stroke_b = shape_obj.stroke_color
                stroke_rgba = Gdk.RGBA()
                stroke_rgba.red, stroke_rgba.green, stroke_rgba.blue = stroke_r, stroke_g, stroke_b
                stroke_rgba.alpha = 1.0
                self.shape_stroke_button.set_rgba(stroke_rgba)

                self.shape_stroke_width_spin.set_value(shape_obj.stroke_width)

        finally:
            self.shape_fill_button.handler_unblock_by_func(self.on_shape_format_changed)
            self.shape_stroke_button.handler_unblock_by_func(self.on_shape_format_changed)
            self.shape_stroke_width_spin.handler_unblock_by_func(self.on_shape_format_changed)
        if hasattr(self, 'shape_tools'):
            self.shape_tools.sync_shape(shape_obj)

    def _update_stroke_format_controls(self, stroke_obj):
        """Update stroke format controls."""
        try:
            self.stroke_color_button.handler_block_by_func(self.on_stroke_format_changed)
            self.stroke_width_spin.handler_block_by_func(self.on_stroke_format_changed)

            if not stroke_obj:
                if self.tool_mode == "highlighter":
                    r, g, b = self.highlighter_color
                    w = self.highlighter_width
                else:
                    r, g, b = self.pen_color
                    w = self.pen_width
            else:
                r, g, b = stroke_obj.stroke_color
                w = stroke_obj.stroke_width

            rgba = Gdk.RGBA()
            rgba.red, rgba.green, rgba.blue, rgba.alpha = r, g, b, 1.0
            self.stroke_color_button.set_rgba(rgba)
            self.stroke_width_spin.set_value(w)
        finally:
            self.stroke_color_button.handler_unblock_by_func(self.on_stroke_format_changed)
            self.stroke_width_spin.handler_unblock_by_func(self.on_stroke_format_changed)
        if hasattr(self, 'shape_tools'):
            self.shape_tools.sync_line(stroke_obj)

    def _update_rotation_controls(self, selected_obj):
        """Sync rotation spin button and reset button with selected object's rotation angle."""
        if not hasattr(self, 'rotation_spin'):
            return

        self.rotation_spin.handler_block_by_func(self.on_object_rotation_spin_changed)
        try:
            if selected_obj:
                rot = round(getattr(selected_obj, 'rotation', 0.0)) % 360
                self.rotation_spin.set_value(rot)
                if hasattr(self, 'rotate_obj_reset_button'):
                    self.rotate_obj_reset_button.set_sensitive(rot != 0)
                if hasattr(self, 'rotate_obj_cw_button'):
                    self.rotate_obj_cw_button.set_sensitive(True)
                if hasattr(self, 'rotate_obj_ccw_button'):
                    self.rotate_obj_ccw_button.set_sensitive(True)
            else:
                self.rotation_spin.set_value(0)
                if hasattr(self, 'rotate_obj_reset_button'):
                    self.rotate_obj_reset_button.set_sensitive(False)
                if hasattr(self, 'rotate_obj_cw_button'):
                    self.rotate_obj_cw_button.set_sensitive(False)
                if hasattr(self, 'rotate_obj_ccw_button'):
                    self.rotate_obj_ccw_button.set_sensitive(False)
        finally:
            self.rotation_spin.handler_unblock_by_func(self.on_object_rotation_spin_changed)

    def on_rotate_object_cw_clicked(self, button=None):
        """Rotate the currently selected object 90 degrees clockwise."""
        selected_obj = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
        if not selected_obj:
            return

        old_rot = getattr(selected_obj, 'rotation', 0.0) % 360.0
        new_rot = (old_rot + 90.0) % 360.0

        command = RotateObjectCommand(self, selected_obj, old_rot, new_rot)
        command.execute()
        self.undo_manager.add_command(command)
        if isinstance(selected_obj, EditableText):
            self.selected_text = selected_obj
            self.pending_format_change_obj = selected_obj
            self.before_format_change_state = copy.deepcopy(selected_obj.__dict__)
            self._update_text_format_controls(selected_obj)

    def on_rotate_object_ccw_clicked(self, button=None):
        """Rotate the currently selected object 90 degrees counterclockwise."""
        selected_obj = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
        if not selected_obj:
            return

        old_rot = getattr(selected_obj, 'rotation', 0.0) % 360.0
        new_rot = (old_rot - 90.0) % 360.0

        command = RotateObjectCommand(self, selected_obj, old_rot, new_rot)
        command.execute()
        self.undo_manager.add_command(command)
        if isinstance(selected_obj, EditableText):
            self.selected_text = selected_obj
            self.pending_format_change_obj = selected_obj
            self.before_format_change_state = copy.deepcopy(selected_obj.__dict__)
            self._update_text_format_controls(selected_obj)

    def on_rotate_object_reset_clicked(self, button=None):
        """Reset rotation of selected object to 0 degrees."""
        selected_obj = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
        if not selected_obj:
            return

        old_rot = getattr(selected_obj, 'rotation', 0.0) % 360.0
        if old_rot == 0.0:
            return

        command = RotateObjectCommand(self, selected_obj, old_rot, 0.0)
        command.execute()
        self.undo_manager.add_command(command)
        if isinstance(selected_obj, EditableText):
            self.selected_text = selected_obj
            self.pending_format_change_obj = selected_obj
            self.before_format_change_state = copy.deepcopy(selected_obj.__dict__)
            self._update_text_format_controls(selected_obj)

    def on_object_rotation_spin_changed(self, spin_button):
        """Handle numeric spin button changes for object rotation."""
        selected_obj = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
        if not selected_obj:
            return

        old_rot = getattr(selected_obj, 'rotation', 0.0) % 360.0
        new_rot = float(spin_button.get_value()) % 360.0

        if abs(old_rot - new_rot) < 0.1:
            return

        command = RotateObjectCommand(self, selected_obj, old_rot, new_rot)
        command.execute()
        self.undo_manager.add_command(command)
        if isinstance(selected_obj, EditableText):
            self.selected_text = selected_obj
            self.pending_format_change_obj = selected_obj
            self.before_format_change_state = copy.deepcopy(selected_obj.__dict__)
            self._update_text_format_controls(selected_obj)

    def _show_inline_editor(self, text_obj, click_x=None, click_y=None):
        """Edit directly over the selected PDF text."""
        self._hide_inline_editor()
        if not text_obj:
            return

        self.inline_editor_text_obj = text_obj
        self._inline_editor_preview_doc = None
        if text_obj.bbox and (not text_obj.is_new or getattr(text_obj,'is_baked',False)):
            try:
                preview = fitz.open()
                preview.insert_pdf(self.doc, from_page=self.current_page_index,
                                   to_page=self.current_page_index)
                preview_page = preview[0]
                if text_obj.is_new or getattr(text_obj,'_ghost_redacted',False):
                    key=(id(self.doc),self.current_page_index)
                    pdf_handler._page_snapshots[id(preview),0]=pdf_handler._page_snapshots[key]
                    pdf_handler._page_original_links[id(preview),0]=copy.deepcopy(pdf_handler._page_original_links.get(key,[]))
                    groups=[]
                    for group in (self.editable_texts,self.editable_shapes,self.editable_images,self.editable_strokes):
                        clones=[copy.deepcopy(obj) for obj in group if obj is not text_obj and obj.page_number==self.current_page_index]
                        for obj in clones:
                            obj.page_number=0
                        groups.append(clones)
                    success,error=pdf_handler.rebuild_page(preview,0,*groups[:3],all_strokes=groups[3])
                    if not success:
                        raise ValueError(error)
                else:
                    rect=fitz.Rect(text_obj.bbox)
                    rotation=getattr(text_obj,'rotation',0)
                    if rotation:
                        rect=(rect.quad*pdf_handler.get_rotation_matrix((rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2,rotation)).rect
                    preview_page.add_redact_annot(rect,fill=False,cross_out=False)
                    preview_page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                                  text=fitz.PDF_REDACT_TEXT_REMOVE)
                self._inline_editor_preview_doc = preview
            except Exception as error:
                if 'preview' in locals():
                    pdf_handler.release_page_snapshots(preview)
                    preview.close()
                print(f"Inline edit preview unavailable: {error}")

        frame = Gtk.Frame()
        frame.add_css_class("inline-editor-frame")
        frame.set_halign(Gtk.Align.START)
        frame.set_valign(Gtk.Align.START)

        tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        tv.set_left_margin(0)
        tv.set_right_margin(0)
        tv.set_top_margin(0)
        tv.set_bottom_margin(0)
        tv.set_accepts_tab(False)
        align_val = getattr(text_obj, 'alignment', 'left')
        if align_val == 'center':
            tv.set_justification(Gtk.Justification.CENTER)
        elif align_val == 'right':
            tv.set_justification(Gtk.Justification.RIGHT)
        elif align_val == 'justify':
            tv.set_justification(Gtk.Justification.FILL)
        else:
            tv.set_justification(Gtk.Justification.LEFT)
        tv.get_buffer().set_text(text_obj.text)
        if text_obj.is_new:
            buffer = tv.get_buffer()
            buffer.select_range(buffer.get_start_iter(), buffer.get_end_iter())
        tv.add_css_class("inline-editor-tv")
        tv.get_buffer().connect("changed", self._on_inline_editor_text_changed)
        frame.set_child(tv)

        focus_ctrl = Gtk.EventControllerFocus()
        focus_ctrl.connect("leave", self._on_inline_editor_focus_leave)
        tv.add_controller(focus_ctrl)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_inline_editor_key)
        tv.add_controller(key_ctrl)

        self.pdf_overlay.add_overlay(frame)
        self.inline_editor_widget = frame
        self.inline_editor_tv = tv
        self._inline_editor_font_provider = Gtk.CssProvider()
        self._inline_editor_font_zoom = None
        tv.get_style_context().add_provider(
            self._inline_editor_font_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._update_inline_editor_position()
        self.pdf_view.queue_draw()
        def focus_editor():
            self._inline_editor_focus_source = None
            if self.inline_editor_tv is tv and tv.get_root() is self:
                tv.grab_focus()
            # grab_focus() returns True on success. Returning that from an idle
            # callback would keep focusing this widget after it is detached.
            return GLib.SOURCE_REMOVE
        self._inline_editor_focus_source = GLib.idle_add(focus_editor)

    def _on_inline_editor_text_changed(self, buffer):
        if self.inline_editor_widget:
            self._update_inline_editor_position()
            self.pdf_view.queue_draw()

    def _hide_inline_editor(self):
        """Hide inline editor."""
        source=getattr(self,'_inline_editor_focus_source',None)
        if source:
            GLib.source_remove(source)
            self._inline_editor_focus_source=None
        widget=self.inline_editor_widget
        # Clear references before unparenting, which can emit focus-leave.
        self.inline_editor_widget = None
        self.inline_editor_tv = None
        self.inline_editor_text_obj = None
        self._inline_editor_bounds = None
        self._inline_editor_wrapped_text = None
        self._inline_editor_font_provider = None
        if widget:
            focus=self.get_focus()
            if focus and (focus is widget or focus.is_ancestor(widget)):
                self.set_focus(None)
            if widget.get_parent() is self.pdf_overlay:
                self.pdf_overlay.remove_overlay(widget)
        preview = getattr(self, '_inline_editor_preview_doc', None)
        if preview:
            pdf_handler.release_page_snapshots(preview)
            pdf_handler.invalidate_page_cache(preview)
            preview.close()
            self._inline_editor_preview_doc = None
            self.pdf_view.queue_draw()

    def _commit_inline_edit(self):
        """Commit inline edit."""
        if not hasattr(self, 'inline_editor_tv') or not self.inline_editor_tv or not self.inline_editor_text_obj:
            self._hide_inline_editor()
            return

        text_obj_to_apply = self.inline_editor_text_obj
        old_properties = copy.deepcopy(text_obj_to_apply.__dict__)

        buf = self.inline_editor_tv.get_buffer()
        new_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        if getattr(self,'_inline_editor_wrapped_text',None) is not None:
            new_text=self._inline_editor_wrapped_text

        self._hide_inline_editor()

        def _calc_bbox(obj, text):
            """Calc bbox."""
            x1, y1 = obj.x, obj.y
            _surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
            _cr = cairo.Context(_surf)
            layout = PangoCairo.create_layout(_cr)
            desc = Pango.FontDescription.from_string(obj.font_family_base)
            if obj.is_bold: desc.set_weight(Pango.Weight.BOLD)
            if obj.is_italic: desc.set_style(Pango.Style.ITALIC)
            desc.set_absolute_size(int(obj.font_size * Pango.SCALE))
            layout.set_font_description(desc)
            layout.set_text(text or "A", -1)
            pw, ph = layout.get_size()
            calc_w = pw / Pango.SCALE
            if getattr(obj, 'bbox', None):
                old_w = obj.bbox[2] - obj.bbox[0]
                final_w = max(calc_w, old_w) if getattr(obj, 'alignment', 'left') != 'left' else calc_w
            else:
                final_w = calc_w
            return (x1, y1, x1 + final_w, y1 + ph / Pango.SCALE)

        if text_obj_to_apply.is_new and text_obj_to_apply not in self.editable_texts:
            text_obj_to_apply.text = new_text
            text_obj_to_apply.is_baked = True
            text_obj_to_apply.bbox = _calc_bbox(text_obj_to_apply, new_text)
            command = AddObjectCommand(self, text_obj_to_apply)
            command.execute()
            self.undo_manager.add_command(command)
            self._refresh_thumbnail(self.current_page_index)
        else:
            new_properties = copy.deepcopy(text_obj_to_apply.__dict__)
            new_properties['text'] = new_text
            if old_properties['text'] != new_text:
                x1, y1 = new_properties['bbox'][0], new_properties['bbox'][1]
                _surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                _cr = cairo.Context(_surf)
                layout = PangoCairo.create_layout(_cr)
                desc = Pango.FontDescription.from_string(new_properties['font_family_base'])
                if new_properties.get('is_bold'): desc.set_weight(Pango.Weight.BOLD)
                if new_properties.get('is_italic'): desc.set_style(Pango.Style.ITALIC)
                desc.set_absolute_size(int(new_properties['font_size'] * Pango.SCALE))
                layout.set_font_description(desc)
                layout.set_text(new_text or "A", -1)
                pw, ph = layout.get_size()
                calc_w = pw / Pango.SCALE
                if old_properties.get('bbox'):
                    old_w = old_properties['bbox'][2] - old_properties['bbox'][0]
                    final_w = max(calc_w, old_w) if new_properties.get('alignment', 'left') != 'left' else calc_w
                else:
                    final_w = calc_w
                new_properties['bbox'] = (x1, y1, x1 + final_w, y1 + ph / Pango.SCALE)
                command = EditObjectCommand(self, text_obj_to_apply, old_properties, new_properties)
                command.execute()
                self.undo_manager.add_command(command)
                self._refresh_thumbnail(self.current_page_index)

        if text_obj_to_apply:
            self.selected_text = text_obj_to_apply
            self.pending_format_change_obj = text_obj_to_apply
            self.before_format_change_state = copy.deepcopy(text_obj_to_apply.__dict__)
            self._update_text_format_controls(text_obj_to_apply)

        self._update_ui_state()
        self.pdf_view.queue_draw()

    def _on_inline_editor_focus_leave(self, controller):
        """Commit active inline text edit when editor widget loses focus."""
        editor=controller.get_widget()
        if editor is not self.inline_editor_tv:
            return
        focus_widget = self.get_focus()
        if focus_widget:
            curr = focus_widget
            while curr:
                if curr == getattr(self, 'main_toolbar', None):
                    return
                curr = curr.get_parent()
                
        def commit_if_current():
            if self.inline_editor_tv is editor:
                self._commit_inline_edit()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(commit_if_current)

    def _on_inline_editor_key(self, controller, keyval, keycode, state):
        """Use Escape to cancel and Ctrl+Enter to apply the edit."""
        if keyval == Gdk.KEY_Escape:
            self._hide_inline_editor()
            self._update_ui_state()
            self.pdf_view.queue_draw()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and state & Gdk.ModifierType.CONTROL_MASK:
            self._commit_inline_edit()
            return True
        return False

    def hide_text_editor(self):
        """Dismiss the inline text editing widget."""
        self._hide_inline_editor()

    def _apply_and_hide_editor(self, force_apply=False):
        """Commit pending changes and dismiss inline text editor."""
        self._commit_inline_edit()

    def check_unsaved_changes(self, session: Optional[DocumentSession] = None):
        """Prompt user to save pending modifications. Returns True if cancelled."""
        target_session = session or getattr(self, '_active_session', None)
        if not target_session or not target_session.is_modified:
            return False

        if target_session != self._active_session:
            self.set_active_session(target_session)

        response = show_save_changes_dialog(self)
        
        if response == Gtk.ResponseType.ACCEPT:
            if target_session.pdf_path:
                self.save_document(target_session.pdf_path, incremental=False)
                return False
            else:
                self.on_save_as(None, None)
                return target_session.is_modified
        elif response == Gtk.ResponseType.REJECT:
            logger.debug(_("dbg_discarding_unsaved"))
            target_session.is_modified = False
            return False
        else:
            return True

        return False

    def on_drop(self, drop_target, value, x, y):
        """Handle drag-and-drop of PDF documents into the main viewer."""
        if isinstance(value, Gio.File):
            filepath = value.get_path()
            if filepath and filepath.lower().endswith('.pdf'):
                if self.doc:
                    self._offer_merge_or_open(filepath)
                else:
                    GLib.idle_add(self.load_document, filepath)
                return True
            from .ops.convert import is_convertible
            if filepath and is_convertible(filepath):
                GLib.idle_add(self.open_path, filepath)
                return True
        return False

    def _offer_merge_or_open(self, filepath):
        from .window_dialogs import _offer_merge_or_open as dialog
        return dialog(self, filepath)

    def _merge_pdf_at_position(self, source_pdf_path, insert_position):
        """Insert pages from another PDF file into the active document at insert_position."""
        old_count = self.doc.page_count
        result = self._change_pages(
            lambda:pdf_handler.merge_pdf_pages(self.doc,source_pdf_path,insert_position),
            lambda page:page+self.doc.page_count-old_count if page>=insert_position else page)
        success,message = result[:2]

        if success:
            self._clear_search()
            self.document_modified = True
            self.status_label.set_text(message)
            self._load_thumbnails()
            self._load_page(insert_position)
            self._update_ui_state()
        else:
            show_error_dialog(self, message, _("err_merge_title"))

    def on_thumbnail_drop(self, drop_target, value, x, y):
        """Handle drag-and-drop of a PDF onto the thumbnail sidebar to append pages."""
        if isinstance(value, Gio.File):
            filepath = value.get_path()
            if filepath and filepath.lower().endswith('.pdf'):
                insert_position = pdf_handler.get_page_count(self.doc)
                self._merge_pdf_at_position(filepath, insert_position)
                return True

        return False

    def on_open_clicked(self, button=None):
        """Prompt open file dialog to load an existing PDF into active or new tab."""
        filter_pdf = Gtk.FileFilter(name=_("filter_pdf"))
        filter_pdf.add_pattern("*.pdf")
        filter_pdf.add_mime_type("application/pdf")
        filter_all = Gtk.FileFilter(name=_("filter_all"))
        filter_all.add_pattern("*")
        from .ops.convert import OPENABLE_EXTENSIONS
        filter_supported = Gtk.FileFilter(name=_("filter_supported"))
        filter_supported.add_mime_type("application/pdf")
        for extension in ("pdf",) + OPENABLE_EXTENSIONS:
            filter_supported.add_pattern(f"*.{extension}")
            filter_supported.add_pattern(f"*.{extension.upper()}")

        def on_open_finish(file):
            if file:
                GLib.idle_add(self.open_path, file.get_path())

        show_open_file_dialog(
            self,
            _("open_pdf_title"),
            filters=[filter_supported, filter_pdf, filter_all],
            default_filter=filter_supported,
            callback=on_open_finish
        )

    def open_path(self, filepath):
        """Open a PDF, or convert another supported format into a new unsaved tab."""
        from .ops.convert import is_convertible
        if filepath and not filepath.lower().endswith('.pdf') and is_convertible(filepath):
            from .features.convert_ui import open_converted
            open_converted(self.features, [filepath])
        else:
            self.load_document(filepath)
        return False

    def on_save_clicked(self, button):
        """Save active document to current path or prompt Save As if untitled."""
        if not self.doc:
            return
        if self.current_file_path:
            self.commit_pending_format_change()
            self.save_document(self.current_file_path, incremental=False)
        else:
            self.on_save_as(None, None)

    def on_save_as(self, action, param):
        """Prompt save file dialog to write PDF to a new destination."""
        self.commit_pending_format_change()
        if not self.doc: return

        suggested = getattr(self._active_session, 'suggested_name', None)
        initial_name = os.path.basename(self.current_file_path or suggested or "edited_document.pdf")
        filter_pdf = Gtk.FileFilter(name=_("filter_pdf"))
        filter_pdf.add_pattern("*.pdf")
        filter_pdf.add_mime_type("application/pdf")

        def on_save_finish(file):
            if file:
                path = file.get_path()
                if not path.lower().endswith('.pdf'): path += '.pdf'
                self.save_document(path, incremental=False)

        show_save_file_dialog(
            self,
            _("save_as_title"),
            initial_name=initial_name,
            filters=[filter_pdf],
            default_filter=filter_pdf,
            callback=on_save_finish
        )

    def on_save_page_pdf(self, action=None, param=None):
        """Save the current page to a separate PDF file."""
        if not self.doc:
            return
        self.commit_pending_format_change()
        if self.inline_editor_widget is not None:
            self._apply_and_hide_editor(force_apply=True)
        doc = self.doc
        page_index = self.current_page_index
        source_path = self.current_file_path
        stem = os.path.splitext(os.path.basename(source_path or "document.pdf"))[0]
        filter_pdf = Gtk.FileFilter(name=_("filter_pdf"))
        filter_pdf.add_pattern("*.pdf")
        filter_pdf.add_mime_type("application/pdf")

        def on_save_finish(file):
            if not file or doc.is_closed:
                return
            path = file.get_path()
            if not path.lower().endswith('.pdf'):
                path += '.pdf'
            if source_path and os.path.realpath(path) == os.path.realpath(source_path):
                show_error_dialog(self, _("err_save_page_same_file"), _("menu_save_page_pdf"))
                return
            success, error = pdf_handler.save_page_as_pdf(doc, page_index, path)
            if success:
                self.status_label.set_text(_("status_page_pdf_saved", page_index + 1, os.path.basename(path)))
            else:
                show_error_dialog(self, error, _("menu_save_page_pdf"))

        show_save_file_dialog(
            self, _("menu_save_page_pdf"),
            initial_name=f"{stem}-page-{page_index + 1}.pdf",
            filters=[filter_pdf], default_filter=filter_pdf,
            callback=on_save_finish,
        )

    def on_document_properties(self, action=None, param=None):
        from .window_dialogs import on_document_properties as dialog
        return dialog(self, action, param)

    def on_bookmarks(self, action=None, param=None):
        from .window_dialogs import on_bookmarks as dialog
        return dialog(self, action, param)

    def on_add_table(self, action=None, param=None, cells=None):
        if (self.view_mode or not self.doc or not self.doc.page_count
                or not getattr(self._active_session, 'can_edit', True)):
            return
        doc, page_index = self.doc, self.current_page_index

        def insert(cells, **options):
            if self.doc is not doc or self.current_page_index != page_index:
                raise ValueError(_("table_document_changed"))
            objects = create_table_objects(doc[page_index], cells, **options)
            self._apply_and_hide_editor(force_apply=True)
            self.commit_pending_format_change()
            self.view_mode = False
            self.on_tool_selected(None, "select")
            command = AddTableCommand(self, objects)
            command.execute()
            self.undo_manager.add_command(command)
            self._select_table(TableSelection(objects))
            self._update_cursor_for_tool()
        TableDialog(self, insert, cells=cells).present()

    def on_paste_table(self, action=None, param=None):
        if (self.view_mode or not self.doc or not self.doc.page_count
                or not getattr(self._active_session, 'can_edit', True)):
            return
        from .table_data import parse_table
        doc, page_index = self.doc, self.current_page_index
        def received(clipboard, task):
            try:
                cells = parse_table(clipboard.read_text_finish(task) or '')
                if self.doc is not doc or self.current_page_index != page_index:
                    raise ValueError(_("table_document_changed"))
                self.on_add_table(cells=cells)
            except Exception as error:
                show_error_dialog(self, str(error), _("table_paste"))
        self.get_clipboard().read_text_async(None, received)

    def copy_table(self, table=None):
        from .table_data import selection_cells, to_tsv
        table = table or getattr(self, 'selected_table', None)
        if table and self.doc and getattr(self._active_session, 'can_copy', True):
            self.get_clipboard().set(to_tsv(selection_cells(table, self.doc[table.page_number])))

    def on_add_signature(self, action=None, param=None):
        if (self.view_mode or not self.doc or not self.doc.page_count
                or not getattr(self._active_session, 'can_edit', True)):
            return
        doc = self.doc

        def chosen(image_bytes):
            if self.doc is not doc:
                raise signatures.SignatureError(_("signature_document_changed"))
            self._apply_and_hide_editor(force_apply=True)
            self.view_mode = False
            self.on_tool_selected(None, "signature")
            self._pending_signature = (doc, image_bytes)
            self.status_label.set_text(_("signature_click_hint"))
            self._update_cursor_for_tool()
        VisibleSignatureDialog(self, chosen).present()

    def _place_visible_signature(self, x, y):
        pending = self._pending_signature
        if not pending or pending[0] is not self.doc:
            show_error_dialog(self, _("signature_document_changed"), _("signature_add"))
            return
        try:
            rect, rotation = signatures.signature_placement(self.doc[self.current_page_index], pending[1], x, y)
            image = EditableImage(bbox=rect, page_number=self.current_page_index,
                                  xref=None, image_bytes=pending[1], is_new=True, rotation=rotation)
            command = AddObjectCommand(self, image)
            command.execute()
            self.undo_manager.add_command(command)
            self.selected_image = image
            self.selected_text = self.selected_shape = self.selected_stroke = None
            self._pending_signature = None
            self.tool_mode = "select"
            self._update_ui_state()
            self._update_cursor_for_tool()
        except Exception as error:
            show_error_dialog(self, str(error), _("signature_add"))

    def on_sign_certificate(self, action=None, param=None, field_name=None):
        if self.view_mode or not self.doc or not self.doc.page_count:
            return
        if not (getattr(self._active_session, 'can_edit', True) and
                getattr(self._active_session, 'can_copy', True)):
            return
        if not signatures.certificate_signing_available():
            show_error_dialog(self, _("signature_dependency"), _("signature_digital"))
            return
        try:
            self._apply_and_hide_editor(force_apply=True)
            self.commit_pending_format_change()
            source = self.current_file_path
            data = signatures.prepare_signing_pdf(self.doc, source, self.document_modified)
            fields = document_features.list_form_fields(self.doc)
            unsigned = list(dict.fromkeys(field['name'] for field in fields
                            if field['type'] == fitz.PDF_WIDGET_TYPE_SIGNATURE and not field['signed']))
            session = self._active_session

            def saved(path):
                if self._active_session is session:
                    self.load_document(path,in_new_tab=True)
                    self.status_label.set_text(_("signature_saved", path))
                from .dialogs import message
                message(self, _("signature_saved", os.path.basename(path)), path)
            dialog=CertificateSignatureDialog(self, data, source, unsigned, bool(self.doc.needs_pass), saved)
            if field_name in unsigned:dialog.field.set_selected(unsigned.index(field_name)+1)
            dialog.present()
        except Exception as error:
            show_error_dialog(self, str(error), _("signature_digital"))

    def on_find_replace(self, action=None, param=None):
        if not self.doc or not self.document_tools.editable():
            return
        from .find_replace import FindReplaceDialog
        initial = self.search_entry.get_text() if self.search_revealer.get_reveal_child() else ''
        FindReplaceDialog(self, initial).present(self)

    def on_form_fields(self, action=None, param=None):
        """Open the forms sidebar; on a page without fields, Add fields is ready for building."""
        if self.doc and self.form_tools.editable():
            self.form_tools.show()
            from .document_features import list_form_fields
            if not list_form_fields(self.doc, [self.current_page_index]):
                self.form_tools.builder.open()

    def _document_export_filter(self, extension, mime):
        file_filter = Gtk.FileFilter(name=f"{extension.upper()} (*.{extension})")
        file_filter.add_pattern(f"*.{extension}")
        file_filter.add_mime_type(mime)
        return file_filter

    def on_export_page_visual(self, image_format):
        if not self.doc:
            return
        doc = self.doc
        page_index = self.current_page_index
        title_key = "menu_export_page_png" if image_format == 'png' else "menu_export_page_svg"
        mime = "image/png" if image_format == 'png' else "image/svg+xml"
        file_filter = self._document_export_filter(image_format, mime)
        stem = os.path.splitext(os.path.basename(self.current_file_path or "document.pdf"))[0]

        def save(file):
            if not file or doc.is_closed:
                return
            path = file.get_path()
            if not path.lower().endswith(f'.{image_format}'):
                path += f'.{image_format}'
            try:
                document_features.export_page_visual(doc, page_index, path, image_format)
                self.status_label.set_text(_("page_visual_exported", page_index + 1, os.path.basename(path)))
            except Exception as error:
                show_error_dialog(self, str(error), _(title_key))

        show_save_file_dialog(self, _(title_key),
                              initial_name=f"{stem}-page-{page_index + 1}.{image_format}",
                              filters=[file_filter], default_filter=file_filter, callback=save)

    def on_export_range(self, action=None, param=None):
        if not self.doc:
            return
        doc = self.doc
        from .dialogs import OperationDialog

        def export(dialog):
            if doc is not self.doc:
                return
            first, last = first_spin.get_value_as_int(), last_spin.get_value_as_int()
            if first > last:
                raise ValueError(_("invalid_page_range"))
            stem = os.path.splitext(os.path.basename(self.current_file_path or "document.pdf"))[0]
            file_filter = self._document_export_filter("pdf", "application/pdf")
            def save(file):
                if file and not doc.is_closed:
                    path = file.get_path()
                    if not path.lower().endswith('.pdf'):
                        path += '.pdf'
                    try:
                        document_features.export_page_range(doc, first - 1, last - 1, path)
                        self.status_label.set_text(_("range_exported", first, last))
                    except Exception as error:
                        show_error_dialog(self, str(error), _("menu_export_range"))
            show_save_file_dialog(self, _("menu_export_range"),
                                  initial_name=f"{stem}-pages-{first}-{last}.pdf",
                                  filters=[file_filter], default_filter=file_filter, callback=save)

        dialog = OperationDialog(self, _("menu_export_range"), _("btn_save"), width=420, on_apply=export)
        group = dialog.group(_("range_pages"))
        first_spin = dialog.spin(group, _("range_from"), 1, 1, doc.page_count)
        last_spin = dialog.spin(group, _("range_to"), doc.page_count, 1, doc.page_count)
        dialog.show()

    def _export_archive(self, title_key, suffix, exporter):
        if not self.doc:
            return
        doc = self.doc
        stem = os.path.splitext(os.path.basename(self.current_file_path or "document.pdf"))[0]
        file_filter = self._document_export_filter("zip", "application/zip")

        def save(file):
            if not file or doc.is_closed:
                return
            path = file.get_path()
            if not path.lower().endswith('.zip'):
                path += '.zip'
            try:
                count = exporter(doc, path)
                self.status_label.set_text(_("items_exported", count) if count else _("no_items_found"))
            except Exception as error:
                show_error_dialog(self, str(error), _(title_key))

        show_save_file_dialog(self, _(title_key), initial_name=f"{stem}-{suffix}.zip",
                              filters=[file_filter], default_filter=file_filter, callback=save)

    def on_extract_images(self, action=None, param=None):
        self._export_archive("menu_extract_images", "images", document_features.export_images_zip)

    def on_extract_tables(self, action=None, param=None):
        self._export_archive("menu_extract_tables", "tables", document_features.export_tables_zip)

    def on_export_as(self, action=None, param=None):
        """Show modern export dialog with format and layout mode options."""
        self.show_export_dialog(initial_format="DOCX")

    def on_export_docx(self, action=None, param=None):
        """Direct export to DOCX: prompt destination directly, using pdf2docx layout analysis."""
        self._prompt_export_destination("DOCX", mode="standard")

    def on_export_txt(self, action=None, param=None):
        """Direct export to TXT: prompt destination directly."""
        self._prompt_export_destination("TXT", mode="standard")

    def show_export_dialog(self, initial_format="DOCX"):
        """Show modern Libadwaita ExportDialog for document export settings."""
        if not self.doc:
            return
        from .export_dialog import ExportDialog
        dialog = ExportDialog(
            parent_window=self,
            initial_format=initial_format,
            initial_mode="standard",
            on_confirm_callback=self._on_export_dialog_confirmed
        )
        dialog.present()

    def _on_export_dialog_confirmed(self, format_name, mode):
        """Handle confirmed format and mode selection from ExportDialog."""
        self._prompt_export_destination(format_name, mode=mode)

    def _prompt_export_destination(self, format_name, mode="standard"):
        """Prompt file save dialog for chosen format and mode, then execute export."""
        if not self.doc:
            return

        base_name = Path(self.current_file_path).stem if self.current_file_path else "document"
        fmt_upper = str(format_name).upper().strip()
        fmt_lower = fmt_upper.lower()

        export_filters = {
            "DOCX": (_("filter_word"), "*.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "TXT": (_("filter_txt"), "*.txt", "text/plain"),
        }

        filter_list = []
        default_ff = None
        for name, (pattern_name, pattern, mime) in export_filters.items():
            ff = Gtk.FileFilter()
            ff.set_name(f"{name} - {pattern_name}")
            ff.add_pattern(pattern)
            if mime:
                ff.add_mime_type(mime)
            filter_list.append(ff)
            if name == fmt_upper:
                default_ff = ff

        def on_export_finish(file, selected_filter=None):
            if not file:
                return
            path = file.get_path()
            if not path:
                return
            self._execute_export(fmt_upper, path, mode=mode)

        show_save_file_dialog(
            self,
            _("export_as_title"),
            initial_name=f"{base_name}.{fmt_lower}",
            filters=filter_list,
            default_filter=default_ff,
            callback=on_export_finish
        )

    def _execute_export(self, format_name, output_path, mode="standard", on_finish=None):
        """Execute export asynchronously in a background worker thread."""
        if not self.doc:
            if on_finish and callable(on_finish):
                on_finish(False, _("err_no_doc_msg"))
            return

        fmt_upper = str(format_name).upper().strip()
        fmt_lower = fmt_upper.lower()

        if not output_path.lower().endswith(f".{fmt_lower}"):
            output_path = f"{output_path}.{fmt_lower}"

        self.status_label.set_text(_("status_exporting", fmt_upper))

        try:
            pdf_bytes = self.doc.tobytes(garbage=0, clean=False, deflate=True)
        except Exception:
            try:
                pdf_bytes = self.doc.tobytes()
            except Exception:
                pdf_bytes = None

        doc_payload = pdf_bytes if pdf_bytes is not None else self.doc
        src_path = self.current_file_path

        def worker():
            success = False
            error_msg = _("err_unknown_export_format")
            try:
                if fmt_lower == "docx":
                    success, error_msg = pdf_handler.export_pdf_as_docx(
                        doc_payload, src_path, output_path, mode=mode
                    )
                elif fmt_lower == "txt":
                    success, error_msg = pdf_handler.export_pdf_as_text(
                        doc_payload, output_path, mode=mode
                    )
                elif fmt_lower == "pdf":
                    success, error_msg = pdf_handler.save_document(
                        self.doc, output_path, incremental=False
                    )
                else:
                    success, error_msg = False, _("err_unknown_export_format")
            except Exception as e:
                success = False
                error_msg = str(e)

            GLib.idle_add(self._on_export_finished, success, error_msg, fmt_upper, output_path, on_finish)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _on_export_finished(self, success, error_msg, format_name, output_path, on_finish=None):
        """Handle export completion on the main GTK thread."""
        try:
            if success:
                self.status_label.set_text(_("status_exported", format_name, os.path.basename(output_path)))
                if format_name.lower() == "pdf":
                    self._update_ui_state()
            else:
                self.status_label.set_text(_("status_export_failed"))
                show_error_dialog(self, _("err_export_failed_msg", error_msg or _("status_export_failed")))
        finally:
            if on_finish and callable(on_finish):
                try:
                    on_finish(success, error_msg)
                except Exception:
                    logger.exception("Completion callback failed")
        return False

    def on_print_activated(self, action, param):
        """Open the pdfLX print dialog for the current document."""
        if not self.doc:
            show_error_dialog(self, _("print_no_doc"), _("print_no_doc_title"))
            return
        
        from .print_dialog import show_print_dialog
        show_print_dialog(self, self.doc)

    def _update_cursor_for_tool(self):
        """Restore cursor based on active tool mode."""
        if self.tool_mode == 'erase_highlight':
            self.pdf_view.set_cursor(highlight_tools.eraser_cursor())
        elif self.tool_mode == 'highlighter':
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode == 'drag':
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name('grab'))
        elif getattr(self, 'view_mode', False):
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("text"))
        elif self.tool_mode == "select":
            self.pdf_view.set_cursor(None)
        elif self.tool_mode in ("add_text", "add_ellipse", "add_rectangle", "add_checkmark", "add_cross", "pen", "highlighter",
                                "add_shape", "add_line", "erase_highlight"):
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        elif self.tool_mode in ("add_image", "signature"):
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("cell"))
        elif self.tool_mode == "drag":
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("move"))
        else:
            self.pdf_view.set_cursor(None)

    def _on_pointer_motion(self, controller, x, y):
        """Track last pointer position on pdf_view for focal zoom and update handle cursor."""
        self._last_pointer_pos = (x, y)
        interaction=getattr(getattr(self,'form_tools',None),'interaction',None)
        if self.tool_mode=='select' and interaction:
            page_x=(x-max(0,(self.pdf_view.get_width()-self.current_pdf_page_width)/2))/self.zoom_level
            page_y=(y-max(0,(self.pdf_view.get_height()-self.current_pdf_page_height)/2))/self.zoom_level
            handle=(interaction.drag or {}).get('handle') or interaction.handle_at(page_x,page_y)
            if handle:
                cursor='move' if handle=='move' else 'nwse-resize' if handle in ('nw','se') else 'nesw-resize' if handle in ('ne','sw') else 'ns-resize' if handle in ('n','s') else 'ew-resize'
                self.pdf_view.set_cursor(Gdk.Cursor.new_from_name(cursor));return
        if self.tool_mode == 'drag':
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name('grabbing' if getattr(self, '_pan_start', None) else 'grab'))
            return
        if self.tool_mode == 'erase_highlight':
            self.pdf_view.set_cursor(highlight_tools.eraser_cursor())
            return
        if hasattr(self,'stamp_interaction') and self.doc and self.tool_mode in ('select','stamp'):
            page_x=(x-max(0,(self.pdf_view.get_width()-self.current_pdf_page_width)/2))/self.zoom_level
            page_y=(y-max(0,(self.pdf_view.get_height()-self.current_pdf_page_height)/2))/self.zoom_level
            handle=(self.stamp_interaction.drag or {}).get('handle') or self.stamp_interaction.handle_at(page_x,page_y)
            if handle:
                cursor='crosshair' if handle=='rotate' else ('nwse-resize' if handle in ('nw','se') else 'nesw-resize')
                self.pdf_view.set_cursor(Gdk.Cursor.new_from_name(cursor))
                return
            if self.stamp_interaction.drag or self.stamp_interaction.hit_note(page_x,page_y):
                self.pdf_view.set_cursor(Gdk.Cursor.new_from_name('grabbing' if self.stamp_interaction.drag else 'grab'))
                return
            if self.view_mode:
                self._update_cursor_for_tool()
        if getattr(self, 'table_drag_state', None) is not None:
            # Keep the gesture's cursor while a table is moved or resized.
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name(
                self._handle_cursor_name(self.table_resize_handle) if self.table_resize_handle else 'move'))
            return
        if not getattr(self, 'view_mode', False) and not getattr(self, 'dragged_object', None):
            selected_obj = getattr(self, 'selected_table', None) or self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
            if selected_obj:
                handle = self._find_resize_handle_at_pos(x, y, selected_obj)
                if handle == "rotate":
                    self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
                    return
                elif handle in ("nw", "se"):
                    self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("nwse-resize"))
                    return
                elif handle in ("ne", "sw"):
                    self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("nesw-resize"))
                    return
                elif handle in ("n", "s"):
                    self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("ns-resize"))
                    return
                elif handle in ("w", "e"):
                    self.pdf_view.set_cursor(Gdk.Cursor.new_from_name("ew-resize"))
                    return
            if self.tool_mode == 'select' and self.inline_editor_widget is None:
                page_x = (x - max(0, (self.pdf_view.get_width() - self.current_pdf_page_width) / 2)) / self.zoom_level
                page_y = (y - max(0, (self.pdf_view.get_height() - self.current_pdf_page_height) / 2)) / self.zoom_level
                if self._find_table_at_pos(page_x, page_y):
                    self.pdf_view.set_cursor(Gdk.Cursor.new_from_name('move'))
                    return
            self._update_cursor_for_tool()

    @staticmethod
    def _handle_cursor_name(handle):
        return {'nw': 'nwse-resize', 'se': 'nwse-resize', 'ne': 'nesw-resize', 'sw': 'nesw-resize',
                'n': 'ns-resize', 's': 'ns-resize', 'w': 'ew-resize', 'e': 'ew-resize'}.get(handle, 'move')

    def _update_inline_editor_position(self):
        """Align the editable text with its original PDF bounding box."""
        if not getattr(self, 'inline_editor_widget', None) or not getattr(self, 'inline_editor_text_obj', None):
            return
        text_obj = self.inline_editor_text_obj
        font_px = max(1, text_obj.font_size * self.zoom_level)
        font = Pango.FontDescription.from_string(text_obj.font_family_base or "Sans")
        font.set_absolute_size(int(font_px * Pango.SCALE))
        if text_obj.is_bold:
            font.set_weight(Pango.Weight.BOLD)
        if text_obj.is_italic:
            font.set_style(Pango.Style.ITALIC)
        if self._inline_editor_font_zoom != self.zoom_level:
            # Use the family Pango parsed (e.g. "Times Roman" -> "Times") so the
            # TextView renders with the same font the layout below measures.
            family = (font.get_family() or "Sans").replace("\\", "\\\\").replace('"', '\\"')
            red, green, blue = (round(max(0, min(1, part)) * 255) for part in text_obj.color)
            self._inline_editor_font_provider.load_from_data(
                f'textview.inline-editor-tv {{ font-family: "{family}"; '
                f'font-size: {font_px:.1f}px; '
                f'font-weight: {"bold" if text_obj.is_bold else "normal"}; '
                f'font-style: {"italic" if text_obj.is_italic else "normal"}; '
                f'color: rgb({red}, {green}, {blue}); }} '
                f'textview.inline-editor-tv text {{ color: rgb({red}, {green}, {blue}); }}'.encode()
            )
            self._inline_editor_font_zoom = self.zoom_level
        da_w = max(self.pdf_view.get_allocated_width(), self.current_pdf_page_width)
        da_h = max(self.pdf_view.get_allocated_height(), self.current_pdf_page_height)
        h_adj = self.pdf_scroll.get_hadjustment()
        visible_left = h_adj.get_value() if h_adj else 0
        visible_right = visible_left + (h_adj.get_page_size() if h_adj and h_adj.get_page_size() > 0 else da_w)
        page_offset_x = max(0, (da_w - self.current_pdf_page_width) / 2)
        page_offset_y = max(0, (da_h - self.current_pdf_page_height) / 2)
        if text_obj.bbox:
            page = self.doc[self.current_page_index]
            vis_rect = (fitz.Rect(text_obj.bbox) * page.rotation_matrix).normalize()
            anchor_x = page_offset_x + vis_rect.x0 * self.zoom_level
            anchor_top = page_offset_y + vis_rect.y0 * self.zoom_level
            anchor_bottom = page_offset_y + vis_rect.y1 * self.zoom_level
            source_width = vis_rect.width * self.zoom_level
        else:
            anchor_x = page_offset_x + text_obj.x * self.zoom_level
            anchor_top = page_offset_y + text_obj.y * self.zoom_level
            anchor_bottom = anchor_top + text_obj.font_size * self.zoom_level
            source_width = 0

        buffer = self.inline_editor_tv.get_buffer()
        content = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        ed_x = int(anchor_x)
        ed_y = int(anchor_top)
        max_width = max(40, min(visible_right - ed_x - 4,
                                page_offset_x + self.current_pdf_page_width - ed_x - 4))
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        layout = PangoCairo.create_layout(cairo.Context(surface))
        layout.set_font_description(font)
        layout.set_text(content or " ", -1)
        natural_width, _ = layout.get_pixel_size()
        ed_w = int(min(max_width, max(source_width + 6, natural_width + 5, 40)))
        layout.set_width(max(1, ed_w - 4) * Pango.SCALE)
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
        _, text_height = layout.get_pixel_size()
        page = self.doc[self.current_page_index]
        baseline = getattr(text_obj, 'baseline', None)
        if baseline is not None and not page.rotation and not getattr(text_obj, 'rotation', 0):
            # Put the editor's first baseline on the PDF baseline; the bbox top
            # sits higher than the TextView's ascent and made the text jump.
            ed_y = int(round(page_offset_y + baseline * self.zoom_level
                             - layout.get_baseline() / Pango.SCALE))
        ed_h = int(max(anchor_bottom - ed_y, text_height + 2, font_px * 1.2))
        self.inline_editor_widget.set_margin_start(ed_x)
        self.inline_editor_widget.set_margin_top(ed_y)
        self.inline_editor_widget.set_size_request(ed_w, ed_h)
        visual=fitz.Rect((ed_x-page_offset_x)/self.zoom_level,
                         (ed_y-page_offset_y)/self.zoom_level,
                         (ed_x+ed_w-page_offset_x)/self.zoom_level,
                         (ed_y+ed_h-page_offset_y)/self.zoom_level)
        self._inline_editor_bounds=tuple(visual*self.doc[self.current_page_index].derotation_matrix)
        self._inline_editor_wrapped_text=None
        if natural_width>ed_w-4:
            encoded=content.encode('utf-8')
            self._inline_editor_wrapped_text='\n'.join(
                encoded[line.start_index:line.start_index+line.length].decode('utf-8')
                for line in layout.get_lines_readonly())

    def _set_zoom(self, new_zoom, focal_point=None, preserve_fit=False):
        """Set zoom level smoothly with focal point anchoring, without reloading page."""
        if not self.doc or not (0 <= self.current_page_index < pdf_handler.get_page_count(self.doc)):
            return

        if not preserve_fit:
            self._active_session.fit_to_view = False
            self._active_session.fit_on_load = False
        clamped_zoom = max(0.1, min(8.0, new_zoom))
        if abs(clamped_zoom - self.zoom_level) < 0.001:
            return

        if self.continuous_view.enabled:
            self.continuous_view.zoom(clamped_zoom,focal_point)
            return
        old_zoom = self.zoom_level
        self.zoom_level = clamped_zoom
        self.zoom_label.set_text(f"{int(self.zoom_level * 100)}%")

        page = self.doc.load_page(self.current_page_index)
        new_page_w = int(page.rect.width * self.zoom_level)
        new_page_h = int(page.rect.height * self.zoom_level)

        h_adj = self.pdf_scroll.get_hadjustment()
        v_adj = self.pdf_scroll.get_vadjustment()

        # Viewport dimensions
        vp_w = h_adj.get_page_size() if h_adj and h_adj.get_page_size() > 0 else float(self.pdf_scroll.get_allocated_width())
        vp_h = v_adj.get_page_size() if v_adj and v_adj.get_page_size() > 0 else float(self.pdf_scroll.get_allocated_height())

        scroll_x = h_adj.get_value() if h_adj else 0.0
        scroll_y = v_adj.get_value() if v_adj else 0.0

        # Current allocated drawing area dimensions
        da_w = max(self.pdf_view.get_allocated_width(), self.current_pdf_page_width)
        da_h = max(self.pdf_view.get_allocated_height(), self.current_pdf_page_height)
        page_offset_x = max(0.0, (da_w - self.current_pdf_page_width) / 2.0)
        page_offset_y = max(0.0, (da_h - self.current_pdf_page_height) / 2.0)

        # Determine focal anchor on the PDF page and screen viewport
        if focal_point is not None:
            focus_x, focus_y = focal_point
            doc_x = (focus_x - page_offset_x) / old_zoom
            doc_y = (focus_y - page_offset_y) / old_zoom
            vp_x = focus_x - scroll_x
            vp_y = focus_y - scroll_y
        else:
            center_x = scroll_x + (vp_w / 2.0)
            center_y = scroll_y + (vp_h / 2.0)
            doc_x = (center_x - page_offset_x) / old_zoom
            doc_y = (center_y - page_offset_y) / old_zoom
            vp_x = vp_w / 2.0
            vp_y = vp_h / 2.0

        # Update content dimensions
        self.current_pdf_page_width = new_page_w
        self.current_pdf_page_height = new_page_h
        self.pdf_view.set_content_width(new_page_w)
        self.pdf_view.set_content_height(new_page_h)

        # Reposition active inline editor if open
        self._update_inline_editor_position()

        # Calculate new offsets for new page size vs viewport
        new_da_w = max(new_page_w, int(vp_w))
        new_da_h = max(new_page_h, int(vp_h))
        new_offset_x = max(0.0, (new_da_w - new_page_w) / 2.0)
        new_offset_y = max(0.0, (new_da_h - new_page_h) / 2.0)

        new_focus_x = new_offset_x + (doc_x * self.zoom_level)
        new_focus_y = new_offset_y + (doc_y * self.zoom_level)

        new_scroll_x = new_focus_x - vp_x
        new_scroll_y = new_focus_y - vp_y

        def _apply_scroll():
            if h_adj:
                max_x = max(0.0, h_adj.get_upper() - h_adj.get_page_size())
                h_adj.set_value(max(0.0, min(new_scroll_x, max_x)))
            if v_adj:
                max_y = max(0.0, v_adj.get_upper() - v_adj.get_page_size())
                v_adj.set_value(max(0.0, min(new_scroll_y, max_y)))
            return False

        _apply_scroll()
        GLib.idle_add(_apply_scroll)

        self.pdf_view.queue_draw()

    def _on_fit_canvas_resize(self,widget,width,height):
        session=self._active_session
        if session and session.doc and session.fit_to_view:
            viewport=(self.pdf_scroll.get_width(),self.pdf_scroll.get_height())
            if viewport!=getattr(session,'_fit_viewport',None):
                self._schedule_fit_document(session)

    def _schedule_fit_document(self,session):
        """Fit a newly loaded page after the canvas has received its allocation."""
        if getattr(self,'_fit_session',None) is session:
            return
        self._fit_session=session
        allocations=0
        def fit_after_layout():
            nonlocal allocations
            if self._active_session is not session or not session.doc or session.doc.is_closed or not session.fit_to_view:
                if self._fit_session is session:
                    self._fit_session=None
                return GLib.SOURCE_REMOVE
            width=self.pdf_scroll.get_width()
            height=self.pdf_scroll.get_height()
            if not self.pdf_scroll.get_mapped() or width<=32 or height<=32:
                return GLib.SOURCE_CONTINUE
            allocations+=1
            if allocations<2:
                return GLib.SOURCE_CONTINUE
            page=session.doc[session.current_page_index]
            zoom=min((width-32)/page.rect.width,(height-32)/page.rect.height)
            session._fit_viewport=(width,height)
            self._set_zoom(zoom,preserve_fit=True)
            session.fit_on_load=False
            if self._fit_session is session:
                self._fit_session=None
            self.pdf_scroll.get_hadjustment().set_value(0)
            if self.continuous_view.enabled:
                self.continuous_view.goto(session.current_page_index)
            else:
                self.pdf_scroll.get_vadjustment().set_value(0)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(30,fit_after_layout)

    def on_zoom_in(self, button=None, focal_point=None):
        if not self.doc: return
        self._set_zoom(self.zoom_level * 1.2, focal_point=focal_point)

    def on_zoom_out(self, button=None, focal_point=None):
        if not self.doc: return
        self._set_zoom(self.zoom_level / 1.2, focal_point=focal_point)

    def on_scroll_zoom(self, controller, dx, dy):
        """Scroll within a page, turn at its edge, or zoom with Ctrl+wheel."""
        if not self.doc or not dy:
            return False
        if controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            # Small touchpad deltas give small zoom changes; wheel notches give 20%.
            delta=dy
            if hasattr(controller,'get_unit') and controller.get_unit()==Gdk.ScrollUnit.SURFACE:
                delta=dy/100
            self._set_zoom(self.zoom_level * 1.2 ** max(-4,min(4,-delta)),
                           focal_point=getattr(self, '_last_pointer_pos', None))
            return True
        if abs(dx)>abs(dy):
            return False
        if self.continuous_view.enabled:
            return False
        adjustment=self.pdf_scroll.get_vadjustment()
        bottom=max(adjustment.get_lower(),adjustment.get_upper()-adjustment.get_page_size())
        at_edge=(dy>0 and adjustment.get_value()>=bottom-1) or (
            dy<0 and adjustment.get_value()<=adjustment.get_lower()+1)
        if not at_edge:
            self._page_scroll_delta=0
            return False  # GtkScrolledWindow provides smooth scrolling within the page.
        now=GLib.get_monotonic_time()
        direction=1 if dy>0 else -1
        previous=getattr(self,'_page_scroll_direction',0)
        if direction!=previous or now-getattr(self,'_page_scroll_time',0)>350000:
            self._page_scroll_delta=0
        self._page_scroll_direction=direction
        self._page_scroll_time=now
        self._page_scroll_delta=getattr(self,'_page_scroll_delta',0)+dy
        if abs(self._page_scroll_delta)<1:
            return True
        if direction==previous and now-getattr(self,'_last_page_scroll_turn',0)<180000:
            self._page_scroll_delta=0
            return True
        target=self.current_page_index+direction
        self._page_scroll_delta=0
        if not 0<=target<self.doc.page_count:
            return True
        self._last_page_scroll_turn=now
        source=self.doc
        self._apply_and_hide_editor()
        self._load_page(target)
        def position_page():
            if self.doc is source and self.current_page_index==target:
                adjustment=self.pdf_scroll.get_vadjustment()
                value=adjustment.get_lower() if direction>0 else max(
                    adjustment.get_lower(),adjustment.get_upper()-adjustment.get_page_size())
                adjustment.set_value(value)
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(60,position_page)
        return True

    def _toggle_fullscreen(self):
        """Show just the document, keeping the normal layout intact for return."""
        if getattr(self,'_document_only_view',False):
            self._exit_document_view()
            return
        if not self.doc:
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()
            return
        self._apply_and_hide_editor()
        self.document_tools.close_note_bubble()
        self._document_view_parent=self.pdf_scroll.get_parent()
        self._document_view_previous=self.pdf_scroll.get_prev_sibling()
        if isinstance(self._document_view_parent,Gtk.Overlay):
            self._document_view_parent.set_child(None)
        else:self._document_view_parent.remove(self.pdf_scroll)
        self.document_only_box.append(self.pdf_scroll)
        self._document_only_view=True
        self.document_view_stack.set_visible_child_name('document')
        self.fullscreen()
        self.pdf_view.grab_focus()

    def _exit_document_view(self,unfullscreen=True):
        if getattr(self,'_document_only_view',False):
            self._document_only_view=False
            self.document_only_box.remove(self.pdf_scroll)
            if isinstance(self._document_view_parent,Gtk.Overlay):
                self._document_view_parent.set_child(self.pdf_scroll)
            else:self._document_view_parent.insert_child_after(self.pdf_scroll,self._document_view_previous)
            self.document_view_stack.set_visible_child_name('normal')
            self.pdf_view.grab_focus()
        if unfullscreen:
            self.unfullscreen()

    def _on_reader_fullscreen_changed(self,window,param):
        if not self.is_fullscreen() and getattr(self,'_document_only_view',False):
            self._exit_document_view(unfullscreen=False)

    def on_prev_page(self, button):
        if self.doc and self.current_page_index > 0:
            self._load_page(self.current_page_index - 1)

    def on_next_page(self, button):
        if self.doc and self.current_page_index < pdf_handler.get_page_count(self.doc) - 1:
            self._load_page(self.current_page_index + 1)

    def on_add_page(self, button):
        """Insert a new blank page with matching dimensions after current page."""
        if self.view_mode or not getattr(self._active_session,'can_edit',True):
            return
        if not self.doc:
            show_error_dialog(self, _("err_no_doc_msg"), _("err_no_doc_title"))
            return

        current_page = self.doc.load_page(self.current_page_index)
        page_width = current_page.rect.width
        page_height = current_page.rect.height
        insert_position = self.current_page_index + 1
        success, message = self._change_pages(
            lambda:pdf_handler.insert_blank_page(self.doc,insert_position,page_width,page_height),
            lambda page:page+1 if page>=insert_position else page)

        if success:
            self._clear_search()
            self.document_modified = True
            self.status_label.set_text(message)
            self._load_thumbnails()
            self._load_page(insert_position)
            self._update_ui_state()
        else:
            show_error_dialog(self, message, _("err_add_page_title"))

    def on_duplicate_page(self, button):
        """Duplicate the current page and select the independent copy."""
        if not self.doc or self.view_mode:
            return
        self.commit_pending_format_change()
        if self.inline_editor_widget is not None:
            self._apply_and_hide_editor(force_apply=True)
        insert_position = self.current_page_index + 1
        current_page = self.current_page_index
        success, message = self._change_pages(
            lambda:pdf_handler.duplicate_page(self.doc,current_page),
            lambda page:page+1 if page>=insert_position else page)
        if success:
            self._clear_search()
            self.document_modified = True
            self._load_thumbnails()
            self._load_page(insert_position)
            self.status_label.set_text(message)
            self._update_ui_state()
        else:
            show_error_dialog(self, message, _("err_duplicate_page_title"))

    def on_delete_page(self, button):
        self._delete_page_at_index(self.current_page_index)

    def _delete_page_at_index(self, page_to_delete):
        """Delete the specified page index with confirmation."""
        if self.view_mode or not getattr(self._active_session,'can_edit',True):
            return
        if not self.doc:
            show_error_dialog(self, _("err_no_doc_open_msg"), _("err_no_doc_title"))
            return

        page_count = pdf_handler.get_page_count(self.doc)
        if page_count <= 1:
            show_error_dialog(self, _("err_cannot_delete_last_page"), _("err_cannot_delete_last_page_title"))
            return

        confirmed = show_confirm_dialog(
            self,
            _("delete_page_warn_msg", page_to_delete + 1),
            _("delete_page_warn_title"),
            destructive=True
        )

        if not confirmed:
            self.status_label.set_text("Sayfa silme iptal edildi.")
            return

        success, message = self._change_pages(
            lambda:pdf_handler.delete_page(self.doc,page_to_delete),
            lambda page:None if page==page_to_delete else page-1 if page>page_to_delete else page)

        if success:
            self._clear_search()
            self.document_modified = True
            self.status_label.set_text(message)
            new_page_count = pdf_handler.get_page_count(self.doc)
            new_page_index = min(page_to_delete, new_page_count - 1)
            self._load_thumbnails()
            self._load_page(new_page_index)
            self._update_ui_state()
        else:
            show_error_dialog(self, message, _("err_delete_page_title"))

    def rotate_current_page(self, angle_delta, page_index=None):
        """Rotate the specified or current page by angle_delta degrees (+90 or -90)."""
        if not self.doc or self.view_mode or not getattr(self._active_session, 'can_edit', True):
            return
        target_idx = self.current_page_index if page_index is None else page_index
        if not (0 <= target_idx < pdf_handler.get_page_count(self.doc)):
            return
        from .undo_manager import RotatePageCommand
        cmd = RotatePageCommand(self, target_idx, angle_delta)
        cmd.execute()
        self.undo_manager.add_command(cmd)
        msg_key = "status_page_rotated_cw" if angle_delta > 0 else "status_page_rotated_ccw"
        self.status_label.set_text(_(msg_key, target_idx + 1))

    def show_thumbnail_context_menu(self, parent_widget, x, y, page_index):
        """Show context menu for a page thumbnail with rotation and deletion options."""
        if not self.doc or self.view_mode or not getattr(self._active_session, 'can_edit', True):
            return

        if hasattr(self, '_thumb_context_popover') and self._thumb_context_popover:
            self._thumb_context_popover.popdown()
            self._thumb_context_popover.unparent()
            self._thumb_context_popover = None

        popover = Gtk.Popover(autohide=True, has_arrow=True)
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        popover_box.set_margin_start(6)
        popover_box.set_margin_end(6)
        popover_box.set_margin_top(6)
        popover_box.set_margin_bottom(6)

        btn_cw = Gtk.Button(label=_("rotate_cw"))
        btn_cw.add_css_class("flat")
        btn_cw.set_tooltip_text("Ctrl+Shift+R")
        def on_cw(b):
            popover.popdown()
            self.rotate_current_page(90, page_index=page_index)
        btn_cw.connect("clicked", on_cw)
        popover_box.append(btn_cw)

        btn_ccw = Gtk.Button(label=_("rotate_ccw"))
        btn_ccw.add_css_class("flat")
        btn_ccw.set_tooltip_text("Ctrl+Shift+L")
        def on_ccw(b):
            popover.popdown()
            self.rotate_current_page(-90, page_index=page_index)
        btn_ccw.connect("clicked", on_ccw)
        popover_box.append(btn_ccw)

        in_edit = not getattr(self, 'view_mode', False)
        page_count = pdf_handler.get_page_count(self.doc) if self.doc else 0
        if in_edit and page_count > 1:
            popover_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            btn_del = Gtk.Button(label=_("delete_page_title"))
            btn_del.add_css_class("flat")
            btn_del.add_css_class("destructive-action")
            def on_del(b):
                popover.popdown()
                self._delete_page_at_index(page_index)
            btn_del.connect("clicked", on_del)
            popover_box.append(btn_del)

        popover.set_child(popover_box)
        popover.set_parent(parent_widget)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        self._thumb_context_popover = popover
        popover.popup()

    def _on_page_slider_changed(self, scale):
        """Navigate to page when slider is dragged."""
        if getattr(self, '_updating_page_slider', False):
            return
        if not self.doc:
            return
        target_page = int(round(scale.get_value())) - 1
        page_count = pdf_handler.get_page_count(self.doc)
        if 0 <= target_page < page_count and target_page != self.current_page_index:
            self._load_page(target_page)

    def update_page_label(self):
        """Update the page indicator label with current page number and total count, and sync slider."""
        count = pdf_handler.get_page_count(self.doc) if self.doc else 0
        self.page_label.set_text(_("page_info_count").format(self.current_page_index + 1, count) if count > 0 else _("page_info_count").format(0, 0))
        if hasattr(self, 'page_slider'):
            if count > 1:
                self._updating_page_slider = True
                self.page_slider.set_range(1, count)
                self.page_slider.set_value(self.current_page_index + 1)
                self._updating_page_slider = False
                self.page_slider.set_sensitive(True)
                self.page_slider.set_visible(True)
            else:
                self._updating_page_slider = True
                self.page_slider.set_value(1)
                self._updating_page_slider = False
                self.page_slider.set_sensitive(False)
                self.page_slider.set_visible(False)

    def on_thumbnail_selected(self, selection_model, position, n_items):
         """Navigate to page selected in the thumbnail sidebar."""
         selected_index = selection_model.get_selected()
         if selected_index != Gtk.INVALID_LIST_POSITION and selected_index != self.current_page_index:
              if hasattr(self, '_syncing_thumb') and self._syncing_thumb: return
              self._load_page(selected_index)

    def _sync_thumbnail_selection(self):
         """Update sidebar thumbnail selection without re-triggering navigation."""
         if not self.doc or not self.thumbnail_selection_model: return
         self._syncing_thumb = True
         self.thumbnail_selection_model.set_selected(self.current_page_index)
         self._syncing_thumb = False

    @property
    def edit_mode(self):
        """Return True if currently in edit mode."""
        return not getattr(self, 'view_mode', True)

    def on_page_reorder(self, from_index, to_index):
         """Move a page in the document when reordered via drag-and-drop in thumbnail list."""
         if getattr(self, 'view_mode', False) or not self.edit_mode:
             return
         if from_index == to_index or from_index < 0 or to_index < 0:
             return
         
         def remap(page):
             if page==from_index:
                 return to_index
             if from_index<to_index and from_index<page<=to_index:
                 return page-1
             if to_index<from_index and to_index<=page<from_index:
                 return page+1
             return page
         success, message = self._change_pages(lambda:pdf_handler.move_page(self.doc,from_index,to_index),remap)
         
         if success:
             self._clear_search()
             self.document_modified = True
             self.status_label.set_text(message)
             self._load_thumbnails()
             self._load_page(to_index)
             self._update_ui_state()
         else:
             show_error_dialog(self, message, _("err_move_page_title"))


    def on_pdf_view_pressed(self, gesture, n_press, x, y):
        """Handle primary mouse button press for object selection, links, or tool starts."""
        if not self.doc or self.current_pdf_page_width == 0 or self.current_pdf_page_height == 0:
            return

        if self.tool_mode=='certificate_signature':return
        if self.tool_mode in ('measure','nodes'):return
        if self.tool_mode == 'drag':
            return

        drawing_area_width = self.pdf_view.get_allocated_width()
        drawing_area_height = self.pdf_view.get_allocated_height()
        page_offset_x = max(0, (drawing_area_width - self.current_pdf_page_width) / 2)
        page_offset_y = max(0, (drawing_area_height - self.current_pdf_page_height) / 2)

        page_x_unzoomed = (x - page_offset_x) / self.zoom_level
        page_y_unzoomed = (y - page_offset_y) / self.zoom_level

        interaction=getattr(getattr(self,'form_tools',None),'interaction',None)
        if self.tool_mode=='select' and interaction and interaction.handle_at(page_x_unzoomed,page_y_unzoomed):return
        if self.tool_mode == 'select':
            from .document_tools import form_field_at_point
            point=fitz.Point(page_x_unzoomed,page_y_unzoomed)*self.doc[self.current_page_index].derotation_matrix
            field=form_field_at_point(self.doc,self.current_page_index,point)
            if field:
                self._apply_and_hide_editor()
                state=gesture.get_current_event_state() if gesture and hasattr(gesture,'get_current_event_state') else 0
                extend=bool(state & (Gdk.ModifierType.SHIFT_MASK|Gdk.ModifierType.CONTROL_MASK))
                self.form_tools.click_field(field,extend=extend)
                return
        if interaction:interaction.selected=None

        if self.tool_mode in ('select','drag','stamp') and hasattr(self,'stamp_interaction'):
            if self.stamp_interaction.handle_at(page_x_unzoomed,page_y_unzoomed):
                return
        if not self.view_mode and self.inline_editor_widget is None and self.selected_text:
            if self._find_resize_handle_at_pos(x,y,self.selected_text):
                return
        if hasattr(self,'form_tools') and not self.form_tools.finish_inline():return
        if self.view_mode and n_press==2:
            self.view_sel_rect=None
            self.view_sel_start=None
            self.document_tools.close_note_bubble()
            self._toggle_fullscreen()
            if gesture:gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.pdf_view.queue_draw()
            return
        if self.tool_mode == 'erase_highlight' or (self.view_mode and self.tool_mode == 'highlighter'):
            # Clicks and drags are resolved by the drag gesture.
            return
        if self.tool_mode == 'sticky_note' and not self.view_mode:
            self.document_tools.place_note(page_x_unzoomed,page_y_unzoomed)
            return
        if self.tool_mode == 'stamp' and not self.view_mode:
            note=self.stamp_interaction.hit(page_x_unzoomed,page_y_unzoomed)
            if note:
                self.stamp_interaction.select(note)
                if n_press==2:
                    self.document_tools.stamp(note)
                return
            self.document_tools.place_stamp(page_x_unzoomed,page_y_unzoomed)
            return
        if self.tool_mode in ('select','drag') or self.view_mode:
            from .document_tools import note_at_point
            point=fitz.Point(page_x_unzoomed,page_y_unzoomed)*self.doc[self.current_page_index].derotation_matrix
            note=note_at_point(self.doc,self.current_page_index,point,include_stamps=True)
            if note:
                self._apply_and_hide_editor()
                if note['kind']=='Stamp' and not self.view_mode:
                    self.stamp_interaction.select(note)
                    if n_press==2:
                        self.document_tools.stamp(note)
                elif (self.tool_mode=='select' and hasattr(self,'stamp_interaction')
                      and self.stamp_interaction.movable(note)):
                    # The drag gesture moves the note, or opens it on a plain click.
                    self.stamp_interaction.select(note)
                else:
                    self.document_tools.open_note_bubble(note)
                return

        if hasattr(self,'stamp_interaction'):
            self.stamp_interaction.cancel()

        modifiers = Gtk.EventController.get_current_event_state(gesture)
        ctrl_pressed = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)

        clicked_text = self._find_text_at_pos(page_x_unzoomed, page_y_unzoomed)
        
        if ctrl_pressed and clicked_text and clicked_text.is_link:
            import webbrowser
            match = re.search(r'https?://[^\s]+', clicked_text.text)
            if match:
                webbrowser.open(match.group(0))
            return

        if self.tool_mode == "signature" and not self.view_mode:
            if (0 <= page_x_unzoomed < self.current_pdf_page_width / self.zoom_level and
                    0 <= page_y_unzoomed < self.current_pdf_page_height / self.zoom_level):
                self._place_visible_signature(page_x_unzoomed, page_y_unzoomed)
            return

        if self.view_mode:
            self.pdf_view.grab_focus()
            if n_press == 1:
                self.view_sel_start=(page_x_unzoomed,page_y_unzoomed)
                self._active_session.view_selection_quads=[]
                clicked_block = pdf_handler.get_block_at_pos(self.doc, self.current_page_index, (page_x_unzoomed, page_y_unzoomed))
                if clicked_block:
                    self.view_sel_rect = clicked_block['bbox']
                    self.view_selected_text = clicked_block['text']
                    self.word_selection_mode = False
                else:
                    self.view_sel_rect = None
                    self.view_selected_text = ""
                    self.word_selection_mode = False
                self.pdf_view.queue_draw()
                self._update_ui_state()
            return

        if self.font_scan_in_progress:
            show_error_dialog(self, _("err_fonts_scanning_msg"), _("err_fonts_scanning_title"))
            return

        drawing_area_width = self.pdf_view.get_allocated_width()
        drawing_area_height = self.pdf_view.get_allocated_height()

        page_w_zoomed = self.current_pdf_page_width
        page_h_zoomed = self.current_pdf_page_height

        page_offset_x = max(0, (drawing_area_width - page_w_zoomed) / 2)
        page_offset_y = max(0, (drawing_area_height - page_h_zoomed) / 2)

        click_x_on_page_zoomed = x - page_offset_x
        click_y_on_page_zoomed = y - page_offset_y

        is_on_page = (0 <= click_x_on_page_zoomed < page_w_zoomed and
                    0 <= click_y_on_page_zoomed < page_h_zoomed)
        
        self.commit_pending_format_change()

        if self.tool_mode in ("select", "drag"):
            table = getattr(self, 'selected_table', None)
            handle = self._find_resize_handle_at_pos(x, y, table) if table else None
            table = table if handle else self._find_table_at_pos(page_x_unzoomed, page_y_unzoomed)
            if table and n_press == 1:
                if self.inline_editor_widget is not None:
                    self._apply_and_hide_editor()
                self._select_table(table)
                return
            if table and n_press > 1:
                # A double-click edits the cell: drop the drag its second press started.
                self._cancel_table_drag()
            self.selected_table = None
            if table and n_press > 1 and clicked_text:
                self.selected_text = clicked_text
                self.selected_image = self.selected_shape = self.selected_stroke = None
                self._show_inline_editor(clicked_text, click_x=x, click_y=y)
                return

        if not is_on_page:
            if self.inline_editor_widget is not None:
                self._apply_and_hide_editor()
            self.selected_text = None
            self.selected_image = None
            self.pdf_view.queue_draw()
            self._update_ui_state()
            return

        page_x_unzoomed = click_x_on_page_zoomed / self.zoom_level
        page_y_unzoomed = click_y_on_page_zoomed / self.zoom_level


        if self.tool_mode == "select":
            if self.inline_editor_widget is not None:
                self._apply_and_hide_editor()

            clicked_image = self._find_image_at_pos(page_x_unzoomed, page_y_unzoomed)
            clicked_text = self._find_text_at_pos(page_x_unzoomed, page_y_unzoomed)
            clicked_shape = self._find_shape_at_pos(page_x_unzoomed, page_y_unzoomed)
            clicked_stroke = self._find_stroke_at_pos(page_x_unzoomed, page_y_unzoomed)

            if clicked_image:
                self.selected_image = clicked_image
                self.selected_text = None
                self.selected_shape = None
                self.selected_stroke = None
            elif clicked_text:
                self.selected_image = None
                self.selected_shape = None
                self.selected_stroke = None
                if clicked_text == self.selected_text and n_press > 1:
                    self._show_inline_editor(clicked_text, click_x=x, click_y=y)
                else:
                    self.selected_text = clicked_text
                    self.word_selection_mode = False
                    self.pending_format_change_obj = self.selected_text
                if self.selected_text:
                    self.pending_format_change_obj = self.selected_text
                    self.before_format_change_state = copy.deepcopy(self.selected_text.__dict__)
                    self._update_text_format_controls(self.selected_text)
            elif clicked_shape:
                self.selected_shape = clicked_shape
                self.selected_text = None
                self.selected_image = None
                self.selected_stroke = None
            elif clicked_stroke:
                self.selected_stroke = clicked_stroke
                self.selected_shape = None
                self.selected_text = None
                self.selected_image = None
                self._update_stroke_format_controls(self.selected_stroke)
            else:
                self.selected_text = None
                self.selected_image = None
                self.selected_shape = None
                self.selected_stroke = None

            self.pdf_view.queue_draw()
            self._update_ui_state()

        elif self.tool_mode == "add_text":
            if self.inline_editor_widget is not None:
                self._apply_and_hide_editor()
                return

            font_fam_display, font_pdf_name, font_size, color, is_bold, is_italic, is_underline, is_strikethrough, alignment = self._get_current_format_settings()
            if self._last_font_family is not None:
                font_fam_display = self._last_font_family
                font_size = self._last_font_size
                is_bold = self._last_is_bold
                is_italic = self._last_is_italic
                is_underline = getattr(self, 'underline_button', None).get_active() if hasattr(self, 'underline_button') else False
                is_strikethrough = getattr(self, 'strikethrough_button', None).get_active() if hasattr(self, 'strikethrough_button') else False
                alignment = self._get_current_alignment()
                color = self._last_color
            unrot_x, unrot_y = self._visual_to_unrotated_page_coords(page_x_unzoomed, page_y_unzoomed)
            baseline_y_unzoomed = unrot_y + (font_size * 0.9)

            target_family_key = font_fam_display
            target_base14 = 'helv'
            iter = self.font_combo.get_active_iter()
            if iter:
                model_key = self.font_store[iter][1]
                normalized_for_base14 = re.sub(r'[^a-zA-Z0-9]', '', model_key).lower()
                for name_key, base14_val in BASE14_FALLBACK_MAP.items():
                    if name_key in normalized_for_base14:
                        target_base14 = base14_val
                        break

            new_text_obj = EditableText(
                x=unrot_x,
                y=unrot_y,
                text=_("default_new_text"),
                font_size=font_size,
                color=color,
                is_new=True,
                baseline=baseline_y_unzoomed
            )
            new_text_obj.font_family_base = target_family_key
            new_text_obj.font_family_original = _("font_user_added", target_family_key)
            new_text_obj.is_bold = is_bold
            new_text_obj.is_italic = is_italic
            new_text_obj.is_underline = is_underline
            new_text_obj.is_strikethrough = is_strikethrough
            new_text_obj.alignment = alignment
            new_text_obj.pdf_fontname_base14 = target_base14
            new_text_obj.page_number = self.current_page_index

            self.selected_text = new_text_obj
            self.selected_image = None
            self._update_text_format_controls(self.selected_text)
            self._show_inline_editor(new_text_obj, click_x=x, click_y=y)
            self._update_ui_state()

        elif self.tool_mode == "add_image":
            # patched
            pass

        elif self.tool_mode == "add_ellipse":
            # patched
            pass

        elif self.tool_mode == "add_rectangle":
            # patched
            pass

    def on_text_format_changed(self, widget, *args):
        """Apply typography updates (font family, size, style, color, alignment) to selected text."""
        if self.font_scan_in_progress:
            return

        iter = self.font_combo.get_active_iter()
        if iter:
            self._last_font_family = self.font_store[iter][1]
        self._last_font_size = self.font_size_spin.get_value()
        self._last_is_bold = self.bold_button.get_active() if self.bold_button else False
        self._last_is_italic = self.italic_button.get_active() if self.italic_button else False
        self._last_is_strikethrough = getattr(self, 'strikethrough_button', None).get_active() if hasattr(self, 'strikethrough_button') else False
        rgba = self.color_button.get_rgba()
        self._last_color = (rgba.red, rgba.green, rgba.blue)
        
        font_family_key = self._last_font_family
        font_size = self._last_font_size
        color = self._last_color
        is_bold = self._last_is_bold
        is_italic = self._last_is_italic
        is_underline = getattr(self, 'underline_button', None).get_active() if hasattr(self, 'underline_button') else False
        is_strikethrough = getattr(self, 'strikethrough_button', None).get_active() if hasattr(self, 'strikethrough_button') else False
        align_btns = [getattr(self, 'align_left_button', None), getattr(self, 'align_center_button', None),
                      getattr(self, 'align_right_button', None), getattr(self, 'align_justify_button', None)]
        if widget in align_btns and not widget.get_active():
            def check_restore_active(deactivated_btn):
                if all(b and not b.get_active() for b in align_btns if b):
                    deactivated_btn.handler_block_by_func(self.on_text_format_changed)
                    deactivated_btn.set_active(True)
                    deactivated_btn.handler_unblock_by_func(self.on_text_format_changed)
                return False
            GLib.idle_add(check_restore_active, widget)
            return

        if widget in align_btns and widget.get_active():
            for b in align_btns:
                if b and b != widget and b.get_active():
                    b.handler_block_by_func(self.on_text_format_changed)
                    b.set_active(False)
                    b.handler_unblock_by_func(self.on_text_format_changed)

        alignment = self._get_current_alignment()
        self._last_alignment = alignment
        
        if hasattr(self, 'inline_editor_tv') and self.inline_editor_tv:
            if alignment == 'center':
                self.inline_editor_tv.set_justification(Gtk.Justification.CENTER)
            elif alignment == 'right':
                self.inline_editor_tv.set_justification(Gtk.Justification.RIGHT)
            elif alignment == 'justify':
                self.inline_editor_tv.set_justification(Gtk.Justification.FILL)
            else:
                self.inline_editor_tv.set_justification(Gtk.Justification.LEFT)
        
        if hasattr(self, 'inline_editor_tv') and self.inline_editor_tv and self.inline_editor_text_obj:
            buf = self.inline_editor_tv.get_buffer()
            # PyGObject returns () when nothing is selected, else (start, end).
            bounds = buf.get_selection_bounds()
            has_sel = len(bounds) == 2
            if has_sel:
                start_iter, end_iter = bounds
                start_char = start_iter.get_offset()
                end_char = end_iter.get_offset()
                current_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
                target_obj = self.inline_editor_text_obj
                self._hide_inline_editor()
                target_obj.text = current_text
                spans = target_obj.split_at_range(start_char, end_char)
                
                if len(spans) > 1:
                    mid_index = 0 if start_char == 0 else 1
                    mid_span = spans[mid_index]
                    mid_span.font_family_base = font_family_key
                    mid_span.font_size = font_size
                    mid_span.color = color
                    mid_span.is_bold = is_bold
                    mid_span.is_italic = is_italic
                    mid_span.is_underline = is_underline
                    mid_span.is_strikethrough = is_strikethrough
                    mid_span.alignment = alignment
                    
                    from .undo_manager import DeleteObjectCommand, AddObjectCommand, CompositeCommand
                    commands = [DeleteObjectCommand(self, target_obj)]
                    for span in spans:
                        commands.append(AddObjectCommand(self, span))
                    
                    batch_cmd = CompositeCommand(self, commands)
                    batch_cmd.execute()
                    self.undo_manager.add_command(batch_cmd)
                    self.selected_text = mid_span
                    self.pending_format_change_obj = mid_span
                    self._update_ui_state()
                    self.pdf_view.queue_draw()
                    return

        if getattr(self, 'word_selection_mode', False) and self.selected_text and hasattr(self, 'selected_word_start_char') and hasattr(self, 'selected_word_end_char'):
            start_char = self.selected_word_start_char
            end_char = self.selected_word_end_char
            target_obj = self.selected_text
            spans = target_obj.split_at_range(start_char, end_char)
            
            if len(spans) > 1:
                mid_index = 0 if start_char == 0 else 1
                mid_span = spans[mid_index]
                if self._last_font_family: mid_span.font_family_base = self._last_font_family
                mid_span.font_size = self._last_font_size
                mid_span.is_bold = is_bold
                mid_span.is_italic = is_italic
                mid_span.is_underline = is_underline
                mid_span.is_strikethrough = is_strikethrough
                mid_span.alignment = alignment
                mid_span.color = color
                
                from .undo_manager import DeleteObjectCommand, AddObjectCommand, CompositeCommand
                commands = [DeleteObjectCommand(self, target_obj)]
                for span in spans:
                    commands.append(AddObjectCommand(self, span))
                
                batch_cmd = CompositeCommand(self, commands)
                batch_cmd.execute()
                self.undo_manager.add_command(batch_cmd)
                
                self.selected_text = mid_span
                self.pending_format_change_obj = mid_span
                self.selected_text = mid_span
                self.pending_format_change_obj = mid_span
                self.before_format_change_state = copy.deepcopy(mid_span.__dict__)
                
                self.selected_word_start_char = 0
                self.selected_word_end_char = len(mid_span.text)
                
                self.pdf_view.queue_draw()
                self._update_ui_state()
                return

        target_text = self.pending_format_change_obj or self.selected_text
        if target_text:
            if not self.pending_format_change_obj:
                self.pending_format_change_obj = target_text
                self.before_format_change_state = copy.deepcopy(target_text.__dict__)
            changed = False

            if font_family_key and self.pending_format_change_obj.font_family_base != font_family_key:
                self.pending_format_change_obj.font_family_base = font_family_key
                changed = True
            
            if self.pending_format_change_obj.font_size != font_size:
                obj = self.pending_format_change_obj
                old_size = obj.font_size
                obj.font_size = font_size
                changed = True
                # Keep the first line inside the box: the baseline's distance
                # below the top edge grows and shrinks with the font size.
                if obj.bbox and old_size and getattr(obj, 'baseline', None) is not None:
                    top = obj.bbox[1]
                    obj.baseline = top + (obj.baseline - top) * (font_size / old_size)
                if obj.bbox:
                    x1, y1, x2, y2 = obj.bbox
                    old_h = y2 - y1
                    old_w = x2 - x1
                    try:
                        _surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                        _cr = cairo.Context(_surf)
                        _layout = PangoCairo.create_layout(_cr)
                        _fd_str = f"{obj.font_family_base} {font_size}"
                        if obj.is_bold: _fd_str += " Bold"
                        if obj.is_italic: _fd_str += " Italic"
                        _layout.set_font_description(Pango.FontDescription.from_string(_fd_str))
                        _layout.set_text(obj.text if obj.text else "Ay", -1)
                        _p_w, _p_h = _layout.get_size()
                        
                        _w = (_p_w / Pango.SCALE) * 0.75
                        _h = (_p_h / Pango.SCALE) * 0.75
                        
                        new_w = max(_w, old_w) if _w > 0 else old_w
                        new_h = _h if _h > 0 else old_h
                        obj.bbox = (x1, y1, x1 + new_w, y1 + new_h)
                    except Exception as e:
                        logger.debug(f"Error recalculating text bbox: {e}")
                        pass
                
            if self.pending_format_change_obj.is_bold != is_bold:
                self.pending_format_change_obj.is_bold = is_bold
                changed = True
            if self.pending_format_change_obj.is_italic != is_italic:
                self.pending_format_change_obj.is_italic = is_italic
                changed = True
            if getattr(self.pending_format_change_obj, 'is_underline', False) != is_underline:
                self.pending_format_change_obj.is_underline = is_underline
                changed = True
            if getattr(self.pending_format_change_obj, 'is_strikethrough', False) != is_strikethrough:
                self.pending_format_change_obj.is_strikethrough = is_strikethrough
                changed = True
            if getattr(self.pending_format_change_obj, 'alignment', 'left') != alignment:
                self.pending_format_change_obj.alignment = alignment
                changed = True

            if self.pending_format_change_obj.color != color:
                self.pending_format_change_obj.color = color
                changed = True
                
            if changed and self.pending_format_change_obj.bbox:
                obj = self.pending_format_change_obj
                x1, y1, x2, y2 = obj.bbox
                try:
                    _surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
                    _cr = cairo.Context(_surf)
                    _layout = PangoCairo.create_layout(_cr)
                    font_desc = Pango.FontDescription.from_string(obj.font_family_base)
                    if obj.is_bold: font_desc.set_weight(Pango.Weight.BOLD)
                    if obj.is_italic: font_desc.set_style(Pango.Style.ITALIC)
                    font_desc.set_absolute_size(int(obj.font_size * Pango.SCALE))
                    _layout.set_font_description(font_desc)
                    _layout.set_text(obj.text if obj.text else "Ay", -1)
                    _p_w, _p_h = _layout.get_size()
                    # Measure with the font the page is rendered with too; Pango's
                    # metrics can be narrower and the last glyph then overflowed.
                    _w = max(_p_w / Pango.SCALE, pdf_handler.text_width(obj)) + 2 + obj.font_size * 0.05
                    _h = (_p_h / Pango.SCALE)
                    if _w > 0 and _h > 0:
                        old_w = x2 - x1
                        final_w = max(_w, old_w) if getattr(obj, 'alignment', 'left') != 'left' else _w
                        obj.bbox = (x1, y1, x1 + final_w, y1 + _h)
                except Exception as e:
                    logger.debug(f"Error recalculating text bbox on format change: {e}")

            if changed:
                obj = self.pending_format_change_obj
                if self.inline_editor_widget is not None:
                    self._apply_and_hide_editor(force_apply=True)
                else:
                    new_properties = copy.deepcopy(obj.__dict__)
                    obj.__dict__.update(self.before_format_change_state)
                    command = EditObjectCommand(self, obj, self.before_format_change_state, new_properties)
                    command.execute()
                    self.undo_manager.add_command(command)
                    self.before_format_change_state = copy.deepcopy(obj.__dict__)
                    self.pdf_view.queue_draw()
                    self._update_ui_state()

    def on_shape_format_changed(self, widget, *args):
        """Apply shape fill, stroke color, and line width updates to selection or next shape."""
        fill_rgba = self.shape_fill_button.get_rgba()
        fill_color = (fill_rgba.red, fill_rgba.green, fill_rgba.blue)
        stroke_rgba = self.shape_stroke_button.get_rgba()
        stroke_color = (stroke_rgba.red, stroke_rgba.green, stroke_rgba.blue)
        stroke_width = self.shape_stroke_width_spin.get_value()
        is_transparent = self.shape_transparent_toggle.get_active()
        self.next_shape_fill = fill_color
        self.next_shape_stroke = stroke_color
        self.next_shape_stroke_width = stroke_width
        self.next_shape_transparent = is_transparent

        if self.selected_shape:
            old_properties = copy.deepcopy(self.selected_shape.__dict__)
            changed = False
            if self.selected_shape.fill_color != fill_color:
                self.selected_shape.fill_color = fill_color
                changed = True
            if self.selected_shape.stroke_color != stroke_color:
                self.selected_shape.stroke_color = stroke_color
                changed = True
            if self.selected_shape.stroke_width != stroke_width:
                self.selected_shape.stroke_width = stroke_width
                changed = True
            if self.selected_shape.is_transparent != is_transparent:
                self.selected_shape.is_transparent = is_transparent
                changed = True

            if changed:
                new_properties = copy.deepcopy(self.selected_shape.__dict__)
                self.selected_shape.__dict__.update(old_properties)
                
                command = EditObjectCommand(self, self.selected_shape, old_properties, new_properties)
                command.execute()
                self.undo_manager.add_command(command)
                self.pdf_view.queue_draw()
                self._update_ui_state()

    def on_stroke_format_changed(self, widget, *args):
        """Apply stroke color or line width updates to active drawing tool or selected stroke."""
        stroke_rgba = self.stroke_color_button.get_rgba()
        stroke_color = (stroke_rgba.red, stroke_rgba.green, stroke_rgba.blue)
        stroke_width = self.stroke_width_spin.get_value()

        if self.tool_mode == "highlighter" or (self.selected_stroke and self.selected_stroke.tool_type == "highlighter"):
            self.highlighter_color = stroke_color
            self.highlighter_width = stroke_width
            self.highlight_color_rgba = stroke_rgba
            self.highlight_color_button.set_rgba(stroke_rgba)
        else:
            self.pen_color = stroke_color
            self.pen_width = stroke_width

        if self.selected_stroke:
            old_properties = copy.deepcopy(self.selected_stroke.__dict__)
            changed = False
            if self.selected_stroke.stroke_color != stroke_color:
                self.selected_stroke.stroke_color = stroke_color
                changed = True
            if self.selected_stroke.stroke_width != stroke_width:
                self.selected_stroke.stroke_width = stroke_width
                self.selected_stroke.recalculate_bbox()
                changed = True

            if changed:
                new_properties = copy.deepcopy(self.selected_stroke.__dict__)
                self.selected_stroke.__dict__.update(old_properties)

                command = EditObjectCommand(self, self.selected_stroke, old_properties, new_properties)
                command.execute()
                self.undo_manager.add_command(command)
                self.pdf_view.queue_draw()
                self._update_ui_state()

    def on_text_edit_done(self, button):
        """Explicitly commit pending inline text edits and dismiss the editor."""
        self._apply_and_hide_editor(force_apply=True)

    def on_key_pressed(self, controller, keyval, keycode, state):
        """Handle global keyboard shortcuts (undo/redo, delete, zoom, navigation, escape)."""
        if keyval == Gdk.KEY_F9 and self.doc and not getattr(self, '_document_only_view', False):
            self._toggle_sidebar()
            return True
        if keyval==Gdk.KEY_F11:
            self._toggle_fullscreen()
            return True
        if keyval==Gdk.KEY_Escape and (self.is_fullscreen() or getattr(self,'_document_only_view',False)):
            self._exit_document_view()
            return True
        if self.search_revealer.get_reveal_child() and self.search_entry.is_focus():
            if keyval == Gdk.KEY_Escape:
                self._hide_search()
                return True
            return False
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        forms=getattr(self,'form_tools',None)
        editor=getattr(forms,'inline_editor',None)
        interaction=getattr(forms,'interaction',None)
        if ctrl and keyval in (Gdk.KEY_d,Gdk.KEY_D) and not self.view_mode and interaction:
            field=interaction.current()
            if field:
                forms.duplicate_field(field)
                return True
        if editor:
            focus=self.get_focus()
            if focus and (focus is editor.frame or focus.is_ancestor(editor.frame)):
                if ctrl and keyval in (Gdk.KEY_s,Gdk.KEY_S):
                    self.on_save_clicked(None)
                    return True
                return False

        if self.view_mode:
            if not ctrl and keyval in (Gdk.KEY_s, Gdk.KEY_S, Gdk.KEY_m, Gdk.KEY_M):
                self.on_tool_selected(None, 'drag' if keyval in (Gdk.KEY_m, Gdk.KEY_M) else 'select')
                return True
            if ctrl and keyval in (Gdk.KEY_z,Gdk.KEY_Z,Gdk.KEY_y,Gdk.KEY_Y):
                redo=keyval in (Gdk.KEY_y,Gdk.KEY_Y) or bool(state&Gdk.ModifierType.SHIFT_MASK)
                action=self.lookup_action('redo' if redo else 'undo')
                if action and action.get_enabled():
                    action.activate(None)
                return True
            if not ctrl and keyval in (Gdk.KEY_h,Gdk.KEY_H):
                self.on_highlight_clicked(None)
                return True
            if ctrl and keyval in (Gdk.KEY_a,Gdk.KEY_A):
                from .reader_selection import characters,select
                text,bounds,quads=select(characters(self.doc[self.current_page_index])) if self.doc else ('',None,[])
                self.view_selected_text=text
                self.view_sel_rect=bounds
                self._active_session.view_selection_quads=quads
                self.pdf_view.queue_draw()
                self._update_ui_state()
                return True
            if keyval == Gdk.KEY_Escape:
                if self.tool_mode in ('highlighter', 'drag', 'erase_highlight'):
                    self.on_tool_selected(None,'select')
                self.view_sel_rect = None
                self.view_sel_start = None
                self.view_selected_text = ""
                self.pdf_view.queue_draw()
                self._update_ui_state()
                return True
            if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
                if self.view_selected_text and getattr(self._active_session, 'can_copy', True):
                    clipboard = self.get_clipboard()
                    clipboard.set(self.view_selected_text)
                return True
            if ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self.on_new_clicked(None)
                return True
            if ctrl and keyval in (Gdk.KEY_o, Gdk.KEY_O):
                self.on_open_clicked(None)
                return True
            if ctrl and keyval in (Gdk.KEY_w, Gdk.KEY_W):
                self.on_close_tab()
                return True
            if ctrl and keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Tab):
                self.on_next_tab()
                return True
            if ctrl and keyval in (Gdk.KEY_Page_Up, Gdk.KEY_ISO_Left_Tab):
                self.on_prev_tab()
                return True
            return False

        if (ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C)
                and getattr(self, 'selected_table', None) and self.inline_editor_widget is None):
            self.copy_table()
            return True

        if keyval == Gdk.KEY_Escape:
            placement=getattr(self,'_certificate_placement',None)
            if placement:
                placement.cancel()
                self.on_tool_selected(None,'select')
                return True
            if self.stamp_interaction.selected:
                self.stamp_interaction.cancel()
                return True
            if self.tool_mode in ("form_create","form_reposition","stamp"):
                self.on_tool_selected(None,"select")
                return True
            if getattr(self, 'selected_table', None):
                self.selected_table = None
                self.pdf_view.queue_draw()
                self._update_ui_state()
                return True
            if self.inline_editor_widget is not None:
                 self.hide_text_editor()
                 if self.selected_text and self.selected_text.is_new:
                      self.selected_text = None
                      self.pdf_view.queue_draw()
                 elif self.selected_text:
                      self.pdf_view.queue_draw()
                 self._update_ui_state()
                 return True
            elif self.selected_text:
                 self.selected_text = None
                 self.pdf_view.queue_draw()
                 self._update_ui_state()
                 return True
            elif getattr(self, 'selected_stroke', None):
                 self.selected_stroke = None
                 self.pdf_view.queue_draw()
                 self._update_ui_state()
                 return True
            elif self.tool_mode in ("add_text", "pen", "highlighter", "signature", "erase_highlight"):
                 self.on_tool_selected(None, "select")
                 return True

        if keyval == Gdk.KEY_F1:
            self.activate_action("quick_guide", None)
            return True

        if ctrl and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.on_new_clicked(None)
            return True
        elif ctrl and keyval in (Gdk.KEY_o, Gdk.KEY_O):
            self.on_open_clicked(None)
            return True
        elif ctrl and keyval in (Gdk.KEY_w, Gdk.KEY_W):
            self.on_close_tab()
            return True
        elif ctrl and keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Tab):
            self.on_next_tab()
            return True
        elif ctrl and keyval in (Gdk.KEY_Page_Up, Gdk.KEY_ISO_Left_Tab):
            self.on_prev_tab()
            return True
        elif ctrl and keyval in (Gdk.KEY_s, Gdk.KEY_S):
            self.on_save_clicked(None)
            return True
        elif ctrl and keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self.on_zoom_in(focal_point=getattr(self, '_last_pointer_pos', None))
            return True
        elif ctrl and keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self.on_zoom_out(focal_point=getattr(self, '_last_pointer_pos', None))
            return True
        elif ctrl and keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self._set_zoom(1.0, focal_point=getattr(self, '_last_pointer_pos', None))
            return True

        elif ctrl and keyval in (Gdk.KEY_bracketright, Gdk.KEY_bracketleft) and not self.view_mode:
            target = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
            if target and self.inline_editor_widget is None:
                from .layering import restack
                self.commit_pending_format_change()
                restack(self, target, keyval == Gdk.KEY_bracketright)
                return True
        elif keyval == Gdk.KEY_Delete:
            self.commit_pending_format_change()
            table = getattr(self, 'selected_table', None)
            if table is not None and self.inline_editor_widget is None:
                from .undo_manager import DeleteObjectsCommand
                command = DeleteObjectsCommand(self, table.objects)
                command.execute()
                self.undo_manager.add_command(command)
                self._update_ui_state()
                return True
            obj_to_delete = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
            if obj_to_delete and not (self.inline_editor_widget is not None):
                self._handle_delete_with_confirmation(obj_to_delete, "delete_confirm_title")
                return True
            elif self.selected_image:
                confirm = show_confirm_dialog(self, _("image_delete_confirm_msg"), _("image_delete_confirm_title"))
                if confirm:
                    self.status_label.set_text("Resim siliniyor...")
                    success, error_msg = pdf_handler.delete_image_from_page(self.doc, self.selected_image)
                    if success:
                        self.document_modified = True
                        self._load_page(self.current_page_index)
                        self.status_label.set_text("Resim silindi.")
                    else:
                        show_error_dialog(self, _("err_image_delete_msg", error_msg), _("err_image_delete_title"))
                    self.selected_image = None
                    self._update_ui_state()
        # Arrow key nudge movement (1pt normal / 10pt with Shift) for selected objects
        if self.inline_editor_widget is None and keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Up, Gdk.KEY_Down):
            selected_obj = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
            if selected_obj:
                step = 10.0 if bool(state & Gdk.ModifierType.SHIFT_MASK) else 1.0
                dx, dy = 0.0, 0.0
                if keyval == Gdk.KEY_Left:
                    dx = -step
                elif keyval == Gdk.KEY_Right:
                    dx = step
                elif keyval == Gdk.KEY_Up:
                    dy = -step
                elif keyval == Gdk.KEY_Down:
                    dy = step

                old_properties = copy.deepcopy(selected_obj.__dict__)

                if isinstance(selected_obj, EditableText):
                    selected_obj.x += dx
                    selected_obj.y += dy
                    if getattr(selected_obj, 'baseline', None) is not None:
                        selected_obj.baseline += dy
                    if selected_obj.bbox:
                        x1, y1, x2, y2 = selected_obj.bbox
                        selected_obj.bbox = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
                elif isinstance(selected_obj, EditableStroke):
                    selected_obj.points = [(p[0] + dx, p[1] + dy) for p in selected_obj.points]
                    selected_obj.recalculate_bbox()
                    selected_obj.original_bbox = selected_obj.bbox
                elif isinstance(selected_obj, (EditableShape, EditableImage)):
                    x1, y1, x2, y2 = selected_obj.bbox
                    selected_obj.bbox = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
                    selected_obj.x = selected_obj.bbox[0]
                    selected_obj.y = selected_obj.bbox[1]

                new_properties = copy.deepcopy(selected_obj.__dict__)
                selected_obj.__dict__.update(old_properties)

                command = EditObjectCommand(self, selected_obj, old_properties, new_properties)
                command.execute()
                self.undo_manager.add_command(command)
                self.document_modified = True
                self._refresh_thumbnail(self.current_page_index)
                self.pdf_view.queue_draw()
                self._update_ui_state()
                return True

        # Single-key tool selection shortcuts (when not editing text)
        if not ctrl and not (state & Gdk.ModifierType.ALT_MASK) and self.inline_editor_widget is None:
            tool_shortcuts = {
                Gdk.KEY_v: "add_checkmark",
                Gdk.KEY_V: "add_checkmark",
                Gdk.KEY_x: "add_cross",
                Gdk.KEY_X: "add_cross",
                Gdk.KEY_p: "pen",
                Gdk.KEY_P: "pen",
                Gdk.KEY_h: "highlighter",
                Gdk.KEY_H: "highlighter",
                Gdk.KEY_s: "select",
                Gdk.KEY_S: "select",
                Gdk.KEY_t: "add_text",
                Gdk.KEY_T: "add_text",
                Gdk.KEY_i: "add_image",
                Gdk.KEY_I: "add_image",
                Gdk.KEY_m: "drag",
                Gdk.KEY_M: "drag",
                Gdk.KEY_c: "add_ellipse",
                Gdk.KEY_C: "add_ellipse",
                Gdk.KEY_r: "add_rectangle",
                Gdk.KEY_R: "add_rectangle",
            }
            if keyval in tool_shortcuts:
                self.on_tool_selected(None, tool_shortcuts[keyval])
                return True

        return False

    def on_tool_selected(self, button, tool_name):
        """Switch active editor tool mode (select, text, shapes, pen, highlighter)."""
        if hasattr(self,'form_tools') and not self.form_tools.finish_inline():return
        placement=getattr(self,'_certificate_placement',None)
        if placement and tool_name!='certificate_signature':placement.cancel(False)
        self._pan_start = None
        self._erase_drag = None
        self._cancel_table_drag()
        if hasattr(self,"stamp_interaction"):
            self.stamp_interaction.cancel()
        if hasattr(self,"form_tools"):
            self.form_tools.cancel_drag()
        if self.inline_editor_widget is not None:
             logger.debug(_("dbg_applying_changes_before_tool"))
             self._apply_and_hide_editor(force_apply=True)

        self.selected_table = None

        if self.selected_text:
            self.selected_text = None

        if self.selected_image:
            self.selected_image = None

        if self.selected_shape:
            self.selected_shape = None

        if hasattr(self, 'selected_stroke') and self.selected_stroke:
            self.selected_stroke = None

        self.pdf_view.queue_draw()

        if tool_name != "signature":
            self._pending_signature = None
        self.tool_mode = tool_name
        logger.debug(_("dbg_tool_changed", self.tool_mode))
        self._update_ui_state()
        if self.tool_mode in ("pen", "highlighter", "add_line"):
            self._update_stroke_format_controls(None)
        if self.tool_mode in ("add_shape", "add_rectangle") and hasattr(self, 'shape_tools'):
            self.shape_tools.sync_shape(None)
        if hasattr(self, 'form_tools'):
            self.form_tools.builder.sync_tool()
        self._update_cursor_for_tool()

    def on_drag_begin(self, gesture, start_x, start_y):
        """Initiate canvas drag gesture for selection, movement, resizing, or freehand drawing."""
        if not self.doc:
            return
        self._form_drag_active=False
        self._form_handle_drag=False
        self._drag_moved=False
        if self.tool_mode == 'drag':
            self._pan_start = (self.pdf_scroll.get_hadjustment().get_value(),
                               self.pdf_scroll.get_vadjustment().get_value())
            event = gesture.get_current_event() if hasattr(gesture, 'get_current_event') else None
            position = event.get_position() if event else None
            self._pan_pointer = position[1:] if position and position[0] else None
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.pdf_view.set_cursor(Gdk.Cursor.new_from_name('grabbing'))
            return

        page_w, page_h = self.current_pdf_page_width, self.current_pdf_page_height
        page_offset_x = max(0, (self.pdf_view.get_allocated_width() - page_w) / 2)
        page_offset_y = max(0, (self.pdf_view.get_allocated_height() - page_h) / 2)

        page_x = (start_x - page_offset_x) / self.zoom_level
        page_y = (start_y - page_offset_y) / self.zoom_level

        if self.tool_mode=='measure' and hasattr(self,'measure_tool'):
            state=gesture.get_current_event_state() if hasattr(gesture,'get_current_event_state') else 0
            self.measure_tool.begin(page_x,page_y,area=bool(state & Gdk.ModifierType.CONTROL_MASK))
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if self.tool_mode=='nodes' and getattr(self,'node_tool',None):
            state=gesture.get_current_event_state() if hasattr(gesture,'get_current_event_state') else 0
            self.node_tool.begin(page_x,page_y,state or Gdk.ModifierType(0))
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return

        if self.tool_mode=='select' and hasattr(self,'form_tools'):
            interaction=getattr(self.form_tools,'interaction',None)
            if interaction and interaction.begin(page_x,page_y):
                self._form_handle_drag=True
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return
            from .document_tools import form_field_at_point
            point=fitz.Point(page_x,page_y)*self.doc[self.current_page_index].derotation_matrix
            if form_field_at_point(self.doc,self.current_page_index,point):
                self._form_drag_active=True
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return
        if self.tool_mode == 'erase_highlight':
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._erase_drag = [(page_x, page_y), (page_x, page_y)]
            return
        if self.view_mode and self.tool_mode == 'select' and self.stamp_interaction.begin(page_x, page_y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if self.view_mode and self.tool_mode == 'highlighter':
            if getattr(self._active_session, 'can_edit', True):
                self._begin_highlighter_stroke(gesture, page_x, page_y)
            return
        if self.view_mode:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.view_drag_active = True
            self.view_sel_start = (page_x, page_y)
            from .reader_selection import characters
            self._view_selection_chars=characters(self.doc[self.current_page_index])
            self.pdf_view.grab_focus()
            self.pdf_view.queue_draw()
            return

        if self.tool_mode=='certificate_signature':
            placement=getattr(self,'_certificate_placement',None)
            if placement and placement.begin(page_x,page_y):gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if self.tool_mode in ('form_create','form_reposition'):
            if self.form_tools.begin_drag(page_x,page_y):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            return
        if self.tool_mode in ('select','drag','stamp') and hasattr(self,'stamp_interaction'):
            if self.stamp_interaction.begin(page_x,page_y):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return
        if self.tool_mode=='select':
            from .document_tools import form_field_at_point
            native=fitz.Point(page_x,page_y)*self.doc[self.current_page_index].derotation_matrix
            if form_field_at_point(self.doc,self.current_page_index,native):
                gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
        if self.tool_mode in ("select", "drag") and self.inline_editor_widget is None:
            table = getattr(self, 'selected_table', None)
            handle = self._find_resize_handle_at_pos(start_x, start_y, table) if table else None
            table = table if handle else self._find_table_at_pos(page_x, page_y)
            if table:
                self.commit_pending_format_change()
                self._select_table(table)
                self.table_drag_state = [copy.deepcopy(obj.__dict__) for obj in table.objects]
                self.table_drag_table = table
                self.table_drag_bbox = table.bbox
                self._table_preview_doc = fitz.open(stream=self.doc.tobytes(), filetype='pdf')
                key = (id(self.doc), table.page_number)
                pdf_handler._page_snapshots[id(self._table_preview_doc), table.page_number] = pdf_handler._page_snapshots[key]
                pdf_handler._page_original_links[id(self._table_preview_doc), table.page_number] = copy.deepcopy(pdf_handler._page_original_links.get(key, []))
                others = [[obj for obj in group if obj not in table.objects]
                          for group in (self.editable_texts, self.editable_shapes, self.editable_images, self.editable_strokes)]
                success, error = pdf_handler.rebuild_page(self._table_preview_doc, table.page_number, *others[:3], all_strokes=others[3])
                if not success:
                    self._clear_table_preview()
                    self._report_command_error(error)
                    return
                self.table_resize_handle = handle
                if hasattr(self.pdf_view, 'set_cursor'):
                    self.pdf_view.set_cursor(Gdk.Cursor.new_from_name(self._handle_cursor_name(handle) if handle else 'move'))
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return
            self.selected_table = None

        # Allow direct resize handle interaction on already selected objects regardless of active tool
        self.commit_pending_format_change()
        selected_obj = self.selected_text or self.selected_image or self.selected_shape or getattr(self, 'selected_stroke', None)
        if selected_obj:
            resize_handle = self._find_resize_handle_at_pos(start_x, start_y, selected_obj)
            if resize_handle:
                self.resize_handle = resize_handle
                self.resize_start_bbox = selected_obj.bbox
                self.dragged_object = selected_obj
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self.drag_start_pos = (start_x, start_y)
                self.drag_begin_state = copy.deepcopy(selected_obj.__dict__)

                if resize_handle == "rotate":
                    x1, y1, x2, y2 = selected_obj.bbox
                    unrot_cx = (x1 + x2) / 2.0
                    unrot_cy = (y1 + y2) / 2.0
                    vis_cx, vis_cy = self._unrotated_to_visual_page_coords(unrot_cx, unrot_cy)
                    cx = page_offset_x + vis_cx * self.zoom_level
                    cy = page_offset_y + vis_cy * self.zoom_level
                    self.rotate_center = (cx, cy)
                    self.rotate_start_angle = getattr(selected_obj, 'rotation', 0.0) % 360.0
                    start_dx = start_x - cx
                    start_dy = start_y - cy
                    self.rotate_pointer_start_angle = math.degrees(math.atan2(start_dy, start_dx))
                return

        unrot_px, unrot_py = self._visual_to_unrotated_page_coords(page_x, page_y)

        if self.tool_mode in ("pen", "highlighter"):
            self._begin_highlighter_stroke(gesture, page_x, page_y)
            return
        elif self.tool_mode == "add_shape":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (unrot_px, unrot_py)
            self.temp_shape = self.shape_tools.new_shape((unrot_px, unrot_py, unrot_px, unrot_py))
            return
        elif self.tool_mode == "add_line":
            self.selected_stroke = self.selected_text = self.selected_image = self.selected_shape = None
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (unrot_px, unrot_py)
            self.temp_stroke = self.shape_tools.new_line(unrot_px, unrot_py)
            self.pdf_view.queue_draw()
            return
        elif self.tool_mode == "add_ellipse":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (unrot_px, unrot_py)
            self.temp_shape = EditableShape(
                shape_type=EditableShape.SHAPE_ELLIPSE,
                bbox=(unrot_px, unrot_py, unrot_px, unrot_py),
                fill_color=self.next_shape_fill,
                stroke_color=self.next_shape_stroke,
                stroke_width=self.next_shape_stroke_width,
                page_number=self.current_page_index,
                is_new=True,
                is_transparent=self.next_shape_transparent
            )
            return
        elif self.tool_mode == "add_rectangle":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (unrot_px, unrot_py)
            self.temp_shape = EditableShape(
                shape_type=EditableShape.SHAPE_RECTANGLE,
                bbox=(unrot_px, unrot_py, unrot_px, unrot_py),
                fill_color=self.next_shape_fill,
                stroke_color=self.next_shape_stroke,
                stroke_width=self.next_shape_stroke_width,
                page_number=self.current_page_index,
                is_new=True,
                is_transparent=self.next_shape_transparent
            )
            return
        elif self.tool_mode == "add_checkmark":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (unrot_px, unrot_py)
            color = self.next_shape_stroke if hasattr(self, 'next_shape_stroke') else (0.1, 0.65, 0.25)
            width = max(getattr(self, 'next_shape_stroke_width', 2.5), 2.0)
            self.temp_shape = EditableShape(
                shape_type=EditableShape.SHAPE_CHECKMARK,
                bbox=(unrot_px, unrot_py, unrot_px, unrot_py),
                fill_color=(1, 1, 1),
                stroke_color=color,
                stroke_width=width,
                page_number=self.current_page_index,
                is_new=True,
                is_transparent=True
            )
            return
        elif self.tool_mode == "add_cross":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (unrot_px, unrot_py)
            color = self.next_shape_stroke if hasattr(self, 'next_shape_stroke') else (0.85, 0.15, 0.15)
            width = max(getattr(self, 'next_shape_stroke_width', 2.5), 2.0)
            self.temp_shape = EditableShape(
                shape_type=EditableShape.SHAPE_CROSS,
                bbox=(unrot_px, unrot_py, unrot_px, unrot_py),
                fill_color=(1, 1, 1),
                stroke_color=color,
                stroke_width=width,
                page_number=self.current_page_index,
                is_new=True,
                is_transparent=True
            )
            return
        elif self.tool_mode == "add_image":
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.dragging_to_create = True
            self.drag_start_page_pos = (unrot_px, unrot_py)
            self.temp_image_bbox = (unrot_px, unrot_py, unrot_px, unrot_py)
            return

        if self.tool_mode in ("select", "drag"):
            self.dragged_object = self._find_image_at_pos(page_x, page_y) or self._find_text_at_pos(page_x, page_y) or self._find_shape_at_pos(page_x, page_y) or self._find_stroke_at_pos(page_x, page_y)
            if not self.dragged_object:
                gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
        else:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        if self.dragged_object:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.drag_start_pos = (start_x, start_y)
            self.drag_begin_state = copy.deepcopy(self.dragged_object.__dict__)

        if self.dragged_object:
            if not hasattr(self.dragged_object, 'original_bbox') or not self.dragged_object.original_bbox:
                self.dragged_object.original_bbox = self.dragged_object.bbox
            if not hasattr(self.dragged_object, 'original_baseline') or self.dragged_object.original_baseline is None:
                self.dragged_object.original_baseline = getattr(self.dragged_object, 'baseline', None)

            x1, y1, _, _ = self.dragged_object.bbox
            self.drag_object_start_pos = (x1, y1)
        else:
            gesture.set_state(Gtk.EventSequenceState.DENIED)

    def _begin_highlighter_stroke(self, gesture, page_x, page_y):
        """Start a freehand pen or highlighter stroke at a visual page point."""
        unrot_px, unrot_py = self._visual_to_unrotated_page_coords(page_x, page_y)
        self.selected_stroke = None
        self.selected_text = None
        self.selected_image = None
        self.selected_shape = None
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        self.dragging_to_create = True
        self.drag_start_page_pos = (unrot_px, unrot_py)
        color = self.pen_color if self.tool_mode == "pen" else self.highlighter_color
        width = self.pen_width if self.tool_mode == "pen" else self.highlighter_width
        opacity = 1.0 if self.tool_mode == "pen" else self.highlighter_opacity
        self.temp_stroke = EditableStroke(
            points=[(unrot_px, unrot_py)],
            stroke_color=color,
            stroke_width=width,
            opacity=opacity,
            tool_type=self.tool_mode,
            page_number=self.current_page_index,
            is_new=True
        )
        self.pdf_view.queue_draw()

    def on_drag_update(self, gesture, offset_x, offset_y):
        """Update canvas interaction during drag (move/resize objects, text select, or draw strokes)."""
        if getattr(self,'_form_handle_drag',False):
            self.form_tools.interaction.update(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if getattr(self,'_form_drag_active',False):return
        if self.tool_mode=='measure' and hasattr(self,'measure_tool'):
            self.measure_tool.update(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if self.tool_mode=='nodes' and getattr(self,'node_tool',None):
            self.node_tool.update(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if self.tool_mode=='certificate_signature':
            placement=getattr(self,'_certificate_placement',None)
            if placement:placement.update(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if getattr(self, '_pan_start', None) is not None:
            event = gesture.get_current_event() if hasattr(gesture, 'get_current_event') else None
            position = event.get_position() if event else None
            if position and position[0] and self._pan_pointer is not None:
                # Surface coordinates stay stable while the canvas scrolls beneath the pointer.
                offset_x = position[1]-self._pan_pointer[0]
                offset_y = position[2]-self._pan_pointer[1]
            x, y = self._pan_start
            for adjustment, value in ((self.pdf_scroll.get_hadjustment(), x-offset_x),
                                      (self.pdf_scroll.get_vadjustment(), y-offset_y)):
                maximum = max(adjustment.get_lower(), adjustment.get_upper()-adjustment.get_page_size())
                adjustment.set_value(max(adjustment.get_lower(), min(maximum, value)))
            return
        if hasattr(self,'stamp_interaction') and self.stamp_interaction.drag:
            self.stamp_interaction.update(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if getattr(self, '_erase_drag', None):
            (sx, sy), _end = self._erase_drag
            self._erase_drag[1] = (sx + offset_x / self.zoom_level, sy + offset_y / self.zoom_level)
            self.pdf_view.queue_draw()
            return
        if self.view_mode and self.dragging_to_create and getattr(self, 'temp_stroke', None) is not None:
            delta_x, delta_y = self._visual_to_unrotated_delta(offset_x / self.zoom_level, offset_y / self.zoom_level)
            start_x, start_y = self.drag_start_page_pos
            self.temp_stroke.add_point(start_x + delta_x, start_y + delta_y)
            self.pdf_view.queue_draw()
            return
        if self.view_mode:
            if self.view_sel_start and self.view_drag_active:
                sx, sy = self.view_sel_start
                dx = offset_x / self.zoom_level
                dy = offset_y / self.zoom_level
                cx = sx + dx
                cy = sy + dy
                from .reader_selection import select
                text,bounds,quads=select(self._view_selection_chars,(sx,sy),(cx,cy))
                self.view_selected_text=text
                self.view_sel_rect=bounds
                self._active_session.view_selection_quads=quads
                self.pdf_view.queue_draw()
            return

        if self.tool_mode in ('form_create','form_reposition') and getattr(self.form_tools,'drag_start',None):
            self.form_tools.update_drag(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if self.inline_editor_widget is not None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
            
        if self.dragging_to_create:
            delta_x, delta_y = self._visual_to_unrotated_delta(offset_x / self.zoom_level, offset_y / self.zoom_level)
            if getattr(self, 'temp_stroke', None) is not None:
                start_x, start_y = self.drag_start_page_pos
                current_x = start_x + delta_x
                current_y = start_y + delta_y
                if getattr(self.temp_stroke, 'tool_type', None) == 'line':
                    state = gesture.get_current_event_state() if hasattr(gesture, 'get_current_event_state') else 0
                    end = self.shape_tools.line_end((start_x, start_y), (current_x, current_y),
                                                    bool(state & Gdk.ModifierType.SHIFT_MASK))
                    self.temp_stroke.points = [(start_x, start_y), end]
                    self.temp_stroke.recalculate_bbox()
                    self.pdf_view.queue_draw()
                    return
                self.temp_stroke.add_point(current_x, current_y)
                self.pdf_view.queue_draw()
                return
            if self.temp_image_bbox is not None:
                start_x, start_y = self.drag_start_page_pos
                current_x = start_x + delta_x
                current_y = start_y + delta_y
                
                x1 = min(start_x, current_x)
                y1 = min(start_y, current_y)
                x2 = max(start_x, current_x)
                y2 = max(start_y, current_y)
                
                if x2 - x1 < 20:
                    x2 = x1 + 20
                if y2 - y1 < 20:
                    y2 = y1 + 20
                    
                self.temp_image_bbox = (x1, y1, x2, y2)
                self.pdf_view.queue_draw()
                return
            if self.temp_shape:
                start_x, start_y = self.drag_start_page_pos
                current_x = start_x + delta_x
                current_y = start_y + delta_y
                
                x1 = min(start_x, current_x)
                y1 = min(start_y, current_y)
                x2 = max(start_x, current_x)
                y2 = max(start_y, current_y)
                
                if x2 - x1 < 10:
                    x2 = x1 + 10
                if y2 - y1 < 10:
                    y2 = y1 + 10
                    
                self.temp_shape.bbox = (x1, y1, x2, y2)
                self.pdf_view.queue_draw()
            return
        
        if (getattr(self, 'table_drag_state', None) is not None or self.dragged_object) \
                and not self._drag_past_threshold(offset_x, offset_y):
            return

        if getattr(self, 'table_drag_state', None) is not None:
            state = gesture.get_current_event_state() if hasattr(gesture, 'get_current_event_state') else 0
            try:
                keep_ratio = bool(state & Gdk.ModifierType.SHIFT_MASK)
            except TypeError:
                keep_ratio = False
            self._update_table_drag(offset_x, offset_y, keep_ratio)
            return

        if not self.dragged_object:
            return

        if self.resize_handle:
            if self.resize_handle == "rotate":
                self._handle_rotate_update(gesture, offset_x, offset_y)
            else:
                self._handle_resize_update(offset_x, offset_y)
            return
        if self.tool_mode not in ("select", "drag"):
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return

        # Finish formatting before changing geometry so moving text creates one
        # history entry rather than being recorded as a formatting change too.
        self.commit_pending_format_change()

        delta_x, delta_y = self._visual_to_unrotated_delta(offset_x / self.zoom_level, offset_y / self.zoom_level)

        start_obj_x, start_obj_y = self.drag_object_start_pos
        new_x = start_obj_x + delta_x
        new_y = start_obj_y + delta_y

        start_bbox = self.drag_begin_state['bbox']
        w = start_bbox[2] - start_bbox[0]
        h = start_bbox[3] - start_bbox[1]
        snapped = self._snap_object_rect(fitz.Rect(new_x, new_y, new_x + w, new_y + h), move=True)
        delta_x += snapped.x0 - new_x
        delta_y += snapped.y0 - new_y
        new_x, new_y = snapped.x0, snapped.y0
        
        self.dragged_object.x = new_x
        self.dragged_object.y = new_y
        self.dragged_object.bbox = (new_x, new_y, new_x + w, new_y + h)
        
        if isinstance(self.dragged_object, EditableText):
            self.dragged_object.baseline = self.drag_begin_state['baseline'] + delta_y

            self.selected_text = self.dragged_object
            self.selected_image = None
            self.selected_shape = None
            self.selected_stroke = None
        
        elif isinstance(self.dragged_object, EditableImage):
            self.selected_image = self.dragged_object
            self.selected_text = None
            self.selected_shape = None
            self.selected_stroke = None
            
        elif isinstance(self.dragged_object, EditableShape):
            self.selected_shape = self.dragged_object
            self.selected_image = None
            self.selected_text = None
            self.selected_stroke = None

        elif isinstance(self.dragged_object, EditableStroke):
            start_points = self.drag_begin_state.get('points', [])
            self.dragged_object.points = [(p[0] + delta_x, p[1] + delta_y) for p in start_points]
            self.dragged_object.recalculate_bbox()
            self.selected_stroke = self.dragged_object
            self.selected_shape = None
            self.selected_image = None
            self.selected_text = None

        self.pdf_view.queue_draw()

    def _drag_past_threshold(self, offset_x, offset_y):
        """A click with a slight hand movement must not move, resize, rotate or snap the object."""
        if not getattr(self, '_drag_moved', False):
            if math.hypot(offset_x, offset_y) < DRAG_THRESHOLD:
                return False
            self._drag_moved = True
        return True

    def _handle_rotate_update(self, gesture, offset_x, offset_y):
        """Handle dynamic rotation update while dragging the stalk rotation handle."""
        if not self.dragged_object or not hasattr(self, 'rotate_center') or not hasattr(self, 'rotate_pointer_start_angle'):
            return

        current_x = self.drag_start_pos[0] + offset_x
        current_y = self.drag_start_pos[1] + offset_y
        cx, cy = self.rotate_center

        cur_dx = current_x - cx
        cur_dy = current_y - cy

        if math.hypot(cur_dx, cur_dy) < 2.0:
            return

        cur_pointer_angle = math.degrees(math.atan2(cur_dy, cur_dx))
        angle_delta = cur_pointer_angle - self.rotate_pointer_start_angle
        new_rot = (self.rotate_start_angle + angle_delta) % 360.0

        # Check Shift key modifier for 15-degree increment snapping
        shift_pressed = False
        try:
            state = gesture.get_current_event_state()
            shift_pressed = bool(state & Gdk.ModifierType.SHIFT_MASK)
        except Exception:
            pass

        if shift_pressed:
            new_rot = round(new_rot / 15.0) * 15.0 % 360.0
        else:
            new_rot = round(new_rot, 1) % 360.0

        if hasattr(self.dragged_object, 'set_rotation'):
            self.dragged_object.set_rotation(new_rot)
        else:
            self.dragged_object.rotation = new_rot

        if hasattr(self, 'status_label') and self.status_label:
            self.status_label.set_text(_("status_object_rotation", f"{new_rot:.1f}°"))

        if hasattr(self, '_update_rotation_controls'):
            self._update_rotation_controls(self.dragged_object)

        self.pdf_view.queue_draw()

    def _snap_object_rect(self, rect, move=False, edges=()):
        """Line a dragged object up with other objects, form fields and the page centre."""
        self._object_guides = []
        from .form_builder import style, snap_rect
        if not self.doc or not style()['snap']:
            return rect
        page = self.doc[self.current_page_index]
        dragged = self.dragged_object
        if page.rotation or getattr(dragged, 'rotation', 0):
            return rect
        table_id = getattr(dragged, 'table_id', None)
        others = [obj.bbox for obj in (*self.editable_texts, *self.editable_shapes, *self.editable_images,
                                        *self.editable_strokes)
                  if obj is not dragged and obj.page_number == self.current_page_index
                  and not (table_id and getattr(obj, 'table_id', None) == table_id)]
        from .document_features import list_form_fields
        others += [field['rect'] for field in list_form_fields(self.doc, [self.current_page_index])]
        snapped, self._object_guides = snap_rect(rect, others, 5 / self.zoom_level, edges=edges, move=move,
                                                 page=page.rect)
        return snapped

    def _handle_resize_update(self, offset_x, offset_y):
        """Handle resize update."""
        if not self.resize_handle or not self.resize_start_bbox or not self.dragged_object:
            return

        x1, y1, x2, y2 = self.resize_start_bbox
        delta_x, delta_y = self._visual_to_unrotated_delta(offset_x / self.zoom_level, offset_y / self.zoom_level)

        if isinstance(self.dragged_object,EditableText):
            original=self.drag_begin_state
            width,height=x2-x1,y2-y1
            if width<=0 or height<=0:
                return
            rotation=original.get('rotation',0)
            delta_x,delta_y=pdf_handler.rotate_point(delta_x,delta_y,0,0,-rotation)
            sx=-1 if 'w' in self.resize_handle else 1
            sy=-1 if 'n' in self.resize_handle else 1
            scale=1+(sx*delta_x*width+sy*delta_y*height)/(width*width+height*height)
            scale=max(scale,10/width,10/height,3/original['font_size'])
            nx=x2-width*scale if sx<0 else x1
            ny=y2-height*scale if sy<0 else y1
            anchor_x=x2 if sx<0 else x1
            anchor_y=y2 if sy<0 else y1
            old_anchor=pdf_handler.rotate_point(anchor_x,anchor_y,(x1+x2)/2,(y1+y2)/2,rotation)
            new_anchor=pdf_handler.rotate_point(anchor_x,anchor_y,nx+width*scale/2,ny+height*scale/2,rotation)
            nx+=old_anchor[0]-new_anchor[0]
            ny+=old_anchor[1]-new_anchor[1]
            obj=self.dragged_object
            obj.bbox=(nx,ny,nx+width*scale,ny+height*scale)
            obj.x,obj.y=nx,ny
            obj.font_size=original['font_size']*scale
            obj.baseline=ny+(original['baseline']-y1)*scale
            self.pdf_view.queue_draw()
            return

        new_x1, new_y1, new_x2, new_y2 = x1, y1, x2, y2

        if "w" in self.resize_handle:  # Left handles
            new_x1 = x1 + delta_x
        if "e" in self.resize_handle:  # Right handles
            new_x2 = x2 + delta_x
        if "n" in self.resize_handle:  # Top handles
            new_y1 = y1 + delta_y
        if "s" in self.resize_handle:  # Bottom handles
            new_y2 = y2 + delta_y

        edges = [edge for letter, edge in (('w', 'x0'), ('e', 'x1'), ('n', 'y0'), ('s', 'y1'))
                 if letter in self.resize_handle]
        snapped = self._snap_object_rect(fitz.Rect(new_x1, new_y1, new_x2, new_y2), edges=edges)
        new_x1, new_y1, new_x2, new_y2 = snapped.x0, snapped.y0, snapped.x1, snapped.y1
        min_size = 10
        if new_x2 - new_x1 < min_size:
            if "e" in self.resize_handle:
                new_x2 = new_x1 + min_size
            else:
                new_x1 = new_x2 - min_size
        if new_y2 - new_y1 < min_size:
            if "s" in self.resize_handle:
                new_y2 = new_y1 + min_size
            else:
                new_y1 = new_y2 - min_size

        if isinstance(self.dragged_object, EditableStroke):
            start_points = self.drag_begin_state.get('points', self.dragged_object.points)
            self.dragged_object.scale_to_bbox((new_x1, new_y1, new_x2, new_y2), self.resize_start_bbox, start_points)
        else:
            self.dragged_object.bbox = (new_x1, new_y1, new_x2, new_y2)
            self.dragged_object.x = new_x1
            self.dragged_object.y = new_y1

        self.pdf_view.queue_draw()

    def on_drag_end(self, gesture, offset_x, offset_y):
        """Finalize canvas drag interaction, committing created or modified objects to undo history."""
        if getattr(self,'_form_handle_drag',False):
            self._form_handle_drag=False
            self.form_tools.interaction.end(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if getattr(self,'_form_drag_active',False):
            self._form_drag_active=False
            return
        if self.tool_mode=='measure' and hasattr(self,'measure_tool'):
            self.measure_tool.end_drag(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if self.tool_mode=='nodes' and getattr(self,'node_tool',None):
            self.node_tool.end(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if self.tool_mode=='certificate_signature':
            placement=getattr(self,'_certificate_placement',None)
            if placement:placement.end(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        if getattr(self, '_pan_start', None) is not None:
            self.on_drag_update(gesture, offset_x, offset_y)
            self._pan_start = None
            self._update_cursor_for_tool()
            if hasattr(self, 'continuous_view') and self.continuous_view.enabled:
                self.continuous_view.scrolled(self.pdf_scroll.get_vadjustment())
            return
        if hasattr(self,'stamp_interaction') and self.stamp_interaction.drag:
            self.stamp_interaction.end(offset_x/self.zoom_level,offset_y/self.zoom_level)
            return
        moved = abs(offset_x) > 2 or abs(offset_y) > 2
        if getattr(self, '_erase_drag', None):
            (sx, sy), (ex, ey) = self._erase_drag
            self._erase_drag = None
            if moved:
                # A small margin lets a straight sweep along a line act like a brush.
                rect = fitz.Rect(sx, sy, ex, ey).normalize() + (-1.5, -1.5, 1.5, 1.5)
                self._erase_highlights([rect * self.doc[self.current_page_index].derotation_matrix])
            else:
                self._delete_highlight_at(sx, sy)
            self.pdf_view.queue_draw()
            return
        if self.view_mode and not self.dragging_to_create:
            if self.view_drag_active and moved:
                self.on_drag_update(gesture,offset_x,offset_y)
            self.view_drag_active=False
            self._update_ui_state()
            self.pdf_view.queue_draw()
            return

        if self.tool_mode in ('form_create','form_reposition') and getattr(self.form_tools,'drag_start',None):
            self.form_tools.end_drag(offset_x/self.zoom_level,offset_y/self.zoom_level)
            self.form_tools.drag_start=None
            return

        if self.dragging_to_create:
            self.dragging_to_create = False
            if getattr(self, 'temp_stroke', None) is not None:
                stroke_to_add = self.temp_stroke
                self.temp_stroke = None
                if getattr(stroke_to_add, 'tool_type', None) == 'line':
                    (ax, ay), (bx, by) = stroke_to_add.points[0], stroke_to_add.points[-1]
                    if math.hypot(bx - ax, by - ay) < 3:
                        stroke_to_add.points = []
                if stroke_to_add.points:
                    stroke_to_add.recalculate_bbox()
                    stroke_to_add.original_bbox = stroke_to_add.bbox
                    self.selected_stroke = stroke_to_add
                    self.selected_text = None
                    self.selected_image = None
                    self.selected_shape = None

                    command = AddObjectCommand(self, stroke_to_add)
                    command.execute()
                    self.undo_manager.add_command(command)
                    self.document_modified = True
                    self._refresh_thumbnail(self.current_page_index)
                self.pdf_view.queue_draw()
                self._update_ui_state()
                return

            if self.temp_shape:
                x1, y1, x2, y2 = self.temp_shape.bbox
                if (x2 - x1) < 10 or (y2 - y1) < 10:
                    if self.temp_shape.shape_type in (EditableShape.SHAPE_CHECKMARK, EditableShape.SHAPE_CROSS):
                        cx, cy = self.drag_start_page_pos
                        size = 24.0
                        self.temp_shape.bbox = (cx - size / 2.0, cy - size / 2.0, cx + size / 2.0, cy + size / 2.0)
                        self.temp_shape.x = self.temp_shape.bbox[0]
                        self.temp_shape.y = self.temp_shape.bbox[1]
                    else:
                        self.temp_shape = None
                        self.pdf_view.queue_draw()
                        return
                
                self.shape_tools.apply_defaults(self.temp_shape)
                self.temp_shape.original_bbox = self.temp_shape.bbox
                self.selected_shape = self.temp_shape
                self.selected_text = None
                self.selected_image = None
                self.selected_stroke = None
                
                command = AddObjectCommand(self, self.temp_shape)
                command.execute()
                self.undo_manager.add_command(command)
                self.document_modified = True
                
                self.temp_shape.is_baked = True
                self.temp_shape = None
                self._refresh_thumbnail(self.current_page_index)
                
                self.pdf_view.queue_draw()
                self._update_ui_state()
            elif self.temp_image_bbox:
                x1, y1, x2, y2 = self.temp_image_bbox
                if (x2 - x1) < 20 or (y2 - y1) < 20:
                    self.temp_image_bbox = None
                    self.pdf_view.queue_draw()
                    return
                
                filter_img = Gtk.FileFilter(name=_("image_filter_label"))
                for mime in ["image/png", "image/jpeg", "image/gif", "image/bmp"]:
                    filter_img.add_mime_type(mime)

                def on_image_selected(file):
                    if file:
                        try:
                            with open(file.get_path(), 'rb') as f:
                                image_bytes = f.read()

                            image_obj = EditableImage(
                                bbox=self.temp_image_bbox,
                                page_number=self.current_page_index,
                                xref=None,
                                image_bytes=image_bytes,
                                is_new=True
                            )

                            self.selected_image = image_obj
                            self.selected_text = None
                            self.selected_shape = None
                            command = AddObjectCommand(self, image_obj)
                            command.execute()
                            self.undo_manager.add_command(command)
                            self.document_modified = True
                            self.pdf_view.queue_draw()
                            self._update_ui_state()
                        except Exception as e:
                            show_error_dialog(self, _("err_adding_image_dialog", e), _("err_title"))

                    self.temp_image_bbox = None
                    self.pdf_view.queue_draw()

                show_open_file_dialog(
                    self,
                    _("image_select_title"),
                    filters=[filter_img],
                    callback=on_image_selected
                )
            return
        
        if getattr(self, 'table_drag_state', None) is not None:
            self._clear_table_preview()
            table = self.table_drag_table
            old_states = self.table_drag_state
            new_states = [copy.deepcopy(obj.__dict__) for obj in table.objects]
            self.table_drag_state = None
            self.table_drag_table = None
            self.table_resize_handle = None
            if any(old['bbox'] != new['bbox'] for old, new in zip(old_states, new_states)):
                command = EditTableCommand(self, table.objects, old_states, new_states)
                command.execute()
                self.undo_manager.add_command(command)
            self.pdf_view.queue_draw()
            return

        if not self.dragged_object or not hasattr(self, 'drag_begin_state'):
            if self.dragged_object:
                self.dragged_object = None
            self.resize_handle = None
            self.resize_start_bbox = None
            if hasattr(self, 'rotate_center'):
                del self.rotate_center
            if hasattr(self, 'rotate_start_angle'):
                del self.rotate_start_angle
            if hasattr(self, 'rotate_pointer_start_angle'):
                del self.rotate_pointer_start_angle
            self.pdf_view.queue_draw()
            return

        self.commit_pending_format_change()

        old_properties = self.drag_begin_state
        
        new_properties = copy.deepcopy(self.dragged_object.__dict__)

        dragged_obj_ref = self.dragged_object
        self.dragged_object = None
        self.resize_handle = None
        self.resize_start_bbox = None
        del self.drag_begin_state
        if hasattr(self, 'rotate_center'):
            del self.rotate_center
        if hasattr(self, 'rotate_start_angle'):
            del self.rotate_start_angle
        if hasattr(self, 'rotate_pointer_start_angle'):
            del self.rotate_pointer_start_angle
        
        rot_changed = (old_properties.get('rotation', 0.0) != new_properties.get('rotation', 0.0))
        if not getattr(self, '_drag_moved', False) or (
                old_properties.get('bbox') == new_properties.get('bbox') and not rot_changed):
            self.pdf_view.queue_draw()
            return

        if rot_changed:
            old_rot = old_properties.get('rotation', 0.0)
            new_rot = new_properties.get('rotation', 0.0)
            dragged_obj_ref.set_rotation(old_rot)
            command = RotateObjectCommand(self, dragged_obj_ref, old_rot, new_rot)
            command.execute()
            self.undo_manager.add_command(command)
        else:
            logger.debug(_("dbg_creating_drag_command"))
            command = EditObjectCommand(self, dragged_obj_ref, old_properties, new_properties)
            command.execute()
            self.undo_manager.add_command(command)

        if isinstance(dragged_obj_ref, EditableText):
            self.selected_text = dragged_obj_ref
            self.selected_image = None
            self.selected_shape = None
            self.selected_stroke = None
            self.pending_format_change_obj = self.selected_text
            self.before_format_change_state = copy.deepcopy(self.selected_text.__dict__)
            self._update_text_format_controls(self.selected_text)
        elif isinstance(dragged_obj_ref, EditableImage):
            self.selected_image = dragged_obj_ref
            self.selected_text = None
            self.selected_shape = None
            self.selected_stroke = None
        elif isinstance(dragged_obj_ref, EditableShape):
            self.selected_shape = dragged_obj_ref
            self.selected_text = None
            self.selected_image = None
            self.selected_stroke = None
        elif isinstance(dragged_obj_ref, EditableStroke):
            self.selected_stroke = dragged_obj_ref
            self.selected_shape = None
            self.selected_text = None
            self.selected_image = None

        self._update_ui_state()
        self.pdf_view.queue_draw()

    def insert_symbol_or_emoji(self, symbol, is_emoji=False):
        """Insert symbol into active text block or create full-color emoji stamp on document."""
        if getattr(self, 'inline_editor_widget', None) is not None and getattr(self, 'inline_text_view', None) is not None:
            buf = self.inline_text_view.get_buffer()
            buf.insert_at_cursor(symbol)
            self.inline_text_view.grab_focus()
            return

        if is_emoji and self.doc and self.current_pdf_page_width > 0:
            try:
                png_bytes = render_emoji_to_png_bytes(symbol, size=96)
                page_w_unzoomed = self.current_pdf_page_width / self.zoom_level
                page_h_unzoomed = self.current_pdf_page_height / self.zoom_level
                stamp_size = 32.0
                stamp_x = max(20.0, (page_w_unzoomed - stamp_size) / 2.0)
                stamp_y = max(20.0, (page_h_unzoomed - stamp_size) / 2.0)

                emoji_obj = EditableImage(
                    bbox=(stamp_x, stamp_y, stamp_x + stamp_size, stamp_y + stamp_size),
                    page_number=self.current_page_index,
                    xref=None,
                    image_bytes=png_bytes,
                    is_new=True
                )
                self.selected_image = emoji_obj
                self.selected_text = None
                self.selected_shape = None
                if hasattr(self, 'selected_stroke'):
                    self.selected_stroke = None

                command = AddObjectCommand(self, emoji_obj)
                command.execute()
                self.undo_manager.add_command(command)
                self.document_modified = True
                self._refresh_thumbnail(self.current_page_index)
                self.pdf_view.queue_draw()
                self._update_ui_state()
                self.status_label.set_text(f"Added emoji stamp: {symbol}")
                return
            except Exception as e:
                print(f"Error creating emoji stamp: {e}")

        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(symbol)

        self.status_label.set_text(_("symbol_copied", symbol))

    def _on_quick_guide_activated(self, action, param):
        """Display the interactive quick user guide dialog."""
        dialog = QuickGuideDialog(self)
        dialog.present()

    def _update_undo_redo_buttons(self, *args):
        """Sync undo/redo toolbar button sensitivity with UndoManager stacks."""
        if hasattr(self,'image_tools'):self.image_tools.sync()
        editable=bool(self.doc and getattr(self._active_session,'can_edit',True))
        can_fill=bool(self.doc and getattr(self.doc,'editor_can_fill_forms',getattr(self._active_session,'can_edit',True)))
        def allowed(stack):
            return bool(stack) and (editable or (can_fill and getattr(stack[-1],'form_fill_allowed',False))) and (not self.view_mode or getattr(stack[-1],'view_mode_allowed',False))
        undo_enabled=allowed(self.undo_manager.undo_stack)
        redo_enabled=allowed(self.undo_manager.redo_stack)
        self.undo_button.set_sensitive(undo_enabled)
        self.redo_button.set_sensitive(redo_enabled)
        if self.lookup_action('undo'):
            self.lookup_action('undo').set_enabled(undo_enabled)
        if self.lookup_action('redo'):
            self.lookup_action('redo').set_enabled(redo_enabled)

    def _refresh_thumbnail(self, page_index):
        """Regenerate and replace the thumbnail pixbuf for a modified page."""
        if not self.doc or not (0 <= page_index < pdf_handler.get_page_count(self.doc)):
            return
        try:
            thumb = pdf_handler.generate_thumbnail(self.doc, page_index, target_width=240)
            if thumb:
                n = self.pages_model.get_n_items()
                for i in range(n):
                    item = self.pages_model.get_item(i)
                    if item and item.index == page_index:
                        from .models import PdfPage
                        new_item = PdfPage(page_index, thumb)
                        self.pages_model.splice(i, 1, [new_item])
                        GLib.idle_add(self._sync_thumbnail_selection)
                        break
        except Exception as e:
            print(f"Warning: Could not refresh thumbnail for page {page_index + 1}: {e}")
    
    def commit_pending_format_change(self):
        """Record pending text/shape style modifications into undo history."""
        tools=getattr(self,'image_tools',None)
        if tools and tools.panel and not tools.panel.committing:tools.panel.finish()
        if self.pending_format_change_obj and self.before_format_change_state:
            current_state = copy.deepcopy(self.pending_format_change_obj.__dict__)
            
            if self.before_format_change_state != current_state:
                logger.debug(_("dbg_format_change_saved"))
                command = EditObjectCommand(self, self.pending_format_change_obj, self.before_format_change_state, current_state)
                command.execute()
                self.undo_manager.add_command(command)

        self.pending_format_change_obj = None
        self.before_format_change_state = None

    def on_new_clicked(self, widget=None):
        """Prompt user for dimensions and initialize a new empty PDF document in a tab."""
        def on_create(width_pt, height_pt, num_pages):
            doc, error_msg = pdf_handler.create_new_pdf(width=width_pt, height=height_pt, num_pages=num_pages)

            if error_msg:
                show_error_dialog(self, error_msg)
                return

            if doc:
                self.open_generated_document(doc)
                self.status_label.set_text(_("status_new_doc_created"))

        show_new_document_dialog(self, on_create)

    def _offer_recovery(self):
        """Offer documents autosaved by a session that ended unexpectedly."""
        entries = session_memory.pending_recoveries()
        if not entries:
            return False
        from .dialogs import alert
        names = '\n'.join(f"• {entry.get('title') or 'Untitled'}" for entry in entries[:8])

        def answer(response):
            if response == 'recover':
                import pymupdf as fitz
                for entry in entries:
                    try:
                        doc = fitz.open(entry['recovery_file'])
                    except Exception as error:
                        logger.warning('Could not open recovery file: %s', error)
                        continue
                    stem = os.path.splitext(entry.get('title') or 'document')[0]
                    self.open_generated_document(doc, suggested_name=f'{stem} (recovered).pdf')
                    self._active_session.recovered_from = entry.get('original')
            if response in ('recover', 'discard'):
                for entry in entries:
                    session_memory.discard_recovery(entry)
        alert(self, _("recovery_title"), _("recovery_body", names),
              [('later', _("recovery_later"), None), ('discard', _("recovery_discard"), 'destructive'),
               ('recover', _("recovery_recover"), 'suggested')], 'recover', 'later', answer)
        return False

    def open_generated_document(self, doc, suggested_name=None):
        """Show an in-memory document (new, converted, or generated) in a new unsaved tab."""
        if self._active_session is not None and self._active_session.doc is not None:
            sess = self.create_session()
            self.add_session(sess, switch_to=True)
        else:
            sess = self._active_session or self.create_session()
            if sess not in self.sessions:
                self.add_session(sess, switch_to=True)

        for attribute in ('editor_can_edit', 'editor_can_fill_forms', 'editor_can_copy', 'editor_can_print'):
            if not hasattr(doc, attribute):
                setattr(doc, attribute, True)
        if not hasattr(doc, 'editor_password'):
            doc.editor_password = ''
        sess.doc = doc
        sess.pdf_path = None
        sess.original_file_path = None
        sess.suggested_name = suggested_name
        sess.current_page_index = 0
        sess.is_modified = True
        sess.fit_on_load = True
        sess.fit_to_view = True

        self.set_active_session(sess)
        self.set_title(f"{constants.APP_NAME} - {sess.display_title}")
        self._update_tab_title(sess)
        if hasattr(self, 'stack') and self.stack:
            self.stack.set_visible_child_name("editor")
        if hasattr(self, 'tab_bar') and self.tab_bar:
            self.tab_bar.set_visible(True)

        self._load_thumbnails(sess)
        self._load_page(0)
        self._update_ui_state()
        return sess

    def do_close_request(self):
        """Prompt to save unsaved changes in all open tabs before closing the window."""
        modified_sessions = [s for s in self.sessions if s and s.is_modified and s.doc is not None]
        for s in modified_sessions:
            if self.check_unsaved_changes(s):
                return True
        for s in list(self.sessions):
            self.remove_session(s)
        return False

    def on_stroke_width_scroll(self, controller, dx, dy):
        """Adjust stroke width of selected shape using mouse wheel scroll."""
        if not self.selected_shape:
            return False
        
        dy_abs = abs(dy)
        increment = 0.5 if dy > 0 else -0.5
        
        new_width = max(0.5, self.selected_shape.stroke_width + increment)
        self.selected_shape.stroke_width = round(new_width, 1)
        
        self.pdf_view.queue_draw()
        return True

    def _toggle_view_edit_mode(self, button=None):
        """Toggle between read-only text-selection mode and interactive object edit mode."""
        self._cancel_table_drag()
        if hasattr(self,"stamp_interaction"):
            self.stamp_interaction.cancel()
        if hasattr(self,"form_tools"):
            self.form_tools.cancel_drag()
        self.view_mode = not self.view_mode
        if self.view_mode:
            self._apply_and_hide_editor()
            self.selected_text = None
            self.selected_image = None
            self.selected_shape = None
            self.selected_table = None
            self.selected_stroke = None
            self._pending_signature = None
            self.tool_mode = "select"
        else:
            self.view_sel_start = None
            self.view_sel_rect = None
            self.view_selected_text = ""
        self._update_ui_state()
        self.pdf_view.queue_draw()

    def _on_highlight_color_changed(self, button):
        rgba = button.get_rgba()
        self.highlight_color_rgba = rgba
        self.highlighter_color = (rgba.red, rgba.green, rgba.blue)
        if self.tool_mode == "highlighter":
            self._update_stroke_format_controls(None)

    def on_highlight_clicked(self, button):
        """Add a highlight annotation over selected text or canvas selection rectangle,
        or activate freehand highlighter drawing mode if no selection exists."""
        if not self.doc or not getattr(self._active_session,'can_edit',True):
            return
        has_text_sel = False
        if self.view_mode and self.view_sel_rect:
            has_text_sel = True
        elif not self.view_mode and self.selected_text and self.selected_text.bbox:
            has_text_sel = True

        if has_text_sel:
            rgba = self.highlight_color_rgba
            color = (rgba.red, rgba.green, rgba.blue)
            
            target_rect = None
            is_visual = False
            rot = 0.0
            if self.view_mode and self.view_sel_rect:
                target_rect = self.view_sel_rect
                is_visual = True
            elif not self.view_mode and self.selected_text and self.selected_text.bbox:
                if getattr(self, 'word_selection_mode', False) and hasattr(self, 'selected_word_start_char'):
                    target_rect = text_geometry.selection_bounds(
                        self.doc, self.selected_text, self.selected_word_start_char, self.selected_word_end_char)
                else:
                    target_rect = self.selected_text.bbox
                is_visual = False
                rot = getattr(self.selected_text, 'rotation', 0.0)

            if not target_rect:
                return

            def add_highlight():
                if self.selected_text and not is_visual:
                    start = self.selected_word_start_char if getattr(self, 'word_selection_mode', False) else 0
                    end = self.selected_word_end_char if getattr(self, 'word_selection_mode', False) else len(self.selected_text.text)
                    quads = text_geometry.selection_quads(self.doc,self.selected_text,start,end)
                else:
                    page = self.doc[self.current_page_index]
                    rect = fitz.Rect(target_rect)
                    if is_visual:
                        rect = rect * page.derotation_matrix
                    visual_quads=getattr(self._active_session,'view_selection_quads',[]) if is_visual else []
                    quads=[quad*page.derotation_matrix for quad in visual_quads]
                    if not quads:
                        quads=[fitz.Rect(word[:4]).quad for word in page.get_text('words')
                               if fitz.Rect(word[:4]).intersects(rect)]
                    if not quads:
                        quads = [rect.quad]
                page = self.doc[self.current_page_index]
                highlight_tools.add(page, quads, color, self.highlighter_opacity)
                self.doc._reset_page_refs()
            success = self._mutate_document(add_highlight,allow_view=True)
            err = ''
            if success:
                pdf_handler.invalidate_page_cache(self.doc, self.current_page_index)
                self.document_modified = True
                self.view_sel_start = None
                self.view_sel_rect = None
                self.view_selected_text = ""
                self._refresh_thumbnail(self.current_page_index)
                self._update_ui_state()
                self.pdf_view.queue_draw()
            else:
                from .ui_components import show_error_dialog
                show_error_dialog(self, _("highlight_failed", err))
        else:
            if self.tool_mode == "highlighter":
                self.on_tool_selected(None,"select")
            else:
                rgba=self.highlight_color_rgba
                self.highlighter_color=(rgba.red,rgba.green,rgba.blue)
                self.on_tool_selected(None,"highlighter")

    def on_remove_highlight_clicked(self, button):
        """Erase highlights under the text selection, or toggle the highlight eraser tool."""
        if not self.doc or not getattr(self._active_session,'can_edit',True):
            return
        rects = self._highlight_selection_rects()
        if rects and self._highlights_overlap(rects):
            self._erase_highlights(rects)
            self._clear_view_selection()
        elif self.tool_mode == "erase_highlight":
            self.on_tool_selected(None, "select")
        else:
            self.on_tool_selected(None, "erase_highlight")

    def _highlight_selection_rects(self):
        """Native page rectangles covered by the current text selection."""
        if not self.doc:
            return []
        page = self.doc[self.current_page_index]
        if self.view_mode:
            quads = getattr(self._active_session, 'view_selection_quads', None) or []
            if quads:
                return [(quad * page.derotation_matrix).rect for quad in quads]
            if self.view_sel_rect:
                return [fitz.Rect(self.view_sel_rect) * page.derotation_matrix]
            return []
        text = self.selected_text
        if not text or not text.bbox:
            return []
        if getattr(self, 'word_selection_mode', False) and hasattr(self, 'selected_word_start_char'):
            quads = text_geometry.selection_quads(
                self.doc, text, self.selected_word_start_char, self.selected_word_end_char)
            if quads:
                return [quad.rect for quad in quads]
        return [fitz.Rect(text.bbox)]

    def _clear_view_selection(self):
        if self.view_mode:
            self.view_sel_start = None
            self.view_sel_rect = None
            self.view_selected_text = ""
            self._active_session.view_selection_quads = []
            self._update_ui_state()
            self.pdf_view.queue_draw()

    def _highlight_strokes(self):
        """Freehand highlighter strokes on the current page, topmost first."""
        return [stroke for stroke in reversed(getattr(self, 'editable_strokes', []))
                if stroke.page_number == self.current_page_index and stroke.points
                and getattr(stroke, 'tool_type', None) == EditableStroke.TOOL_HIGHLIGHTER]

    def _stroke_split(self, stroke, rects):
        if getattr(stroke, 'rotation', 0.0) % 360.0:
            # Rotated strokes are erased whole when touched.
            bbox = fitz.Rect(stroke.bbox)
            return [] if any(bbox.intersects(rect) for rect in rects) else None
        reach = stroke.stroke_width / 2.0
        return highlight_tools.split_stroke(stroke.points, [fitz.Rect(rect) + (0, -reach, 0, reach) for rect in rects])

    def _highlights_overlap(self, rects):
        return (highlight_tools.overlaps(self.doc, self.current_page_index, rects)
                or any(self._stroke_split(stroke, rects) is not None for stroke in self._highlight_strokes()))

    def _run_object_commands(self, commands):
        command = CompositeCommand(self, commands)
        command.execute()
        self.undo_manager.add_command(command)
        self.selected_stroke = None
        self.document_modified = True
        self._refresh_thumbnail(self.current_page_index)
        self._update_ui_state()
        self.pdf_view.queue_draw()

    def _erase_highlight_strokes(self, rects):
        """Erase the parts of freehand highlighter strokes under ``rects``."""
        commands = []
        for stroke in self._highlight_strokes():
            runs = self._stroke_split(stroke, rects)
            if runs is None:
                continue
            commands.append(DeleteObjectCommand(self, stroke))
            for run in runs:
                piece = copy.deepcopy(stroke)
                piece.points = run
                piece.is_new = True
                piece.is_baked = True
                piece._ghost_redacted = False
                piece.recalculate_bbox()
                piece.original_bbox = None
                commands.append(AddObjectCommand(self, piece))
        if commands:
            self._run_object_commands(commands)
        return sum(isinstance(command, DeleteObjectCommand) for command in commands)

    def _erase_highlights(self, rects):
        """Undoably erase highlighted areas under native ``rects``."""
        if not self.doc or not rects:
            return 0
        page = self.current_page_index
        removed = self._erase_highlight_strokes(rects)
        # Skip no-op mutations so they do not create empty undo steps.
        if highlight_tools.overlaps(self.doc, page, rects):
            counts = []
            if self._mutate_document(lambda: counts.append(highlight_tools.erase(self.doc, page, rects)),
                                     allow_view=True):
                removed += counts[-1]
        self.status_label.set_text(_("highlight_erased") if removed else _("highlight_none_here"))
        return removed

    def _delete_highlight_at(self, page_x, page_y):
        """Delete the whole highlight under a visual page point."""
        point = fitz.Point(page_x, page_y) * self.doc[self.current_page_index].derotation_matrix
        for stroke in self._highlight_strokes():
            if highlight_tools.stroke_hit(stroke.points, stroke.stroke_width, point):
                self._run_object_commands([DeleteObjectCommand(self, stroke)])
                self.status_label.set_text(_("highlight_erased"))
                return True
        hit = highlight_tools.highlight_at(self.doc, self.current_page_index, point)
        if not hit:
            self.status_label.set_text(_("highlight_none_here"))
            return False
        page = self.current_page_index
        if self._mutate_document(lambda: highlight_tools.delete(self.doc, page, hit['xref']), allow_view=True):
            self.status_label.set_text(_("highlight_erased", 1))
            return True
        return False

    def _recolor_highlight(self, xref, color):
        page = self.current_page_index
        self._mutate_document(lambda: highlight_tools.recolor(self.doc, page, xref, color), allow_view=True)

    def _append_highlight_menu(self, box, add, page_x, page_y, include_highlight=True, include_delete=True):
        """Shared highlight section of the view and edit context menus.

        ``add(label_key, callback)`` appends a menu button and returns it.
        """
        can_edit = getattr(self._active_session, 'can_edit', True)
        rects = self._highlight_selection_rects()
        if include_highlight:
            add("menu_highlight", lambda: self.on_highlight_clicked(None)).set_sensitive(can_edit and bool(rects))
        if rects:
            def erase_selection():
                self._erase_highlights(self._highlight_selection_rects())
                self._clear_view_selection()
            erase = add("remove_highlight_tip", erase_selection)
            erase.set_sensitive(can_edit and self._highlights_overlap(rects))
        point = fitz.Point(page_x, page_y) * self.doc[self.current_page_index].derotation_matrix
        hit = highlight_tools.highlight_at(self.doc, self.current_page_index, point)
        if not hit or not can_edit:
            return
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        row = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        for color in highlight_tools.COLORS:
            area = Gtk.DrawingArea(content_width=18, content_height=18)
            def draw(_area, cr, width, height, color=color):
                cr.set_source_rgb(*color)
                cr.arc(width / 2, height / 2, min(width, height) / 2 - 1, 0, 2 * math.pi)
                cr.fill_preserve()
                cr.set_source_rgba(0, 0, 0, 0.35)
                cr.set_line_width(1)
                cr.stroke()
            area.set_draw_func(draw)
            swatch = Gtk.Button(child=area, tooltip_text=_("highlight_color_tip"))
            swatch.add_css_class('flat')
            swatch.add_css_class('circular')
            if all(abs(a - b) < 0.02 for a, b in zip(color, hit['color'])):
                swatch.add_css_class('suggested-action')
            def pick(_button, color=color):
                popover = getattr(self, 'context_popover', None)
                if popover:
                    popover.popdown()
                self._recolor_highlight(hit['xref'], color)
            swatch.connect('clicked', pick)
            row.append(swatch)
        box.append(row)
        if include_delete:
            add("highlight_delete", lambda: self._delete_highlight_at(page_x, page_y))

    def _extract_word_at_position(self, text, click_pos_in_text):
        """Extract contiguous non-whitespace word and its character indices at cursor."""
        if not text or click_pos_in_text < 0 or click_pos_in_text > len(text):
            return None, 0, 0
        
        start = click_pos_in_text
        end = click_pos_in_text
        
        while start > 0 and text[start - 1] not in ' \t\n':
            start -= 1
        
        while end < len(text) and text[end] not in ' \t\n':
            end += 1
        
        return text[start:end], start, end

    def _on_middle_click(self, gesture, n_press, x, y):
        """Handle middle-click gesture to quickly select words or jump to links."""
        if not self.doc:
            return
        
        drawing_area_width = self.pdf_view.get_allocated_width()
        drawing_area_height = self.pdf_view.get_allocated_height()
        page_offset_x = max(0, (drawing_area_width - self.current_pdf_page_width) / 2)
        page_offset_y = max(0, (drawing_area_height - self.current_pdf_page_height) / 2)
        click_x_zoomed = x - page_offset_x
        click_y_zoomed = y - page_offset_y
        page_x = click_x_zoomed / self.zoom_level
        page_y = click_y_zoomed / self.zoom_level
        
        if self.view_mode:
            clicked_word = pdf_handler.get_word_at_pos(self.doc, self.current_page_index, (page_x, page_y))
            if clicked_word:
                self.selected_word = clicked_word['text']
                self.view_selected_text=clicked_word['text']
                self._active_session.view_selection_quads=[]
                self.pdf_view.grab_focus()
                self.view_sel_rect = clicked_word['bbox']
                self.word_selection_mode = True
                self.pdf_view.queue_draw()
                self._update_ui_state()
        else:
            clicked_text = self._find_text_at_pos(page_x, page_y)
            if clicked_text:
                self.selected_text = clicked_text
                pdf_word = pdf_handler.get_word_at_pos(self.doc, self.current_page_index, (page_x, page_y))
                if pdf_word:
                    self.selected_word = pdf_word['text']
                    idx = clicked_text.text.find(self.selected_word)
                    if idx != -1:
                        self.selected_word_start_char = idx
                        self.selected_word_end_char = idx + len(self.selected_word)
                        self.word_selection_mode = True
                        self.pending_format_change_obj = clicked_text
                        self.before_format_change_state = copy.deepcopy(clicked_text.__dict__)
                        self.pdf_view.queue_draw()
                        self._update_ui_state()
                else:
                    unrot_x, unrot_y = self._visual_to_unrotated_page_coords(page_x, page_y)
                    rot = getattr(clicked_text, 'rotation', 0.0) % 360.0
                    ux, uy = self._visual_to_unrotated_page_coords(page_x,page_y)
                    approx_char_pos = text_geometry.character_at_point(self.doc,clicked_text,ux,uy)

                    word, start_pos, end_pos = self._extract_word_at_position(clicked_text.text, approx_char_pos)
                    if word:
                        self.selected_word = word
                        self.selected_word_start_char = start_pos
                        self.selected_word_end_char = end_pos
                        self.word_selection_mode = True
                        self.pending_format_change_obj = clicked_text
                        self.before_format_change_state = copy.deepcopy(clicked_text.__dict__)
                        self.pdf_view.queue_draw()
                        self._update_ui_state()

    def _on_right_click(self, gesture, n_press, x, y):
        """Display context popover menu tailored to clicked text, shape, image, or empty area."""
        if not self.doc:
            return
        if not self.view_mode and not self._active_session.can_edit:
            return
        if self.inline_editor_widget is not None:
            self._commit_inline_edit()
        from .element_menu import ElementMenu
        if ElementMenu(self).open(x,y):
            return
        
        drawing_area_width = self.pdf_view.get_allocated_width()
        drawing_area_height = self.pdf_view.get_allocated_height()
        page_offset_x = max(0, (drawing_area_width - self.current_pdf_page_width) / 2)
        page_offset_y = max(0, (drawing_area_height - self.current_pdf_page_height) / 2)
        click_x_zoomed = x - page_offset_x
        click_y_zoomed = y - page_offset_y
        page_x = click_x_zoomed / self.zoom_level
        page_y = click_y_zoomed / self.zoom_level
        
        modifiers = Gtk.EventController.get_current_event_state(gesture)
        ctrl_pressed = bool(modifiers & Gdk.ModifierType.CONTROL_MASK)

        if self.view_mode:
            if not self.view_sel_rect or not (self.view_sel_rect[0] <= page_x <= self.view_sel_rect[2] and self.view_sel_rect[1] <= page_y <= self.view_sel_rect[3]):
                clicked_word = pdf_handler.get_word_at_pos(self.doc, self.current_page_index, (page_x, page_y))
                if clicked_word:
                    self._active_session.view_selection_quads=[]
                    self.view_sel_rect = clicked_word['bbox']
                    self.view_selected_text = clicked_word['text']
                    self.pdf_view.queue_draw()
                else:
                    point = fitz.Point(page_x, page_y) * self.doc[self.current_page_index].derotation_matrix
                    if not highlight_tools.highlight_at(self.doc, self.current_page_index, point):
                        return
                    self._clear_view_selection()
            
            if ctrl_pressed and self.view_selected_text:
                import webbrowser
                match = re.search(r'https?://[^\s]+', self.view_selected_text)
                if match:
                    webbrowser.open(match.group(0))
                return
                    
            popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            popover_box.set_margin_start(6); popover_box.set_margin_end(6)
            popover_box.set_margin_top(6); popover_box.set_margin_bottom(6)
            
            btn_copy = Gtk.Button(label=_("btn_copy_text"))
            btn_copy.set_sensitive(getattr(self._active_session,'can_copy',True))
            btn_copy.connect("clicked", lambda b: self._handle_context_action("copy_view", None, x, y))
            popover_box.append(btn_copy)
            
            def add(label, callback):
                button = Gtk.Button(label=_(label))
                def activate(_button):
                    popover = getattr(self, 'context_popover', None)
                    if popover:
                        popover.popdown()
                    callback()
                button.connect("clicked", activate)
                popover_box.append(button)
                return button
            self._append_highlight_menu(popover_box, add, page_x, page_y)

            popover=getattr(self,'context_popover',None)
            if popover and popover.get_parent() is self.pdf_view:
                popover.popdown()
                focus=self.get_focus()
                if focus and (focus is popover or focus.is_ancestor(popover)):
                    self.set_focus(None)
            else:
                popover=Gtk.Popover(autohide=True,has_arrow=True)
                popover.set_parent(self.pdf_view)
            self.context_popover=popover
            popover.set_child(popover_box)
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            self.context_popover.set_pointing_to(rect)
            self.context_popover.popup()
            return

        clicked_text = self._find_text_at_pos(page_x, page_y)
        clicked_shape = self._find_shape_at_pos(page_x, page_y)
        clicked_image = self._find_image_at_pos(page_x, page_y)
        
        popover_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        popover_box.set_margin_start(6); popover_box.set_margin_end(6)
        popover_box.set_margin_top(6); popover_box.set_margin_bottom(6)
        
        if clicked_text:
            self.selected_text = clicked_text
            self.selected_shape = None
            self.selected_image = None
            
            btn_copy = Gtk.Button(label=_("btn_copy"))
            def on_copy_clicked(b):
                if getattr(self, 'word_selection_mode', False) and hasattr(self, 'selected_word'):
                    self.get_clipboard().set(self.selected_word)
                else:
                    self.get_clipboard().set(clicked_text.text)
                if hasattr(self, 'context_popover') and self.context_popover:
                    self.context_popover.popdown()
            btn_copy.connect("clicked", on_copy_clicked)
            popover_box.append(btn_copy)
            
            btn_paste = Gtk.Button(label=_("btn_paste"))
            btn_paste.set_sensitive(False)
            popover_box.append(btn_paste)
            popover_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            
            btn_bold = Gtk.Button(label=_("bold_tip"))
            btn_italic = Gtk.Button(label=_("italic_tip"))
            btn_underline = Gtk.Button(label=_("underline_tip"))
            btn_strikethrough = Gtk.Button(label=_("strikethrough_tip"))
            def on_bold_clicked(b):
                self._toggle_text_bold(clicked_text)
            def on_italic_clicked(b):
                self._toggle_text_italic(clicked_text)
            def on_underline_clicked(b):
                self._toggle_text_underline(clicked_text)
            def on_strikethrough_clicked(b):
                self._toggle_text_strikethrough(clicked_text)
            btn_bold.connect("clicked", on_bold_clicked)
            btn_italic.connect("clicked", on_italic_clicked)
            btn_underline.connect("clicked", on_underline_clicked)
            btn_strikethrough.connect("clicked", on_strikethrough_clicked)
            popover_box.append(btn_bold)
            popover_box.append(btn_italic)
            popover_box.append(btn_underline)
            popover_box.append(btn_strikethrough)
            popover_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

            align_popover_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            align_popover_box.add_css_class("linked")
            btn_al_l = Gtk.Button(icon_name="format-justify-left-symbolic")
            btn_al_l.set_tooltip_text(_("align_left_tip"))
            btn_al_l.connect("clicked", lambda b: self._set_text_alignment(clicked_text, "left"))
            align_popover_box.append(btn_al_l)

            btn_al_c = Gtk.Button(icon_name="format-justify-center-symbolic")
            btn_al_c.set_tooltip_text(_("align_center_tip"))
            btn_al_c.connect("clicked", lambda b: self._set_text_alignment(clicked_text, "center"))
            align_popover_box.append(btn_al_c)

            btn_al_r = Gtk.Button(icon_name="format-justify-right-symbolic")
            btn_al_r.set_tooltip_text(_("align_right_tip"))
            btn_al_r.connect("clicked", lambda b: self._set_text_alignment(clicked_text, "right"))
            align_popover_box.append(btn_al_r)

            btn_al_j = Gtk.Button(icon_name="format-justify-fill-symbolic")
            btn_al_j.set_tooltip_text(_("align_justify_tip"))
            btn_al_j.connect("clicked", lambda b: self._set_text_alignment(clicked_text, "justify"))
            align_popover_box.append(btn_al_j)
            popover_box.append(align_popover_box)
            popover_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            
            btn_hl = Gtk.Button(label=_("menu_highlight"))
            btn_hl.connect("clicked", lambda b: self._handle_context_action("highlight_edit", clicked_text, x, y))
            popover_box.append(btn_hl)
            
            btn_rm_hl = Gtk.Button(label=_("menu_remove_highlight"))
            btn_rm_hl.connect("clicked", lambda b: self._handle_context_action("remove_highlight", clicked_text, x, y))
            popover_box.append(btn_rm_hl)
            
            popover_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
            btn_edit = Gtk.Button(label=_("menu_edit_text"))
            btn_edit.connect("clicked", lambda b: self._handle_context_action("edit_text", clicked_text, x, y))
            popover_box.append(btn_edit)
            
            btn_del = Gtk.Button(label=_("delete_confirm"))
            btn_del.add_css_class("destructive-action")
            def on_delete_text(b):
                self._handle_delete_with_confirmation(clicked_text, "delete_text_confirm")
            btn_del.connect("clicked", on_delete_text)
            popover_box.append(btn_del)
            
        elif clicked_shape:
            self.selected_shape = clicked_shape
            self.selected_text = None
            self.selected_image = None
            
            btn_del = Gtk.Button(label=_("menu_delete_shape"))
            btn_del.add_css_class("destructive-action")
            def on_delete_shape(b):
                self._handle_delete_with_confirmation(clicked_shape, "delete_shape_confirm")
            btn_del.connect("clicked", on_delete_shape)
            popover_box.append(btn_del)
            
        elif clicked_image:
            self.selected_image = clicked_image
            self.selected_text = None
            self.selected_shape = None
            
            btn_del = Gtk.Button(label=_("menu_delete_image"))
            btn_del.add_css_class("destructive-action")
            def on_delete_image(b):
                self._handle_delete_with_confirmation(clicked_image, "delete_image_confirm")
            btn_del.connect("clicked", on_delete_image)
            popover_box.append(btn_del)
            
        else:
            popover_box_empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            popover_box_empty.set_margin_start(6); popover_box_empty.set_margin_end(6)
            popover_box_empty.set_margin_top(6); popover_box_empty.set_margin_bottom(6)
            
            btn_paste_new = Gtk.Button(label=_("btn_paste_new"))
            btn_paste_new.connect("clicked", lambda b: self._handle_context_action("paste_new_text", (page_x, page_y), x, y))
            popover_box_empty.append(btn_paste_new)
            btn_paste_table = Gtk.Button(label=_("table_paste"))
            btn_paste_table.connect('clicked', lambda b: (self.context_popover.popdown(), self.on_paste_table()))
            popover_box_empty.append(btn_paste_table)
            
            if hasattr(self, 'context_popover') and self.context_popover:
                self.context_popover.popdown()
                
            self.context_popover = Gtk.Popover(autohide=True, has_arrow=True)
            self.context_popover.set_child(popover_box_empty)
            self.context_popover.set_parent(self.pdf_view)
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            self.context_popover.set_pointing_to(rect)
            self.context_popover.set_position(Gtk.PositionType.RIGHT)
            self.context_popover.popup()
            return
            
        self._update_ui_state()
        self.pdf_view.queue_draw()
        
        self.context_popover = Gtk.Popover(autohide=True, has_arrow=True)
        self.context_popover.set_child(popover_box)
        self.context_popover.set_parent(self.pdf_view)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.context_popover.set_pointing_to(rect)
        self.context_popover.set_position(Gtk.PositionType.RIGHT)
        self.context_popover.popup()

    def _handle_context_action(self, action, obj, x, y):
        """Execute actions dispatched from context menu popovers."""
        if hasattr(self, 'context_popover'):
            self.context_popover.popdown()
            
        if action == "edit_text":
            self._show_inline_editor(obj, click_x=x, click_y=y)
        elif action == "delete":
            self.selected_text = obj if type(obj).__name__ == 'EditableText' else None
            self.selected_shape = obj if type(obj).__name__ == 'EditableShape' else None
            if self.selected_text or self.selected_shape:
                obj_to_delete = self.selected_text or self.selected_shape
                command = DeleteObjectCommand(self, obj_to_delete)
                command.execute()
                self.undo_manager.add_command(command)
                self.selected_text = None
                self.selected_shape = None
                self._update_ui_state()
                self.pdf_view.queue_draw()
        elif action == "copy_view":
            if self.view_selected_text and getattr(self._active_session,'can_copy',True):
                self.get_clipboard().set(self.view_selected_text)
        elif action == "highlight_view":
            self.on_highlight_clicked(None)
        elif action == "remove_highlight_view":
            self._erase_highlights(self._highlight_selection_rects())
            self._clear_view_selection()
        elif action == "highlight_edit":
            self.on_highlight_clicked(None)
        elif action == "remove_highlight":
            rects = self._highlight_selection_rects() if obj is self.selected_text else []
            if not rects and obj and getattr(obj, 'bbox', None):
                rects = [fitz.Rect(obj.bbox)]
            self._erase_highlights(rects)
        elif action == "paste_new_text":
            page_x, page_y = obj
            clipboard = self.get_clipboard()
            def _on_paste_finished(cb, task):
                try:
                    text = cb.read_text_finish(task)
                    if text and text.strip():
                        self._create_text_from_paste(page_x, page_y, text)
                except Exception:
                    pass
            clipboard.read_text_async(None, _on_paste_finished)
        elif action == "toggle_bold":
            if self.view_mode:
                self._convert_view_selection_to_editable()
            if self.selected_text:
                self._toggle_text_bold(self.selected_text)
        elif action == "toggle_italic":
            if self.view_mode:
                self._convert_view_selection_to_editable()
            if self.selected_text:
                self._toggle_text_italic(self.selected_text)
        elif action == "toggle_underline":
            if self.view_mode:
                self._convert_view_selection_to_editable()
            if self.selected_text:
                self._toggle_text_underline(self.selected_text)
        elif action == "toggle_strikethrough":
            if self.view_mode:
                self._convert_view_selection_to_editable()
            if self.selected_text:
                self._toggle_text_strikethrough(self.selected_text)

    def _convert_view_selection_to_editable(self):
        """Convert view selection to editable."""
        if not self.view_sel_rect or not self.view_selected_text:
            return
            
        from .models import EditableText
        from .undo_manager import AddObjectCommand
        
        x1, y1, x2, y2 = self.view_sel_rect
        new_obj = EditableText(x1, y1, self.view_selected_text, is_new=False)
        new_obj.bbox = (x1, y1, x2, y2)
        new_obj.page_number = self.current_page_index
        
        command = AddObjectCommand(self, new_obj)
        command.execute()
        self.undo_manager.add_command(command)
        self.selected_text = new_obj
        self.view_sel_rect = None
        self.view_selected_text = None
        self.word_selection_mode = False
        self.pdf_view.queue_draw()

    def _toggle_text_bold(self, text_obj):
        """Toggle text bold."""
        if text_obj:
            self.selected_text = text_obj
            old_properties = {'is_bold': text_obj.is_bold, 'bbox': text_obj.bbox}
            new_properties = {'is_bold': not text_obj.is_bold, 'bbox': text_obj.bbox}
            command = EditObjectCommand(self, text_obj, old_properties, new_properties)
            command.execute()
            self.undo_manager.add_command(command)
            if hasattr(self, '_update_text_format_controls'):
                self._update_text_format_controls(text_obj)
            self.document_modified = True
            self.pdf_view.queue_draw()
            if hasattr(self, 'context_popover') and self.context_popover:
                self.context_popover.popdown()

    def _toggle_text_italic(self, text_obj):
        """Toggle text italic."""
        if text_obj:
            self.selected_text = text_obj
            old_properties = {'is_italic': text_obj.is_italic, 'bbox': text_obj.bbox}
            new_properties = {'is_italic': not text_obj.is_italic, 'bbox': text_obj.bbox}
            command = EditObjectCommand(self, text_obj, old_properties, new_properties)
            command.execute()
            self.undo_manager.add_command(command)
            if hasattr(self, '_update_text_format_controls'):
                self._update_text_format_controls(text_obj)
            self.document_modified = True
            self.pdf_view.queue_draw()
            if hasattr(self, 'context_popover') and self.context_popover:
                self.context_popover.popdown()

    def _toggle_text_underline(self, text_obj):
        """Toggle text underline."""
        if text_obj:
            self.selected_text = text_obj
            old_val = getattr(text_obj, 'is_underline', False)
            old_properties = {'is_underline': old_val, 'bbox': text_obj.bbox}
            new_properties = {'is_underline': not old_val, 'bbox': text_obj.bbox}
            command = EditObjectCommand(self, text_obj, old_properties, new_properties)
            command.execute()
            self.undo_manager.add_command(command)
            if hasattr(self, '_update_text_format_controls'):
                self._update_text_format_controls(text_obj)
            self.document_modified = True
            self.pdf_view.queue_draw()
            if hasattr(self, 'context_popover') and self.context_popover:
                self.context_popover.popdown()

    def _toggle_text_strikethrough(self, text_obj):
        """Toggle text strikethrough."""
        if text_obj:
            self.selected_text = text_obj
            old_val = getattr(text_obj, 'is_strikethrough', False)
            old_properties = {'is_strikethrough': old_val, 'bbox': text_obj.bbox}
            new_properties = {'is_strikethrough': not old_val, 'bbox': text_obj.bbox}
            command = EditObjectCommand(self, text_obj, old_properties, new_properties)
            command.execute()
            self.undo_manager.add_command(command)
            if hasattr(self, '_update_text_format_controls'):
                self._update_text_format_controls(text_obj)
            self.document_modified = True
            self.pdf_view.queue_draw()
            if hasattr(self, 'context_popover') and self.context_popover:
                self.context_popover.popdown()

    def _set_text_alignment(self, text_obj, new_align):
        """Set text alignment with undo/redo."""
        if text_obj:
            self.selected_text = text_obj
            old_val = getattr(text_obj, 'alignment', 'left')
            if old_val != new_align:
                old_properties = {'alignment': old_val, 'bbox': text_obj.bbox}
                new_properties = {'alignment': new_align, 'bbox': text_obj.bbox}
                command = EditObjectCommand(self, text_obj, old_properties, new_properties)
                command.execute()
                self.undo_manager.add_command(command)
                self.document_modified = True
                self.pdf_view.queue_draw()
                if hasattr(self, '_update_text_format_controls'):
                    self._update_text_format_controls(text_obj)
            if hasattr(self, 'context_popover') and self.context_popover:
                self.context_popover.popdown()

    def _update_confirm_delete_menu_state(self, val: bool):
        """Update the confirm delete menu action state."""
        if hasattr(self, 'action_confirm_delete'):
            self.action_confirm_delete.set_state(GLib.Variant.new_boolean(val))

    def _handle_delete_with_confirmation(self, obj, confirmation_key):
        """Handle delete with confirmation."""
        from .ui_components import show_confirm_dialog
        
        if isinstance(obj, EditableText):
            confirm_text = _("delete_text_confirm").format(f"{obj.text[:50]}...")
            confirm_title = _("delete_confirm_title")
        elif isinstance(obj, EditableShape):
            confirm_text = _("delete_shape_confirm")
            confirm_title = _("delete_confirm_title")
        elif isinstance(obj, EditableImage):
            confirm_text = _("delete_image_confirm")
            confirm_title = _("delete_confirm_title")
        elif isinstance(obj, EditableStroke):
            confirm_text = _("delete_shape_confirm")
            confirm_title = _("delete_confirm_title")
        else:
            return

        confirm_needed = get_setting("confirm_delete_objects", True)
        confirmed = True
        if confirm_needed:
            confirmed, do_not_ask = show_confirm_dialog(
                self, confirm_text, confirm_title, destructive=True,
                checkbox_label=_("do_not_ask_again")
            )
            if confirmed and do_not_ask:
                set_setting("confirm_delete_objects", False)
                self._update_confirm_delete_menu_state(False)
            
        if confirmed:
            command = DeleteObjectCommand(self, obj)
            command.execute()
            self.undo_manager.add_command(command)
            self.selected_text = None
            self.selected_image = None
            self.selected_shape = None
            self.selected_stroke = None
            self._update_ui_state()
            self.pdf_view.queue_draw()
            self.status_label.set_text(_("object_deleted"))


    def _create_text_from_paste(self, page_x, page_y, text):
        """Create text from paste."""
        if not self.doc or not text or not text.strip():
            return
        
        try:
            font_family = self._last_font_family or "Liberation Sans"
            font_size = self._last_font_size or 11.0
            is_bold = self._last_is_bold or False
            is_italic = self._last_is_italic or False
            is_strikethrough = getattr(self, '_last_is_strikethrough', False)
            alignment = getattr(self, '_last_alignment', 'left')
            color = self._last_color or (0.0, 0.0, 0.0)
            
            new_text = EditableText(
                x=page_x,
                y=page_y,
                text=text.strip(),
                font_size=font_size,
                font_family=font_family,
                color=color,
                is_new=True,
                baseline=page_y + (font_size * 0.85),
                alignment=alignment
            )
            new_text.is_bold = is_bold
            new_text.is_italic = is_italic
            new_text.is_strikethrough = is_strikethrough
            new_text.page_number = self.current_page_index
            
            self.editable_texts.append(new_text)
            command = AddObjectCommand(self, new_text)
            command.execute()
            self.undo_manager.add_command(command)
            
            self.document_modified = True
            self._refresh_thumbnail(self.current_page_index)
            self._update_ui_state()
            self.pdf_view.queue_draw()
            
        except Exception as e:
            print(f"Error creating text from paste: {e}")
