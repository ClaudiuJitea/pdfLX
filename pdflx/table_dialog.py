"""Table creation with an editable cell grid."""
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk

from .i18n import _
from .ui_components import show_error_dialog, show_open_file_dialog
from . import table_data


class TableDialog(Adw.Window):
    def __init__(self, parent, on_insert, cells=None):
        super().__init__(transient_for=parent, modal=True, title=_("table_add"),
                         default_width=720, default_height=560)
        self.on_insert = on_insert
        self.entries = []
        self._loading = False
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(layout)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=_("table_add")))
        insert = Gtk.Button(label=_("table_insert"))
        insert.add_css_class('suggested-action')
        insert.connect('clicked', self._accept)
        header.pack_end(insert)
        layout.append(header)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                       margin_start=24, margin_end=24, margin_top=20, margin_bottom=24,
                       vexpand=True)
        layout.append(body)
        imports = Gtk.Box(spacing=24, hexpand=True)
        import_actions = Gtk.Box(spacing=12, valign=Gtk.Align.CENTER)
        paste = Gtk.Button(label=_("table_paste"), tooltip_text=_("table_paste_tip"))
        paste.connect('clicked', self._paste)
        paste.set_size_request(-1, 36)
        import_actions.append(paste)
        load = Gtk.Button(label=_("table_import_csv"))
        load.connect('clicked', self._import_csv)
        load.set_size_request(-1, 36)
        import_actions.append(load)
        imports.append(import_actions)
        imports.append(Gtk.Box(hexpand=True))
        separator_group = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER)
        separator_group.append(Gtk.Label(label=_("table_separator"), valign=Gtk.Align.CENTER))
        self.separator = Gtk.DropDown.new_from_strings([_("table_auto"), ',', ';', _("table_tab"), '|'])
        self.separator.set_size_request(150, 36)
        self.separator.set_valign(Gtk.Align.CENTER)
        separator_group.append(self.separator)
        imports.append(separator_group)
        body.append(imports)
        self.summary = Gtk.Label(xalign=0, wrap=True)
        self.summary.add_css_class('dim-label')
        body.append(self.summary)
        options = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                              max_children_per_line=5, min_children_per_line=2,
                              column_spacing=12, row_spacing=12)
        body.append(options)

        def spin(label, lower, upper, value):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.append(Gtk.Label(label=_(label), xalign=0))
            control = Gtk.SpinButton.new_with_range(lower, upper, 1)
            control.set_value(value)
            box.append(control)
            options.append(box)
            return control

        self.rows = spin('table_rows', 1, table_data.MAX_ROWS, 4)
        self.columns = spin('table_columns', 1, table_data.MAX_COLUMNS, 3)
        self.width = spin('table_width', 20, 95, 80)
        self.font_size = spin('table_font', 6, 24, 10)
        self.row_height = spin('table_height', 10, 100, 32)
        self.header_row = Gtk.CheckButton(label=_("table_header"), active=True)
        body.append(self.header_row)
        self.fit_columns = Gtk.CheckButton(label=_("table_fit_columns"), active=True)
        body.append(self.fit_columns)
        hint = Gtk.Label(label=_("table_hint"), wrap=True, xalign=0)
        hint.add_css_class('dim-label')
        body.append(hint)
        self.grid = Gtk.Grid(column_spacing=6, row_spacing=6,
                             margin_start=8, margin_end=8, margin_top=8, margin_bottom=8)
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True,
                                   min_content_height=200)
        scroll.set_child(self.grid)
        frame = Gtk.Frame(child=scroll)
        frame.set_vexpand(True)
        body.append(frame)
        self.rows.connect('value-changed', self._rebuild)
        self.columns.connect('value-changed', self._rebuild)
        self._rebuild()
        if cells is not None:
            self._load_cells(cells, _("table_clipboard"))

    def _rebuild(self, *args):
        if self._loading:
            return
        if self.rows.get_value_as_int()*self.columns.get_value_as_int() > table_data.MAX_CELLS:
            self._loading = True
            self.rows.set_value(min(self.rows.get_value_as_int(), table_data.MAX_CELLS//self.columns.get_value_as_int()))
            self._loading = False
        old = [[entry.get_text() for entry in row] for row in self.entries]
        child = self.grid.get_first_child()
        while child:
            sibling = child.get_next_sibling()
            self.grid.remove(child)
            child = sibling
        self.entries = []
        for row in range(self.rows.get_value_as_int()):
            self.grid.attach(Gtk.Label(label=str(row+1)), 0, row+1, 1, 1)
            entries = []
            for column in range(self.columns.get_value_as_int()):
                if row == 0:
                    self.grid.attach(Gtk.Label(label=str(column+1)), column+1, 0, 1, 1)
                value = old[row][column] if row < len(old) and column < len(old[row]) else ''
                entry = Gtk.Entry(text=value, width_chars=14, hexpand=True)
                entry.set_tooltip_text(_("table_cell", row+1, column+1))
                entry.update_property([Gtk.AccessibleProperty.LABEL],
                                      [_("table_cell", row+1, column+1)])
                self.grid.attach(entry, column+1, row+1, 1, 1)
                entries.append(entry)
            self.entries.append(entries)

    def _delimiter(self):
        return (None, ',', ';', '\t', '|')[self.separator.get_selected()]

    def _load_cells(self, cells, source):
        cells = table_data.validate_cells(cells)
        self._loading = True
        try:
            self.rows.set_value(len(cells))
            self.columns.set_value(len(cells[0]))
        finally:
            self._loading = False
        self._rebuild()
        for entries, values in zip(self.entries, cells):
            for entry, value in zip(entries, values):
                entry.set_text(value)
        self.summary.set_text(_("table_import_summary").format(source, len(cells), len(cells[0])))

    def _paste(self, button):
        delimiter = self._delimiter()
        def received(clipboard, task):
            try:
                text = clipboard.read_text_finish(task)
                self._load_cells(table_data.parse_table(text or '', delimiter), _("table_clipboard"))
            except Exception as error:
                show_error_dialog(self, str(error), _("table_paste"))
        self.get_clipboard().read_text_async(None, received)

    def _import_csv(self, button):
        from pathlib import Path
        csv_filter = Gtk.FileFilter(name=_("table_import_csv"))
        for pattern in ('*.csv', '*.CSV', '*.tsv', '*.TSV', '*.txt'):
            csv_filter.add_pattern(pattern)
        delimiter = self._delimiter()
        def chosen(file):
            if not file:
                return
            try:
                path = file.get_path()
                self._load_cells(table_data.read_csv(path, delimiter), Path(path).name)
            except Exception as error:
                show_error_dialog(self, str(error), _("table_import_csv"))
        show_open_file_dialog(self, _("table_import_csv"), filters=[csv_filter], callback=chosen)

    def _accept(self, button):
        try:
            self.on_insert([[entry.get_text() for entry in row] for row in self.entries],
                           width_percent=self.width.get_value(),
                           font_size=self.font_size.get_value(),
                           header=self.header_row.get_active(),
                           row_height=self.row_height.get_value(),
                           fit_columns=self.fit_columns.get_active())
            self.close()
        except Exception as error:
            show_error_dialog(self, str(error), _("table_add"))
