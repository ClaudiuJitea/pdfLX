"""Modern note marker rendering, persistence, and legacy note editing."""
import unittest
import pymupdf as fitz
from pdflx import document_tools as tools


class NoteAppearanceTests(unittest.TestCase):
    def appearance(self, doc, page_number, xref):
        page=doc[page_number]
        annot=page.load_annot(xref)
        kind,value=doc.xref_get_key(xref,'AP/N')
        self.assertEqual(kind,'xref')
        stream=doc.xref_stream(int(value.split()[0]))
        self.assertIn(b'5 12.5 m 13 12.5 l S',stream)
        self.assertEqual(annot.type[0],fitz.PDF_ANNOT_TEXT)
        self.assertEqual(annot.info['name'],'Comment')
        return stream

    def test_vector_bubble_survives_save_and_reopen_at_each_rotation(self):
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation),fitz.open() as doc:
                page=doc.new_page(width=300,height=400)
                page.set_rotation(rotation)
                xref=tools.add_review(doc,0,'note',(50,60,70,80),'Hello\nSecond line','Reviewer')
                stream=self.appearance(doc,0,xref)
                pixels=doc[0].get_pixmap().samples
                with fitz.open(stream=doc.tobytes(),filetype='pdf') as reopened:
                    saved_page=reopened[0]
                    note=next(saved_page.annots())
                    self.assertEqual(self.appearance(reopened,0,note.xref),stream)
                    self.assertEqual(note.info['content'],'Hello\nSecond line')
                    self.assertEqual(note.info['title'],'Reviewer')
                    self.assertEqual(reopened[0].get_pixmap().samples,pixels)
                    native_center=fitz.Rect(note.rect).tl+(9,9)
                    self.assertEqual(tools.note_at_point(reopened,0,native_center)['xref'],note.xref)

    def test_editing_legacy_folded_note_replaces_marker_without_losing_comment(self):
        with fitz.open() as doc:
            page=doc.new_page(width=100,height=100)
            note=page.add_text_annot((30,30),'Old note',icon='Note')
            xref=note.xref
            tools.edit_annotation(doc,0,xref,'Updated comment','Author')
            self.appearance(doc,0,xref)
            annot=doc[0].load_annot(xref)
            self.assertEqual(annot.info['content'],'Updated comment')
            self.assertEqual(annot.info['title'],'Author')
            pix=doc[0].get_pixmap()
            colors=[pix.pixel(x,y) for y in range(pix.height) for x in range(pix.width)]
            self.assertTrue(any(r>200 and g>180 and b<190 for r,g,b in colors))
            self.assertFalse(any(b>r+50 for r,g,b in colors))

    def test_other_review_annotations_keep_their_own_appearance(self):
        with fitz.open() as doc:
            doc.new_page(width=100,height=100)
            xref=tools.add_review(doc,0,'underline',(20,20,60,30),'Review')
            tools.edit_annotation(doc,0,xref,'Updated','Author')
            page=doc[0];annot=page.load_annot(xref)
            self.assertEqual(annot.type[0],fitz.PDF_ANNOT_UNDERLINE)
            self.assertNotEqual(annot.info.get('name'),'Comment')
