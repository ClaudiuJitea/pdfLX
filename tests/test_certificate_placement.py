"""Canvas placement is reversible setup, not an unsigned mark added to the PDF."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
import pymupdf as fitz
from pdflx.certificate_placement import CertificatePlacement
from pdflx.certificate_appearance import template_png,pdf_box
from test_canvas_drag import CanvasHarness
from test_image_rotation import image_bytes
from pdflx.models import EditableImage


class CertificatePlacementTests(unittest.TestCase):
    def window(self,rotation=0,zoom=1):
        image=EditableImage((10,10,40,30),0,None,image_bytes(),is_new=True)
        window=CanvasHarness(image,rotation=rotation,zoom=zoom)
        window.on_tool_selected=Mock()
        dialog=SimpleNamespace(preview_png=template_png(),accept_placement=Mock(),present=Mock(),close=Mock())
        placement=CertificatePlacement(window,dialog)
        window._certificate_placement=placement
        window.tool_mode='certificate_signature'
        self.addCleanup(window.doc.close)
        return window,dialog,placement

    def test_drag_selects_position_size_at_all_page_rotations_and_zoom(self):
        for rotation in (0,90,180,270):
            for zoom in (.6,1.4):
                with self.subTest(rotation=rotation,zoom=zoom):
                    w,dialog,placement=self.window(rotation,zoom)
                    before=w.doc[0].get_contents()
                    gesture=SimpleNamespace(set_state=Mock())
                    w.on_drag_begin(gesture,50+80*zoom,40+100*zoom)
                    w.on_drag_update(gesture,240*zoom,75*zoom)
                    w.on_drag_end(gesture,240*zoom,75*zoom)
                    options=dialog.accept_placement.call_args.args[0]
                    visible=fitz.Rect(options['rect'])*w.doc[0].rotation_matrix
                    for actual,expected in zip(visible,(80,100,320,175)):
                        self.assertAlmostEqual(actual,expected,places=3)
                    pdf_box(w.doc.tobytes(),options)
                    self.assertEqual(w.doc[0].get_contents(),before)
                    self.assertIsNone(w._certificate_placement)
                    self.assertFalse(w.dragging_to_create)

    def test_click_default_size_clamps_inside_page(self):
        w,dialog,placement=self.window(90)
        self.assertTrue(placement.begin(790,590))
        placement.end(0,0)
        options=dialog.accept_placement.call_args.args[0]
        self.assertTrue(fitz.Rect(options['rect']) in w.doc[0].rect*w.doc[0].derotation_matrix)
        pdf_box(w.doc.tobytes(),options)

    def test_document_switch_during_drag_cancels_safely(self):
        w,dialog,placement=self.window()
        placement.begin(100,100)
        original=w.doc
        other=fitz.open();other.new_page();self.addCleanup(other.close)
        w.doc=other
        placement.end(200,75)
        dialog.close.assert_called_once()
        dialog.accept_placement.assert_not_called()
        self.assertIsNone(w._certificate_placement)
        w.doc=original

    def test_cancel_and_document_switch_do_not_modify_pdf(self):
        w,dialog,placement=self.window()
        before=w.doc[0].get_contents()
        placement.begin(100,100);placement.update(200,70)
        placement.cancel()
        dialog.present.assert_called_once()
        self.assertFalse(w.dragging_to_create)
        self.assertEqual(w.doc[0].get_contents(),before)
        other=fitz.open();other.new_page();self.addCleanup(other.close)
        original=w.doc;w.doc=other
        self.assertFalse(placement.begin(20,20))
        placement.cancel(False)
        dialog.close.assert_called_once()
        w.doc=original
