"""Native pixel, transform, persistence and transaction checks for image tools."""
import copy
import unittest
import pymupdf as fitz
from test_image_rotation import image_bytes
from test_canvas_drag import CanvasHarness
from pdflx.models import EditableImage
from pdflx import image_editing, pdf_handler, page_state
from pdflx.undo_manager import AddObjectCommand, EditObjectCommand


class ImageEditingTests(unittest.TestCase):
    def image(self, **style):
        obj=EditableImage((50,60,250,160),0,None,image_bytes(),is_new=True)
        obj.__dict__.update(style)
        return obj

    def render(self,obj):
        doc=fitz.open();page=doc.new_page(width=350,height=250)
        image_editing.insert_image(doc,page,obj)
        self.addCleanup(doc.close)
        return doc,page,page.get_pixmap()

    def test_flip_axes_keep_alpha(self):
        _,_,pix=self.render(self.image(flip_horizontal=True))
        self.assertEqual(pix.pixel(90,80),(0,0,255))
        self.assertEqual(pix.pixel(210,130),(255,0,0))
        self.assertEqual(pix.pixel(90,130),(255,255,255))
        _,_,pix=self.render(self.image(flip_vertical=True))
        self.assertEqual(pix.pixel(210,80),(255,255,255))
        self.assertEqual(pix.pixel(210,130),(0,0,255))

    def test_crop_clip_and_rounding_do_not_destroy_source(self):
        obj=self.image(crop=(.5,0,0,0),corner_radius=20)
        original=obj.image_bytes
        _,page,pix=self.render(obj)
        self.assertEqual(pix.pixel(150,80),(0,0,255))
        self.assertEqual(pix.pixel(49,80),(255,255,255))
        self.assertEqual(pix.pixel(51,61),(255,255,255))
        self.assertEqual(pix.pixel(150,140),(255,255,255))
        self.assertEqual(len(page.get_images()),1)
        self.assertEqual(obj.image_bytes,original)

    def test_opacity_border_and_shadow(self):
        _,_,pix=self.render(self.image(opacity=.5,border_width=6,border_color=(0,1,0),
                                     shadow=True,shadow_offset=12,shadow_opacity=.4))
        red=pix.pixel(80,68)
        self.assertEqual(red[0],255)
        self.assertTrue(120<red[1]<140)
        edge=pix.pixel(52,100)
        self.assertGreater(edge[1],edge[0])
        shadow=pix.pixel(260,100)
        self.assertTrue(190<shadow[0]<220)
        self.assertEqual(shadow[0],shadow[1])

    def test_crop_flip_on_rotated_cropped_pages(self):
        for angle in (0,90,180,270):
            with self.subTest(angle=angle),fitz.open() as doc:
                page=doc.new_page(width=500,height=450)
                page.set_cropbox(fitz.Rect(30,40,380,290));page.set_rotation(angle)
                obj=self.image(crop=(.5,0,0,0),flip_vertical=True,rotation=30,opacity=.8)
                image_editing.insert_image(doc,page,obj)
                self.assertEqual(page.rotation,angle)
                page.set_rotation(0)
                point=fitz.Point(150,135)*pdf_handler.get_rotation_matrix(150,110,30)
                rgb=page.get_pixmap().pixel(round(point.x),round(point.y))
                self.assertTrue(rgb[2]>240 and rgb[0]<70)

    def test_edit_undo_redo_and_saved_model(self):
        obj=self.image()
        window=CanvasHarness(obj);self.addCleanup(window.doc.close)
        AddObjectCommand(window,obj).execute()
        old=copy.deepcopy(obj.__dict__)
        new=copy.deepcopy(old)
        new.update(crop=(.2,.1,0,0),flip_horizontal=True,opacity=.55,
                   corner_radius=14,border_width=3,shadow=True,rotation=32)
        command=EditObjectCommand(window,obj,old,new)
        command.execute()
        self.assertEqual(obj.corner_radius,14)
        command.undo()
        self.assertEqual(obj.corner_radius,0)
        self.assertEqual(obj.crop,(0,0,0,0))
        command.execute()
        window.doc.editor_page_models={0:([],[],[obj],[])}
        page_state.persist(window.doc)
        with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as saved:
            loaded=page_state.load(saved,0)[2][0]
            self.assertEqual(loaded.crop,obj.crop)
            self.assertEqual(loaded.image_bytes,obj.image_bytes)
            self.assertEqual(loaded.shadow,True)
            self.assertEqual(len(saved[0].get_image_info()),1)

    def test_replace_existing_image_then_undo_without_ghosts(self):
        original=self.image()
        window=CanvasHarness(original);self.addCleanup(window.doc.close)
        image_editing.insert_image(window.doc,window.doc[0],original)
        pdf_handler.save_page_snapshot(window.doc,0,force=True)
        images,error=pdf_handler.extract_editable_images(window.doc,0)
        self.assertIsNone(error)
        obj=images[0];window.editable_images=[obj]
        old=copy.deepcopy(obj.__dict__)
        green=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,40,20),False)
        green.set_rect(green.irect,(0,255,0))
        new=dict(old,image_bytes=green.tobytes('png'),corner_radius=12,shadow=True)
        command=EditObjectCommand(window,obj,old,new)
        command.execute()
        self.assertEqual(window.doc[0].get_pixmap().pixel(100,90),(0,255,0))
        self.assertEqual(len(window.doc[0].get_image_info()),1)
        command.undo()
        self.assertEqual(obj.image_bytes,old['image_bytes'])
        self.assertEqual(window.doc[0].get_pixmap().pixel(100,90),(255,0,0))
        self.assertEqual(len(window.doc[0].get_image_info()),1)

    def test_invalid_crop_cannot_mutate_page(self):
        with fitz.open() as doc:
            page=doc.new_page()
            before=(doc.xref_length(),page.get_contents(),page.rotation)
            with self.assertRaises(ValueError):
                image_editing.insert_image(doc,page,self.image(crop=(.6,0,.4,0)))
            self.assertEqual((doc.xref_length(),page.get_contents(),page.rotation),before)

    def test_preview_preserves_source_and_style(self):
        obj=self.image(corner_radius=25,shadow=True,rotation=35,border_width=3,border_color=(.2,.6,.3))
        original=copy.deepcopy(obj.__dict__)
        png=image_editing.preview(obj)
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertEqual(obj.__dict__,original)
        with open('/tmp/pdflx-image-style-preview.png','wb') as out:out.write(png)
