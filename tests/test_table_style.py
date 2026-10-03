"""Native table styles, measured text fitting, persistence, and grouped undo."""
import copy
import unittest
import pymupdf as fitz
from test_canvas_drag import CanvasHarness
from pdflx.models import EditableShape, EditableText
from pdflx.table_creation import create_table_objects, TableSelection
from pdflx.table_style import PRESETS, style_states, current_style
from pdflx.undo_manager import AddTableCommand, EditTableCommand
from pdflx import pdf_handler, table_data, page_state


class TableStyleTests(unittest.TestCase):
    def window(self, rotation=0):
        window = CanvasHarness(EditableShape('rectangle', (0,0,0,0)), rotation)
        window.editable_shapes = []
        self.addCleanup(window.doc.close)
        cells = [['Code', 'Description'], ['0012', 'Résumé preparation'], ['0013', 'Review']]
        objects = create_table_objects(window.doc[0], cells)
        command = AddTableCommand(window, objects)
        command.execute()
        window.undo_manager.add_command(command)
        return window, TableSelection(objects), cells

    def test_presets_render_fills_behind_text_and_keep_table_geometry(self):
        for rotation in (0,90,180,270):
            for preset in PRESETS:
                with self.subTest(rotation=rotation, preset=preset):
                    window, table, cells = self.window(rotation)
                    original = copy.deepcopy([obj.__dict__ for obj in table.objects])
                    style = dict(PRESETS[preset], font_size=10, border_width=.8)
                    updated = style_states(table, window.doc[0], style)
                    self.assertEqual([obj.__dict__ for obj in table.objects], original)
                    command = EditTableCommand(window, table.objects, original, updated)
                    command.execute()
                    page = window.doc[0]
                    self.assertEqual(table_data.selection_cells(table, page), cells)
                    shapes = [obj for obj in table.objects if isinstance(obj, EditableShape)]
                    drawings = page.get_drawings()
                    self.assertEqual(len(drawings),6)
                    for index, shape in enumerate(shapes):
                        self.assertEqual(shape.bbox, original[index]['bbox'])
                    if style['fill']:
                        self.assertIsNotNone(drawings[0]['fill'])
                        self.assertAlmostEqual(drawings[0]['fill'][0],style['header_fill'][0],places=5)
                        # First drawing stream precedes all text rendering streams.
                        trace = page.get_bboxlog()
                        fill_index = next(i for i,(kind,*_) in enumerate(trace) if kind=='fill-path')
                        text_index = next(i for i,(kind,*_) in enumerate(trace) if kind=='fill-text')
                        self.assertLess(fill_index,text_index)
                        pix = page.get_pixmap()
                        text = next(obj for obj in table.objects if isinstance(obj,EditableText))
                        bounds = fitz.Rect(text.bbox)*page.rotation_matrix
                        crop = fitz.IRect(bounds)
                        bg = tuple(round(c*255) for c in style['header_fill'])
                        samples = [pix.pixel(x,y) for y in range(crop.y0,crop.y1)
                                   for x in range(crop.x0,crop.x1)]
                        self.assertTrue(any(sum(abs(a-b) for a,b in zip(pixel,bg))>80 for pixel in samples),
                                        'Filled header must contain visible text pixels')

    def test_style_is_one_undo_step_and_survives_reopening(self):
        window, table, cells = self.window()
        old = [copy.deepcopy(obj.__dict__) for obj in table.objects]
        style = dict(PRESETS['striped'], border_width=1.2,font_size=10)
        command = EditTableCommand(window, table.objects,old,style_states(table,window.doc[0],style))
        command.execute()
        window.undo_manager.add_command(command)
        self.assertEqual(len(window.undo_manager.undo_stack),2)
        self.assertEqual(current_style(table),style)
        window.undo_manager.undo()
        self.assertNotIn('table_style',table.objects[0].__dict__)
        self.assertTrue(table.objects[0].is_transparent)
        window.undo_manager.redo()
        self.assertEqual(current_style(table),style)
        window.doc.editor_page_models={0:(window.editable_texts,window.editable_shapes,[],[])}
        page_state.persist(window.doc)
        with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as reopened:
            groups=page_state.load(reopened,0)
            saved=TableSelection(groups[1]+groups[0])
            self.assertEqual(current_style(saved),style)
            self.assertEqual(table_data.selection_cells(saved,reopened[0]),cells)
            self.assertIn('Résumé',reopened[0].get_text())

    def test_oversized_text_and_invalid_styles_leave_page_and_objects_unchanged(self):
        window, table, cells = self.window()
        original=[copy.deepcopy(obj.__dict__) for obj in table.objects]
        before=window.doc[0].get_pixmap().samples
        for options in ({'font_size':24},{'border_width':float('nan')},{'text_color':(2,0,0)}):
            style=dict(PRESETS['blue'],font_size=10,border_width=.75)
            style.update(options)
            with self.assertRaises(ValueError):
                style_states(table,window.doc[0],style)
            self.assertEqual([obj.__dict__ for obj in table.objects],original)
            self.assertEqual(window.doc[0].get_pixmap().samples,before)

    def test_legacy_table_without_cell_indices_can_be_styled(self):
        window, table, cells = self.window(90)
        for obj in table.objects:
            for key in ('table_row','table_column','table_source_text','table_wrapped_text'):
                obj.__dict__.pop(key,None)
        style=dict(PRESETS['green'],font_size=10,border_width=.75)
        states=style_states(table,window.doc[0],style)
        self.assertEqual(len(states),len(table.objects))
        self.assertTrue(next(state['is_bold'] for obj,state in zip(table.objects,states)
                             if isinstance(obj,EditableText)))
