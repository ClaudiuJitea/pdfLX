import unittest
import pymupdf as fitz
from pdflx.reader_selection import characters,select

class ReaderSelectionTests(unittest.TestCase):
    def test_character_ranges_preserve_lines_and_rotation(self):
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation),fitz.open() as doc:
                page=doc.new_page()
                page.insert_text((50,100),'Select this sentence')
                page.insert_text((50,130),'Another line')
                page.set_rotation(rotation)
                items=characters(page)
                center=lambda item:(item[1].rect.tl+item[1].rect.br)/2
                a,b=center(items[0]),center(items[19])
                text,bounds,quads=select(items,a,b)
                self.assertEqual(text,'Select this sentence')
                self.assertEqual(select(items,b,a)[0],text)
                self.assertEqual(len(quads),20)
                self.assertTrue(fitz.Rect(bounds).contains(quads[0].rect))
                self.assertEqual(select(items)[0],'Select this sentence\nAnother line')
                self.assertEqual(select(items,center(items[7]),center(items[10]))[0],'this')

    def test_blank_pages_can_be_selected_without_errors(self):
        with fitz.open() as doc:
            page=doc.new_page()
            self.assertEqual(select(characters(page),(0,0),(100,100)),('',None,[]))
