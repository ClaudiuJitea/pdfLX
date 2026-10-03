"""Native markup annotations, annotation interchange, summaries, and flattening."""
import os

import pymupdf as fitz

from ..i18n import _, get_setting, set_setting
from ..dialogs import OperationDialog, choose_files, file_filter, toast, ask, message
from ..ops import annotations as ops
from ..ops.common import OperationError

KINDS = ('rectangle', 'ellipse', 'line', 'arrow', 'double_arrow', 'textbox', 'callout', 'caret', 'attachment')


def add_markup(c):
    w = c.window
    doc = w.doc
    number = w.current_page_index
    page = doc[number]
    selection = c.selection_rect()
    dialog = OperationDialog(w, _('menu_add_markup'), _('btn_add'), width=600)
    group = dialog.group(_('markup_type'), _('markup_hint_selection') if selection else _('markup_hint_no_selection'))
    kind = dialog.combo(group, _('markup_type'), [(k, _(f'markup_{k}')) for k in KINDS], 'rectangle')
    content = dialog.entry(group, _('markup_text'), '')
    author = dialog.entry(group, _('tool_author'), get_setting('review_author', '') or os.environ.get('USER', ''))
    attachment = dialog.file(group, _('markup_attachment_file'))

    area = dialog.group(_('markup_area'))
    visible = page.rect
    coords = [dialog.spin(area, label, value, 0, limit, 1)
              for label, value, limit in (('X (pt)', min(72, visible.width / 4), visible.width),
                                          ('Y (pt)', min(72, visible.height / 4), visible.height),
                                          (_('tool_width'), min(180, visible.width / 3), visible.width),
                                          (_('tool_height'), 60, visible.height))]
    area.set_visible(selection is None)

    style = dialog.group(_('markup_style'))
    stroke = dialog.color(style, _('markup_stroke'), (0.85, 0.1, 0.1))
    use_fill = dialog.switch(style, _('markup_fill_enable'), False)
    fill = dialog.color(style, _('markup_fill'), (1, 0.95, 0.6))
    width = dialog.spin(style, _('markup_line_width'), 1.5, 0.25, 12, 0.25, 2)
    dashed = dialog.switch(style, _('markup_dashed'), False)
    size = dialog.spin(style, _('tool_font_size'), 12, 6, 72)
    opacity = dialog.spin(style, _('tool_opacity'), 100, 5, 100, 5)

    def update(*_args):
        key = kind.key
        textual = key in ('textbox', 'callout')
        content.set_title(_('markup_text') if textual else _('tool_comment'))
        attachment.set_visible(key == 'attachment')
        size.set_visible(textual)
        dashed.set_visible(key in ('rectangle', 'ellipse', 'line', 'arrow', 'double_arrow'))
        use_fill.set_visible(key in ('rectangle', 'ellipse', 'textbox', 'callout'))
    kind.connect('notify::selected', update)
    update()

    def bounds():
        if selection:
            return fitz.Rect(selection)
        x, y, wd, ht = (spin.get_value() for spin in coords)
        return fitz.Rect(x, y, x + wd, y + ht) * doc[number].derotation_matrix

    def apply(_dialog):
        key = kind.key
        rect = bounds()
        text = content.get_text()
        who = author.get_text().strip()
        set_setting('review_author', who)
        fill_rgb = fill.rgb() if use_fill.get_active() else None
        alpha = opacity.get_value() / 100
        data = None
        if key == 'attachment':
            if not attachment.path:
                raise OperationError(_('err_choose_file'))
            with open(attachment.path, 'rb') as handle:
                data = handle.read()

        def mutation():
            if key in ops.SHAPE_KINDS:
                ops.add_shape(doc, number, key, rect, stroke.rgb(), fill_rgb, width.get_value(), dashed.get_active(),
                              who, text, alpha)
            elif key == 'textbox':
                ops.add_free_text(doc, number, rect, text, size.get_value(), stroke.rgb(), fill_rgb,
                                  width.get_value() if use_fill.get_active() else 0, author=who, opacity=alpha)
            elif key == 'callout':
                target = (rect.tl + rect.br) / 2
                page_rect = doc[number].rect * doc[number].derotation_matrix
                box_w, box_h = max(120, rect.width), max(40, size.get_value() * 3)
                x0 = rect.x1 + 24 if rect.x1 + 24 + box_w <= page_rect.x1 else max(page_rect.x0, rect.x0 - 24 - box_w)
                y0 = max(page_rect.y0, min(rect.y0 - box_h - 12, page_rect.y1 - box_h))
                box = fitz.Rect(x0, y0, x0 + box_w, y0 + box_h)
                ops.add_free_text(doc, number, box, text, size.get_value(), stroke.rgb(), fill_rgb,
                                  author=who, callout_to=target, opacity=alpha)
            elif key == 'caret':
                ops.add_caret(doc, number, rect.bl, text, who, stroke.rgb())
            elif key == 'attachment':
                ops.add_file_attachment(doc, number, rect.tl, data, os.path.basename(attachment.path),
                                        text, author=who)
            doc._reset_page_refs()
        c.mutate(mutation, page_num=number)
        if hasattr(w, 'document_tools'):
            w.document_tools.refresh_comments()
        toast(w, _('status_markup_added'))
    dialog.on_apply = apply
    dialog.show()


def export_annotations(c):
    w = c.window
    text = ops.export_json(w.doc)
    c.save_data(text, f'{c.stem()}-annotations.json', [file_filter('JSON', ('*.json',))],
                _('status_annotations_exported'))


def import_annotations(c):
    w = c.window
    doc = w.doc

    def chosen(paths):
        if not paths:
            return
        try:
            with open(paths[0], 'r', encoding='utf-8') as handle:
                text = handle.read()
            result = []
            c.mutate(lambda: result.append(ops.import_json(doc, text)))
            imported, skipped = result[0]
            if hasattr(w, 'document_tools'):
                w.document_tools.refresh_comments()
            w._load_thumbnails()
            toast(w, _('status_annotations_imported', imported, skipped))
        except (OperationError, OSError, UnicodeDecodeError) as error:
            message(w, _('err_title'), str(error))
    choose_files(w, _('menu_import_annotations'), [file_filter('JSON', ('*.json',))], False, chosen)


def comment_summary(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_comment_summary'), _('btn_export'), width=640, height=600)
    markdown = ops.summary_markdown(w.doc, c.stem())
    group = dialog.group(_('summary_preview'))
    dialog.text_view(group, markdown, height=320)
    fmt = dialog.combo(dialog.group(_('output')), _('structured_format'),
                       [('pdf', 'PDF'), ('md', 'Markdown'), ('tab', _('output_new_tab'))], 'pdf')

    def apply(_dialog):
        if fmt.key == 'md':
            c.save_data(markdown, f'{c.stem()}-comments.md', [file_filter('Markdown', ('*.md',))])
            return
        summary = ops.summary_pdf(w.doc, c.stem())
        if fmt.key == 'tab':
            c.open_new(summary, f'{c.stem()}-comments.pdf')
        else:
            data = summary.tobytes(garbage=3, deflate=True)
            summary.close()
            c.save_pdf(data, f'{c.stem()}-comments')
    dialog.on_apply = apply
    dialog.show()


def flatten_annotations(c):
    w = c.window
    doc = w.doc
    count = sum(1 for page in doc for _a in page.annots() or ())
    if not count:
        raise OperationError(_('err_no_annotations'))
    keep = ask(w, _('menu_flatten_annotations'), _('flatten_annotations_body', count),
               [('cancel', _('btn_cancel'), None), ('flatten', _('btn_flatten'), 'destructive')], 'cancel')
    if keep != 'flatten':
        return
    c.flush_edits()
    pages = tuple(range(doc.page_count))
    c.mutate(lambda: ops.flatten_annotations(doc), rebase_pages=pages)
    if hasattr(w, 'document_tools'):
        w.document_tools.refresh_comments()
    w._load_thumbnails()
    toast(w, _('status_annotations_flattened', count))


def annotation_properties(c, info):
    """Edit an annotation's flags, opacity, colours, author, and subject."""
    w = c.window
    doc = w.doc
    if not c.editable():
        raise OperationError(_('err_edit_not_allowed'))
    page = doc[info['page']]
    annot = page.load_annot(info['xref'])
    if annot is None:
        raise OperationError(_('err_no_annotations'))
    flags = ops.get_flags(doc, info['page'], info['xref'])
    colors = annot.colors or {}
    dialog = OperationDialog(w, _('comments_properties'), _('btn_apply'), width=560)
    general = dialog.group(f"{info['kind']} · {_('form_page_label', info['page'] + 1)}")
    author = dialog.entry(general, _('tool_author'), annot.info.get('title', ''))
    subject = dialog.entry(general, _('annot_subject'), annot.info.get('subject', ''))
    opacity = dialog.spin(general, _('tool_opacity'), round((annot.opacity if annot.opacity >= 0 else 1) * 100),
                          5, 100, 5)
    style = dialog.group(_('markup_style'))
    stroke = dialog.color(style, _('markup_stroke'), tuple((colors.get('stroke') or (0, 0, 0))[:3]))
    fill_supported = annot.type[0] in (fitz.PDF_ANNOT_SQUARE, fitz.PDF_ANNOT_CIRCLE, fitz.PDF_ANNOT_POLYGON,
                                       fitz.PDF_ANNOT_LINE)
    use_fill = dialog.switch(style, _('markup_fill_enable'), bool(colors.get('fill')))
    fill = dialog.color(style, _('markup_fill'), tuple((colors.get('fill') or (1, 1, 1))[:3]))
    use_fill.set_visible(fill_supported)
    fill.row.set_visible(fill_supported)
    behaviour = dialog.group(_('annot_flags'), _('annot_flags_hint'))
    switches = {name: dialog.switch(behaviour, _(f'annot_flag_{name}'), flags[name]) for name, _f in ops.FLAG_NAMES}
    number, xref = info['page'], info['xref']
    keep_colors = annot.type[0] not in (fitz.PDF_ANNOT_FREE_TEXT, fitz.PDF_ANNOT_STAMP, fitz.PDF_ANNOT_FILE_ATTACHMENT)

    def apply(_dialog):
        values = {name: row.get_active() for name, row in switches.items()}
        alpha = opacity.get_value() / 100

        def mutation():
            target_page = doc[number]
            target = target_page.load_annot(xref)
            target.set_info(title=author.get_text(), subject=subject.get_text())
            target.set_opacity(alpha)
            if keep_colors:
                target.set_colors(stroke=stroke.rgb(),
                                  fill=fill.rgb() if fill_supported and use_fill.get_active() else None)
            target.update()
            ops.set_flags(doc, number, xref, values)
            doc._reset_page_refs()
        c.mutate(mutation, page_num=number)
        if hasattr(w, 'document_tools'):
            w.document_tools.refresh_comments(force=True)
    dialog.on_apply = apply
    dialog.show()
