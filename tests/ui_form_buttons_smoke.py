"""The form panel's Button tile places a button with the chosen action."""
import gi
gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
from gi.repository import Adw,GLib,Gio
import pymupdf as fitz
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler, document_features as features, form_buttons, form_builder as builder_module
app=Adw.Application(application_id='org.pdflx.TestFormButtons',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w=PdfEditorWindow(application=app)
w.doc,_=pdf_handler.create_new_pdf()
w.doc.new_page()
w.doc.new_page()
w.view_mode=False
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop=GLib.MainLoop()
errors=[]
def action_of(name):
    field=next(f for f in features.list_form_fields(w.doc) if f['name']==name)
    return form_buttons.details(w.doc,field['xref'])
def place(action,y,url='',label='',script=''):
    builder=w.form_tools.builder
    builder.label.set_text(label)
    builder.buttons['button'].set_active(True)
    assert builder.preset=='button'
    assert builder.action_row.get_visible()
    builder.action.set_selected(builder_module.BUTTON_ACTIONS.index(action))
    builder.url.set_text(url)
    builder.script.set_text(script)
    before={f['name'] for f in features.list_form_fields(w.doc)}
    builder.place(fitz.Rect(0,0,0,0),(60,y))
    after={f['name'] for f in features.list_form_fields(w.doc)}
    created=after-before
    return created.pop() if created else None
def buttons():
    builder=w.form_tools.builder
    assert [k for k in builder.buttons if k in ('submit','button','signature')]==['submit','button','signature']
    for gone in ('reset','print','link','goto'):
        assert gone not in builder.buttons,gone
    name=place('reset',60)
    assert action_of(name)==('reset',None),action_of(name)
    name=place('print',100)
    assert action_of(name)==('print',None),action_of(name)
    assert not builder.url.get_visible() and not builder.page_row.get_visible()
    # A link needs an address first.
    assert place('url',150) is None
    assert builder.url.get_visible()
    name=place('url',150,url='example.com/help')
    assert action_of(name)==('url','https://example.com/help'),action_of(name)
    builder.action.set_selected(builder_module.BUTTON_ACTIONS.index('goto'))
    assert builder.page_row.get_visible() and not builder.url.get_visible()
    builder.page.set_value(3)
    builder.label.set_text('Terms')
    before={f['name'] for f in features.list_form_fields(w.doc)}
    builder.place(fitz.Rect(0,0,0,0),(60,200))
    name=({f['name'] for f in features.list_form_fields(w.doc)}-before).pop()
    assert action_of(name)==('goto',2),action_of(name)
    page=w.doc[0]
    captions={widget.field_name:widget.button_caption for widget in page.widgets()}
    assert captions[name]=='Terms',captions
    # A script button needs its script.
    assert place('javascript',250) is None
    assert builder.script.get_visible()
    name=place('javascript',250,script='app.alert("Hi")')
    assert action_of(name)==('javascript','app.alert("Hi")'),action_of(name)
    builder.stop()
    assert not builder.page_row.get_visible() and not builder.action_row.get_visible()
    assert not builder.script.get_visible()
    with fitz.open(stream=w.doc.tobytes(),filetype='pdf') as saved:
        kinds=[form_buttons.details(saved,widget.xref)[0] for widget in saved[0].widgets()]
    assert sorted(kinds)==['goto','javascript','print','reset','url'],kinds
    print('Generic button placed with each action passed',flush=True)
def run(callback):
    try: callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False
GLib.timeout_add(800,run,buttons)
GLib.timeout_add(1500,lambda:(w.destroy(),loop.quit(),False)[2])
loop.run()
if errors: raise SystemExit(1)
