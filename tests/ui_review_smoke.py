import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio,Gdk
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler,document_tools as tools
app=Adw.Application(application_id='org.pdflx.TestNotes',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
window.doc,_=pdf_handler.create_new_pdf(num_pages=2)
window.doc[0].insert_text((50,100),'Inline text')
window.view_mode=False
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]
def dialogs():
    return ([window.get_visible_dialog()] if window.get_visible_dialog() else [])
def text_views(widget):
    result=[widget] if isinstance(widget,Gtk.TextView) else []
    child=widget.get_first_child()
    while child:
        result.extend(text_views(child))
        child=child.get_next_sibling()
    return result
def create():
    window.document_tools.start_note()
    window.document_tools.place_note(100,150)
    dialog=dialogs()[-1]
    text_views(dialog)[0].get_buffer().set_text('First line\nSecond line')
def apply_created():
    dialogs()[-1].response(Gtk.ResponseType.APPLY)
    assert tools.annotations(window.doc)[0]['content']=='First line\nSecond line'
    assert window.doc[0].load_annot(tools.annotations(window.doc)[0]['xref']).info['name']=='Comment'
def click(x,y):
    # A real click runs the press handler and a zero-length drag.
    from unittest.mock import Mock
    window.on_pdf_view_pressed(None,1,x,y)
    window.on_drag_begin(Mock(),x,y)
    window.on_drag_end(Mock(),0,0)
def open_note():
    window.view_mode=True
    note=tools.annotations(window.doc)[0]
    import pymupdf as fitz
    center=fitz.Rect(note['rect']).tl+(9,9)
    x=center.x*window.zoom_level+max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    y=center.y*window.zoom_level+max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    click(x,y)
    bubble=window.document_tools.note_bubble
    assert bubble.get_has_arrow()
    assert not text_views(bubble)[0].get_editable()
def close_readonly():
    bubble=window.document_tools.note_bubble
    paintable=Gtk.WidgetPaintable.new(bubble)
    snapshot=Gtk.Snapshot()
    paintable.snapshot(snapshot,bubble.get_width(),bubble.get_height())
    node=snapshot.to_node()
    if node:
        window.get_renderer().render_texture(node,None).save_to_png('/tmp/comment-bubble.png')
    window.document_tools.close_note_bubble()
    window.view_mode=False
    note=tools.annotations(window.doc)[0]
    import pymupdf as fitz
    center=fitz.Rect(note['rect']).tl+(9,9)
    x=center.x*window.zoom_level+max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    y=center.y*window.zoom_level+max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    click(x,y)
    bubble=window.document_tools.note_bubble
    assert text_views(bubble)[0].get_editable()
    buffer=text_views(bubble)[0].get_buffer()
    buffer.set_text('Edited note\nAdditional details')
def apply_edited():
    window.document_tools.note_save_button.emit('clicked')
    assert tools.annotations(window.doc)[0]['content']=='Edited note\nAdditional details'
    row=window.document_tools.rows.get_first_child()
    row.get_first_child().get_next_sibling().get_next_sibling().get_first_child().grab_focus()
def refresh():
    window.document_tools._comments_state=None
    window._load_page(0)
    window._show_inline_editor(window.editable_texts[0])
def hide():
    assert window._inline_editor_focus_source is None
    window._load_page(1)
    window._load_page(0)
    window._show_inline_editor(window.editable_texts[0])
    window.hide_text_editor()
    assert window._inline_editor_focus_source is None
    print('Note placement, page clicks in both modes, multiline edits, sidebar focus, and inline focus passed',flush=True)
def forms_and_stamp():
    assert window.sidebar_box.get_visible()
    window.hide_pages_button.emit('clicked')
    assert not window.sidebar_box.get_visible()
    assert not window.toggle_sidebar_button.has_css_class('active')
    window.toggle_sidebar_button.emit('clicked')
    assert window.sidebar_box.get_visible()
    assert window.toggle_sidebar_button.has_css_class('active')
    assert window.on_key_pressed(None, Gdk.KEY_F9, 0, 0)
    assert not window.sidebar_box.get_visible()
    assert window.on_key_pressed(None, Gdk.KEY_F9, 0, 0)
    assert window.sidebar_box.get_visible()
    assert window.forms_tool_button.get_visible()
    forms=window.forms_tool_button.get_popover().get_child()
    actions=[]
    child=forms.get_first_child()
    while child:
        actions.append(child.get_action_name())
        child=child.get_next_sibling()
    assert actions==['win.form_fields','win.create_field','win.flatten_forms']
    assert window.stamp_tool_button.get_visible()
    shape_menu = window.shapes_tool_button.get_popover()
    for button in (window.add_rectangle_tool_button, window.add_ellipse_tool_button,
                   window.checkmark_tool_button, window.cross_tool_button):
        assert button.get_parent() is shape_menu.get_child()
        assert button.get_parent() is not window.tools_sidebar
    window.view_mode=True
    window._update_ui_state()
    assert not window.lookup_action('stamp').get_enabled()
    editing_actions=('create_field','form_fields','flatten_forms','document_properties',
                     'redact','review','sticky_note','decorate','crop','add_table','paste_table','add_signature',
                     'rotate_page_cw','rotate_page_ccw','undo','redo')
    for action in editing_actions:
        assert not window.lookup_action(action).get_enabled(),action
    for button in (window.forms_tool_button,window.document_tools_button,
                   window.rotate_page_cw_button,window.rotate_page_ccw_button):
        assert not button.get_sensitive()
    assert window.highlight_button.get_sensitive()
    # The eraser is a tool, so it is usable before anything is selected.
    assert window.remove_highlight_button.get_sensitive()
    assert window.select_tool_button.get_sensitive()
    assert window.drag_tool_button.get_sensitive()
    assert not window.shapes_tool_button.get_sensitive()
    assert window.highlight_color_button.get_sensitive()
    assert not window.forms_tool_button.get_sensitive()
    for action in ('comments','bookmarks','search','print','export_page_png','save_as'):
        assert window.lookup_action(action).get_enabled(),action
    original_rotation=window.doc[0].rotation
    window.rotate_current_page(90)
    assert window.doc[0].rotation==original_rotation
    window.view_mode=False
    window._update_ui_state()
    assert window.forms_tool_button.get_sensitive()
    assert window.document_tools_button.get_sensitive()
    for action in editing_actions:
        if action not in ('undo','redo'):
            assert window.lookup_action(action).get_enabled(),action
    window.lookup_action('stamp').activate(None)
    assert dialogs()[-1].get_title()=='Add Stamp'
def apply_stamp():
    dialogs()[-1].response(Gtk.ResponseType.APPLY)
    assert window.tool_mode=='stamp'
    x=200*window.zoom_level+max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    y=300*window.zoom_level+max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    window.on_pdf_view_pressed(None,1,x,y)
    assert any(row['kind']=='Stamp' for row in tools.annotations(window.doc))
    window.undo_manager.undo()
    assert not any(row['kind']=='Stamp' for row in tools.annotations(window.doc))
    window.undo_manager.redo()
    assert any(row['kind']=='Stamp' for row in tools.annotations(window.doc))
    print('Forms toolbar and click-to-place stamps passed',flush=True)
def edit_stamp():
    assert window.tool_mode=='stamp'
    note=next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')
    import pymupdf as fitz
    from types import SimpleNamespace
    from unittest.mock import Mock
    original=fitz.Rect(note['rect'])
    visual=original*window.doc[0].rotation_matrix
    center=(visual.tl+visual.br)/2
    x=center.x*window.zoom_level+max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    y=center.y*window.zoom_level+max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    window.on_pdf_view_pressed(None,1,x,y)
    assert not dialogs(), 'Single click should select, leaving dragging available'
    gesture=SimpleNamespace(set_state=Mock())
    window.on_drag_begin(gesture,x,y)
    window.on_drag_update(gesture,40*window.zoom_level,25*window.zoom_level)
    assert window.stamp_interaction.preview_doc is not None
    import cairo
    width,height=window.pdf_view.get_width(),window.pdf_view.get_height()
    surface=cairo.ImageSurface(cairo.FORMAT_ARGB32,width,height)
    window.draw_pdf_page(window.pdf_view,cairo.Context(surface),width,height)
    assert next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')['rect']==tuple(original)
    window.on_drag_end(gesture,40*window.zoom_level,25*window.zoom_level)
    moved=next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')
    assert abs(moved['rect'][0]-original.x0-40)<0.01
    assert abs(moved['rect'][1]-original.y0-25)<0.01
    window.undo_manager.undo()
    assert next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')['rect']==tuple(original)
    window.undo_manager.redo()
    window.on_tool_selected(None,'select')
    window.on_pdf_view_pressed(None,1,x+40*window.zoom_level,y+25*window.zoom_level)
    note=next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')
    before=fitz.Rect(note['rect'])
    offset_x=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    offset_y=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    corner_x=before.x1*window.zoom_level+offset_x
    corner_y=before.y1*window.zoom_level+offset_y
    window._on_pointer_motion(None,corner_x,corner_y)
    assert window.pdf_view.get_cursor().get_name()=='nwse-resize'
    window.on_pdf_view_pressed(None,1,corner_x+3,corner_y+3)
    assert not dialogs()
    window.on_drag_begin(gesture,corner_x+3,corner_y+3)
    assert window.stamp_interaction.drag['handle']=='se'
    dx,dy=before.width*0.25*window.zoom_level,before.height*0.25*window.zoom_level
    window.on_drag_update(gesture,dx,dy)
    window.draw_pdf_page(window.pdf_view,cairo.Context(surface),width,height)
    window.on_drag_end(gesture,dx,dy)
    resized=fitz.Rect(next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')['rect'])
    assert abs(resized.width-before.width*1.25)<0.01
    assert abs(resized.height-before.height*1.25)<0.01
    window.undo_manager.undo()
    assert next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')['rect']==tuple(before)
    window.undo_manager.redo()
    window.on_pdf_view_pressed(None,1,x+40*window.zoom_level,y+25*window.zoom_level)
    from pdflx.stamp_rotation import tilt_info
    import math
    note=next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')
    bounds=fitz.Rect(note['rect'])
    rotate_x=(bounds.x0+bounds.x1)/2*window.zoom_level+offset_x
    rotate_y=bounds.y0*window.zoom_level+offset_y-24
    window._on_pointer_motion(None,rotate_x,rotate_y)
    assert window.pdf_view.get_cursor().get_name()=='crosshair'
    window.on_pdf_view_pressed(None,1,rotate_x,rotate_y)
    assert not dialogs()
    window.on_drag_begin(gesture,rotate_x,rotate_y)
    assert window.stamp_interaction.drag['handle']=='rotate'
    radius=bounds.height*window.zoom_level/2+24
    dx,dy=radius*0.5,radius*(1-math.cos(math.pi/6))
    window.on_drag_update(gesture,dx,dy)
    window.draw_pdf_page(window.pdf_view,cairo.Context(surface),width,height)
    window.on_drag_end(gesture,dx,dy)
    assert abs(tilt_info(window.doc,note['xref'])-30)<0.01
    window.undo_manager.undo()
    assert tilt_info(window.doc,note['xref'])==0
    window.undo_manager.redo()
    window.on_pdf_view_pressed(None,2,x+40*window.zoom_level,y+25*window.zoom_level)
    assert dialogs()[-1].get_title()=='Edit Stamp Style'
    dialog=dialogs()[-1]
    dialog.text_row.set_text('REVIEW COMPLETE')
    dialog.set_shape('seal')
def save_style():
    dialogs()[-1].response(Gtk.ResponseType.APPLY)
    stamp=next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')
    assert stamp['content']=='REVIEW COMPLETE'
    from pdflx.stamp_rotation import tilt_info
    assert abs(tilt_info(window.doc,stamp['xref'])-30)<0.01
    import json
    assert json.loads(window.doc.xref_get_key(stamp['xref'],'PdfLXStampStyle')[1])['shape']=='seal'
    window.undo_manager.undo()
    stamp=next(row for row in tools.annotations(window.doc) if row['kind']=='Stamp')
    assert stamp['content']=='APPROVED'
    print('View/Edit gating and stamp style editing passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((400,create),(750,apply_created),(1100,open_note),(1500,close_readonly),(1900,apply_edited),(2300,refresh),(2700,hide),(3000,forms_and_stamp),(3400,apply_stamp),(3800,edit_stamp),(4200,save_style)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(4600,lambda:(window.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
