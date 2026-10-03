"""Exercise canvas movement handlers without opening a GTK window."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf as fitz

try:
    from pdflx.window import PdfEditorWindow
    from gi.repository import Gtk
    from pdflx.models import (
        EditableImage, EditableShape, EditableStroke, EditableText,
    )
    from pdflx.undo_manager import UndoManager
    from pdflx import pdf_handler
except ImportError:
    PdfEditorWindow = None


class CanvasHarness:
    """Real canvas handlers with stand-ins for widget redraws and controls."""
    def __init__(self, obj, rotation=0, zoom=1):
        self.doc = fitz.open()
        page = self.doc.new_page(width=600, height=800)
        page.set_rotation(rotation)
        pdf_handler.save_page_snapshot(self.doc, 0, force=True)
        self.current_page_index = 0
        self.zoom_level = zoom
        self.current_pdf_page_width = page.rect.width * zoom
        self.current_pdf_page_height = page.rect.height * zoom
        self.pdf_view = SimpleNamespace(
            get_allocated_width=lambda: self.current_pdf_page_width + 100,
            get_allocated_height=lambda: self.current_pdf_page_height + 80,
            queue_draw=Mock(),
        )
        self.status_label = SimpleNamespace(set_text=Mock())
        self.view_mode = False
        self.tool_mode = 'select'
        self.inline_editor_widget = None
        self.selected_text = self.selected_image = self.selected_shape = None
        self.selected_stroke = self.dragged_object = None
        self.resize_handle = self.resize_start_bbox = None
        self.dragging_to_create = False
        self.pending_format_change_obj = self.before_format_change_state = None
        for name, kind in (
            ('editable_texts', EditableText), ('editable_images', EditableImage),
            ('editable_shapes', EditableShape), ('editable_strokes', EditableStroke),
        ):
            setattr(self, name, [obj] if isinstance(obj, kind) else [])
        for name in (
            '_update_undo_redo_buttons', '_update_ui_state',
            '_update_text_format_controls', '_refresh_thumbnail',
        ):
            setattr(self, name, Mock())
        self._find_resize_handle_at_pos = Mock(return_value=None)
        self.undo_manager = UndoManager(self)


if PdfEditorWindow:
    for method in (
        'on_drag_begin', 'on_drag_update', 'on_drag_end',
        '_visual_to_unrotated_page_coords', '_visual_to_unrotated_delta',
        '_unrotated_to_visual_page_coords', '_find_text_at_pos',
        '_find_image_at_pos', '_find_shape_at_pos', '_find_stroke_at_pos',
        'commit_pending_format_change', '_find_table_at_pos', '_select_table',
        '_update_table_drag', '_clear_table_preview', '_is_table_preview_member', '_cancel_table_drag',
    ):
        setattr(CanvasHarness, method, getattr(PdfEditorWindow, method))


@unittest.skipUnless(PdfEditorWindow, 'GTK bindings are required')
class CanvasDragTests(unittest.TestCase):
    def objects(self):
        text = EditableText(100, 100, 'Move me', baseline=118, page_number=0)
        text.bbox = (100, 100, 160, 140)
        return [
            text,
            EditableImage((100, 100, 160, 140), 0, None, b'', is_new=True),
            EditableShape('rectangle', (100, 100, 160, 140), page_number=0, is_new=True),
            EditableStroke([(100, 100), (160, 140)], page_number=0),
        ]

    def start(self, window, obj):
        x0, y0, x1, y1 = obj.bbox
        x, y = window._unrotated_to_visual_page_coords((x0+x1)/2, (y0+y1)/2)
        gesture = SimpleNamespace(set_state=Mock())
        window.on_drag_begin(gesture, x*window.zoom_level+50, y*window.zoom_level+40)
        self.assertIs(window.dragged_object, obj)
        gesture.set_state.assert_called_with(Gtk.EventSequenceState.CLAIMED)
        return gesture

    def test_select_moves_all_object_types_at_zoom_and_page_rotations(self):
        expected_deltas = [(20, 10), (10, -20), (-20, -10), (-10, 20)]
        for rotation, delta in zip((0, 90, 180, 270), expected_deltas):
            for obj in self.objects():
                with self.subTest(rotation=rotation, kind=type(obj).__name__):
                    window = CanvasHarness(obj, rotation, zoom=2)
                    self.addCleanup(window.doc.close)
                    old = copy.deepcopy(obj.__dict__)
                    gesture = self.start(window, obj)
                    window.on_drag_update(gesture, 20, 10)
                    window.on_drag_update(gesture, 40, 20)
                    dx, dy = delta
                    self.assertEqual(obj.bbox, tuple(v+d for v, d in zip(old['bbox'], (dx, dy, dx, dy))))
                    if isinstance(obj, EditableText):
                        self.assertEqual(obj.baseline, old['baseline']+dy)
                    if isinstance(obj, EditableStroke):
                        self.assertEqual(obj.points, [(x+dx, y+dy) for x, y in old['points']])

    def test_moving_keeps_resized_dimensions(self):
        obj = self.objects()[1]
        obj.original_bbox = (100, 100, 120, 120)
        window = CanvasHarness(obj)
        self.addCleanup(window.doc.close)
        gesture = self.start(window, obj)
        window.on_drag_update(gesture, 30, 15)
        self.assertEqual(obj.bbox, (130, 115, 190, 155))

    def test_drag_is_saved_and_undoable_as_one_change(self):
        obj = self.objects()[2]
        original = obj.bbox
        window = CanvasHarness(obj)
        self.addCleanup(window.doc.close)
        # A click can leave this object awaiting a formatting commit.
        window.pending_format_change_obj = obj
        window.before_format_change_state = copy.deepcopy(obj.__dict__)
        gesture = self.start(window, obj)
        window.on_drag_update(gesture, 30, 15)
        window.on_drag_end(gesture, 30, 15)
        moved = (130, 115, 190, 155)
        self.assertEqual(obj.bbox, moved)
        self.assertEqual(len(window.undo_manager.undo_stack), 1)
        self.assertEqual(tuple(window.doc[0].get_drawings()[0]['rect']), moved)
        window.undo_manager.undo()
        self.assertEqual(obj.bbox, original)
        self.assertEqual(tuple(window.doc[0].get_drawings()[0]['rect']), original)
        window.undo_manager.redo()
        self.assertEqual(obj.bbox, moved)
        self.assertIsNone(window.dragged_object)

    def test_empty_canvas_does_not_start_object_drag(self):
        obj = self.objects()[2]
        window = CanvasHarness(obj)
        self.addCleanup(window.doc.close)
        gesture = SimpleNamespace(set_state=Mock())
        window.on_drag_begin(gesture, 400, 400)
        self.assertIsNone(window.dragged_object)
        gesture.set_state.assert_called_with(Gtk.EventSequenceState.DENIED)

    def test_text_corner_resize_scales_font_and_keeps_opposite_corner_fixed(self):
        from pdflx import pdf_handler
        for page_rotation in (0,90,180,270):
            for text_rotation in (0,30):
                for handle in ('nw','ne','sw','se'):
                    with self.subTest(page_rotation=page_rotation,text_rotation=text_rotation,handle=handle):
                        obj=EditableText(120,180,'Resize',font_size=18,is_new=True,
                                         baseline=198,page_number=0,rotation=text_rotation)
                        obj.bbox=(120,180,240,208)
                        window=CanvasHarness(obj,page_rotation,zoom=2)
                        self.addCleanup(window.doc.close)
                        window.selected_text=obj
                        window._handle_resize_update=PdfEditorWindow._handle_resize_update.__get__(window)
                        window._find_resize_handle_at_pos=PdfEditorWindow._find_resize_handle_at_pos.__get__(window)
                        original=copy.deepcopy(obj.__dict__)
                        rect=fitz.Rect(obj.bbox)
                        padding=3/window.zoom_level
                        corner=fitz.Point(rect.x0-padding if 'w' in handle else rect.x1+padding,
                                          rect.y0-padding if 'n' in handle else rect.y1+padding)
                        cx,cy=(rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2
                        x,y=pdf_handler.rotate_point(corner.x,corner.y,cx,cy,text_rotation)
                        x,y=window._unrotated_to_visual_page_coords(x,y)
                        gesture=SimpleNamespace(set_state=Mock())
                        window.on_drag_begin(gesture,x*2+50,y*2+40)
                        self.assertEqual(window.resize_handle,handle)
                        dx=(-1 if 'w' in handle else 1)*rect.width*0.5
                        dy=(-1 if 'n' in handle else 1)*rect.height*0.5
                        dx,dy=pdf_handler.rotate_point(dx,dy,0,0,text_rotation)
                        page=window.doc[0]
                        mat=page.rotation_matrix
                        dx,dy=mat.a*dx+mat.c*dy,mat.b*dx+mat.d*dy
                        window.on_drag_update(gesture,dx*2,dy*2)
                        self.assertAlmostEqual(obj.font_size,27,places=4)
                        resized=fitz.Rect(obj.bbox)
                        self.assertAlmostEqual(resized.width,rect.width*1.5,places=4)
                        self.assertAlmostEqual(resized.height,rect.height*1.5,places=4)
                        anchor=dict(nw='br',ne='bl',sw='tr',se='tl')[handle]
                        before=getattr(rect,anchor)
                        after=getattr(resized,anchor)
                        fixed=pdf_handler.rotate_point(before.x,before.y,cx,cy,text_rotation)
                        actual=pdf_handler.rotate_point(after.x,after.y,(resized.x0+resized.x1)/2,
                                                       (resized.y0+resized.y1)/2,text_rotation)
                        self.assertAlmostEqual(actual[0],fixed[0],places=4)
                        self.assertAlmostEqual(actual[1],fixed[1],places=4)
                        window.on_drag_end(gesture,dx*2,dy*2)
                        self.assertEqual(len(window.undo_manager.undo_stack),1)
                        window.undo_manager.undo()
                        self.assertEqual(obj.bbox,original['bbox'])
                        self.assertEqual(obj.font_size,original['font_size'])
                        window.undo_manager.redo()
                        self.assertAlmostEqual(obj.font_size,27,places=4)


if __name__ == '__main__':
    unittest.main()
