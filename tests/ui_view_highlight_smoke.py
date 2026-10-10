import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio,Gdk
import pymupdf as fitz
from unittest.mock import Mock
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
from pdflx.reader_selection import characters,select
app=Adw.Application(application_id='org.pdflx.TestViewHighlight',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w=PdfEditorWindow(application=app)
w.doc,_=pdf_handler.create_new_pdf()
w.doc[0].insert_text((50,100),'Select this sentence')
w.doc[0].set_rotation(90)
w.view_mode=True
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop=GLib.MainLoop()
errors=[]
def annotations():
    page=w.doc[0]
    return [{'vertices':annot.vertices,'colors':annot.colors} for annot in page.annots() or []]
def set_selection(first,last):
    items=characters(w.doc[0])
    center=lambda index:(items[index][1].rect.tl+items[index][1].rect.br)/2
    a,b=center(first),center(last)
    w.view_selected_text,w.view_sel_rect,w._active_session.view_selection_quads=select(items,a,b)
    w._update_ui_state()
    return a,b
def highlight():
    set_selection(0,5)
    assert w.highlight_button.get_sensitive()
    assert w.remove_highlight_button.get_sensitive()
    assert w.highlight_color_button.get_sensitive()
    color=Gdk.RGBA();color.parse('#33b366')
    w.highlight_color_button.set_rgba(color)
    w.highlight_color_button.emit('color-set')
    w.highlight_button.emit('clicked')
    assert w.view_mode
    assert len(annotations())==1
    assert len(annotations()[0]['vertices'])==6*4
    assert all(abs(a-b)<.01 for a,b in zip(annotations()[0]['colors']['stroke'],(.2,.702,.4)))
    assert w.document_modified
    assert w.lookup_action('undo').get_enabled()
def undo():
    w.on_key_pressed(None,Gdk.KEY_z,0,Gdk.ModifierType.CONTROL_MASK)
    assert not annotations()
    assert w.lookup_action('redo').get_enabled()
def redo():
    w.on_key_pressed(None,Gdk.KEY_z,0,Gdk.ModifierType.CONTROL_MASK|Gdk.ModifierType.SHIFT_MASK)
    assert len(annotations())==1
def drag_highlight():
    w.highlight_button.emit('clicked')
    assert w.tool_mode=='highlighter'
    items=characters(w.doc[0])
    center=lambda index:(items[index][1].rect.tl+items[index][1].rect.br)/2
    # Away from the text, so the later eraser clicks hit only the annotations.
    a,b=center(7)+(0,40),center(10)+(0,40)
    ox=max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2)
    oy=max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2)
    w.on_drag_begin(Mock(),a.x*w.zoom_level+ox,a.y*w.zoom_level+oy)
    w.on_drag_update(Mock(),(b.x-a.x)*w.zoom_level,(b.y-a.y)*w.zoom_level)
    w.on_drag_end(Mock(),(b.x-a.x)*w.zoom_level,(b.y-a.y)*w.zoom_level)
    # The view-mode highlighter draws the same freehand marker as edit mode.
    assert w.view_mode
    assert len(annotations())==1
    assert len(w._highlight_strokes())==1
    w.on_key_pressed(None,Gdk.KEY_Escape,0,Gdk.ModifierType(0))
    assert w.tool_mode=='select'
    set_selection(7,10)
    w.highlight_button.emit('clicked')
    assert len(annotations())==2
    assert len(annotations()[-1]['vertices'])==4*4
def erase_tool():
    w._clear_view_selection()
    w.remove_highlight_button.emit('clicked')
    assert w.tool_mode=='erase_highlight'
    items=characters(w.doc[0])
    center=lambda index:(items[index][1].rect.tl+items[index][1].rect.br)/2
    ox=max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2)
    oy=max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2)
    def drag(a,b):
        w.on_pdf_view_pressed(None,1,a.x*w.zoom_level+ox,a.y*w.zoom_level+oy)
        w.on_drag_begin(Mock(),a.x*w.zoom_level+ox,a.y*w.zoom_level+oy)
        w.on_drag_update(Mock(),(b.x-a.x)*w.zoom_level,(b.y-a.y)*w.zoom_level)
        w.on_drag_end(Mock(),(b.x-a.x)*w.zoom_level,(b.y-a.y)*w.zoom_level)
    # Dragging over part of a highlight keeps the rest of it.
    drag(center(1),center(3))
    assert len(annotations())==2
    assert 0<len(annotations()[0]['vertices'])<6*4
    assert all(abs(a-b)<.01 for a,b in zip(annotations()[0]['colors']['stroke'],(.2,.702,.4)))
    # A plain click deletes the whole highlight under the pointer.
    drag(center(8),center(8))
    assert len(annotations())==1
    w.on_key_pressed(None,Gdk.KEY_z,0,Gdk.ModifierType.CONTROL_MASK)
    w.on_key_pressed(None,Gdk.KEY_z,0,Gdk.ModifierType.CONTROL_MASK)
    assert [len(item['vertices']) for item in annotations()]==[6*4,4*4]
    w.on_key_pressed(None,Gdk.KEY_Escape,0,Gdk.ModifierType(0))
    assert w.tool_mode=='select'
def remove():
    w.on_key_pressed(None,Gdk.KEY_a,0,Gdk.ModifierType.CONTROL_MASK)
    assert w.document_view_stack.get_visible_child_name()=='document'
    items=characters(w.doc[0])
    rect=items[0][1].rect
    center=(rect.tl+rect.br)/2
    x=center.x*w.zoom_level+max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2)
    y=center.y*w.zoom_level+max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2)
    gesture=Gtk.GestureClick.new()
    w.pdf_view.add_controller(gesture)
    w._on_right_click(gesture,1,x,y)
    button=w.context_popover.get_child().get_first_child().get_next_sibling().get_next_sibling()
    assert button.get_sensitive()
    button.emit('clicked')
    assert not annotations()
def finish():
    w.lookup_action('undo').activate(None)
    assert len(annotations())==2
    with fitz.open(stream=w.doc.tobytes(),filetype='pdf') as saved:
        assert len(list(saved[0].annots()))==2
    assert w._mutate_document(lambda:w.doc.set_metadata({'title':'Blocked'})) is False
    w._active_session.can_edit=False
    set_selection(0,5)
    for button in (w.highlight_button,w.remove_highlight_button,w.highlight_color_button):
        assert not button.get_sensitive()
    w.on_highlight_clicked(None)
    w.on_remove_highlight_clicked(None)
    assert len(annotations())==2
    print('View highlighting, glyph positions, color, drag highlighting, eraser tool, remove, undo/redo, and save passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((600,highlight),(1000,undo),(1300,redo),(1600,drag_highlight),(1700,erase_tool),(1800,w._toggle_fullscreen),(2100,remove),(2500,finish)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(2900,lambda:(w.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
