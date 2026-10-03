"""Draw/import signatures and export certificate-signed copies."""

from pathlib import Path
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk

from .i18n import _
from . import signatures
from .ui_components import show_error_dialog, show_open_file_dialog, show_save_file_dialog


class VisibleSignatureDialog(Adw.Window):
    def __init__(self, parent, on_signature):
        super().__init__(transient_for=parent, modal=True, title=_("signature_add"),
                         default_width=580, resizable=False)
        self.strokes = []
        self.image_bytes = None
        self.on_signature = on_signature
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(layout)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=_("signature_add")))
        use = Gtk.Button(label=_("signature_place"))
        use.add_css_class("suggested-action")
        use.connect("clicked", self._accept)
        header.pack_end(use)
        layout.append(header)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14,
                       margin_start=24, margin_end=24, margin_top=20, margin_bottom=24)
        body.append(Gtk.Label(label=_("signature_draw_hint"), wrap=True, xalign=0))
        self.pad = Gtk.DrawingArea(content_width=520, content_height=180)
        self.pad.set_draw_func(self._draw)
        frame = Gtk.Frame()
        frame.add_css_class("signature-pad")
        self.preview = Gtk.Stack()
        self.preview.add_named(self.pad, "draw")
        self.picture = Gtk.Picture(can_shrink=True, keep_aspect_ratio=True)
        self.picture.set_size_request(-1, 180)
        self.preview.add_named(self.picture, "image")
        frame.set_child(self.preview)
        body.append(frame)
        drag = Gtk.GestureDrag()
        drag.set_button(1)
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        self.pad.add_controller(drag)
        actions = Gtk.Box(spacing=8)
        clear = Gtk.Button(label=_("signature_clear"))
        clear.connect("clicked", self._clear)
        upload = Gtk.Button(label=_("signature_import"))
        upload.connect("clicked", self._import)
        actions.append(clear)
        actions.append(upload)
        body.append(actions)
        notice = Gtk.Label(label=_("signature_visible_note"), wrap=True, xalign=0,
                           max_width_chars=60)
        notice.add_css_class("dim-label")
        body.append(notice)
        certificate = Gtk.Button(label=_("signature_digital"))
        certificate.set_halign(Gtk.Align.START)
        certificate.connect("clicked", self._open_certificate)
        body.append(certificate)
        layout.append(body)

    def _open_certificate(self, button):
        parent = self.get_transient_for()
        self.close()
        parent.on_sign_certificate()

    def _point(self, x, y):
        return (max(0, min(600, x * 600 / max(1, self.pad.get_width()))),
                max(0, min(200, y * 200 / max(1, self.pad.get_height()))))

    def _drag_begin(self, gesture, x, y):
        self.origin = (x, y)
        self.strokes.append([self._point(x, y)])
        self.pad.queue_draw()

    def _drag_update(self, gesture, dx, dy):
        if self.strokes:
            self.strokes[-1].append(self._point(self.origin[0]+dx, self.origin[1]+dy))
            self.pad.queue_draw()

    def _draw(self, area, cr, width, height):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.scale(width/600, height/200)
        cr.set_source_rgb(0.08, 0.08, 0.1)
        cr.set_line_width(2)
        cr.set_line_cap(1)
        cr.set_line_join(1)
        for stroke in self.strokes:
            if not stroke:
                continue
            cr.move_to(*stroke[0])
            for point in stroke[1:]:
                cr.line_to(*point)
            cr.stroke()

    def _clear(self, button):
        self.image_bytes = None
        self.strokes.clear()
        self.picture.set_paintable(None)
        self.preview.set_visible_child_name("draw")
        self.pad.queue_draw()

    def _import(self, button):
        filter_image = Gtk.FileFilter(name=_("signature_import"))
        for mime in ("image/png", "image/jpeg"):
            filter_image.add_mime_type(mime)

        def selected(file):
            if not file:
                return
            try:
                data = signatures.signature_image_png(Path(file.get_path()).read_bytes())
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                self.image_bytes = data
                self.picture.set_paintable(texture)
                self.preview.set_visible_child_name("image")
            except Exception as error:
                show_error_dialog(self, str(error), _("signature_add"))
        show_open_file_dialog(self, _("signature_import"), filters=[filter_image], callback=selected)

    def _accept(self, button):
        try:
            data = self.image_bytes or signatures.drawn_signature_png(self.strokes)
            self.on_signature(data)
            self.close()
        except Exception as error:
            show_error_dialog(self, str(error), _("signature_add"))


class CertificateSignatureDialog(Adw.Window):
    def __init__(self, parent, pdf_bytes, source_path, unsigned_fields, encrypted, on_saved):
        super().__init__(transient_for=parent, modal=True, title=_("signature_digital"),
                         default_width=480, resizable=False)
        self.pdf_bytes = pdf_bytes
        self.source_path = source_path
        self.on_saved = on_saved
        self.certificate_path = None
        self.source_doc=parent.doc
        self.placement=None
        self.preview_png=None
        self.busy = False
        self.connect("close-request", self._close_requested)
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(layout)
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=_("signature_digital")))
        layout.append(header)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            margin_start=24, margin_end=24, margin_top=20, margin_bottom=24)
        self.body.append(Gtk.Label(label=_("signature_digital_hint"), wrap=True,
                                   xalign=0, max_width_chars=52))
        self.certificate_button = Gtk.Button(label=_("signature_certificate"))
        self.certificate_button.connect("clicked", self._choose_certificate)
        self.body.append(self.certificate_button)
        self.password = Gtk.PasswordEntry(show_peek_icon=True)
        self._add_field(_("signature_password"), self.password)
        self.fields = [None] + unsigned_fields
        self.field = Gtk.DropDown.new_from_strings([_("signature_new_field")] + unsigned_fields)
        self._add_field(_("signature_field"), self.field)
        from .certificate_appearance import TEMPLATES
        self.template=Gtk.DropDown.new_from_strings(TEMPLATES)
        self._add_field('Signature design',self.template)
        self.preview=Gtk.Picture(can_shrink=True,content_fit=Gtk.ContentFit.CONTAIN)
        self.preview.set_size_request(360,112)
        self.body.append(self.preview)
        self.place_button=Gtk.Button(label='Place on Document…')
        self.place_button.connect('clicked',self._place)
        self.body.append(self.place_button)
        self.position=Gtk.Label(label='Drag a rectangle on the page to position and size the signature.',wrap=True,xalign=0)
        self.position.add_css_class('dim-label');self.body.append(self.position)
        self.field.connect('notify::selected',self._selection_changed)
        self.template.connect('notify::selected',self._preview)
        self.password.connect('changed',self._preview)
        self.reason = Gtk.Entry()
        self._add_field(_("signature_reason"), self.reason)
        self.reason.connect('changed',self._preview)
        self.pdf_password = Gtk.PasswordEntry(show_peek_icon=True)
        if encrypted:
            self._add_field(_("pdf_password_title"), self.pdf_password)
        self.progress = Gtk.Label(xalign=0, wrap=True)
        self.progress.add_css_class("dim-label")
        self.body.append(self.progress)
        self.sign_button = Gtk.Button(label=_("signature_save_signed"))
        self.sign_button.add_css_class("suggested-action")
        self.sign_button.set_sensitive(False)
        self.sign_button.connect("clicked", self._choose_output)
        self.body.append(self.sign_button)
        layout.append(self.body)
        self._preview()

    def _preview(self,*args):
        from .certificate_appearance import template_png,TEMPLATES
        name='Certificate holder'
        if self.certificate_path:
            try:
                from cryptography.hazmat.primitives.serialization.pkcs12 import load_key_and_certificates
                from cryptography.x509.oid import NameOID
                data=Path(self.certificate_path).read_bytes()
                _,cert,_=load_key_and_certificates(data,self.password.get_text().encode() or None)
                names=cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                if names:name=names[0].value
            except Exception:pass
        self.preview_png=template_png(name=name,reason=self.reason.get_text(),
                                      template=TEMPLATES[self.template.get_selected()])
        self.preview.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(self.preview_png)))

    def _selection_changed(self,*args):
        existing=self.field.get_selected()!=0
        self.place_button.set_sensitive(not existing)
        if existing:self.position.set_text('The existing signature field determines the position and size.')
        elif self.placement:self.position.set_text(f"Placed on page {self.placement['page']+1}. Place again to move or resize.")
        else:self.position.set_text('Drag a rectangle on the page to position and size the signature.')
        self.sign_button.set_sensitive(bool(self.certificate_path and (existing or self.placement)))

    def _place(self,*args):
        parent=self.get_transient_for()
        if parent.doc is not self.source_doc:
            self.progress.set_text('The document changed. Reopen certificate signing.');return
        parent.on_tool_selected(None,'certificate_signature')
        from .certificate_placement import CertificatePlacement
        parent._certificate_placement=CertificatePlacement(parent,self)
        self.set_visible(False)
        parent.status_label.set_text('Drag on the page to place and size the certificate signature. Escape returns to signing.')
        parent.pdf_view.grab_focus()

    def accept_placement(self,placement):
        self.placement=placement
        parent=self.get_transient_for()
        self.pdf_bytes=signatures.prepare_signing_pdf(parent.doc,self.source_path,parent.document_modified)
        self.position.set_text(f"Placed on page {placement['page']+1}. Use Place on Document to move or resize it.")
        self._selection_changed()
        self.present()

    def _add_field(self, label, control):
        self.body.append(Gtk.Label(label=label, xalign=0))
        self.body.append(control)

    def _choose_certificate(self, button):
        filter_cert = Gtk.FileFilter(name="PKCS#12 (.p12, .pfx)")
        for pattern in ("*.p12", "*.pfx", "*.P12", "*.PFX"):
            filter_cert.add_pattern(pattern)

        def selected(file):
            if file:
                self.certificate_path = file.get_path()
                self.certificate_button.set_label(Path(self.certificate_path).name)
                self._preview()
                self._selection_changed()
        show_open_file_dialog(self, _("signature_certificate"), filters=[filter_cert], callback=selected)

    def _choose_output(self, button):
        if self.get_transient_for().doc is not self.source_doc:
            self.progress.set_text('The document changed. Reopen certificate signing.');return
        if self.field.get_selected()==0 and not self.placement:
            self._place();return
        pdf_filter = Gtk.FileFilter(name="PDF")
        pdf_filter.add_pattern("*.pdf")
        name = Path(self.source_path).stem if self.source_path else "document"
        show_save_file_dialog(self, _("signature_save_signed"), initial_name=f"{name}-signed.pdf",
                              filters=[pdf_filter], callback=self._sign)

    def _sign(self, file):
        if file is None or self.busy:
            return
        path = file.get_path()
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        options = dict(password=self.password.get_text(), pdf_password=self.pdf_password.get_text(),
                       field_name=self.fields[self.field.get_selected()], reason=self.reason.get_text())
        from .certificate_appearance import TEMPLATES
        options.update(template=TEMPLATES[self.template.get_selected()],
                       placement=self.placement if self.field.get_selected()==0 else None)
        self.password.set_text("")
        self.pdf_password.set_text("")
        self.busy = True
        self.body.set_sensitive(False)
        self.progress.set_text(_("signature_signing"))

        def work():
            error_message = None
            try:
                signatures.sign_pdf_copy(self.pdf_bytes, path, self.certificate_path,
                                         source_path=self.source_path, **options)
            except Exception as error:
                error_message = str(error)
            finally:
                options.clear()
            GLib.idle_add(self._finished, path, error_message)
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, path, error):
        self.busy = False
        self.body.set_sensitive(True)
        self.progress.set_text("")
        if error:
            show_error_dialog(self, error, _("signature_digital"))
        else:
            self.on_saved(path)
            self.close()
        return GLib.SOURCE_REMOVE

    def _close_requested(self, window):
        if self.busy:
            return True
        self.password.set_text("")
        self.pdf_password.set_text("")
        return False
