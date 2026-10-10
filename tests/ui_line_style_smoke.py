"""Line style in the shape and line popovers uses buttons, not a nested drop-down."""
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw,GLib,Gio,Gtk
import sys
from unittest.mock import Mock
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
from pdflx.shape_tools import DashPicker
app=Adw.Application(application_id='org.pdflx.TestLineStyle',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w=PdfEditorWindow(application=app)
w.doc,_=pdf_handler.create_new_pdf()
w.view_mode=False
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop=GLib.MainLoop()
errors=[]
def point(x,y):
    ox=max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2)
    oy=max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2)
    return x*w.zoom_level+ox,y*w.zoom_level+oy
def drag(x,y,dx,dy):
    sx,sy=point(x,y)
    w.on_drag_begin(Mock(),sx,sy)
    for step in range(1,5):
        w.on_drag_update(Mock(),dx*w.zoom_level*step/4,dy*w.zoom_level*step/4)
    w.on_drag_end(Mock(),dx*w.zoom_level,dy*w.zoom_level)
def popovers_have_no_dropdown():
    for button in (w.shape_tools.options_button,w.shape_tools.line_button):
        stack=[button.get_popover()]
        while stack:
            widget=stack.pop()
            assert not isinstance(widget,Gtk.DropDown),button
            child=widget.get_first_child()
            while child:
                stack.append(child)
                child=child.get_next_sibling()
    assert isinstance(w.shape_tools.dash,DashPicker) and isinstance(w.shape_tools.line_dash,DashPicker)
def shape_dash():
    popovers_have_no_dropdown()
    w.on_tool_selected(None,'add_rectangle')
    drag(200,300,120,80)
    shape=w.selected_shape
    assert shape is not None
    w.shape_tools.dash.buttons[1].set_active(True)
    assert shape.dash=='dashed',shape.dash
    w.shape_tools.dash.buttons[2].set_active(True)
    assert shape.dash=='dotted'
    w.lookup_action('undo').activate(None)
    assert w.selected_shape is None or w.selected_shape.dash in ('dashed','solid')
def line_dash():
    w.on_tool_selected(None,'pen')
    drag(150,500,200,30)
    stroke=w.selected_stroke
    assert stroke is not None
    w.shape_tools.line_dash.buttons[1].set_active(True)
    assert getattr(stroke,'dash','solid')=='dashed'
    w.shape_tools.arrow_end.set_active(True)
    assert stroke.arrow_end
    # Selecting the stroke again syncs the buttons without creating edits.
    history=len(w.undo_manager.undo_stack)
    w.shape_tools.sync_line(stroke)
    assert w.shape_tools.line_dash.get_selected()==1
    assert len(w.undo_manager.undo_stack)==history
    print('Line style buttons edit shapes and pen strokes without nested drop-downs passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((700,shape_dash),(1000,line_dash)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(1400,lambda:(w.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
