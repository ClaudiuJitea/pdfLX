"""Native highlights share the freehand highlighter look and erase partially."""
import unittest

import pymupdf as fitz
from pdflx import highlight_tools


class HighlightToolsTests(unittest.TestCase):
    def page(self):
        doc=fitz.open()
        page=doc.new_page()
        page.insert_text((72,100),'Services provided accordingly',fontsize=11)
        return doc,page,[fitz.Rect(word[:4]) for word in page.get_text('words')]

    def test_marker_appearance_is_flat_and_translucent(self):
        doc,page,words=self.page()
        annot=highlight_tools.add(page,[word.quad for word in words],(1,.9,0))
        stream=doc.xref_get_key(annot.xref,'AP/N')[1].split()[0]
        content=doc.xref_stream(int(stream)).decode()
        # One continuous bar across the words, no rounded MuPDF ends.
        self.assertEqual(content.count(' re'),1)
        self.assertNotIn(' c\n',content)
        self.assertAlmostEqual(annot.opacity,highlight_tools.OPACITY)
        self.assertTrue(annot.rect.contains(words[0]|words[-1]))
        self.assertLess(annot.rect.height,words[0].height*1.5)

    def test_partial_erase_keeps_style_and_color(self):
        doc,page,words=self.page()
        highlight_tools.add(page,[word.quad for word in words],(.2,.7,.4),title='me')
        self.assertEqual(highlight_tools.erase(doc,0,[words[1]]),1)
        page=doc[0]
        annot=next(page.annots())
        self.assertEqual(len(annot.vertices),2*4)
        self.assertEqual(annot.info['title'],'me')
        self.assertAlmostEqual(annot.opacity,highlight_tools.OPACITY)
        self.assertTrue(all(abs(a-b)<.01 for a,b in zip(annot.colors['stroke'],(.2,.7,.4))))
        self.assertIsNone(highlight_tools.highlight_at(doc,0,(words[1].tl+words[1].br)/2))

    def test_thin_sweep_through_a_line_erases_it(self):
        doc,page,words=self.page()
        highlight_tools.add(page,[word.quad for word in words])
        middle=(words[1].y0+words[1].y1)/2
        sweep=fitz.Rect(words[1].x0,middle-1.5,words[1].x1,middle+1.5)
        self.assertEqual(highlight_tools.erase(doc,0,[sweep]),1)
        # Grazing the bottom edge of a line leaves it alone.
        graze=fitz.Rect(words[0].x0,words[0].y1-1,words[0].x1,words[0].y1+3)
        self.assertEqual(highlight_tools.erase(doc,0,[graze]),0)

    def test_freehand_stroke_split_and_hit(self):
        points=[(0,10),(100,10)]
        self.assertIsNone(highlight_tools.split_stroke(points,[fitz.Rect(0,30,100,40)]))
        runs=highlight_tools.split_stroke(points,[fitz.Rect(40,0,60,20)])
        self.assertEqual(len(runs),2)
        self.assertLess(runs[0][-1][0],40)
        self.assertGreaterEqual(runs[1][0][0],60)
        self.assertEqual(highlight_tools.split_stroke(points,[fitz.Rect(-5,0,105,20)]),[])
        self.assertTrue(highlight_tools.stroke_hit(points,14,(50,16)))
        self.assertFalse(highlight_tools.stroke_hit(points,14,(50,30)))


if __name__=='__main__':
    unittest.main()
