"""'Format & Calculation' tab for the field create/edit dialogs.

Choices are stored as the standard Acrobat AF* scripts (see ops.formbehaviour),
so the behaviour also works in other PDF readers.
"""
from gi.repository import Gtk

from .i18n import _
from .ops import formbehaviour as behaviour_ops

FORMAT_LABELS = ('none', 'number', 'percent', 'date', 'time', 'special')
CALC_KEYS = ('none',) + tuple(key for key, _label in behaviour_ops.CALCULATIONS) + ('custom',)


def add_behaviour_tab(dialog, doc, field=None):
    """Append the tab to ``dialog.form_notebook``. Returns (read, page_widget).

    ``read()`` returns a behaviour dict for ops.formbehaviour.apply().
    """
    current = behaviour_ops.parse(doc, field['xref']) if field else {
        'format': 'none', 'calculate': 'none', 'minimum': None, 'maximum': None, 'sources': [], 'custom': ''}
    grid = dialog.new_grid()
    previous_grid, previous_row = dialog.grid, dialog.row
    dialog.grid, dialog.row = grid, 0
    scroll = grid
    dialog.form_notebook.append_page(grid, Gtk.Label(label=_('behaviour_tab')))

    fmt = dialog.dropdown(_('behaviour_format'), [_(f'behaviour_format_{key}') for key in FORMAT_LABELS])
    fmt.set_selected(FORMAT_LABELS.index(current.get('format', 'none')) if current.get('format') in FORMAT_LABELS else 0)
    decimals = dialog.spin(_('behaviour_decimals'), current.get('decimals', 2), 0, 10)
    separator = dialog.dropdown(_('behaviour_separator'), [label for _key, label in behaviour_ops.SEPARATORS])
    separator.set_selected(int(current.get('separator', 0)))
    negative = dialog.dropdown(_('behaviour_negative'), [label for _key, label in behaviour_ops.NEGATIVE_STYLES])
    negative.set_selected(int(current.get('negative', 0)))
    currency = dialog.entry(_('behaviour_currency'), current.get('currency', ''))
    currency.set_placeholder_text('€ , $ , kr')
    currency_before = dialog.check(_('behaviour_currency_before'), current.get('currency_before', True))
    dates = list(behaviour_ops.DATE_FORMATS)
    if current.get('date_format') and current['date_format'] not in dates:
        dates.append(current['date_format'])
    date_format = dialog.dropdown(_('behaviour_date_format'), dates)
    date_format.set_selected(dates.index(current.get('date_format', dates[0])))
    time_format = dialog.dropdown(_('behaviour_time_format'), [label for _key, label in behaviour_ops.TIME_FORMATS])
    time_format.set_selected(int(current.get('time_format', 0)))
    special = dialog.dropdown(_('behaviour_special'), [_(f'behaviour_special_{key}') for key, _l in
                                                       behaviour_ops.SPECIAL_FORMATS])
    special.set_selected(int(current.get('special', 0)))

    use_min = dialog.check(_('behaviour_minimum'), current.get('minimum') is not None)
    minimum = dialog.spin(_('behaviour_minimum_value'), current.get('minimum') or 0, -1e12, 1e12, 1)
    minimum.set_digits(2)
    use_max = dialog.check(_('behaviour_maximum'), current.get('maximum') is not None)
    maximum = dialog.spin(_('behaviour_maximum_value'), current.get('maximum') or 0, -1e12, 1e12, 1)
    maximum.set_digits(2)

    calculate = dialog.dropdown(_('behaviour_calculate'), [_(f'behaviour_calc_{key}') for key in CALC_KEYS])
    calculate.set_selected(CALC_KEYS.index(current.get('calculate', 'none')) if current.get('calculate') in CALC_KEYS else 0)
    sources_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    sources_scroll = Gtk.ScrolledWindow(min_content_height=110, max_content_height=160, propagate_natural_height=True)
    sources_scroll.set_child(sources_box)
    sources_label = Gtk.Label(label=_('behaviour_sources'), xalign=0)
    sources_holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=10, margin_bottom=12,
                             margin_start=12, margin_end=12)
    sources_holder.append(sources_label)
    sources_holder.append(sources_scroll)
    grid.add(Gtk.ListBoxRow(activatable=False, child=sources_holder), sources_scroll, dialog.row)
    dialog.row += 1
    checks = []
    own = field['name'] if field else None
    for _xref, name in behaviour_ops.text_fields(doc):
        if name == own:
            continue
        check = Gtk.CheckButton(label=name, active=name in current.get('sources', []))
        sources_box.append(check)
        checks.append((name, check))
    if not checks:
        sources_box.append(Gtk.Label(label=_('behaviour_no_sources'), xalign=0))
    custom = dialog.multiline(_('behaviour_custom'), current.get('custom', ''))
    custom_widget = grid.get_child_at(1, dialog.row - 1)
    custom_label = grid.get_child_at(0, dialog.row - 1)
    note = Gtk.Label(label=_('behaviour_note'), wrap=True, xalign=0)
    note.add_css_class('dim-label')
    grid.attach(note, 0, dialog.row, 2, 1)
    dialog.row += 1

    def row_widgets(widget):
        label = None
        for child_row in range(dialog.row):
            if grid.get_child_at(1, child_row) is widget:
                label = grid.get_child_at(0, child_row)
        return [widget] + ([label] if label else [])

    groups = {
        'number': [decimals, separator, negative, currency, currency_before],
        'percent': [decimals, separator],
        'date': [date_format], 'time': [time_format], 'special': [special],
    }
    all_format = {decimals, separator, negative, currency, currency_before, date_format, time_format, special}

    def update(*_args):
        kind = FORMAT_LABELS[fmt.get_selected()]
        visible = set(groups.get(kind, []))
        for widget in all_format:
            for item in row_widgets(widget):
                item.set_visible(widget in visible)
        for item in row_widgets(minimum):
            item.set_sensitive(use_min.get_active())
        for item in row_widgets(maximum):
            item.set_sensitive(use_max.get_active())
        calc = CALC_KEYS[calculate.get_selected()]
        for item in (sources_label, sources_scroll):
            item.set_visible(calc not in ('none', 'custom'))
        for item in (custom_widget, custom_label):
            item.set_visible(calc == 'custom')
    for widget, signal in ((fmt, 'notify::selected'), (calculate, 'notify::selected'), (use_min, 'toggled'),
                           (use_max, 'toggled')):
        widget.connect(signal, update)
    update()
    dialog.grid, dialog.row = previous_grid, previous_row

    def read():
        return {
            'format': FORMAT_LABELS[fmt.get_selected()], 'decimals': decimals.get_value_as_int(),
            'separator': separator.get_selected(), 'negative': negative.get_selected(),
            'currency': currency.get_text(), 'currency_before': currency_before.get_active(),
            'date_format': dates[date_format.get_selected()], 'time_format': time_format.get_selected(),
            'special': special.get_selected(),
            'minimum': minimum.get_value() if use_min.get_active() else None,
            'maximum': maximum.get_value() if use_max.get_active() else None,
            'calculate': CALC_KEYS[calculate.get_selected()],
            'sources': [name for name, check in checks if check.get_active()],
            'custom': custom.get_text(custom.get_start_iter(), custom.get_end_iter(), True),
        }
    return read, scroll
