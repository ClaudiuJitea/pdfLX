"""Drive form scripts, the behaviour tab, the date picker, and script buttons in a real window."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib, Gtk

from pdflx.window import PdfEditorWindow
from pdflx import pdf_handler, document_tools as tools
from pdflx.ops import formbehaviour

app = Adw.Application(application_id='org.pdflx.TestFormScripts', flags=Gio.ApplicationFlags.NON_UNIQUE)
app.register()
w = PdfEditorWindow(application=app)
w.doc, _ = pdf_handler.create_new_pdf()
doc = w.doc
price = tools.create_form_field(doc, 0, 'Price', 'text', (72, 72, 272, 92))
qty = tools.create_form_field(doc, 0, 'Qty', 'text', (72, 100, 272, 120))
total = tools.create_form_field(doc, 0, 'Total', 'text', (72, 128, 272, 148))
date = tools.create_form_field(doc, 0, 'Date', 'text', (72, 156, 272, 176))
button = tools.create_form_field(doc, 0, 'Fill', 'button', (300, 72, 400, 102),
                                 options={'button_action': 'javascript', 'button_caption': 'Fill',
                                          'button_script': 'this.getField("Qty").value = "4";'})
formbehaviour.apply(doc, price, {'format': 'number', 'decimals': 2, 'minimum': 0, 'maximum': 100})
formbehaviour.apply(doc, total, {'format': 'number', 'decimals': 2, 'calculate': 'SUM', 'sources': ['Price', 'Qty']})
formbehaviour.apply(doc, date, {'format': 'date', 'date_format': 'dd/mm/yyyy'})
w.view_mode = False
w._load_page(0)
w.stack.set_visible_child_name('editor')
w.present()
loop = GLib.MainLoop()
errors = []


def widgets(widget, kind):
    found = [widget] if isinstance(widget, kind) else []
    child = widget.get_first_child()
    while child:
        found.extend(widgets(child, kind))
        child = child.get_next_sibling()
    return found


def field(name):
    from pdflx import document_features
    return next(f for f in document_features.list_form_fields(w.doc) if f['name'] == name)


def value(name):
    return field(name)['value']


def behaviour_tab():
    w.form_tools.edit_field(field('Total'))
    dialog = w.get_visible_dialog()
    labels = [dialog.form_notebook.get_tab_label_text(dialog.form_notebook.get_nth_page(i))
              for i in range(dialog.form_notebook.get_n_pages())]
    assert 'Format & Calculation' in labels, labels
    dialog.force_close()


def fill_and_reject():
    tools_ = w.form_tools
    tools_.show()
    tools_.pending[(0, price)] = '12.5'
    tools_.pending[(0, qty)] = '3'
    assert tools_.save_values()
    assert value('Total') == '15.5', value('Total')
    tools_.pending[(0, price)] = '500'
    assert tools_.save_values() is False
    assert value('Price') == '12.5'
    assert (0, price) not in tools_.pending
    current = w.get_visible_dialog()
    assert current is not None
    current.force_close()


def date_picker():
    tools_ = w.form_tools
    tools_.refresh()
    buttons = [b for b in widgets(tools_.sidebar, Gtk.MenuButton) if b.get_tooltip_text() == 'Pick a date']
    assert buttons, 'date picker missing'


def script_button():
    w.form_tools.click_field(field('Fill'))
    assert value('Qty') == '4', value('Qty')
    assert value('Total') == '16.5', value('Total')
    w.lookup_action('undo').activate(None)
    assert value('Qty') == '3'


def scripts_toggle():
    w.lookup_action('form_scripts').change_state(GLib.Variant.new_boolean(False))
    w.form_tools.pending[(0, price)] = '500'
    assert w.form_tools.save_values()
    assert value('Price') == '500'
    w.lookup_action('form_scripts').change_state(GLib.Variant.new_boolean(True))
    print('Form behaviour tab, script calculation and rejection, date picker, script buttons, and the scripts '
          'toggle passed', flush=True)


def run(callback):
    try:
        callback()
    except Exception as error:
        import traceback
        traceback.print_exc()
        errors.append(error)
        loop.quit()
    return False


for delay, callback in ((600, behaviour_tab), (1000, fill_and_reject), (1500, date_picker), (1800, script_button),
                        (2200, scripts_toggle)):
    GLib.timeout_add(delay, run, callback)
GLib.timeout_add(2600, lambda: (w.destroy(), loop.quit(), False)[2])
loop.run()
if errors:
    raise SystemExit(1)
