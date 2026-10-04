"""Organize Pages: a full-window grid of page thumbnails to reorder, rotate, duplicate,
delete, insert and extract pages. Every change is one undo step."""
import pymupdf as fitz
from gi.repository import Gdk, GLib, GObject, Gtk, Pango

from . import pdf_handler
from .i18n import _
from .ops import pages as page_ops

THUMB_WIDTH = 160


def moved_order(count, moving, target):
    """New page order after moving the pages in ``moving`` before position ``target``."""
    moving = sorted(set(moving))
    rest = [page for page in range(count) if page not in moving]
    insert_at = sum(1 for page in rest if page < target)
    return rest[:insert_at] + moving + rest[insert_at:]


class OrganizePages:
    def __init__(self, window, surface):
        self.window = window
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False, hexpand=True, vexpand=True)
        self.root.add_css_class('pdflx-organize')
        bar = Gtk.Box(spacing=6, margin_start=16, margin_end=12, margin_top=10, margin_bottom=10)
        title = Gtk.Label(label=_("organize_title"), xalign=0)
        title.add_css_class('title-4')
        bar.append(title)
        # Labels may shrink so the action buttons and Done always stay visible.
        self.count = Gtk.Label(xalign=0, hexpand=True, margin_start=8, ellipsize=Pango.EllipsizeMode.END,
                               width_chars=4)
        self.count.add_css_class('dim-label')
        bar.append(self.count)
        self.actions = {}
        for key, icon, tip in (('rotate_left', 'editor-rotate-left-symbolic', 'organize_rotate_left'),
                               ('rotate_right', 'editor-rotate-right-symbolic', 'organize_rotate_right'),
                               ('duplicate', 'edit-copy-symbolic', 'organize_duplicate'),
                               ('insert', 'list-add-symbolic', 'organize_insert'),
                               ('extract', 'document-send-symbolic', 'organize_extract'),
                               ('delete', 'user-trash-symbolic', 'organize_delete')):
            button = Gtk.Button(icon_name=icon, tooltip_text=_(tip))
            button.add_css_class('flat')
            button.connect('clicked', lambda _b, key=key: self.run(key))
            bar.append(button)
            self.actions[key] = button
        self.edit_button = Gtk.Button(label=_("mode_edit"), tooltip_text=_("organize_need_edit"), visible=False)
        self.edit_button.connect('clicked', lambda *_: self._enter_edit())
        bar.append(self.edit_button)
        bar.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, margin_start=4, margin_end=4))
        done = Gtk.Button(label=_("organize_done"))
        done.add_css_class('suggested-action')
        done.connect('clicked', lambda *_: self.close())
        bar.append(done)
        self.root.append(bar)
        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.MULTIPLE, homogeneous=False, valign=Gtk.Align.START,
                                column_spacing=14, row_spacing=14, margin_start=16, margin_end=16, margin_top=6,
                                margin_bottom=16,
                                max_children_per_line=30, activate_on_single_click=False)
        self.grid.connect('selected-children-changed', lambda *_: self._sync())
        self.grid.connect('child-activated', lambda _grid, child: self._open(child.page))
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.grid)
        self.root.append(scroll)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self._on_key)
        self.root.add_controller(keys)
        surface.add_overlay(self.root)
        self._render_source = None

    # ------------------------------------------------------------ show / hide
    def open(self):
        if not self.window.doc:
            return
        self.root.set_visible(True)
        self.populate(select=[self.window.current_page_index])
        self.grid.grab_focus()

    def close(self):
        self.root.set_visible(False)
        self.window.pdf_view.grab_focus()

    def is_open(self):
        return self.root.get_visible()

    def selected(self):
        return sorted(child.page for child in self.grid.get_selected_children())

    def _sync(self):
        doc = self.window.doc
        chosen = self.selected()
        editable = bool(doc) and self.window.document_tools.editable()
        self.count.set_text(_("organize_count", doc.page_count if doc else 0, len(chosen)))
        # Page changes need Edit mode; say so instead of only greying the buttons out.
        view_only = bool(doc) and not editable
        can_edit = bool(doc) and getattr(self.window._active_session, 'can_edit', True)
        self.edit_button.set_visible(view_only and can_edit)
        self.count.set_tooltip_text(_("organize_protected") if view_only and not can_edit else None)
        for key, button in self.actions.items():
            needs = key != 'insert'
            allowed = editable or key == 'extract'  # extracting leaves this document unchanged
            button.set_sensitive(allowed and (bool(chosen) or not needs) and
                                 not (key == 'delete' and doc and len(chosen) >= doc.page_count))

    # ------------------------------------------------------------ thumbnails
    def populate(self, select=()):
        doc = self.window.doc
        while child := self.grid.get_first_child():
            self.grid.remove(child)
        if not doc:
            return
        self._render_source = (doc, list(range(doc.page_count)))
        for number in range(doc.page_count):
            child = Gtk.FlowBoxChild(halign=Gtk.Align.START, valign=Gtk.Align.START)
            child.page = number
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                          valign=Gtk.Align.START)
            page = doc[number]
            ratio = page.rect.height / max(1, page.rect.width)
            # A fixed box per page keeps its proportions (landscape pages get a wider box).
            width = THUMB_WIDTH if ratio >= 1 else int(THUMB_WIDTH / ratio * 0.75)
            # Rendered at exactly this size (see _render_next), so the picture never stretches.
            frame = Gtk.Picture(can_shrink=False, hexpand=False, vexpand=False,
                                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            frame.set_size_request(width, int(width * ratio))
            frame.display_width = width
            frame.add_css_class('pdflx-organize-thumb')
            box.append(frame)
            label = Gtk.Label(label=str(number + 1))
            label.add_css_class('caption')
            box.append(label)
            child.set_child(box)
            child.picture = frame
            self._drag(child)
            self.grid.append(child)
            if number in select:
                self.grid.select_child(child)
        GLib.idle_add(self._render_next, doc, 0)
        self._sync()

    def _render_next(self, doc, number):
        """Render thumbnails a few at a time so large documents stay responsive."""
        if doc is not self.window.doc or not self.root.get_visible():
            return False
        child = self.grid.get_child_at_index(number)
        if child is None:
            return False
        try:
            page = doc[number]
            zoom = child.picture.display_width / max(1, page.rect.width)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            texture = Gdk.MemoryTexture.new(pix.width, pix.height, Gdk.MemoryFormat.R8G8B8,
                                            GLib.Bytes.new(pix.samples), pix.stride)
            child.picture.set_paintable(texture)
        except Exception:
            pass
        GLib.idle_add(self._render_next, doc, number + 1)
        return False

    # ------------------------------------------------------------ drag and drop
    def _drag(self, child):
        source = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        source.connect('prepare', lambda _s, _x, _y, child=child: Gdk.ContentProvider.new_for_value(str(child.page)))
        source.connect('drag-begin', lambda _s, _d, child=child: self._drag_begin(child))
        child.add_controller(source)
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect('drop', lambda _t, value, x, _y, child=child: self._drop(int(value), child, x))
        child.add_controller(target)

    def _drag_begin(self, child):
        if not self.grid.get_selected_children() or child not in self.grid.get_selected_children():
            self.grid.unselect_all()
            self.grid.select_child(child)

    def _drop(self, dragged, child, x):
        moving = self.selected() or [dragged]
        if dragged not in moving:
            moving = [dragged]
        target = child.page + (1 if x > child.get_width() / 2 else 0)
        self.move(moving, target)
        return True

    # ------------------------------------------------------------ actions
    def _change(self, operation, remap, focus, message, select):
        try:
            self.window.features.change_pages(operation, remap, focus=focus, message_text=message)
        except Exception as error:
            self.window.status_label.set_text(str(error))
            return False
        self.populate(select=select)
        return True

    def move(self, pages, target):
        doc = self.window.doc
        if not self.window.document_tools.editable():
            return False
        order = moved_order(doc.page_count, pages, target)
        if order == list(range(doc.page_count)):
            return False
        new_positions = [order.index(page) for page in sorted(pages)]
        return self._change(lambda: page_ops.apply_order(doc, order), page_ops.order_remap(order), new_positions[0],
                            _("organize_moved", len(pages)), new_positions)

    def run(self, key):
        doc = self.window.doc
        chosen = self.selected()
        if not doc or not chosen and key != 'insert':
            return
        if key != 'extract' and not self.window.document_tools.editable():
            return
        if key in ('rotate_left', 'rotate_right'):
            delta = -90 if key == 'rotate_left' else 90

            def rotate():
                for number in chosen:
                    page = doc[number]
                    page.set_rotation((page.rotation + delta) % 360)
            self._change(rotate, lambda page: page, chosen[0], _("organize_rotated", len(chosen)), chosen)
        elif key == 'delete':
            if len(chosen) >= doc.page_count:
                return

            def delete():
                for number in reversed(chosen):
                    ok, message = pdf_handler.delete_page(doc, number)
                    if not ok:
                        raise ValueError(message)
            self._change(delete, page_ops.delete_remap(chosen), max(0, min(chosen[0], doc.page_count - len(chosen) - 1)),
                         _("organize_deleted", len(chosen)), [])
        elif key == 'duplicate':
            def duplicate():
                for number in reversed(chosen):
                    ok, message = pdf_handler.duplicate_page(doc, number)
                    if not ok:
                        raise ValueError(message)
            remap = lambda page: page + sum(1 for number in chosen if number < page)
            copies = [number + index + 1 for index, number in enumerate(chosen)]
            self._change(duplicate, remap, copies[0], _("organize_duplicated", len(chosen)), copies)
        elif key == 'insert':
            at = (chosen[-1] + 1) if chosen else doc.page_count
            reference = doc[chosen[-1] if chosen else doc.page_count - 1].rect
            self._change(lambda: pdf_handler.insert_blank_page(doc, at, reference.width, reference.height),
                         page_ops.insert_remap(at, 1), at, _("organize_inserted"), [at])
        elif key == 'extract':
            extracted = fitz.open()
            for number in chosen:
                extracted.insert_pdf(doc, from_page=number, to_page=number)
            stem = 'pages'
            self.close()
            self.window.features.open_new(extracted, f'{stem}-{"-".join(str(n + 1) for n in chosen[:6])}.pdf')

    def _enter_edit(self):
        if self.window.view_mode:
            self.window._toggle_view_edit_mode()
        self._sync()

    def _open(self, number):
        self.close()
        self.window._load_page(number)

    def _on_key(self, controller, keyval, keycode, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval == Gdk.KEY_Delete and self.selected():
            self.run('delete')
            return True
        if ctrl and keyval in (Gdk.KEY_a, Gdk.KEY_A):
            self.grid.select_all()
            return True
        if ctrl and keyval in (Gdk.KEY_d, Gdk.KEY_D) and self.selected():
            self.run('duplicate')
            return True
        return False
