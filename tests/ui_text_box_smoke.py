"""Check growing text bounds, corner resizing, and editing without duplicates."""
import copy
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio
import pymupdf as fitz
import cairo
from pdflx.window import PdfEditorWindow
from pdflx.models import EditableText
from pdflx import pdf_handler

app=Adw.Application(application_id='org.pdflx.TestTextBox',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
Gtk.Settings.get_default().set_property('gtk-enable-animations',False)
window=PdfEditorWindow(application=app)
window.set_default_size(1300,950)
window.doc,_=pdf_handler.create_new_pdf()
window.doc[0].insert_text((80,90),'Neighbour stays')
window.view_mode=False
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]
obj=EditableText(80,150,'Hi',font_size=20,is_new=True,baseline=168,page_number=0)
tilt_before=None

def draw():
    width,height=window.pdf_view.get_width(),window.pdf_view.get_height()
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
    window.draw_pdf_page(window.pdf_view,cairo.Context(surface),width,height)

def start():
    window.selected_text=obj
    window._show_inline_editor(obj)

def type_text():
    previous=copy.deepcopy(obj.__dict__)
    bounds=fitz.Rect(window._inline_editor_bounds)
    window.inline_editor_tv.get_buffer().set_text('Growing text box with several words')
    wide=fitz.Rect(window._inline_editor_bounds)
    assert wide.width>bounds.width*2
    window.inline_editor_tv.get_buffer().set_text('Growing text box with several words\nSecond line')
    assert fitz.Rect(window._inline_editor_bounds).height>wide.height
    assert obj.__dict__==previous, 'Typing preview must not mutate the model'
    draw()

def commit_text():
    bounds=fitz.Rect(window._inline_editor_bounds)
    assert window.inline_editor_widget.get_width()>=bounds.width*window.zoom_level-2,(window.inline_editor_widget.get_width(),bounds.width,window.zoom_level)
    paintable=Gtk.WidgetPaintable.new(window.pdf_overlay)
    snapshot=Gtk.Snapshot()
    paintable.snapshot(snapshot,window.pdf_overlay.get_width(),window.pdf_overlay.get_height())
    node=snapshot.to_node()
    if node:
        window.get_renderer().render_texture(node,None).save_to_png('/tmp/text-box-growing.png')
    window._commit_inline_edit()
    assert obj.text=='Growing text box with several words\nSecond line'
    assert sum(item is obj for item in window.editable_texts)==1

def resize():
    window.on_tool_selected(None,'select')
    window.selected_text=obj
    old=copy.deepcopy(obj.__dict__)
    x0,y0,x1,y1=obj.bbox
    offset_x=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    offset_y=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    x=(x1+3/window.zoom_level)*window.zoom_level+offset_x
    y=(y1+3/window.zoom_level)*window.zoom_level+offset_y
    assert window._find_resize_handle_at_pos(x,y,obj)=='se'
    window.on_pdf_view_pressed(None,1,x,y)
    assert window.selected_text is obj
    gesture=SimpleNamespace(set_state=Mock())
    window.on_drag_begin(gesture,x,y)
    assert window.resize_handle=='se'
    dx=(x1-x0)*0.2*window.zoom_level
    dy=(y1-y0)*0.2*window.zoom_level
    window.on_drag_update(gesture,dx,dy)
    assert abs(obj.font_size-old['font_size']*1.2)<0.01
    draw()
    window.on_drag_end(gesture,dx,dy)
    assert abs(obj.bbox[2]-obj.bbox[0]-(x1-x0)*1.2)<0.01
    assert abs(obj.baseline-(y0+(old['baseline']-y0)*1.2))<0.01
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'resized.pdf'
        assert pdf_handler.save_document(window.doc,str(path))==(True,None)
        with fitz.open(path) as saved:
            assert 'Growing text box' in saved[0].get_text()
            spans=[span for block in saved[0].get_text('dict')['blocks'] if 'lines' in block
                   for line in block['lines'] for span in line['spans']]
            assert any(abs(span['size']-24)<0.1 for span in spans)
    window.undo_manager.undo()
    assert obj.bbox==old['bbox'] and obj.font_size==old['font_size']
    window.undo_manager.redo()
    window._show_inline_editor(obj)
    window.inline_editor_tv.get_buffer().set_text('Updated text')
    window._commit_inline_edit()
    assert sum(item is obj for item in window.editable_texts)==1
    window.undo_manager.undo()
    assert obj.text==old['text']

def wrap_and_cancel():
    before=copy.deepcopy(obj.__dict__)
    window._show_inline_editor(obj)
    window.inline_editor_tv.get_buffer().set_text('Long text to wrap at the available page boundary. '*8)
    assert '\n' in window._inline_editor_wrapped_text
    draw()
    window.hide_text_editor()
    assert obj.__dict__==before
    assert window._inline_editor_bounds is None
    window._show_inline_editor(obj)
    content='Long text to wrap at the available page boundary. '*8
    window.inline_editor_tv.get_buffer().set_text(content)
    wrapped=window._inline_editor_wrapped_text
    assert wrapped.replace('\n','')==content
    window._commit_inline_edit()
    assert obj.text==wrapped
    assert obj.text.count('\n')>2
    window.undo_manager.undo()
    assert obj.text==before['text']
    print('Text bounds grow; resizing, save/undo, wrapping, and editing without duplicates passed',flush=True)

def tilted_preview():
    global tilt_before
    from pdflx.undo_manager import RotateObjectCommand
    command=RotateObjectCommand(window,obj,obj.rotation,30)
    assert command.execute() is not False
    window.undo_manager.add_command(command)
    tilt_before=window.doc[0].get_pixmap().samples
    window._show_inline_editor(obj)
    preview=window._inline_editor_preview_doc
    assert preview is not None
    assert 'Growing text box' not in preview[0].get_text()
    assert 'Neighbour stays' in preview[0].get_text()
    draw()

def finish_tilted_preview():
    paintable=Gtk.WidgetPaintable.new(window.pdf_overlay)
    snapshot=Gtk.Snapshot()
    paintable.snapshot(snapshot,window.pdf_overlay.get_width(),window.pdf_overlay.get_height())
    node=snapshot.to_node()
    if node:
        window.get_renderer().render_texture(node,None).save_to_png('/tmp/tilted-text-fixed.png')
    window.hide_text_editor()
    assert window.doc[0].get_pixmap().samples==tilt_before
    print('Tilted text preview removes old text and preserves neighbours',flush=True)

def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((700,start),(1100,type_text),(1800,commit_text),(2400,resize),(2900,tilted_preview),(3400,finish_tilted_preview),(3900,wrap_and_cancel)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(4400,lambda:(window.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
