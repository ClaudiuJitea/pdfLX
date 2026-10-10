"""Sticky notes move by dragging in edit and view mode; a plain click opens them."""
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw,GLib,Gio,Gtk
import pymupdf as fitz
from unittest.mock import Mock
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler, document_tools as tools
app=Adw.Application(application_id='org.pdflx.TestNoteMove',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w=PdfEditorWindow(application=app)
w.doc,_=pdf_handler.create_new_pdf()
xref=tools.add_review(w.doc,0,'note',fitz.Rect(100,100,118,118),'Check this','me')
w.view_mode=False
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop=GLib.MainLoop()
errors=[]
def rect():
    page=w.doc[0]
    return page.load_annot(xref).rect
def press_drag(x,y,dx,dy):
    ox=max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2)
    oy=max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2)
    z=w.zoom_level
    gesture=Gtk.GestureClick.new()
    w.pdf_view.add_controller(gesture)
    w.on_pdf_view_pressed(gesture,1,x*z+ox,y*z+oy)
    w.on_drag_begin(Mock(),x*z+ox,y*z+oy)
    w.on_drag_update(Mock(),dx*z,dy*z)
    w.on_drag_end(Mock(),dx*z,dy*z)
    w.pdf_view.remove_controller(gesture)
def edit_mode():
    press_drag(109,109,60,40)
    moved=rect()
    assert abs(moved.x0-160)<0.5 and abs(moved.y0-140)<0.5,moved
    assert abs(moved.width-18)<0.5
    page=w.doc[0]
    assert page.load_annot(xref).info['content']=='Check this'
    bubble=getattr(w.document_tools,'note_bubble',None)
    assert not (bubble and bubble.get_visible())
    press_drag(169,149,0,0)
    assert w.document_tools.note_bubble.get_visible()
    w.document_tools.close_note_bubble()
    w.lookup_action('undo').activate(None)
    assert abs(rect().x0-100)<0.5
def view_mode():
    w.view_mode=True
    w._load_page(0)
    w._update_ui_state()
    press_drag(109,109,0,80)
    assert abs(rect().y0-180)<0.5,rect()
    assert not w.view_sel_rect
    print('Sticky notes move in edit and view mode and open on click passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((600,edit_mode),(1000,view_mode)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(1400,lambda:(w.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
