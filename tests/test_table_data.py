"""CSV/clipboard parsing, native table copying, and content-aware layout."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf as fitz
from pdflx import table_data, pdf_handler, page_state
from pdflx.table_creation import create_table_objects, TableSelection
from pdflx.models import EditableShape, EditableText


class TableDataTests(unittest.TestCase):
    def test_spreadsheet_paste_preserves_empty_cells_and_literal_values(self):
        text = 'Code\tDescription\tValue\r\n0012\tRésumé\t0\r\n\tLast\t\r\n'
        self.assertEqual(table_data.parse_table(text),
                         [['Code', 'Description', 'Value'], ['0012', 'Résumé', '0'], ['', 'Last', '']])

    def test_common_csv_separators_quotes_and_multiline_cells(self):
        for separator in (',', ';', '\t', '|'):
            with self.subTest(separator=separator):
                text = f'Name{separator}Note\r\n"A{separator}B"{separator}"First\nSecond"\r\n'
                self.assertEqual(table_data.parse_table(text),
                                 [['Name', 'Note'], [f'A{separator}B', 'First\nSecond']])
        self.assertEqual(table_data.parse_table('a;b,c\nx;y,z', ';'), [['a', 'b,c'], ['x', 'y,z']])

    def test_ragged_rows_are_padded_without_discarding_data(self):
        self.assertEqual(table_data.parse_table('a\tb\tc\n1\t2\n3\t\t'),
                         [['a', 'b', 'c'], ['1', '2', ''], ['3', '', '']])

    def test_csv_file_encodings_and_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'table.csv'
            for encoding in ('utf-8-sig', 'utf-16', 'cp1252'):
                path.write_bytes('Name;City\nAndré;München'.encode(encoding))
                self.assertEqual(table_data.read_csv(path), [['Name', 'City'], ['André', 'München']])
            path.write_bytes(b'a'*(table_data.MAX_BYTES+1))
            with self.assertRaises(ValueError):
                table_data.read_csv(path)

    def test_invalid_and_oversized_data_is_rejected_without_truncation(self):
        for text in ('', '  \n', 'a,\x00b', 'a,"unterminated',
                     '\n'.join('row' for _ in range(table_data.MAX_ROWS+1)),
                     '\t'.join('col' for _ in range(table_data.MAX_COLUMNS+1)),
                     '\n'.join('\t'.join(['x']*20) for _ in range(51))):
            with self.subTest(text=text[:30]):
                with self.assertRaises(ValueError):
                    table_data.parse_table(text)

    def test_clipboard_roundtrip_handles_tabs_quotes_and_linebreaks(self):
        cells = [['Name', 'Note', 'Empty'], ['a\tb', '"yes"\nnext', '']]
        self.assertEqual(table_data.parse_table(table_data.to_tsv(cells), '\t'), cells)

    def test_copy_current_table_cells_after_resize_edit_and_reopen(self):
        original = [['Name', 'Notes', ''], ['Alpha', 'Long note '*10, '0012']]
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation), fitz.open() as doc:
                page = doc.new_page(width=600, height=800)
                page.set_rotation(rotation)
                pdf_handler.save_page_snapshot(doc, 0, force=True)
                objects = create_table_objects(page, original, fit_columns=True)
                table = TableSelection(objects)
                self.assertEqual(table_data.selection_cells(table, page), original)
                states = [obj.__dict__.copy() for obj in objects]
                x0, y0, x1, y1 = table.bbox
                table.transform(states, table.bbox, (x0+10, y0+10, x0+10+(x1-x0)*.9, y0+10+(y1-y0)*.9))
                self.assertEqual(table_data.selection_cells(table, page), original)
                text = next(obj for obj in objects if isinstance(obj, EditableText) and obj.text=='Alpha')
                text.text = 'Updated'
                expected = [['Name', 'Notes', ''], ['Updated', 'Long note '*10, '0012']]
                self.assertEqual(table_data.selection_cells(table, page), expected)
                shapes = [obj for obj in objects if isinstance(obj, EditableShape)]
                texts = [obj for obj in objects if isinstance(obj, EditableText)]
                ok, error = pdf_handler.rebuild_page(doc, 0, texts, shapes, [])
                self.assertTrue(ok, error)
                doc.editor_page_models = {0: (texts, shapes, [], [])}
                page_state.persist(doc)
                with fitz.open(stream=doc.tobytes(), filetype='pdf') as reopened:
                    groups = page_state.load(reopened, 0)
                    self.assertEqual(table_data.selection_cells(TableSelection(groups[0]+groups[1]), reopened[0]), expected)

    def test_content_aware_widths_keep_short_columns_narrow_and_text_inside(self):
        with fitz.open() as doc:
            page = doc.new_page(width=600, height=800)
            pdf_handler.save_page_snapshot(doc, 0, force=True)
            cells = [['ID', 'Description'], ['1', 'A detailed description of the item']]
            objects = create_table_objects(page, cells, fit_columns=True)
            shapes = [obj for obj in objects if isinstance(obj, EditableShape)]
            texts = [obj for obj in objects if isinstance(obj, EditableText)]
            self.assertLess(fitz.Rect(shapes[0].bbox).width, fitz.Rect(shapes[1].bbox).width)
            ok, error = pdf_handler.rebuild_page(doc, 0, texts, shapes, [])
            self.assertTrue(ok, error)
            for word in page.get_text('words'):
                self.assertTrue(any(fitz.Rect(word[:4]) in fitz.Rect(shape.bbox) for shape in shapes))

    def test_copy_table_uses_current_values_and_respects_copy_permission(self):
        from pdflx.window import PdfEditorWindow
        with fitz.open() as doc:
            page = doc.new_page()
            table = TableSelection(create_table_objects(page, [['A', 'B'], ['1', '2']]))
            clipboard = SimpleNamespace(set=Mock())
            window = SimpleNamespace(doc=doc, selected_table=table,
                                     _active_session=SimpleNamespace(can_copy=True),
                                     get_clipboard=lambda: clipboard)
            PdfEditorWindow.copy_table(window)
            self.assertEqual(table_data.parse_table(clipboard.set.call_args.args[0]), [['A', 'B'], ['1', '2']])
            clipboard.set.reset_mock()
            window._active_session.can_copy = False
            PdfEditorWindow.copy_table(window)
            clipboard.set.assert_not_called()
