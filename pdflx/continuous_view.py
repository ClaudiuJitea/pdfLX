"""Continuous document layout with one interactive page and visible-page rendering."""
from bisect import bisect_right
import math
import gi
gi.require_version("Gtk","4.0")
from gi.repository import Gtk,GLib
from . import pdf_handler


SCROLLING_MODES=('continuous','spread','book')


def spread_rows(count,mode):
    """Page indices per row: one per row, pairs (spread), or a lone cover then pairs (book)."""
    if mode=='spread':
        return [list(range(start,min(start+2,count))) for start in range(0,count,2)]
    if mode=='book':
        return [[0]]+[list(range(start,min(start+2,count))) for start in range(1,count,2)] if count else []
    return [[number] for number in range(count)]


class ContinuousView:
    GAP=20
    MARGIN=16

    def __init__(self,window):
        self.window=window
        self.source=None
        self.box=None
        self.slots=[]
        self.views=[]
        self.starts=[]
        self.sizes=[]
        self.active=-1
        self.busy=False
        self.from_scroll=False
        self.pending=None
        self.visible=set()
        self.mode='continuous'
        self.rows=[]
        window.pdf_scroll.get_vadjustment().connect('value-changed',self.scrolled)

    @property
    def enabled(self):
        w=self.window
        return bool(self.box and self.source is w.doc and
                    w._active_session.scroll_mode in SCROLLING_MODES)

    def detach_overlay(self):
        w=self.window
        parent=w.pdf_overlay.get_parent()
        focus=w.get_focus()
        if focus and (focus is w.pdf_overlay or focus.is_ancestor(w.pdf_overlay)):
            self.restore_focus=True
            w.set_focus(None)
        if isinstance(parent,Gtk.Overlay):
            parent.remove_overlay(w.pdf_overlay)
        elif parent is w.pdf_viewport:
            parent.set_child(None)

    def sync(self):
        if self.busy:
            return
        w=self.window
        wanted=bool(w.doc and w._active_session.scroll_mode in SCROLLING_MODES)
        self.busy=True
        try:
            if not wanted:
                if self.box:
                    self.detach_overlay()
                    w.pdf_viewport.set_child(w.pdf_overlay)
                    w.pdf_viewport.set_scroll_to_focus(self._scroll_to_focus)
                    self.box=None
                    self.source=None
                    self.slots=[]
                    self.views=[]
                    self.active=-1
                    w.pdf_scroll.get_vadjustment().set_value(0)
                return
            mode=w._active_session.scroll_mode
            if self.source is not w.doc or len(self.slots)!=w.doc.page_count or mode!=self.mode:
                if self.box is None:
                    self._scroll_to_focus=w.pdf_viewport.get_scroll_to_focus()
                w.pdf_viewport.set_scroll_to_focus(False)
                self.detach_overlay()
                self.source=w.doc
                self.box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=self.GAP,
                                 margin_top=self.MARGIN,margin_bottom=self.MARGIN,
                                 vexpand=False,valign=Gtk.Align.START)
                self.box.add_css_class('pdf-view')
                self.slots=[]
                self.views=[]
                self.sizes=[(page.rect.width,page.rect.height) for page in w.doc]
                self.active=-1
                self.visible=set()
                self.mode=mode
                self.rows=spread_rows(w.doc.page_count,mode)
                paired=mode!='continuous'
                containers={}
                for row in self.rows:
                    if paired:
                        container=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=self.GAP,
                                          halign=Gtk.Align.CENTER)
                        self.box.append(container)
                    else:
                        container=self.box
                    for number in row:
                        containers[number]=container
                for number in range(w.doc.page_count):
                    slot=Gtk.Overlay(hexpand=not paired,vexpand=False)
                    view=Gtk.DrawingArea(hexpand=not paired)
                    view.set_draw_func(self.draw,number)
                    slot.set_child(view)
                    containers[number].append(slot)
                    self.slots.append(slot)
                    self.views.append(view)
                    click=Gtk.GestureClick(button=1)
                    click.connect('pressed',self.click,number)
                    view.add_controller(click)
                    right=Gtk.GestureClick(button=3)
                    right.connect('pressed',self.right_click,number)
                    view.add_controller(right)
                    drag=Gtk.GestureDrag(button=1)
                    drag.connect('drag-begin',self.drag_begin,number)
                    drag.connect('drag-update',lambda gesture,x,y:w.on_drag_update(gesture,x,y))
                    drag.connect('drag-end',lambda gesture,x,y:w.on_drag_end(gesture,x,y))
                    view.add_controller(drag)
                    drag.group(click)
                    motion=Gtk.EventControllerMotion()
                    motion.connect('motion',self.motion,number)
                    view.add_controller(motion)
                w.pdf_viewport.set_child(self.box)
                self.layout()
            if self.active!=w.current_page_index:
                old=self.active
                self.detach_overlay()
                self.active=w.current_page_index
                w.pdf_overlay.set_hexpand(True)
                w.pdf_overlay.set_vexpand(True)
                self.slots[self.active].add_overlay(w.pdf_overlay)
                if old>=0 and old<len(self.views):
                    self.views[old].queue_draw()
                self.views[self.active].queue_draw()
                if not self.from_scroll:
                    self.goto(self.active)
        finally:
            self.busy=False
            if getattr(self,'restore_focus',False) and w.pdf_view.get_mapped():
                w.pdf_view.grab_focus()
                self.restore_focus=False

    def layout(self):
        w=self.window
        self.starts=[0]*len(self.slots)
        y=self.MARGIN
        paired=self.mode!='continuous'
        widest=max(sum(self.sizes[n][0] for n in row)*w.zoom_level+self.GAP*(len(row)-1) for row in self.rows)
        self.box.set_size_request(math.ceil(widest)+32,-1)
        for row in self.rows:
            row_height=0
            for number in row:
                width,height=self.sizes[number]
                self.starts[number]=y
                pixels=math.ceil(height*w.zoom_level)
                row_height=max(row_height,pixels)
                view=self.views[number]
                view.set_content_height(pixels)
                if paired:
                    view.set_content_width(math.ceil(width*w.zoom_level))
                    self.slots[number].set_size_request(math.ceil(width*w.zoom_level),pixels)
                else:
                    self.slots[number].set_size_request(-1,pixels)
                view.queue_draw()
            y+=row_height+self.GAP

    def goto(self,number):
        source=self.source
        serial=getattr(self,'_goto_serial',0)+1
        self._goto_serial=serial
        self._goto_pending=True
        def position():
            if self.enabled and self.source is source and self.window.current_page_index==number and serial==self._goto_serial:
                adjustment=self.window.pdf_scroll.get_vadjustment()
                adjustment.set_value(min(self.starts[number],max(0,adjustment.get_upper()-adjustment.get_page_size())))
            if serial==self._goto_serial:
                self._goto_pending=False
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(50,position)

    def page_at(self,y):
        index=max(0,min(len(self.starts)-1,bisect_right(self.starts,y)-1))
        # Pages sharing a row share a start; report the row's first page.
        while index>0 and self.starts[index-1]==self.starts[index]:
            index-=1
        return index

    def scrolled(self,adjustment):
        if not self.enabled or self.busy:
            return
        top=adjustment.get_value()
        bottom=top+adjustment.get_page_size()
        visible=set(range(self.page_at(top),self.page_at(bottom)+1))
        for number in visible|self.visible:
            self.views[number].queue_draw()
        self.visible=visible
        if self.pending is None and not getattr(self,'_goto_pending',False):
            self.pending=GLib.timeout_add(50,self.follow_scroll)

    def follow_scroll(self):
        self.pending=None
        w=self.window
        if (not self.enabled or w.view_drag_active or getattr(w, '_pan_start', None) is not None
                or getattr(w, 'dragged_object', None) is not None
                or getattr(w, 'dragging_to_create', False)
                or getattr(w, 'table_drag_state', None) is not None
                or getattr(w, 'inline_editor_widget', None) is not None
                or getattr(getattr(w, 'stamp_interaction', None), 'drag', None)
                or getattr(getattr(w, 'form_tools', None), 'drag_start', None) is not None
                or getattr(self,'_goto_pending',False)):
            return GLib.SOURCE_REMOVE
        adjustment=w.pdf_scroll.get_vadjustment()
        number=self.page_at(adjustment.get_value()+adjustment.get_page_size()/2)
        if number!=w.current_page_index:
            self.from_scroll=True
            try:
                w._load_page(number,preserve_scroll=True)
            finally:
                self.from_scroll=False
        return GLib.SOURCE_REMOVE

    def activate(self,number):
        w=self.window
        if self.enabled and number!=w.current_page_index:
            if getattr(w, 'inline_editor_widget', None) is not None:
                w._apply_and_hide_editor(force_apply=True)
            self.from_scroll=True
            try:
                w._load_page(number,preserve_scroll=True)
            finally:
                self.from_scroll=False

    def click(self,gesture,count,x,y,number):
        self.activate(number)
        self.window.on_pdf_view_pressed(gesture,count,x,y)

    def right_click(self,gesture,count,x,y,number):
        self.activate(number)
        self.window._on_right_click(gesture,count,x,y)

    def drag_begin(self,gesture,x,y,number):
        self.activate(number)
        self.window.on_drag_begin(gesture,x,y)

    def motion(self,controller,x,y,number):
        if self.enabled:
            self.window._last_pointer_pos=(x,y+self.starts[number]-self.starts[self.window.current_page_index])

    def draw(self,area,cr,width,height,number):
        cr.set_source_rgb(.42,.42,.42)
        cr.paint()
        if not self.enabled or self.source.is_closed or number==self.active:
            return
        w=self.window
        adjustment=w.pdf_scroll.get_vadjustment()
        top=adjustment.get_value()
        if self.starts[number]+height<top or self.starts[number]>top+adjustment.get_page_size():
            return
        page_width,page_height=self.sizes[number]
        x=max(0,(width-page_width*w.zoom_level)/2)
        y=max(0,(height-page_height*w.zoom_level)/2)
        cr.set_source_rgba(0,0,0,.15)
        cr.rectangle(x+4,y+4,page_width*w.zoom_level,page_height*w.zoom_level)
        cr.fill()
        surface=pdf_handler.get_page_cairo_surface(self.source,number,w.zoom_level)
        if surface:
            cr.set_source_surface(surface,x,y)
            cr.paint()

    def zoom(self,new_zoom,focal_point):
        w=self.window
        old_zoom=w.zoom_level
        vertical=w.pdf_scroll.get_vadjustment()
        horizontal=w.pdf_scroll.get_hadjustment()
        viewport_width=w.pdf_scroll.get_width()
        viewport_height=w.pdf_scroll.get_height()
        if focal_point:
            global_x=focal_point[0]
            global_y=self.starts[w.current_page_index]+focal_point[1]
            screen_x=global_x-horizontal.get_value()
            screen_y=global_y-vertical.get_value()
        else:
            screen_x=viewport_width/2
            screen_y=viewport_height/2
            global_x=horizontal.get_value()+screen_x
            global_y=vertical.get_value()+screen_y
        if getattr(self,'_zoom_pending',False) and focal_point==self._zoom_focal:
            anchor,point_x,point_y,screen_x,screen_y=self._zoom_anchor
            page_width=self.sizes[anchor][0]
        else:
            anchor=self.page_at(global_y)
            page_width=self.sizes[anchor][0]
            old_width=max(viewport_width,max(width for width,_ in self.sizes)*old_zoom+32)
            point_x=(global_x-max(0,(old_width-page_width*old_zoom)/2))/old_zoom
            point_y=(global_y-self.starts[anchor])/old_zoom
            self._zoom_anchor=(anchor,point_x,point_y,screen_x,screen_y)
            self._zoom_focal=focal_point
        serial=getattr(self,'_zoom_serial',0)+1
        self._zoom_serial=serial
        self._zoom_pending=True
        self.busy=True
        w.zoom_level=new_zoom
        w.zoom_label.set_text(f'{int(new_zoom*100)}%')
        self.layout()
        active_width,active_height=self.sizes[w.current_page_index]
        w.current_pdf_page_width=int(active_width*new_zoom)
        w.current_pdf_page_height=int(active_height*new_zoom)
        w.pdf_view.set_content_width(w.current_pdf_page_width)
        w.pdf_view.set_content_height(w.current_pdf_page_height)
        new_width=max(viewport_width,max(width for width,_ in self.sizes)*new_zoom+32)
        target_x=max(0,(new_width-page_width*new_zoom)/2)+point_x*new_zoom-screen_x
        target_y=self.starts[anchor]+point_y*new_zoom-screen_y
        source=self.source
        def finish():
            if serial!=self._zoom_serial:
                return GLib.SOURCE_REMOVE
            self._zoom_pending=False
            if self.enabled and self.source is source:
                horizontal.set_value(max(0,min(target_x,horizontal.get_upper()-horizontal.get_page_size())))
                vertical.set_value(max(0,min(target_y,vertical.get_upper()-vertical.get_page_size())))
                w.pdf_view.queue_draw()
            self.busy=False
            self.sync()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add(50,finish)
