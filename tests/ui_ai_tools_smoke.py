"""Real GTK workflow: the AI document tools and the agent loop with a scripted model (no network)."""
import json
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio
import pymupdf as fitz
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler
from pdflx.ai import client, config
from pdflx.ai.agent import load_attachment

app=Adw.Application(application_id='org.pdflx.TestAiTools',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
window.doc,_=pdf_handler.create_new_pdf()
window.doc[0].insert_text((72,100),'Original heading',fontsize=20)
window.view_mode=False
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]
bar=window.ai_bar
tools=bar.tools

# A small red/blue reference picture as an attachment.
pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,200,100),False)
pix.set_rect(fitz.IRect(0,0,100,100),(220,30,30));pix.set_rect(fitz.IRect(100,0,200,100),(30,30,220))
reference=load_attachment('ref.png',pix.tobytes('png'))
bar.conversation.attachments.extend(reference)

def call(name,**args):
    text,images=tools.run(name,args)
    data=json.loads(text)
    assert 'error' not in data,(name,data)
    return data,images

def text_of(page_index):
    return window.doc[page_index].get_text()

def painted_after(page,later,earlier,area=None):
    log=[kind for kind,rect in page.get_bboxlog() if area is None or fitz.Rect(rect).intersects(area)]
    return later in log and earlier in log and max(i for i,k in enumerate(log) if k==later)>max(i for i,k in enumerate(log) if k==earlier)

def user_layering():
    # Send to Back puts a filled rectangle under the PDF's original (never edited) text.
    from pdflx.models import EditableShape
    from pdflx.undo_manager import AddObjectCommand
    from pdflx.layering import restack
    window._load_page(0)
    original=[t for t in window.editable_texts if 'heading' in t.text][0]
    x0,y0,x1,y1=original.bbox
    shape=EditableShape('rectangle',(x0-5,y0-5,x1+5,y1+5),fill_color=(0.7,0.9,0.8),stroke_color=(0.7,0.9,0.8),
                        stroke_width=0,page_number=0,is_new=True,is_transparent=False)
    command=AddObjectCommand(window,shape);command.execute();window.undo_manager.add_command(command)
    area=fitz.Rect(shape.bbox)
    assert painted_after(window.doc[0],'fill-path','fill-text',area)
    assert restack(window,shape,False)
    assert painted_after(window.doc[0],'fill-text','fill-path',area),window.doc[0].get_bboxlog()
    assert 'Edited heading' in text_of(0)
    window.lookup_action('undo').activate(None)
    assert painted_after(window.doc[0],'fill-path','fill-text',area)

def original_layering():
    # The PDF's own text (never edited) is lifted above a shape sent behind it.
    from pdflx.models import EditableShape
    from pdflx.undo_manager import AddObjectCommand
    from pdflx.layering import restack
    doc,_=pdf_handler.create_new_pdf()
    doc[0].insert_text((72,700),'Untouched original line',fontsize=14)
    window.open_generated_document(doc)
    if window.view_mode:window._toggle_view_edit_mode()
    plain=[t for t in window.editable_texts if 'Untouched' in t.text][0]
    x0,y0,x1,y1=plain.bbox
    band=EditableShape('rectangle',(x0-5,y0-5,x1+5,y1+5),fill_color=(0.7,0.9,0.8),stroke_color=(0.7,0.9,0.8),
                       stroke_width=0,page_number=0,is_new=True,is_transparent=False)
    command=AddObjectCommand(window,band);command.execute();window.undo_manager.add_command(command)
    area=fitz.Rect(band.bbox)
    assert painted_after(window.doc[0],'fill-path','fill-text',area)
    assert restack(window,band,False)
    assert painted_after(window.doc[0],'fill-text','fill-path',area),window.doc[0].get_bboxlog()
    assert window.doc[0].get_text().count('Untouched original line')==1,'lifted text is not duplicated'
    window.lookup_action('undo').activate(None)
    assert painted_after(window.doc[0],'fill-path','fill-text',area)

def tools_work():
    info,_=call('get_document_info')
    assert info['page_count']==1
    listing,_=call('list_elements',page=1)
    heading=[e for e in listing['elements'] if e['type']=='text' and 'Original' in e['text']][0]
    undo_before=len(window.undo_manager.undo_stack)
    added,_=call('add_elements',page=1,elements=[
        {'type':'text','text':'Hello from AI','x':72,'y':200,'size':18,'font':'Liberation Serif','bold':True,'color':'#cc0000'},
        {'type':'text','text':'A long paragraph that should wrap inside a narrow box for sure','x':72,'y':260,'size':11,'width':120},
        {'type':'rectangle','x':300,'y':200,'width':100,'height':40,'fill':'#00aa00'},
        {'type':'line','x1':72,'y1':240,'x2':400,'y2':240,'color':'#333333','stroke_width':2},
        {'type':'image','attachment':1,'content':'logo','crop':[100,0,200,100],'x':72,'y':400,'width':80,'height':80},
        {'type':'field','kind':'text','name':'ai_field','x':300,'y':300,'width':150,'height':20},
    ])
    assert len(added['added'])==6,added
    assert 'Hello from AI' in text_of(0)
    wrapped=[t for t in window.editable_texts if t.text.startswith('A long')][0]
    assert '\n' in wrapped.text,'width wraps text'
    ids=added['added']
    edited,_=call('edit_elements',page=1,edits=[
        {'id':heading['id'],'text':'Edited heading','color':'#0000ff'},
        {'id':ids[0],'x':80,'y':210,'size':24},
        {'id':ids[2],'width':50,'fill':None,'stroke':'#000000'},
        {'id':ids[5],'value':'filled'},
    ])
    assert 'Edited heading' in text_of(0) and 'Original heading' not in text_of(0),text_of(0)
    hello=[t for t in window.editable_texts if t.text=='Hello from AI'][0]
    assert abs(hello.bbox[0]-80)<.01 and abs(hello.bbox[1]-210)<.01 and hello.font_size==24
    rect=[s for s in window.editable_shapes if getattr(s,'_ai_id',None)==ids[2]][0]
    assert rect.is_transparent and abs(rect.bbox[2]-rect.bbox[0]-50)<.01
    call('delete_elements',page=1,ids=[ids[3]])
    assert not any(getattr(s,'_ai_id',None)==ids[3] for s in window.editable_strokes)
    page,_=call('add_page',size='letter')
    assert window.doc.page_count==2 and page['page']==2 and abs(window.doc[1].rect.width-612)<.1
    call('add_elements',page=2,elements=[{'type':'text','text':'Second page','x':50,'y':50,'size':12}])
    assert 'Second page' in text_of(1)
    shot,images=call('render_page',page=1,grid=True)
    assert images and images[0][:4]==b'\x89PNG'
    _,images=call('view_attachment',attachment=1,region=[0,0,100,100],grid=True)
    assert images
    strip,_=tools.run('add_elements',{'page':1,'elements':[{'type':'image','attachment':1,'content':'logo',
        'crop':[0,40,200,55],'x':72,'y':500,'width':400,'height':30}]})
    assert 'must be rebuilt as text' in strip,strip
    unlabeled,_=tools.run('add_elements',{'page':1,'elements':[{'type':'image','attachment':1,'crop':[0,0,50,50],
        'x':72,'y':500,'width':50,'height':50}]})
    assert 'need \\"content\\" set' in unlabeled,unlabeled
    whole,_=tools.run('add_elements',{'page':1,'elements':[{'type':'image','attachment':1,'content':'logo',
        'x':72,'y':500,'width':50,'height':50}]})
    assert 'crop' in whole,whole
    bad,_=tools.run('edit_elements',{'page':1,'edits':[{'id':'zzz','x':1}]})
    assert 'Unknown element ids' in bad
    # A filled band added after its text must still be painted under it.
    call('add_elements',page=2,elements=[{'type':'text','text':'On the band','x':60,'y':300,'size':16}])
    call('add_elements',page=2,elements=[{'type':'rectangle','x':50,'y':290,'width':300,'height':40,'fill':'#b7dfc9'}])
    assert painted_after(window.doc[1],'fill-text','fill-path'),window.doc[1].get_bboxlog()
    order=[e['type'] for e in call('list_elements',page=2)[0]['elements']]
    assert order[0]=='rectangle',order
    band=[e for e in call('list_elements',page=2)[0]['elements'] if e['type']=='rectangle'][0]
    call('edit_elements',page=2,edits=[{'id':band['id'],'layer':'front'}])
    assert painted_after(window.doc[1],'fill-path','fill-text')
    tools.end_turn()
    stack=window.undo_manager.undo_stack
    assert len(stack)==undo_before+1,'one undo step for the whole request'
    window.lookup_action('undo').activate(None)
    assert window.doc.page_count==1 and 'Original heading' in text_of(0) and 'Hello from AI' not in text_of(0),text_of(0)
    window.lookup_action('redo').activate(None)
    assert window.doc.page_count==2 and 'Hello from AI' in text_of(0) and 'Second page' in text_of(1)

scripted=[]
def fake_chat(base_url,key,model,messages,definitions):
    assert messages[0]['role']=='system' and definitions
    return scripted.pop(0),{'total_tokens':100,'cost':0.001},'stop'

def agent_loop():
    original=client.chat
    client.chat=fake_chat
    config.api_key=lambda:'test-key'
    config.model=lambda:'test/model'
    scripted.extend([
        {'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{
            'name':'add_elements','arguments':json.dumps({'page':1,'elements':[{'type':'text','text':'Agent was here','x':72,'y':600,'size':14}]})}}]},
        {'role':'assistant','content':None,'tool_calls':[{'id':'c2','type':'function','function':{
            'name':'render_page','arguments':json.dumps({'page':1})}}]},
        {'role':'assistant','content':'Added the text.'},
    ])
    window._load_page(0)
    bar.show()
    bar.input.get_buffer().set_text('Add a note')
    bar.submit()
    state['restore']=original

state={}
def agent_done():
    if bar.run is not None:
        steps.insert(0,agent_done);return
    client.chat=state['restore']
    assert 'Agent was here' in text_of(0)
    roles=[m['role'] for m in bar.conversation.messages]
    assert roles==['user','assistant','tool','assistant','tool','user','assistant'],roles
    assert bar.conversation.messages[5].get('_tool_images')
    children=[]
    child=bar.log.get_first_child()
    while child:
        children.append(child);child=child.get_next_sibling()
    assert len(children)==3,children  # the request, one steps row, the answer
    steps=children[1]
    assert isinstance(steps,Gtk.Expander) and steps.get_label()=='2 steps' and not steps.get_expanded()
    step_texts=[]
    label=steps.get_child().get_first_child()
    while label:
        step_texts.append(label.get_text());label=label.get_next_sibling()
    assert step_texts==['• Added 1 element on page 1','• Looked at page 1'],step_texts
    assert children[2].get_last_child().get_text()=='Added the text.'
    # Collapse hides the conversation; docking moves the bar to the side.
    bar.toggle_collapsed()
    assert not bar.scroller.get_visible()
    bar.toggle_collapsed()
    assert bar.scroller.get_visible()
    docked=bar.docked
    bar.toggle_docked()
    assert bar.root.get_halign()==(Gtk.Align.CENTER if docked else Gtk.Align.END)
    bar.toggle_docked()
    payload=bar.conversation.payload()
    assert all(not any(k.startswith('_') for k in m) for m in payload)

steps=[tools_work,user_layering,agent_loop,agent_done,original_layering]
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
print('ai tools workflow ok')
