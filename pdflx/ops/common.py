"""Shared helpers for document operations."""
import re

import pymupdf as fitz


class OperationError(ValueError):
    """A user-facing failure that should be shown without a traceback."""


class Cancelled(Exception):
    """Raised by long operations when the caller requests cancellation."""


def parse_page_ranges(text, page_count):
    """Parse '1-3, 5, 8-' (1-based, inclusive) into sorted unique 0-based indices.

    An empty string or 'all' selects every page. 'odd'/'even' are accepted.
    """
    text = (text or '').strip().lower()
    if text in ('', 'all', '*'):
        return list(range(page_count))
    if text == 'odd':
        return list(range(0, page_count, 2))
    if text == 'even':
        return list(range(1, page_count, 2))
    pages = set()
    for part in re.split(r'[,;\s]+', text):
        if not part:
            continue
        match = re.fullmatch(r'(\d*)\s*-\s*(\d*)', part)
        if match:
            first = int(match.group(1)) if match.group(1) else 1
            last = int(match.group(2)) if match.group(2) else page_count
        elif part.isdigit():
            first = last = int(part)
        else:
            raise OperationError(f'Invalid page range: "{part}".')
        if first < 1 or last > page_count or first > last:
            raise OperationError(f'Page range "{part}" is outside 1–{page_count}.')
        pages.update(range(first - 1, last))
    if not pages:
        raise OperationError('Select at least one page.')
    return sorted(pages)


def format_page_ranges(pages):
    """Format 0-based indices as a compact 1-based range string."""
    pages = sorted(set(pages))
    parts, start = [], None
    for index, page in enumerate(pages):
        if start is None:
            start = page
        if index + 1 == len(pages) or pages[index + 1] != page + 1:
            parts.append(str(start + 1) if start == page else f'{start + 1}-{page + 1}')
            start = None
    return ', '.join(parts)


def document_bytes(doc):
    """Serialize a document without renumbering xrefs or changing encryption."""
    return doc.tobytes(garbage=0, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)


def open_copy(doc):
    """Open an independent, authenticated copy of a live document."""
    copy = fitz.open(stream=document_bytes(doc), filetype='pdf')
    if copy.needs_pass and not copy.authenticate(getattr(doc, 'editor_password', '') or ''):
        copy.close()
        raise OperationError('Could not open a copy of the encrypted document.')
    return copy


def mark_new_document(doc, password=''):
    """Give a generated document the attributes the editor expects from loaded ones."""
    doc.editor_can_edit = doc.editor_can_fill_forms = True
    doc.editor_can_copy = doc.editor_can_print = True
    doc.editor_password = password
    return doc


def signed_signature_fields(doc):
    """Return names of signature fields that carry a signature value."""
    names = []
    if not doc.is_pdf:
        return names
    for page in doc:
        for widget in page.widgets() or ():
            if widget.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE and widget.is_signed:
                names.append(widget.field_name or f'Signature (page {page.number + 1})')
    return names


def check_cancel(cancel):
    if cancel is not None and cancel():
        raise Cancelled()


def report(progress, done, total):
    if progress is not None:
        progress(done, total)
