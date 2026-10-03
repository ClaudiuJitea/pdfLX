"""Open every document-operation dialog and drive key workflows in a real window."""
import os
import tempfile

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk
import pymupdf as fitz

from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
from pdflx.features import ACTIONS

app = Adw.Application(application_id='org.pdflx.TestFeatures', flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w = PdfEditorWindow(application=app)
directory = tempfile.TemporaryDirectory()
source = os.path.join(directory.name, 'source.pdf')
with fitz.open() as doc:
    for number in range(3):
        page = doc.new_page()
        page.insert_text((72, 90), f'Heading {number + 1}', fontsize=24)
        page.insert_text((72, 140), f'Body text {number + 1} at www.example.com', fontsize=11)
    doc.save(source)
w.present()
loop = GLib.MainLoop()
errors = []


def dialog():
    return w.get_visible_dialog()


def close_dialog():
    current = dialog()
    if current is not None:
        current.force_close()


def labels(model):
    result = []
    for index in range(model.get_n_items()):
        label = model.get_item_attribute_value(index, 'label', GLib.VariantType.new('s'))
        if label:
            result.append(label.get_string())
        for link in ('section', 'submenu'):
            child = model.get_item_link(index, link)
            if child is not None:
                result.extend(labels(child))
    return result


def load():
    w.load_document(source)


def menus():
    assert w.doc is not None and w.doc.page_count == 3
    w.view_mode = False
    w._update_ui_state()
    names = labels(w.main_menu_button.get_menu_model())
    for expected in ('Pages', 'Protect', 'Split Document…', 'Password and Permissions…', 'Reading colors'):
        assert expected in names, expected
    assert w.pages_tool_button.get_popover() is not None
    assert w.pages_tool_button.get_sensitive()
    child = w.tools_sidebar.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.MenuButton):
            assert child.get_direction() == Gtk.ArrowType.RIGHT, child.get_tooltip_text()
        child = child.get_next_sibling()
    assert not w.lookup_action('enter_edit_mode').get_enabled()
    w.view_mode = True
    w._update_ui_state()
    assert w.lookup_action('enter_edit_mode').get_enabled()
    w.lookup_action('enter_edit_mode').activate(None)
    assert not w.view_mode
    assert not w.lookup_action('enter_edit_mode').get_enabled()


def every_dialog():
    skipped = {'reverse_pages', 'snapshot', 'save_incremental', 'text_style', 'annot_export',
               'open_convert', 'annot_import', 'flatten_annotations', 'presentation', 'measure',
               'side_viewer', 'export_vector_svg'}
    for name, _rule, _module, _function in ACTIONS:
        if name in skipped:
            continue
        action = w.lookup_action(name)
        assert action.get_enabled(), name
        action.activate(None)
        current = dialog()
        assert current is not None, f'{name} opened no dialog'
        current.force_close()


def reverse_and_undo():
    w.lookup_action('reverse_pages').activate(None)
    assert 'Heading 3' in w.doc[0].get_text()
    w.lookup_action('undo').activate(None)
    assert 'Heading 1' in w.doc[0].get_text()


def headers():
    w.lookup_action('headers_footers').activate(None)
    sheet = dialog()
    sheet._on_apply_clicked(None)
    assert '1 / 3' in w.doc[0].get_text(), w.doc[0].get_text()
    assert w.document_modified


def labels_and_layout():
    from pdflx.ops import pages
    w.lookup_action('page_labels').activate(None)
    sheet = dialog()
    sheet._on_apply_clicked(None)
    assert pages.get_page_labels(w.doc)[0]['style'] == 'D'
    w.lookup_action('insert_pages').activate(None)
    sheet = dialog()
    rows = []
    child = sheet.page
    def walk(widget):
        if widget.__class__.__name__ == 'FileRow':
            rows.append(widget)
        item = widget.get_first_child()
        while item:
            walk(item)
            item = item.get_next_sibling()
    walk(child)
    rows[0].paths = [source]
    sheet._on_apply_clicked(None)
    assert w.doc.page_count == 6, w.doc.page_count


def new_tab():
    sessions = len(w.sessions)
    w.lookup_action('nup_pages').activate(None)
    sheet = dialog()
    sheet._on_apply_clicked(None)


def check_tab():
    assert w.doc.page_count == 3, w.doc.page_count
    assert w._active_session.pdf_path is None
    assert w._active_session.suggested_name.endswith('-2-up.pdf')
    assert w._active_session.display_title.startswith('*')


def search_and_modes():
    w.search_entry.set_text(r'Heading \d')
    w.search_option_buttons['regex'].set_active(True)


def viewer_features():
    from unittest.mock import Mock
    w.lookup_action('page_layout').change_state(GLib.Variant.new_string('spread'))
    assert w._active_session.scroll_mode == 'spread'
    assert w.continuous_view.enabled and w.continuous_view.rows[0] == [0, 1]
    w.lookup_action('page_layout').change_state(GLib.Variant.new_string('book'))
    assert w.continuous_view.rows[:2] == [[0], [1, 2]]
    w.lookup_action('page_layout').change_state(GLib.Variant.new_string('page'))
    assert not w.continuous_view.enabled
    w.lookup_action('measure').activate(None)
    assert w.tool_mode == 'measure'
    ox = max(0, (w.pdf_view.get_width() - w.current_pdf_page_width) / 2)
    oy = max(0, (w.pdf_view.get_height() - w.current_pdf_page_height) / 2)
    gesture = Mock()
    gesture.get_current_event_state.return_value = 0
    w.on_drag_begin(gesture, ox + 72 * w.zoom_level, oy + 100 * w.zoom_level)
    w.on_drag_update(gesture, 72 * w.zoom_level, 0)
    w.on_drag_end(gesture, 72 * w.zoom_level, 0)
    assert w.measure_tool.last and w.measure_tool.last[3].startswith('25.40 mm'), w.measure_tool.last
    w.on_tool_selected(None, 'select')
    w.lookup_action('side_viewer').activate(None)
    assert w.side_viewer.revealer.get_reveal_child()
    assert len(w.side_viewer.pictures) == w.doc.page_count
    w.lookup_action('side_viewer').activate(None)
    w.lookup_action('presentation').activate(None)
    assert getattr(w, '_document_only_view', False)


def node_editing():
    from unittest.mock import Mock
    from pdflx.models import EditableStroke
    from pdflx.features.drawing_ui import NodeTool
    w.view_mode = False
    w._update_ui_state()
    w.on_tool_selected(None, 'pen')
    ox = max(0, (w.pdf_view.get_width() - w.current_pdf_page_width) / 2)
    oy = max(0, (w.pdf_view.get_height() - w.current_pdf_page_height) / 2)
    gesture = Mock()
    gesture.get_current_event_state.return_value = 0
    z = w.zoom_level
    w.on_drag_begin(gesture, ox + 100 * z, oy + 300 * z)
    for step in range(1, 11):
        w.on_drag_update(gesture, step * 10 * z, (step % 2) * 10 * z)
    w.on_drag_end(gesture, 100 * z, 0)
    strokes = [s for s in w.editable_strokes if isinstance(s, EditableStroke)]
    assert strokes, 'pen stroke was not created'
    stroke = strokes[-1]
    w.selected_stroke = stroke
    w.lookup_action('node_editor').activate(None)
    sheet = dialog()
    sheet._on_apply_clicked(None)
    assert w.tool_mode == 'nodes'
    first = stroke.points[0]
    vx, vy = first
    w.on_drag_begin(gesture, ox + vx * z, oy + vy * z)
    w.on_drag_update(gesture, 0, 30 * z)
    w.on_drag_end(gesture, 0, 30 * z)
    assert abs(stroke.points[0][1] - (first[1] + 30)) < 0.6, (stroke.points[0], first)
    count = len(stroke.points)
    gesture.get_current_event_state.return_value = Gdk.ModifierType.SHIFT_MASK
    w.on_drag_begin(gesture, ox + stroke.points[1][0] * z, oy + stroke.points[1][1] * z)
    w.on_drag_end(gesture, 0, 0)
    assert len(stroke.points) == count - 1
    w.lookup_action('undo').activate(None)
    assert len(stroke.points) == count
    w.lookup_action('undo').activate(None)
    assert abs(stroke.points[0][1] - first[1]) < 0.6
    w.on_tool_selected(None, 'select')
    print('Feature menus, every operation dialog, page workflows, new tabs, search options, reader modes, '
          'layouts, measurement, side viewer, presentation, and node editing passed', flush=True)


def leave_presentation():
    w._exit_document_view()
    assert not getattr(w, '_document_only_view', False)



def search_results():
    assert len(w.search_results) >= 3, w.search_results
    w.lookup_action('reader_mode').change_state(GLib.Variant.new_string('night'))
    assert pdf_handler.get_reader_mode() == 'night'
    w.lookup_action('reader_mode').change_state(GLib.Variant.new_string('normal'))
    w.search_option_buttons['regex'].set_active(False)



def run(callback):
    try:
        callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False


steps = ((300, load), (1500, menus), (1700, every_dialog), (2200, reverse_and_undo), (2500, headers),
         (2900, labels_and_layout), (3400, new_tab), (4200, check_tab), (4500, search_and_modes),
         (5600, search_results), (5900, viewer_features), (6600, leave_presentation), (7000, node_editing))
for delay, callback in steps:
    GLib.timeout_add(delay, run, callback)
GLib.timeout_add(7900, lambda: (w.destroy(), loop.quit(), False)[2])
loop.run()
directory.cleanup()
if errors:
    raise SystemExit(1)
