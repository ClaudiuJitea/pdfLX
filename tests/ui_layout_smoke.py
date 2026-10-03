"""Check that collapsed side panels reserve no canvas width, including tabs."""
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
app=Adw.Application(application_id='org.pdflx.TestLayout',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
Gtk.Settings.get_default().set_property('gtk-enable-animations',False)
window=PdfEditorWindow(application=app)
window.set_default_size(1600,1000)
window.doc,_=pdf_handler.create_new_pdf()
window.view_mode=False
window._load_page(0)
first=window._active_session
window.present()
loop=GLib.MainLoop()
errors=[]
def full_width():
    assert window.document_tools.sidebar.get_width()==0
    assert window.form_tools.sidebar.get_width()==0
    unused=window.editor_container.get_width()-window.paned.get_allocated_width()-window.tools_sidebar.get_allocated_width()
    # The rail has four points of horizontal margins and a CSS border.
    assert 0<=unused<=8,unused
def closed():
    full_width()
    window.form_tools.show()
def forms_open():
    assert window.form_tools.sidebar.get_width()>=320
    window.form_tools.sidebar.set_reveal_child(False)
def after_forms():
    full_width()
    window.document_tools.show_comments()
def comments_open():
    assert window.document_tools.sidebar.get_width()>=290
    window.document_tools.sidebar.set_reveal_child(False)
def second_tab():
    full_width()
    second=window.create_session()
    second.doc,_=pdf_handler.create_new_pdf()
    window.add_session(second,switch_to=True)
def switch_back():
    full_width()
    window.set_active_session(first)
def finished():
    full_width()
    print('Closed side panels use zero width after opening, closing, and switching tabs',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((400,closed),(800,forms_open),(1200,after_forms),(1600,comments_open),(2000,second_tab),(2400,switch_back),(2800,finished)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(3200,lambda:(window.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
