"""Edit one form field's JavaScript, with ready-made examples."""
from gi.repository import Gtk, Pango

from .i18n import _
from .ops.javascript import FIELD_EVENTS, set_field_script

# (label key, event, code) — standard Acrobat form JavaScript that other readers run too.
SNIPPETS = (
    ('js_snippet_uppercase', 'K', 'if (!event.willCommit) event.change = event.change.toUpperCase();'),
    ('js_snippet_digits', 'K', 'if (!event.willCommit && !/^[0-9]*$/.test(event.change)) event.rc = false;'),
    ('js_snippet_min_length', 'V',
     'if (event.value && event.value.length < 3) {\n    app.alert("Enter at least 3 characters.");\n'
     '    event.rc = false;\n}'),
    ('js_snippet_range', 'V',
     'var n = Number(event.value);\nif (event.value !== "" && (isNaN(n) || n < 1 || n > 100)) {\n'
     '    app.alert("Enter a number from 1 to 100.");\n    event.rc = false;\n}'),
    ('js_snippet_sum', 'C',
     '// Replace the names with your fields.\nevent.value = Number(this.getField("price").value) *\n'
     '              Number(this.getField("quantity").value);'),
    ('js_snippet_today', 'Fo', 'if (!event.target.value)\n    event.target.value = util.printd("yyyy-mm-dd", new Date());'),
    ('js_snippet_show_hide', 'V',
     '// Show another field only when this one has a value.\nvar other = this.getField("details");\n'
     'other.display = event.value ? display.visible : display.hidden;'),
    ('js_snippet_copy', 'V', '// Copy this value into another field.\nthis.getField("copy_of_this").value = event.value;'),
    ('js_snippet_message', 'U', 'app.alert("Thank you!");'),
)


def current_script(doc, xref, event):
    from .ops.formbehaviour import _script
    return _script(doc, xref, event) or ''


class FieldScriptsDialog:
    def __new__(cls, controller, field):
        from .document_tool_ui import ToolDialog
        window = controller.window
        doc = window.doc
        xref = field['xref']
        original = {key: current_script(doc, xref, key) for key, _label, _description in FIELD_EVENTS}
        edits = dict(original)
        state = {'event': next((key for key, *_rest in FIELD_EVENTS if original[key]), 'V')}

        def apply():
            store()
            changed = {key: code for key, code in edits.items() if code.strip() != original[key].strip()}
            if not changed:
                return True

            def mutate():
                for key, code in changed.items():
                    set_field_script(doc, xref, key, code)
            if window._mutate_document(mutate, page_num=field['page']):
                window.status_label.set_text(_("js_saved", field['name']))
            return True

        dialog = ToolDialog(window, _("js_title", field['name']), apply)
        dialog.apply_button.set_label(_("btn_save"))
        events = [key for key, _label, _description in FIELD_EVENTS]

        def labels():
            return [('• ' if edits[key].strip() else '') + label for key, label, _d in FIELD_EVENTS]
        event_row = dialog.dropdown(_("js_event"), labels())
        event_row.set_selected(events.index(state['event']))
        description = Gtk.Label(xalign=0, wrap=True, max_width_chars=60, margin_start=4)
        description.add_css_class('dim-label')
        description.add_css_class('caption')
        dialog.box.append(description)

        editor = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                              left_margin=10, right_margin=10)
        scroll = Gtk.ScrolledWindow(min_content_height=220, hscrollbar_policy=Gtk.PolicyType.NEVER, child=editor)
        scroll.add_css_class('pdflx-textarea')

        examples = Gtk.MenuButton(label=_("js_examples"), halign=Gtk.Align.START)
        popover = Gtk.Popover()
        menu = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                       margin_start=6, margin_end=6)
        for label, event, code in SNIPPETS:
            button = Gtk.Button(label=_(label) + ' · ' + dict((k, l) for k, l, _d in FIELD_EVENTS)[event])
            button.add_css_class('flat')
            button.get_child().set_xalign(0)
            button.get_child().set_ellipsize(Pango.EllipsizeMode.END)
            button.connect('clicked', lambda _b, event=event, code=code: (popover.popdown(), insert(event, code)))
            menu.append(button)
        popover.set_child(menu)
        examples.set_popover(popover)
        tools = Gtk.Box(spacing=6)
        tools.append(examples)
        clear = Gtk.Button(label=_("js_clear"))
        clear.connect('clicked', lambda *_: editor.get_buffer().set_text(''))
        tools.append(clear)
        dialog.box.append(tools)
        dialog.box.append(scroll)
        note = Gtk.Label(label=_("js_note"), xalign=0, wrap=True, max_width_chars=60, margin_start=4)
        note.add_css_class('dim-label')
        note.add_css_class('caption')
        dialog.box.append(note)

        def text():
            buffer = editor.get_buffer()
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

        def store():
            edits[state['event']] = text()

        def show(event):
            state['event'] = event
            editor.get_buffer().set_text(edits[event])
            description.set_text(next(d for key, _l, d in FIELD_EVENTS if key == event))

        def changed(*_args):
            store()
            show(events[event_row.get_selected()])

        def insert(event, code):
            store()
            existing = edits[event].rstrip()
            edits[event] = (existing + '\n\n' if existing else '') + code
            if event_row.get_selected() != events.index(event):
                event_row.set_selected(events.index(event))  # shows the event via changed()
            else:
                show(event)
        event_row.connect('notify::selected', changed)
        show(state['event'])
        dialog.editor = editor
        dialog.event_row = event_row
        dialog.insert_snippet = insert
        return dialog
