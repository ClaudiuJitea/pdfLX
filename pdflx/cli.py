"""pdflx-cli: batch document operations without the GUI.

Every subcommand reuses pdflx.ops, so results match the application.
Inputs are never modified in place; an output path or directory is required
(except for read-only commands such as info, verify, and compare).
"""
import argparse
import glob
import json
import os
import sys

import pymupdf as fitz

from .ops import authoring, convert, pages, security, structure, text as text_ops
from .ops.common import OperationError, parse_page_ranges


def _open(path, password=None):
    try:
        doc = fitz.open(path)
    except Exception as error:
        raise OperationError(f'Cannot open {path}: {error}')
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            raise OperationError(f'{path} is encrypted; pass the correct --password.')
    doc.editor_password = password or ''
    doc.editor_can_edit = True
    if not doc.is_pdf:
        converted = fitz.open('pdf', doc.convert_to_pdf())
        doc.close()
        converted.editor_password = ''
        return converted
    return doc


def _expand(inputs):
    paths = []
    for item in inputs:
        matches = sorted(glob.glob(item)) if any(ch in item for ch in '*?[') else [item]
        paths.extend(matches)
    if not paths:
        raise OperationError('No input files matched.')
    return paths


def _write(path, data, force=False):
    if os.path.exists(path) and not force:
        raise OperationError(f'{path} exists; use --force to overwrite.')
    mode = 'w' if isinstance(data, str) else 'wb'
    with open(path, mode, **({'encoding': 'utf-8'} if mode == 'w' else {})) as handle:
        handle.write(data)


def _save(doc, path, force=False):
    _write(path, doc.tobytes(garbage=3, deflate=True), force)


def _outputs(args, suffix):
    """Map each input to an output path: --output for one file, --out-dir for many."""
    inputs = _expand(args.inputs)
    if getattr(args, 'output', None):
        if len(inputs) != 1:
            raise OperationError('--output works with one input; use --out-dir for several.')
        return [(inputs[0], args.output)]
    directory = getattr(args, 'out_dir', None) or '.'
    os.makedirs(directory, exist_ok=True)
    result = []
    for path in inputs:
        stem = os.path.splitext(os.path.basename(path))[0]
        result.append((path, os.path.join(directory, f'{stem}{suffix}')))
    return result


def _each(args, suffix, function):
    for source, target in _outputs(args, suffix):
        with _open(source, args.password) as doc:
            result = function(doc)
            if isinstance(result, (bytes, str)):
                _write(target, result, args.force)
            else:
                _save(result, target, args.force)
        print(f'{source} -> {target}')


# ---------------------------------------------------------------- commands

def cmd_merge(args):
    output = fitz.open()
    for path in _expand(args.inputs):
        with _open(path, args.password) as doc:
            output.insert_pdf(doc)
    _save(output, args.output, args.force)
    print(f'Merged {output.page_count} pages -> {args.output}')


def cmd_split(args):
    for path in _expand(args.inputs):
        with _open(path, args.password) as doc:
            if args.ranges:
                plan = pages.split_plan(doc, 'ranges', [parse_page_ranges(r, doc.page_count)
                                                        for r in args.ranges.split(';') if r.strip()])
            else:
                plan = pages.split_plan(doc, args.mode, args.every)
            os.makedirs(args.out_dir, exist_ok=True)
            stem = os.path.splitext(os.path.basename(path))[0]
            written = pages.write_parts(doc, plan, args.out_dir, stem)
            print(f'{path}: {len(written)} file(s) in {args.out_dir}')


def cmd_extract(args):
    _each(args, '-extract.pdf', lambda doc: pages.extract_pages(doc, parse_page_ranges(args.pages, doc.page_count)))


def cmd_optimize(args):
    _each(args, '-optimized.pdf', lambda doc: convert.optimize(
        doc, image_dpi=args.dpi or None, image_quality=args.quality, grayscale_images=args.gray,
        subset_fonts=not args.no_subset, remove_metadata=args.strip_metadata))


def cmd_protect(args):
    allowed = {key: key in args.allow for key, _f, _l in security.PERMISSIONS}
    _each(args, '-protected.pdf', lambda doc: security.protect(doc, args.user_password or '', args.owner_password,
                                                               allowed, args.method))


def cmd_unprotect(args):
    _each(args, '-unprotected.pdf', security.unprotect)


def cmd_sanitize(args):
    options = {key: True for key, _l in security.SCRUB_OPTIONS if key != 'reset_fields'}
    _each(args, '-sanitized.pdf', lambda doc: security.scrub(doc, options))


def cmd_rasterize(args):
    _each(args, '-raster.pdf', lambda doc: security.rasterize(doc, None, args.dpi, args.gray))


def cmd_watermark(args):
    def apply(doc):
        authoring.watermark(doc, range(doc.page_count), args.text, args.size, opacity=args.opacity,
                            angle=args.angle, tile=args.tile)
        return doc
    _each(args, '-watermarked.pdf', apply)


def cmd_bates(args):
    def apply(doc):
        pages.add_headers_footers(doc, range(doc.page_count), {args.position: args.template},
                                  font_size=args.size,
                                  bates={'prefix': args.prefix, 'start': args.start, 'digits': args.digits})
        return doc
    _each(args, '-bates.pdf', apply)


def cmd_nup(args):
    size = pages.paper_size(args.paper)
    if args.layout == 'booklet':
        _each(args, '-booklet.pdf', lambda doc: pages.booklet(doc, (size[1], size[0])))
        return
    columns, rows = {'2': (2, 1), '4': (2, 2), '6': (2, 3), '9': (3, 3), '16': (4, 4)}[args.layout]
    sheet = (size[1], size[0]) if args.layout == '2' else size
    _each(args, f'-{args.layout}up.pdf', lambda doc: pages.nup(doc, columns, rows, sheet))


def cmd_recolor(args):
    _each(args, f'-{args.to}.pdf', lambda doc: convert.recolor(doc, {'gray': 1, 'rgb': 3, 'cmyk': 4}[args.to]))


def cmd_to_pdf(args):
    if args.combine:
        result = convert.images_to_pdf(_expand(args.inputs))
        _save(result, args.output, args.force)
        print(f'{result.page_count} page(s) -> {args.output}')
        return
    for source, target in _outputs(args, '.pdf'):
        result = convert.convert_file_to_pdf(source) if convert.is_convertible(source) else _open(source)
        _save(result, target, args.force)
        print(f'{source} -> {target}')


def cmd_text(args):
    extension = dict((key, ext) for key, _l, ext in convert.TEXT_FORMATS)[args.format]
    _each(args, f'.{extension}', lambda doc: convert.export_text(doc, parse_page_ranges(args.pages, doc.page_count),
                                                                 args.format))


def cmd_images(args):
    for path in _expand(args.inputs):
        with _open(path, args.password) as doc:
            os.makedirs(args.out_dir, exist_ok=True)
            stem = os.path.splitext(os.path.basename(path))[0]
            written = convert.export_pages_raster(doc, parse_page_ranges(args.pages, doc.page_count), args.out_dir,
                                                  stem, args.dpi, args.format, args.colorspace, args.alpha)
            print(f'{path}: {len(written)} image(s) in {args.out_dir}')


def cmd_info(args):
    for path in _expand(args.inputs):
        with _open(path, args.password) as doc:
            info = structure.properties(doc, path)
            info['page_sizes'] = {f'{w}x{h}': n for (w, h), n in info['page_sizes'].items()}
            info['metadata'] = doc.metadata
            info['fonts'] = [font['name'] for font in structure.fonts(doc)]
            print(json.dumps({'file': path, **info}, indent=1, ensure_ascii=False, default=str))


def cmd_verify(args):
    from .ops import verify
    status = 0
    for path in _expand(args.inputs):
        with open(path, 'rb') as handle:
            results = verify.verify(handle.read(), args.password, args.trust or ())
        print(f'{path}: {len(results)} signature(s)')
        for item in results:
            print(f"  {item['field']}: {item['status'].upper()} — {item['signer']} ({item['signing_time']})")
            status = max(status, 0 if item['status'] == 'valid' else 1)
    return status


def cmd_compare(args):
    with _open(args.first, args.password) as first, _open(args.second, args.password) as second:
        result = text_ops.compare_text(first, second)
    if result['equal']:
        print('No text differences.')
        return 0
    print(f"{len(result['changes'])} change(s); {result['ratio'] * 100:.1f}% similar")
    for change in result['changes'][:args.limit]:
        print(f"  [{change['kind']}] p{change['page_a'] + 1}: {change['old'][:70]!r} -> {change['new'][:70]!r}")
    return 1


def cmd_compose(args):
    with open(args.input, encoding='utf-8') as handle:
        content = handle.read()
    markdown = not args.input.lower().endswith(('.html', '.htm'))
    doc = authoring.compose(content, markdown, pages.paper_size(args.paper), toc=args.toc)
    _save(doc, args.output, args.force)
    print(f'{doc.page_count} page(s) -> {args.output}')


def cmd_merge_csv(args):
    with open(args.template, 'rb') as handle:
        template = handle.read()
    rows = authoring.read_csv(args.csv)
    os.makedirs(args.out_dir, exist_ok=True)
    for index, record, doc in authoring.mail_merge(template, rows, args.flatten):
        path = os.path.join(args.out_dir, authoring.merge_filename(args.name, record, index))
        _save(doc, path, args.force)
        print(path)


def cmd_form_export(args):
    from .ops import formdata
    for source, target in _outputs(args, f'-data.{args.format}'):
        with _open(source, args.password) as doc:
            _write(target, formdata.export(doc, args.format, os.path.basename(source)), args.force)
        print(f'{source} -> {target}')


def cmd_form_import(args):
    from .ops import formdata
    with open(args.data, 'rb') as handle:
        data = handle.read()
    fmt = args.format or formdata.detect_format(args.data, data)
    values = formdata.parse(data, fmt, args.row - 1)
    with _open(args.input, args.password) as doc:
        changed, unmatched = formdata.import_values(doc, values, run_scripts=not args.no_scripts)
        if args.flatten:
            doc.bake(annots=False, widgets=True)
        _save(doc, args.output, args.force)
    print(f'Filled {changed} field(s) -> {args.output}')
    if unmatched:
        print('Not found: ' + ', '.join(unmatched), file=sys.stderr)


# ---------------------------------------------------------------- parser

def build_parser():
    parser = argparse.ArgumentParser(prog='pdflx-cli', description='pdfLX batch operations.')
    parser.add_argument('--password', help='password for encrypted inputs')
    parser.add_argument('--force', action='store_true', help='overwrite existing output files')
    sub = parser.add_subparsers(dest='command', required=True)

    def many(name, help_text, function, suffix=True):
        command = sub.add_parser(name, help=help_text)
        command.add_argument('inputs', nargs='+')
        if suffix:
            group = command.add_mutually_exclusive_group()
            group.add_argument('-o', '--output')
            group.add_argument('-d', '--out-dir')
        command.set_defaults(function=function)
        return command

    c = sub.add_parser('merge', help='combine documents in order')
    c.add_argument('inputs', nargs='+')
    c.add_argument('-o', '--output', required=True)
    c.set_defaults(function=cmd_merge)
    c = many('split', 'split into several files', cmd_split, suffix=False)
    c.add_argument('-d', '--out-dir', default='.')
    c.add_argument('--mode', choices=('every', 'bookmarks', 'single'), default='every')
    c.add_argument('--every', type=int, default=1)
    c.add_argument('--ranges', help='groups such as "1-3;4-10"')
    c = many('extract', 'extract pages', cmd_extract)
    c.add_argument('--pages', required=True)
    c = many('optimize', 'reduce file size', cmd_optimize)
    c.add_argument('--dpi', type=int, default=150)
    c.add_argument('--quality', type=int, default=75)
    c.add_argument('--gray', action='store_true')
    c.add_argument('--no-subset', action='store_true')
    c.add_argument('--strip-metadata', action='store_true')
    c = many('protect', 'encrypt with passwords and permissions', cmd_protect)
    c.add_argument('--owner-password', required=True)
    c.add_argument('--user-password')
    c.add_argument('--allow', nargs='*', default=['print', 'print_hq', 'copy', 'accessibility', 'form'],
                   choices=[key for key, _f, _l in security.PERMISSIONS])
    c.add_argument('--method', default='aes256', choices=[key for key, _v, _l in security.ENCRYPTION_METHODS])
    many('unprotect', 'remove encryption (needs the owner password)', cmd_unprotect)
    many('sanitize', 'remove metadata, scripts, attachments, hidden text', cmd_sanitize)
    c = many('rasterize', 'replace pages with images', cmd_rasterize)
    c.add_argument('--dpi', type=int, default=150)
    c.add_argument('--gray', action='store_true')
    c = many('watermark', 'add a text watermark', cmd_watermark)
    c.add_argument('--text', required=True)
    c.add_argument('--size', type=float, default=60)
    c.add_argument('--angle', type=float, default=45)
    c.add_argument('--opacity', type=float, default=0.2)
    c.add_argument('--tile', action='store_true')
    c = many('bates', 'add Bates numbers or footers', cmd_bates)
    c.add_argument('--prefix', default='')
    c.add_argument('--start', type=int, default=1)
    c.add_argument('--digits', type=int, default=6)
    c.add_argument('--template', default='{bates}')
    c.add_argument('--position', default='bottom-right', choices=pages.POSITIONS)
    c.add_argument('--size', type=float, default=9)
    c = many('nup', 'several pages per sheet, or a booklet', cmd_nup)
    c.add_argument('--layout', default='2', choices=('2', '4', '6', '9', '16', 'booklet'))
    c.add_argument('--paper', default='a4', choices=[key for key, _l, _s in pages.PAPER_SIZES])
    c = many('recolor', 'convert colors', cmd_recolor)
    c.add_argument('--to', choices=('gray', 'rgb', 'cmyk'), default='gray')
    c = many('to-pdf', 'convert images, XPS, EPUB, … to PDF', cmd_to_pdf)
    c.add_argument('--combine', action='store_true', help='combine all inputs into --output')
    c = many('text', 'export text in a structured format', cmd_text)
    c.add_argument('--format', default='markdown', choices=[key for key, _l, _e in convert.TEXT_FORMATS])
    c.add_argument('--pages', default='')
    c = many('images', 'render pages as images', cmd_images, suffix=False)
    c.add_argument('-d', '--out-dir', default='.')
    c.add_argument('--pages', default='')
    c.add_argument('--dpi', type=int, default=150)
    c.add_argument('--format', default='png', choices=[key for key, _l in convert.RASTER_FORMATS])
    c.add_argument('--colorspace', default='rgb', choices=[key for key, _l in convert.COLORSPACES])
    c.add_argument('--alpha', action='store_true')
    many('info', 'print document properties as JSON', cmd_info, suffix=False)
    c = many('verify', 'verify digital signatures', cmd_verify, suffix=False)
    c.add_argument('--trust', nargs='*', help='extra trusted certificates (PEM/DER)')
    c = sub.add_parser('compare', help='compare the text of two PDFs')
    c.add_argument('first')
    c.add_argument('second')
    c.add_argument('--limit', type=int, default=50)
    c.set_defaults(function=cmd_compare)
    c = sub.add_parser('compose', help='build a PDF from Markdown or HTML')
    c.add_argument('input')
    c.add_argument('-o', '--output', required=True)
    c.add_argument('--paper', default='a4', choices=[key for key, _l, _s in pages.PAPER_SIZES])
    c.add_argument('--toc', action='store_true')
    c.set_defaults(function=cmd_compose)
    c = many('form-export', 'export form field values', cmd_form_export)
    c.add_argument('--format', default='xfdf', choices=('fdf', 'xfdf', 'json', 'csv'))
    c = sub.add_parser('form-import', help='fill a form from FDF, XFDF, JSON, or CSV data')
    c.add_argument('input')
    c.add_argument('data')
    c.add_argument('-o', '--output', required=True)
    c.add_argument('--format', choices=('fdf', 'xfdf', 'json', 'csv'))
    c.add_argument('--row', type=int, default=1, help='CSV data row to use (1-based)')
    c.add_argument('--flatten', action='store_true')
    c.add_argument('--no-scripts', action='store_true', help='do not run the form\'s calculation/validation scripts')
    c.set_defaults(function=cmd_form_import)
    c = sub.add_parser('mail-merge', help='fill a template from CSV rows')
    c.add_argument('template')
    c.add_argument('csv')
    c.add_argument('-d', '--out-dir', default='.')
    c.add_argument('--name', default='merged-{n}', help='file name pattern, e.g. "{name}-{n}"')
    c.add_argument('--flatten', action='store_true')
    c.set_defaults(function=cmd_merge_csv)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.function(args) or 0
    except OperationError as error:
        print(f'pdflx-cli: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
