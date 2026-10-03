"""Table layout, PDF persistence, rotation, and grouped undo."""
import unittest
import copy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf as fitz

from test_canvas_drag import CanvasHarness, PdfEditorWindow

if PdfEditorWindow:
    from pdflx.table_creation import create_table_objects
    from pdflx.models import EditableText, EditableShape
    from pdflx.undo_manager import AddTableCommand
    from gi.repository import Gtk


@unittest.skipUnless(PdfEditorWindow, 'GTK bindings are required')
class TableCreationTests(unittest.TestCase):
    def window(self, rotation=0):
        window = CanvasHarness(EditableShape('rectangle', (0, 0, 0, 0)), rotation)
        window.editable_shapes = []
        self.addCleanup(window.doc.close)
        return window

    def insert(self, window, cells, **options):
        objects = create_table_objects(window.doc[0], cells, **options)
        command = AddTableCommand(window, objects)
        command.execute()
        window.undo_manager.add_command(command)
        return objects

    def test_table_saved_and_removed_as_one_undo_step(self):
        window = self.window()
        window.doc[0].insert_text((20, 20), 'Existing document')
        from pdflx import pdf_handler
        pdf_handler.save_page_snapshot(window.doc, 0, force=True)
        self.insert(window, [['Name', 'Value'], ['Alpha', '123']])
        self.assertEqual(len(window.editable_shapes), 4)
        self.assertEqual(len(window.editable_texts), 4)
        self.assertEqual(len(window.undo_manager.undo_stack), 1)
        with fitz.open(stream=window.doc.tobytes(), filetype='pdf') as reopened:
            text = reopened[0].get_text()
            for value in ('Existing document', 'Name', 'Value', 'Alpha', '123'):
                self.assertIn(value, text)
            tables = reopened[0].find_tables().tables
            self.assertEqual(tables[0].extract(), [['Name', 'Value'], ['Alpha', '123']])
        window.selected_text = window.editable_texts[0]
        window.undo_manager.undo()
        self.assertIsNone(window.selected_text)
        self.assertEqual(window.doc[0].get_text().strip(), 'Existing document')
        self.assertEqual(window.editable_shapes, [])
        self.assertEqual(window.editable_texts, [])
        window.undo_manager.redo()
        self.assertIn('Alpha', window.doc[0].get_text())

    def test_rotated_pages_keep_table_upright_and_inside_cells(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                window = self.window(rotation)
                self.insert(window, [['Header', 'Value'], ['Alpha', '123']])
                page = window.doc[0]
                visual_cells = [fitz.Rect(obj.bbox) * page.rotation_matrix
                                for obj in window.editable_shapes]
                for block in page.get_text('dict')['blocks']:
                    for line in block.get('lines', []):
                        # PDF text direction transformed by the page rotation.
                        direction = fitz.Point(*line['dir'])
                        origin = fitz.Point(0, 0) * page.rotation_matrix
                        vector = direction * page.rotation_matrix - origin
                        self.assertAlmostEqual(vector.x, 1, places=4)
                        self.assertAlmostEqual(vector.y, 0, places=4)
                        bounds = fitz.Rect(line['bbox']) * page.rotation_matrix
                        self.assertTrue(any(bounds in cell for cell in visual_cells))

    def test_long_and_unicode_text_wraps_inside_growing_rows(self):
        window = self.window()
        objects = self.insert(window, [['Details', 'Notes'],
                              ['Long text ' * 15, 'Résumé · München · Ω']], width_percent=60)
        self.assertTrue(any('\n' in obj.text for obj in objects if isinstance(obj, EditableText)))
        self.assertIn('Résumé', window.doc[0].get_text())
        for word in window.doc[0].get_text('words'):
            bounds = fitz.Rect(word[:4])
            self.assertTrue(any(bounds in fitz.Rect(cell.bbox) for cell in window.editable_shapes))

    def table_gesture(self, window, point):
        vx, vy = window._unrotated_to_visual_page_coords(*point)
        gesture = SimpleNamespace(set_state=Mock())
        window.on_drag_begin(gesture, vx*window.zoom_level+50, vy*window.zoom_level+40)
        gesture.set_state.assert_called_with(Gtk.EventSequenceState.CLAIMED)
        return gesture

    def assert_pdf_cells(self, window):
        drawings = window.doc[0].get_drawings()
        self.assertEqual(len(drawings), len(window.editable_shapes))
        for drawing, cell in zip(drawings, window.editable_shapes):
            for actual, expected in zip(drawing['rect'], cell.bbox):
                self.assertAlmostEqual(actual, expected, places=3)
        with fitz.open(stream=window.doc.tobytes(), filetype='pdf') as saved:
            self.assertIn('Alpha', saved[0].get_text())

    def test_move_whole_table_from_empty_cell_at_all_page_rotations(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                window = self.window(rotation)
                window.zoom_level = 2
                window.current_pdf_page_width *= 2
                window.current_pdf_page_height *= 2
                objects = self.insert(window, [['Header', 'Value'], ['Alpha', '']])
                old = [copy.deepcopy(obj.__dict__) for obj in objects]
                cell = window.editable_shapes[-1]
                x0, y0, x1, y1 = cell.bbox
                gesture = self.table_gesture(window, ((x0+x1)/2, (y0+y1)/2))
                window.on_drag_update(gesture, 20, 10)
                window.on_drag_update(gesture, 40, 20)
                dx, dy = window._visual_to_unrotated_delta(20, 10)
                for obj, state in zip(objects, old):
                    self.assertEqual(obj.bbox, tuple(v+d for v, d in zip(state['bbox'], (dx, dy, dx, dy))))
                    if isinstance(obj, EditableText):
                        self.assertEqual(obj.font_size, state['font_size'])
                        self.assertEqual(obj.baseline, state['baseline']+dy)
                window.on_drag_end(gesture, 40, 20)
                self.assertEqual(len(window.undo_manager.undo_stack), 2)
                moved = [obj.bbox for obj in objects]
                self.assert_pdf_cells(window)
                window.undo_manager.undo()
                self.assertEqual([obj.bbox for obj in objects], [state['bbox'] for state in old])
                self.assert_pdf_cells(window)
                window.undo_manager.redo()
                self.assertEqual([obj.bbox for obj in objects], moved)
                self.assert_pdf_cells(window)

    def test_corner_resize_scales_entire_table_and_keeps_opposite_corner_fixed(self):
        for rotation in (0, 90, 180, 270):
            for corner in ('nw', 'ne', 'sw', 'se'):
                with self.subTest(rotation=rotation, corner=corner):
                    window = self.window(rotation)
                    objects = self.insert(window, [['Header', 'Value'], ['Alpha', '123']], width_percent=60)
                    table = window._find_table_at_pos(*window._unrotated_to_visual_page_coords(
                        *objects[0].bbox[:2]))
                    window._select_table(table)
                    window._find_resize_handle_at_pos = PdfEditorWindow._find_resize_handle_at_pos.__get__(window)
                    old = [copy.deepcopy(obj.__dict__) for obj in table.objects]
                    x0, y0, x1, y1 = table.bbox
                    pad = 3/window.zoom_level
                    point = (x0-pad if 'w' in corner else x1+pad,
                             y0-pad if 'n' in corner else y1+pad)
                    gesture = self.table_gesture(window, point)
                    self.assertEqual(window.table_resize_handle, corner)
                    dx = -(x1-x0)*0.25 if 'w' in corner else (x1-x0)*0.25
                    dy = -(y1-y0)*0.25 if 'n' in corner else (y1-y0)*0.25
                    matrix = window.doc[0].rotation_matrix
                    delta = fitz.Point(dx, dy)*matrix - fitz.Point(0, 0)*matrix
                    window.on_drag_update(gesture, delta.x, delta.y)
                    nx0, ny0, nx1, ny1 = table.bbox
                    self.assertAlmostEqual(nx1-nx0, (x1-x0)*1.25)
                    self.assertAlmostEqual(ny1-ny0, (y1-y0)*1.25)
                    self.assertEqual(nx1 if 'w' in corner else nx0, x1 if 'w' in corner else x0)
                    self.assertEqual(ny1 if 'n' in corner else ny0, y1 if 'n' in corner else y0)
                    for obj, state in zip(table.objects, old):
                        if isinstance(obj, EditableText):
                            self.assertAlmostEqual(obj.font_size, state['font_size']*1.25)
                    window.on_drag_end(gesture, delta.x, delta.y)
                    self.assert_pdf_cells(window)
                    resized = [obj.bbox for obj in table.objects]
                    window.undo_manager.undo()
                    self.assertEqual([obj.bbox for obj in table.objects], [state['bbox'] for state in old])
                    self.assert_pdf_cells(window)
                    window.undo_manager.redo()
                    self.assertEqual([obj.bbox for obj in table.objects], resized)
                    self.assert_pdf_cells(window)

    def test_click_selects_table_and_double_click_edits_cell(self):
        window = self.window()
        objects = self.insert(window, [['Alpha', '123']])
        text = next(obj for obj in objects if isinstance(obj, EditableText))
        window.font_scan_in_progress = False
        window._show_inline_editor = Mock()
        window.on_pdf_view_pressed = PdfEditorWindow.on_pdf_view_pressed.__get__(window)
        x, y = text.bbox[:2]
        gesture = SimpleNamespace()
        with patch.object(Gtk.EventController, 'get_current_event_state', return_value=0):
            window.on_pdf_view_pressed(gesture, 1, x+51, y+41)
            self.assertEqual(len(window.selected_table.objects), len(objects))
            self.assertIsNone(window.selected_text)
            window.on_pdf_view_pressed(gesture, 2, x+51, y+41)
        self.assertIsNone(window.selected_table)
        self.assertIs(window.selected_text, text)
        window._show_inline_editor.assert_called_once()

    def test_drag_changes_only_selected_table_and_click_does_not_add_history(self):
        window = self.window()
        first = self.insert(window, [['Alpha', '123']], width_percent=60)
        second = self.insert(window, [['Other', 'Table']], width_percent=40)
        untouched = [copy.deepcopy(obj.__dict__) for obj in second]
        x0, y0, x1, y1 = first[0].bbox
        # The larger table's outer cell area is outside the second table.
        gesture = self.table_gesture(window, (x0+2, (y0+y1)/2))
        window.on_drag_end(gesture, 0, 0)
        self.assertEqual(len(window.undo_manager.undo_stack), 2)
        gesture = self.table_gesture(window, (x0+2, (y0+y1)/2))
        window.on_drag_update(gesture, 10, 10)
        window.on_drag_end(gesture, 10, 10)
        self.assertEqual(len(window.undo_manager.undo_stack), 3)
        self.assertEqual([obj.__dict__ for obj in second], untouched)
        self.assertEqual(first[0].bbox, (x0+10, y0+10, x1+10, y1+10))

    def test_undo_insertion_clears_table_selection(self):
        window = self.window()
        objects = self.insert(window, [['Alpha']])
        from pdflx.table_creation import TableSelection
        window._select_table(TableSelection(objects))
        window.undo_manager.undo()
        self.assertIsNone(window.selected_table)
        window.undo_manager.redo()
        self.assertEqual(len(window.editable_shapes), 1)

    def test_invalid_and_overflowing_tables_do_not_modify_document(self):
        window = self.window()
        for cells, options in (
            ([], {}), ([['a'], ['a', 'b']], {}),
            ([['a']], {'font_size': float('nan')}),
            ([['a'] for _ in range(20)], {'row_height': 100}),
            ([['W'] * 10], {'width_percent': 20, 'font_size': 24}),
        ):
            with self.subTest(options=options):
                with self.assertRaises(ValueError):
                    create_table_objects(window.doc[0], cells, **options)
                self.assertEqual(window.doc[0].get_text(), '')
                self.assertEqual(window.doc[0].get_drawings(), [])


if __name__ == '__main__':
    unittest.main()
