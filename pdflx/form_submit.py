"""Submit-form buttons: send the form data to the button's address after the user confirms."""
import html
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from gi.repository import GLib, Gtk

from .constants import APP_NAME, APP_VERSION
from .i18n import _

CONTENT_TYPES = {'fdf': 'application/vnd.fdf', 'xfdf': 'application/vnd.adobe.xfdf',
                 'html': 'application/x-www-form-urlencoded', 'pdf': 'application/pdf'}


def payload(doc, fmt, source_name=''):
    """(bytes, content type) for a SubmitForm format."""
    from .ops import formdata
    if fmt == 'pdf':
        return doc.tobytes(garbage=0, deflate=True), CONTENT_TYPES['pdf']
    if fmt == 'html':
        values = formdata.collect(doc)
        # Like an HTML form: unchecked boxes are left out, lists repeat their name.
        pairs = []
        for name, value in values.items():
            if isinstance(value, list):
                pairs.extend((name, item) for item in value)
            elif value != 'Off':
                pairs.append((name, value))
        return urllib.parse.urlencode(pairs).encode('utf-8'), CONTENT_TYPES['html']
    data = formdata.export(doc, fmt, source_name)
    return (data if isinstance(data, bytes) else data.encode('utf-8')), CONTENT_TYPES.get(fmt, 'application/octet-stream')


def is_private(url):
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    return host in ('localhost', '::1') or host.startswith('127.') or host.endswith('.local')


def post(url, data, content_type, timeout=30):
    """POST and return (status, reason, response text excerpt)."""
    request = urllib.request.Request(url, data=data, method='POST', headers={
        'Content-Type': content_type, 'User-Agent': f'{APP_NAME}/{APP_VERSION}'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.reason, _excerpt(response.read(), response.headers.get_content_type())
    except urllib.error.HTTPError as error:
        return error.code, error.reason, _excerpt(error.read(), error.headers.get_content_type() if error.headers else '')


def _excerpt(body, content_type):
    text = body[:20000].decode('utf-8', 'replace')
    if 'html' in (content_type or ''):
        text = re.sub(r'(?is)<(script|style)\b.*?</\1>', ' ', text)
        text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:400] + ('…' if len(text) > 400 else '')


def mailto(url, doc):
    """Open the mail client with the form values in the message body."""
    from .ops import formdata
    values = formdata.collect(doc)
    body = '\n'.join(f'{name}: {", ".join(value) if isinstance(value, list) else value}' for name, value in values.items())
    separator = '&' if '?' in url else '?'
    return url + separator + urllib.parse.urlencode({'subject': _("submit_mail_subject"), 'body': body},
                                                    quote_via=urllib.parse.quote)


class FormSubmission:
    def __init__(self, controller, destination):
        self.controller = controller
        self.window = controller.window
        self.url = (destination or {}).get('url', '').strip()
        self.fmt = (destination or {}).get('format', 'fdf')

    def start(self):
        from .dialogs import alert
        from .form_properties import missing_required
        from . import document_features as features
        if not self.controller.save_values():
            return
        if not re.match(r'^(https?://|mailto:)', self.url, re.I):
            alert(self.window, _("button_submit_title"), _("submit_bad_address", self.url or '—'),
                  [('close', _("btn_close"), None)], 'close', 'close')
            return
        missing = missing_required(features.list_form_fields(self.window.doc))
        if missing:
            names = ', '.join(field['name'] for field in missing[:8]) + ('…' if len(missing) > 8 else '')
            self.window._load_page(missing[0]['page'])
            alert(self.window, _("button_submit_title"), _("submit_missing", names),
                  [('close', _("btn_close"), 'suggested')], 'close', 'close')
            return
        if self.url.lower().startswith('mailto:'):
            self._confirm(_("submit_confirm_mail", self.url[7:]), self._send_mail)
            return
        body = _("submit_confirm", self.fmt.upper(), self.url)
        if self.url.lower().startswith('http://') and not is_private(self.url):
            body += '\n\n' + _("submit_unencrypted")
        self._confirm(body, self._send)

    def _confirm(self, body, action):
        from .dialogs import alert
        def answer(response):
            if response == 'submit':
                action()
            elif response == 'export':
                self.window.lookup_action('form_export_data').activate(None)
        alert(self.window, _("button_submit_title"), body,
              [('cancel', _("btn_cancel"), None), ('export', _("button_submit_export"), None),
               ('submit', _("submit_send"), 'suggested')], 'submit', 'cancel', answer)

    def _send_mail(self):
        Gtk.UriLauncher.new(mailto(self.url, self.window.doc)).launch(self.window, None, None, None)

    def _send(self):
        import os
        try:
            source = os.path.basename(self.window.current_file_path or 'document.pdf')
            data, content_type = payload(self.window.doc, self.fmt, source)
        except Exception as error:
            self._finished(None, str(error), '')
            return
        self.window.status_label.set_text(_("submit_sending", self.url))
        url = self.url

        def work():
            try:
                status, reason, text = post(url, data, content_type)
            except urllib.error.URLError as error:
                GLib.idle_add(self._finished, None, str(error.reason), '')
                return
            except Exception as error:
                GLib.idle_add(self._finished, None, str(error), '')
                return
            GLib.idle_add(self._finished, status, reason, text)
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, status, reason, text):
        from .dialogs import alert
        if status is not None and 200 <= status < 300:
            self.window.status_label.set_text(_("submit_done_status", status))
            body = _("submit_done", self.url, status) + (f'\n\n{text}' if text else '')
            alert(self.window, _("submit_done_title"), body, [('close', _("btn_close"), 'suggested')], 'close', 'close')
            return False
        problem = f'HTTP {status} {reason}' if status is not None else reason
        self.window.status_label.set_text(_("submit_failed_status"))
        body = _("submit_failed", self.url, problem) + (f'\n\n{text}' if text else '')
        def answer(response):
            if response == 'retry':
                self._send()
            elif response == 'export':
                self.window.lookup_action('form_export_data').activate(None)
        alert(self.window, _("submit_failed_title"), body,
              [('close', _("btn_close"), None), ('export', _("button_submit_export"), None),
               ('retry', _("submit_retry"), 'suggested')], 'retry', 'close', answer)
        return False
