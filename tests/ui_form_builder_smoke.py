"""Real GTK workflow: build a form with quick fields, labels, guides, copies, style matching,
tab order and field detection."""
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio
import pymupdf as fitz
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler, document_features as features, form_builder
from pdflx.ops.formbehaviour import parse

app=Adw.Application(application_id='org.pdflx.TestFormBuilder',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
window.doc,_=pdf_handler.create_new_pdf()
window.view_mode=False
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]
forms=window.form_tools
panel=forms.builder
form_builder.save_style({'label_position':'above','snap':True,'fill_color':(0.97,0.98,0.99),'border_color':(0.8,0.85,0.9)})

def fields():
    return {f['name']:f for f in features.list_form_fields(window.doc,[0])}

def draw(preset,x0,y0,x1,y1,label='',required=False,**extra):
    panel.buttons[preset].set_active(True)
    assert window.tool_mode=='form_create'
    panel.label.set_text(label);panel.required.set_active(required)
    if 'choices' in extra:panel.choices.set_text(extra['choices'])
    if 'url' in extra:panel.url.set_text(extra['url'])
    assert forms.begin_drag(x0,y0)
    forms.update_drag(x1-x0,y1-y0)
    forms.end_drag(x1-x0,y1-y0)

def build():
    window.lookup_action('form_fields').activate(None)
    assert forms.sidebar.get_reveal_child()
    assert panel.label.has_focus() or panel.label.get_focus_child() is not None or window.get_focus() is not None
    draw('text',40,130,290,152,'First Name',True)
    f=fields()['first_name']
    assert f['required'] and abs(f['fill_color'][0]-0.97)<0.01 and abs(f['border_color'][2]-0.9)<0.01,f
    assert 'First Name *' in window.doc[0].get_text(),'label with required star is drawn above'
    assert f['tooltip']=='First Name'
    label=[t for t in window.editable_texts if t.text=='First Name *'][0]
    assert label.bbox[3]<=130 and abs(label.bbox[0]-40)<0.5
    # The next field snaps its left edge to the first one (drawn 3 pt off).
    draw('email',43,180,290,202,'Email')
    assert abs(fields()['email']['rect'][0]-40)<0.01,fields()['email']['rect']
    assert 'event.rc = false' in window.doc.xref_get_key(fields()['email']['xref'],'AA/V/JS')[1] or \
        window.doc.xref_get_key(fields()['email']['xref'],'AA/V')[0]!='null'
    draw('date',305,130,425,152,'Departure Date')
    assert parse(window.doc,fields()['departure_date']['xref'])['format']=='date'
    draw('dropdown',440,130,555,152,'Room',choices='Double, Single, Suite')
    assert fields()['room']['choices']==['Double','Single','Suite']
    # A click (no drag) places the default size.
    draw('checkbox',40,230,40,230,'Insurance')
    box=fields()['insurance']['rect']
    assert abs(box[2]-box[0]-14)<0.01
    draw('multiline',40,270,40,270,'Notes')
    assert fields()['notes']['multiline']
    draw('submit',180,400,180,400,'Send booking',url='http://localhost:5000/api/submit')
    send=fields()['send_booking']
    assert send['button_action']=='submit' and send['button_target']['url']=='http://localhost:5000/api/submit'
    panel.buttons['submit'].set_active(False)
    assert window.tool_mode=='select'

def undo_once():
    count=len(fields())
    draw('phone',305,180,555,202,'Phone')
    assert len(fields())==count+1 and 'Phone' in window.doc[0].get_text()
    window.lookup_action('undo').activate(None)
    assert len(fields())==count and 'Phone' not in window.doc[0].get_text(),'one undo removes field and label'
    panel.stop()

def copies_and_style():
    forms.interaction.select(fields()['insurance'])
    forms.multiple_copies([fields()['insurance']],3,1,0,8)
    names=fields()
    assert 'insurance_2' in names and 'insurance_3' in names,(window.status_label.get_text(),sorted(names))
    assert abs(names['insurance_2']['rect'][1]-(230+14+8))<0.01
    # Match style: the dropdown takes the email field's style.
    from pdflx.document_tools import edit_form_field
    email=fields()['email']
    window._mutate_document(lambda:edit_form_field(window.doc,0,email['xref'],'email',False,0,options={'fill_color':(1,0.9,0.9),'font_size':12}))
    forms.interaction.select(fields()['email'])
    forms.interaction.select(fields()['room'],extend=True)
    forms.arrange_fields('match_style')
    room=fields()['room']
    assert abs(room['fill_color'][1]-0.9)<0.01 and room['font_size']==12,room

def tab_order():
    panel.set_tab_order('rows')
    order=[f['name'] for f in features.list_form_fields(window.doc,[0])]
    assert order.index('first_name')<order.index('departure_date')<order.index('room')<order.index('email'),order
    assert window.doc.xref_get_key(window.doc[0].xref,'Tabs')[1]=='/R'

def detection():
    doc,_=pdf_handler.create_new_pdf()
    page=doc[0]
    page.insert_text((50,95),'Full name:',fontsize=10)
    page.draw_rect(fitz.Rect(120,82,400,100),color=(0.5,0.5,0.5))
    page.insert_text((50,145),'City',fontsize=10)
    page.draw_line((50,170),(300,170),color=(0,0,0))
    page.draw_rect(fitz.Rect(50,200,62,212),color=(0,0,0))
    page.insert_text((70,210),'Subscribe',fontsize=10)
    window.open_generated_document(doc)
    if window.view_mode:window._toggle_view_edit_mode()
    found=form_builder.detect_fields(window.doc[0])
    kinds=sorted(kind for kind,_r in found)
    assert kinds==['checkbox','text','text'],found
    import pdflx.dialogs as dialogs
    original=dialogs.alert
    dialogs.alert=lambda *args,**kw:args[6]('create') if len(args)>6 else kw['callback']('create')
    try:
        panel.detect()
    finally:
        dialogs.alert=original
    names=sorted(fields())
    assert names==['city','full_name','subscribe'],names
    assert fields()['full_name']['tooltip']=='Full name'

def ai_fields():
    import json
    text,_=window.ai_bar.tools.run('add_elements',{'page':1,'elements':[
        {'type':'field','kind':'text','name':'ai_email','format':'email','x':40,'y':600,'width':200,'height':22,
         'fill_color':'#f7fafc','border_color':'#cdd9e5','font_size':10},
        {'type':'field','kind':'combo','name':'ai_room','choices':['Double','Suite'],'x':260,'y':600,'width':120,'height':22},
        {'type':'field','kind':'text','name':'ai_notes','multiline':True,'x':40,'y':630,'width':300,'height':40},
        {'type':'field','kind':'button','name':'ai_send','action':'submit','url':'http://localhost:5000/api/submit',
         'label':'SUBMIT','x':200,'y':690,'width':180,'height':30,'fill_color':'#149473'},
    ]})
    assert 'error' not in json.loads(text),text
    f=fields()
    assert abs(f['ai_email']['fill_color'][0]-0xf7/255)<0.01 and f['ai_email']['font_size']==10
    assert window.doc.xref_get_key(f['ai_email']['xref'],'AA/V')[0]!='null','email validation script'
    assert f['ai_notes']['multiline'] and f['ai_room']['choices']==['Double','Suite']
    assert f['ai_send']['button_action']=='submit' and f['ai_send']['button_caption']=='SUBMIT'
    bad,_=window.ai_bar.tools.run('add_elements',{'page':1,'elements':[{'type':'field','kind':'button','name':'x','action':'submit','x':1,'y':1,'width':50,'height':20}]})
    assert 'submit button needs' in bad,bad

def scripts():
    from pdflx.form_scripts_ui import FieldScriptsDialog, current_script
    field=fields()['first_name']
    dialog=FieldScriptsDialog(forms,field)
    dialog.insert_snippet('K','if (!event.willCommit) event.change = event.change.toUpperCase();')
    events=[k for k,*_r in __import__('pdflx.ops.javascript',fromlist=['x']).FIELD_EVENTS]
    assert events[dialog.event_row.get_selected()]=='K'
    dialog._on_apply_clicked(None)
    assert 'toUpperCase' in current_script(window.doc,fields()['first_name']['xref'],'K')
    # The email preset's validation script is listed and can be cleared.
    email=fields()['email']
    dialog=FieldScriptsDialog(forms,email)
    assert events[dialog.event_row.get_selected()]=='V' and 'event.rc = false' in dialog.editor.get_buffer().get_property('text')
    dialog.editor.get_buffer().set_text('')
    dialog._on_apply_clicked(None)
    assert current_script(window.doc,fields()['email']['xref'],'V')==''
    window.lookup_action('undo').activate(None)
    assert 'event.rc' in current_script(window.doc,fields()['email']['xref'],'V'),'scripts edits undo'
    # From the Tools list with a selected field.
    forms.interaction.select(fields()['first_name'])
    panel.tool_rows['scripts'].callback()

steps=[build,undo_once,copies_and_style,tab_order,scripts,ai_fields,detection]
def run():
    try:
        steps.pop(0)()
    except Exception as error:
        import traceback;traceback.print_exc();errors.append(error);steps.clear()
    if steps:return True
    loop.quit();return False
GLib.timeout_add(300,run)
loop.run()
if errors:raise SystemExit(1)
print('form builder workflow ok')
