"""Conversion, optimization, OCR, and export dialogs."""
import os

import pymupdf as fitz

from ..i18n import _
from ..dialogs import (OperationDialog, choose_files, choose_folder, choose_save, file_filter, toast,
                       message)
from ..ops import convert as ops, ocr as ocr_ops, pages as page_ops
from ..ops.common import OperationError


def _documents_filter():
    return file_filter(_('filter_convertible'), [f'*.{ext}' for ext in ops.DOCUMENT_EXTENSIONS + ops.IMAGE_EXTENSIONS])


def _images_filter():
    return file_filter(_('image_filter_label'), [f'*.{ext}' for ext in ops.IMAGE_EXTENSIONS])


def open_other_format(c):
    """Open XPS/EPUB/MOBI/FB2/CBZ/SVG/TXT/images as a converted, unsaved PDF tab."""
    choose_files(c.window, _('menu_open_other_format'), [_documents_filter()], True,
                 lambda paths: open_converted(c, paths))


def open_converted(c, paths):
    for path in paths:
        try:
            result = ops.convert_file_to_pdf(path, layout=(fitz.paper_size('a5') + (11,)))
        except OperationError as error:
            message(c.window, _('err_title'), str(error))
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        c.open_new(result, stem + '.pdf', _('status_converted', os.path.basename(path)))


def images_to_pdf(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_images_to_pdf'), _('btn_create'))
    group = dialog.group(_('images_source'), _('images_hint'))
    files = dialog.file(group, _('images_choose'), [_images_filter()], multiple=True)
    layout = dialog.group(_('images_layout'))
    size = dialog.combo(layout, _('paper_size'), [('image', _('images_native_size'))] +
                        [(key, label) for key, label, _s in page_ops.PAPER_SIZES], 'image')
    margin = dialog.spin(layout, _('margin_pt'), 0, 0, 144)
    fit = dialog.combo(layout, _('images_fit'), [('fit', _('images_fit_keep')), ('fill', _('images_fit_stretch'))])

    def apply(_dialog):
        paths = files.paths
        if not paths:
            raise OperationError(_('err_choose_file'))
        page_size = None if size.key == 'image' else page_ops.paper_size(size.key)

        def work(progress, cancel):
            return ops.images_to_pdf(paths, page_size, margin.get_value(), fit.key, progress, cancel)
        name = os.path.splitext(os.path.basename(paths[0]))[0] + '.pdf'
        dialog.run(work, lambda result: c.open_new(result, name, _('status_images_combined', len(paths))))
        return False
    dialog.on_apply = apply
    dialog.show()


def optimize(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_optimize'), _('optimize_save'), width=580)
    images = dialog.group(_('optimize_images'), _('optimize_images_hint'))
    downsample = dialog.switch(images, _('optimize_downsample'), True)
    dpi = dialog.spin(images, _('optimize_target_dpi'), 150, 36, 600, 10)
    quality = dialog.spin(images, _('optimize_jpeg_quality'), 75, 10, 100, 5)
    gray = dialog.switch(images, _('optimize_gray_images'), False)
    other = dialog.group(_('optimize_other'))
    subset = dialog.switch(other, _('optimize_subset_fonts'), True, _('optimize_subset_hint'))
    objstm = dialog.switch(other, _('optimize_object_streams'), True)
    strip_meta = dialog.switch(other, _('optimize_remove_metadata'), False)
    result = dialog.info(dialog.group(_('optimize_result')), _('optimize_size'), _('optimize_not_run'))
    data = c.snapshot_bytes()
    original = len(data)
    if w.current_file_path and os.path.isfile(w.current_file_path):
        original = os.path.getsize(w.current_file_path)
    state = {}

    def options():
        return dict(image_dpi=dpi.get_value_as_int() if downsample.get_active() else None,
                    image_quality=quality.get_value_as_int() if downsample.get_active() else 0,
                    grayscale_images=gray.get_active(), subset_fonts=subset.get_active(),
                    object_streams=objstm.get_active(), remove_metadata=strip_meta.get_active())

    def apply(_dialog):
        values = options()
        if state.get('options') == values and state.get('data'):
            c.save_pdf(state['data'], f'{c.stem()}-optimized', _('status_optimized'))
            return True

        def work(progress, cancel):
            progress(0, 1)
            with fitz.open('pdf', data) as copy:
                copy.editor_password = getattr(w.doc, 'editor_password', '')
                if copy.needs_pass:
                    copy.authenticate(copy.editor_password)
                return ops.optimize(copy, **values)

        def done(output):
            state.update(options=values, data=output)
            saved = 100 - len(output) * 100 / max(1, original)
            result.set_subtitle(_('optimize_size_result', _size(original), _size(len(output)), saved))
            dialog.apply_button.set_label(_('optimize_save_now'))
            return False
        dialog.run(work, done, _('optimize_running'))
        return False
    dialog.apply_button.set_label(_('optimize_analyze'))
    dialog.on_apply = apply
    dialog.show()


def _size(count):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if count < 1024 or unit == 'GB':
            return f'{count:.0f} {unit}' if unit == 'B' else f'{count:.1f} {unit}'
        count /= 1024


def recolor(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_recolor'), _('btn_create'))
    group = dialog.group(_('recolor_title'), _('recolor_hint'))
    target = dialog.combo(group, _('recolor_target'), [('1', _('colorspace_gray')), ('3', _('colorspace_rgb')),
                                                        ('4', _('colorspace_cmyk'))], '1')

    def apply(_dialog):
        components = int(target.key)
        data = c.snapshot_bytes()

        def work(progress, cancel):
            progress(0, 1)
            with fitz.open('pdf', data) as copy:
                return ops.recolor(copy, components)
        suffix = {1: 'gray', 3: 'rgb', 4: 'cmyk'}[components]
        dialog.run(work, lambda result: c.open_new(result, f'{c.stem()}-{suffix}.pdf', _('status_new_tab_created')))
        return False
    dialog.on_apply = apply
    dialog.show()


def ocr(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_ocr'), _('ocr_run'), width=580)
    if not ocr_ops.available():
        group = dialog.group(_('ocr_unavailable'), ocr_ops.status_message())
        dialog.apply_button.set_sensitive(False)
        dialog.show()
        return
    group = dialog.group(_('ocr_title'), _('ocr_hint'))
    languages = ocr_ops.languages()
    default = 'eng' if 'eng' in languages else languages[0]
    language = dialog.combo(group, _('ocr_language'), [(lang, lang) for lang in languages], default)
    extra = dialog.entry(group, _('ocr_extra_languages'), '')
    pages = dialog.pages(group)
    dpi = dialog.spin(group, _('dpi'), 300, 100, 600, 50)
    skip = dialog.switch(group, _('ocr_skip_text_pages'), True)

    def apply(_dialog):
        lang = '+'.join([language.key] + [part.strip() for part in extra.get_text().split('+') if part.strip()])
        selected = pages.pages()
        data = c.snapshot_bytes()

        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                return ocr_ops.ocr_document(copy, selected, lang, dpi.get_value_as_int(), skip.get_active(),
                                            progress, cancel)

        def done(result):
            count = getattr(result, 'ocr_processed_pages', 0)
            if not count:
                result.close()
                dialog.error(_('ocr_nothing_done'))
                return False
            c.open_new(result, f'{c.stem()}-ocr.pdf', _('status_ocr_done', count))
        dialog.run(work, done, _('ocr_running'))
        return False
    dialog.on_apply = apply
    dialog.show()


def export_images(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_export_images'), _('btn_export'))
    group = dialog.group(_('range_pages'))
    pages = dialog.pages(group, default='current')
    fmt = dialog.group(_('export_image_format'))
    image_format = dialog.combo(fmt, _('export_image_format'), list(ops.RASTER_FORMATS), 'png')
    colorspace = dialog.combo(fmt, _('export_colorspace'),
                              [(key, _(f'colorspace_{key}')) for key, _l in ops.COLORSPACES], 'rgb')
    dpi = dialog.spin(fmt, _('dpi'), 150, 36, 1200, 12)
    quality = dialog.spin(fmt, _('optimize_jpeg_quality'), 90, 10, 100, 5)
    alpha = dialog.switch(fmt, _('export_transparent'), False, _('export_transparent_hint'))
    destination = dialog.combo(dialog.group(_('output')), _('output_destination'),
                               [('zip', _('output_zip')), ('folder', _('output_folder'))], 'zip')

    def apply(_dialog):
        selected = pages.pages()
        # Validate the combination before choosing a destination.
        ops.render_page_image(fitz.open().new_page(width=10, height=10), 10, image_format.key, colorspace.key,
                              alpha.get_active())
        data = c.snapshot_bytes()
        stem = c.stem()
        values = dict(dpi=dpi.get_value_as_int(), image_format=image_format.key, colorspace=colorspace.key,
                      alpha=alpha.get_active(), quality=quality.get_value_as_int())

        def start(target):
            def work(progress, cancel):
                with fitz.open('pdf', data) as copy:
                    return ops.export_pages_raster(copy, selected, target, stem, progress=progress, cancel=cancel,
                                                   **values)
            dialog.run(work, lambda written: toast(w, _('status_images_exported', len(selected))))
        if destination.key == 'zip':
            choose_save(w, _('menu_export_images'), f'{stem}-pages.zip',
                        [file_filter('ZIP', ('*.zip',))], start)
        else:
            choose_folder(w, _('menu_export_images'), start)
        return False
    dialog.on_apply = apply
    dialog.show()


def export_structured(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_export_structured'), _('btn_export'))
    group = dialog.group(_('structured_title'), _('structured_hint'))
    fmt = dialog.combo(group, _('structured_format'), [(key, label) for key, label, _e in ops.TEXT_FORMATS], 'markdown')
    pages = dialog.pages(group)
    sort = dialog.switch(group, _('structured_reading_order'), True, _('structured_reading_order_hint'))

    def apply(_dialog):
        selected = pages.pages()
        data = c.snapshot_bytes()
        key = fmt.key
        extension = dict((k, e) for k, _l, e in ops.TEXT_FORMATS)[key]

        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                return ops.export_text(copy, selected, key, sort.get_active(), progress, cancel)
        dialog.run(work, lambda text: (c.save_data(text, f'{c.stem()}.{extension}',
                                                   [file_filter(extension.upper(), (f'*.{extension}',))]), True)[1])
        return False
    dialog.on_apply = apply
    dialog.show()


def export_tables(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_export_tables'), _('btn_export'))
    group = dialog.group(_('tables_title'), _('tables_hint'))
    fmt = dialog.combo(group, _('structured_format'), [('markdown', 'Markdown'), ('json', 'JSON'),
                                                        ('csv', _('tables_csv_zip'))], 'markdown')
    strategy = dialog.combo(group, _('tables_strategy'), [('lines', _('tables_strategy_lines')),
                                                          ('text', _('tables_strategy_text'))], 'lines')
    pages = dialog.pages(group)

    def apply(_dialog):
        selected = pages.pages()
        data = c.snapshot_bytes()
        key = fmt.key
        extension = {'markdown': 'md', 'json': 'json', 'csv': 'zip'}[key]

        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                return ops.export_tables(copy, selected, key, strategy.key, progress, cancel)
        dialog.run(work, lambda output: (c.save_data(output, f'{c.stem()}-tables.{extension}',
                                                     [file_filter(extension.upper(), (f'*.{extension}',))]), True)[1])
        return False
    dialog.on_apply = apply
    dialog.show()
