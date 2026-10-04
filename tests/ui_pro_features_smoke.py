"""Real GTK workflow: Find & Replace, sensitive-data redaction, Organize Pages and smart guides."""
from types import SimpleNamespace
from unittest.mock import Mock
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Adw','1')
from gi.repository import Gtk,Adw,GLib,Gio
import pymupdf as fitz
from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler, document_tools as tools
from pdflx.models import EditableShape
from pdflx.undo_manager import AddObjectCommand

app=Adw.Application(application_id='org.pdflx.TestProFeatures',flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
window=PdfEditorWindow(application=app)
doc,_=pdf_handler.create_new_pdf(num_pages=3)
doc[0].insert_text((72,100),'Hello world, the world is round.',fontsize=12)
doc[0].insert_text((72,130),'Contact sarah@example.com or +1 (555) 345-6789',fontsize=11)
doc[1].insert_text((72,100),'World peace',fontsize=12)
doc[2].insert_text((72,100),'Page three',fontsize=12)
window.doc=doc
window.view_mode=False
window._load_page(0)
window.present()
loop=GLib.MainLoop()
errors=[]

def text(n):
    return window.doc[n].get_text()

def find_replace():
    from pdflx.find_replace import FindReplaceDialog
    dialog=FindReplaceDialog(window,'world')
    dialog.replace_entry.set_text('Earth')
    dialog.refresh()
    changes=dialog.state['changes']
    assert [c[0] for c in changes]==[0,1],changes
    assert changes[0][4]==2,'two matches in one line'
    dialog.checks[1].set_active(False)   # keep page 2
    before=len(window.undo_manager.undo_stack)
    dialog._on_apply_clicked(None)
    assert 'Hello Earth, the Earth is round.' in text(0) and 'World peace' in text(1),(text(0),text(1))
    assert len(window.undo_manager.undo_stack)==before+1
    window.lookup_action('undo').activate(None)
    assert 'Hello world, the world is round.' in text(0)
    # Regex with a captured group, case sensitive.
    dialog=FindReplaceDialog(window,'')
    dialog.find_entry.set_text(r'(\w+) peace')
    dialog.replace_entry.set_text(r'peace for \1')
    from pdflx.find_replace import Replacer
    replacer=Replacer(window)
    changes=replacer.matches(r'(\w+) peace',r'peace for \1',case=True,regex=True)
    assert changes and changes[0][3]=='peace for World',changes
    replacer.apply(changes)   # after an undo: fonts from the undone edit must still be usable
    assert 'peace for World' in text(1)
    literal=Replacer(window).matches('round','a\\1b')
    assert literal[0][3].endswith('a\\1b.'),literal
    window.lookup_action('undo').activate(None)

def redaction():
    from pdflx.ops import redact_patterns
    items,review=redact_patterns.targets(window.doc,0,2,{'email','phone'})
    assert sorted(key for _n,key,_t in review)==['email','phone'],review
    pages=sorted({page for page,_ in items})
    assert window._mutate_document(lambda:tools.apply_redactions(window,items,fill=(0,0,0)),rebase_pages=pages)
    assert 'sarah@example.com' not in text(0) and '345-6789' not in text(0) and 'Contact' in text(0),text(0)
    window.lookup_action('undo').activate(None)
    assert 'sarah@example.com' in text(0)

def organize():
    view=window.organize_pages
    window._load_page(0)
    window.lookup_action('organize_pages').activate(None)
    assert view.is_open() and view.selected()==[0],(view.is_open(),view.selected(),window.current_page_index)
    # In View mode the view explains why page actions are off and offers Edit.
    window._toggle_view_edit_mode();view._sync()
    assert window.view_mode and view.edit_button.get_visible() and not view.actions['delete'].get_sensitive()
    assert view.actions['extract'].get_sensitive(),'extracting works in View mode'
    view.edit_button.emit('clicked')
    assert not window.view_mode and not view.edit_button.get_visible() and view.actions['rotate_left'].get_sensitive()
    assert view.move([2],0)
    assert 'Page three' in text(0) and 'Hello' in text(1),'page 3 moved to the front'
    assert view.selected()==[0]
    window.lookup_action('undo').activate(None)
    assert 'Hello' in text(0) and 'Page three' in text(2)
    view.populate(select=[0,1])
    view.run('rotate_right')
    assert window.doc[0].rotation==90 and window.doc[1].rotation==90 and window.doc[2].rotation==0
    view.run('duplicate')
    assert window.doc.page_count==5 and view.selected()==[1,3]
    view.run('delete')
    assert window.doc.page_count==3
    view.populate(select=[2])
    view.run('insert')
    assert window.doc.page_count==4 and not text(3).strip()
    for _ in range(4):window.lookup_action('undo').activate(None)
    assert window.doc.page_count==3 and window.doc[0].rotation==0
    view.populate(select=[1,2])
    original=window.doc
    view.run('extract')
    assert window.doc is not original and window.doc.page_count==2 and 'World peace' in text(0)
    assert not view.is_open()

def smart_guides():
    if window.view_mode:window._toggle_view_edit_mode()
    window._load_page(0)
    a=EditableShape('rectangle',(400,600,480,640),page_number=0,is_new=True)
    command=AddObjectCommand(window,a);command.execute()
    b=EditableShape('rectangle',(403,700,483,740),page_number=0,is_new=True)
    command=AddObjectCommand(window,b);command.execute()
    window.dragged_object=b
    snapped=window._snap_object_rect(fitz.Rect(403,700,483,740),move=True)
    assert snapped.x0==400 and ('v',400) in window._object_guides,(snapped,window._object_guides)
    window.lookup_action('alignment_guides').change_state(GLib.Variant.new_boolean(False))
    assert window._snap_object_rect(fitz.Rect(403,700,483,740),move=True).x0==403
    window.lookup_action('alignment_guides').change_state(GLib.Variant.new_boolean(True))
    window.dragged_object=None

steps=[find_replace,redaction,organize,smart_guides]
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
print('pro features workflow ok')
