"""Vector diagram export and the document image manager."""

import pymupdf as fitz
from gi.repository import Adw, Gdk, GLib, Gtk

from ..i18n import _
from ..dialogs import OperationDialog, choose_files, file_filter, toast
from ..ops import graphics as ops
from ..ops.common import OperationError, format_page_ranges
from ..ops.convert import IMAGE_EXTENSIONS


def export_diagram(c):
    w = c.window
    doc = w.doc
    number = w.current_page_index
    page = doc[number]
    selection = c.selection_rect()
    regions = ops.diagram_regions(page)
    if not selection and not regions:
        raise OperationError(_('diagram_none'))
    dialog = OperationDialog(w, _('menu_export_diagram'), _('btn_export'),
                             preview=lambda: _region_preview(doc, number, region()))
    group = dialog.group(_('diagram_area'), _('diagram_hint'))
    options = ([('selection', _('diagram_selection'))] if selection else []) + \
        [(str(index), _('diagram_cluster', index + 1, round(r.width), round(r.height))) for index, r in enumerate(regions)]
    source = dialog.combo(group, _('diagram_area'), options, options[0][0])
    snap = dialog.switch(group, _('diagram_snap'), True, _('diagram_snap_hint'))
    snap.set_visible(bool(selection))
    fmt = dialog.combo(group, _('structured_format'), [('svg', 'SVG'), ('pdf', 'PDF'), ('png', 'PNG')], 'svg')
    vectors_only = dialog.switch(group, _('diagram_vectors_only'), False, _('diagram_vectors_only_hint'))

    def region():
        if source.key == 'selection':
            return ops.region_for_selection(doc[number], selection) if snap.get_active() else fitz.Rect(selection)
        return regions[int(source.key)] + (-2, -2, 2, 2)

    for widget, signal in ((source, 'notify::selected'), (snap, 'notify::active')):
        widget.connect(signal, lambda *_a: dialog.refresh_preview())
    fmt.connect('notify::selected', lambda *_a: vectors_only.set_visible(fmt.key == 'svg'))

    def apply(_dialog):
        rect = region()
        if fmt.key == 'svg' and vectors_only.get_active():
            data = ops.drawings_svg(doc[number], rect).encode('utf-8')
        else:
            data = ops.export_region(doc, number, rect, fmt.key)
        c.save_data(data, f'{c.stem()}-page{number + 1}-diagram.{fmt.key}',
                    [file_filter(fmt.key.upper(), (f'*.{fmt.key}',))])
    dialog.on_apply = apply
    dialog.show()


def _region_preview(doc, number, rect):
    page = doc[number]
    pix = page.get_pixmap(dpi=110, clip=fitz.Rect(rect) * page.rotation_matrix)
    return pix.tobytes('png')


def export_vector_svg(c):
    w = c.window
    page = w.doc[w.current_page_index]
    svg = ops.drawings_svg(page, c.selection_rect())
    c.save_data(svg, f'{c.stem()}-page{page.number + 1}-vectors.svg', [file_filter('SVG', ('*.svg',))])


def images_manager(c):
    w = c.window
    doc = w.doc
    editable = c.editable()
    dialog = OperationDialog(w, _('menu_images_manager'), width=680, height=680)
    group = dialog.group(_('images_manager_title'), _('images_manager_hint'))
    rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    rows.add_css_class('boxed-list')
    group.add(rows)
    archive = Gtk.Button(label=_('images_save_all'), halign=Gtk.Align.START, margin_top=8,
                         sensitive=c.can_copy())
    group.add(archive)

    def refresh():
        while child := rows.get_first_child():
            rows.remove(child)
        items = ops.image_inventory(doc)
        if not items:
            rows.append(Gtk.Label(label=_('images_none'), margin_top=10, margin_bottom=10))
        for item in items[:400]:
            row = Adw.ActionRow(
                title=f"{item['width']}×{item['height']} px · {item['ext'].upper() or '?'}"
                      f"{' · ' + _('images_transparent') if item['smask'] else ''}",
                subtitle=_('images_placements', item['placements'], format_page_ranges(item['pages'])))
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(ops.thumbnail(doc, item['xref'], 64)))
                picture = Gtk.Picture(paintable=texture, can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN)
                picture.set_size_request(56, 56)
                row.add_prefix(picture)
            except Exception:
                row.add_prefix(Gtk.Image(icon_name='image-x-generic-symbolic'))
            save = Gtk.Button(icon_name='document-save-symbolic', tooltip_text=_('btn_save'),
                              valign=Gtk.Align.CENTER, sensitive=c.can_copy())
            save.add_css_class('flat')
            save.connect('clicked', lambda _b, xref=item['xref']: save_one(xref))
            row.add_suffix(save)
            replace = Gtk.Button(label=_('images_replace_everywhere'), valign=Gtk.Align.CENTER, sensitive=editable)
            replace.add_css_class('flat')
            replace.connect('clicked', lambda _b, xref=item['xref']: replace_one(xref))
            row.add_suffix(replace)
            go = Gtk.Button(icon_name='go-jump-symbolic', tooltip_text=_('links_go'), valign=Gtk.Align.CENTER)
            go.add_css_class('flat')
            go.connect('clicked', lambda _b, page=item['pages'][0]: w._load_page(page))
            row.add_suffix(go)
            rows.append(row)

    def save_one(xref):
        ext, data = ops.image_bytes(doc, xref)
        c.save_data(data, f'{c.stem()}-image-{xref}.{ext}', [file_filter(ext.upper(), (f'*.{ext}',))])

    def replace_one(xref):
        def chosen(paths):
            if not paths:
                return
            try:
                with open(paths[0], 'rb') as handle:
                    data = handle.read()
                c.flush_edits()
                numbers = [page.number for page in doc
                           if any(info.get('xref') == xref for info in page.get_image_info(xrefs=True))]
                c.mutate(lambda: ops.replace_image_everywhere(doc, xref, stream=data), rebase_pages=numbers)
                w._load_thumbnails()
                refresh()
                toast(w, _('status_image_replaced', len(numbers)))
            except (OperationError, OSError) as error:
                dialog.error(error)
        choose_files(w, _('images_replace_everywhere'),
                     [file_filter(_('image_filter_label'), [f'*.{ext}' for ext in IMAGE_EXTENSIONS])], False, chosen)

    def save_all(_button):
        try:
            data, count = ops.export_images_archive(doc)
        except OperationError as error:
            dialog.error(error)
            return
        c.save_data(data, f'{c.stem()}-images.zip', [file_filter('ZIP', ('*.zip',))],
                    _('status_images_saved', count))
    archive.connect('clicked', save_all)
    refresh()
    dialog.show()
