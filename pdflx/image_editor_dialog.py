"""Selection-aware image tools, applied directly to the document canvas."""
import copy
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk, Gdk, GLib, GObject
import pymupdf as fitz
from .ui_components import show_open_file_dialog
from .undo_manager import EditObjectCommand
from . import image_editing
from .i18n import _


class ImageEditorPanel(Gtk.Box):
    def __init__(self, menu, image):
        self.menu, self.image = menu, image
        self.initial = dict(image_editing.settings(image), **copy.deepcopy(image.__dict__))
        self.pending_bytes = image.image_bytes
        self.loading = True
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class('pdflx-inspector-body')
        self.owner, self.source = menu.window, menu.source
        self.timer=None
        self.committing=False
        self.keep_ratio=False
        self.controls={}
        values=image_editing.settings(image)

        # Header: the same title + close layout as every other inspector.
        header=Gtk.Box(spacing=10)
        header.add_css_class('pdflx-inspector-header')
        badge=Gtk.Image(icon_name='editor-image-symbolic',valign=Gtk.Align.CENTER)
        badge.add_css_class('pdflx-inspector-badge')
        header.append(badge)
        titles=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,hexpand=True,valign=Gtk.Align.CENTER)
        title=Gtk.Label(label=_('image_tools_title'),xalign=0)
        title.add_css_class('heading')
        x0,y0,x1,y1=image.bbox
        self.size_label=Gtk.Label(label=f'{x1-x0:.0f} × {y1-y0:.0f} pt',xalign=0)
        self.size_label.add_css_class('caption')
        self.size_label.add_css_class('dim-label')
        titles.append(title);titles.append(self.size_label)
        header.append(titles)
        close=Gtk.Button(icon_name='window-close-symbolic',tooltip_text=_('image_tools_close'),valign=Gtk.Align.CENTER)
        close.add_css_class('flat');close.add_css_class('circular')
        close.connect('clicked',lambda *_:self.owner.image_tools.close())
        header.append(close)
        self.append(header)

        # Quick actions, grouped by purpose.
        toolbar=Gtk.Box(spacing=6,halign=Gtk.Align.FILL)
        toolbar.add_css_class('pdflx-inspector-toolbar')
        def group(*buttons):
            box=Gtk.Box()
            box.add_css_class('linked')
            for button in buttons:box.append(button)
            toolbar.append(box)
        def action(icon,tip,callback):
            button=Gtk.Button(icon_name=icon,tooltip_text=tip)
            button.connect('clicked',callback)
            return button
        def flip(key,icon,tip):
            button=Gtk.ToggleButton(icon_name=icon,tooltip_text=tip,active=values[key])
            button.connect('toggled',self.update_preview)
            self.controls[key]=button
            return button
        group(action('editor-rotate-left-symbolic',_('image_rotate_left'),lambda *_:self.turn(-90)),
              action('editor-rotate-right-symbolic',_('image_rotate_right'),lambda *_:self.turn(90)))
        group(flip('flip_horizontal','editor-flip-horizontal-symbolic',_('image_flip_horizontal')),
              flip('flip_vertical','editor-flip-vertical-symbolic',_('image_flip_vertical')))
        group(action('editor-proportions-symbolic',_('image_restore_ratio'),self.restore_ratio),
              action('editor-undo-symbolic',_('image_reset'),self.reset))
        spacer=Gtk.Box(hexpand=True);toolbar.append(spacer)
        replace=Gtk.Button(tooltip_text=_('image_replace'))
        replace_content=Gtk.Box(spacing=6)
        replace_content.append(Gtk.Image(icon_name='editor-image-symbolic'))
        replace_content.append(Gtk.Label(label=_('image_replace_short')))
        replace.set_child(replace_content)
        replace.connect('clicked',self.replace)
        toolbar.append(replace)
        self.append(toolbar)

        content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        content.add_css_class('pdflx-inspector-content')
        def section(text):
            label=Gtk.Label(label=text,xalign=0)
            label.add_css_class('pdflx-section-title')
            if content.get_first_child() is not None:label.set_margin_top(8)
            content.append(label)
            rows=Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            rows.add_css_class('boxed-list')
            content.append(rows)
            return rows
        def number(value,low,high,step=1,digits=0):
            widget=Gtk.SpinButton.new_with_range(low,high,step)
            widget.set_digits(digits)
            widget.set_value(value)
            widget.set_width_chars(4)
            widget.set_valign(Gtk.Align.CENTER)
            widget.connect('value-changed',self.update_preview)
            return widget
        def row(rows,label,widget,unit=None,parent=None):
            item=Adw.ActionRow(title=label,use_markup=False)
            item.add_suffix(widget)
            if unit:
                suffix=Gtk.Label(label=unit,width_chars=2,xalign=0)
                suffix.add_css_class('dim-label')
                item.add_suffix(suffix)
            (parent.add_row if parent else rows.append)(item)
            return widget

        look=section(_('image_section_appearance'))
        scale=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,0,100,1)
        scale.set_draw_value(False);scale.set_hexpand(True);scale.set_valign(Gtk.Align.CENTER)
        scale.set_size_request(110,-1)
        scale.set_value(values['opacity']*100)
        scale.connect('value-changed',self.update_preview)
        percent=Gtk.Label(label=f"{values['opacity']*100:.0f}%",width_chars=4,xalign=1)
        percent.add_css_class('numeric')
        scale.connect('value-changed',lambda control:percent.set_text(f'{control.get_value():.0f}%'))
        opacity=Adw.ActionRow(title=_('image_opacity'),use_markup=False)
        opacity.add_suffix(scale);opacity.add_suffix(percent)
        look.append(opacity)
        self.controls['opacity']=(scale,100)
        self.angle=row(look,_('image_tilt'),number(getattr(image,'rotation',0),-360,360),'°')
        corner=row(look,_('image_corners'),number(values['corner_radius'],0,500),'pt')
        self.controls['corner_radius']=(corner,1)

        frame=section(_('image_section_border'))
        border=row(frame,_('image_border_width'),number(values['border_width'],0,50,.5,1),'pt')
        self.controls['border_width']=(border,1)
        self.color=Gtk.ColorButton(title=_('image_border_color'),valign=Gtk.Align.CENTER)
        rgba=Gdk.RGBA();rgba.red,rgba.green,rgba.blue=values['border_color'];rgba.alpha=1
        self.color.set_rgba(rgba);self.color.connect('color-set',self.update_preview)
        row(frame,_('image_border_color'),self.color)
        border.bind_property('value',self.color,'sensitive',GObject.BindingFlags.SYNC_CREATE,
                             lambda _binding,value:value>0)

        effects=section(_('image_section_effects'))
        self.crop_row=Adw.ExpanderRow(title=_('image_crop'),use_markup=False)
        self.crop_row.add_prefix(Gtk.Image(icon_name='editor-crop-symbolic'))
        self.crop=[]
        for label,value in zip((_('image_crop_left'),_('image_crop_top'),_('image_crop_right'),_('image_crop_bottom')),values['crop']):
            self.crop.append(row(None,label,number(value*100,0,98),'%',self.crop_row))
        effects.append(self.crop_row)
        self.shadow_row=Adw.ExpanderRow(title=_('image_shadow'),use_markup=False,show_enable_switch=True,
                                        enable_expansion=values['shadow'],expanded=values['shadow'])
        self.shadow_row.add_prefix(Gtk.Image(icon_name='editor-shadow-symbolic'))
        self.shadow_row.connect('notify::enable-expansion',self.update_preview)
        self.controls['shadow']=_ExpanderToggle(self.shadow_row)
        for key,label,factor,high,unit in (('shadow_offset',_('image_shadow_distance'),1,100,'pt'),
                                          ('shadow_opacity',_('image_shadow_strength'),100,100,'%')):
            control=row(None,label,number(values[key]*factor,0,high),unit,self.shadow_row)
            self.controls[key]=(control,factor)
        effects.append(self.shadow_row)

        self.message=Gtk.Label(wrap=True,xalign=0,visible=False)
        self.message.add_css_class('error');self.message.add_css_class('caption')
        content.append(self.message)
        self.scroller=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,propagate_natural_height=True,
                                         max_content_height=560,child=content)
        self.append(self.scroller)
        self.loading=False
        self.update_indicators()

    def update_indicators(self):
        cropped=any(control.get_value() for control in self.crop)
        self.crop_row.set_subtitle(_('image_crop_active') if cropped else '')
        x0,y0,x1,y1=self.image.bbox
        self.size_label.set_text(f'{x1-x0:.0f} × {y1-y0:.0f} pt')

    def error(self,error):
        self.message.set_text(str(error))
        self.message.set_visible(bool(error))

    def state(self):
        updated=copy.deepcopy(self.image.__dict__)
        updated.update(image_bytes=self.pending_bytes,rotation=self.angle.get_value()%360,
                       crop=tuple(control.get_value()/100 for control in self.crop))
        for key,control in self.controls.items():
            updated[key]=control[0].get_value()/control[1] if isinstance(control,tuple) else control.get_active()
        rgba=self.color.get_rgba()
        updated['border_color']=(rgba.red,rgba.green,rgba.blue)
        if self.keep_ratio:
            pix=fitz.Pixmap(self.pending_bytes)
            left,top,right,bottom=updated['crop']
            x0,y0,x1,y1=updated['bbox']
            height=(x1-x0)*pix.height*(1-top-bottom)/(pix.width*(1-left-right))
            cy=(y0+y1)/2
            updated['bbox']=(x0,cy-height/2,x1,cy+height/2)
        return updated

    def clone(self):
        clone=copy.deepcopy(self.image)
        clone.__dict__.update(self.state())
        image_editing.validate(clone)
        return clone

    def update_preview(self,*args):
        if self.loading:return
        try:
            self.clone()
            self.error('')
            self.update_indicators()
            if self.timer:GLib.source_remove(self.timer)
            self.timer=GLib.timeout_add(180,self.commit)
        except Exception as error:
            if self.timer:GLib.source_remove(self.timer);self.timer=None
            self.error(error)

    def commit(self):
        self.timer=None
        if not self.menu.editable() or self.image not in self.owner.editable_images:return GLib.SOURCE_REMOVE
        self.committing=True
        try:
            updated=self.clone().__dict__
            old=dict(image_editing.settings(self.image),**copy.deepcopy(self.image.__dict__))
            if updated!=old:
                self.menu.execute(EditObjectCommand(self.owner,self.image,old,updated))
            self.initial=copy.deepcopy(self.image.__dict__)
        except Exception as error:self.error(error)
        finally:self.committing=False
        return GLib.SOURCE_REMOVE

    def finish(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer=None
            self.commit()

    def turn(self,amount):
        self.angle.set_value((self.angle.get_value()+amount)%360)

    def restore_ratio(self,*args):
        self.keep_ratio=True
        self.update_preview()

    def replace(self,*args):
        filter=Gtk.FileFilter(name='Images')
        for mime in ('image/png','image/jpeg','image/webp','image/tiff','image/bmp'):
            filter.add_mime_type(mime)
        def chosen(file):
            if not file or not self.get_visible() or not self.menu.editable() or self.owner.image_tools.panel is not self:return
            try:
                with open(file.get_path(),'rb') as source:
                    data=source.read()
                fitz.Pixmap(data)  # Validate before changing the pending source.
                self.pending_bytes=data
                self.update_preview()
            except Exception as error:self.error(error)
        show_open_file_dialog(self.owner,_('image_replace'),filters=[filter],callback=chosen)

    def reset(self,*args):
        self.loading=True
        for control in self.crop:control.set_value(0)
        for key,control in self.controls.items():
            value=image_editing.DEFAULTS[key]
            if isinstance(control,tuple):control[0].set_value(value*control[1])
            else:control.set_active(value)
        rgba=Gdk.RGBA();rgba.red=rgba.green=rgba.blue=0;rgba.alpha=1
        self.color.set_rgba(rgba)
        self.loading=False
        self.update_preview()

class _ExpanderToggle:
    """CheckButton-like view of an ExpanderRow's enable switch."""
    def __init__(self,row):self.row=row
    def get_active(self):return self.row.get_enable_expansion()
    def set_active(self,value):self.row.set_enable_expansion(bool(value))


class ImageToolsController:
    """Selection-aware, non-modal tools anchored to the viewport's right edge."""
    def __init__(self,window,surface):
        self.window=window
        self.surface=surface
        self.frame=Adw.Bin(halign=Gtk.Align.END,valign=Gtk.Align.START,
                           margin_top=12,margin_end=16,visible=False)
        self.frame.add_css_class('pdflx-inspector')
        self.frame.set_size_request(320,-1)
        surface.add_overlay(self.frame)
        self.panel=None
        self.hidden_target=None
        self.syncing=False

    def target(self):
        w=self.window
        if not w.doc or w.view_mode or not w.document_tools.editable():return None
        image=w.selected_image
        if image is None or image.page_number!=w.current_page_index:return None
        return w.doc,w.current_page_index,image

    def sync(self):
        if self.syncing:return GLib.SOURCE_REMOVE
        self.syncing=True
        try:
            target=self.target()
            button=self.window.image_tools_button
            button.set_visible(bool(self.window.doc and not self.window.view_mode))
            button.set_sensitive(target is not None)
            if self.panel and self.panel.committing:return GLib.SOURCE_REMOVE
            old=(self.panel.source,self.panel.menu.page,self.panel.image) if self.panel else None
            if target!=old:
                if self.panel:self.panel.finish()
                self.frame.set_child(None);self.panel=None
                if target:
                    from .element_menu import ElementMenu
                    self.panel=ImageEditorPanel(ElementMenu(self.window),target[2])
                    self.frame.set_child(self.panel)
            elif self.panel and self.panel.initial!=self.panel.image.__dict__ and not self.panel.timer:
                # Moving, resizing, undo and redo also refresh the controls.
                self.panel.finish()
                from .element_menu import ElementMenu
                self.panel=ImageEditorPanel(ElementMenu(self.window),target[2])
                self.frame.set_child(self.panel)
            visible=bool(target and target!=self.hidden_target)
            scroller=getattr(self.panel,'scroller',None)
            if scroller:
                # Keep the floating card inside the canvas on small windows.
                height=self.surface.get_height()
                scroller.set_max_content_height(max(220,height-150) if height>0 else 560)
            self.frame.set_visible(visible)
            if visible:button.add_css_class('active')
            else:button.remove_css_class('active')
        finally:self.syncing=False
        return GLib.SOURCE_REMOVE

    def open(self,image=None):
        if image is not None:self.window.selected_image=image
        self.hidden_target=None
        self.sync()

    def close(self):
        if self.panel:self.panel.finish()
        self.hidden_target=self.target()
        self.frame.set_visible(False)
        self.window.image_tools_button.remove_css_class('active')

    def toggle(self,*args):
        if self.frame.get_visible():self.close()
        else:self.open()
