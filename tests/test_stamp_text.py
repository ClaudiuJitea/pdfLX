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

    def test_every_template_and_shape_places(self):
        import gi
        gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')
        from pdflx.stamp_dialog import TEMPLATES
        from pdflx.stamp_shapes import SHAPES
        names=[name for name,_style in TEMPLATES]
        self.assertNotIn('Sign here',names)
        for name in ('Verified','Top priority','Thank you','On hold','Archived','Scanned','Original'):
            self.assertIn(name,names)
        used={style['shape'] for _name,style in TEMPLATES}
        self.assertTrue({'hexagon','burst','tag','ticket'}<=used)
        doc=fitz.open()
        doc.new_page(width=600,height=900)
        for name,style in TEMPLATES:
            tools.place_stamp(doc,0,(20,20),name,170,'Reviewer',dict(style))
        for shape in SHAPES:
            tools.place_stamp(doc,0,(20,400),'Custom',170,'',dict(TEMPLATES[0][1],shape=shape,text='SHAPE TEST'))
        page=doc[0]
        self.assertEqual(len(list(page.annots())),len(TEMPLATES)+len(SHAPES))

    def test_opacity_changes_the_rendered_stamp(self):
        def darkness(opacity,angle=0):
            doc=fitz.open()
            doc.new_page(width=300,height=200)
            style=dict(text='APPROVED',color=(0.1,0.2,0.7),shape='rounded',border='Double',
                       opacity=opacity,angle=angle,fill=True)
            tools.place_stamp(doc,0,(20,20),'Custom',200,'',style)
            with fitz.open(stream=doc.tobytes(),filetype='pdf') as saved:
                samples=saved[0].get_pixmap(alpha=False).samples
            return sum(255-value for value in samples)
        solid,faded,tilted=darkness(1),darkness(0.3),darkness(0.3,angle=-15)
        self.assertLess(faded,solid*0.5)
        self.assertLess(tilted,solid*0.5)


if __name__=='__main__':
    unittest.main()
