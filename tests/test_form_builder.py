"""Form builder helpers: names from labels, label lookup, detection, snapping and tab order."""
import unittest

import pymupdf as fitz

from pdflx import form_builder as b
from pdflx import document_tools as tools


def blank_form():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((40, 122), 'Departure Date (YYYY-MM-DD)', fontsize=9)
    page.insert_text((300, 122), 'Room Preference', fontsize=9)
    page.draw_rect(fitz.Rect(40, 127, 260, 149), color=(0.8, 0.85, 0.9), fill=(0.97, 0.98, 0.99))
    page.draw_rect(fitz.Rect(300, 127, 555, 149), color=(0.8, 0.85, 0.9), fill=(0.97, 0.98, 0.99))
    page.insert_text((40, 200), 'Name:', fontsize=10)
    page.draw_line((80, 200), (300, 200))
    page.draw_rect(fitz.Rect(40, 240, 56, 256), color=(0, 0, 0))
    page.insert_text((62, 252), 'I agree to the terms', fontsize=9)
    page.draw_rect(fitz.Rect(40, 300, 555, 340), color=(0.8, 0.85, 0.9))
    page.insert_text((40, 295), 'Notes', fontsize=9)
    page.draw_rect(fitz.Rect(0, 0, 595, 80), fill=(0.08, 0.58, 0.45))  # dark header: not a field
    return doc


class FieldNames(unittest.TestCase):
    def test_names(self):
        self.assertEqual(b.field_name('Departure Date (YYYY-MM-DD)', set()), 'departure_date')
        self.assertEqual(b.field_name('First Name *', {'first_name'}), 'first_name_2')
        self.assertEqual(b.field_name('', set(), 'checkbox'), 'checkbox')
        long = b.field_name('I confirm that I have or will arrange adequate travel insurance.', set())
        self.assertLessEqual(len(long), 32)
        self.assertFalse(long.endswith('_'))


class Detection(unittest.TestCase):
    def test_detects_boxes_lines_and_checkboxes(self):
        page = blank_form()[0]
        found = b.detect_fields(page)
        kinds = sorted(kind for kind, _rect in found)
        self.assertEqual(kinds, ['checkbox', 'multiline', 'text', 'text', 'text'])
        names = {b.field_name(b.nearby_label(page, rect, right=kind == 'checkbox'), set()) for kind, rect in found}
        self.assertEqual(names, {'departure_date', 'room_preference', 'name', 'i_agree_to_the_terms', 'notes'})

    def test_skips_existing_fields_and_filled_boxes(self):
        doc = blank_form()
        tools.create_form_field(doc, 0, 'departure', 'text', (40, 127, 260, 149))
        doc[0].insert_text((305, 142), 'Suite', fontsize=10)
        found = b.detect_fields(doc[0])
        self.assertFalse(any(fitz.Rect(r).intersects(fitz.Rect(40, 127, 260, 149)) for _k, r in found))
        self.assertFalse(any(fitz.Rect(r).intersects(fitz.Rect(300, 127, 555, 149)) for _k, r in found))


class Snapping(unittest.TestCase):
    def test_move_snaps_nearest_edge(self):
        rect, guides = b.snap_rect((43, 200, 243, 222), [(40, 100, 240, 122)], 5, move=True)
        self.assertEqual(rect.x0, 40)
        self.assertIn(('v', 40), guides)

    def test_resize_snaps_only_moving_edges(self):
        rect, _guides = b.snap_rect((40, 200, 238, 222), [(100, 100, 240, 122)], 5, edges=('x1',))
        self.assertEqual((rect.x0, rect.x1), (40, 240))

    def test_far_edges_do_not_snap(self):
        rect, guides = b.snap_rect((60, 200, 200, 222), [(40, 100, 240, 122)], 5, move=True)
        self.assertEqual(rect.x0, 60)
        self.assertEqual([g for g in guides if g[0] == 'v'], [])


class TabOrder(unittest.TestCase):
    def test_rows_and_columns(self):
        doc = fitz.open()
        doc.new_page()
        for name, rect in (('c', (40, 200, 140, 220)), ('b', (300, 100, 400, 120)), ('a', (40, 100, 140, 120)),
                           ('d', (300, 200, 400, 220))):
            tools.create_form_field(doc, 0, name, 'text', rect)
        b.tab_order(doc, 0, 'rows')
        self.assertEqual([w.field_name for w in doc[0].widgets()], ['a', 'b', 'c', 'd'])
        b.tab_order(doc, 0, 'columns')
        self.assertEqual([w.field_name for w in doc[0].widgets()], ['a', 'c', 'b', 'd'])
        self.assertEqual(doc.xref_get_key(doc[0].xref, 'Tabs')[1], '/C')


if __name__ == '__main__':
    unittest.main()
