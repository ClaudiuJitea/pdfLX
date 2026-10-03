"""Verify image tilt in native PDF content and actual canvas transactions."""
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import cairo
import pymupdf as fitz

from pdflx import pdf_handler, page_state
from pdflx.models import EditableImage
from pdflx.window import PdfEditorWindow
from pdflx.undo_manager import RotateObjectCommand
from test_canvas_drag import CanvasHarness


def image_bytes():
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 40, 20)
    cr = cairo.Context(surface)
    # Deliberately asymmetric, with transparency and a different display aspect ratio.
    cr.set_source_rgb(1, 0, 0)
    cr.rectangle(0, 0, 20, 20)
    cr.fill()
    cr.set_source_rgb(0, 0, 1)
    cr.rectangle(20, 0, 20, 10)
    cr.fill()
    out = io.BytesIO()
    surface.write_to_png(out)
    return out.getvalue()


class ImageRotationTests(unittest.TestCase):
    def assert_placement(self, page, obj):
        infos = page.get_image_info()
        self.assertEqual(len(infos), 1, 'Rotation must not leave duplicate images')
        matrix = fitz.Matrix(infos[0]['transform'])
        rect = fitz.Rect(obj.bbox)
        rotation = pdf_handler.get_rotation_matrix(rect.x0+rect.width/2,
                                                   rect.y0+rect.height/2, obj.rotation)
        for uv, corner in zip(((0, 0), (1, 0), (0, 1), (1, 1)),
                              (rect.tl, rect.tr, rect.bl, rect.br)):
            actual = fitz.Point(*uv) * matrix
            expected = corner * rotation
            self.assertLess(abs(actual-expected), .001)

    def test_angles_crop_and_page_rotation_preserve_exact_placement(self):
        for page_angle in (0, 90, 180, 270):
            for angle in (0, 30, 90, 147, 270, 359):
                with self.subTest(page_angle=page_angle, angle=angle):
                    with fitz.open() as doc:
                        page = doc.new_page(width=600, height=800)
                        page.set_cropbox(fitz.Rect(50, 60, 550, 760))
                        page.set_rotation(page_angle)
                        obj = EditableImage((100, 150, 300, 230), 0, None,
                                            image_bytes(), is_new=True, rotation=angle)
                        pdf_handler._apply_single_object_to_page(doc, page, obj)
                        self.assert_placement(page, obj)
                        extracted, error = pdf_handler.extract_editable_images(doc, 0)
                        self.assertIsNone(error)
                        self.assertEqual(len(extracted), 1)
                        self.assertLess(abs((extracted[0].rotation-angle+180) % 360-180), .001)
                        for actual, expected in zip(extracted[0].bbox, obj.bbox):
                            self.assertAlmostEqual(actual, expected, places=3)
                        # Sample interior pixels in unrotated page coordinates.
                        page.set_rotation(0)
                        pix = page.get_pixmap()
                        matrix = fitz.Matrix(page.get_image_info()[0]['transform'])
                        for uv, rgb in (((.25, .5), (255, 0, 0)),
                                        ((.75, .25), (0, 0, 255)),
                                        ((.75, .75), (255, 255, 255))):
                            point = fitz.Point(*uv)*matrix
                            self.assertEqual(pix.pixel(round(point.x), round(point.y)), rgb)

    def test_drag_tilt_undo_redo_resize_and_reopen(self):
        obj = EditableImage((100, 150, 300, 230), 0, None, image_bytes(), is_new=True)
        window = CanvasHarness(obj)
        self.addCleanup(window.doc.close)
        window.selected_image = obj
        window._find_resize_handle_at_pos.return_value = 'rotate'
        window._handle_rotate_update = PdfEditorWindow._handle_rotate_update.__get__(window)
        gesture = SimpleNamespace(set_state=Mock(), get_current_event_state=lambda: 0)
        window.on_drag_begin(gesture, 250, 160)
        # Around the visual centre (250,230), from above to the right: clockwise 90°.
        window.on_drag_update(gesture, 70, 70)
        self.assertEqual(obj.rotation, 90)
        window.on_drag_end(gesture, 70, 70)
        self.assert_placement(window.doc[0], obj)
        window.undo_manager.undo()
        self.assertEqual(obj.rotation, 0)
        self.assert_placement(window.doc[0], obj)
        window.undo_manager.redo()
        self.assertEqual(obj.rotation, 90)
        self.assert_placement(window.doc[0], obj)
        obj.bbox = (110, 140, 350, 240)
        success, error = pdf_handler.rebuild_page(window.doc, 0, [], [], [obj])
        self.assertTrue(success, error)
        self.assert_placement(window.doc[0], obj)
        window.doc.editor_page_models = {0: ([], [], [obj], [])}
        page_state.persist(window.doc)
        with fitz.open(stream=window.doc.tobytes(), filetype='pdf') as reopened:
            groups = page_state.load(reopened, 0)
            restored = groups[2][0]
            self.assertEqual(restored.rotation, 90)
            self.assertEqual(restored.image_bytes, obj.image_bytes)
            self.assert_placement(reopened[0], restored)

    def test_certificate_button_opens_parent_signing_flow(self):
        from pdflx.signature_dialog import VisibleSignatureDialog
        parent = SimpleNamespace(on_sign_certificate=Mock())
        dialog = SimpleNamespace(get_transient_for=lambda: parent, close=Mock())
        VisibleSignatureDialog._open_certificate(dialog, None)
        dialog.close.assert_called_once()
        parent.on_sign_certificate.assert_called_once()

    def test_existing_image_can_be_tilted_repeatedly_without_ghosts(self):
        obj = EditableImage((100, 150, 300, 230), 0, None, image_bytes(), is_new=True)
        window = CanvasHarness(obj)
        self.addCleanup(window.doc.close)
        pdf_handler._apply_single_object_to_page(window.doc, window.doc[0], obj)
        pdf_handler.save_page_snapshot(window.doc, 0, force=True)
        images, error = pdf_handler.extract_editable_images(window.doc, 0)
        self.assertIsNone(error)
        obj = images[0]
        window.editable_images = [obj]
        window.selected_image = obj
        for angle in (30, 147, 270):
            command = RotateObjectCommand(window, obj, obj.rotation, angle)
            command.execute()
            self.assert_placement(window.doc[0], obj)
        command.undo()
        self.assertEqual(obj.rotation, 147)
        self.assert_placement(window.doc[0], obj)
