"""Drag a visible certificate signature rectangle directly on the document."""
import pymupdf as fitz


class CertificatePlacement:
    def __init__(self,window,dialog):
        self.window,self.dialog,self.source=window,dialog,window.doc
        self.drag_start=None
        self.rect=None
        self.png=dialog.preview_png

    def begin(self,x,y):
        w=self.window
        if w.doc is not self.source or w.view_mode:return False
        if fitz.Point(x,y) not in w.doc[w.current_page_index].rect:return False
        self.page=w.current_page_index
        self.drag_start=(x,y)
        self.rect=fitz.Rect(x,y,x,y)
        w.dragging_to_create=True
        return True

    def update(self,dx,dy):
        if self.drag_start is None:return
        if self.window.doc is not self.source or self.source.is_closed or self.window.current_page_index!=self.page:
            self.cancel(False);return
        x,y=self.drag_start
        visible=self.source[self.page].rect
        endx=max(0,min(visible.width,x+dx));endy=max(0,min(visible.height,y+dy))
        self.rect=fitz.Rect(min(x,endx),min(y,endy),max(x,endx),max(y,endy))
        self.window.pdf_view.queue_draw()

    def end(self,dx,dy):
        if self.drag_start is None:return
        self.update(dx,dy)
        if self.drag_start is None:return
        w=self.window
        if w.doc is not self.source or self.page!=w.current_page_index:
            self.cancel(False);return
        page=self.source[self.page]
        if self.rect.width<80 or self.rect.height<24:
            x,y=self.drag_start
            width=min(240,page.rect.width);height=min(75,page.rect.height)
            x=min(x,page.rect.width-width);y=min(y,page.rect.height-height)
            self.rect=fitz.Rect(x,y,x+width,y+height)
        placement=dict(page=self.page,rect=tuple(self.rect*page.derotation_matrix))
        w._certificate_placement=None
        w.dragging_to_create=False
        self.drag_start=None
        w.on_tool_selected(None,'select')
        self.dialog.accept_placement(placement)
        w.pdf_view.queue_draw()

    def draw(self,cr):
        if not self.rect or self.rect.is_empty or self.window.doc is not self.source:return
        if self.window.current_page_index!=self.page:return
        import gi
        gi.require_version('GdkPixbuf','2.0')
        from gi.repository import Gdk,GdkPixbuf
        # The canvas context is already in native page coordinates.
        page=self.source[self.page]
        cr.save()
        m=page.derotation_matrix
        import cairo
        cr.transform(cairo.Matrix(m.a,m.b,m.c,m.d,m.e,m.f))
        loader=GdkPixbuf.PixbufLoader.new();loader.write(self.png);loader.close()
        pix=loader.get_pixbuf()
        cr.translate(self.rect.x0,self.rect.y0)
        cr.scale(self.rect.width/pix.get_width(),self.rect.height/pix.get_height())
        Gdk.cairo_set_source_pixbuf(cr,pix,0,0);cr.paint_with_alpha(.9)
        cr.restore()

    def cancel(self,reopen=True):
        w=self.window
        w._certificate_placement=None
        w.dragging_to_create=False
        self.drag_start=None
        if w.tool_mode=='certificate_signature':
            w.tool_mode='select'
            if hasattr(w,'_update_cursor_for_tool'):w._update_cursor_for_tool()
        w.pdf_view.queue_draw()
        if reopen and w.doc is self.source:
            self.dialog.present()
        else:self.dialog.close()
