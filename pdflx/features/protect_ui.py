"""Security dialogs: passwords/permissions, sanitization, hidden text, rasterize, signatures."""
import os

import pymupdf as fitz
from gi.repository import GLib, Gtk

from ..i18n import _
from ..dialogs import OperationDialog
from ..ops import verify as verify_ops
from ..ops.common import OperationError
from .. import pdf_handler


def security(c):
    w = c.window
    doc = w.doc
    state = security_module().describe_security(doc)
    dialog = OperationDialog(w, _('menu_security'), _('security_save_protected'), width=600)
    status = dialog.group(_('security_current'))
    dialog.info(status, _('security_encryption'), state['method'] or _('security_none'))
    granted = [_(f'perm_{key}') for key, allowed in state['permissions'].items() if allowed]
    dialog.info(status, _('security_allowed'), ', '.join(granted) or _('security_none'))

    passwords = dialog.group(_('security_passwords'), _('security_passwords_hint'))
    user_pw = dialog.password(passwords, _('security_open_password'))
    owner_pw = dialog.password(passwords, _('security_owner_password'))
    method = dialog.combo(passwords, _('security_method'),
                          [(key, label) for key, _v, label in security_module().ENCRYPTION_METHODS], 'aes256')
    perms = dialog.group(_('security_permissions'), _('security_permissions_hint'))
    checks = {key: dialog.switch(perms, _(f'perm_{key}'), True) for key, _f, _l in security_module().PERMISSIONS}
    remove_group = dialog.group(_('security_remove'), _('security_remove_hint'))
    remove = Gtk.Button(label=_('security_remove_button'), halign=Gtk.Align.START)
    remove.add_css_class('destructive-action')
    remove.set_sensitive(state['encrypted'] and getattr(doc, 'editor_can_edit', True))
    remove_group.add(remove)

    def save_unprotected(_button):
        try:
            data = security_module().unprotect(open_copy_source(c))
            c.save_pdf(data, f'{c.stem()}-unprotected', _('status_security_removed'))
            dialog.force_close()
        except OperationError as error:
            dialog.error(error)
    remove.connect('clicked', save_unprotected)

    def apply(_dialog):
        if not c.can_copy() and not getattr(doc, 'editor_can_edit', True):
            raise OperationError(_('err_owner_required'))
        data = security_module().protect(open_copy_source(c), user_pw.get_text(), owner_pw.get_text(),
                                         {key: row.get_active() for key, row in checks.items()}, method.key)
        c.save_pdf(data, f'{c.stem()}-protected', _('status_security_saved'))
    dialog.on_apply = apply
    dialog.show()


def security_module():
    # The action handler is named ``security``; keep the ops module reachable.
    from ..ops import security as module
    return module


def open_copy_source(c):
    """Live document with unsaved edits persisted, for ops that copy it."""
    c.flush_edits()
    from ..page_state import persist
    persist(c.window.doc)
    return c.window.doc


def sanitize(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_sanitize'), _('sanitize_save'), destructive=True, width=600)
    group = dialog.group(_('sanitize_remove'), _('sanitize_hint'))
    defaults = {'metadata', 'xml_metadata', 'javascript', 'embedded_files', 'attached_files', 'thumbnails',
                'reset_responses', 'hidden_text', 'redactions', 'clean_pages'}
    checks = {key: dialog.switch(group, _(f'scrub_{key}'), key in defaults) for key, _l in security_module().SCRUB_OPTIONS}
    dialog.group(None, _("sanitize_note"))

    def apply(_dialog):
        options = {key: row.get_active() for key, row in checks.items()}
        source = open_copy_source(c)
        data = security_module().scrub(source, options)
        c.save_pdf(data, f'{c.stem()}-sanitized', _('status_sanitized'))
    dialog.on_apply = apply
    dialog.show()


def hidden_text(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_hidden_text'), _('btn_scan'), width=640)
    group = dialog.group(_('hidden_title'), _('hidden_hint'))
    results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    results.add_css_class('boxed-list')
    group.add(results)
    summary = dialog.info(group, _('hidden_summary'), _('hidden_not_scanned'))
    data = c.snapshot_bytes()

    def apply(_dialog):
        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                return security_module().find_hidden_text(copy, progress, cancel)

        def done(findings):
            while child := results.get_first_child():
                results.remove(child)
            summary.set_subtitle(_('hidden_found', len(findings)) if findings else _('hidden_none'))
            for item in findings[:300]:
                row = Gtk.Button()
                row.add_css_class('flat')
                text = item['text'] if len(item['text']) < 80 else item['text'][:77] + '…'
                label = Gtk.Label(xalign=0, wrap=True,
                                  label=f"{_('form_page_label', item['page'] + 1)} · {_('hidden_reason_' + item['reason'])}: {text}")
                row.set_child(label)
                row.connect('clicked', lambda _b, page=item['page']: (w._load_page(page), dialog.force_close()))
                results.append(row)
            return False
        dialog.run(work, done)
        return False
    dialog.on_apply = apply
    dialog.show()


def rasterize(c):
    w = c.window
    dialog = OperationDialog(w, _('menu_rasterize'), _('btn_create'), width=560)
    group = dialog.group(_('rasterize_title'), _('rasterize_hint'))
    pages = dialog.pages(group)
    dpi = dialog.spin(group, _('dpi'), 150, 50, 600, 10)
    gray = dialog.switch(group, _('rasterize_gray'), False)

    def apply(_dialog):
        selected = pages.pages()
        data = c.snapshot_bytes()

        def work(progress, cancel):
            with fitz.open('pdf', data) as copy:
                return security_module().rasterize(copy, selected, dpi.get_value_as_int(), gray.get_active(),
                                                   progress, cancel)
        dialog.run(work, lambda result: c.open_new(result, f'{c.stem()}-flattened.pdf', _('status_new_tab_created')))
        return False
    dialog.on_apply = apply
    dialog.show()


def verify_signatures(c):
    w = c.window
    if not verify_ops.available():
        raise OperationError(_('err_pyhanko_missing'))
    path = w.current_file_path
    modified = w.document_modified
    dialog = OperationDialog(w, _('menu_verify_signatures'), width=660, height=560)
    dialog.extra_roots = []
    dialog.group(None, _('verify_hint_modified') if modified else _('verify_hint'))
    options = dialog.group(_('verify_trust'))
    system = dialog.switch(options, _('verify_system_roots'), True)
    roots = dialog.file(options, _('verify_extra_roots'), None, True, subtitle=_('verify_extra_roots_hint'))
    results_group = dialog.group(_('verify_results'))
    results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    results_group.add(results)
    run_button = Gtk.Button(label=_('verify_run'), halign=Gtk.Align.START)
    run_button.add_css_class('suggested-action')
    options.add(run_button)

    def source_bytes():
        # Verify what is on disk: the live document may hold unsaved edits.
        if path and os.path.isfile(path):
            with open(path, 'rb') as handle:
                return handle.read()
        return w.doc.tobytes(garbage=0, encryption=fitz.PDF_ENCRYPT_KEEP)

    def run(_button=None):
        data = source_bytes()
        password = getattr(w.doc, 'editor_password', '') or None
        extra = list(roots.paths)
        use_system = system.get_active()

        def work(progress, cancel):
            progress(0, 1)
            results = verify_ops.verify(data, password, extra, use_system)
            try:
                details = {item['field']: item for item in verify_ops.inspect(data, password)}
            except Exception:
                details = {}
            for result in results:
                result['pkcs7_info'] = details.get(result['field'])
            return results

        def done(items):
            while child := results.get_first_child():
                results.remove(child)
            if not items:
                results.append(Gtk.Label(label=_('verify_none'), xalign=0))
            for item in items:
                results.append(_signature_card(item, c))
            return False
        dialog.run(work, done, _('verify_running'))
    run_button.connect('clicked', run)
    dialog.show()
    run()


def _signature_card(item, c=None):
    from gi.repository import Adw
    icons = {'valid': 'emblem-ok-symbolic', 'untrusted': 'dialog-warning-symbolic',
             'modified': 'dialog-warning-symbolic', 'invalid': 'dialog-error-symbolic'}
    row = Adw.ExpanderRow(title=item['field'] or _('verify_unnamed'),
                          subtitle=_(f"verify_status_{item['status']}"))
    row.add_prefix(Gtk.Image(icon_name=icons.get(item['status'], 'dialog-question-symbolic')))
    for key, value in (('verify_signer', item['signer']), ('verify_issuer', item['issuer']),
                       ('verify_time', item['signing_time']), ('verify_intact', _yes(item.get('intact'))),
                       ('verify_valid', _yes(item.get('valid'))), ('verify_trusted', _yes(item.get('trusted'))),
                       ('verify_coverage', item.get('coverage', '')),
                       ('verify_modifications', item.get('modification_level', ''))):
        child = Adw.ActionRow(title=_(key), subtitle=str(value or '—'))
        child.add_css_class('property')
        child.set_subtitle_selectable(True)
        row.add_row(child)
    info = item.get('pkcs7_info')
    if info:
        for key, value in (('pkcs7_subfilter', info['subfilter']), ('pkcs7_digest', info['digest_algorithm']),
                           ('pkcs7_algorithm', info['signature_algorithm']),
                           ('pkcs7_byte_range', ' '.join(str(v) for v in info['byte_range'])),
                           ('pkcs7_timestamp', _yes(info['has_timestamp'])),
                           ('pkcs7_reason', info['reason']), ('pkcs7_location', info['location'])):
            child = Adw.ActionRow(title=_(key), subtitle=GLib.markup_escape_text(str(value or '—')))
            child.add_css_class('property')
            child.set_subtitle_selectable(True)
            row.add_row(child)
        for index, cert in enumerate(info['certificates']):
            child = Adw.ActionRow(title=_('pkcs7_certificate', index + 1),
                                  subtitle=GLib.markup_escape_text(
                                      f"{cert['subject']}\n{_('verify_issuer')}: {cert['issuer']}\n"
                                      f"{cert['not_before']} – {cert['not_after']}\nSHA-256 {cert['sha256']}"))
            child.set_subtitle_selectable(True)
            row.add_row(child)
        if c is not None:
            buttons = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12)
            p7s = Gtk.Button(label=_('pkcs7_save_container'))
            p7s.connect('clicked', lambda _b: c.save_data(info['pkcs7'], f"{item['field'] or 'signature'}.p7s"))
            chain = Gtk.Button(label=_('pkcs7_save_chain'))
            chain.connect('clicked', lambda _b: c.save_data(''.join(cert['pem'] for cert in info['certificates']),
                                                            f"{item['field'] or 'signature'}-chain.pem"))
            buttons.append(p7s)
            buttons.append(chain)
            row.add_row(buttons)
    details = Gtk.Label(label=item.get('details') or '', xalign=0, wrap=True, selectable=True,
                        margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
    details.add_css_class('monospace')
    details.add_css_class('caption')
    row.add_row(details)
    box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    box.add_css_class('boxed-list')
    box.append(row)
    return box


def _yes(value):
    return _('yes') if value else _('no')


def save_incremental(c):
    w = c.window
    if not pdf_handler.can_save_incrementally(w.doc, w.current_file_path):
        raise OperationError(_('err_incremental_unavailable'))
    c.flush_edits()
    w.save_document(w.current_file_path, incremental=True)


def confirm_signed_save(window, save_path):
    """Ask how to save a digitally signed document.

    Returns 'full', 'incremental', or None (cancelled / redirected to Save As).
    """
    from ..ops.common import signed_signature_fields
    from ..dialogs import ask
    names = signed_signature_fields(window.doc)
    if not names:
        return 'full'
    same_file = bool(window.current_file_path and save_path and
                     os.path.realpath(window.current_file_path) == os.path.realpath(save_path))
    can_append = same_file and pdf_handler.can_save_incrementally(window.doc, save_path)
    responses = [('cancel', _('btn_cancel'), None)]
    if same_file:
        responses.append(('copy', _('signed_save_copy'), None))
    if can_append:
        responses.append(('append', _('signed_save_append'), 'suggested'))
    responses.append(('rewrite', _('signed_save_rewrite'), 'destructive'))
    body = _('signed_save_body', ', '.join(names[:3]) + ('…' if len(names) > 3 else ''))
    if can_append:
        body += '\n\n' + _('signed_save_append_hint')
    answer = ask(window, _('signed_save_title'), body, responses, 'append' if can_append else 'cancel')
    if answer == 'append':
        return 'incremental'
    if answer == 'rewrite':
        return 'full'
    if answer == 'copy':
        window.on_save_as(None, None)
    return None
