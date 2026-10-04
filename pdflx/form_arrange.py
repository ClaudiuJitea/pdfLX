"""Align, distribute and match the size of several selected form fields."""
import pymupdf as fitz
from gi.repository import Gtk, GLib
from .i18n import _

# (operation, icon, tooltip key); None separates button groups.
OPERATIONS = (
    ('left', 'editor-align-left-symbolic', 'form_align_left'),
    ('hcenter', 'editor-align-hcenter-symbolic', 'form_align_hcenter'),
    ('right', 'editor-align-right-symbolic', 'form_align_right'),
    None,
    ('top', 'editor-align-top-symbolic', 'form_align_top'),
    ('vcenter', 'editor-align-vcenter-symbolic', 'form_align_vcenter'),
    ('bottom', 'editor-align-bottom-symbolic', 'form_align_bottom'),
    None,
    ('distribute_h', 'editor-distribute-h-symbolic', 'form_distribute_h'),
    ('distribute_v', 'editor-distribute-v-symbolic', 'form_distribute_v'),
    None,
    ('width', 'editor-same-width-symbolic', 'form_same_width'),
    ('height', 'editor-same-height-symbolic', 'form_same_height'),
    ('size', 'editor-same-size-symbolic', 'form_same_size'),
    None,
    ('match_style', 'editor-match-style-symbolic', 'form_match_style'),
)


def arrange(rects, operation, page_rect):
    """Return new rects for ``operation``; the first rect is the reference.

    Alignment and sizing follow the first rect. Distribution keeps the outermost
    rects in place and spaces the others with equal gaps between them.
    """
    rects = [fitz.Rect(rect) for rect in rects]
    if len(rects) < 2:
        return rects
    key = rects[0]
    result = []
    if operation in ('distribute_h', 'distribute_v'):
        horizontal = operation == 'distribute_h'
        start = (lambda r: r.x0) if horizontal else (lambda r: r.y0)
        size = (lambda r: r.width) if horizontal else (lambda r: r.height)
        order = sorted(range(len(rects)), key=lambda i: start(rects[i]))
        first, last = rects[order[0]], rects[order[-1]]
        span = start(last) + size(last) - start(first)
        gap = (span - sum(size(r) for r in rects)) / (len(rects) - 1)
        result = list(rects)
        position = start(first)
        for index in order:
            rect = fitz.Rect(rects[index])
            delta = position - start(rect)
            rect += (delta, 0, delta, 0) if horizontal else (0, delta, 0, delta)
            result[index] = rect
            position += size(rect) + gap
    else:
        for rect in rects:
            rect = fitz.Rect(rect)
            if operation == 'left':
                rect += (key.x0 - rect.x0, 0, key.x0 - rect.x0, 0)
            elif operation == 'right':
                rect += (key.x1 - rect.x1, 0, key.x1 - rect.x1, 0)
            elif operation == 'hcenter':
                delta = (key.x0 + key.x1 - rect.x0 - rect.x1) / 2
                rect += (delta, 0, delta, 0)
            elif operation == 'top':
                rect += (0, key.y0 - rect.y0, 0, key.y0 - rect.y0)
            elif operation == 'bottom':
                rect += (0, key.y1 - rect.y1, 0, key.y1 - rect.y1)
            elif operation == 'vcenter':
                delta = (key.y0 + key.y1 - rect.y0 - rect.y1) / 2
                rect += (0, delta, 0, delta)
            if operation in ('width', 'size'):
                rect.x1 = rect.x0 + key.width
            if operation in ('height', 'size'):
                rect.y1 = rect.y0 + key.height
            result.append(rect)
    page_rect = fitz.Rect(page_rect)
    clamped = []
    for rect in result:
        # Keep each field fully on the page, moving rather than shrinking it.
        dx = max(page_rect.x0 - rect.x0, min(0, page_rect.x1 - rect.x1))
        dy = max(page_rect.y0 - rect.y0, min(0, page_rect.y1 - rect.y1))
        clamped.append(rect + (dx, dy, dx, dy))
    return clamped


class ArrangeBar:
    """Floating toolbar shown while two or more form fields are selected."""
    def __init__(self, controller, surface):
        self.controller = controller
        self.window = controller.window
        self.pending = None
        self.frame = Gtk.Box(spacing=2, halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                             margin_top=12, visible=False)
        self.frame.add_css_class('pdflx-inspector')
        self.frame.add_css_class('pdflx-arrange-bar')
        self.count = Gtk.Label(margin_start=8, margin_end=6)
        self.count.add_css_class('dim-label')
        self.frame.append(self.count)
        self.buttons = {}
        for item in OPERATIONS:
            if item is None:
                self.frame.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL,
                                                margin_start=3, margin_end=3))
                continue
            operation, icon, tip = item
            button = Gtk.Button(icon_name=icon, tooltip_text=_(tip))
            button.add_css_class('flat')
            button.connect('clicked', lambda _button, op=operation: self.controller.arrange_fields(op))
            self.frame.append(button)
            self.buttons[operation] = button
        surface.add_overlay(self.frame)

    def request_sync(self):
        if self.pending is None:
            self.pending = GLib.idle_add(self.sync)

    def sync(self):
        self.pending = None
        count = len(self.controller.interaction.fields())
        self.frame.set_visible(count > 1)
        if count > 1:
            self.count.set_text(_("form_fields_selected", count))
            for operation in ('distribute_h', 'distribute_v'):
                self.buttons[operation].set_sensitive(count > 2)
        return GLib.SOURCE_REMOVE
