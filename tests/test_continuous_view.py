"""Headless coverage of reader layout and navigation using real PDF geometry."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
import pymupdf as fitz
try:
    from pdflx.continuous_view import ContinuousView
except ImportError:
    ContinuousView=None

class Widget:
    def __init__(self,**kw):
        self.parent=None
        self.children=[]
        self.width=1000
        self.height=300
    def get_parent(self): return self.parent
    def get_mapped(self): return True
    def get_prev_sibling(self): return None
    def append(self,child): self.children.append(child);child.parent=self
    def set_child(self,child):
        for old in self.children: old.parent=None
        self.children=[]
        if child: self.append(child)
    def add_overlay(self,child): self.append(child)
    def remove_overlay(self,child): self.children.remove(child);child.parent=None
    def get_width(self): return self.width
    def get_height(self): return self.height
    def get_scroll_to_focus(self): return getattr(self,'scroll_focus',True)
    def set_scroll_to_focus(self,value): self.scroll_focus=value
    def set_size_request(self,width,height): self.request=(width,height)
    def set_content_height(self,value): self.height=value
    def set_content_width(self,value): self.width=value
    def set_hexpand(self,value): pass
    def set_vexpand(self,value): pass
    def set_draw_func(self,*args): pass
    def add_css_class(self,*args): pass
    def add_controller(self,*args): pass
    def connect(self,*args): pass
    def group(self,*args): pass
    def queue_draw(self): pass
    def grab_focus(self): pass
    def set_text(self,*args): pass

class Adjustment:
    def __init__(self,size): self.value=0;self.upper=size;self.size=size;self.callbacks=[]
    def connect(self,signal,callback): self.callbacks.append(callback)
    def get_value(self): return self.value
    def get_upper(self): return self.upper
    def get_page_size(self): return self.size
    def set_value(self,value):
        if self.value!=value:
            self.value=value
            for callback in self.callbacks: callback(self)

class Scroll(Widget):
    def __init__(self): super().__init__();self.v=Adjustment(300);self.h=Adjustment(1000)
    def get_vadjustment(self): return self.v
    def get_hadjustment(self): return self.h

@unittest.skipUnless(ContinuousView,"GTK bindings required")
class ContinuousViewTests(unittest.TestCase):
    def setUp(self):
        self.callbacks=[]
        self.addCleanup(patch.stopall)
        for name in ('Box','Overlay','DrawingArea','GestureClick','GestureDrag','EventControllerMotion'):
            patch('pdflx.continuous_view.Gtk.'+name,Widget).start()
        patch('pdflx.continuous_view.GLib.timeout_add',self.schedule).start()
        self.doc=fitz.open()
        for number in range(3):
            page=self.doc.new_page(width=500,height=700)
            page.insert_text((30,80),f'Page {number+1}')
        self.doc[1].set_rotation(90)
        self.addCleanup(self.doc.close)
        self.w=SimpleNamespace(doc=self.doc,view_mode=True,current_page_index=0,zoom_level=1,
            view_drag_active=False,_active_session=SimpleNamespace(scroll_mode='continuous'),
            pdf_scroll=Scroll(),pdf_viewport=Widget(),pdf_overlay=Widget(),pdf_view=Widget(),
            get_focus=lambda:None,zoom_label=Widget(),set_focus=Mock())
        self.w.pdf_viewport.set_child(self.w.pdf_overlay)
        self.c=ContinuousView(self.w)
        self.w._load_page=self.load
        self.c.sync()
        self.allocate()
        self.flush()
    def schedule(self,delay,callback,*args): self.callbacks.append(lambda:callback(*args));return len(self.callbacks)
    def flush(self):
        for _ in range(10):
            pending,self.callbacks=self.callbacks,[]
            if not pending: return
            for callback in pending: callback()
        self.fail('Reader callbacks did not settle')
    def allocate(self):
        if self.c.box:
            self.w.pdf_scroll.v.upper=self.c.starts[-1]+self.c.sizes[-1][1]*self.w.zoom_level+16
    def load(self,number,preserve_scroll=False):
        self.w.current_page_index=number
        self.c.sync()
    def test_smooth_scroll_preserves_position_and_updates_active_page(self):
        self.assertEqual(self.c.starts,[16,736,1256])
        self.assertFalse(self.w.pdf_viewport.get_scroll_to_focus())
        self.assertIs(self.w.pdf_overlay.parent,self.c.slots[0])
        self.w.pdf_scroll.v.set_value(780)
        self.flush()
        self.assertEqual(self.w.pdf_scroll.v.value,780)
        self.assertEqual(self.w.current_page_index,1)
        self.assertIs(self.w.pdf_overlay.parent,self.c.slots[1])
        self.w._load_page(2)
        self.flush()
        self.assertEqual(self.w.pdf_scroll.v.value,1256)
    def test_zoom_keeps_the_same_document_point_in_the_viewport(self):
        self.w.pdf_scroll.v.set_value(780)
        self.flush()
        before=(780+150-self.c.starts[1])/self.w.zoom_level
        self.c.zoom(1.2,None)
        self.c.zoom(1.4,None)
        self.allocate()
        self.flush()
        after=(self.w.pdf_scroll.v.value+150-self.c.starts[1])/self.w.zoom_level
        self.assertAlmostEqual(before,after)
        self.assertAlmostEqual(self.w.current_pdf_page_width,980,delta=1)
        self.assertEqual(self.w.current_pdf_page_height,700)
    def test_mode_switch_and_document_change_restore_interactive_canvas(self):
        self.w.view_mode=False
        self.c.sync()
        self.assertTrue(self.c.enabled)
        self.assertIs(self.w.pdf_overlay.parent,self.c.slots[0])
        self.w._active_session.scroll_mode='page'
        self.c.sync()
        self.assertFalse(self.c.enabled)
        self.assertTrue(self.w.pdf_viewport.get_scroll_to_focus())
        self.assertIs(self.w.pdf_overlay.parent,self.w.pdf_viewport)
        self.w.view_mode=True
        self.w._active_session.scroll_mode='continuous'
        self.c.sync()
        self.allocate()
        self.flush()
        self.assertTrue(self.c.enabled)
        other=fitz.open();other.new_page(width=300,height=400)
        self.addCleanup(other.close)
        self.w.doc=other
        self.w.current_page_index=0
        self.c.sync()
        self.allocate()
        self.flush()
        self.assertEqual(len(self.c.slots),1)
        self.assertIs(self.c.source,other)
    def test_scroll_does_not_change_pages_during_text_selection(self):
        self.w.view_drag_active=True
        self.w.pdf_scroll.v.set_value(780)
        self.flush()
        self.assertEqual(self.w.current_page_index,0)

    def test_edit_gestures_and_inline_text_keep_the_active_page(self):
        self.w.view_mode=False
        for attribute, value in (('dragged_object', object()), ('table_drag_state', []),
                                 ('dragging_to_create', True), ('inline_editor_widget', object()),
                                 ('stamp_interaction', SimpleNamespace(drag=True)),
                                 ('form_tools', SimpleNamespace(drag_start=(0, 0)))):
            with self.subTest(attribute=attribute):
                setattr(self.w, attribute, value)
                self.w.pdf_scroll.v.set_value(780)
                self.c.follow_scroll()
                self.assertEqual(self.w.current_page_index,0)
                delattr(self.w, attribute)
        self.c.follow_scroll()
        self.assertEqual(self.w.current_page_index,1)

    def test_click_another_page_commits_inline_edit_before_activation(self):
        self.w.view_mode=False
        self.w.inline_editor_widget=object()
        events=[]
        def commit(**kwargs):
            events.append(('commit',kwargs))
            self.w.inline_editor_widget=None
        self.w._apply_and_hide_editor=commit
        load=self.w._load_page
        self.w._load_page=lambda number,**kwargs:(events.append(('page',number)),load(number,**kwargs))
        self.c.activate(1)
        self.assertEqual(events,[('commit',{'force_apply':True}),('page',1)])
        self.assertIs(self.w.pdf_overlay.parent,self.c.slots[1])

    def test_rendering_is_limited_to_visible_pages(self):
        import cairo
        from pdflx import pdf_handler
        self.w.pdf_scroll.v.set_value(600)
        self.w.pdf_scroll.v.size=700
        surface=cairo.ImageSurface(cairo.FORMAT_RGB24,1000,700)
        cr=cairo.Context(surface)
        with patch('pdflx.continuous_view.pdf_handler.get_page_cairo_surface',
                   wraps=pdf_handler.get_page_cairo_surface) as render:
            self.c.draw(self.c.views[1],cr,1000,500,1)
            self.assertEqual(render.call_count,1)
            self.assertEqual(render.call_args.args[1],1)
            self.w.pdf_scroll.v.set_value(0)
            self.w.pdf_scroll.v.size=300
            self.c.draw(self.c.views[2],cr,1000,700,2)
            self.assertEqual(render.call_count,1)
