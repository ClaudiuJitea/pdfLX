"""Real GTK workflow: multi-select form fields, align, size, distribute and move them together."""
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio,Gdk
from types import SimpleNamespace
from unittest.mock import Mock
import pymupdf as fitz
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler,document_features as features,document_tools as tools
from pdflx.form_arrange import arrange

page=fitz.Rect(0,0,600,800)
rects=arrange([(100,100,300,120),(130,140,250,170),(90,200,200,215)],'left',page)
assert [r.x0 for r in rects]==[100,100,100]
rects=arrange([(100,100,300,120),(130,140,250,170)],'size',page)
assert rects[1]==fitz.Rect(130,140,330,160)
rects=arrange([(10,0,30,10),(40,0,50,10),(100,0,120,10)],'distribute_h',page)
assert [r.x0 for r in rects]==[10,60,100],rects
rects=arrange([(500,0,580,10),(560,20,580,30)],'left',page)
assert rects[1].x0==500
rects=arrange([(100,0,600,10),(560,20,580,30)],'width',page)
assert rects[1]==fitz.Rect(100,20,600,30),'Resizing past the page edge moves the field back on'

app=Adw.Application(application_id='org.pdflx.TestFormArrange',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
window.doc,_=pdf_handler.create_new_pdf()
for index,rect in enumerate([(100,100,350,125),(110,150,330,180),(95,220,260,236)]):
    tools.create_form_field(window.doc,0,f'field{index}','text',rect)
window.view_mode=False
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]
forms=window.form_tools
bar=forms.arrange_bar

def fields():
    return {f['name']:fitz.Rect(f['rect']) for f in features.list_form_fields(window.doc,[0])}

def click(name,extend=False):
    field=next(f for f in features.list_form_fields(window.doc,[0]) if f['name']==name)
    r=fitz.Rect(field['rect'])
    ox=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
    oy=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
    state=Gdk.ModifierType.SHIFT_MASK if extend else Gdk.ModifierType(0)
    gesture=SimpleNamespace(get_current_event_state=lambda:state,set_state=Mock())
    window.on_pdf_view_pressed(gesture,1,ox+(r.x0+r.x1)/2*window.zoom_level,oy+(r.y0+r.y1)/2*window.zoom_level)
    forms.finish_inline()

def select_two():
    click('field0');click('field1',True)
    assert [f['name'] for f in forms.interaction.fields()]==['field0','field1']

def bar_shown():
    assert bar.frame.get_visible(),'Arrange bar must appear for two fields'
    assert not bar.buttons['distribute_h'].get_sensitive()
    assert bar.count.get_text()=='2 fields'

def align_left():
    bar.buttons['left'].emit('clicked')
    after=fields()
    assert after['field1'].x0==after['field0'].x0==100,after
    bar.buttons['size'].emit('clicked')
    after=fields()
    assert (after['field1'].width,after['field1'].height)==(250,25),after
    window.lookup_action('undo').activate(None)
    assert fields()['field1'].width==220,'Each arrange action is one undo step'
    window.lookup_action('redo').activate(None)

def add_third():
    click('field2',True)
    assert len(forms.interaction.fields())==3

def bar_three():
    assert bar.count.get_text()=='3 fields' and bar.buttons['distribute_v'].get_sensitive()
    bar.buttons['distribute_v'].emit('clicked')
    after=fields()
    gaps=(after['field1'].y0-after['field0'].y1,after['field2'].y0-after['field1'].y1)
    assert abs(gaps[0]-gaps[1])<.01,gaps

def group_move():
    before=fields()
    interaction=forms.interaction
    field=interaction.current()
    r=fitz.Rect(field['rect'])
    handle=interaction.points(r)['move']
    assert interaction.begin(*handle)
    interaction.update(20,10);interaction.end(20,10)
    after=fields()
    for name in before:
        assert abs(after[name].x0-before[name].x0-20)<.01 and abs(after[name].y0-before[name].y0-10)<.01,(name,before,after)

def toggle_off():
    click('field2',True)
    assert [f['name'] for f in forms.interaction.fields()]==['field0','field1']
    click('field1')
    assert [f['name'] for f in forms.interaction.fields()]==['field1']

def bar_hidden():
    assert not bar.frame.get_visible()

steps=[select_two,bar_shown,align_left,add_third,bar_three,group_move,toggle_off,bar_hidden]
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
print('form arrange workflow ok')
