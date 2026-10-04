"""Real GTK workflow: a submit button posts the form to a local server after confirmation."""
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio
import pymupdf as fitz
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler, document_tools as tools, document_features as features
from pdflx.form_buttons import configure
from pdflx.form_submit import payload

received=[]
class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body=self.rfile.read(int(self.headers['Content-Length']))
        received.append((self.path,self.headers['Content-Type'],body))
        status=500 if self.path=='/fail' else 200
        self.send_response(status);self.send_header('Content-Type','text/html');self.end_headers()
        self.wfile.write(b'<html><body><h1>Thanks!</h1><p>Trip booked.</p></body></html>' if status==200 else b'oops')
    def log_message(self,*args):pass
server=HTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=server.serve_forever,daemon=True).start()
base=f'http://127.0.0.1:{server.server_port}'

app=Adw.Application(application_id='org.pdflx.TestSubmit',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
doc,_=pdf_handler.create_new_pdf()
tools.create_form_field(doc,0,'name','text',(72,72,300,92),required=True)
tools.create_form_field(doc,0,'agree','checkbox',(72,110,90,128))
button=tools.create_form_field(doc,0,'send','button',(72,150,200,180),value='Submit')
configure(doc,button,{'button_action':'submit','button_url':base+'/api/submit','button_format':'html'})
window.doc=doc
window.view_mode=True
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]
dialogs=[]
import pdflx.dialogs as dialog_module
original_alert=dialog_module.alert
def recording_alert(window_,heading,body,responses,default=None,close='cancel',callback=None,extra=None):
    dialogs.append((heading,body,[r[0] for r in responses],callback))
dialog_module.alert=recording_alert

def field(name):
    return next(f for f in features.list_form_fields(window.doc) if f['name']==name)

def click_button():
    window.form_tools.click_field(field('send'))

def required_blocks():
    click_button()
    heading,body,responses,_cb=dialogs.pop()
    assert 'required' in body and 'name' in body,body
    assert not received

def fill():
    features.update_form_fields(window.doc,{(0,field('name')['xref']):'Sarah Connor',(0,field('agree')['xref']):True})

def confirm_and_send():
    click_button()
    heading,body,responses,callback=dialogs.pop()
    assert responses==['cancel','export','submit'] and base+'/api/submit' in body and 'HTML' in body,body
    assert 'not encrypted' not in body,'local addresses are not flagged'
    callback('submit')

def wait_for_answer():
    if not dialogs:
        steps.insert(0,wait_for_answer);return
    heading,body,responses,_cb=dialogs.pop()
    assert heading=='Form submitted' and 'HTTP 200' in body and 'Trip booked.' in body and '<' not in body,body
    path,content_type,data=received.pop()
    assert path=='/api/submit' and content_type=='application/x-www-form-urlencoded'
    values=urllib.parse.parse_qs(data.decode())
    assert values['name']==['Sarah Connor'] and values['agree']==['Yes'],values

def failure():
    configure(window.doc,field('send')['xref'],{'button_action':'submit','button_url':base+'/fail','button_format':'xfdf'})
    click_button()
    dialogs.pop()[3]('submit')

def wait_for_failure():
    if not dialogs:
        steps.insert(0,wait_for_failure);return
    heading,body,responses,_cb=dialogs.pop()
    assert heading=='Form not submitted' and 'HTTP 500' in body and responses==['close','export','retry'],(heading,body)
    path,content_type,data=received.pop()
    assert content_type=='application/vnd.adobe.xfdf' and b'Sarah Connor' in data

def formats():
    for fmt,marker in (('fdf',b'%FDF'),('xfdf',b'<xfdf'),('pdf',b'%PDF'),('html',b'name=')):
        data,_type=payload(window.doc,fmt,'trip.pdf')
        assert marker in data[:200],fmt

def remote_http_warns():
    configure(window.doc,field('send')['xref'],{'button_action':'submit','button_url':'http://example.org/submit','button_format':'fdf'})
    click_button()
    heading,body,responses,callback=dialogs.pop()
    assert 'not encrypted' in body,body
    callback('cancel')
    assert not received

steps=[required_blocks,fill,confirm_and_send,wait_for_answer,failure,wait_for_failure,formats,remote_http_warns]
def run():
    try:
        steps.pop(0)()
    except Exception as error:
        import traceback;traceback.print_exc();errors.append(error);steps.clear()
    if steps:return True
    loop.quit();return False
GLib.timeout_add(300,run)
loop.run()
dialog_module.alert=original_alert
server.shutdown()
if errors:raise SystemExit(1)
print('form submit workflow ok')
