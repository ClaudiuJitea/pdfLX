"""Open real files and verify initial page fit and independent tab zoom."""
import tempfile
from pathlib import Path
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gio
import pymupdf as fitz
from pdflx.window import PdfEditorWindow

app = Adw.Application(application_id='org.pdflx.TestFit', flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window = PdfEditorWindow(application=app)
window.set_default_size(1200, 900)
window._record_recent_file = lambda path: None
loop = GLib.MainLoop()
errors = []
first = None

def assert_fit():
    session = window._active_session
    assert session.doc and not session.fit_on_load, (window.pdf_scroll.get_width(), window.pdf_scroll.get_height(), window.pdf_view.get_mapped(), window.stack.get_visible_child_name())
    page = session.doc[session.current_page_index]
    width, height = window.pdf_scroll.get_width(), window.pdf_scroll.get_height()
    expected = min((width - 32) / page.rect.width, (height - 32) / page.rect.height)
    assert abs(window.zoom_level - expected) < 0.001, (window.zoom_level, expected)
    assert page.rect.width * window.zoom_level <= width - 31
    assert page.rect.height * window.zoom_level <= height - 31
    assert not window.document_modified

def check_initial():
    assert_fit()
    assert window._active_session.fit_to_view
    window.set_default_size(1000,750)

def check_portrait():
    global first
    assert_fit()
    first = window._active_session
    window._set_zoom(1.25)
    assert not first.fit_to_view
    window.load_document(str(landscape), in_new_tab=True)

def check_landscape():
    assert_fit()
    window.set_active_session(first)

def check_restored():
    assert window.zoom_level == 1.25
    assert not first.fit_on_load
    print('Loaded pages fit the canvas; tab zoom is preserved', flush=True)

def run(callback):
    try:
        callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False

with tempfile.TemporaryDirectory() as directory:
    portrait = Path(directory) / 'portrait.pdf'
    landscape = Path(directory) / 'landscape.pdf'
    for path, size, rotation in ((portrait, (1200, 1800), 0), (landscape, (1200, 1800), 90)):
        with fitz.open() as doc:
            page = doc.new_page(width=size[0], height=size[1])
            page.set_rotation(rotation)
            doc.save(path)
    # Loading before presentation also needs to wait for the real canvas allocation.
    window.load_document(str(portrait), in_new_tab=False)
    window.present()
    for delay, callback in ((1800, check_initial), (3000, check_portrait), (5000, check_landscape), (5700, check_restored)):
        GLib.timeout_add(delay, run, callback)
    GLib.timeout_add(6000, lambda: (window.destroy(), loop.quit(), False)[2])
    loop.run()
if errors:
    raise SystemExit(1)
