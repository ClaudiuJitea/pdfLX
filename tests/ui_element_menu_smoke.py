"""Right-click actions for each editable element, with GTK lifetime checks."""
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio
import pymupdf as fitz
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler,document_tools as tools,document_features as features
from pdflx.models import EditableText,EditableShape,EditableImage,EditableStroke
from pdflx.undo_manager import AddObjectCommand,AddTableCommand
from pdflx.table_creation import create_table_objects,TableSelection

app=Adw.Application(application_id='org.pdflx.TestElementMenus',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
Gtk.Settings.get_default().set_property('gtk-enable-animations',False)
window=PdfEditorWindow(application=app)
window.set_default_size(1300,1000)
window.doc,_=pdf_handler.create_new_pdf()
window.view_mode=False
window._load_page(0)
text=EditableText(80,80,'Menu text',font_size=18,is_new=True,baseline=96,page_number=0)
text.bbox=(80,80,190,103)
shape=EditableShape('rectangle',(80,200,190,250),page_number=0,is_new=True)
pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,20,20),False);pix.clear_with(150)
image=EditableImage((350,80,420,140),0,None,pix.tobytes('png'),is_new=True)
stroke=EditableStroke([(80,300),(180,310)],page_number=0,is_new=True)
for obj in (text,shape,image,stroke):
    command=AddObjectCommand(window,obj);command.execute();window.undo_manager.add_command(command)
objects=create_table_objects(window.doc[0],[['Name','Value'],['Alpha','123']],width_percent=40)
table=TableSelection(objects)
import copy
table.transform([copy.deepcopy(obj.__dict__) for obj in objects],table.bbox,(300,440,550,530))
command=AddTableCommand(window,objects);command.execute();window.undo_manager.add_command(command)
tools.place_stamp(window.doc,0,(350,250),style={'text':'DRAFT','shape':'ribbon','angle':20})
tools.add_review(window.doc,0,'note',(80,540,110,570),'Review comment')
tools.create_form_field(window.doc,0,'Name','text',(300,600,480,632))
window._load_page(0,reload_objects=False)
window.present()
loop=GLib.MainLoop();errors=[]

def dialogs():
    return ([window.get_visible_dialog()] if window.get_visible_dialog() else [])

def open_menu(point):
    x=point[0]*window.zoom_level+max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    y=point[1]*window.zoom_level+max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    window._on_right_click(None,1,x,y)
    pop=window.context_popover
    assert pop and pop.get_parent() is window.pdf_view
    result={}
    child=pop.get_child().get_first_child()
    while child:
        if isinstance(child,Gtk.Button): result[child.get_label()]=child
        child=child.get_next_sibling()
    assert {'Duplicate','Delete','Properties…'}<=result.keys(),result.keys()
    return result

def counts():
    return tuple(len(group) for group in (window.editable_texts,window.editable_shapes,window.editable_images,window.editable_strokes))+(
        len(tools.annotations(window.doc)),len(features.list_form_fields(window.doc)))

def check_actions(point):
    before=counts()
    open_menu(point)['Duplicate'].emit('clicked')
    assert counts()!=before
    window.undo_manager.undo()
    assert counts()==before
    open_menu(point)['Delete'].emit('clicked')
    assert counts()!=before
    assert not dialogs(), 'Fast deletion must not open a confirmation dialog'
    window.undo_manager.undo()
    assert counts()==before

def properties():
    open_menu((120,220))['Properties…'].emit('clicked')
    dialog=dialogs()[-1]
    assert dialog.get_title()=='Properties'

def apply_properties():
    # Wait for the dialog to map before activating its Apply button, as an
    # actual click would. Wayland exports its parent handle asynchronously.
    dialog=dialogs()[-1]
    def spins(widget):
        result=[widget] if isinstance(widget,Gtk.SpinButton) else []
        child=widget.get_first_child()
        while child:
            result.extend(spins(child));child=child.get_next_sibling()
        return result
    before=shape.bbox
    spins(dialog)[2].set_value(before[2]-before[0]+20)
    dialog.response(Gtk.ResponseType.APPLY)
    assert abs(shape.bbox[2]-shape.bbox[0]-(before[2]-before[0]+20))<0.01
    window.undo_manager.undo()
    assert shape.bbox==before
    print('Right-click duplicate/delete for all element types and properties passed',flush=True)

def run(callback,*args):
    try: callback(*args)
    except Exception as error:
        import traceback
        traceback.print_exc();errors.append(error);loop.quit()
    return False

points=[(120,90),(120,220),(385,110),(100,302),(425,485),(430,274),(89,549),(390,616)]
for index,point in enumerate(points): GLib.timeout_add(500+index*350,run,check_actions,point)
GLib.timeout_add(3500,run,properties)
GLib.timeout_add(3900,run,apply_properties)
GLib.timeout_add(4400,lambda:(window.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
