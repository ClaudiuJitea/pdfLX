"""On-page form field selection, movement and resizing with one commit per drag."""
import pymupdf as fitz
from . import document_features as features,document_tools as tools


class FormInteraction:
    def __init__(self,controller):
        self.controller=controller;self.window=controller.window
        # group keeps the selection order; group[0] is the reference field
        # that alignment and sizing follow, selected the one with handles.
        self._selected=None;self.group=[];self.source=None;self.drag=None;self.guides=[]

    @property
    def selected(self):
        return self._selected

    @selected.setter
    def selected(self,xref):
        self._selected=xref
        if xref is None:self.group=[]
        elif xref not in self.group:self.group=[xref]
        bar=getattr(self.controller,'arrange_bar',None)
        if bar:bar.request_sync()

    def _live(self):
        if not self.group or self.source is not self.window.doc or not self.controller.editable():return {}
        return {f['xref']:f for f in features.list_form_fields(self.source,[self.window.current_page_index])
                if f['xref'] in self.group and not f.get('signed') and f['type']!=fitz.PDF_WIDGET_TYPE_SIGNATURE}

    def current(self):
        if not self.selected or self.source is not self.window.doc or not self.controller.editable():return None
        return next((f for f in features.list_form_fields(self.source,[self.window.current_page_index])
                     if f['xref']==self.selected and not f.get('signed')),None)

    def fields(self):
        """Selected fields on the current page, reference field first."""
        live=self._live()
        return [live[xref] for xref in self.group if xref in live]

    def select(self,field,extend=False,keep=False):
        if self.source is not self.window.doc:self.group=[]
        self.source=self.window.doc
        if extend and field['xref'] in self.group and len(self.group)>1:
            self.group.remove(field['xref'])
            self.selected=self.group[-1]
        elif extend:
            self.group=[xref for xref in self.group if xref in self._live()]+[field['xref']]
            self.selected=field['xref']
        elif keep and field['xref'] in self.group:
            self.selected=field['xref']
        else:
            self.group=[];self.selected=field['xref']
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
        matrix=self.source[field['page']].rotation_matrix
        others=[f for f in self.fields() if f['xref']!=field['xref']] if handle=='move' else []
        self.drag=dict(field=field,handle=handle,source=self.source,
                       bounds=fitz.Rect(field['rect'])*matrix,
                       others=[(f,fitz.Rect(f['rect'])*matrix) for f in others])
        self.preview=fitz.Rect(self.drag['bounds']);self.offset=(0,0)
        self.window.dragging_to_create=True
        return True

    def update(self,dx,dy):
        if not self.drag or self.source is not self.window.doc:return
        rect=fitz.Rect(self.drag['bounds']);handle=self.drag['handle']
        page=self.source[self.drag['field']['page']].rect
        if handle=='move':
            # Moving a group stops when any of its fields reaches the page edge.
            union=fitz.Rect(rect)
            for _field,bounds in self.drag['others']:union|=bounds
            dx=max(-union.x0,min(dx,page.width-union.x1));dy=max(-union.y0,min(dy,page.height-union.y1))
            rect+= (dx,dy,dx,dy)
            snapped,self.guides=self._snap(rect,move=True)
            if snapped!=rect:
                sx,sy=snapped.x0-rect.x0,snapped.y0-rect.y0
                if (union+(dx+sx,dy+sy,dx+sx,dy+sy)) in page:
                    dx,dy=dx+sx,dy+sy;rect=snapped
            self.offset=(dx,dy)
        else:
            if 'w' in handle:rect.x0=max(0,min(rect.x0+dx,rect.x1-10))
            if 'e' in handle:rect.x1=min(page.width,max(rect.x1+dx,rect.x0+10))
            if 'n' in handle:rect.y0=max(0,min(rect.y0+dy,rect.y1-10))
            if 's' in handle:rect.y1=min(page.height,max(rect.y1+dy,rect.y0+10))
            edges=[edge for letter,edge in (('w','x0'),('e','x1'),('n','y0'),('s','y1')) if letter in handle]
            snapped,self.guides=self._snap(rect,edges=edges)
            if snapped.width>=10 and snapped.height>=10 and snapped in page:rect=snapped
        self.preview=rect;self.window.pdf_view.queue_draw()

    def _snap(self,rect,move=False,edges=()):
        from .form_builder import style,snap_rect
        if not style()['snap']:return rect,[]
        page=self.source[self.drag['field']['page']]
        skip={self.drag['field']['xref']}|{f['xref'] for f,_b in self.drag['others']}
        others=[fitz.Rect(f['rect'])*page.rotation_matrix
                for f in features.list_form_fields(self.source,[self.drag['field']['page']]) if f['xref'] not in skip]
        return snap_rect(rect,others,5/self.window.zoom_level,edges=edges,move=move,page=page.rect)

    def end(self,dx,dy):
        if not self.drag:return
        self.update(dx,dy);drag=self.drag;self.drag=None;self.guides=[]
        self.window.dragging_to_create=False
        if drag['source'] is not self.window.doc or not self.controller.editable():return
        field=drag['field'];derotate=self.source[field['page']].derotation_matrix
        native=self.preview*derotate
        if all(abs(a-b)<.01 for a,b in zip(native,field['rect'])):return
        dx,dy=getattr(self,'offset',(0,0)) if drag['others'] else (0,0)
        moves=[(field,native)]+[(other,(bounds+(dx,dy,dx,dy))*derotate) for other,bounds in drag['others']]
        source=self.source
        def mutate():
            for item,rect in moves:
                tools.edit_form_field(source,item['page'],item['xref'],item['name'],item['required'],
                                      item['max_length'],rect=rect)
        self.window._mutate_document(mutate,page_num=field['page'])

    def draw(self,cr):
        field=self.current()
        if not field or field['type']==fitz.PDF_WIDGET_TYPE_SIGNATURE:return
        page=self.source[field['page']]
        rect=self.preview*page.derotation_matrix if self.drag else fitz.Rect(field['rect'])
        cr.save();cr.set_source_rgb(.1,.45,.9);cr.set_line_width(1.5/self.window.zoom_level)
        others=self.fields()
        if len(others)>1:
            moving={f['xref']:bounds for f,bounds in (self.drag or {}).get('others',())}
            dx,dy=getattr(self,'offset',(0,0)) if moving and self.drag['handle']=='move' else (0,0)
            for index,other in enumerate(others):
                if other['xref']==field['xref']:continue
                box=fitz.Rect(other['rect'])
                if other['xref'] in moving:box=(moving[other['xref']]+(dx,dy,dx,dy))*page.derotation_matrix
                cr.rectangle(box.x0,box.y0,box.width,box.height);cr.stroke()
            # Mark the reference field that alignment and sizing follow.
            key=fitz.Rect(others[0]['rect'])
            if others[0]['xref'] in moving:key=(moving[others[0]['xref']]+(dx,dy,dx,dy))*page.derotation_matrix
            elif others[0]['xref']==field['xref']:key=rect
            gap=3/self.window.zoom_level
            cr.save();cr.set_dash([4/self.window.zoom_level,3/self.window.zoom_level])
            cr.rectangle(key.x0-gap,key.y0-gap,key.width+2*gap,key.height+2*gap);cr.stroke();cr.restore()
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
