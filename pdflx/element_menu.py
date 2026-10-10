"""Fast, undoable context actions for canvas objects, tables, forms, and annotations."""
import copy
import uuid
import pymupdf as fitz
import gi
gi.require_version('Gtk','4.0')
gi.require_version('Gdk','4.0')
from gi.repository import Gtk,Gdk,GLib,GObject
from . import document_tools as tools,document_features as features
from .models import EditableText,EditableShape,EditableImage,EditableStroke
from .table_creation import TableSelection
from .undo_manager import AddObjectCommand,AddTableCommand,DeleteObjectCommand,CompositeCommand,EditObjectCommand,EditTableCommand
from .document_tool_ui import ToolDialog
from .i18n import _


def duplicate_offset(doc,page_number,rect):
    page=doc[page_number]
    visual=fitz.Rect(rect)*page.rotation_matrix
    dx=min(16,max(0,page.rect.width-visual.x1))
    dy=min(16,max(0,page.rect.height-visual.y1))
    if dx==0 and dy==0:
        dx=-min(16,max(0,visual.x0))
        dy=-min(16,max(0,visual.y0))
    matrix=page.derotation_matrix
    return matrix.a*dx+matrix.c*dy,matrix.b*dx+matrix.d*dy


def duplicate_annotation(doc,page_number,xref):
    page=doc[page_number]
    annot=page.load_annot(xref)
    dx,dy=duplicate_offset(doc,page_number,annot.rect)
    new=doc.get_new_xref()
    doc.update_object(new,doc.xref_object(xref))
    doc.xref_set_key(new,'NM',fitz.get_pdf_str(str(uuid.uuid4())))
    for key in ('Popup','IRT'):
        doc.xref_set_key(new,key,'null')
    def translate(values):
        if values and isinstance(values[0],list):
            return [translate(row) for row in values]
        return [value+(dx if index%2==0 else -dy) for index,value in enumerate(values)]
    for key in ('Rect','QuadPoints','Vertices','L','InkList','CL'):
        kind,value=doc.xref_get_key(new,key)
        if kind=='array':
            # PDF arrays use whitespace, including nested arrays for ink paths.
            import re
            tokens=re.findall(r'\[|\]|[-+]?\d*\.?\d+',value)
            stack=[]
            root=[]
            for token in tokens:
                if token=='[':
                    row=[]
                    if stack: stack[-1].append(row)
                    else: root=row
                    stack.append(row)
                elif token==']': stack.pop()
                else: stack[-1].append(float(token))
            def pdf_array(items):
                return '['+' '.join(pdf_array(item) if isinstance(item,list) else f'{item:.9f}' for item in items)+']'
            doc.xref_set_key(new,key,pdf_array(translate(root)))
    kind,value=doc.xref_get_key(page.xref,'Annots')
    if kind=='xref':
        refs=int(value.split()[0])
        array=doc.xref_object(refs)
        doc.update_object(refs,array.rstrip()[:-1]+f' {new} 0 R ]')
    else:
        doc.xref_set_key(page.xref,'Annots',value.rstrip()[:-1]+f' {new} 0 R ]')
    doc._reset_page_refs()
    return new


class ElementMenu:
    def __init__(self,window):
        self.window=window
        self.source=window.doc
        self.page=window.current_page_index

    def editable(self):
        w=self.window
        return self.source is w.doc and self.page==w.current_page_index and w.document_tools.editable()

    def target(self,x,y):
        w=self.window
        native=fitz.Point(x,y)*self.source[self.page].derotation_matrix
        field=tools.form_field_at_point(self.source,self.page,native)
        if field: return 'field',field
        page=self.source[self.page]
        for annot in reversed(list(page.annots() or ())):
            # Highlighted text keeps its text menu, which includes the highlight actions.
            if annot.type[0]==fitz.PDF_ANNOT_HIGHLIGHT and w._find_text_at_pos(x,y):
                continue
            if native in annot.rect:
                return 'annotation',dict(page=self.page,xref=annot.xref,kind=annot.type[1],
                    content=annot.info.get('content',''),author=annot.info.get('title',''),rect=tuple(annot.rect))
        table=w._find_table_at_pos(x,y)
        if table: return 'table',table
        for find in (w._find_stroke_at_pos,w._find_image_at_pos,w._find_text_at_pos,w._find_shape_at_pos):
            obj=find(x,y)
            if obj: return 'object',obj
        return None

    def open(self,x,y):
        w=self.window
        if not self.editable(): return False
        px=(x-max(0,(w.pdf_view.get_width()-w.current_pdf_page_width)/2))/w.zoom_level
        py=(y-max(0,(w.pdf_view.get_height()-w.current_pdf_page_height)/2))/w.zoom_level
        target=self.target(px,py)
        if not target: return False
        kind,obj=target
        interaction=getattr(getattr(w,'form_tools',None),'interaction',None)
        if interaction:
            if kind=='field':interaction.select(obj,keep=True)
            else:interaction.selected=None
        if kind=='table': w._select_table(obj)
        elif kind=='object':
            w.stamp_interaction.cancel()
            w.selected_text=obj if isinstance(obj,EditableText) else None
            w.selected_shape=obj if isinstance(obj,EditableShape) else None
            w.selected_image=obj if isinstance(obj,EditableImage) else None
            w.selected_stroke=obj if isinstance(obj,EditableStroke) else None
            w.selected_table=None
            w.word_selection_mode=False
        elif kind=='annotation' and obj['kind']=='Stamp': w.stamp_interaction.select(obj)
        w.pdf_view.queue_draw()
        old=getattr(w,'context_popover',None)
        if old and old.get_parent(): old.popdown()
        if old and getattr(old,'_element_menu',False) and old.get_parent() is w.pdf_view:
            popover=old
            focus=w.get_focus()
            if focus and (focus is popover or focus.is_ancestor(popover)):
                w.set_focus(None)
        else:
            popover=Gtk.Popover(autohide=True,has_arrow=True)
            popover._element_menu=True
            popover.set_parent(w.pdf_view)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3,
                    margin_start=6,margin_end=6,margin_top=6,margin_bottom=6)
        def button(label,callback,destructive=False):
            btn=Gtk.Button(label=_(label))
            btn.add_css_class('flat')
            if destructive: btn.add_css_class('destructive-action')
            def activate(_button):
                popover.popdown()
                if self.editable(): callback(kind,obj)
            btn.connect('clicked',activate)
            box.append(btn)
            return btn
        if kind=='object' and isinstance(obj,EditableText):
            button('menu_edit_text',lambda _kind,text:w._show_inline_editor(text))
            copy_button=button('btn_copy',lambda _kind,text:w.get_clipboard().set(text.text))
            copy_button.set_sensitive(w._active_session.can_copy)
            w._append_highlight_menu(box,lambda label,callback:button(label,lambda _kind,_obj:callback()),px,py)
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        if kind=='annotation' and obj['kind']=='Highlight':
            w._append_highlight_menu(box,lambda label,callback:button(label,lambda _kind,_obj:callback()),px,py,
                                     include_highlight=False,include_delete=False)
            box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
        if kind=='object' and isinstance(obj,EditableImage):
            button('Edit Image…', self.image_editor)
        if kind=='table':
            copy_button=button('table_copy',lambda _kind,table:w.copy_table(table))
            copy_button.set_sensitive(w._active_session.can_copy)
            button('table_style', self.table_style)
        if kind=='object':
            from .layering import restack
            button('element_bring_front',lambda _kind,item:restack(w,item,True))
            button('element_send_back',lambda _kind,item:restack(w,item,False))
        button('element_properties',self.properties)
        duplicate=button('element_duplicate',self.duplicate)
        delete=button('delete_confirm',self.delete,True)
        if kind=='field' and obj['type']==fitz.PDF_WIDGET_TYPE_SIGNATURE:
            duplicate.set_sensitive(not obj.get('signed'))
            delete.set_sensitive(False)
        popover.set_child(box)
        rect=Gdk.Rectangle();rect.x=int(x);rect.y=int(y);rect.width=1;rect.height=1
        popover.set_pointing_to(rect)
        w.context_popover=popover
        # Keep the native popover alive while Wayland delivers surface events.
        # Reuse it instead of destroying its widget on each menu dismissal.
        popover.popup()
        return True

    def execute(self,command):
        self.window.commit_pending_format_change()
        if command.execute() is False: return False
        self.window.undo_manager.add_command(command)
        self.window.document_modified=True
        self.window._update_ui_state()
        self.window.pdf_view.queue_draw()
        return True

    def table_style(self, kind, table):
        if not self.editable():
            return
        from .table_style_dialog import TableStyleDialog
        TableStyleDialog(self, table).present()

    def delete(self,kind,obj):
        if not self.editable(): return
        w=self.window
        if kind=='annotation':
            w._mutate_document(lambda:tools.delete_annotation(self.source,self.page,obj['xref']))
        elif kind=='field':
            w._mutate_document(lambda:tools.delete_form_field(self.source,self.page,obj['xref']))
        else:
            objects=obj.objects if kind=='table' else [obj]
            self.execute(CompositeCommand(w,[DeleteObjectCommand(w,item) for item in objects]))
        w.selected_text=w.selected_image=w.selected_shape=w.selected_stroke=w.selected_table=None
        w.stamp_interaction.cancel()

    def duplicate(self,kind,obj):
        if not self.editable(): return
        w=self.window
        w.commit_pending_format_change()
        if kind=='annotation':
            return w._mutate_document(lambda:duplicate_annotation(self.source,self.page,obj['xref']))
        if kind=='field':
            if hasattr(w,'form_tools'):
                return w.form_tools.duplicate_field(obj)
            from .form_duplication import duplicate_form_field
            return w._mutate_document(lambda:duplicate_form_field(self.source,self.page,obj['xref']),page_num=self.page)
        objects=obj.objects if kind=='table' else [obj]
        dx,dy=duplicate_offset(self.source,self.page,obj.bbox)
        copies=[]
        table_id=str(uuid.uuid4())
        for item in objects:
            clone=copy.deepcopy(item)
            clone.is_new=True;clone.is_baked=True;clone._ghost_redacted=False
            clone.original_bbox=None;clone.original_text=''
            clone.bbox=tuple(value+delta for value,delta in zip(item.bbox,(dx,dy,dx,dy)))
            clone.x=clone.bbox[0];clone.y=clone.bbox[1]
            if isinstance(clone,EditableText): clone.baseline+=dy
            if isinstance(clone,EditableStroke): clone.points=[(x+dx,y+dy) for x,y in clone.points]
            if kind=='table': clone.table_id=table_id
            elif hasattr(clone,'table_id'): del clone.table_id
            copies.append(clone)
        self.execute(AddTableCommand(w,copies) if kind=='table' else AddObjectCommand(w,copies[0]))

    def image_editor(self,kind,obj):
        if not self.editable(): return
        self.window.image_tools.open(obj)

    def properties(self,kind,obj):
        if not self.editable(): return
        w=self.window
        if kind=='annotation': return w.document_tools.edit_comment(obj)
        if kind=='field': return w.form_tools.edit_field(obj)
        original=[copy.deepcopy(item.__dict__) for item in obj.objects] if kind=='table' else copy.deepcopy(obj.__dict__)
        x0,y0,x1,y1=obj.bbox
        def apply():
            bounds=(x.get_value(),y.get_value(),x.get_value()+width.get_value(),y.get_value()+height.get_value())
            if kind=='table':
                obj.transform(original,obj.bbox,bounds)
                updated=[copy.deepcopy(item.__dict__) for item in obj.objects]
                for item,state in zip(obj.objects,original): item.__dict__.update(state)
                return self.execute(EditTableCommand(w,obj.objects,original,updated))
            updated=copy.deepcopy(original)
            updated.update(x=bounds[0],y=bounds[1],bbox=bounds,rotation=angle.get_value()%360)
            if isinstance(obj,EditableText):
                updated['font_size']=size.get_value()
                updated['baseline']=bounds[1]+(original['baseline']-y0)*(size.get_value()/original['font_size'])
                updated['font_family_base']=family.get_text()
                updated.update(is_bold=bold.get_active(),is_italic=italic.get_active(),
                               is_underline=underline.get_active(),is_strikethrough=strikeout.get_active())
            if isinstance(obj,EditableStroke):
                obj.scale_to_bbox(bounds,original['bbox'],original['points'])
                updated['points']=copy.deepcopy(obj.points)
                obj.__dict__.update(original)
            if color:
                rgba=color.get_rgba()
                updated['color' if isinstance(obj,EditableText) else 'stroke_color']=(rgba.red,rgba.green,rgba.blue)
            if stroke: updated['stroke_width']=stroke.get_value()
            if fill:
                rgba=fill.get_rgba()
                updated['fill_color']=(rgba.red,rgba.green,rgba.blue)
                updated['is_transparent']=transparent.get_active()
            return self.execute(EditObjectCommand(w,obj,original,updated))
        dialog=ToolDialog(w,_('element_properties'),apply)
        def unit(widget,text):
            widget.set_width_chars(7)
            suffix=Gtk.Label(label=text)
            suffix.add_css_class('dim-label')
            widget.get_parent().append(suffix)
            return widget
        dialog.section(_('props_position'))
        x=unit(dialog.spin(_('props_x'),x0,-100000,100000),'pt')
        y=unit(dialog.spin(_('props_y'),y0,-100000,100000),'pt')
        dialog.section(_('props_size'))
        width=unit(dialog.spin(_('props_width'),x1-x0,1,100000),'pt')
        height=unit(dialog.spin(_('props_height'),y1-y0,1,100000),'pt')
        angle=None
        if kind!='table':
            angle=unit(dialog.spin(_('props_rotation'),getattr(obj,'rotation',0),-360,360),'°')
        color=stroke=fill=None
        if isinstance(obj,EditableText):
            dialog.section(_('props_text'))
            family=dialog.entry(_('tool_stamp_font'),obj.font_family_base)
            size=unit(dialog.spin(_('props_font_size'),obj.font_size,3,4096),'pt')
            styles=Gtk.Box(spacing=0)
            styles.add_css_class('linked')
            def style_toggle(icon,tip,active):
                button=Gtk.ToggleButton(icon_name=icon,tooltip_text=tip,active=active)
                styles.append(button)
                return button
            bold=style_toggle('format-text-bold-symbolic',_('tool_stamp_bold'),obj.is_bold)
            italic=style_toggle('format-text-italic-symbolic',_('element_italic'),obj.is_italic)
            underline=style_toggle('format-text-underline-symbolic',_('element_underline'),obj.is_underline)
            strikeout=style_toggle('format-text-strikethrough-symbolic',_('element_strikeout'),obj.is_strikethrough)
            dialog.field(_('props_style'),styles)
        if isinstance(obj,(EditableText,EditableShape,EditableStroke)):
            dialog.section(_('props_appearance'))
            color=Gtk.ColorButton();rgba=Gdk.RGBA()
            rgba.red,rgba.green,rgba.blue=getattr(obj,'color',getattr(obj,'stroke_color',(0,0,0)))
            rgba.alpha=1;color.set_rgba(rgba)
            dialog.field(_('tool_stamp_color') if isinstance(obj,EditableText) else _('props_stroke_color'),color)
        if isinstance(obj,(EditableShape,EditableStroke)):
            stroke=unit(dialog.spin(_('props_stroke_width'),obj.stroke_width,0.1,1000),'pt')
        if isinstance(obj,EditableShape):
            fill=Gtk.ColorButton();rgba=Gdk.RGBA()
            rgba.red,rgba.green,rgba.blue=obj.fill_color;rgba.alpha=1
            fill.set_rgba(rgba)
            dialog.field(_('element_fill'),fill)
            transparent=dialog.check(_('element_no_fill'),obj.is_transparent)
            transparent.bind_property('active',fill,'sensitive',
                                      GObject.BindingFlags.SYNC_CREATE|GObject.BindingFlags.INVERT_BOOLEAN)
        dialog.present()
