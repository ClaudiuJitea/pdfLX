"""Page workflow dialogs: insert, extract, split, order, blanks, geometry, imposition, labels."""
import datetime
import math
import os

import pymupdf as fitz
from gi.repository import Gtk

from ..i18n import _
from ..dialogs import OperationDialog, choose_folder, file_filter, pdf_filter, render_preview, toast
from ..ops import pages as ops
from ..ops.common import OperationError, format_page_ranges, parse_page_ranges
from ..ops.convert import OPENABLE_EXTENSIONS


def _source_filters():
    patterns = ['*.pdf'] + [f'*.{ext}' for ext in OPENABLE_EXTENSIONS]
    return [file_filter(_('filter_documents'), patterns), pdf_filter()]


def _paper_options():
    return [(key, label) for key, label, _size in ops.PAPER_SIZES]


# ---------------------------------------------------------------- insert / extract / split

def insert_pages(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_insert_pages'), _('btn_insert'))
    group = dialog.group(_('insert_source'), _('insert_source_hint'))
    source = dialog.file(group, _('insert_file'), _source_filters())
    pages = dialog.entry(group, _('insert_source_pages'), '')
    where = dialog.group(_('insert_position'))
    position = dialog.combo(where, _('insert_position'), [
        ('after_current', _('pos_after_current')), ('before_current', _('pos_before_current')),
        ('start', _('pos_start')), ('end', _('pos_end'))], 'after_current')

    def apply(_dialog):
        if not source.path:
            raise OperationError(_('err_choose_file'))
        try:
            other = fitz.open(source.path)
        except Exception as error:
            raise OperationError(_('err_open_file', os.path.basename(source.path), error))
        with other:
            if other.needs_pass:
                raise OperationError(_('err_insert_encrypted'))
            selected = parse_page_ranges(pages.get_text(), other.page_count)
            at = {'after_current': w.current_page_index + 1, 'before_current': w.current_page_index,
                  'start': 0, 'end': w.doc.page_count}[position.key]
            c.flush_edits()
            c.change_pages(lambda: ops.insert_document(w.doc, other, at, selected),
                           ops.insert_remap(at, len(selected)), focus=at,
                           message_text=_('status_pages_inserted', len(selected)))
    dialog.on_apply = apply
    dialog.show()


def extract_pages(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_extract_pages'), _('btn_extract'))
    group = dialog.group(_('range_pages'))
    pages = dialog.pages(group, default='current')
    output = dialog.group(_('output'))
    target = dialog.combo(output, _('output_destination'), [('tab', _('output_new_tab')), ('file', _('output_file'))])
    delete = dialog.switch(output, _('extract_delete_after'), False, _('extract_delete_hint'))
    delete.set_sensitive(c.editable())

    dialog.on_apply = lambda d: _extract_apply(c, d, pages, target, delete)
    dialog.show()


def _extract_apply(c, dialog, pages, target, delete):
    w = c.window
    selected = pages.pages()
    remove = delete.get_active() and c.editable()
    if remove and len(selected) >= w.doc.page_count:
        raise OperationError(_('err_cannot_delete_all_pages'))
    with fitz.open('pdf', c.snapshot_bytes()) as copy:
        result = ops.extract_pages(copy, selected)
    if remove:
        _delete_pages(c, selected)
    name = f'{c.stem()}-pages-{format_page_ranges(selected).replace(", ", "_")}'
    if target.key == 'tab':
        c.open_new(result, name + '.pdf', _('status_pages_extracted', len(selected)))
    else:
        data = result.tobytes(garbage=3, deflate=True)
        result.close()
        c.save_pdf(data, name)


def _delete_pages(c, selected):
    w = c.window
    focus = max(0, min(selected) - 1)
    c.change_pages(lambda: w.doc.delete_pages(selected), ops.delete_remap(selected), focus=focus,
                   message_text=_('status_pages_deleted', len(selected)))


def split_document(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_split_document'), _('btn_split'))
    group = dialog.group(_('split_method'))
    mode = dialog.combo(group, _('split_method'), [
        ('every', _('split_every')), ('ranges', _('split_ranges')),
        ('bookmarks', _('split_bookmarks')), ('single', _('split_single'))])
    every = dialog.spin(group, _('split_pages_per_file'), 1, 1, max(1, w.doc.page_count))
    ranges = dialog.entry(group, _('split_range_groups'), '')
    preview = dialog.info(group, _('split_result'), '')

    def plan():
        if mode.key == 'ranges':
            groups = [g for g in ranges.get_text().split(';') if g.strip()]
            return ops.split_plan(w.doc, 'ranges', [parse_page_ranges(g, w.doc.page_count) for g in groups])
        return ops.split_plan(w.doc, mode.key, every.get_value_as_int())

    def update(*_args):
        every.set_visible(mode.key == 'every')
        ranges.set_visible(mode.key == 'ranges')
        try:
            parts = plan()
            preview.set_subtitle(_('split_preview', len(parts)) + ' — ' +
                                 ', '.join(format_page_ranges(p) for _l, p in parts[:6]) + ('…' if len(parts) > 6 else ''))
        except OperationError as error:
            preview.set_subtitle(str(error))
    for widget, signal in ((mode, 'notify::selected'), (every, 'notify::value'), (ranges, 'changed')):
        widget.connect(signal, update)
    update()

    def apply(_dialog):
        parts = plan()
        data = c.snapshot_bytes()
        stem = c.stem()

        def chosen(folder):
            def work(progress, cancel):
                with fitz.open('pdf', data) as copy:
                    return ops.write_parts(copy, parts, folder, stem, progress, cancel)
            dialog.run(work, lambda written: toast(w, _('status_split_done', len(written), folder)))
        choose_folder(w, _('split_choose_folder'), chosen)
        return False
    dialog.on_apply = apply
    dialog.show()


# ---------------------------------------------------------------- order

def reverse_pages(c):
    w = c.window
    if w.doc.page_count < 2:
        return
    c.flush_edits()
    order = ops.reverse_order(w.doc.page_count)
    c.change_pages(lambda: ops.apply_order(w.doc, order), ops.order_remap(order),
                   focus=0, message_text=_('status_pages_reversed'))


def collate_pages(c):
    w = c.window
    count = w.doc.page_count
    if count < 2:
        raise OperationError(_('err_need_two_pages'))
    dialog = OperationDialog(w, _('menu_collate_pages'), _('btn_collate'))
    group = dialog.group(_('collate_title'), _('collate_hint'))
    fronts = dialog.spin(group, _('collate_fronts'), math.ceil(count / 2), 1, count - 1)
    reverse = dialog.switch(group, _('collate_backs_reversed'), True)
    result = dialog.info(group, _('collate_result'), '')

    def update(*_args):
        try:
            order = ops.collate_order(count, fronts.get_value_as_int(), reverse.get_active())
            result.set_subtitle(', '.join(str(n + 1) for n in order[:16]) + ('…' if count > 16 else ''))
        except OperationError as error:
            result.set_subtitle(str(error))
    fronts.connect('notify::value', update)
    reverse.connect('notify::active', update)
    update()

    def apply(_dialog):
        order = ops.collate_order(count, fronts.get_value_as_int(), reverse.get_active())
        c.flush_edits()
        c.change_pages(lambda: ops.apply_order(w.doc, order), ops.order_remap(order), focus=0,
                       message_text=_('status_pages_collated'))
    dialog.on_apply = apply
    dialog.show()


def remove_blank_pages(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_remove_blank_pages'), _('btn_find_remove'))
    group = dialog.group(_('blank_title'), _('blank_hint'))
    pages = dialog.pages(group)
    sensitivity = dialog.spin(group, _('blank_threshold'), 99.5, 90, 100, 0.1, 1, _('blank_threshold_hint'))

    def apply(_dialog):
        selected = pages.pages()
        threshold = sensitivity.get_value() / 100
        data = c.snapshot_bytes()

        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                return ops.blank_pages(copy, threshold, selected, progress, cancel)

        def done(blank):
            if not blank:
                dialog.error(_('blank_none_found'))
                return False
            if len(blank) >= w.doc.page_count:
                dialog.error(_('err_cannot_delete_all_pages'))
                return False
            _delete_pages(c, blank)
            toast(w, _('status_blank_removed', len(blank), format_page_ranges(blank)))
        dialog.run(work, done)
        return False
    dialog.on_apply = apply
    dialog.show()


# ---------------------------------------------------------------- geometry

def remove_rotation(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_remove_rotation'), _('btn_apply'))
    group = dialog.group(_('rotation_title'), _('rotation_hint'))
    pages = dialog.pages(group)

    def apply(_dialog):
        selected = [n for n in pages.pages() if w.doc[n].rotation]
        if not selected:
            raise OperationError(_('rotation_none'))
        c.flush_edits()
        c.require_plain_pages(selected)
        c.mutate(lambda: ops.remove_rotation(w.doc, selected), rebase_pages=selected)
        w._load_thumbnails()
        toast(w, _('status_rotation_removed', len(selected)))
    dialog.on_apply = apply
    dialog.show()


def resize_pages(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_resize_pages'), _('btn_create'), preview=None)
    group = dialog.group(_('resize_title'), _('resize_hint'))
    pages = dialog.pages(group)
    paper = dialog.combo(group, _('paper_size'), _paper_options(), 'a4')
    landscape = dialog.switch(group, _('paper_landscape'), False)
    margin = dialog.spin(group, _('margin_pt'), 0, 0, 144)
    keep = dialog.switch(group, _('keep_proportions'), True)

    def apply(_dialog):
        width, height = ops.paper_size(paper.key, landscape.get_active())
        selected = pages.pages()
        with fitz.open('pdf', c.snapshot_bytes()) as copy:
            result = ops.normalize_sizes(copy, width, height, selected, keep.get_active(), margin.get_value())
        c.open_new(result, f'{c.stem()}-{paper.key}.pdf', _('status_new_tab_created'))
    dialog.on_apply = apply
    dialog.show()


def nup_pages(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_nup_pages'), _('btn_create'))
    group = dialog.group(_('nup_layout'), _('nup_hint'))
    layout = dialog.combo(group, _('nup_layout'), [
        ('2', _('nup_2')), ('4', _('nup_4')), ('6', _('nup_6')), ('9', _('nup_9')),
        ('16', _('nup_16')), ('booklet', _('nup_booklet'))], '2')
    sheet = dialog.group(_('nup_sheet'))
    paper = dialog.combo(sheet, _('paper_size'), _paper_options(), 'a4')
    margin = dialog.spin(sheet, _('margin_pt'), 18, 0, 72)
    borders = dialog.switch(sheet, _('nup_borders'), False)
    columns_first = dialog.switch(sheet, _('nup_columns_first'), False)

    def apply(_dialog):
        data = c.snapshot_bytes()
        key = layout.key

        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                if key == 'booklet':
                    return ops.booklet(copy, ops.paper_size(paper.key, True), margin.get_value(), progress, cancel)
                columns, rows = {'2': (2, 1), '4': (2, 2), '6': (2, 3), '9': (3, 3), '16': (4, 4)}[key]
                landscape = key == '2'
                return ops.nup(copy, columns, rows, ops.paper_size(paper.key, landscape), margin.get_value(), 6,
                               'columns' if columns_first.get_active() else 'rows', borders.get_active(),
                               progress, cancel)
        dialog.run(work, lambda result: c.open_new(result, f'{c.stem()}-{key}-up.pdf', _('status_new_tab_created')))
        return False
    dialog.on_apply = apply
    dialog.show()


def overlay_pages(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_overlay_pages'), _('btn_apply'))
    group = dialog.group(_('overlay_source'), _('overlay_hint'))
    source = dialog.file(group, _('overlay_file'), [pdf_filter()])
    source_page = dialog.spin(group, _('overlay_source_page'), 1, 1, 9999)
    placement = dialog.combo(group, _('overlay_placement'), [('behind', _('overlay_behind')), ('front', _('overlay_front'))])
    keep = dialog.switch(group, _('keep_proportions'), True)
    target = dialog.group(_('range_pages'))
    pages = dialog.pages(target)

    def apply(_dialog):
        if not source.path:
            raise OperationError(_('err_choose_file'))
        if w.current_file_path and os.path.realpath(source.path) == os.path.realpath(w.current_file_path):
            raise OperationError(_('err_overlay_same_file'))
        selected = pages.pages()
        c.flush_edits()
        with fitz.open(source.path) as other:
            if other.needs_pass:
                raise OperationError(_('err_insert_encrypted'))
            index = source_page.get_value_as_int() - 1
            c.mutate(lambda: ops.overlay(w.doc, other, index, selected, placement.key == 'behind', keep.get_active()),
                     rebase_pages=selected)
        w._load_thumbnails()
        toast(w, _('status_overlay_applied', len(selected)))
    dialog.on_apply = apply
    dialog.show()


def headers_footers(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_headers_footers'), _('btn_apply'), width=620,
                             preview=lambda: _header_preview(c, dialog, controls))
    controls = {}
    texts = dialog.group(_('hf_text'), _('hf_placeholders'))
    for key in ops.POSITIONS:
        default = '{page} / {total}' if key == 'bottom-center' else ''
        controls[key] = dialog.entry(texts, _(f'hf_{key.replace("-", "_")}'), default)
    style = dialog.group(_('hf_style'))
    controls['size'] = dialog.spin(style, _('tool_font_size'), 9, 5, 48)
    controls['margin'] = dialog.spin(style, _('margin_pt'), 24, 4, 144)
    controls['color'] = dialog.color(style, _('tool_stamp_color'), (0, 0, 0))
    bates = dialog.group(_('hf_bates'), _('hf_bates_hint'))
    controls['bates'] = dialog.switch(bates, _('hf_bates_enable'), False)
    controls['prefix'] = dialog.entry(bates, _('hf_bates_prefix'), c.stem().upper()[:8] + '-')
    controls['start'] = dialog.spin(bates, _('hf_bates_start'), 1, 0, 10 ** 9)
    controls['digits'] = dialog.spin(bates, _('hf_bates_digits'), 6, 1, 12)
    controls['suffix'] = dialog.entry(bates, _('hf_bates_suffix'), '')
    target = dialog.group(_('range_pages'))
    controls['pages'] = dialog.pages(target)

    def apply(_dialog):
        options = _header_options(c, controls)
        selected = options.pop('pages')
        c.flush_edits()
        c.mutate(lambda: ops.add_headers_footers(w.doc, selected, **options), rebase_pages=selected)
        w._load_thumbnails()
        toast(w, _('status_headers_applied', len(selected)))
    dialog.on_apply = apply
    dialog.show()


def _header_options(c, controls):
    bates = None
    if controls['bates'].get_active():
        bates = {'prefix': controls['prefix'].get_text(), 'start': controls['start'].get_value_as_int(),
                 'digits': controls['digits'].get_value_as_int(), 'suffix': controls['suffix'].get_text()}
    return {'pages': controls['pages'].pages(),
            'texts': {key: controls[key].get_text() for key in ops.POSITIONS},
            'font_size': controls['size'].get_value(), 'margin': controls['margin'].get_value(),
            'color': controls['color'].rgb(), 'bates': bates,
            'filename': os.path.basename(c.window.current_file_path or ''),
            'date': datetime.date.today().isoformat()}


def _header_preview(c, dialog, controls):
    options = _header_options(c, controls)
    selected = options.pop('pages')
    with fitz.open('pdf', c.snapshot_bytes()) as scratch:
        ops.add_headers_footers(scratch, selected, **options)
        return render_preview(scratch, selected[0])


# ---------------------------------------------------------------- labels and boxes

def page_labels(c):
    w = c.window
    doc = w.doc
    dialog = OperationDialog(w, _('menu_page_labels'), _('btn_apply'), width=640)
    group = dialog.group(_('labels_rules'), _('labels_hint'))
    rules_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    rules_box.add_css_class('boxed-list')
    group.add(rules_box)
    add = Gtk.Button(label=_('labels_add_rule'), halign=Gtk.Align.START, margin_top=8)
    add.add_css_class('flat')
    group.add(add)
    preview = dialog.info(dialog.group(_('labels_preview')), _('labels_preview'), '')
    rows = []

    def make_row(rule):
        box = Gtk.Box(spacing=8, margin_top=6, margin_bottom=6, margin_start=8, margin_end=8)
        start = Gtk.SpinButton.new_with_range(1, doc.page_count, 1)
        start.set_value(rule['startpage'] + 1)
        start.set_tooltip_text(_('labels_start_page'))
        styles = [key for key, _label in ops.LABEL_STYLES]
        style = Gtk.DropDown.new_from_strings([_(f'label_style_{key or "none"}') for key in styles])
        style.set_selected(styles.index(rule.get('style', 'D')) if rule.get('style', 'D') in styles else 0)
        prefix = Gtk.Entry(placeholder_text=_('labels_prefix'), text=rule.get('prefix', ''), width_chars=8)
        first = Gtk.SpinButton.new_with_range(1, 100000, 1)
        first.set_value(rule.get('firstpagenum', 1))
        first.set_tooltip_text(_('labels_first_number'))
        remove = Gtk.Button(icon_name='user-trash-symbolic', tooltip_text=_('labels_remove_rule'))
        remove.add_css_class('flat')
        for widget, label in ((start, _('labels_from_page')), (style, None), (prefix, None), (first, _('labels_from_number'))):
            if label:
                box.append(Gtk.Label(label=label))
            box.append(widget)
        box.append(remove)
        row = Gtk.ListBoxRow(child=box, activatable=False)
        entry = {'row': row, 'start': start, 'style': style, 'prefix': prefix, 'first': first, 'styles': styles}
        rows.append(entry)
        rules_box.append(row)
        remove.connect('clicked', lambda _b: (rows.remove(entry), rules_box.remove(row), update()))
        for widget, signal in ((start, 'value-changed'), (style, 'notify::selected'), (prefix, 'changed'),
                               (first, 'value-changed')):
            widget.connect(signal, lambda *_a: update())

    def collect():
        return [{'startpage': r['start'].get_value_as_int() - 1, 'style': r['styles'][r['style'].get_selected()],
                 'prefix': r['prefix'].get_text(), 'firstpagenum': r['first'].get_value_as_int()} for r in rows]

    def update():
        try:
            with fitz.open() as scratch:
                for _n in range(min(doc.page_count, 12)):
                    scratch.new_page(width=10, height=10)
                rules = [r for r in collect() if r['startpage'] < scratch.page_count]
                ops.set_page_labels(scratch, rules)
                labels = [scratch[n].get_label() or str(n + 1) for n in range(scratch.page_count)]
            preview.set_subtitle(', '.join(labels) + ('…' if doc.page_count > 12 else ''))
            dialog.clear_error()
        except OperationError as error:
            preview.set_subtitle(str(error))

    for rule in ops.get_page_labels(doc) or [{'startpage': 0, 'style': 'D', 'prefix': '', 'firstpagenum': 1}]:
        make_row(rule)
    add.connect('clicked', lambda _b: (make_row({'startpage': min(w.current_page_index, doc.page_count - 1),
                                                 'style': 'D', 'prefix': '', 'firstpagenum': 1}), update()))
    update()

    def apply(_dialog):
        rules = collect()
        c.mutate(lambda: ops.set_page_labels(doc, rules))
        w.update_page_label()
        toast(w, _('status_labels_saved'))
    dialog.on_apply = apply
    dialog.show()


def page_boxes(c):
    w = c.window
    doc = w.doc
    page = doc[w.current_page_index]
    current = ops.get_boxes(page)
    dialog = OperationDialog(w, _('menu_page_boxes'), _('btn_apply'), width=600)
    info = dialog.group(_('boxes_current', w.current_page_index + 1), _('boxes_hint'))
    for name in ops.BOXES:
        dialog.info(info, _(f'box_{name}'), '  '.join(f'{v:g}' for v in current[name]))
    controls = {}
    crop = fitz.Rect(page.cropbox)
    for name in ('trimbox', 'bleedbox', 'artbox'):
        group = dialog.group(_(f'box_{name}'), _(f'box_{name}_hint'))
        rect = fitz.Rect(current[name])
        enabled = dialog.switch(group, _('boxes_set_inset'), rect != crop)
        insets = [dialog.spin(group, _(key), value, 0, 2000, 0.5, 1)
                  for key, value in (('tool_left', rect.x0 - crop.x0), ('tool_top', rect.y0 - crop.y0),
                                     ('tool_right', crop.x1 - rect.x1), ('tool_bottom', crop.y1 - rect.y1))]
        controls[name] = (enabled, insets)
    target = dialog.group(_('range_pages'))
    pages = dialog.pages(target, default='current')

    def apply(_dialog):
        selected = pages.pages()

        def mutation():
            for number in selected:
                page_crop = fitz.Rect(doc[number].cropbox)
                boxes = {}
                for name, (enabled, insets) in controls.items():
                    left, top, right, bottom = (spin.get_value() for spin in insets)
                    rect = fitz.Rect(page_crop.x0 + left, page_crop.y0 + top, page_crop.x1 - right, page_crop.y1 - bottom)
                    boxes[name] = rect if enabled.get_active() else page_crop
                ops.set_boxes(doc, [number], boxes)
        c.mutate(mutation)
        toast(w, _('status_boxes_saved', len(selected)))
    dialog.on_apply = apply
    dialog.show()
