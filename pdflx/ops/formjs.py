"""Run a form's own JavaScript with MuPDF's built-in engine (mujs).

The engine is sandboxed: scripts can read and change form fields but have no
file-system or network access. pdfLX enables it for a document only when a
field value is committed (never on opening), so keystroke, validation,
calculation, and format scripts behave as they would in other PDF readers.
"""
import pymupdf as fitz
import pymupdf.mupdf as mupdf

from .common import OperationError

SCRIPT_EVENTS = ('K', 'F', 'V', 'C')


class ValidationRejected(OperationError):
    """A field's keystroke or validation script refused the value."""
    # Undoable commands roll back and re-raise instead of showing a generic error.
    propagate = True


def available():
    return all(hasattr(mupdf, name) for name in ('pdf_enable_js', 'pdf_set_field_value', 'pdf_calculate_form',
                                                 'pdf_update_page', 'pdf_annot_event_up'))


def _pdf(doc):
    return mupdf.pdf_specifics(doc.this)


def has_scripts(doc):
    """True when any field carries keystroke/format/validate/calculate scripts or /CO exists."""
    if not doc.is_form_pdf:
        return False
    if doc.xref_get_key(doc.pdf_catalog(), 'AcroForm/CO')[0] == 'array':
        return True
    for page in doc:
        for widget in page.widgets() or ():
            for event in SCRIPT_EVENTS:
                if doc.xref_get_key(widget.xref, f'AA/{event}')[0] != 'null':
                    return True
    return False


def enable(doc):
    """Create the JavaScript context for ``doc`` (idempotent)."""
    pdf = _pdf(doc)
    if not mupdf.pdf_js_supported(pdf):
        mupdf.pdf_enable_js(pdf)
    doc.editor_js_enabled = True
    return pdf


def disable(doc):
    pdf = _pdf(doc)
    if mupdf.pdf_js_supported(pdf):
        mupdf.pdf_disable_js(pdf)
    doc.editor_js_enabled = False


def _widget(doc, page_number, xref):
    page = doc[page_number]
    for widget in page.widgets() or ():
        if widget.xref == xref:
            return page, widget
    raise OperationError('The form field no longer exists.')


def set_value(doc, page_number, xref, value):
    """Commit a value through the field's keystroke and validation scripts."""
    from .formbehaviour import check_value, describe_rejection, parse
    pdf = enable(doc)
    page, widget = _widget(doc, page_number, xref)
    if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
        try:
            value = check_value(parse(doc, xref), value)
        except OperationError as error:
            raise ValidationRejected(f'{widget.field_name}: {error}')
    obj = mupdf.pdf_annot_obj(widget._annot)
    if not mupdf.pdf_set_field_value(pdf, obj, str(value), 0):
        raise ValidationRejected(describe_rejection(doc, xref, widget.field_name, value))
    del page
    return value


def recalculate(doc):
    """Run calculation scripts in /CO order, then regenerate formatted appearances."""
    pdf = enable(doc)
    mupdf.pdf_calculate_form(pdf)
    refresh_appearances(doc)


def refresh_appearances(doc):
    """Let MuPDF rebuild changed widget appearances (runs format scripts)."""
    for page in doc:
        if page.first_widget is None:
            continue
        mupdf.pdf_update_page(mupdf.pdf_page_from_fz_page(page.this))
    # MuPDF does not highlight list selections or mask passwords; redo those.
    from ..form_appearance import refresh_appearance
    for page in doc:
        for widget in page.widgets() or ():
            if widget.field_type == fitz.PDF_WIDGET_TYPE_LISTBOX or (
                    widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT and widget.field_flags & fitz.PDF_TX_FIELD_IS_PASSWORD):
                refresh_appearance(doc, widget.xref)
    doc._reset_page_refs()


def run_button(doc, page_number, xref):
    """Run a button's mouse-up action (JavaScript) and recalculate dependent fields."""
    enable(doc)
    page, widget = _widget(doc, page_number, xref)
    mupdf.pdf_annot_event_up(widget._annot)
    del page
    recalculate(doc)
