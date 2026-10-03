"""Format conversion, optimization, recolouring, and structured exports."""
import io
import json
import os
import zipfile

import pymupdf as fitz

from .common import OperationError, check_cancel, report, open_copy

# Extensions MuPDF can open besides PDF, grouped for file-dialog filters.
DOCUMENT_EXTENSIONS = ('xps', 'oxps', 'epub', 'mobi', 'fb2', 'cbz', 'svg', 'txt')
IMAGE_EXTENSIONS = ('png', 'jpg', 'jpeg', 'tif', 'tiff', 'bmp', 'gif', 'jxr', 'pnm', 'pgm', 'pbm', 'ppm', 'pam', 'psd', 'webp')
OPENABLE_EXTENSIONS = DOCUMENT_EXTENSIONS + IMAGE_EXTENSIONS


def is_convertible(path):
    return os.path.splitext(path)[1].lower().lstrip('.') in OPENABLE_EXTENSIONS


def convert_file_to_pdf(path, layout=None):
    """Open a non-PDF document or image and return a new PDF ``Document``.

    ``layout`` is an optional (width, height, font_size) for reflowable formats.
    """
    try:
        source = fitz.open(path)
    except Exception as error:
        raise OperationError(f'Could not open {os.path.basename(path)}: {error}')
    with source:
        if source.is_pdf:
            raise OperationError('The file is already a PDF.')
        if layout and source.is_reflowable:
            width, height, size = layout
            source.layout(width=width, height=height, fontsize=size)
        data = source.convert_to_pdf()
        toc = source.get_toc(simple=True)
        meta = {k: v for k, v in (source.metadata or {}).items()
                if k in ('title', 'author', 'subject', 'keywords') and v}
    output = fitz.open('pdf', data)
    if toc:
        try:
            output.set_toc(toc)
        except Exception:
            pass
    if meta:
        output.set_metadata(meta)
    return output


def images_to_pdf(paths, page_size=None, margin=0, fit='fit', progress=None, cancel=None):
    """Combine image files into a PDF, one page per image frame.

    page_size=None makes each page the image's own size; otherwise images are
    fitted ('fit') or stretched ('fill') onto the given (width, height).
    """
    if not paths:
        raise OperationError('Choose at least one image.')
    output = fitz.open()
    for index, path in enumerate(paths):
        check_cancel(cancel)
        try:
            image_doc = fitz.open(path)
        except Exception as error:
            raise OperationError(f'Could not open {os.path.basename(path)}: {error}')
        with image_doc:
            pdf_bytes = image_doc.convert_to_pdf()
        with fitz.open('pdf', pdf_bytes) as frames:
            for frame in frames:
                if page_size is None:
                    width, height = frame.rect.width + 2 * margin, frame.rect.height + 2 * margin
                else:
                    width, height = page_size
                    if (frame.rect.width > frame.rect.height) != (width > height):
                        width, height = height, width
                page = output.new_page(width=width, height=height)
                target = fitz.Rect(margin, margin, width - margin, height - margin)
                page.show_pdf_page(target, frames, frame.number, keep_proportion=(fit != 'fill'))
        report(progress, index + 1, len(paths))
    return output


# ---------------------------------------------------------------- optimization

def optimize(doc, image_dpi=None, image_quality=0, grayscale_images=False, subset_fonts=True,
             remove_metadata=False, remove_thumbnails=True, object_streams=True, clean=True):
    """Return optimized PDF bytes and leave the live document untouched."""
    with open_copy(doc) as copy:
        if image_dpi or image_quality or grayscale_images:
            threshold = int(image_dpi * 1.25) if image_dpi else None
            copy.rewrite_images(dpi_threshold=threshold, dpi_target=int(image_dpi or 0),
                                quality=int(image_quality or 0), set_to_gray=grayscale_images)
        if subset_fonts:
            try:
                copy.subset_fonts()
            except Exception:
                # Subsetting is an optimization; a font that cannot be subset stays whole.
                pass
        if remove_metadata or remove_thumbnails:
            copy.scrub(attached_files=False, clean_pages=False, embedded_files=False, hidden_text=False,
                       javascript=False, metadata=remove_metadata, redactions=False, remove_links=False,
                       reset_fields=False, reset_responses=False, thumbnails=remove_thumbnails,
                       xml_metadata=remove_metadata)
        return copy.tobytes(garbage=4, deflate=True, deflate_images=True, deflate_fonts=True,
                            clean=clean, use_objstms=1 if object_streams else 0,
                            encryption=fitz.PDF_ENCRYPT_KEEP)


def recolor(doc, components=1):
    """Return a new document converted to gray (1), RGB (3), or CMYK (4)."""
    if components not in (1, 3, 4):
        raise OperationError('Choose gray, RGB, or CMYK.')
    copy = open_copy(doc)
    copy.recolor(components)
    return copy


# ---------------------------------------------------------------- raster export

RASTER_FORMATS = (('png', 'PNG'), ('jpeg', 'JPEG'), ('pnm', 'PNM'), ('psd', 'PSD'), ('pam', 'PAM'))
COLORSPACES = (('rgb', 'RGB'), ('gray', 'Grayscale'), ('cmyk', 'CMYK'))


def render_page_image(page, dpi=150, image_format='png', colorspace='rgb', alpha=False, quality=90,
                      annots=True, clip=None):
    """Render one page to encoded image bytes, validating format/colourspace pairs."""
    space = {'rgb': fitz.csRGB, 'gray': fitz.csGRAY, 'cmyk': fitz.csCMYK}[colorspace]
    if alpha and image_format not in ('png', 'pam'):
        raise OperationError('Transparency is only available for PNG and PAM.')
    if colorspace == 'cmyk' and image_format not in ('pam', 'psd'):
        raise OperationError('CMYK output requires PSD or PAM.')
    if alpha and colorspace == 'cmyk':
        raise OperationError('Transparent CMYK output is not supported.')
    pix = page.get_pixmap(dpi=dpi, colorspace=space, alpha=alpha, annots=annots, clip=clip)
    if image_format == 'jpeg':
        return pix.tobytes('jpeg', jpg_quality=int(quality))
    if image_format == 'psd':
        # MuPDF's PSD writer needs a seekable output, which memory buffers are not.
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'page.psd')
            pix.save(path)
            with open(path, 'rb') as handle:
                return handle.read()
    return pix.tobytes(image_format)


def export_pages_raster(doc, pages, destination, stem, dpi=150, image_format='png', colorspace='rgb',
                        alpha=False, quality=90, progress=None, cancel=None):
    """Write each page as an image. ``destination`` is a directory or a .zip path."""
    extension = 'jpg' if image_format == 'jpeg' else image_format
    names = [f'{stem}-page-{n + 1:0{len(str(doc.page_count))}d}.{extension}' for n in pages]
    if destination.lower().endswith('.zip'):
        temporary = destination + '.part'
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_STORED) as archive:
                for index, (number, name) in enumerate(zip(pages, names)):
                    check_cancel(cancel)
                    archive.writestr(name, render_page_image(doc[number], dpi, image_format, colorspace, alpha, quality))
                    report(progress, index + 1, len(pages))
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return [destination]
    written = []
    for index, (number, name) in enumerate(zip(pages, names)):
        check_cancel(cancel)
        path = os.path.join(destination, name)
        with open(path, 'wb') as handle:
            handle.write(render_page_image(doc[number], dpi, image_format, colorspace, alpha, quality))
        written.append(path)
        report(progress, index + 1, len(pages))
    return written


# ---------------------------------------------------------------- structured text

TEXT_FORMATS = (
    ('text', 'Plain text (reading order)', 'txt'),
    ('blocks', 'Text blocks (JSON)', 'json'),
    ('json', 'Structured JSON (blocks, lines, spans)', 'json'),
    ('rawjson', 'Raw JSON (with characters)', 'json'),
    ('html', 'HTML (positioned)', 'html'),
    ('xhtml', 'XHTML (semantic)', 'xhtml'),
    ('xml', 'XML (characters)', 'xml'),
    ('markdown', 'Markdown (headings and tables)', 'md'),
)


def export_text(doc, pages, text_format, sort=True, progress=None, cancel=None):
    """Return text in the requested format for the selected pages as a str."""
    parts = []
    flags = fitz.TEXTFLAGS_TEXT | fitz.TEXT_PRESERVE_WHITESPACE
    for index, number in enumerate(pages):
        check_cancel(cancel)
        page = doc[number]
        if text_format == 'text':
            parts.append(page.get_text('text', sort=sort, flags=flags))
        elif text_format == 'blocks':
            parts.append({'page': number + 1, 'blocks': [
                {'bbox': block[:4], 'text': block[4], 'type': 'image' if block[6] else 'text'}
                for block in page.get_text('blocks', sort=sort)]})
        elif text_format in ('json', 'rawjson'):
            parts.append(json.loads(page.get_text(text_format, sort=sort)))
        elif text_format == 'markdown':
            parts.append(_page_markdown(page))
        elif text_format in ('html', 'xhtml', 'xml'):
            parts.append(page.get_text(text_format))
        else:
            raise OperationError(f'Unknown text format: {text_format}.')
        report(progress, index + 1, len(pages))
    if text_format in ('blocks', 'json', 'rawjson'):
        return json.dumps({'pages': parts}, ensure_ascii=False, indent=1,
                          default=lambda value: list(value) if hasattr(value, '__iter__') else str(value))
    if text_format == 'text':
        return '\f'.join(parts)
    if text_format == 'markdown':
        return '\n\n---\n\n'.join(parts)
    if text_format == 'xml':
        return '<?xml version="1.0" encoding="UTF-8"?>\n<document>\n' + '\n'.join(parts) + '\n</document>\n'
    head = '<!DOCTYPE html>\n<html><head><meta charset="utf-8"></head><body>\n'
    return head + '\n'.join(parts) + '\n</body></html>\n'


def _page_markdown(page):
    """Markdown approximation: font-size headings, paragraphs, and detected tables."""
    try:
        tables = list(page.find_tables())
    except Exception:
        tables = []
    table_rects = [fitz.Rect(t.bbox) for t in tables]
    data = page.get_text('dict', sort=True)
    sizes = [span['size'] for block in data['blocks'] if block['type'] == 0
             for line in block['lines'] for span in line['spans'] if span['text'].strip()]
    body = sorted(sizes)[len(sizes) // 2] if sizes else 11
    items = []
    for block in data['blocks']:
        if block['type'] != 0:
            continue
        rect = fitz.Rect(block['bbox'])
        if any(rect.intersects(t) for t in table_rects):
            continue
        text = ' '.join(''.join(s['text'] for s in line['spans']).strip() for line in block['lines']).strip()
        if not text:
            continue
        size = max(s['size'] for line in block['lines'] for s in line['spans'])
        bold = all(s['flags'] & 16 for line in block['lines'] for s in line['spans'] if s['text'].strip())
        if size >= body * 1.6:
            text = '# ' + text
        elif size >= body * 1.3:
            text = '## ' + text
        elif bold and len(text) < 120 and size > body * 1.05:
            text = '### ' + text
        items.append((rect.y0, text))
    for table, rect in zip(tables, table_rects):
        try:
            items.append((rect.y0, table.to_markdown(clean=False)))
        except Exception:
            continue
    return '\n\n'.join(text for _y, text in sorted(items, key=lambda item: item[0]))


def export_tables(doc, pages, table_format='markdown', strategy='lines', progress=None, cancel=None):
    """Export detected tables as Markdown, JSON, or a ZIP of CSV files (bytes)."""
    import csv
    found = []
    for index, number in enumerate(pages):
        check_cancel(cancel)
        for table_index, table in enumerate(doc[number].find_tables(strategy=strategy)):
            found.append((number, table_index, table))
        report(progress, index + 1, len(pages))
    if not found:
        raise OperationError('No tables were detected on the selected pages.')
    if table_format == 'markdown':
        chunks = [f'### Page {n + 1}, table {i + 1}\n\n{t.to_markdown(clean=False)}' for n, i, t in found]
        return '\n\n'.join(chunks).encode('utf-8')
    if table_format == 'json':
        rows = [{'page': n + 1, 'table': i + 1, 'bbox': list(t.bbox),
                 'header': list(t.header.names) if t.header else None, 'rows': t.extract()} for n, i, t in found]
        return json.dumps(rows, ensure_ascii=False, indent=1).encode('utf-8')
    if table_format == 'csv':
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for n, i, t in found:
                text = io.StringIO()
                csv.writer(text).writerows([[cell if cell is not None else '' for cell in row] for row in t.extract()])
                archive.writestr(f'page-{n + 1}-table-{i + 1}.csv', text.getvalue())
        return buffer.getvalue()
    raise OperationError(f'Unknown table format: {table_format}.')
