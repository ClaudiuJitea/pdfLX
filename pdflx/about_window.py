from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gtk

from . import constants
from .i18n import _


class AboutWindow(Adw.Window):
    """A focused product About window with separate license access."""

    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, resizable=False,
                         title=f'{_("menu_about")} {constants.APP_NAME}',
                         default_width=420, default_height=430)
        self.add_css_class("pdflx-about")
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(layout)
        header = Adw.HeaderBar()
        header.add_css_class("flat")
        header.set_title_widget(Gtk.Label(label=_("menu_about")))
        layout.append(header)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_start=36, margin_end=36, margin_top=18, margin_bottom=28)
        layout.append(body)
        icon = Gtk.Image.new_from_file(str(Path(__file__).parent / "img" / "pdflx.svg"))
        icon.set_pixel_size(96)
        icon.set_margin_bottom(4)
        body.append(icon)

        name = Gtk.Label(label=constants.APP_NAME)
        name.add_css_class("about-product-name")
        body.append(name)
        version = Gtk.Label(label=f'{_("about_version")} {constants.APP_VERSION}',
                            halign=Gtk.Align.CENTER)
        version.add_css_class("about-version")
        body.append(version)
        description = Gtk.Label(label=_("about_tagline"), wrap=True, max_width_chars=38,
                                justify=Gtk.Justification.CENTER, margin_top=8)
        description.add_css_class("about-description")
        body.append(description)
        tools = Gtk.Label(label=_("about_tools"))
        tools.add_css_class("dim-label")
        body.append(tools)
        body.append(Gtk.Separator(margin_top=12, margin_bottom=2))

        license_button = Gtk.Button(halign=Gtk.Align.CENTER)
        license_button.add_css_class("flat")
        license_box = Gtk.Box(spacing=8)
        license_box.append(Gtk.Image(icon_name="text-x-generic-symbolic", pixel_size=16))
        license_box.append(Gtk.Label(label="GPL-3.0-or-later"))
        license_box.append(Gtk.Image(icon_name="go-next-symbolic", pixel_size=12))
        license_button.set_child(license_box)
        license_button.set_tooltip_text(_("about_tab_license"))
        license_button.connect("clicked", self._show_license)
        body.append(license_button)

        key = Gtk.EventControllerKey()
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _show_license(self, button):
        window = Adw.Window(transient_for=self, modal=True,
                            title=_("about_tab_license"), default_width=640, default_height=520)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=_("about_tab_license"),
                                              subtitle="GPL-3.0-or-later"))
        layout.append(header)
        text = Gtk.TextView(editable=False, cursor_visible=False,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=24, right_margin=24,
                            top_margin=20, bottom_margin=20)
        license_path = Path(__file__).parent / "COPYING"
        text.get_buffer().set_text(license_path.read_text(encoding="utf-8"))
        scroll = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        scroll.set_child(text)
        layout.append(scroll)
        window.set_content(layout)
        window.present()
