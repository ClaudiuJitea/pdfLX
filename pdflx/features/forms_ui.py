"""Form structure (field hierarchy), JavaScript, calculation order, and form data dialogs."""
import os

from gi.repository import Adw, GLib, Gtk

from ..i18n import _
from ..dialogs import OperationDialog, ComboRow, toast
from ..ops import forms as form_ops, javascript as js_ops
from ..ops.common import OperationError


def _clear(box):
    while child := box.get_first_child():
        box.remove(child)


def form_structure(c):
    w = c.window
    doc = w.doc
    editable = c.editable()
    dialog = OperationDialog(w, _('menu_form_structure'), width=700, height=700)
    dialog.group(None, _('forms_js_notice'))

    tree_group = dialog.group(_('forms_hierarchy'), _('forms_hierarchy_hint'))
    tree_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    tree_box.add_css_class('boxed-list')
    tree_group.add(tree_box)
    parent_name = dialog.entry(tree_group, _('forms_parent_name'), '')
    group_button = Gtk.Button(label=_('forms_group_selected'), halign=Gtk.Align.START, margin_top=6,
                              sensitive=editable)
    tree_group.add(group_button)
    checks = {}

    order_group = dialog.group(_('forms_calc_order'), _('forms_calc_order_hint'))
    order_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    order_box.add_css_class('boxed-list')
    order_group.add(order_box)

    scripts_group = dialog.group(_('forms_scripts'))
    scripts_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    scripts_box.add_css_class('boxed-list')
    scripts_group.add(scripts_box)

    add_group = dialog.group(_('forms_add_script'), _('forms_add_script_hint'))
    field_row = ComboRow(_('forms_field'), [])
    add_group.add(field_row)
    event_row = ComboRow(_('forms_event'), [(key, label) for key, label, _d in js_ops.FIELD_EVENTS], 'V')
    add_group.add(event_row)
    event_help = Gtk.Label(xalign=0, wrap=True, margin_top=4, margin_bottom=4)
    event_help.add_css_class('dim-label')
    add_group.add(event_help)
    code_view = dialog.text_view(add_group, '', height=120, editable=True, monospace=True)
    add_button = Gtk.Button(label=_('forms_save_script'), halign=Gtk.Align.START, margin_top=6, sensitive=editable)
    add_group.add(add_button)
    widgets = {}

    def describe(*_args):
        event_help.set_text(next(d for k, _l, d in js_ops.FIELD_EVENTS if k == event_row.key))
    event_row.connect('notify::selected', describe)
    describe()

    def refresh():
        _clear(tree_box)
        checks.clear()
        names = form_ops.full_names(doc)

        def add_rows(items, depth):
            for item in items:
                row = Adw.ActionRow(title=GLib.markup_escape_text(item['name'] or '(unnamed)'),
                                    subtitle=GLib.markup_escape_text(
                                        f"{item['full_name']} · {item['kind'] or _('forms_group')}"))
                row.set_margin_start(depth * 18)
                if depth == 0 and not item['children']:
                    check = Gtk.CheckButton(valign=Gtk.Align.CENTER, sensitive=editable)
                    row.add_prefix(check)
                    checks[item['xref']] = check
                if item['children'] and not item['widget'] and depth == 0:
                    button = Gtk.Button(label=_('forms_ungroup'), valign=Gtk.Align.CENTER, sensitive=editable)
                    button.add_css_class('flat')
                    button.connect('clicked', lambda _b, xref=item['xref']: ungroup(xref))
                    row.add_suffix(button)
                tree_box.append(row)
                add_rows(item['children'], depth + 1)
        tree = form_ops.field_tree(doc)
        if not tree:
            tree_box.append(Gtk.Label(label=_('forms_none'), margin_top=10, margin_bottom=10))
        add_rows(tree, 0)

        from ..ops import formbehaviour
        _clear(order_box)
        order = formbehaviour.calculation_order(doc)
        if not order:
            order_box.append(Gtk.Label(label=_('forms_calc_none'), margin_top=8, margin_bottom=8))
        for position, (xref, field_name) in enumerate(order):
            row = Adw.ActionRow(title=f'{position + 1}. {GLib.markup_escape_text(field_name)}')
            for icon, delta in (('go-up-symbolic', -1), ('go-down-symbolic', 1)):
                button = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER,
                                    sensitive=editable and 0 <= position + delta < len(order))
                button.add_css_class('flat')
                button.connect('clicked', lambda _b, position=position, delta=delta: move(position, delta))
                row.add_suffix(button)
            order_box.append(row)

        _clear(scripts_box)
        entries = js_ops.scripts(doc)
        if not entries:
            scripts_box.append(Gtk.Label(label=_('forms_no_scripts'), margin_top=10, margin_bottom=10))
        for entry in entries:
            row = Adw.ExpanderRow(title=GLib.markup_escape_text(f"{entry['location']} — {entry['event']}"),
                                  subtitle=GLib.markup_escape_text(entry['description'] or ''))
            view = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, editable=editable,
                                top_margin=6, bottom_margin=6, left_margin=8, right_margin=8)
            view.get_buffer().set_text(entry['code'])
            row.add_row(view)
            actions = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=8)
            save = Gtk.Button(label=_('btn_save'), sensitive=editable)
            remove = Gtk.Button(label=_('forms_remove_script'), sensitive=editable)
            remove.add_css_class('destructive-action')
            actions.append(save)
            actions.append(remove)
            row.add_row(actions)
            save.connect('clicked', lambda _b, entry=entry, view=view: change(
                lambda: js_ops.set_code(doc, entry, _buffer_text(view))))
            remove.connect('clicked', lambda _b, entry=entry: change(lambda: js_ops.remove(doc, entry)))
            scripts_box.append(row)

        widgets.clear()
        options = []
        for page in doc:
            for widget in page.widgets() or ():
                label = f'{names.get(widget.xref, widget.field_name)} (p. {page.number + 1})'
                widgets[str(widget.xref)] = widget.xref
                options.append((str(widget.xref), label))
        field_row.keys = [key for key, _l in options]
        field_row.set_model(Gtk.StringList.new([label for _k, label in options]))
        add_group.set_sensitive(bool(options) and editable)

    def change(mutation):
        try:
            c.mutate(mutation)
            refresh()
            toast(w, _('status_forms_updated'))
        except OperationError as error:
            dialog.error(error)

    def move(position, delta):
        from ..ops import formbehaviour
        xrefs = [xref for xref, _name in formbehaviour.calculation_order(doc)]
        xrefs[position], xrefs[position + delta] = xrefs[position + delta], xrefs[position]
        change(lambda: formbehaviour.set_calculation_order(doc, xrefs))

    def ungroup(xref):
        change(lambda: form_ops.ungroup_field(doc, xref))

    def group(_button):
        selected = [xref for xref, check in checks.items() if check.get_active()]
        name = parent_name.get_text()
        change(lambda: form_ops.group_fields(doc, selected, name))
        if hasattr(w, 'form_tools'):
            w.form_tools.refresh()

    def add_script(_button):
        if field_row.key is None:
            dialog.error(_('forms_choose_field'))
            return
        code = _buffer_text(code_view)
        change(lambda: js_ops.set_field_script(doc, widgets[field_row.key], event_row.key, code))
        code_view.get_buffer().set_text('')

    group_button.connect('clicked', group)
    add_button.connect('clicked', add_script)
    refresh()
    dialog.show()


def _buffer_text(view):
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


# ---------------------------------------------------------------- form data

def export_data(c):
    from ..ops import formdata
    from ..dialogs import OperationDialog as Dialog, file_filter
    w = c.window
    dialog = Dialog(w, _('menu_form_export'), _('btn_export'))
    group = dialog.group(_('formdata_export_title'), _('formdata_export_hint'))
    fmt = dialog.combo(group, _('structured_format'), [(key, label) for key, label in formdata.FORMATS], 'xfdf')
    values = formdata.collect(w.doc)
    dialog.info(group, _('formdata_fields'), str(len(values)))

    def apply(_dialog):
        c.flush_edits()
        source = os.path.basename(w.current_file_path or c.stem() + '.pdf')
        data = formdata.export(w.doc, fmt.key, source)
        c.save_data(data, f'{c.stem()}-data.{fmt.key}', [file_filter(fmt.key.upper(), (f'*.{fmt.key}',))],
                    _('status_formdata_exported', len(values)))
    dialog.on_apply = apply
    dialog.show()


def import_data(c):
    from ..ops import formdata
    from ..dialogs import OperationDialog as Dialog, file_filter
    w = c.window
    doc = w.doc
    can_fill = c.editable() or getattr(doc, 'editor_can_fill_forms', True)
    dialog = Dialog(w, _('menu_form_import'), _('btn_import'))
    group = dialog.group(_('formdata_import_title'), _('formdata_import_hint'))
    state = {}
    summary = dialog.info(group, _('formdata_match'), _('file_none_selected'))
    row = dialog.spin(group, _('formdata_csv_row'), 1, 1, 100000)
    row.set_visible(False)

    def load(paths):
        try:
            with open(paths[0], 'rb') as handle:
                data = handle.read()
            fmt = formdata.detect_format(paths[0], data)
            state.update(data=data, fmt=fmt)
            if fmt == 'csv':
                import csv as csv_module
                import io
                rows = list(csv_module.reader(io.StringIO(data.decode('utf-8-sig', 'replace'))))
                row.set_range(1, max(1, len(rows) - 1))
                row.set_visible(len(rows) > 2)
            preview()
        except (OSError, formdata.OperationError) as error:
            dialog.error(error)

    def preview(*_args):
        if 'data' not in state:
            return
        try:
            values = formdata.parse(state['data'], state['fmt'], row.get_value_as_int() - 1)
            updates, unmatched = formdata.plan_import(doc, values)
            state['values'] = values
            text = _('formdata_match_value', state['fmt'].upper(), len(updates), len(values))
            if unmatched:
                text += ' ' + _('formdata_unmatched', ', '.join(unmatched[:6]) + ('…' if len(unmatched) > 6 else ''))
            summary.set_subtitle(text)
            dialog.clear_error()
        except formdata.OperationError as error:
            dialog.error(error)
    dialog.file(group, _('formdata_file'), [file_filter(_('formdata_files'), ('*.fdf', '*.xfdf', '*.json', '*.csv'))],
                on_change=load)
    row.connect('notify::value', preview)

    def apply(_dialog):
        if not can_fill:
            raise OperationError(_('err_edit_not_allowed'))
        if 'values' not in state:
            raise OperationError(_('err_choose_file'))
        if hasattr(w, 'form_tools') and not w.form_tools.save_values():
            return False
        result = []
        c.mutate(lambda: result.append(formdata.import_values(doc, state['values'])), form_fill=True)
        changed, unmatched = result[0]
        if hasattr(w, 'form_tools'):
            w.form_tools.refresh()
        w._load_thumbnails()
        toast(w, _('status_formdata_imported', changed, len(unmatched)))
    dialog.on_apply = apply
    dialog.show()
