"""Printing: page rendering, the print job runner and the system dialog fallback."""
import io
import traceback

import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk
import cairo
try:
    import pymupdf as fitz
except ImportError:
    import fitz
from .i18n import _

# Highest resolution sent to the printer; enough for text and scans without
# producing huge spool files.
MAX_PRINT_DPI = 300
SCALE_FIT, SCALE_SHRINK, SCALE_ACTUAL = 'fit', 'shrink', 'actual'


def page_surface(page, scale, gray=False):
    """Render ``page`` to a cairo surface at ``scale`` (1.0 = 72 dpi)."""
    colorspace = fitz.csGRAY if gray else fitz.csRGB
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), colorspace=colorspace, alpha=False)
    return cairo.ImageSurface.create_from_png(io.BytesIO(pix.tobytes('png')))


def placement(page_w, page_h, area_w, area_h, mode=SCALE_FIT):
    """Scale and offset that place a page inside a printable area."""
    fit = min(area_w / page_w, area_h / page_h)
    scale = fit if mode == SCALE_FIT else min(1.0, fit) if mode == SCALE_SHRINK else 1.0
    return scale, (area_w - page_w * scale) / 2, (area_h - page_h * scale) / 2


def is_landscape(page):
    rect = page.rect
    return rect.width > rect.height


def draw_page(cr, page, area_w, area_h, mode=SCALE_FIT, gray=False, dpi=MAX_PRINT_DPI):
    """Paint one PDF page centred in an ``area_w`` × ``area_h`` point area."""
    rect = page.rect
    if rect.width <= 0 or rect.height <= 0:
        return
    scale, dx, dy = placement(rect.width, rect.height, area_w, area_h, mode)
    render = scale * min(dpi, MAX_PRINT_DPI) / 72
    surface = page_surface(page, render, gray)
    cr.save()
    cr.rectangle(0, 0, area_w, area_h)
    cr.clip()
    cr.translate(dx, dy)
    cr.scale(rect.width * scale / surface.get_width(), rect.height * scale / surface.get_height())
    cr.set_source_surface(surface, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_GOOD)
    cr.paint()
    cr.restore()


def run_print(parent_window, doc, job):
    """Print without the system dialog using the choices in ``job``.

    ``job`` keys: pages (0-based list), printer (Gtk.Printer or None),
    export_path (for Print to File), copies, collate, orientation
    ('auto'|'portrait'|'landscape'), paper (Gtk.PaperSize), scaling, gray,
    duplex (Gtk.PrintDuplex). Returns (ok, message).
    """
    pages = list(job['pages'])
    if not doc or not pages:
        return False, _('print_no_doc')
    settings = Gtk.PrintSettings()
    settings.set_n_copies(job.get('copies', 1))
    settings.set_collate(job.get('collate', True))
    settings.set_use_color(not job.get('gray', False))
    settings.set_duplex(job.get('duplex', Gtk.PrintDuplex.SIMPLEX))
    printer = job.get('printer')
    if printer is not None:
        settings.set_printer(printer.get_name())
    setup = Gtk.PageSetup()
    setup.set_paper_size_and_default_margins(job['paper'])
    orientation = job.get('orientation', 'auto')
    if orientation == 'landscape':
        setup.set_orientation(Gtk.PageOrientation.LANDSCAPE)

    operation = Gtk.PrintOperation.new()
    operation.set_print_settings(settings)
    operation.set_default_page_setup(setup)
    operation.set_n_pages(len(pages))
    operation.set_unit(Gtk.Unit.POINTS)
    operation.set_use_full_page(False)
    operation.set_embed_page_setup(True)
    operation.set_job_name(job.get('name') or 'pdfLX')

    def request_setup(_operation, _context, page_nr, page_setup):
        if orientation == 'auto':
            landscape = is_landscape(doc.load_page(pages[page_nr]))
            page_setup.set_orientation(Gtk.PageOrientation.LANDSCAPE if landscape else Gtk.PageOrientation.PORTRAIT)

    def draw(_operation, context, page_nr):
        try:
            draw_page(context.get_cairo_context(), doc.load_page(pages[page_nr]), context.get_width(),
                      context.get_height(), job.get('scaling', SCALE_FIT), job.get('gray', False),
                      context.get_dpi_x() or MAX_PRINT_DPI)
        except Exception:  # one bad page must not abort the whole job
            traceback.print_exc()

    operation.connect('request-page-setup', request_setup)
    operation.connect('draw-page', draw)
    action = Gtk.PrintOperationAction.PRINT
    if job.get('export_path'):
        operation.set_export_filename(job['export_path'])
        action = Gtk.PrintOperationAction.EXPORT
    try:
        result = operation.run(action, parent_window)
    except Exception as error:
        traceback.print_exc()
        return False, _('print_start_failed').format(error)
    if result == Gtk.PrintOperationResult.ERROR:
        return False, _('print_error_occurred')
    if result == Gtk.PrintOperationResult.CANCEL:
        return False, None
    parent_window._print_settings = settings
    parent_window._page_setup = setup
    return True, _('print_success')


def print_document(parent_window, doc):
    """Run the system print dialog for a PDF document (advanced printer options)."""
    if not doc or doc.page_count == 0:
        return False, _("print_no_doc")

    print_op = Gtk.PrintOperation.new()
    if getattr(parent_window, '_print_settings', None):
        print_op.set_print_settings(parent_window._print_settings)
    if getattr(parent_window, '_page_setup', None):
        print_op.set_default_page_setup(parent_window._page_setup)

    print_op.set_n_pages(doc.page_count)
    print_op.set_unit(Gtk.Unit.POINTS)
    print_op.set_use_full_page(False)
    print_op.set_embed_page_setup(True)
    print_op.set_job_name("pdfLX Print")
    if hasattr(parent_window, 'current_page_index'):
        print_op.set_current_page(parent_window.current_page_index)

    def on_draw_page(operation, print_context, page_nr):
        try:
            draw_page(print_context.get_cairo_context(), doc.load_page(page_nr), print_context.get_width(),
                      print_context.get_height(), dpi=print_context.get_dpi_x() or MAX_PRINT_DPI)
        except Exception as e:
            print(f"ERROR in print draw-page for page {page_nr}: {e}")
            traceback.print_exc()

    def on_done(operation, result):
        if result == Gtk.PrintOperationResult.APPLY:
            parent_window._print_settings = operation.get_print_settings()
            parent_window._page_setup = operation.get_default_page_setup()

    print_op.connect("draw-page", on_draw_page)
    print_op.connect("done", on_done)

    try:
        result = print_op.run(Gtk.PrintOperationAction.PRINT_DIALOG, parent_window)
        if result == Gtk.PrintOperationResult.ERROR:
            return False, _("print_error_occurred")
        elif result == Gtk.PrintOperationResult.CANCEL:
            return False, None
        elif result == Gtk.PrintOperationResult.APPLY:
            return True, _("print_success")
        elif result == Gtk.PrintOperationResult.IN_PROGRESS:
            return True, _("print_in_progress")
        return True, None
    except Exception as e:
        print(f"ERROR starting print operation: {e}")
        traceback.print_exc()
        return False, _("print_start_failed").format(e)
