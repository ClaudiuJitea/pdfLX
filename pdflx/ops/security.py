"""Password protection, permissions, and sanitization."""
import pymupdf as fitz

from .common import OperationError, open_copy, check_cancel, report

# (key, PyMuPDF flag, English description). The keys double as i18n suffixes.
PERMISSIONS = (
    ('print', fitz.PDF_PERM_PRINT, 'Print'),
    ('print_hq', fitz.PDF_PERM_PRINT_HQ, 'Print in high quality'),
    ('modify', fitz.PDF_PERM_MODIFY, 'Modify content'),
    ('copy', fitz.PDF_PERM_COPY, 'Copy text and images'),
    ('accessibility', fitz.PDF_PERM_ACCESSIBILITY, 'Extract for accessibility'),
    ('annotate', fitz.PDF_PERM_ANNOTATE, 'Add or change comments'),
    ('form', fitz.PDF_PERM_FORM, 'Fill in form fields'),
    ('assemble', fitz.PDF_PERM_ASSEMBLE, 'Insert, delete, or rotate pages'),
)

ENCRYPTION_METHODS = (
    ('aes256', fitz.PDF_ENCRYPT_AES_256, 'AES 256-bit'),
    ('aes128', fitz.PDF_ENCRYPT_AES_128, 'AES 128-bit'),
    ('rc4_128', fitz.PDF_ENCRYPT_RC4_128, 'RC4 128-bit (legacy)'),
)


def describe_security(doc):
    """Summarize the current encryption and permission state."""
    meta = doc.metadata or {}
    granted = {key: bool(doc.permissions & flag) for key, flag, _label in PERMISSIONS}
    return {
        'encrypted': bool(doc.is_encrypted or meta.get('encryption')),
        'method': meta.get('encryption') or '',
        'permissions': granted,
    }


def protect(doc, user_password='', owner_password='', permissions=None, method='aes256'):
    """Return encrypted PDF bytes. ``permissions`` maps PERMISSIONS keys to bools.

    An owner password is required so restrictions cannot be removed by anyone
    holding only the open password.
    """
    if not owner_password:
        raise OperationError('An owner password is required to set permissions.')
    if user_password and user_password == owner_password:
        raise OperationError('Use different open and owner passwords.')
    allowed = permissions if permissions is not None else {key: True for key, _f, _l in PERMISSIONS}
    mask = 0
    for key, flag, _label in PERMISSIONS:
        if allowed.get(key, False):
            mask |= flag
    algorithm = dict((key, value) for key, value, _l in ENCRYPTION_METHODS).get(method)
    if algorithm is None:
        raise OperationError(f'Unknown encryption method: {method}.')
    with open_copy(doc) as copy:
        return copy.tobytes(garbage=3, deflate=True, encryption=algorithm,
                            owner_pw=owner_password, user_pw=user_password or None,
                            permissions=mask)


def unprotect(doc):
    """Return decrypted PDF bytes. Requires owner-level access on the live document."""
    if not getattr(doc, 'editor_can_edit', True):
        raise OperationError('Open the document with its owner password to remove security.')
    with open_copy(doc) as copy:
        return copy.tobytes(garbage=3, deflate=True, encryption=fitz.PDF_ENCRYPT_NONE)


# Scrub options in display order: (keyword for Document.scrub, English description).
SCRUB_OPTIONS = (
    ('metadata', 'Document information (title, author, …)'),
    ('xml_metadata', 'XMP metadata'),
    ('javascript', 'JavaScript'),
    ('embedded_files', 'Embedded files'),
    ('attached_files', 'File-attachment annotations'),
    ('remove_links', 'Links'),
    ('thumbnails', 'Embedded page thumbnails'),
    ('reset_fields', 'Reset form fields to defaults'),
    ('reset_responses', 'Annotation replies'),
    ('hidden_text', 'Hidden text (invisible render mode)'),
    ('redactions', 'Apply pending redaction annotations'),
    ('clean_pages', 'Clean and compact page content streams'),
)


def scrub(doc, options):
    """Sanitize a copy of the document and return the resulting bytes.

    The copy is saved with garbage collection so unreferenced objects (old
    revisions, removed resources) are dropped from the output.
    """
    selected = {key: bool(options.get(key, False)) for key, _label in SCRUB_OPTIONS}
    if not any(selected.values()):
        raise OperationError('Choose at least one item to remove.')
    with open_copy(doc) as copy:
        copy.scrub(redact_images=fitz.PDF_REDACT_IMAGE_PIXELS, **selected)
        # Editor state may carry text that was scrubbed from the page.
        for page in copy:
            if copy.xref_get_key(page.xref, 'PdfLXEditor')[0] != 'null':
                copy.xref_set_key(page.xref, 'PdfLXEditor', 'null')
        return copy.tobytes(garbage=4, deflate=True, clean=True, encryption=fitz.PDF_ENCRYPT_KEEP)


def rasterize(doc, pages=None, dpi=150, grayscale=False, progress=None, cancel=None):
    """Return a new document where the selected pages are image-only.

    Text, vector graphics, links, annotations, and form fields on those pages
    are replaced by a picture of the page; other pages are copied unchanged.
    """
    selected = set(range(doc.page_count) if pages is None else pages)
    output = fitz.open()
    colorspace = fitz.csGRAY if grayscale else fitz.csRGB
    for number in range(doc.page_count):
        check_cancel(cancel)
        page = doc[number]
        if number in selected:
            pix = page.get_pixmap(dpi=dpi, colorspace=colorspace, alpha=False, annots=True)
            target = output.new_page(width=page.rect.width, height=page.rect.height)
            target.insert_image(target.rect, stream=pix.tobytes('jpeg', jpg_quality=88))
        else:
            output.insert_pdf(doc, from_page=number, to_page=number)
        report(progress, number + 1, doc.page_count)
    output.set_metadata({k: v for k, v in (doc.metadata or {}).items()
                         if k in ('title', 'author', 'subject', 'keywords')})
    return output


def find_hidden_text(doc, progress=None, cancel=None):
    """Report text a reader would not see: invisible render mode, off-page, or tiny.

    Returns a list of dicts with page (0-based), text, reason, and bbox.
    White-on-white and covered text are reported via colour/opacity heuristics
    and may include false positives; results are for review, not removal.
    """
    findings = []
    for number in range(doc.page_count):
        check_cancel(cancel)
        page = doc[number]
        visible = page.rect
        for span in page.get_texttrace():
            text = ''.join(chr(char[0]) for char in span.get('chars', ()) if char[0] > 0).strip()
            if not text:
                continue
            bbox = fitz.Rect(span['bbox'])
            reason = None
            if span.get('type') == 3:
                reason = 'invisible'
            elif not bbox.intersects(visible):
                reason = 'off-page'
            elif span.get('opacity', 1) == 0:
                reason = 'transparent'
            elif span.get('size', 12) * abs(span.get('scale', 1) or 1) < 1:
                reason = 'tiny'
            elif tuple(round(c, 2) for c in (span.get('color') or ())) in ((1.0,), (1.0, 1.0, 1.0)):
                reason = 'white'
            if reason:
                findings.append({'page': number, 'text': text, 'reason': reason, 'bbox': tuple(bbox)})
        report(progress, number + 1, doc.page_count)
    return findings
