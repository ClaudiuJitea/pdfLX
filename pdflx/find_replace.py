"""Find & Replace text across the document, with a reviewable list of changes and one undo step."""
import re

from gi.repository import GLib, Gtk, Pango

from .i18n import _


def expression(find, case=False, word=False, regex=False):
    flags = 0 if case else re.IGNORECASE
    pattern = find if regex else re.escape(find)
    if word:
        pattern = r'(?<!\w)(?:' + pattern + r')(?!\w)'
    return re.compile(pattern, flags | re.UNICODE)


def candidate_pages(doc, compiled, pages):
    """Pages whose text contains a match (quick check before loading editable objects)."""
    found = []
    for number in pages:
        text = ' '.join(doc[number].get_text().split())
        if compiled.search(text):
            found.append(number)
    return found


class Replacer:
    """Collects matches in editable text objects and applies the replacements."""
    def __init__(self, window):
        from .ai.tools import DocumentTools
        self.window = window
        self.tools = DocumentTools(window, lambda: [])

    def matches(self, find, replace, case=False, word=False, regex=False, pages=None):
        """[(page, object id, old text, new text, count)] for every text object that changes."""
        compiled = expression(find, case, word, regex)
        doc = self.window.doc
        numbers = list(range(doc.page_count)) if pages is None else pages
        start = self.window.current_page_index
        results = []
        try:
            for number in candidate_pages(doc, compiled, numbers):
                self.tools.goto(number)
                for obj in list(self.window.editable_texts):
                    if not obj.text:
                        continue
                    try:
                        # Literal replacements must not interpret backslashes or group references.
                        new, count = compiled.subn(replace if regex else (lambda _match: replace), obj.text)
                    except (re.error, IndexError) as error:
                        raise ValueError(_("replace_bad_pattern", error)) from None
                    if count:
                        results.append((number, self.tools.ident(obj), obj.text, new, count))
        finally:
            if self.window.current_page_index != start:
                self.window._load_page(start)
        return results

    def apply(self, changes):
        """Apply [(page, id, old, new, count)] as one undo step; returns the number of replacements."""
        by_page = {}
        for number, ident, _old, new, count in changes:
            by_page.setdefault(number, []).append(({'id': ident, 'text': new}, count))
        total = 0
        start = self.window.current_page_index
        try:
            for number, edits in sorted(by_page.items()):
                self.tools.edit_elements([edit for edit, _count in edits], page=number + 1)
                total += sum(count for _edit, count in edits)
        finally:
            self.tools.end_turn()
            if start < self.window.doc.page_count:
                self.window._load_page(start)
        return total


class FindReplaceDialog:
    def __new__(cls, window, initial=''):
        from .document_tool_ui import ToolDialog
        replacer = Replacer(window)
        state = {'changes': [], 'timer': None}

        def apply():
            chosen = [change for change, check in zip(state['changes'], checks) if check.get_active()]
            if not chosen:
                dialog.error(_("replace_nothing"))
                return False
            if not window.document_tools.editable():
                return False
            total = replacer.apply(chosen)
            window.status_label.set_text(_("replace_done", total))
            return True

        dialog = ToolDialog(window, _("replace_title"), apply)
        dialog.apply_button.set_label(_("replace_apply"))
        find = dialog.entry(_("replace_find"), initial)
        replace = dialog.entry(_("replace_with"), '')
        case = dialog.check(_("search_case"), False)
        word = dialog.check(_("search_option_word"), False)
        regex = dialog.check(_("search_regex"), False)
        scope = dialog.dropdown(_("replace_scope"), [_("replace_scope_all"), _("replace_scope_page")])
        header = Gtk.Box(spacing=8)
        summary = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        summary.add_css_class('dim-label')
        header.append(summary)
        toggle = Gtk.CheckButton(label=_("replace_select_all"), active=True)
        header.append(toggle)
        dialog.box.append(header)
        listing = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listing.add_css_class('boxed-list')
        scroll = Gtk.ScrolledWindow(min_content_height=220, max_content_height=360, propagate_natural_height=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER, child=listing)
        dialog.box.append(scroll)
        note = Gtk.Label(label=_("replace_note"), xalign=0, wrap=True, max_width_chars=60)
        note.add_css_class('dim-label')
        note.add_css_class('caption')
        dialog.box.append(note)
        checks = []

        def refresh():
            state['timer'] = None
            while child := listing.get_first_child():
                listing.remove(child)
            checks.clear()
            state['changes'] = []
            text = find.get_text()
            if not text:
                summary.set_text(_("replace_type"))
                dialog.apply_button.set_sensitive(False)
                return False
            pages = [window.current_page_index] if scope.get_selected() == 1 else None
            try:
                compiled = expression(text, case.get_active(), word.get_active(), regex.get_active())
                state['changes'] = replacer.matches(text, replace.get_text(), case.get_active(), word.get_active(),
                                                    regex.get_active(), pages)
            except (re.error, ValueError) as error:
                summary.set_text(str(error))
                dialog.apply_button.set_sensitive(False)
                return False
            total = sum(change[4] for change in state['changes'])
            summary.set_text(_("replace_found", total, len({c[0] for c in state['changes']})) if total
                             else _("replace_none"))
            for number, _ident, old, new, count in state['changes'][:500]:
                row = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6, margin_start=10, margin_end=10)
                check = Gtk.CheckButton(active=toggle.get_active(), valign=Gtk.Align.CENTER)
                checks.append(check)
                row.append(check)
                texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
                label = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, max_width_chars=60)
                label.set_markup(_markup(old, new, compiled))
                texts.append(label)
                meta = Gtk.Label(label=_("replace_row_meta", number + 1, count), xalign=0)
                meta.add_css_class('dim-label')
                meta.add_css_class('caption')
                texts.append(meta)
                row.append(texts)
                listing.append(Gtk.ListBoxRow(child=row, activatable=False))
            dialog.apply_button.set_sensitive(bool(total))
            return False

        def schedule(*_args):
            if state['timer']:
                GLib.source_remove(state['timer'])
            state['timer'] = GLib.timeout_add(350, refresh)
        for widget in (find, replace):
            widget.connect('changed', schedule)
        for widget in (case, word, regex):
            widget.connect('notify::active', schedule)
        scope.connect('notify::selected', schedule)
        toggle.connect('toggled', lambda button: [check.set_active(button.get_active()) for check in checks])
        refresh()
        dialog.refresh = refresh
        dialog.find_entry, dialog.replace_entry, dialog.checks = find, replace, checks
        dialog.state = state
        return dialog


def _markup(old, new, compiled):
    """Old text with every match struck through, followed by the new text."""
    parts, last = [], 0
    for match in compiled.finditer(old):
        parts.append(GLib.markup_escape_text(old[last:match.start()]))
        parts.append(f'<span strikethrough="true" alpha="60%">{GLib.markup_escape_text(match.group(0))}</span>')
        last = match.end()
    parts.append(GLib.markup_escape_text(old[last:]))
    return ''.join(parts) + '\n→ <b>' + GLib.markup_escape_text(new) + '</b>'
