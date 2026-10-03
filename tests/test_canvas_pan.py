"""Ensure the hand scrolls the viewport while the arrow retains object editing."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from test_canvas_drag import CanvasHarness
from pdflx.models import EditableShape


class Adjustment:
    def __init__(self, value, upper=1000, size=300):
        self.value, self.upper, self.size = value, upper, size
    def get_value(self): return self.value
    def set_value(self, value): self.value = value
    def get_lower(self): return 0
    def get_upper(self): return self.upper
    def get_page_size(self): return self.size


class CanvasPanTests(unittest.TestCase):
    def window(self, view_mode):
        obj = EditableShape('rectangle', (100, 100, 200, 200), is_new=True)
        window = CanvasHarness(obj)
        self.addCleanup(window.doc.close)
        window.view_mode = view_mode
        window.tool_mode = 'drag'
        window.pdf_view.set_cursor = Mock()
        window._update_cursor_for_tool = Mock()
        horizontal, vertical = Adjustment(100), Adjustment(200)
        window.pdf_scroll = SimpleNamespace(get_hadjustment=lambda: horizontal,
                                            get_vadjustment=lambda: vertical)
        return window, obj, horizontal, vertical

    def test_hand_pans_in_both_modes_without_editing_or_undo(self):
        for view_mode in (False, True):
            with self.subTest(view_mode=view_mode):
                window, obj, horizontal, vertical = self.window(view_mode)
                original = obj.__dict__.copy()
                gesture = SimpleNamespace(set_state=Mock())
                window.on_drag_begin(gesture, 150, 150)
                window.on_drag_update(gesture, 30, -40)
                self.assertEqual((horizontal.value, vertical.value), (70, 240))
                window.on_drag_update(gesture, 40, -60)
                self.assertEqual((horizontal.value, vertical.value), (60, 260))
                window.on_drag_end(gesture, 40, -60)
                self.assertIsNone(window._pan_start)
                self.assertEqual(obj.__dict__, original)
                self.assertEqual(window.undo_manager.undo_stack, [])
                self.assertIsNone(window.dragged_object)

    def test_surface_coordinates_avoid_scroll_feedback_and_clamp_edges(self):
        window, obj, horizontal, vertical = self.window(True)
        point = [500, 500]
        event = SimpleNamespace(get_position=lambda: (True, *point))
        gesture = SimpleNamespace(set_state=Mock(), get_current_event=lambda: event)
        window.on_drag_begin(gesture, 150, 150)
        point[:] = [520, 470]
        window.on_drag_update(gesture, 400, 400)  # Canvas coordinates changed during scrolling.
        self.assertEqual((horizontal.value, vertical.value), (80, 230))
        point[:] = [520, 470]
        window.on_drag_update(gesture, 800, 800)
        self.assertEqual((horizontal.value, vertical.value), (80, 230))
        point[:] = [1500, -1500]
        window.on_drag_end(gesture, 800, 800)
        self.assertEqual((horizontal.value, vertical.value), (0, 700))
