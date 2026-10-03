import os
import random
import datetime
from pathlib import Path

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango

from . import constants
from .i18n import _, get_setting, set_setting

_WELCOME_CSS = """
.welcome-title {
    font-size: 28px;
    font-weight: 800;
    letter-spacing: -0.5px;
    color: @theme_fg_color;
}

.welcome-subtitle {
    font-size: 13.5px;
    opacity: 0.65;
    margin-top: 2px;
}

.welcome-action-card {
    border-radius: 12px;
    min-height: 96px;
    min-width: 170px;
    padding: 12px 16px;
    border: 1px solid alpha(currentColor, 0.08);
    background-color: alpha(currentColor, 0.03);
    transition: all 160ms cubic-bezier(0.2, 0, 0, 1);
}

.welcome-action-card:hover {
    background-color: alpha(currentColor, 0.07);
    border-color: alpha(currentColor, 0.16);
}

.welcome-action-card:active {
    background-color: alpha(currentColor, 0.1);
}

.welcome-action-card.suggested-action {
    background-color: @accent_color;
    color: @accent_fg_color;
    border-color: transparent;
    box-shadow: 0 2px 8px alpha(@accent_color, 0.35);
}

.welcome-action-card.suggested-action:hover {
    background-color: mix(@accent_color, white, 0.12);
    box-shadow: 0 4px 12px alpha(@accent_color, 0.45);
}

.welcome-card-icon {
    margin-bottom: 2px;
}

.welcome-shortcut-badge {
    font-size: 11px;
    font-weight: 600;
    opacity: 0.55;
    letter-spacing: 0.3px;
    margin-top: 2px;
}

.suggested-action .welcome-shortcut-badge {
    opacity: 0.85;
}

.recent-section-header {
    margin-top: 6px;
    margin-bottom: 4px;
}

.recent-section-title {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: -0.2px;
    color: @theme_fg_color;
}

.recent-count-badge {
    font-size: 11px;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 9999px;
    background-color: alpha(currentColor, 0.08);
    color: alpha(currentColor, 0.65);
}

.recent-clear-btn {
    opacity: 0.55;
    font-size: 12px;
    font-weight: 500;
    padding: 3px 8px;
    border-radius: 6px;
    transition: opacity 150ms ease, background-color 150ms ease;
}

.recent-clear-btn:hover {
    opacity: 1.0;
    background-color: alpha(currentColor, 0.08);
}

.recent-list-container {
    background: transparent;
}

.recent-row {
    padding: 6px 10px;
    transition: background-color 140ms ease;
}

.recent-row:hover {
    background-color: alpha(currentColor, 0.06);
}

.recent-file-icon {
    color: #e04f5f;
    margin-left: 4px;
    margin-right: 2px;
}

.recent-remove-btn {
    opacity: 0.35;
    padding: 4px;
    border-radius: 9999px;
    transition: opacity 150ms ease, background-color 150ms ease;
}

.recent-remove-btn:hover {
    opacity: 1.0;
    background-color: alpha(currentColor, 0.1);
}

.recent-empty-card {
    padding: 32px 24px;
    border-radius: 12px;
    border: 1px dashed alpha(currentColor, 0.12);
    background-color: alpha(currentColor, 0.015);
}

.welcome-tip-pill {
    background-color: alpha(currentColor, 0.03);
    border: 1px solid alpha(currentColor, 0.07);
    border-radius: 9999px;
    padding: 7px 18px;
    margin-top: 10px;
}
"""

_css_initialized = False


def _ensure_css():
    """Ensure WelcomeView styling is applied to default display."""
    global _css_initialized
    if not _css_initialized:
        display = Gdk.Display.get_default()
        if display:
            provider = Gtk.CssProvider()
            provider.load_from_data(_WELCOME_CSS.encode('utf-8'))
            Gtk.StyleContext.add_provider_for_display(
                display,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            _css_initialized = True


class WelcomeView(Adw.Bin):
    """The start screen view showing quick actions, recent files, and tips."""

    def __init__(self, parent_window, **kwargs):
        """Initialise the WelcomeView and load recent files."""
        super().__init__(**kwargs)
        self.parent_window = parent_window

        _ensure_css()
        self._build_ui()
        self._populate_recent_files()

    def _build_ui(self):
        """Build a clean, professional start screen layout."""
        # Scrolled window allows content to scroll gracefully on smaller screens
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_propagate_natural_height(True)
        self.set_child(scrolled)

        clamp = Adw.Clamp(maximum_size=780, tightening_threshold=400)
        scrolled.set_child(clamp)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=26)
        main_box.set_vexpand(True)
        main_box.set_valign(Gtk.Align.CENTER)
        main_box.set_margin_top(36)
        main_box.set_margin_bottom(36)
        main_box.set_margin_start(24)
        main_box.set_margin_end(24)
        clamp.set_child(main_box)

        # 1. Header / Hero section
        hero_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER)

        app_icon = Gtk.Image.new_from_icon_name(constants.APP_ICON)
        app_icon.set_pixel_size(72)
        app_icon.set_margin_bottom(12)
        hero_box.append(app_icon)

        title = Gtk.Label(label=constants.APP_NAME)
        title.add_css_class("welcome-title")
        hero_box.append(title)

        subtitle = Gtk.Label(label=_("app_subtitle"))
        subtitle.add_css_class("welcome-subtitle")
        subtitle.add_css_class("dim-label")
        hero_box.append(subtitle)

        main_box.append(hero_box)

        # 2. Quick Action Cards (Side-by-side)
        cards_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=16,
            halign=Gtk.Align.FILL,
            homogeneous=True,
        )
        cards_box.set_margin_top(8)
        cards_box.set_margin_bottom(6)

        # Open File (Primary suggested action)
        open_card = self._create_action_card(
            title=_("btn_open"),
            subtitle="Ctrl+O",
            icon_name="document-open-symbolic",
            action_name="win.open",
            is_primary=True,
        )
        cards_box.append(open_card)

        # New Document
        new_card = self._create_action_card(
            title=_("btn_new"),
            subtitle="Ctrl+N",
            icon_name="document-new-symbolic",
            action_name="win.new",
            is_primary=False,
        )
        cards_box.append(new_card)

        # Quick Start Guide
        guide_card = self._create_action_card(
            title=_("btn_guide"),
            subtitle="F1",
            icon_name="help-browser-symbolic",
            action_name="win.quick_guide",
            is_primary=False,
        )
        cards_box.append(guide_card)

        main_box.append(cards_box)

        # 3. Recent Documents Section
        recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        recent_box.set_margin_top(12)

        # Section header bar
        header_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        header_bar.add_css_class("recent-section-header")

        icon_title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8, valign=Gtk.Align.CENTER)
        recents_icon = Gtk.Image.new_from_icon_name("document-open-recent-symbolic")
        recents_icon.set_pixel_size(16)
        recents_icon.add_css_class("dim-label")
        icon_title_box.append(recents_icon)

        raw_header = _("recent_header") or "Recent Documents"
        clean_header = raw_header.replace("<b>", "").replace("</b>", "")
        header_title = Gtk.Label(label=clean_header)
        header_title.add_css_class("recent-section-title")
        icon_title_box.append(header_title)

        self.recent_count_badge = Gtk.Label(label="0")
        self.recent_count_badge.add_css_class("recent-count-badge")
        icon_title_box.append(self.recent_count_badge)

        header_bar.append(icon_title_box)

        spacer = Gtk.Box(hexpand=True)
        header_bar.append(spacer)

        self.clear_recent_btn = Gtk.Button(label=_("btn_cancel") if _("btn_cancel") == "Clear" else "Clear")
        self.clear_recent_btn.add_css_class("flat")
        self.clear_recent_btn.add_css_class("recent-clear-btn")
        self.clear_recent_btn.set_tooltip_text("Clear recent files history")
        self.clear_recent_btn.connect("clicked", self._clear_recent_files)
        header_bar.append(self.clear_recent_btn)

        recent_box.append(header_bar)

        # Container for recent list
        self.recent_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.recent_container.add_css_class("recent-list-container")

        self.recent_list_box = Gtk.ListBox()
        self.recent_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.recent_list_box.add_css_class("boxed-list")
        self.recent_list_box.connect("row-activated", self._on_recent_row_activated)

        self.recent_scroll = Gtk.ScrolledWindow()
        self.recent_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.recent_scroll.set_max_content_height(240)
        self.recent_scroll.set_propagate_natural_height(True)
        self.recent_scroll.set_child(self.recent_list_box)
        self.recent_container.append(self.recent_scroll)

        # Empty state placeholder
        self.empty_recent_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=8,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
        )
        self.empty_recent_box.add_css_class("recent-empty-card")

        empty_icon = Gtk.Image.new_from_icon_name("document-open-recent-symbolic")
        empty_icon.set_pixel_size(32)
        empty_icon.add_css_class("dim-label")
        self.empty_recent_box.append(empty_icon)

        empty_label = Gtk.Label(label="No Recent Documents")
        empty_label.add_css_class("heading")
        empty_label.add_css_class("dim-label")
        self.empty_recent_box.append(empty_label)

        empty_sublabel = Gtk.Label(label="Documents you open will appear here for quick access")
        empty_sublabel.add_css_class("caption")
        empty_sublabel.add_css_class("dim-label")
        self.empty_recent_box.append(empty_sublabel)

        self.recent_container.append(self.empty_recent_box)

        recent_box.append(self.recent_container)
        main_box.append(recent_box)
        self.recent_box = recent_box

        # 4. Tips / Footer Pill
        tips = _("tips")
        tip_text = random.choice(tips) if isinstance(tips, list) else str(tips)

        tip_pill = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        tip_pill.add_css_class("welcome-tip-pill")

        tip_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        tip_icon.set_pixel_size(14)
        tip_icon.add_css_class("dim-label")
        tip_pill.append(tip_icon)

        tip_lbl = Gtk.Label(label=tip_text)
        tip_lbl.add_css_class("dim-label")
        tip_lbl.add_css_class("caption")
        tip_lbl.set_wrap(True)
        tip_pill.append(tip_lbl)

        main_box.append(tip_pill)

    def _create_action_card(self, title: str, subtitle: str, icon_name: str, action_name: str, is_primary: bool = False) -> Gtk.Button:
        """Create a card-style hero action button."""
        btn = Gtk.Button()
        btn.set_action_name(action_name)
        btn.add_css_class("welcome-action-card")
        if is_primary:
            btn.add_css_class("suggested-action")
        else:
            btn.add_css_class("card")

        btn.set_tooltip_text(f"{title} ({subtitle})")

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            valign=Gtk.Align.CENTER,
            halign=Gtk.Align.CENTER,
        )
        box.set_margin_top(14)
        box.set_margin_bottom(14)
        box.set_margin_start(16)
        box.set_margin_end(16)

        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(28)
        icon.add_css_class("welcome-card-icon")
        box.append(icon)

        title_lbl = Gtk.Label(label=title)
        title_lbl.add_css_class("heading")
        box.append(title_lbl)

        accel_lbl = Gtk.Label(label=subtitle)
        accel_lbl.add_css_class("welcome-shortcut-badge")
        box.append(accel_lbl)

        btn.set_child(box)
        return btn

    def refresh_recent_files(self):
        """Public helper to refresh the recent files list."""
        self._populate_recent_files()

    def _clear_recent_files(self, button=None):
        """Clear all recent opened files from settings and reload view."""
        set_setting("recent_opened_files", [])
        self._populate_recent_files()

    def _remove_recent_file(self, file_path_str: str):
        """Remove an individual file from recent files."""
        recents = get_setting("recent_opened_files", [])
        if isinstance(recents, list):
            norm_target = os.path.abspath(os.path.normpath(file_path_str))
            updated = [
                p for p in recents
                if isinstance(p, str) and os.path.abspath(os.path.normpath(p)) != norm_target
            ]
            set_setting("recent_opened_files", updated)
            self._populate_recent_files()

    def _populate_recent_files(self, *args):
        """Populate the list of recently opened PDF files from settings."""
        child = self.recent_list_box.get_first_child()
        while child:
            self.recent_list_box.remove(child)
            child = self.recent_list_box.get_first_child()

        recent_files = get_setting("recent_opened_files", [])
        if not isinstance(recent_files, list):
            recent_files = []

        valid_files = []
        for file_path_str in recent_files:
            if not file_path_str or not isinstance(file_path_str, str):
                continue
            p = Path(file_path_str)
            if p.is_file():
                valid_files.append(file_path_str)

        if len(valid_files) != len(recent_files):
            set_setting("recent_opened_files", valid_files)

        displayed_count = 0
        for file_path_str in valid_files[:10]:
            row = self._create_recent_file_row(file_path_str)
            self.recent_list_box.append(row)
            displayed_count += 1

        self.recent_count_badge.set_label(str(displayed_count))
        has_recents = displayed_count > 0

        self.recent_count_badge.set_visible(has_recents)
        self.clear_recent_btn.set_visible(has_recents)
        self.recent_scroll.set_visible(has_recents)
        self.empty_recent_box.set_visible(not has_recents)

    def _format_file_subtitle(self, file_path: Path) -> str:
        """Format a human-readable subtitle with path, size, and modified date."""
        parts = []
        # Path with ~ shorthand for home
        try:
            home = Path.home()
            parent = file_path.parent
            if parent == home or home in parent.parents:
                rel_to_home = parent.relative_to(home)
                parts.append(f"~/{rel_to_home}" if str(rel_to_home) != "." else "~")
            else:
                parts.append(str(parent))
        except Exception:
            parts.append(str(file_path.parent))

        # File size & modified date if accessible
        try:
            stat = file_path.stat()
            size = stat.st_size
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f} MB"
            parts.append(size_str)

            mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
            date_str = mtime.strftime("%b %d, %Y")
            parts.append(date_str)
        except Exception:
            pass

        return "  •  ".join(parts)

    def _create_recent_file_row(self, file_path_str: str) -> Gtk.ListBoxRow:
        """Create a stylized list row widget for a recent file entry."""
        row = Gtk.ListBoxRow()
        row._file_path = str(file_path_str)
        try:
            row._uri = Path(file_path_str).as_uri()
        except Exception:
            row._uri = ""
        row.set_activatable(True)
        row.set_tooltip_text(str(file_path_str))
        row.add_css_class("recent-row")

        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=14,
            margin_start=12,
            margin_end=8,
            margin_top=8,
            margin_bottom=8,
        )

        # Document Icon (clean, unboxed)
        icon = Gio.ThemedIcon.new_from_names([
            "application-pdf-symbolic",
            "x-office-document-symbolic",
            "text-x-generic-symbolic",
            "application-x-generic-symbolic",
            "document-symbolic",
        ])
        from .session_memory import cached_thumbnail
        thumbnail = cached_thumbnail(file_path_str)
        if thumbnail:
            # First-page preview cached when the file was last opened or saved.
            icon_img = Gtk.Picture.new_for_filename(thumbnail)
            icon_img.set_can_shrink(True)
            icon_img.set_content_fit(Gtk.ContentFit.CONTAIN)
            icon_img.set_size_request(34, 44)
            icon_img.add_css_class("card")
        else:
            icon_img = Gtk.Image.new_from_gicon(icon)
            icon_img.set_pixel_size(24)
        icon_img.set_valign(Gtk.Align.CENTER)
        icon_img.add_css_class("recent-file-icon")
        box.append(icon_img)

        # File metadata
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        file_path = Path(file_path_str)
        filename = file_path.name
        title_lbl = Gtk.Label(label=filename, xalign=0.0, ellipsize=Pango.EllipsizeMode.END)
        title_lbl.add_css_class("heading")
        vbox.append(title_lbl)

        subtitle_text = self._format_file_subtitle(file_path)
        subtitle_lbl = Gtk.Label(label=subtitle_text, xalign=0.0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        subtitle_lbl.add_css_class("dim-label")
        subtitle_lbl.add_css_class("caption")
        vbox.append(subtitle_lbl)

        box.append(vbox)

        # Right side actions (Remove button & subtle arrow)
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, valign=Gtk.Align.CENTER)

        remove_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        remove_btn.add_css_class("flat")
        remove_btn.add_css_class("circular")
        remove_btn.add_css_class("recent-remove-btn")
        remove_btn.set_tooltip_text("Remove from recent files")
        remove_btn.set_can_focus(False)
        remove_btn.connect("clicked", lambda b, fp=file_path_str: self._remove_recent_file(fp))
        actions_box.append(remove_btn)

        arrow = Gtk.Image.new_from_icon_name("go-next-symbolic")
        arrow.set_pixel_size(14)
        arrow.add_css_class("dim-label")
        actions_box.append(arrow)

        box.append(actions_box)
        row.set_child(box)
        return row

    def on_open_clicked(self, button):
        """Handle open button clicks by delegating to parent window."""
        if self.parent_window:
            self.parent_window.on_open_clicked(button)

    def _on_recent_row_activated(self, list_box, row):
        """Handle recent file row activation."""
        if not self.parent_window:
            return
        if hasattr(row, '_file_path') and row._file_path:
            self.parent_window.load_document(row._file_path)
        elif hasattr(row, '_uri') and row._uri:
            gfile = Gio.File.new_for_uri(row._uri)
            self.parent_window.load_document(gfile.get_path())
