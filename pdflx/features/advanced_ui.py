"""Dialogs for e-invoices, portfolios, and the raw PDF object inspector."""
from gi.repository import Adw, GLib, Gtk

from ..i18n import _
from ..dialogs import OperationDialog, file_filter, toast
from ..ops import advanced as ops
from ..ops.common import OperationError


def attach_einvoice(c):
    w = c.window
    doc = w.doc
    dialog = OperationDialog(w, _('menu_einvoice'), _('btn_add'), width=600)
    group = dialog.group(_('einvoice_title'), _('einvoice_hint'))
    xml = dialog.file(group, _('einvoice_xml'), [file_filter('XML', ('*.xml',))])
    profile = dialog.combo(group, _('einvoice_profile'), [(p, p) for p in ops.FACTURX_PROFILES], 'EN 16931')
    filename = dialog.combo(group, _('einvoice_filename'), [('factur-x.xml', 'factur-x.xml (Factur-X / ZUGFeRD 2.x)'),
                                                            ('xrechnung.xml', 'xrechnung.xml')])
    dialog.group(None, _('einvoice_warning'))
    existing = ops.einvoice_xml(doc)
    if existing:
        dialog.info(dialog.group(_('einvoice_existing')), existing[0], _('einvoice_existing_hint'))

    def apply(_dialog):
        if not xml.path:
            raise OperationError(_('err_choose_file'))
        with open(xml.path, 'rb') as handle:
            data = handle.read()
        c.mutate(lambda: ops.attach_einvoice(doc, data, profile.key, filename.key))
        toast(w, _('status_einvoice_attached'))
    dialog.on_apply = apply
    dialog.show()


def create_portfolio(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_portfolio'), _('btn_create'))
    group = dialog.group(_('portfolio_title'), _('portfolio_hint'))
    files = dialog.file(group, _('portfolio_files'), None, multiple=True)
    title = dialog.entry(group, _('metadata_title'), _('portfolio_default_title'))

    def apply(_dialog):
        if not files.paths:
            raise OperationError(_('err_choose_file'))
        result = ops.create_portfolio(files.paths, title.get_text().strip() or _('portfolio_default_title'))
        c.open_new(result, (title.get_text().strip() or 'portfolio') + '.pdf', _('status_portfolio_created',
                                                                                len(files.paths)))
    dialog.on_apply = apply
    dialog.show()


def object_inspector(c):
    w = c.window
    doc = w.doc
    editable = c.editable()
    dialog = OperationDialog(w, _('menu_object_inspector'), width=820, height=760)
    dialog.group(None, _('objects_warning') if editable else _('objects_read_only'))
    search_group = dialog.group(_('objects_find'))
    query = dialog.entry(search_group, _('objects_query'), 'Page')
    results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
    results.add_css_class('boxed-list')
    scroller = Gtk.ScrolledWindow(min_content_height=180, max_content_height=220, propagate_natural_height=True)
    scroller.set_child(results)
    search_group.add(scroller)
    editor = dialog.group(_('objects_selected'))
    title = Gtk.Label(xalign=0)
    title.add_css_class('heading')
    editor.add(title)
    source_view = dialog.text_view(editor, '', height=180, editable=editable, monospace=True)
    stream_view = dialog.text_view(editor, '', height=180, editable=editable, monospace=True)
    stream_note = Gtk.Label(xalign=0, wrap=True)
    stream_note.add_css_class('dim-label')
    editor.add(stream_note)
    save = Gtk.Button(label=_('objects_save'), halign=Gtk.Align.START, margin_top=6, sensitive=False)
    save.add_css_class('destructive-action')
    editor.add(save)
    state = {'xref': None, 'stream': None}

    def search(*_args):
        while child := results.get_first_child():
            results.remove(child)
        for item in ops.find_objects(doc, query.get_text())[:500]:
            row = Adw.ActionRow(title=f"{item['xref']} 0 obj  {item['type']} {item['subtype']}".strip(),
                                subtitle=GLib.markup_escape_text(item['preview']), activatable=True)
            row.xref = item['xref']
            results.append(row)

    def selected(_box, row):
        if row is None:
            return
        child = row.get_child() if not hasattr(row, 'xref') else row
        xref = getattr(child, 'xref', getattr(row, 'xref', None))
        if xref is None:
            return
        source, stream = ops.read_object(doc, xref)
        state.update(xref=xref, stream=stream)
        title.set_text(_('objects_object', xref))
        source_view.get_buffer().set_text(source)
        stream_view.get_buffer().set_text(stream or '')
        stream_view.get_parent().set_visible(stream is not None)
        stream_note.set_text(_('objects_binary_stream') if doc.xref_is_stream(xref) and stream is None else '')
        save.set_sensitive(editable)

    def write(_button):
        xref = state['xref']
        if xref is None:
            return
        buffer = source_view.get_buffer()
        source = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        stream = None
        if state['stream'] is not None:
            sbuffer = stream_view.get_buffer()
            stream = sbuffer.get_text(sbuffer.get_start_iter(), sbuffer.get_end_iter(), True)
        try:
            c.mutate(lambda: ops.write_object(doc, xref, source, stream))
            w._load_thumbnails()
            toast(w, _('status_object_saved', xref))
        except OperationError as error:
            dialog.error(error)

    query.connect('entry-activated', search)
    query.connect('changed', lambda *_a: GLib.timeout_add(250, lambda: (search(), False)[1]))
    results.connect('row-selected', selected)
    save.connect('clicked', write)
    search()
    dialog.show()
