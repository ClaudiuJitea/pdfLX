"""Optional OCR through MuPDF's Tesseract integration.

Tesseract is an external program; OCR is offered only when it and at least one
language model are found. OCR output is a new document whose recognized text is
an invisible layer over the original page image or content, so it stays
distinguishable from the original digital text (it can be identified by its
invisible render mode via ``get_texttrace``).
"""
import glob
import os
import shutil

import pymupdf as fitz

from .common import OperationError, check_cancel, report


def tessdata_dir():
    """Locate the tessdata directory, or return None."""
    env = os.environ.get('TESSDATA_PREFIX')
    if env and glob.glob(os.path.join(env, '*.traineddata')):
        return env
    try:
        found = fitz.get_tessdata()
        if found and glob.glob(os.path.join(found, '*.traineddata')):
            return found
    except Exception:
        pass
    for pattern in ('/usr/share/tesseract-ocr/*/tessdata', '/usr/share/tessdata', '/usr/local/share/tessdata',
                    '/opt/homebrew/share/tessdata', '/app/share/tessdata'):
        for candidate in sorted(glob.glob(pattern), reverse=True):
            if glob.glob(os.path.join(candidate, '*.traineddata')):
                return candidate
    return None


def languages():
    directory = tessdata_dir()
    if not directory:
        return []
    names = [os.path.basename(p)[:-len('.traineddata')] for p in glob.glob(os.path.join(directory, '*.traineddata'))]
    return sorted(name for name in names if name != 'osd')


def available():
    return bool(languages())


def status_message():
    if available():
        return ''
    if shutil.which('tesseract') is None:
        return ('OCR needs Tesseract. Install it with your package manager, for example: '
                'sudo apt install tesseract-ocr tesseract-ocr-eng')
    return 'Tesseract is installed but no language data was found. Install a package such as tesseract-ocr-eng.'


def page_has_text(page, minimum=20):
    return len(page.get_text('text').strip()) >= minimum


def ocr_document(doc, pages=None, language='eng', dpi=300, skip_text_pages=True,
                 progress=None, cancel=None):
    """Return a new searchable PDF.

    Selected pages keep their original content and receive an invisible
    (render mode 3) text layer from Tesseract, so appearance is unchanged and
    OCR text remains distinguishable from original text. Pages that already
    contain text are copied unchanged when ``skip_text_pages`` is set.
    ``language`` accepts Tesseract syntax such as 'eng+deu'.
    """
    directory = tessdata_dir()
    if not directory:
        raise OperationError(status_message())
    missing = [lang for lang in language.split('+') if lang not in languages()]
    if missing:
        raise OperationError(f'Missing OCR language data: {", ".join(missing)}.')
    selected = set(range(doc.page_count) if pages is None else pages)
    output = fitz.open()
    output.insert_pdf(doc)
    processed = 0
    for number in range(doc.page_count):
        check_cancel(cancel)
        page = doc[number]
        if number in selected and not (skip_text_pages and page_has_text(page)):
            textpage = page.get_textpage_ocr(language=language, dpi=dpi, full=True, tessdata=directory)
            add_invisible_words(output[number], page.get_text('words', textpage=textpage))
            processed += 1
        report(progress, number + 1, doc.page_count)
    output.ocr_processed_pages = processed
    return output


def add_invisible_words(page, words):
    """Write OCR words as invisible text, horizontally scaled to fill each word box."""
    font = fitz.Font('helv')
    for x0, y0, x1, y1, word, *_rest in words:
        height = y1 - y0
        if height <= 0 or not word.strip():
            continue
        size = height * 0.85
        natural = font.text_length(word, fontsize=size)
        if natural <= 0:
            continue
        origin = fitz.Point(x0, y1 - height * 0.18)
        writer = fitz.TextWriter(page.rect)
        writer.append(origin, word, font=font, fontsize=size)
        writer.write_text(page, render_mode=3, morph=(origin, fitz.Matrix((x1 - x0) / natural, 1)))


def ocr_text_layer(page, language='eng', dpi=300, full=True):
    """Return OCR text for one page without changing the document."""
    directory = tessdata_dir()
    if not directory:
        raise OperationError(status_message())
    textpage = page.get_textpage_ocr(language=language, dpi=dpi, full=full, tessdata=directory)
    return page.get_text('text', textpage=textpage)
