"""A readable, selectable presentation of the bundled license."""
from pathlib import Path
import re

from gi.repository import Adw, Gdk, Gtk, Pango

from .i18n import _


class LicenseWindow(Adw.Window):
    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, title=_("about_tab_license"),
                         default_width=720, default_height=640)
        self.add_css_class('pdflx-license')
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.add_css_class('flat')
        header.set_title_widget(Gtk.Label(label=_("about_tab_license")))
        layout.append(header)

        self.text = Gtk.TextView(editable=False, cursor_visible=False,
                                 wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=28, right_margin=28,
                                 top_margin=28, bottom_margin=28,
                                 pixels_below_lines=12, pixels_inside_wrap=4)
        self.text.add_css_class('license-text')
        buffer = self.text.get_buffer()
        title = buffer.create_tag('title', weight=Pango.Weight.BOLD, scale=1.4,
                                  pixels_below_lines=6)
        subtitle = buffer.create_tag('subtitle', scale=0.9, pixels_below_lines=24)
        heading = buffer.create_tag('heading', weight=Pango.Weight.BOLD,
                                    pixels_above_lines=12, pixels_below_lines=12)
        example = buffer.create_tag('example', family='monospace', scale=0.9)
        source = (Path(__file__).parent / 'COPYING').read_text(encoding='utf-8')
        blocks = re.split(r'\n\s*\n', source.strip())
        for index, block in enumerate(blocks):
            lines = block.splitlines()
            if index == 0:
                for line, tag in zip(lines, (title, subtitle)):
                    buffer.insert_with_tags(buffer.get_end_iter(), line.strip() + '\n', tag)
                continue
            # Reflow prose from the plain-text file; retain line breaks in examples.
            is_example = all(not line.strip() or line.startswith('    ') for line in lines)
            paragraph = ('\n'.join(line.strip() for line in lines) if is_example
                         else ' '.join(block.split()))
            is_heading = len(lines) == 1 and (paragraph in (
                'Preamble', 'TERMS AND CONDITIONS', 'END OF TERMS AND CONDITIONS',
                'How to Apply These Terms to Your New Programs') or re.match(r'^\d+\. ', paragraph))
            tags = (heading,) if is_heading else (example,) if is_example else ()
            buffer.insert_with_tags(buffer.get_end_iter(), paragraph + '\n', *tags)

        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=560, child=self.text)
        scroll = Gtk.ScrolledWindow(child=clamp, vexpand=True, hexpand=True,
                                    hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    margin_start=18, margin_end=18, margin_bottom=16)
        scroll.add_css_class('license-reader')
        scroll.set_overflow(Gtk.Overflow.HIDDEN)
        layout.append(scroll)
        footer = Gtk.Box(spacing=12, margin_start=24, margin_end=18, margin_bottom=18)
        badge = Gtk.Label(label='GPL-3.0-or-later', hexpand=True, xalign=0)
        badge.add_css_class('dim-label')
        badge.add_css_class('caption')
        footer.append(badge)
        close = Gtk.Button(label=_("btn_close"))
        close.connect('clicked', lambda _button: self.close())
        footer.append(close)
        layout.append(footer)
        self.set_content(layout)
        key = Gtk.EventControllerKey()
        key.connect('key-pressed', self._on_key_pressed)
        self.add_controller(key)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False
