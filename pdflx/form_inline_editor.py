"""In-place AcroForm value editing on the PDF canvas."""
import pymupdf as fitz
from gi.repository import Gtk,Gdk,GLib


def field_geometry(page,rect,zoom,width,height):
    visual=fitz.Rect(rect)*page.rotation_matrix
    ox=max(0,(width-page.rect.width*zoom)/2)
    oy=max(0,(height-page.rect.height*zoom)/2)
    return (round(ox+visual.x0*zoom),round(oy+visual.y0*zoom),
            max(1,round(visual.width*zoom)),max(1,round(visual.height*zoom)))


class FormInlineEditor:
    def __init__(self,controller,field):
        self.controller=controller;self.window=controller.window
        self.source=self.window.doc;self.field=field;self.closed=False
        self.key=(field['page'],field['xref'])
        self.had_draft=self.key in controller.pending
        self.previous_draft=controller.pending.get(self.key)
        self.was_modified=self.window.document_modified
        current=dict(field)
        if self.had_draft:current['value']=self.previous_draft
        self.control,self.read,signal=controller.value_control(current)
        self.frame=Gtk.Frame(halign=Gtk.Align.START,valign=Gtk.Align.START)
        self.frame.add_css_class('form-inline-editor')
        self.frame.set_child(self.control)
        if isinstance(self.control,Gtk.Entry):
            self.control.set_width_chars(1);self.control.set_max_width_chars(1)
            self.control.connect('activate',lambda *args:self.finish())
        if isinstance(self.control,Gtk.CheckButton):self.control.set_label('')
        if isinstance(self.control,Gtk.ScrolledWindow):
            self.control.set_min_content_height(0);self.control.set_max_content_height(-1)
            self.control.set_propagate_natural_height(False)
        self.window.pdf_overlay.add_overlay(self.frame)
        self.provider=Gtk.CssProvider()
        self.frame.get_style_context().add_provider(self.provider,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+1)
        self.control.get_style_context().add_provider(self.provider,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+1)
        if isinstance(self.control,Gtk.ScrolledWindow):
            self.control.get_child().get_style_context().add_provider(self.provider,Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION+1)
        getattr(self.control,'_form_signal_source',self.control).connect(signal,self.changed)
        focus=Gtk.EventControllerFocus();focus.connect('leave',self.focus_left);self.frame.add_controller(focus)
        keys=Gtk.EventControllerKey();keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed',self.key_pressed);self.frame.add_controller(keys)
        self.resize_handler=self.window.pdf_view.connect('resize',lambda *args:self.reposition())
        self.reposition()
        self.focus_source=GLib.idle_add(self.focus)

    def focus(self):
        self.focus_source=None
        if not self.closed:
            child=self.control.get_child() if isinstance(self.control,Gtk.ScrolledWindow) else self.control
            child.grab_focus()
        return GLib.SOURCE_REMOVE

    def reposition(self):
        if self.closed or self.source is not self.window.doc:return
        page=self.source[self.field['page']]
        x,y,width,height=field_geometry(page,self.field['rect'],self.window.zoom_level,
            self.window.pdf_view.get_allocated_width(),self.window.pdf_view.get_allocated_height())
        self.frame.set_margin_start(max(0,x));self.frame.set_margin_top(max(0,y))
        self.frame.set_size_request(width,height)
        size=max(6,(self.field.get('font_size') or 11)*self.window.zoom_level)
        self.provider.load_from_data((f'.form-inline-editor {{ border: 1px solid #3584e4; border-radius: 2px; background: white; }} '
            f'.form-inline-editor entry, .form-inline-editor textview, .form-inline-editor textview text {{ '
            f'font-size: {size:.1f}px; color: #202020; background: white; min-width: 0; min-height: 0; padding: 1px 3px; border: none; box-shadow: none; }}').encode())

    def changed(self,*args):
        if self.closed:return
        c=self.controller
        c.pending[self.key]=self.read()
        if c.source is self.source:self.source.editor_form_drafts=dict(c.pending)
        self.window.document_modified=True
        c.save.set_sensitive(True)
        self.window._update_ui_state()

    def focus_left(self,*args):
        # Run after a dropdown popover/focus transfer settles.
        GLib.idle_add(self.finish_if_unfocused)

    def finish_if_unfocused(self):
        if not self.closed:
            focus=self.window.get_focus()
            if not focus or not (focus is self.frame or focus.is_ancestor(self.frame)):
                self.finish()
        return GLib.SOURCE_REMOVE

    def key_pressed(self,controller,keyval,keycode,state):
        if state&Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_d,Gdk.KEY_D) and self.controller.editable():
            self.controller.duplicate_field(self.field);return True
        if keyval==Gdk.KEY_Escape:
            self.finish(False);return True
        if keyval in (Gdk.KEY_Return,Gdk.KEY_KP_Enter) and (not self.field.get('multiline') or state&Gdk.ModifierType.CONTROL_MASK):
            self.finish();return True
        if keyval in (Gdk.KEY_Tab,Gdk.KEY_ISO_Left_Tab):
            backwards=keyval==Gdk.KEY_ISO_Left_Tab or bool(state&Gdk.ModifierType.SHIFT_MASK)
            if self.finish():self.controller.focus_next(self.field,backwards)
            return True
        return False

    def finish(self,commit=True):
        if self.closed:return True
        self.closed=True
        c=self.controller
        if getattr(c,'inline_editor',None) is self:c.inline_editor=None
        if self.focus_source:GLib.source_remove(self.focus_source);self.focus_source=None
        self.window.pdf_view.disconnect(self.resize_handler)
        focus=self.window.get_focus()
        if focus and (focus is self.frame or focus.is_ancestor(self.frame)):self.window.set_focus(None)
        self.window.pdf_overlay.remove_overlay(self.frame)
        if not commit:
            if self.had_draft:c.pending[self.key]=self.previous_draft
            else:c.pending.pop(self.key,None)
            if self.source is self.window.doc and not c.pending:self.window.document_modified=self.was_modified
        if c.source is self.source:self.source.editor_form_drafts=dict(c.pending)
        if self.source is not self.window.doc:return True
        result=c.save_values() if commit else True
        if not commit:self.window._update_ui_state()
        return result
