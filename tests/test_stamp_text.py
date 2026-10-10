"""Long stamp text wraps onto two lines instead of shrinking to unreadable size."""
import unittest

import pymupdf as fitz
from pdflx import document_tools as tools


class StampTextTests(unittest.TestCase):
    font=fitz.Font('helv')
    box=fitz.Rect(0,0,150,40)

    def test_short_text_stays_on_one_line(self):
        lines,_size=tools.stamp_lines(self.font,'VOID',self.box,24)
        self.assertEqual(lines,['VOID'])

    def test_long_text_wraps_into_balanced_lines_with_larger_type(self):
        text='SIGN HERE HELLO TO EVERYBODY AND TO EVERYONE'
        single=self.box.width/self.font.text_length(text,fontsize=1)
        lines,size=tools.stamp_lines(self.font,text,self.box,24)
        self.assertEqual(len(lines),2)
        self.assertEqual(' '.join(lines),text)
        self.assertGreater(size,single*1.5)
        widths=[self.font.text_length(line,fontsize=1) for line in lines]
        self.assertLess(max(widths)/min(widths),1.8)

    def test_wrapped_stamp_is_placed(self):
        doc=fitz.open()
        doc.new_page()
        style=dict(text='APPROVED BY THE BOARD OF DIRECTORS',color=(0,0,1),shape='rounded',border='Solid')
        xref=tools.place_stamp(doc,0,(20,20),'Custom',200,'',style)
        text=doc[0].load_annot(xref)
        self.assertIsNotNone(text)

    def test_dialog_limits_are_sane(self):
        self.assertLessEqual(tools.STAMP_TEXT_LIMIT,48)
        self.assertGreaterEqual(tools.STAMP_DETAILS_LIMIT,len('{datetime} {author}'))


if __name__=='__main__':
    unittest.main()
