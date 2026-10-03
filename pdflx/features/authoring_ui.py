"""Authoring dialogs: compose documents, rich text boxes, watermarks, and mail merge."""
import os

import pymupdf as fitz

from ..i18n import _
from ..dialogs import OperationDialog, choose_folder, file_filter, render_preview, toast
from ..ops import authoring as ops, pages as page_ops
from ..ops.common import OperationError

SAMPLE = """# Title

Write in **Markdown**: *emphasis*, `code`, [links](https://example.org), lists, and tables.

## Section

- First point
- Second point

| Item | Value |
|------|-------|
| A    | 1     |
"""


def _text(view):
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


def compose_document(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_compose'), _('btn_create'), width=720, height=760,
                             preview=lambda: _compose_preview(controls))
    controls = {}
    content = dialog.group(_('compose_content'), _('compose_hint'))
    controls['format'] = dialog.combo(content, _('structured_format'), [('md', 'Markdown'), ('html', 'HTML')], 'md')
    controls['text'] = dialog.text_view(content, SAMPLE, height=260, editable=True, monospace=True)
    layout = dialog.group(_('compose_layout'))
    controls['paper'] = dialog.combo(layout, _('paper_size'),
                                     [(key, label) for key, label, _s in page_ops.PAPER_SIZES], 'a4')
    controls['landscape'] = dialog.switch(layout, _('paper_landscape'), False)
    controls['margin'] = dialog.spin(layout, _('margin_pt'), 56, 18, 144)
    controls['toc'] = dialog.switch(layout, _('compose_toc'), True, _('compose_toc_hint'))
    controls['numbers'] = dialog.switch(layout, _('compose_page_numbers'), True)
    controls['title'] = dialog.entry(layout, _('metadata_title'), '')
    controls['css'] = dialog.entry(layout, _('compose_css'), '')

    def apply(_dialog):
        result = _compose(controls)
        name = (controls['title'].get_text().strip() or 'document').replace('/', '-') + '.pdf'
        c.open_new(result, name, _('status_composed', result.page_count))
    dialog.on_apply = apply
    dialog.show()


def _compose(controls):
    margin = controls['margin'].get_value()
    return ops.compose(_text(controls['text']), controls['format'].key == 'md',
                       page_ops.paper_size(controls['paper'].key, controls['landscape'].get_active()),
                       (margin,) * 4, controls['css'].get_text(), controls['toc'].get_active(),
                       _('compose_toc_title'), controls['numbers'].get_active(), controls['title'].get_text().strip())


def _compose_preview(controls):
    with _compose(controls) as doc:
        return render_preview(doc, min(1, doc.page_count - 1) if controls['toc'].get_active() else 0, 0.45)


def rich_text(c):
    w = c.window
    doc = w.doc
    number = w.current_page_index
    page = doc[number]
    selection = c.selection_rect()
    dialog = OperationDialog(w, _('menu_rich_text'), _('btn_insert'), width=640,
                             preview=lambda: _rich_preview(c, number, area(), controls))
    controls = {}
    group = dialog.group(_('compose_content'), _('rich_text_hint_selection') if selection else _('rich_text_hint'))
    controls['format'] = dialog.combo(group, _('structured_format'), [('md', 'Markdown'), ('html', 'HTML')], 'md')
    controls['text'] = dialog.text_view(group, '**Note:** text with *formatting*.', height=150, editable=True)
    controls['css'] = dialog.entry(group, _('compose_css'), '')
    controls['opacity'] = dialog.spin(group, _('tool_opacity'), 100, 5, 100, 5)
    position = dialog.group(_('markup_area'))
    visible = page.rect
    coords = [dialog.spin(position, label, value, 0, limit)
              for label, value, limit in (('X (pt)', 72, visible.width), ('Y (pt)', 72, visible.height),
                                          (_('tool_width'), min(300, visible.width - 144), visible.width),
                                          (_('tool_height'), 120, visible.height))]
    position.set_visible(selection is None)

    def area():
        if selection:
            return fitz.Rect(selection)
        x, y, width, height = (spin.get_value() for spin in coords)
        return fitz.Rect(x, y, x + width, y + height) * doc[number].derotation_matrix

    def apply(_dialog):
        rect = area()
        text = _text(controls['text'])
        values = dict(is_markdown=controls['format'].key == 'md', css=controls['css'].get_text(),
                      opacity=controls['opacity'].get_value() / 100)
        c.flush_edits()
        c.mutate(lambda: ops.insert_rich_text(doc, number, rect, text, **values), rebase_pages=(number,),
                 page_num=number)
        w._refresh_thumbnail(number)
        toast(w, _('status_rich_text_added'))
    dialog.on_apply = apply
    dialog.show()


def _rich_preview(c, number, rect, controls):
    with fitz.open('pdf', c.snapshot_bytes()) as scratch:
        ops.insert_rich_text(scratch, number, rect, _text(controls['text']), controls['format'].key == 'md',
                             controls['css'].get_text(), controls['opacity'].get_value() / 100)
        return render_preview(scratch, number)


def watermark_text(c):
    w = c.window
    doc = w.doc
    dialog = OperationDialog(w, _('menu_watermark'), _('btn_apply'),
                             preview=lambda: _watermark_preview(c, controls))
    controls = {}
    group = dialog.group(_('watermark_text'), _('watermark_hint'))
    controls['text'] = dialog.entry(group, _('watermark_text'), 'CONFIDENTIAL')
    controls['size'] = dialog.spin(group, _('tool_font_size'), 60, 6, 300)
    controls['angle'] = dialog.spin(group, _('watermark_angle'), 45, -180, 180, 5)
    controls['opacity'] = dialog.spin(group, _('tool_opacity'), 20, 5, 100, 5)
    controls['color'] = dialog.color(group, _('tool_stamp_color'), (0.75, 0.1, 0.1))
    controls['tile'] = dialog.switch(group, _('watermark_tile'), False)
    controls['behind'] = dialog.switch(group, _('watermark_behind'), False, _('watermark_behind_hint'))
    controls['pages'] = dialog.pages(dialog.group(_('range_pages')))

    def apply(_dialog):
        options = _watermark_options(controls)
        selected = options.pop('pages')
        c.flush_edits()
        c.mutate(lambda: ops.watermark(doc, selected, **options), rebase_pages=selected)
        w._load_thumbnails()
        toast(w, _('status_headers_applied', len(selected)))
    dialog.on_apply = apply
    dialog.show()


def _watermark_options(controls):
    return dict(pages=controls['pages'].pages(), text=controls['text'].get_text(),
                font_size=controls['size'].get_value(), angle=controls['angle'].get_value(),
                opacity=controls['opacity'].get_value() / 100, color=controls['color'].rgb(),
                tile=controls['tile'].get_active(), behind=controls['behind'].get_active())


def _watermark_preview(c, controls):
    options = _watermark_options(controls)
    selected = options.pop('pages')
    with fitz.open('pdf', c.snapshot_bytes()) as scratch:
        ops.watermark(scratch, selected[:1], **options)
        return render_preview(scratch, selected[0])


def mail_merge(c):
    w = c.window
    doc = w.doc
    fields, placeholders = ops.merge_targets(doc)
    dialog = OperationDialog(w, _('menu_mail_merge'), _('btn_create'), width=640)
    group = dialog.group(_('merge_template'), _('merge_hint'))
    dialog.info(group, _('merge_fields'), ', '.join(fields) or _('security_none'))
    dialog.info(group, _('merge_placeholders'), ', '.join('{{' + p + '}}' for p in placeholders) or _('security_none'))
    data = dialog.group(_('merge_data'))
    status = dialog.info(data, _('merge_matched'), _('file_none_selected'))
    state = {}

    def loaded(paths):
        try:
            rows = ops.read_csv(paths[0])
        except (OperationError, OSError, UnicodeDecodeError) as error:
            dialog.error(error)
            return
        columns = list(rows[0].keys())
        matched = [col for col in columns if col in fields or col in placeholders]
        state['rows'] = rows
        status.set_subtitle(_('merge_matched_value', len(rows), ', '.join(matched) or _('security_none')))
        if not matched:
            dialog.error(_('merge_no_match'))
    dialog.file(data, _('merge_csv'), [file_filter('CSV', ('*.csv', '*.tsv', '*.txt'))], on_change=loaded)
    output = dialog.group(_('output'))
    mode = dialog.combo(output, _('output_destination'), [('combined', _('merge_combined')),
                                                          ('files', _('merge_separate'))])
    pattern = dialog.entry(output, _('merge_filename'), f'{c.stem()}-{{n}}')
    flatten = dialog.switch(output, _('merge_flatten'), True, _('merge_flatten_hint'))

    def apply(_dialog):
        rows = state.get('rows')
        if not rows:
            raise OperationError(_('err_choose_file'))
        template = c.snapshot_bytes()
        password = getattr(doc, 'editor_password', '')
        flat = flatten.get_active()

        if mode.key == 'combined':
            def work(progress, cancel):
                return ops.combine(merged for _i, _r, merged in
                                   ops.mail_merge(template, rows, flat, password, progress, cancel))
            dialog.run(work, lambda result: c.open_new(result, f'{c.stem()}-merged.pdf',
                                                       _('status_merged', len(rows))))
            return False

        name_pattern = pattern.get_text()

        def chosen(folder):
            def work(progress, cancel):
                written = []
                for index, record, merged in ops.mail_merge(template, rows, flat, password, progress, cancel):
                    path = os.path.join(folder, ops.merge_filename(name_pattern, record, index))
                    merged.save(path, garbage=3, deflate=True)
                    merged.close()
                    written.append(path)
                return written
            dialog.run(work, lambda written: toast(w, _('status_split_done', len(written), folder)))
        choose_folder(w, _('split_choose_folder'), chosen)
        return False
    dialog.on_apply = apply
    dialog.show()
