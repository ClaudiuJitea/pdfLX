"""Document-level dialogs: information, viewer settings, bookmarks, links, attachments,
layers, comparison, advanced search, text style, and region snapshots."""
import os

import pymupdf as fitz
from gi.repository import Adw, Gdk, GLib, Gtk

from ..i18n import _
from ..dialogs import (OperationDialog, choose_files, file_filter, pdf_filter, toast,
                       message)
from ..ops import structure, text as text_ops
from ..ops.common import OperationError, format_page_ranges


def _boxed_list():
    box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    box.add_css_class('boxed-list')
    return box


def _clear(container):
    while child := container.get_first_child():
        container.remove(child)


def _flat_icon(icon, tooltip, callback, sensitive=True):
    button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER, sensitive=sensitive)
    button.add_css_class('flat')
    button.connect('clicked', lambda _b: callback())
    return button


# ---------------------------------------------------------------- information

def document_info(c):
    w = c.window
    doc = w.doc
    info = structure.properties(doc, w.current_file_path)
    dialog = OperationDialog(w, _('menu_document_info'), width=640, height=640)
    stack = Adw.ViewStack()
    switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE, margin_bottom=6)
    dialog.page.get_parent().prepend(switcher)
    dialog.page.set_visible(False)
    dialog.page.get_parent().append(stack)
    stack.set_vexpand(True)

    overview = Adw.PreferencesPage()
    general = Adw.PreferencesGroup(title=_('info_general'))
    meta = doc.metadata or {}
    for key, value in ((_('metadata_title'), meta.get('title')), (_('metadata_author'), meta.get('author')),
                       (_('metadata_subject'), meta.get('subject')), (_('metadata_keywords'), meta.get('keywords')),
                       (_('info_file'), w.current_file_path or _('untitled_document')),
                       (_('info_file_size'), _human(info['file_size']) if info['file_size'] else '—'),
                       (_('info_pages'), info['pages']),
                       (_('info_page_sizes'), ', '.join(f'{wd}×{ht} pt ({n})' for (wd, ht), n in info['page_sizes'].items())),
                       (_('info_pdf_version'), info['format']), (_('metadata_creator'), info['creator']),
                       (_('info_producer'), info['producer']), (_('info_created'), info['created']),
                       (_('info_modified'), info['modified'])):
        _info_row(general, key, value)
    overview.add(general)
    features = Adw.PreferencesGroup(title=_('info_features'))
    for key, value in ((_('info_encryption'), info['encryption'] or _('security_none')),
                       (_('info_signed_fields'), ', '.join(info['signed_fields']) or _('security_none')),
                       (_('info_form'), _yes(info['form'])), (_('info_tagged'), _yes(info['tagged'])),
                       (_('info_attachments'), info['attachments']), (_('info_layers'), info['layers']),
                       (_('info_javascript'), _yes(info['javascript'])), (_('info_xmp'), _yes(info['xmp'])),
                       (_('info_fast_web'), _yes(info['fast_web_view'])), (_('info_repaired'), _yes(info['repaired']))):
        _info_row(features, key, value)
    overview.add(features)
    stack.add_titled_with_icon(overview, 'overview', _('info_overview'), 'document-properties-symbolic')

    fonts_page = Adw.PreferencesPage()
    fonts_group = Adw.PreferencesGroup(title=_('info_fonts'), description=_('info_fonts_hint'))
    for font in structure.fonts(doc):
        row = Adw.ActionRow(title=GLib.markup_escape_text(font['name'] or '?'),
                            subtitle=GLib.markup_escape_text(
                                f"{font['type']} · {_('info_embedded') if font['embedded'] else _('info_not_embedded')}"
                                f"{' · ' + _('info_subset') if font['subset'] else ''} · "
                                f"{_('info_pages')}: {format_page_ranges(font['pages'])}"))
        if font['embedded'] and c.can_copy():
            row.add_suffix(_flat_icon('document-save-symbolic', _('info_extract_font'),
                                      lambda xref=font['xref']: _save_font(c, xref)))
        fonts_group.add(row)
    fonts_page.add(fonts_group)
    stack.add_titled_with_icon(fonts_page, 'fonts', _('info_fonts'), 'font-x-generic-symbolic')

    stats_page = Adw.PreferencesPage()
    stats_group = Adw.PreferencesGroup(title=_('info_statistics'))
    compute = Gtk.Button(label=_('info_compute_statistics'), halign=Gtk.Align.START)
    stats_group.add(compute)
    stats_page.add(stats_group)
    stack.add_titled_with_icon(stats_page, 'stats', _('info_statistics'), 'view-list-bullet-symbolic')

    def show_stats(_button):
        compute.set_sensitive(False)
        data = doc.tobytes(garbage=0, encryption=fitz.PDF_ENCRYPT_KEEP)
        password = getattr(doc, 'editor_password', '')

        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                if copy.needs_pass:
                    copy.authenticate(password)
                return text_ops.statistics(copy, progress, cancel)

        def done(stats):
            compute.set_visible(False)
            for key in ('pages', 'words', 'characters', 'images', 'links', 'annotations', 'form_fields'):
                _info_row(stats_group, _(f'stat_{key}'), f'{stats[key]:,}')
            _info_row(stats_group, _('info_fonts'), len(stats['fonts']))
            return False
        dialog.run(work, done)
    compute.connect('clicked', show_stats)
    dialog.show()


def _human(count):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if count < 1024 or unit == 'GB':
            return f'{count:.0f} {unit}' if unit == 'B' else f'{count:.1f} {unit}'
        count /= 1024


def _info_row(group, title, value):
    row = Adw.ActionRow(title=title, subtitle=GLib.markup_escape_text(str(value if value not in (None, '') else '—')))
    row.add_css_class('property')
    row.set_subtitle_selectable(True)
    group.add(row)


def _yes(value):
    return _('yes') if value else _('no')


def _save_font(c, xref):
    try:
        name, data = structure.extract_font(c.window.doc, xref)
    except OperationError as error:
        message(c.window, _('err_title'), str(error))
        return
    c.save_data(data, name)


def viewer_settings(c):
    w = c.window
    doc = w.doc
    current = structure.viewer_settings(doc)
    dialog = OperationDialog(w, _('menu_viewer_settings'), _('btn_save'))
    opening = dialog.group(_('viewer_opening'), _('viewer_hint'))
    mode = dialog.combo(opening, _('viewer_page_mode'), [(k, _(f'pagemode_{k}')) for k, _l in structure.PAGE_MODES],
                        current['page_mode'])
    layout = dialog.combo(opening, _('viewer_page_layout'),
                          [(k, _(f'pagelayout_{k}')) for k, _l in structure.PAGE_LAYOUTS], current['page_layout'])
    window_group = dialog.group(_('viewer_window'))
    prefs = {key: dialog.switch(window_group, _(f'viewerpref_{key}'), current['preferences'].get(key, False))
             for key in ('DisplayDocTitle', 'FitWindow', 'CenterWindow', 'HideToolbar', 'HideMenubar')}
    other = dialog.group(_('viewer_document'))
    language = dialog.entry(other, _('viewer_language'), current['language'])
    appearances = dialog.switch(other, _('viewer_need_appearances'), current['need_appearances'],
                                _('viewer_need_appearances_hint'))
    appearances.set_sensitive(doc.is_form_pdf)

    def apply(_dialog):
        values = dict(page_mode=mode.key, page_layout=layout.key, language=language.get_text(),
                      preferences={key: row.get_active() for key, row in prefs.items()},
                      need_appearances=appearances.get_active() if doc.is_form_pdf else None)
        c.mutate(lambda: structure.set_viewer_settings(doc, **values))
        toast(w, _('status_viewer_settings_saved'))
    dialog.on_apply = apply
    dialog.show()


# ---------------------------------------------------------------- bookmarks

def generate_bookmarks(c):
    w = c.window
    doc = w.doc
    dialog = OperationDialog(w, _('menu_generate_bookmarks'), _('bookmarks_replace'), width=620, height=620)
    group = dialog.group(_('autotoc_settings'), _('autotoc_hint'))
    levels = dialog.spin(group, _('autotoc_levels'), 2, 1, 4)
    ratio = dialog.spin(group, _('autotoc_ratio'), 1.15, 1.05, 3, 0.05, 2)
    mode = dialog.combo(group, _('autotoc_mode'), [('replace', _('autotoc_replace')), ('append', _('autotoc_append'))])
    preview_group = dialog.group(_('autotoc_preview'))
    entries = _boxed_list()
    preview_group.add(entries)
    state = {'toc': []}

    def refresh(*_args):
        try:
            toc = text_ops.heading_candidates(doc, ratio.get_value(), levels.get_value_as_int())
        except OperationError as error:
            dialog.error(error)
            toc = []
        state['toc'] = toc
        _clear(entries)
        if not toc:
            entries.append(Gtk.Label(label=_('autotoc_none'), margin_top=10, margin_bottom=10))
        for level, title, page in toc[:300]:
            entries.append(Adw.ActionRow(title=GLib.markup_escape_text(title),
                                         subtitle=_('form_page_label', page), margin_start=(level - 1) * 18))
    levels.connect('notify::value', refresh)
    ratio.connect('notify::value', refresh)
    refresh()

    def apply(_dialog):
        toc = state['toc']
        if not toc:
            raise OperationError(_('autotoc_none'))
        final = (doc.get_toc(simple=False) + toc) if mode.key == 'append' else toc
        c.mutate(lambda: doc.set_toc(final))
        toast(w, _('status_bookmarks_generated', len(toc)))
    dialog.on_apply = apply
    dialog.show()


# ---------------------------------------------------------------- links

def links(c):
    w = c.window
    doc = w.doc
    editable = c.editable()
    dialog = OperationDialog(w, _('menu_links'), width=660, height=640)
    add_group = dialog.group(_('links_add'), _('links_add_hint') if editable else _('links_view_only'))
    kind = dialog.combo(add_group, _('links_type'), [(k, _(f'link_{k}')) for k, _v, _l in structure.LINK_KINDS], 'uri')
    target = dialog.entry(add_group, _('links_target'), '')
    page_target = dialog.spin(add_group, _('links_target_page'), 1, 1, doc.page_count)
    named = dialog.combo(add_group, _('links_named_action'), [(n, _(f'named_{n}')) for n in structure.NAMED_ACTIONS])
    file_row = dialog.file(add_group, _('links_target_file'))
    add_button = Gtk.Button(label=_('links_add_button'), halign=Gtk.Align.START, margin_top=6)
    add_button.add_css_class('suggested-action')
    add_group.add(add_button)
    detect = Gtk.Button(label=_('links_detect_urls'), halign=Gtk.Align.START, margin_top=6)
    add_group.add(detect)
    for widget in (kind, target, page_target, named, file_row, add_button, detect):
        widget.set_sensitive(editable)

    list_group = dialog.group(_('links_existing'))
    scope = dialog.combo(list_group, _('links_scope'), [('page', _('links_scope_page')), ('all', _('links_scope_all'))])
    rows = _boxed_list()
    list_group.add(rows)

    def update_kind(*_args):
        key = kind.key
        target.set_visible(key == 'uri')
        page_target.set_visible(key in ('goto', 'gotor'))
        named.set_visible(key == 'named')
        file_row.set_visible(key in ('gotor', 'launch'))
    kind.connect('notify::selected', update_kind)
    update_kind()

    def refresh(*_args):
        _clear(rows)
        pages = [w.current_page_index] if scope.key == 'page' else None
        items = structure.links(doc, pages)
        if not items:
            rows.append(Gtk.Label(label=_('links_none'), margin_top=10, margin_bottom=10))
        for item in items[:500]:
            row = Adw.ActionRow(title=GLib.markup_escape_text(f"{_(f'link_{item['kind']}') if item['kind'] != 'other' else '?'}: {item['target']}"),
                                subtitle=_('form_page_label', item['page'] + 1))
            row.add_suffix(_flat_icon('go-jump-symbolic', _('links_go'), lambda page=item['page']: w._load_page(page)))
            row.add_suffix(_flat_icon('user-trash-symbolic', _('links_delete'),
                                      lambda item=item: remove(item), editable and bool(item['xref'])))
            rows.append(row)
    scope.connect('notify::selected', refresh)

    def remove(item):
        try:
            c.mutate(lambda: structure.delete_link(doc, item['page'], item['xref']), page_num=item['page'])
            refresh()
        except OperationError as error:
            dialog.error(error)

    def add(_button):
        rect = c.selection_rect()
        try:
            if not rect:
                raise OperationError(_('links_need_selection'))
            number = w.current_page_index
            values = dict(kind=kind.key, target=named.key if kind.key == 'named' else target.get_text(),
                          page_target=page_target.get_value_as_int() - 1, file=file_row.path or '')
            c.mutate(lambda: structure.add_link(doc, number, rect, **values), page_num=number)
            toast(w, _('status_link_added'))
            refresh()
        except OperationError as error:
            dialog.error(error)

    def detect_urls(_button):
        result = []
        try:
            c.mutate(lambda: result.append(structure.detect_urls(doc)))
            toast(w, _('status_links_detected', result[0]))
            refresh()
        except OperationError as error:
            dialog.error(error)
    add_button.connect('clicked', add)
    detect.connect('clicked', detect_urls)
    refresh()
    dialog.show()


# ---------------------------------------------------------------- attachments

def attachments(c):
    w = c.window
    doc = w.doc
    editable = c.editable()
    dialog = OperationDialog(w, _('menu_attachments'), width=600, height=520)
    group = dialog.group(_('attachments_title'), _('attachments_hint'))
    rows = _boxed_list()
    group.add(rows)
    add = Gtk.Button(label=_('attachments_add'), halign=Gtk.Align.START, margin_top=8, sensitive=editable)
    group.add(add)

    def refresh():
        _clear(rows)
        items = structure.attachments(doc)
        if not items:
            rows.append(Gtk.Label(label=_('attachments_none'), margin_top=10, margin_bottom=10))
        for item in items:
            row = Adw.ActionRow(title=GLib.markup_escape_text(item['filename']),
                                subtitle=GLib.markup_escape_text(f"{_human(item['size'] or 0)} · {item['description']}"))
            row.add_suffix(_flat_icon('document-save-symbolic', _('attachments_save'),
                                      lambda name=item['name'], filename=item['filename']: save(name, filename),
                                      c.can_copy()))
            row.add_suffix(_flat_icon('user-trash-symbolic', _('attachments_remove'),
                                      lambda name=item['name']: remove(name), editable))
            rows.append(row)

    def save(name, filename):
        c.save_data(structure.attachment_bytes(doc, name), filename)

    def remove(name):
        try:
            c.mutate(lambda: structure.remove_attachment(doc, name))
            refresh()
        except OperationError as error:
            dialog.error(error)

    def chosen(paths):
        try:
            for path in paths:
                c.mutate(lambda path=path: structure.add_attachment(doc, path))
            refresh()
        except (OperationError, OSError) as error:
            dialog.error(error)
    add.connect('clicked', lambda _b: choose_files(w, _('attachments_add'), None, True, chosen))
    refresh()
    dialog.show()


# ---------------------------------------------------------------- layers

def layers(c):
    w = c.window
    doc = w.doc
    editable = c.editable()
    dialog = OperationDialog(w, _('menu_layers'), _('btn_save'), width=560, height=520)
    group = dialog.group(_('layers_title'), _('layers_hint'))
    rows = {}
    items = structure.layers(doc)
    if not items:
        group.add(Gtk.Label(label=_('layers_none'), margin_top=10, margin_bottom=10))
    for item in items:
        row = dialog.switch(group, item['name'], item['on'], ', '.join(item['intent']) if item['intent'] else None)
        row.set_sensitive(not item['locked'])
        rows[item['xref']] = row
    new_group = dialog.group(_('layers_new'))
    name = dialog.entry(new_group, _('layers_new_name'), '')
    new_group.set_sensitive(editable)

    def apply(_dialog):
        visibility = {xref: row.get_active() for xref, row in rows.items()}

        def mutation():
            if visibility:
                structure.set_layer_visibility(doc, visibility)
            if name.get_text().strip():
                structure.add_layer(doc, name.get_text())
        if not editable:
            # View mode: change display only for this session, without marking edits.
            structure.set_layer_visibility(doc, visibility)
            from .. import pdf_handler
            pdf_handler.invalidate_page_cache(doc)
            w._load_page(w.current_page_index, reload_objects=False)
            return
        c.mutate(mutation)
        w._load_thumbnails()
        toast(w, _('status_layers_saved'))
    dialog.on_apply = apply
    if not editable:
        dialog.apply_button.set_label(_('layers_apply_view'))
    dialog.show()


# ---------------------------------------------------------------- compare

def compare(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_compare'), _('compare_run'), width=640, height=640)
    group = dialog.group(_('compare_with'), _('compare_hint'))
    other = dialog.file(group, _('compare_file'), [pdf_filter()])
    method = dialog.combo(group, _('compare_method'), [('text', _('compare_text')), ('visual', _('compare_visual'))])
    mark = dialog.switch(group, _('compare_mark'), True, _('compare_mark_hint'))
    results_group = dialog.group(_('compare_results'))
    summary = dialog.info(results_group, _('compare_summary'), _('compare_not_run'))
    rows = _boxed_list()
    results_group.add(rows)

    def apply(_dialog):
        if not other.path:
            raise OperationError(_('err_choose_file'))
        data = c.snapshot_bytes()
        path = other.path
        key = method.key

        def work(progress, cancel):
            with fitz.open('pdf', data) as mine, fitz.open(path) as theirs:
                if theirs.needs_pass:
                    raise OperationError(_('err_insert_encrypted'))
                if key == 'visual':
                    return {'visual': text_ops.compare_visual(mine, theirs, progress=progress, cancel=cancel)}
                result = text_ops.compare_text(mine, theirs, progress, cancel)
                if mark.get_active() and not result['equal']:
                    marked = fitz.open('pdf', theirs.tobytes())
                    text_ops.annotate_differences(marked, result['rects_b'])
                    result['marked'] = marked
                return result

        def done(result):
            _clear(rows)
            if 'visual' in result:
                differing = result['visual']
                summary.set_subtitle(_('compare_visual_summary', len(differing)) if differing else _('compare_identical'))
                for page, fraction in sorted(differing.items()):
                    row = Adw.ActionRow(title=_('form_page_label', page + 1), subtitle=f'{fraction * 100:.2f}%')
                    row.add_suffix(_flat_icon('go-jump-symbolic', _('links_go'), lambda page=page: w._load_page(
                        min(page, w.doc.page_count - 1))))
                    rows.append(row)
                return False
            if result['equal']:
                summary.set_subtitle(_('compare_identical'))
                return False
            summary.set_subtitle(_('compare_text_summary', len(result['changes']), result['ratio'] * 100))
            for change in result['changes'][:400]:
                old = change['old'][:120] or '∅'
                new = change['new'][:120] or '∅'
                row = Adw.ActionRow(title=GLib.markup_escape_text(f'{_("compare_" + change["kind"])}: {old} → {new}'),
                                    subtitle=_('form_page_label', change['page_a'] + 1))
                row.add_suffix(_flat_icon('go-jump-symbolic', _('links_go'),
                                          lambda page=change['page_a']: w._load_page(min(page, w.doc.page_count - 1))))
                rows.append(row)
            if result.get('marked') is not None:
                c.open_new(result['marked'], os.path.splitext(os.path.basename(path))[0] + '-changes.pdf',
                           _('compare_marked_opened'))
            return False
        dialog.run(work, done, _('compare_running'))
        return False
    dialog.on_apply = apply
    dialog.show()


# ---------------------------------------------------------------- search

def advanced_search(c):
    w = c.window
    doc = w.doc
    dialog = OperationDialog(w, _('menu_advanced_search'), _('search_find_all'), width=640, height=660)
    group = dialog.group(_('search_query'))
    query = dialog.entry(group, _('search_placeholder'), w.search_entry.get_text() if hasattr(w, 'search_entry') else '')
    regex = dialog.switch(group, _('search_regex'), False, _('search_regex_hint'))
    case = dialog.switch(group, _('search_case'), False)
    whole = dialog.switch(group, _('search_whole_word'), False)
    pages = dialog.pages(group)
    results_group = dialog.group(_('search_results'))
    summary = dialog.info(results_group, _('search_results'), '')
    rows = _boxed_list()
    results_group.add(rows)
    query.connect('entry-activated', lambda _e: dialog._on_apply_clicked(None))

    def apply(_dialog):
        pattern = query.get_text()
        if not pattern:
            raise OperationError(_('search_enter_query'))
        selected = pages.pages()
        data = doc.tobytes(garbage=0, encryption=fitz.PDF_ENCRYPT_KEEP)
        password = getattr(doc, 'editor_password', '')
        options = dict(regex=regex.get_active(), case_sensitive=case.get_active(), whole_word=whole.get_active())

        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                if copy.needs_pass:
                    copy.authenticate(password)
                return text_ops.search_document(copy, pattern, pages=selected, progress=progress, cancel=cancel,
                                                **options)

        def done(hits):
            _clear(rows)
            flat, starts = [], []
            for hit in hits:
                starts.append(len(flat))
                flat.extend((hit['page'], fitz.Rect(q.rect)) for q in hit['quads'])
            if doc is w.doc:
                w._search_generation += 1
                w._search_document = doc
                w.search_results = flat
                w.search_current_result = -1
            summary.set_subtitle(_('search_found', len(hits)) if hits else _('search_no_results'))
            for index, hit in enumerate(hits[:1000]):
                row = Adw.ActionRow(title=GLib.markup_escape_text(hit['context']),
                                    subtitle=_('form_page_label', hit['page'] + 1), activatable=True)
                row.connect('activated', lambda _r, start=starts[index]: _goto(w, start))
                rows.append(row)
            if flat:
                _goto(w, 0)
            return False
        dialog.run(work, done)
        return False
    dialog.on_apply = apply
    dialog.show()
    if query.get_text():
        GLib.idle_add(lambda: (dialog._on_apply_clicked(None), False)[1])


def _goto(w, index):
    if w.search_results:
        w.search_revealer.set_reveal_child(True)
        w._go_to_search_result(index)


# ---------------------------------------------------------------- inspection and snapshot

def text_style(c):
    w = c.window
    doc = w.doc
    page = doc[w.current_page_index]
    rect = c.selection_rect()
    point = None
    if rect:
        rect = fitz.Rect(rect)
        point = (rect.tl + rect.br) / 2
        style = text_ops.text_style_at(page, point)
        if style is None:
            words = [fitz.Rect(word[:4]) for word in page.get_text('words') if fitz.Rect(word[:4]).intersects(rect)]
            if words:
                point = (words[0].tl + words[0].br) / 2
                style = text_ops.text_style_at(page, point)
    else:
        style = None
    if style is None:
        raise OperationError(_('text_style_need_selection'))
    dialog = OperationDialog(w, _('menu_text_style'), width=520)
    group = dialog.group(_('text_style_details'))
    colour = '#%02x%02x%02x' % tuple(int(v * 255) for v in style['color'])
    for key, value in (('text_style_text', style['text'].strip()), ('text_style_font', style['font']),
                       ('text_style_size', f"{style['size']:g} pt"), ('text_style_color', colour),
                       ('text_style_bold', _yes(style['bold'])), ('text_style_italic', _yes(style['italic'])),
                       ('text_style_opacity', f"{style['opacity'] * 100:.0f}%"),
                       ('text_style_render', _(f"render_mode_{style['render_mode']}"))):
        _info_row(group, _(key), value)
    similar = text_ops.spans_with_style(doc, style['font'], style['size'])
    same = dialog.group(_('text_style_same', len(similar)))
    rows = _boxed_list()
    same.add(rows)
    for number, bbox, text in similar[:200]:
        row = Adw.ActionRow(title=GLib.markup_escape_text(text.strip()[:100]), subtitle=_('form_page_label', number + 1),
                            activatable=True)
        row.connect('activated', lambda _r, number=number: w._load_page(number))
        rows.append(row)
    dialog.show()


def snapshot(c):
    """Copy the selected region of the page to the clipboard as a PNG image."""
    w = c.window
    rect = c.selection_rect()
    if not rect:
        raise OperationError(_('snapshot_need_selection'))
    page = w.doc[w.current_page_index]
    clip = fitz.Rect(rect) & page.cropbox
    if clip.is_empty:
        raise OperationError(_('snapshot_need_selection'))
    pix = page.get_pixmap(dpi=200, clip=clip, alpha=False)
    png = pix.tobytes('png')
    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
    w.get_clipboard().set_texture(texture)
    toast(w, _('status_snapshot_copied', pix.width, pix.height), button_label=_('btn_save'),
          callback=lambda: c.save_data(png, f'{c.stem()}-snapshot.png', [file_filter('PNG', ('*.png',))]))
