"""Dragging and resizing a large table redraws every frame without errors."""
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw,GLib,Gio,Gtk,Gdk
import cairo
from unittest.mock import Mock
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
from pdflx.table_creation import create_table_objects
from pdflx.undo_manager import AddTableCommand
app=Adw.Application(application_id='org.pdflx.TestTableDrag',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w=PdfEditorWindow(application=app)
w.doc,_=pdf_handler.create_new_pdf()
w.view_mode=False
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop=GLib.MainLoop()
errors=[]
def frame():
    # Exceptions in a GTK draw callback are only printed; call it directly so they fail.
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,w.pdf_view.get_width(),w.pdf_view.get_height())
    w.draw_pdf_page(w.pdf_view,cairo.Context(surface),surface.get_width(),surface.get_height())
def point(x,y):
    ox=max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2)
    oy=max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2)
    return x*w.zoom_level+ox,y*w.zoom_level+oy
def drag(x,y,dx,dy,shift=False,cursor=None):
    gesture=Gtk.GestureClick.new()
    w.pdf_view.add_controller(gesture)
    sx,sy=point(x,y)
    w.on_pdf_view_pressed(gesture,1,sx,sy)
    drag_gesture=Mock()
    drag_gesture.get_current_event_state.return_value=Gdk.ModifierType.SHIFT_MASK if shift else Gdk.ModifierType(0)
    w.on_drag_begin(drag_gesture,sx,sy)
    for step in range(1,6):
        w.on_drag_update(drag_gesture,dx*w.zoom_level*step/5,dy*w.zoom_level*step/5)
        frame()
        if cursor:
            w._on_pointer_motion(None,sx+dx*w.zoom_level*step/5,sy+dy*w.zoom_level*step/5)
            assert w.pdf_view.get_cursor().get_name()==cursor,(w.pdf_view.get_cursor().get_name(),cursor)
    w.on_drag_end(Mock(),dx*w.zoom_level,dy*w.zoom_level)
    frame()
    w.pdf_view.remove_controller(gesture)
def table_drag():
    cells=[[f'R{r}C{c} value' for c in range(6)] for r in range(15)]
    command=AddTableCommand(w,create_table_objects(w.doc[0],cells))
    command.execute()
    w.undo_manager.add_command(command)
    text=[obj for obj in w.editable_texts if getattr(obj,'table_id',None)][3]
    x0,y0,x1,y1=text.bbox
    hover=point((x0+x1)/2,(y0+y1)/2)
    w._on_pointer_motion(None,*hover)
    assert w.pdf_view.get_cursor().get_name()=='move'
    drag((x0+x1)/2,(y0+y1)/2,30,20,cursor='move')
    table=w.selected_table
    assert table is not None
    bx0,by0,bx1,by1=table.bbox
    # Moving far right stops at the page edge.
    drag((x0+x1)/2+30,(y0+y1)/2+20,400,0)
    page=w.doc[0].cropbox
    assert abs(w.selected_table.bbox[2]-page.width)<0.5,w.selected_table.bbox
    w.lookup_action('undo').activate(None)
    table=w.selected_table or table
    w._select_table(table)
    sizes=lambda:sorted({round(obj.font_size,2) for obj in w.selected_table.objects if hasattr(obj,'baseline')})
    fonts=sizes()
    # The left side handle widens only: same height, same text size.
    bx0,by0,bx1,by1=table.bbox
    drag(bx0,(by0+by1)/2,-60,0,cursor='ew-resize')
    nx0,ny0,nx1,ny1=w.selected_table.bbox
    assert abs((nx1-nx0)-(bx1-bx0+60))<0.5 and abs((ny1-ny0)-(by1-by0))<0.5,(table.bbox,w.selected_table.bbox)
    assert sizes()==fonts
    # A corner resizes freely...
    bx0,by0,bx1,by1=w.selected_table.bbox
    drag(bx0,by1,20,60,cursor='nesw-resize')
    nx0,ny0,nx1,ny1=w.selected_table.bbox
    assert abs((nx1-nx0)-(bx1-bx0-20))<0.5 and abs((ny1-ny0)-(by1-by0+60))<0.5,(bx0,by0,bx1,by1,w.selected_table.bbox)
    # ...and keeps proportions with Shift.
    bx0,by0,bx1,by1=w.selected_table.bbox
    drag(bx0,by1,-30,10,shift=True)
    nx0,ny0,nx1,ny1=w.selected_table.bbox
    assert abs((nx1-nx0)/(bx1-bx0)-(ny1-ny0)/(by1-by0))<0.01,(bx0,by0,bx1,by1,w.selected_table.bbox)
    assert nx0>=-0.5
    double_click_into_cell()
    style_dialog_with_long_values()
    delete_large_table()
    print('Large table drag, page-edge clamp, free and proportional resize, cursors and frame drawing passed',flush=True)
def double_click_into_cell():
    # The second press of a double-click may start a table drag before the click
    # handler opens the cell editor; releasing must not crash.
    text=[obj for obj in w.selected_table.objects if hasattr(obj,'baseline') and obj.text][7]
    sx,sy=point((text.bbox[0]+text.bbox[2])/2,(text.bbox[1]+text.bbox[3])/2)
    gesture=Gtk.GestureClick.new()
    w.pdf_view.add_controller(gesture)
    w.on_pdf_view_pressed(gesture,1,sx,sy)
    w.on_drag_begin(Mock(),sx,sy)
    w.on_drag_end(Mock(),0,0)
    w.on_drag_begin(Mock(),sx,sy)
    assert w.table_drag_state is not None
    w.on_pdf_view_pressed(gesture,2,sx,sy)
    w.on_drag_end(Mock(),0,0)
    assert w.table_drag_state is None
    frame()
    w._apply_and_hide_editor(force_apply=True)
    w.pdf_view.remove_controller(gesture)
def delete_large_table():
    # Deleting a whole table rebuilds the page once, not once per cell.
    import time
    from pdflx.element_menu import ElementMenu
    table=w.selected_table
    count=len(table.objects)
    assert count>=180
    page=w.doc[0]
    before=len(page.get_drawings())
    start=time.perf_counter()
    ElementMenu(w).delete('table',table)
    assert time.perf_counter()-start<3,time.perf_counter()-start
    assert w.selected_table is None
    assert not any(obj is member for member in table.objects for obj in w.editable_texts+w.editable_shapes)
    page=w.doc[0]
    assert len(page.get_drawings())<before
    start=time.perf_counter()
    w.lookup_action('undo').activate(None)
    assert time.perf_counter()-start<3
    page=w.doc[0]
    assert len(page.get_drawings())==before
    # The Delete key removes a selected table too.
    w._select_table(table)
    w.on_key_pressed(None,Gdk.KEY_Delete,0,Gdk.ModifierType(0))
    page=w.doc[0]
    assert len(page.get_drawings())<before
    w.lookup_action('undo').activate(None)
    frame()
def style_dialog_with_long_values():
    from pdflx.element_menu import ElementMenu
    from pdflx.table_style_dialog import TableStyleDialog
    cells=[['Index','Customer Id','First Name','Last Name','Sex','Email']]
    cells+=[[str(r),'1bA7A3dc874da3c'+str(r),'Lori','Todd','Male',f'buchananmanuel{r}@example.net'] for r in range(14)]
    command=AddTableCommand(w,create_table_objects(w.doc[0],cells,font_size=9))
    command.execute()
    w.undo_manager.add_command(command)
    table=w._find_table_at_pos(*[(v[0]+v[2])/2 for v in [command.objects[0].bbox]],
                               *[(v[1]+v[3])/2 for v in [command.objects[0].bbox]])
    w._select_table(table)
    # Narrow the table so the long values no longer fit at their size.
    bx0,by0,bx1,by1=table.bbox
    drag(bx1,(by0+by1)/2,-(bx1-bx0)*0.35,0)
    table=w.selected_table
    dialog=TableStyleDialog(ElementMenu(w),table)
    dialog.size.set_value(9)
    dialog.preset.set_selected(2)
    assert dialog.picture.get_paintable() is not None,dialog.message.get_text()
    assert 'smaller size' in dialog.message.get_text(),dialog.message.get_text()
    assert dialog.apply_style() is not False
    texts=[obj for obj in table.objects if hasattr(obj,'baseline') and obj.text]
    assert all(4<=obj.font_size<=9 for obj in texts)
    assert any(obj.font_size<9 for obj in texts)
    frame()
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
GLib.timeout_add(700,run,table_drag)
GLib.timeout_add(1500,lambda:(w.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
