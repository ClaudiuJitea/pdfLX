"""Highlight eraser on freehand highlighter strokes in edit and view mode."""
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw,GLib,Gio
from unittest.mock import Mock
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler, highlight_tools
app=Adw.Application(application_id='org.pdflx.TestHighlightEraser',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w=PdfEditorWindow(application=app)
w.doc,_=pdf_handler.create_new_pdf()
w.doc[0].insert_text((50,100),'Services provided accordingly',fontsize=11)
w.view_mode=False
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop=GLib.MainLoop()
errors=[]
def drag(x,y,dx,dy):
    ox=max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2)
    oy=max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2)
    z=w.zoom_level
    w.on_drag_begin(Mock(),x*z+ox,y*z+oy)
    w.on_drag_update(Mock(),dx*z,dy*z)
    w.on_drag_end(Mock(),dx*z,dy*z)
def strokes():
    return w._highlight_strokes()
def edit_mode():
    w.on_tool_selected(None,'highlighter')
    drag(50,96,120,0)
    assert len(strokes())==1
    w.remove_highlight_button.emit('clicked')
    assert w.tool_mode=='erase_highlight'
    assert w.pdf_view.get_cursor() is highlight_tools.eraser_cursor()
    drag(90,85,20,20)
    assert len(strokes())==2
    drag(60,96,0,0)
    assert len(strokes())==1
    drag(300,300,0,0)
    assert len(strokes())==1
    w.lookup_action('undo').activate(None)
    w.lookup_action('undo').activate(None)
    assert len(strokes())==1 and len(strokes()[0].points)==2
def view_mode():
    w.view_mode=True
    w._load_page(0)
    w._update_ui_state()
    w._update_cursor_for_tool()
    assert w.tool_mode=='erase_highlight'
    assert w.pdf_view.get_cursor() is highlight_tools.eraser_cursor()
    w.on_drag_begin(Mock(),0,0)
    w.on_drag_end(Mock(),0,0)
    drag(60,96,0,0)
    assert not strokes()
    print('Freehand highlight erasing in edit and view mode and eraser cursor passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((600,edit_mode),(900,view_mode)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(1300,lambda:(w.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
