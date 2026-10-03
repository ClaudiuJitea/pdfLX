import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio,Gdk
from unittest.mock import Mock
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
from pdflx.reader_selection import characters
app=Adw.Application(application_id='org.pdflx.TestContinuous',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w=PdfEditorWindow(application=app)
w.set_default_size(1200,900)
w.doc,_=pdf_handler.create_new_pdf(width=500,height=700,num_pages=3)
for index,page in enumerate(w.doc):
    page.insert_text((50,100),f'Page {index+1} readable text',fontsize=24)
w.doc[1].set_rotation(90)
w.view_mode=True
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop=GLib.MainLoop()
errors=[]
controller=Mock()
controller.get_current_event_state.return_value=Gdk.ModifierType(0)
def enable():
    assert w.scroll_mode_button.get_visible()
    assert not w.scroll_mode_button.get_active()
    w.scroll_mode_button.set_active(True)
def check_column():
    c=w.continuous_view
    assert c.enabled and len(c.slots)==3
    assert w.pdf_overlay.get_parent() is c.slots[w.current_page_index]
    assert w.pdf_scroll.get_vadjustment().get_upper()>1700
    assert w.on_scroll_zoom(controller,0,1) is False
    w.pdf_scroll.get_vadjustment().set_value(450)
def check_scroll():
    c=w.continuous_view
    assert abs(w.pdf_scroll.get_vadjustment().get_value()-450)<2
    assert w.current_page_index==1,w.current_page_index
    assert '2' in w.page_label.get_text()
    paintable=Gtk.WidgetPaintable.new(w.pdf_viewport)
    snapshot=Gtk.Snapshot()
    paintable.snapshot(snapshot,w.pdf_viewport.get_width(),w.pdf_viewport.get_height())
    node=snapshot.to_node()
    if node:
        w.get_renderer().render_texture(node,None).save_to_png('/tmp/continuous-pages.png')
    w._load_page(2)
def check_navigation():
    c=w.continuous_view
    adj=w.pdf_scroll.get_vadjustment()
    assert w.current_page_index==2
    assert abs(adj.get_value()-min(c.starts[2],adj.get_upper()-adj.get_page_size()))<2
    w._set_zoom(1.2)
def zoom_and_copy():
    c=w.continuous_view
    assert abs(w.zoom_level-1.2)<.001
    assert c.starts[1]==16+840+20
    w.on_key_pressed(None,Gdk.KEY_a,0,Gdk.ModifierType.CONTROL_MASK)
    assert w.view_selected_text.startswith('Page 3'),w.view_selected_text
    w.on_highlight_clicked(None)
    page=w.doc[2]
    assert len(list(page.annots() or []))==1
    assert c.enabled
    w._toggle_fullscreen()
def fullscreen():
    assert w.document_view_stack.get_visible_child_name()=='document'
    assert w.continuous_view.enabled
    w._exit_document_view()
    w.scroll_mode_button.set_active(False)
def page_mode():
    assert not w.continuous_view.enabled
    assert w.pdf_overlay.get_parent() is w.pdf_viewport
    assert w.current_page_index==2
    w.scroll_mode_button.set_active(True)
    w._toggle_view_edit_mode()
def edit_mode():
    assert w.scroll_mode_button.get_visible()
    assert w.continuous_view.enabled
    from pdflx.models import EditableShape
    from pdflx.undo_manager import AddObjectCommand
    obj=EditableShape('rectangle',(40,100,140,150),page_number=w.current_page_index,is_new=True)
    command=AddObjectCommand(w,obj)
    command.execute()
    w.undo_manager.add_command(command)
    assert w.continuous_view.enabled
    assert obj in w.editable_shapes
    w.undo_manager.undo()
    assert obj not in w.editable_shapes
    assert w.continuous_view.enabled
    w.scroll_mode_button.set_active(False)
    assert not w.continuous_view.enabled
    assert w.pdf_overlay.get_parent() is w.pdf_viewport
    w.scroll_mode_button.set_active(True)
    assert w.continuous_view.enabled
    w._toggle_view_edit_mode()
def finish():
    assert w.continuous_view.enabled
    assert w.scroll_mode_button.get_active()
    print('Continuous pages, smooth scrolling, navigation, zoom, highlight, fullscreen, and mode switching passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((600,enable),(1000,check_column),(1500,check_scroll),(2000,check_navigation),
                       (2500,zoom_and_copy),(3100,fullscreen),(3500,page_mode),(3900,edit_mode),(4300,finish)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(4700,lambda:(w.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
