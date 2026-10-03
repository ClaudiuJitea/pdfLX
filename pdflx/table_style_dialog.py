"""Post-insertion style controls and a native PDF table preview."""
import copy
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, GLib
import pymupdf as fitz
from .document_tool_ui import ToolDialog
from .table_style import PRESETS, current_style, style_states
from .undo_manager import EditTableCommand
from .i18n import _


class TableStyleDialog(ToolDialog):
    def __init__(self, menu, table):
        self.menu, self.table = menu, table
        self.initial = [copy.deepcopy(obj.__dict__) for obj in table.objects]
        self.loading = True
        super().__init__(menu.window, _('table_style'), self.apply_style)
        style = current_style(table)
        self.section(_('table_style_preset'))
        self.preset = self.dropdown(_('table_style_preset'),
                                   [_('table_style_custom'), _('table_style_plain'),
                                    _('table_style_blue'), _('table_style_green'), _('table_style_striped')])
        self.header = self.check(_('table_style_header'), style['header'])
        self.fill = self.check(_('table_style_fill'), style['fill'])
        self.striped = self.check(_('table_style_banding'), style['striped'])
        self.colors = {}
        self.section(_('tool_stamp_color'))
        for key, label in (('header_fill','table_style_header_fill'),('body_fill','table_style_body_fill'),
                           ('alternate_fill','table_style_alternate_fill'),('border_color','table_style_border'),
                           ('text_color','table_style_text'),('header_text','table_style_header_text')):
            color = Gtk.ColorButton()
            color.set_title(_(label))
            color.set_tooltip_text(_(label))
            self.set_color(color, style[key])
            self.field(_(label), color)
            self.colors[key] = color
            color.connect('color-set', self.preview_style)
        self.section(_('table_style_border'))
        self.border = self.spin(_('table_style_border_width'), style['border_width'], .1, 5, .1)
        self.size = self.spin(_('table_font'), style['font_size'], 6, 24)
        self.picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, vexpand=True)
        self.picture.set_size_request(-1, 150)
        self.add_preview(self.picture)
        for control in (self.header, self.fill, self.striped):
            control.connect('toggled', self.preview_style)
        for control in (self.border, self.size):
            control.connect('value-changed', self.preview_style)
        self.preset.connect('notify::selected', self.choose_preset)
        self.loading = False
        self.preview_style()

    @staticmethod
    def set_color(button, rgb):
        rgba = Gdk.RGBA()
        rgba.red, rgba.green, rgba.blue = rgb
        rgba.alpha = 1
        button.set_rgba(rgba)

    def settings(self):
        result = dict(header=self.header.get_active(), fill=self.fill.get_active(),
                      striped=self.striped.get_active(), border_width=self.border.get_value(),
                      font_size=self.size.get_value())
        for key, button in self.colors.items():
            rgba = button.get_rgba()
            result[key] = (rgba.red, rgba.green, rgba.blue)
        return result

    def choose_preset(self, *args):
        selected = self.preset.get_selected()
        if not selected:
            return
        style = PRESETS[('plain','blue','green','striped')[selected-1]]
        self.loading = True
        for key, button in self.colors.items():
            self.set_color(button, style[key])
        self.header.set_active(style['header'])
        self.fill.set_active(style['fill'])
        self.striped.set_active(style['striped'])
        self.loading = False
        self.preview_style()

    def updated_states(self):
        if not self.menu.editable():
            raise ValueError(_('table_document_changed'))
        return style_states(self.table, self.source[self.table.page_number], self.settings())

    def preview_style(self, *args):
        if self.loading:
            return
        if args and self.preset.get_selected():
            self.preset.set_selected(0)
        try:
            states = self.updated_states()
            from . import pdf_handler
            source_page = self.source[self.table.page_number]
            with fitz.open() as scratch:
                page = scratch.new_page(width=source_page.mediabox.width, height=source_page.mediabox.height)
                page.set_cropbox(source_page.cropbox)
                page.set_rotation(source_page.rotation)
                # Paint cell fills and borders before all text, even after reload.
                from .models import EditableShape
                pairs = sorted(zip(self.table.objects, states), key=lambda pair: not isinstance(pair[0], EditableShape))
                for obj, state in pairs:
                    clone = copy.deepcopy(obj)
                    clone.__dict__.update(state)
                    ok, error = pdf_handler._apply_single_object_to_page(scratch, page, clone)
                    if not ok:
                        raise ValueError(error)
                clip = (fitz.Rect(self.table.bbox)*page.rotation_matrix)+(-3,-3,3,3)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(1.2,1.2), clip=clip, alpha=False)
                self.picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(pixmap.tobytes('png'))))
            self.message.set_text(_('table_style_hint'))
        except Exception as error:
            self.picture.set_paintable(None)
            self.message.set_text(str(error))

    def apply_style(self):
        updated = self.updated_states()
        return self.menu.execute(EditTableCommand(self.owner, self.table.objects, self.initial, updated))
