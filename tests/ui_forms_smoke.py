"""Real GTK workflow: draw fields, fill them on page, reposition, and bookmarks."""
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio
from types import SimpleNamespace
from unittest.mock import Mock
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler,document_features as features,document_tools as tools
app=Adw.Application(application_id='org.pdflx.TestForms',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
window.doc,_=pdf_handler.create_new_pdf(num_pages=2)
window.view_mode=False
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]
def widgets(widget,kind):
    result=[widget] if isinstance(widget,kind) else []
    child=widget.get_first_child()
    while child:
        result.extend(widgets(child,kind))
        child=child.get_next_sibling()
    return result
def row_subtitle(row):
    return row.get_subtitle()
def dialog():
    return window.get_visible_dialog()
def open_create():
    window.lookup_action('create_field').activate(None)
    assert dialog().get_title()=='Create Form Field'
    assert widgets(dialog(),Gtk.Notebook)[0].get_current_page()==0
    notebook=dialog().form_notebook
    assert [notebook.get_tab_label_text(notebook.get_nth_page(i)) for i in range(notebook.get_n_pages())]==['Field','Behaviour','Appearance','Options','Format & Calculation']
    kind=widgets(dialog(),Adw.ComboRow)[0]
    assert kind.get_model().get_n_items()==7
    options=notebook.get_nth_page(3)
    for index in range(7):
        kind.set_selected(index)
        assert options.get_visible()==(index in (2,3,4))
    kind.set_selected(0)
def draw():
    dialog().response(Gtk.ResponseType.APPLY)
    assert window.tool_mode=='form_create'
    gesture=SimpleNamespace(set_state=Mock())
    dx=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    dy=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    window.on_drag_begin(gesture,dx+80,dy+150)
    window.on_drag_update(gesture,180,32)
    window.on_drag_end(gesture,180,32)
    fields=features.list_form_fields(window.doc)
    assert len(fields)==1
    assert fields[0]['rect']==(80,150,260,182)
    assert window.tool_mode=='select'
    assert window.form_tools.sidebar.get_reveal_child()
def click():
    dx=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    dy=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    window.on_pdf_view_pressed(None,1,dx+100,dy+160)
    editor=window.form_tools.inline_editor
    assert editor is not None
    assert window.get_visible_dialog() is None
    editor.control.set_text('Jane Doe')
def save_field():
    assert window.form_tools.finish_inline()
    field=features.list_form_fields(window.doc)[0]
    assert field['name']=='Field 1' and field['value']=='Jane Doe'
    window.form_tools.edit_field(field)
def reposition():
    button=next(button for button in widgets(dialog(),Gtk.Button) if button.get_label()=='Move / resize on page')
    button.emit('clicked')
    assert window.tool_mode=='form_reposition'
    gesture=SimpleNamespace(set_state=Mock())
    dx=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    dy=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    window.on_drag_begin(gesture,dx+100,dy+200)
    window.on_drag_end(gesture,220,40)
    field=features.list_form_fields(window.doc)[0]
    assert field['rect']==(100,200,320,240)
    assert field['value']=='Jane Doe'
    window.undo_manager.undo()
    assert features.list_form_fields(window.doc)[0]['rect']==(80,150,260,182)
    tools.create_form_field(window.doc,0,'Consent','checkbox',(80,260,100,280))
    window.form_tools.refresh()
    dx=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    dy=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    window.on_pdf_view_pressed(None,1,dx+90,dy+270)
    field=features.list_form_fields(window.doc)[1]
    assert field['value']==field['on_state']
    window.lookup_action('bookmarks').activate(None)
def add_bookmark():
    entry=widgets(dialog(),Adw.EntryRow)[0]
    entry.set_text('Invoice')
    widgets(dialog(),Gtk.SpinButton)[0].set_value(2)
    next(button for button in widgets(dialog(),Gtk.Button) if button.get_label()=='Add Bookmark').emit('clicked')
    assert window.doc.get_toc()[0][:3]==[1,'Invoice',2]
    dialog().force_close()
    window.view_mode=True
    window._update_ui_state()
    window.lookup_action('bookmarks').activate(None)
def navigate():
    buttons=widgets(dialog(),Gtk.Button)
    add=next(button for button in buttons if button.get_label()=='Add Bookmark')
    assert not add.is_sensitive()
    go=next(row for row in widgets(dialog(),Adw.ActionRow) if row.get_title()=='Invoice')
    assert row_subtitle(go)=='Page 2'
    go.emit('activated')
    assert window.current_page_index==1
    print('Forms drawing, filling, resizing, checkbox page clicks, and bookmark navigation passed',flush=True)
def configure_combo():
    window.view_mode=False
    window._update_ui_state()
    window.lookup_action('create_field').activate(None)
    widgets(dialog(),Gtk.Entry)[0].set_text('Department')
    widgets(dialog(),Adw.ComboRow)[0].set_selected(2)
    choices=next(view for view in widgets(dialog(),Gtk.TextView)
                 if view.get_buffer().get_text(view.get_buffer().get_start_iter(),view.get_buffer().get_end_iter(),True).startswith('Option 1'))
    assert choices.get_parent().get_visible()
def draw_combo():
    dialog().response(Gtk.ResponseType.APPLY)
    gesture=SimpleNamespace(set_state=Mock())
    dx=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    dy=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    window.on_drag_begin(gesture,dx+80,dy+100)
    window.on_drag_end(gesture,180,32)
    field=next(field for field in features.list_form_fields(window.doc) if field['name']=='Department')
    assert field['choices']==['Option 1','Option 2']
    window.form_tools.edit_field(field)
    widgets(dialog(),Gtk.DropDown)[0].set_selected(1)
def save_combo():
    dialog().response(Gtk.ResponseType.APPLY)
    field=next(field for field in features.list_form_fields(window.doc) if field['name']=='Department')
    assert field['value']=='Option 2'
    window.form_tools.interaction.select(field)
    from gi.repository import Gdk
    assert window.on_key_pressed(None,Gdk.KEY_d,0,Gdk.ModifierType.CONTROL_MASK)
    copied=window.form_tools.interaction.current()
    assert copied['name']=='Department copy'
    assert copied['value']=='Option 2'
    assert copied['choices']==field['choices']
    window.undo_manager.undo()
    assert not any(f['name']=='Department copy' for f in features.list_form_fields(window.doc))
    print('Dropdown creation and page filling passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
for delay,callback in ((400,open_create),(800,draw),(1200,click),(1600,save_field),(2000,reposition),(2400,add_bookmark),(2800,navigate),(3200,configure_combo),(3600,draw_combo),(4000,save_combo)):
    GLib.timeout_add(delay,run,callback)
GLib.timeout_add(4400,lambda:(window.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
