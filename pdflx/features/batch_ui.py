"""Batch processing: apply one operation to many files, writing copies to a folder."""
import os

import pymupdf as fitz

from ..i18n import _
from ..dialogs import OperationDialog, choose_folder, file_filter, toast
from ..ops import authoring, convert, pages, security
from ..ops.common import OperationError, check_cancel, report

OPERATIONS = ('optimize', 'sanitize', 'protect', 'watermark', 'bates', 'rasterize', 'gray', 'to_pdf', 'markdown')


def _process(key, doc, values, index):
    """Return (bytes or str, extension) for one document."""
    if key == 'optimize':
        return convert.optimize(doc, image_dpi=values['dpi'], image_quality=75), 'pdf'
    if key == 'sanitize':
        return security.scrub(doc, {k: True for k, _l in security.SCRUB_OPTIONS if k != 'reset_fields'}), 'pdf'
    if key == 'protect':
        return security.protect(doc, values['user_pw'], values['owner_pw']), 'pdf'
    if key == 'watermark':
        authoring.watermark(doc, range(doc.page_count), values['text'] or 'CONFIDENTIAL')
        return doc.tobytes(garbage=3, deflate=True), 'pdf'
    if key == 'bates':
        pages.add_headers_footers(doc, range(doc.page_count), {'bottom-right': '{bates}'},
                                  bates={'prefix': values['text'], 'start': values['next_bates'], 'digits': 6})
        values['next_bates'] += doc.page_count
        return doc.tobytes(garbage=3, deflate=True), 'pdf'
    if key == 'rasterize':
        with security.rasterize(doc, None, values['dpi']) as result:
            return result.tobytes(garbage=3, deflate=True), 'pdf'
    if key == 'gray':
        with convert.recolor(doc, 1) as result:
            return result.tobytes(garbage=3, deflate=True), 'pdf'
    if key == 'to_pdf':
        return doc.tobytes(garbage=3, deflate=True), 'pdf'
    if key == 'markdown':
        return convert.export_text(doc, range(doc.page_count), 'markdown'), 'md'
    raise OperationError(f'Unknown operation: {key}.')


def batch_process(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_batch'), _('batch_run'), width=600)
    files = dialog.group(_('batch_files'), _('batch_files_hint'))
    patterns = ['*.pdf'] + [f'*.{ext}' for ext in convert.OPENABLE_EXTENSIONS]
    chooser = dialog.file(files, _('batch_choose'), [file_filter(_('filter_supported'), patterns)], multiple=True)
    group = dialog.group(_('batch_operation'))
    operation = dialog.combo(group, _('batch_operation'), [(key, _(f'batch_{key}')) for key in OPERATIONS])
    text = dialog.entry(group, _('batch_text'), '')
    dpi = dialog.spin(group, _('dpi'), 150, 36, 600, 10)
    owner = dialog.password(group, _('security_owner_password'))
    user = dialog.password(group, _('security_open_password'))
    bates_start = dialog.spin(group, _('hf_bates_start'), 1, 0, 10 ** 9)

    def update(*_args):
        key = operation.key
        text.set_visible(key in ('watermark', 'bates'))
        text.set_title(_('hf_bates_prefix') if key == 'bates' else _('watermark_text'))
        dpi.set_visible(key in ('optimize', 'rasterize'))
        owner.set_visible(key == 'protect')
        user.set_visible(key == 'protect')
        bates_start.set_visible(key == 'bates')
    operation.connect('notify::selected', update)
    update()

    def apply(_dialog):
        paths = chooser.paths
        if not paths:
            raise OperationError(_('err_choose_file'))
        key = operation.key
        if key == 'protect' and not owner.get_text():
            raise OperationError(_('security_owner_required'))
        values = {'text': text.get_text(), 'dpi': dpi.get_value_as_int(), 'owner_pw': owner.get_text(),
                  'user_pw': user.get_text(), 'next_bates': bates_start.get_value_as_int()}

        def chosen(folder):
            def work(progress, cancel):
                written, failed = [], []
                for index, path in enumerate(paths):
                    check_cancel(cancel)
                    try:
                        source = fitz.open(path)
                        if source.needs_pass:
                            raise OperationError(_('err_insert_encrypted'))
                        doc = source if source.is_pdf else fitz.open('pdf', source.convert_to_pdf())
                        doc.editor_password = ''
                        data, extension = _process(key, doc, values, index)
                        stem = os.path.splitext(os.path.basename(path))[0]
                        target = os.path.join(folder, f'{stem}-{key}.{extension}')
                        if os.path.realpath(target) == os.path.realpath(path):
                            raise OperationError(_('err_overwrite_open_file'))
                        mode = 'w' if isinstance(data, str) else 'wb'
                        with open(target, mode, **({'encoding': 'utf-8'} if mode == 'w' else {})) as handle:
                            handle.write(data)
                        written.append(target)
                    except Exception as error:
                        failed.append(f'{os.path.basename(path)}: {error}')
                    report(progress, index + 1, len(paths))
                return written, failed

            def done(result):
                written, failed = result
                if failed:
                    dialog.error(_('batch_failed', len(failed)) + ' ' + '; '.join(failed[:3]))
                    return False
                toast(w, _('status_split_done', len(written), folder))
            dialog.run(work, done)
        choose_folder(w, _('split_choose_folder'), chosen)
        return False
    dialog.on_apply = apply
    dialog.show()
