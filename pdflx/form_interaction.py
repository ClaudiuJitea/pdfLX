"""On-page form field selection, movement and resizing with one commit per drag."""
import pymupdf as fitz
from . import document_features as features,document_tools as tools


class FormInteraction:
    def __init__(self,controller):
        self.controller=controller;self.window=controller.window
        self.selected=None;self.source=None;self.drag=None

    def current(self):
        if not self.selected or self.source is not self.window.doc or not self.controller.editable():return None
        return next((f for f in features.list_form_fields(self.source,[self.window.current_page_index])
                     if f['xref']==self.selected and not f.get('signed')),None)

    def select(self,field):
        self.selected=field['xref'];self.source=self.window.doc
        self.window.pdf_view.queue_draw()

    def points(self,rect):
        x0,y0,x1,y1=rect;cx=(x0+x1)/2;cy=(y0+y1)/2
        gap=5/self.window.zoom_level
        return dict(nw=(x0-gap,y0-gap),n=(cx,y0-gap),ne=(x1+gap,y0-gap),e=(x1+gap,cy),se=(x1+gap,y1+gap),
                    s=(cx,y1+gap),sw=(x0-gap,y1+gap),w=(x0-gap,cy),move=(cx,y0-20/self.window.zoom_level))

    def handle_at(self,x,y):
        field=self.current()
        if not field or field['type']==fitz.PDF_WIDGET_TYPE_SIGNATURE:return None
        rect=fitz.Rect(field['rect'])*self.source[field['page']].rotation_matrix
        if fitz.Point(x,y) in rect:return None
        tolerance=6/self.window.zoom_level
        for name,(hx,hy) in self.points(rect).items():
            if abs(x-hx)<=tolerance and abs(y-hy)<=tolerance:return name
        # The outside rim moves the field; the interior still fills/activates it.
        outer=rect+(-tolerance,-tolerance,tolerance,tolerance)
        if fitz.Point(x,y) in outer and fitz.Point(x,y) not in rect:return 'move'
        return None

    def begin(self,x,y):
        handle=self.handle_at(x,y)
        if not handle:return False
        if not self.controller.finish_inline():return False
        field=self.current()
        if not field:return False
        self.drag=dict(field=field,handle=handle,source=self.source,
                       bounds=fitz.Rect(field['rect'])*self.source[field['page']].rotation_matrix)
        self.preview=fitz.Rect(self.drag['bounds'])
        self.window.dragging_to_create=True
        return True

    def update(self,dx,dy):
        if not self.drag or self.source is not self.window.doc:return
        rect=fitz.Rect(self.drag['bounds']);handle=self.drag['handle']
        page=self.source[self.drag['field']['page']].rect
        if handle=='move':
            dx=max(-rect.x0,min(dx,page.width-rect.x1));dy=max(-rect.y0,min(dy,page.height-rect.y1))
            rect+= (dx,dy,dx,dy)
        else:
            if 'w' in handle:rect.x0=max(0,min(rect.x0+dx,rect.x1-10))
            if 'e' in handle:rect.x1=min(page.width,max(rect.x1+dx,rect.x0+10))
            if 'n' in handle:rect.y0=max(0,min(rect.y0+dy,rect.y1-10))
            if 's' in handle:rect.y1=min(page.height,max(rect.y1+dy,rect.y0+10))
        self.preview=rect;self.window.pdf_view.queue_draw()

    def end(self,dx,dy):
        if not self.drag:return
        self.update(dx,dy);drag=self.drag;self.drag=None
        self.window.dragging_to_create=False
        if drag['source'] is not self.window.doc or not self.controller.editable():return
        field=drag['field'];native=self.preview*self.source[field['page']].derotation_matrix
        if all(abs(a-b)<.01 for a,b in zip(native,field['rect'])):return
        self.window._mutate_document(lambda:tools.edit_form_field(self.source,field['page'],field['xref'],
            field['name'],field['required'],field['max_length'],rect=native),page_num=field['page'])

    def draw(self,cr):
        field=self.current()
        if not field or field['type']==fitz.PDF_WIDGET_TYPE_SIGNATURE:return
        page=self.source[field['page']]
        rect=self.preview*page.derotation_matrix if self.drag else fitz.Rect(field['rect'])
        cr.save();cr.set_source_rgb(.1,.45,.9);cr.set_line_width(1.5/self.window.zoom_level)
        cr.rectangle(rect.x0,rect.y0,rect.width,rect.height);cr.stroke()
        # Convert visual handle centres to the unrotated drawing coordinates.
        size=7/self.window.zoom_level
        for name,point in self.points(rect*page.rotation_matrix).items():
            centre=fitz.Point(point)*page.derotation_matrix
            if name=='move':
                radius=5/self.window.zoom_level
                cr.arc(centre.x,centre.y,radius,0,6.283185307)
                cr.set_source_rgb(1,1,1);cr.fill_preserve();cr.set_source_rgb(.1,.45,.9);cr.stroke()
                arm=3/self.window.zoom_level
                cr.move_to(centre.x-arm,centre.y);cr.line_to(centre.x+arm,centre.y)
                cr.move_to(centre.x,centre.y-arm);cr.line_to(centre.x,centre.y+arm);cr.stroke()
                continue
            cr.rectangle(centre.x-size/2,centre.y-size/2,size,size)
            cr.set_source_rgb(1,1,1);cr.fill_preserve();cr.set_source_rgb(.1,.45,.9);cr.stroke()
        cr.restore()
