"""Verify reader fullscreen, wheel page turns, and Ctrl+wheel zoom in GTK."""
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio,Gdk
from unittest.mock import Mock,patch
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
app=Adw.Application(application_id='org.pdflx.TestNavigation',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
window.doc,_=pdf_handler.create_new_pdf(num_pages=3)
window.view_mode=True
window._active_session.fit_to_view=True
window._load_page(0)
window.stack.set_visible_child_name('editor')
window._schedule_fit_document(window._active_session)
window.present()
loop=GLib.MainLoop()
errors=[]
controller=Mock()
controller.get_current_event_state.return_value=Gdk.ModifierType(0)
def forward():
    assert window.page_scroll_controller.get_widget() is window.pdf_scroll
    assert window.page_scroll_controller.get_propagation_phase()==Gtk.PropagationPhase.CAPTURE
    zoom=window.zoom_level
    assert window.on_scroll_zoom(controller,0,1)
    assert window.current_page_index==1
    assert window.zoom_level==zoom
    assert window.on_scroll_zoom(controller,1,0) is False
def backward():
    window.on_scroll_zoom(controller,0,-1)
    assert window.current_page_index==0
    assert window.on_scroll_zoom(controller,0,-1)
    assert window.current_page_index==0
    window.on_pdf_view_pressed(Mock(),2,150,150)
def fullscreen():
    assert window.is_fullscreen()
    assert window.document_view_stack.get_visible_child_name()=='document'
    assert window.pdf_scroll.get_parent() is window.document_only_box
    assert window.pdf_scroll.get_mapped()
    for widget in (window.main_box,window.tools_sidebar,window.sidebar_box,window.main_toolbar,
                   window.status_bar_box,window.tab_bar):
        assert not widget.get_mapped(), type(widget)
    assert window.pdf_view.has_focus()
    paintable=Gtk.WidgetPaintable.new(window.document_view_stack)
    snapshot=Gtk.Snapshot()
    paintable.snapshot(snapshot,window.document_view_stack.get_width(),window.document_view_stack.get_height())
    node=snapshot.to_node()
    if node:
        window.get_renderer().render_texture(node,None).save_to_png('/tmp/reader-document-only.png')
    window.on_scroll_zoom(controller,0,1)
    assert window.current_page_index==1
    window.on_key_pressed(None,Gdk.KEY_Escape,0,Gdk.ModifierType(0))
def zoom():
    assert not window.is_fullscreen()
    assert window.document_view_stack.get_visible_child_name()=='normal'
    assert window.pdf_scroll.get_parent() is window._document_view_parent
    assert window.sidebar_box.get_mapped()
    assert window.status_bar_box.get_mapped()
    window._load_page(0)
    controller.get_current_event_state.return_value=Gdk.ModifierType.CONTROL_MASK
    original=window.zoom_level
    window._last_pointer_pos=(150,150)
    window.on_scroll_zoom(controller,0,-1)
    assert abs(window.zoom_level-original*1.2)<.001
    window.on_scroll_zoom(controller,0,1)
    assert abs(window.zoom_level-original)<.001
    assert window.current_page_index==0
    assert not window._active_session.fit_to_view
    assert not window.on_scroll_zoom(controller,2,0)
    window._set_zoom(2)
def within_page():
    controller.get_current_event_state.return_value=Gdk.ModifierType(0)
    adj=window.pdf_scroll.get_vadjustment()
    bottom=adj.get_upper()-adj.get_page_size()
    assert bottom>100
    adj.set_value(bottom/2)
    assert window.on_scroll_zoom(controller,0,1) is False
    assert window.current_page_index==0
    adj.set_value(bottom)
    assert window.on_scroll_zoom(controller,0,1)
    assert window.current_page_index==1
def at_top():
    assert window.pdf_scroll.get_vadjustment().get_value()==0
    window.on_scroll_zoom(controller,0,-1)
    assert window.current_page_index==0
def previous_bottom():
    adj=window.pdf_scroll.get_vadjustment()
    assert abs(adj.get_value()-(adj.get_upper()-adj.get_page_size()))<1
    window.view_mode=False
    gesture=Gtk.GestureClick.new()
    window.pdf_view.add_controller(gesture)
    with patch.object(window,'_toggle_fullscreen') as toggle:
        window.on_pdf_view_pressed(gesture,2,5,5)
        toggle.assert_not_called()
    assert not window.document_modified
    window.view_mode=True
    window.on_pdf_view_pressed(Mock(),2,100,100)

def exit_by_double_click():
    assert window.is_fullscreen()
    assert window.document_view_stack.get_visible_child_name()=='document'
    window.on_pdf_view_pressed(Mock(),2,100,100)

def restored():
    assert not window.is_fullscreen()
    assert window.document_view_stack.get_visible_child_name()=='normal'
    assert window.pdf_scroll.get_parent() is window._document_view_parent
    assert window.zoom_level==2
    print('View fullscreen, Escape, wheel page turns, within-page scroll, and Ctrl zoom passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((800,forward),(1100,backward),(1500,fullscreen),(1850,zoom),
                       (2200,within_page),(2550,at_top),(2900,previous_bottom),(3300,exit_by_double_click),(3600,restored)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(3900,lambda:(window.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
