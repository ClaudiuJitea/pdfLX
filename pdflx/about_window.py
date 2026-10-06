from pathlib import Path
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from . import constants
from .i18n import _
from .updates import NoReleasesError, fetch_latest_release


class AboutWindow(Adw.Window):
    """A focused product About window with separate license access."""

    def __init__(self, parent):
        super().__init__(transient_for=parent, modal=True, resizable=False,
                         title=f'{_("menu_about")} {constants.APP_NAME}',
                         default_width=420, default_height=430)
        self.add_css_class("pdflx-about")
        self._closed = False
        self._checking_updates = False
        self._update_url = None
        self.connect('close-request', self._on_close)
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
        update_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.update_button = Gtk.Button(label=_("updates_title"), halign=Gtk.Align.CENTER)
        self.update_button.add_css_class('flat')
        self.update_button.connect('clicked', self._on_update_clicked)
        update_box.append(self.update_button)
        self.update_status = Gtk.Label(visible=False, wrap=True, max_width_chars=38,
                                       justify=Gtk.Justification.CENTER)
        self.update_status.add_css_class('dim-label')
        self.update_status.add_css_class('caption')
        update_box.append(self.update_status)
        body.append(update_box)
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

    def _on_close(self, _window):
        self._closed = True
        return False

    def _on_update_clicked(self, _button):
        if self._update_url:
            Gtk.UriLauncher.new(self._update_url).launch(self, None, None, None)
        else:
            self.check_updates()

    def check_updates(self):
        if self._closed or self._checking_updates:
            return
        self._checking_updates = True
        self._update_url = None
        self.update_button.set_label(_("updates_checking"))
        self.update_button.set_sensitive(False)
        self.update_status.set_visible(False)

        def worker():
            try:
                release = fetch_latest_release()
            except NoReleasesError:
                GLib.idle_add(self._finish_update_check, None, 'updates_none')
            except Exception:
                GLib.idle_add(self._finish_update_check, None, 'updates_failed')
            else:
                GLib.idle_add(self._finish_update_check, release, None)
        threading.Thread(target=worker, daemon=True).start()

    def _finish_update_check(self, release, error):
        if self._closed:
            return GLib.SOURCE_REMOVE
        self._checking_updates = False
        self._update_url = None
        self.update_button.set_sensitive(True)
        self.update_button.set_label(_("updates_title"))
        if error:
            self.update_status.set_text(_(error))
        elif release.newer_than():
            self._update_url = release.url
            self.update_button.set_label(_("updates_view"))
            self.update_status.set_text(_("updates_available", release.version))
        else:
            self.update_status.set_text(_("updates_current"))
        self.update_status.set_visible(True)
        return GLib.SOURCE_REMOVE

    def _show_license(self, button):
        from .license_window import LicenseWindow
        window = LicenseWindow(self)
        window.present()
