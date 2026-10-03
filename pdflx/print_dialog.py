"""pdfLX print dialog: printer, pages and layout beside a live sheet preview."""
import os

from gi.repository import Adw, GLib, Gtk

from . import print_handler as printing
from .dialogs import ComboRow, PageRangeRow, SheetDialog, choose_save, pdf_filter
from .i18n import _, get_setting, set_setting
from .ops.common import OperationError

PAPERS = (('iso_a4', 'A4'), ('na_letter', 'Letter'), ('na_legal', 'Legal'),
          ('iso_a3', 'A3'), ('iso_a5', 'A5'))
DUPLEX = {'off': Gtk.PrintDuplex.SIMPLEX, 'long': Gtk.PrintDuplex.HORIZONTAL,
          'short': Gtk.PrintDuplex.VERTICAL}


def _default_paper():
    name = Gtk.PaperSize.get_default()
    return name if name in dict(PAPERS) else 'iso_a4'


class SheetPreview(Gtk.DrawingArea):
    """Draws the selected page on a sheet of paper as it will print."""

    def __init__(self, dialog):
        super().__init__(hexpand=True, vexpand=True, content_height=360)
        self.dialog = dialog
        self.cache = {}
        self.set_draw_func(self._draw)

    def invalidate(self):
        self.queue_draw()

    def _surface(self, index, gray):
        key = (index, gray)
        if key not in self.cache:
            if len(self.cache) > 24:
                self.cache.clear()
            self.cache[key] = printing.page_surface(self.dialog.doc.load_page(index), 1.4, gray)
        return self.cache[key]

    def _draw(self, _area, cr, width, height):
        dialog = self.dialog
        pages = dialog.selected_pages(quiet=True)
        if not pages:
            return
        index = pages[min(dialog.preview_index, len(pages) - 1)]
        page = dialog.doc.load_page(index)
        paper = Gtk.PaperSize.new(dialog.paper.key)
        sheet_w, sheet_h = paper.get_width(Gtk.Unit.POINTS), paper.get_height(Gtk.Unit.POINTS)
        margins = [paper.get_default_top_margin(Gtk.Unit.POINTS), paper.get_default_right_margin(Gtk.Unit.POINTS),
                   paper.get_default_bottom_margin(Gtk.Unit.POINTS), paper.get_default_left_margin(Gtk.Unit.POINTS)]
        orientation = dialog.orientation.key
        landscape = printing.is_landscape(page) if orientation == 'auto' else orientation == 'landscape'
        if landscape:
            sheet_w, sheet_h = sheet_h, sheet_w
            margins = margins[3:] + margins[:3]
        pad = 28
        zoom = min((width - pad * 2) / sheet_w, (height - pad * 2) / sheet_h)
        x = (width - sheet_w * zoom) / 2
        y = (height - sheet_h * zoom) / 2
        cr.save()
        cr.translate(x, y)
        cr.scale(zoom, zoom)
        # Soft shadow, then the sheet.
        for spread, alpha in ((6, .05), (3, .08), (1, .12)):
            cr.set_source_rgba(0, 0, 0, alpha)
            cr.rectangle(-spread / zoom, (2 - spread) / zoom, sheet_w + 2 * spread / zoom, sheet_h + 2 * spread / zoom)
            cr.fill()
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(0, 0, sheet_w, sheet_h)
        cr.fill()
        top, right, bottom, left = margins
        area_w, area_h = sheet_w - left - right, sheet_h - top - bottom
        rect = page.rect
        scale, dx, dy = printing.placement(rect.width, rect.height, area_w, area_h, dialog.scaling.key)
        surface = self._surface(index, dialog.color.key == 'gray')
        cr.translate(left, top)
        cr.rectangle(0, 0, area_w, area_h)
        cr.clip()
        cr.translate(dx, dy)
        cr.scale(rect.width * scale / surface.get_width(), rect.height * scale / surface.get_height())
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.restore()


class PrintDialog(SheetDialog):
    def __init__(self, window, doc):
        super().__init__(window, _('print_title'), _('print_action'), width=900, height=620)
        self.doc = doc
        self.preview_index = 0
        self.printers = []
        self.export_path = None
        saved = get_setting('print_options', {}) or {}

        split = Gtk.Box()
        split.append(self._build_preview())
        split.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        page = Adw.PreferencesPage(width_request=400)
        split.append(page)
        self.view.set_content(split)

        group = Adw.PreferencesGroup(title=_('print_group_printer'))
        self.printer_row = Adw.ComboRow(title=_('print_printer'), subtitle=_('print_searching'),
                                        model=Gtk.StringList())
        self.printer_row.connect('notify::selected', self._printer_changed)
        group.add(self.printer_row)
        self.file_row = Adw.ActionRow(title=_('print_output_file'), visible=False)
        choose = Gtk.Button(label=_('btn_choose'), valign=Gtk.Align.CENTER)
        choose.connect('clicked', self._choose_file)
        self.file_row.add_suffix(choose)
        self.file_row.set_activatable_widget(choose)
        group.add(self.file_row)
        self.copies = Adw.SpinRow.new_with_range(1, 999, 1)
        self.copies.set_title(_('print_copies'))
        group.add(self.copies)
        self.collate = Adw.SwitchRow(title=_('print_collate'), subtitle=_('print_collate_hint'),
                                     active=saved.get('collate', True), sensitive=False)
        group.add(self.collate)
        page.add(group)

        group = Adw.PreferencesGroup(title=_('print_group_pages'))
        self.range = PageRangeRow(_('range_pages'), doc.page_count, window.current_page_index)
        group.add(self.range)
        self.reverse = Adw.SwitchRow(title=_('print_reverse'))
        group.add(self.reverse)
        page.add(group)

        group = Adw.PreferencesGroup(title=_('print_group_layout'))
        self.paper = ComboRow(_('print_paper'), PAPERS, saved.get('paper', _default_paper()))
        self.orientation = ComboRow(_('print_orientation'), (('auto', _('print_orientation_auto')),
                                                             ('portrait', _('print_orientation_portrait')),
                                                             ('landscape', _('print_orientation_landscape'))),
                                    saved.get('orientation', 'auto'))
        self.scaling = ComboRow(_('print_scaling'), ((printing.SCALE_FIT, _('print_scale_fit')),
                                                     (printing.SCALE_SHRINK, _('print_scale_shrink')),
                                                     (printing.SCALE_ACTUAL, _('print_scale_actual'))),
                                saved.get('scaling', printing.SCALE_FIT))
        for row in (self.paper, self.orientation, self.scaling):
            group.add(row)
        page.add(group)

        group = Adw.PreferencesGroup(title=_('print_group_output'))
        self.color = ComboRow(_('print_color'), (('color', _('print_color_color')), ('gray', _('print_color_gray'))),
                              saved.get('color', 'color'))
        self.duplex = ComboRow(_('print_duplex'), (('off', _('print_duplex_off')), ('long', _('print_duplex_long')),
                                                   ('short', _('print_duplex_short'))), saved.get('duplex', 'off'))
        group.add(self.color)
        group.add(self.duplex)
        page.add(group)

        group = Adw.PreferencesGroup()
        system = Adw.ActionRow(title=_('print_system_dialog'), subtitle=_('print_system_dialog_hint'),
                               activatable=True)
        system.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
        system.connect('activated', self._system_dialog)
        group.add(system)
        page.add(group)

        self.copies.connect('notify::value', self._changed)
        self.range.connect('changed', self._changed)
        for row in (self.reverse,):
            row.connect('notify::active', self._changed)
        for row in (self.paper, self.orientation, self.scaling, self.color, self.duplex):
            row.connect('notify::selected', self._changed)
        self._enumerate(saved.get('printer'))
        self._changed()

    # -- preview --------------------------------------------------------------
    def _build_preview(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=440)
        box.add_css_class('pdflx-print-preview')
        self.sheet = SheetPreview(self)
        box.append(self.sheet)
        nav = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, margin_bottom=14)
        self.prev_button = Gtk.Button(icon_name='go-previous-symbolic', tooltip_text=_('print_prev_page'))
        self.next_button = Gtk.Button(icon_name='go-next-symbolic', tooltip_text=_('print_next_page'))
        for button, step in ((self.prev_button, -1), (self.next_button, 1)):
            button.add_css_class('circular')
            button.add_css_class('flat')
            button.connect('clicked', lambda _b, step=step: self._step(step))
        self.page_label = Gtk.Label(width_chars=14)
        self.page_label.add_css_class('numeric')
        nav.append(self.prev_button)
        nav.append(self.page_label)
        nav.append(self.next_button)
        box.append(nav)
        self.summary = Gtk.Label(margin_bottom=16)
        self.summary.add_css_class('caption')
        self.summary.add_css_class('dim-label')
        box.append(self.summary)
        return box

    def _step(self, delta):
        self.preview_index += delta
        self._changed()

    # -- state ----------------------------------------------------------------
    def selected_pages(self, quiet=False):
        try:
            pages = self.range.pages()
        except OperationError:
            if quiet:
                return []
            raise
        return list(reversed(pages)) if self.reverse.get_active() else pages

    def _changed(self, *_args):
        pages = self.selected_pages(quiet=True)
        count = len(pages)
        self.preview_index = max(0, min(self.preview_index, count - 1))
        copies = int(self.copies.get_value())
        self.collate.set_sensitive(copies > 1)
        self.prev_button.set_sensitive(self.preview_index > 0)
        self.next_button.set_sensitive(self.preview_index < count - 1)
        self.page_label.set_text(_('print_page_of', self.preview_index + 1, count) if count else '–')
        per_sheet = 2 if self.duplex.key != 'off' else 1
        sheets = -(-count // per_sheet) * copies
        self.summary.set_text(_('print_summary', count, sheets) if count else _('print_no_pages'))
        self.apply_button.set_sensitive(count > 0 and bool(self.printers))
        self.sheet.invalidate()

    def _enumerate(self, preferred):
        self._preferred = preferred
        self._enumerator = self._found  # keep the callback alive
        Gtk.enumerate_printers(self._enumerator, False)

    def _found(self, printer, *_args):
        if any(item.get_name() == printer.get_name() for item in self.printers):
            return False
        self.printers.append(printer)
        self.printer_row.get_model().append(printer.get_name())
        current = self.printers[self.printer_row.get_selected()] if self.printer_row.get_selected() < len(self.printers) else None
        better = (printer.get_name() == self._preferred or
                  (current is not None and current.get_name() != self._preferred and
                   (printer.is_default() or (current.is_virtual() and not printer.is_virtual()))))
        if len(self.printers) == 1 or better:
            self.printer_row.set_selected(len(self.printers) - 1)
        self._printer_changed()
        self._changed()
        return False

    @property
    def printer(self):
        index = self.printer_row.get_selected()
        return self.printers[index] if 0 <= index < len(self.printers) else None

    def _to_file(self):
        printer = self.printer
        return printer is not None and printer.is_virtual() and printer.accepts_pdf() and 'file' in printer.get_name().lower()

    def _printer_changed(self, *_args):
        printer = self.printer
        if printer is None:
            return
        details = [text for text in (printer.get_location(), printer.get_state_message()) if text]
        self.printer_row.set_subtitle(' · '.join(details) or (_('print_default') if printer.is_default() else ''))
        to_file = self._to_file()
        self.file_row.set_visible(to_file)
        self.duplex.set_sensitive(not to_file)
        if to_file and not self.export_path:
            folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS) or GLib.get_home_dir()
            stem = os.path.splitext(os.path.basename(getattr(self.window, 'current_file_path', None) or 'document.pdf'))[0]
            self.export_path = os.path.join(folder, f'{stem}-print.pdf')
        if to_file:
            self.file_row.set_subtitle(GLib.markup_escape_text(self.export_path))
        self.apply_button.set_label(_('print_save_pdf') if to_file else _('print_action'))

    def _choose_file(self, _button):
        def chosen(path):
            if not path.lower().endswith('.pdf'):
                path += '.pdf'
            self.export_path = path
            self._printer_changed()
        choose_save(self.window, _('print_output_file'), os.path.basename(self.export_path or 'print.pdf'),
                    [pdf_filter()], chosen)

    def _remember(self):
        set_setting('print_options', {
            'printer': self.printer.get_name() if self.printer else None, 'collate': self.collate.get_active(),
            'paper': self.paper.key, 'orientation': self.orientation.key, 'scaling': self.scaling.key,
            'color': self.color.key, 'duplex': self.duplex.key})

    # -- actions --------------------------------------------------------------
    def _on_apply_clicked(self, _button):
        try:
            pages = self.selected_pages()
        except OperationError as error:
            self.error(error)
            return
        job = dict(pages=pages, printer=self.printer, copies=int(self.copies.get_value()),
                   collate=self.collate.get_active(), orientation=self.orientation.key,
                   paper=Gtk.PaperSize.new(self.paper.key), scaling=self.scaling.key,
                   gray=self.color.key == 'gray', duplex=DUPLEX[self.duplex.key],
                   export_path=self.export_path if self._to_file() else None,
                   name=os.path.basename(getattr(self.window, 'current_file_path', None) or 'pdfLX'))
        self._remember()
        window = self.window
        self.force_close()
        ok, message = printing.run_print(window, self.doc, job)
        _report(window, ok, message)

    def _system_dialog(self, *_args):
        window, doc = self.window, self.doc
        self._remember()
        self.force_close()
        GLib.idle_add(lambda: (_report(window, *printing.print_document(window, doc)), False)[1])


def _report(window, ok, message):
    from .dialogs import message as show_message, toast
    if ok:
        if message:
            toast(window, message)
    elif message:
        show_message(window, _('print_error_title'), message)
    else:
        window.status_label.set_text(_('print_cancelled'))


def show_print_dialog(window, doc):
    dialog = PrintDialog(window, doc)
    dialog.present(window)
    return dialog
