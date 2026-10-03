import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio,Gdk
from unittest.mock import Mock
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
from pdflx.reader_selection import characters
app=Adw.Application(application_id='org.pdflx.TestReaderSelection',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w=PdfEditorWindow(application=app)
w.doc,_=pdf_handler.create_new_pdf()
w.doc[0].insert_text((50,100),'Select this sentence')
w.doc[0].insert_text((50,130),'Another line')
w.view_mode=True
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop=GLib.MainLoop()
errors=[]
reads=[]
gesture=Gtk.GestureClick.new()
w.pdf_view.add_controller(gesture)
def point(item):
    rect=item[1].rect
    center=(rect.tl+rect.br)/2
    return (center.x*w.zoom_level+max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2),
            center.y*w.zoom_level+max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2))
def clipboard(expected):
    def read(cb,result):
        try:
            text=cb.read_text_finish(result)
            assert text==expected,(text,expected)
            reads.append(text)
        except Exception as error:
            errors.append(error)
            loop.quit()
    w.get_clipboard().read_text_async(None,read)
def select_range():
    global start
    items=characters(w.doc[0])
    start=point(items[7]);end=point(items[10])
    w.on_drag_begin(Mock(),*start)
    # Click and drag handlers may run in either order for a grouped gesture.
    w.on_pdf_view_pressed(gesture,1,*start)
    w.on_drag_update(Mock(),end[0]-start[0],0)
    w.on_drag_end(Mock(),end[0]-start[0],0)
    assert w.view_selected_text=='this',w.view_selected_text
    assert len(w._active_session.view_selection_quads)==4
    assert w.pdf_view.has_focus()
    w.on_key_pressed(None,Gdk.KEY_c,0,Gdk.ModifierType.CONTROL_MASK)
    clipboard('this')
def context():
    w._on_right_click(gesture,1,*start)
    button=w.context_popover.get_child().get_first_child()
    assert button.get_sensitive()
    button.emit('clicked')
    clipboard('this')
def middle_word():
    w._on_middle_click(gesture,1,*point(characters(w.doc[0])[15]))
    assert w.view_selected_text=='sentence'
    assert not w._active_session.view_selection_quads
    w.on_key_pressed(None,Gdk.KEY_c,0,Gdk.ModifierType.CONTROL_MASK)
    clipboard('sentence')
def fullscreen():
    w._toggle_fullscreen()
def all_text():
    assert w.document_view_stack.get_visible_child_name()=='document'
    w.on_key_pressed(None,Gdk.KEY_a,0,Gdk.ModifierType.CONTROL_MASK)
    assert w.view_selected_text=='Select this sentence\nAnother line'
    w.on_key_pressed(None,Gdk.KEY_c,0,Gdk.ModifierType.CONTROL_MASK)
    clipboard(w.view_selected_text)
def finish():
    assert len(reads)==4,reads
    assert not w.document_modified
    w._active_session.can_copy=False
    w.view_selected_text='Restricted text'
    w.on_key_pressed(None,Gdk.KEY_c,0,Gdk.ModifierType.CONTROL_MASK)
    clipboard('Select this sentence\nAnother line')
    assert w.doc[0].get_text().strip()=='Select this sentence\nAnother line'
    print('Reader horizontal selection, context copy, Ctrl+C, and fullscreen Ctrl+A passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((700,select_range),(1100,context),(1300,middle_word),(1600,fullscreen),(2000,all_text),(2400,finish)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(2900,lambda:(w.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
