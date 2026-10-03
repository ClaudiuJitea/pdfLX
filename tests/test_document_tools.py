"""Regression coverage for atomic edits, persistent models, and native PDF tools."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pymupdf as fitz
from test_canvas_drag import CanvasHarness, PdfEditorWindow
from pdflx import pdf_handler, document_tools as tools, document_features, text_geometry
from pdflx.models import EditableText, EditableShape
from pdflx.undo_manager import (AddObjectCommand, AddTableCommand, EditObjectCommand,
                                            EditTableCommand, DocumentMutationCommand,
                                            DeleteObjectCommand, CompositeCommand)
from pdflx.table_creation import create_table_objects, TableSelection
from pdflx.page_state import load as load_state


@unittest.skipUnless(PdfEditorWindow,'GTK bindings required')
class DocumentToolsTests(unittest.TestCase):
    def window(self, pages=1, rotation=0):
        window=CanvasHarness(EditableShape('rectangle',(0,0,0,0)),rotation)
        window.editable_shapes=[]
        for _ in range(pages-1):
            window.doc.new_page(width=600,height=800)
        window._active_session=SimpleNamespace(page_objects={},can_edit=True)
        for method in ('_load_page','_mutate_document','_change_pages'):
            setattr(window,method,getattr(PdfEditorWindow,method).__get__(window))
        for method in ('hide_text_editor','_sync_thumbnail_selection','_apply_and_hide_editor','_clear_search','_report_command_error'):
            setattr(window,method,Mock())
        window.pdf_view.set_content_width=Mock()
        window.pdf_view.set_content_height=Mock()
        window.document_modified=False
        self.addCleanup(window.doc.close)
        self.cache(window)
        return window

    def cache(self,window):
        groups=(window.editable_texts,window.editable_shapes,window.editable_images,window.editable_strokes)
        window._active_session.page_objects[window.current_page_index]=groups
        window.doc.editor_page_models=window._active_session.page_objects
        return groups

    def insert_table(self,window):
        objects=create_table_objects(window.doc[window.current_page_index],[['Name','Value'],['Alpha','123']],width_percent=50)
        command=AddTableCommand(window,objects)
        self.assertIsNot(command.execute(),False)
        window.undo_manager.add_command(command)
        self.cache(window)
        return objects

    def assert_text(self,doc,*values):
        text=''.join(page.get_text() for page in doc)
        for value in values:
            self.assertIn(value,text)

    def test_rebuild_rolls_back_page_on_missing_font(self):
        window=self.window()
        window.doc[0].insert_text((20,50),'Keep this text')
        pdf_handler.save_page_snapshot(window.doc,0,force=True)
        obj=EditableText(20,100,'Ω',baseline=120,page_number=0,is_new=True)
        before=window.doc[0].get_pixmap().samples
        with patch.object(pdf_handler,'find_specific_font_variant',return_value=None),patch.object(pdf_handler,'get_default_unicode_font_path',return_value=None):
            success,error=pdf_handler.rebuild_page(window.doc,0,[obj],[],[])
        self.assertFalse(success)
        self.assertIn('Unicode',error)
        self.assertEqual(window.doc[0].get_pixmap().samples,before)
        self.assert_text(window.doc,'Keep this text')

    def test_failed_text_edit_restores_original_pdf_model_and_history(self):
        window=self.window()
        window.doc[0].insert_text((20,50),'Original')
        window.editable_texts=pdf_handler.extract_editable_text(window.doc,0)[0]
        self.cache(window)
        pdf_handler.save_page_snapshot(window.doc,0,force=True)
        obj=window.editable_texts[0]
        old=copy.deepcopy(obj.__dict__)
        obj.text='Ω'
        command=EditObjectCommand(window,obj,old,copy.deepcopy(obj.__dict__))
        with patch.object(pdf_handler,'find_specific_font_variant',return_value=None),patch.object(pdf_handler,'get_default_unicode_font_path',return_value=None):
            self.assertIs(command.execute(),False)
        window.undo_manager.add_command(command)
        self.assertEqual(obj.text,'Original')
        self.assert_text(window.doc,'Original')
        self.assertEqual(window.undo_manager.undo_stack,[])
        self.assertFalse(getattr(obj,'_ghost_redacted',False))

    def test_page_navigation_keeps_table_objects_and_history(self):
        window=self.window(pages=2)
        objects=self.insert_table(window)
        window._load_page(1)
        window._load_page(0)
        self.assertIs(window.editable_shapes[0],objects[0])
        self.assertEqual(len(window.undo_manager.undo_stack),1)
        window._load_page(1)
        window.undo_manager.undo()
        self.assertEqual(window.current_page_index,0)
        self.assertNotIn('Alpha',window.doc[0].get_text())
        window.undo_manager.redo()
        self.assert_text(window.doc,'Alpha')

    def test_table_reopens_grouped_and_moves_without_duplicate_content(self):
        window=self.window()
        window.doc[0].insert_text((20,30),'Existing text')
        pdf_handler.save_page_snapshot(window.doc,0,force=True)
        objects=self.insert_table(window)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'table.pdf'
            self.assertEqual(pdf_handler.save_document(window.doc,str(path)),(True,None))
            with fitz.open(path) as reopened:
                groups=load_state(reopened,0)
                members=[obj for group in groups for obj in group if hasattr(obj,'table_id')]
                self.assertEqual(len(members),len(objects))
                self.assertEqual(len({obj.table_id for obj in members}),1)
                other=self.window()
                original_doc=other.doc
                other.doc=reopened
                other.editable_texts,other.editable_shapes,other.editable_images,other.editable_strokes=groups
                self.cache(other)
                old=[copy.deepcopy(obj.__dict__) for obj in members]
                selection=TableSelection(members)
                x0,y0,x1,y1=selection.bbox
                selection.transform(old,selection.bbox,(x0+10,y0+20,x1+10,y1+20))
                command=EditTableCommand(other,members,old,[copy.deepcopy(obj.__dict__) for obj in members])
                self.assertIsNot(command.execute(),False)
                self.assertEqual(reopened[0].get_text().count('Alpha'),1)
                self.assert_text(reopened,'Existing text')
                self.assertIsNot(command.undo(),False)
                self.assertEqual([obj.bbox for obj in members],[state['bbox'] for state in old])
                other.doc=original_doc

    def test_grouped_delete_undo_returns_to_affected_page(self):
        window=self.window(pages=2)
        objects=self.insert_table(window)
        command=CompositeCommand(window,[DeleteObjectCommand(window,obj) for obj in objects])
        self.assertIsNot(command.execute(),False)
        window.undo_manager.add_command(command)
        window._load_page(1)
        window.undo_manager.undo()
        self.assertEqual(window.current_page_index,0)
        self.assert_text(window.doc,'Alpha')
        window._load_page(1)
        window.undo_manager.redo()
        self.assertEqual(window.current_page_index,0)
        self.assertNotIn('Alpha',window.doc[0].get_text())

    def test_note_click_opens_comment_at_every_rotation_and_zoom(self):
        for rotation in (0,90,180,270):
            for view_mode in (False,True):
                with self.subTest(rotation=rotation,view_mode=view_mode):
                    window=self.window(rotation=rotation)
                    window.zoom_level=2
                    window.current_pdf_page_width=window.doc[0].rect.width*2
                    window.current_pdf_page_height=window.doc[0].rect.height*2
                    xref=tools.add_review(window.doc,0,'note',(60,80,90,110),'First line\nSecond line')
                    note=tools.annotations(window.doc)[0]
                    center=fitz.Rect(note['rect']).tl+(9,9)
                    visual=center*window.doc[0].rotation_matrix
                    window.document_tools=SimpleNamespace(open_note_bubble=Mock(),close_note_bubble=Mock())
                    window.view_mode=view_mode
                    PdfEditorWindow.on_pdf_view_pressed(window,None,1,visual.x*2+50,visual.y*2+40)
                    opened=window.document_tools.open_note_bubble.call_args.args[0]
                    self.assertEqual(opened['xref'],xref)
                    self.assertEqual(opened['content'],'First line\nSecond line')
                    self.assertIsNone(tools.note_at_point(window.doc,0,(400,400)))

    def test_multiline_note_edit_is_saved_and_undoable(self):
        window=self.window()
        xref=tools.add_review(window.doc,0,'note',(40,40,60,60),'Original note')
        window._mutate_document(lambda:tools.edit_annotation(window.doc,0,xref,'Line one\nLine two','Reviewer'))
        self.assertEqual(tools.annotations(window.doc)[0]['content'],'Line one\nLine two')
        window.undo_manager.undo()
        self.assertEqual(tools.annotations(window.doc)[0]['content'],'Original note')
        window.undo_manager.redo()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'notes.pdf'
            self.assertEqual(pdf_handler.save_document(window.doc,str(path)),(True,None))
            with fitz.open(path) as saved:
                self.assertEqual(tools.annotations(saved)[0]['content'],'Line one\nLine two')

    def test_stamp_presets_stay_upright_and_inside_rotated_pages(self):
        for rotation in (0,90,180,270):
            for preset in ('Approved','Draft','Confidential','Final','Not approved'):
                with self.subTest(rotation=rotation,preset=preset):
                    window=self.window(rotation=rotation)
                    visible=window.doc[0].rect
                    point=(visible.width-5,visible.height-5)
                    self.assertTrue(window._mutate_document(lambda:tools.place_stamp(window.doc,0,point,preset,160,'Reviewer')))
                    page=window.doc[0]
                    annotation=next(page.annots())
                    visual=annotation.rect*page.rotation_matrix
                    self.assertGreaterEqual(visual.x0,-0.01)
                    self.assertGreaterEqual(visual.y0,-0.01)
                    self.assertLessEqual(visual.x1,visible.width+0.01)
                    self.assertLessEqual(visual.y1,visible.height+0.01)
                    self.assertAlmostEqual(visual.width,160,places=2)
                    self.assertEqual(annotation.rotation,rotation)
                    self.assertEqual(annotation.info['content'],preset)
                    self.assertEqual(annotation.info['title'],'Reviewer')
                    window.undo_manager.undo()
                    self.assertEqual(tools.annotations(window.doc),[])
                    window.undo_manager.redo()
                    with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as saved:
                        self.assertEqual(tools.annotations(saved)[0]['content'],preset)

    def test_custom_stamp_style_survives_save_rotation_and_undo(self):
        import numpy as np
        reference=None
        style=dict(text='REVIEWED Ω',color=(0,0.6,0),font_family='DejaVu Serif',
                   font_size=20,bold=False,border='Double',opacity=0.65)
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation):
                window=self.window(rotation=rotation)
                count=window.doc.page_count
                self.assertTrue(window._mutate_document(lambda:tools.place_stamp(window.doc,0,(60,60),style=style)))
                self.assertEqual(window.doc.page_count,count)
                page=window.doc[0]
                annotation=next(page.annots())
                self.assertEqual(annotation.info['content'],'REVIEWED Ω')
                self.assertAlmostEqual(annotation.opacity,0.65,places=5)
                visual=annotation.rect*page.rotation_matrix
                pixels=page.get_pixmap(clip=visual,matrix=fitz.Matrix(2,2))
                raster=np.frombuffer(pixels.samples,dtype=np.uint8).reshape(-1,3)
                self.assertTrue(np.any((raster[:,1]>raster[:,0]) & (raster[:,1]>raster[:,2])))
                if reference is None:
                    reference=raster
                else:
                    self.assertLess(np.abs(raster.astype(float)-reference).mean(),1)
                with tempfile.TemporaryDirectory() as directory:
                    path=Path(directory)/'styled-stamp.pdf'
                    self.assertEqual(pdf_handler.save_document(window.doc,str(path)),(True,None))
                    with fitz.open(path) as reopened:
                        saved=reopened[0]
                        note=next(saved.annots())
                        self.assertEqual(note.info['content'],'REVIEWED Ω')
                        self.assertEqual(saved.get_pixmap(clip=visual,matrix=fitz.Matrix(2,2)).samples,pixels.samples)
                        self.assertEqual(reopened.xref_get_key(note.xref,'PdfLXStampStyle')[0],'string')
                window.undo_manager.undo()
                self.assertEqual(tools.annotations(window.doc),[])
                window.undo_manager.redo()
                self.assertEqual(tools.annotations(window.doc)[0]['content'],'REVIEWED Ω')

    def test_stamp_details_fields_borders_and_templates(self):
        import json
        import time
        from pdflx.stamp_shapes import BORDERS
        rasters=set()
        for border in BORDERS:
            with self.subTest(border=border):
                window=self.window()
                style=dict(text='RECEIVED',details='{author} · {date}',color=(0.4,0.2,0.6),border=border,
                           fill=border=='Solid',italic=border=='Dashed',shape='rounded')
                xref=tools.place_stamp(window.doc,0,(60,60),'Received',170,'Jane Doe',style)
                page=window.doc[0]
                annotation=page.load_annot(xref)
                stored=json.loads(window.doc.xref_get_key(xref,'PdfLXStampStyle')[1])
                # Fields are expanded once, so editing later keeps the original date.
                self.assertEqual(stored['details'],'Jane Doe · '+time.strftime('%d %b %Y'))
                self.assertEqual(stored['stamp'],'Received')
                self.assertEqual(annotation.info['content'],'RECEIVED')
                self.assertIn('Jane Doe',page.get_text(clip=annotation.rect))
                rasters.add(page.get_pixmap(clip=annotation.rect).samples)
        self.assertEqual(len(rasters),len(BORDERS))
        window=self.window()
        with self.assertRaises(ValueError):
            tools.place_stamp(window.doc,0,(60,60),'Received',170)  # unstyled stamps must be standard
        with self.assertRaises(ValueError):
            tools.place_stamp(window.doc,0,(60,60),style=dict(text='X',border='Wavy'))

    def test_stamp_shapes_remain_distinct_native_annotations_after_saving(self):
        import json
        from pdflx.stamp_shapes import SHAPES
        appearances=set()
        for shape in SHAPES:
            for rotation in (0,90):
                with self.subTest(shape=shape,rotation=rotation):
                    window=self.window(rotation=rotation)
                    style=dict(shape=shape,text='APPROVED',color=(0.7,0.1,0.2),
                               font_family='DejaVu Sans',font_size=20,bold=True,
                               border='Double',opacity=0.8)
                    self.assertTrue(window._mutate_document(
                        lambda:tools.place_stamp(window.doc,0,(60,60),width=180,style=style)))
                    page=window.doc[0]
                    annotation=next(page.annots())
                    bounds=annotation.rect*page.rotation_matrix
                    self.assertAlmostEqual(bounds.width,180,places=2)
                    if shape in ('circle','seal'):
                        self.assertAlmostEqual(bounds.width,bounds.height,places=2)
                    raster=page.get_pixmap(clip=bounds).samples
                    if rotation==0:
                        appearances.add(raster)
                    with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as saved:
                        saved_page=saved[0]
                        saved_annotation=next(saved_page.annots())
                        stored=json.loads(saved.xref_get_key(saved_annotation.xref,'PdfLXStampStyle')[1])
                        self.assertEqual(stored['shape'],shape)
                        self.assertEqual(saved_annotation.type[0],fitz.PDF_ANNOT_STAMP)
                        self.assertEqual(saved_page.get_pixmap(clip=bounds).samples,raster)
                    window.undo_manager.undo()
                    self.assertEqual(tools.annotations(window.doc),[])
                    window.undo_manager.redo()
                    self.assertEqual(window.doc[0].get_pixmap(clip=bounds).samples,raster)
        self.assertEqual(len(appearances),len(SHAPES))

    def test_stamp_drag_preserves_appearance_and_commits_once(self):
        from pdflx.stamp_interaction import StampInteraction
        for rotation in (0,90,180,270):
            for styled in (False,True):
                with self.subTest(rotation=rotation,styled=styled):
                    window=self.window(rotation=rotation)
                    window.zoom_level=1.7
                    window.current_pdf_page_width=window.doc[0].rect.width*1.7
                    window.current_pdf_page_height=window.doc[0].rect.height*1.7
                    window.stamp_interaction=StampInteraction(window)
                    window.tool_mode='stamp' if styled else 'select'
                    style=(dict(shape='seal',text='APPROVED Ω',color=(0.1,0.6,0.2),
                                font_family='DejaVu Sans',font_size=20,bold=True,
                                border='Double',opacity=0.65) if styled else None)
                    xref=tools.place_stamp(window.doc,0,(60,60),width=160,style=style)
                    page=window.doc[0]
                    original=page.load_annot(xref).rect*page.rotation_matrix
                    appearance=page.get_pixmap(clip=original).samples
                    metadata=window.doc.xref_get_key(xref,'PdfLXStampStyle')
                    ap=window.doc.xref_get_key(xref,'AP')
                    gesture=SimpleNamespace(set_state=Mock())
                    center=(original.tl+original.br)/2
                    window.on_drag_begin(gesture,center.x*1.7+50,center.y*1.7+40)
                    self.assertIsNotNone(window.stamp_interaction.drag)
                    for dx,dy in ((20,10),(50,30),(100,70)):
                        window.on_drag_update(gesture,dx*1.7,dy*1.7)
                        page=window.doc[0]
                        self.assertEqual(page.load_annot(xref).rect*page.rotation_matrix,original)
                    window.on_drag_end(gesture,170,119)
                    self.assertEqual(len(window.undo_manager.undo_stack),1)
                    page=window.doc[0]
                    moved=page.load_annot(xref).rect*page.rotation_matrix
                    self.assertAlmostEqual(moved.x0,original.x0+100,places=3)
                    self.assertAlmostEqual(moved.y0,original.y0+70,places=3)
                    self.assertEqual(page.get_pixmap(clip=moved).samples,appearance)
                    self.assertEqual(window.doc.xref_get_key(xref,'AP'),ap)
                    self.assertEqual(window.doc.xref_get_key(xref,'PdfLXStampStyle'),metadata)
                    with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as saved:
                        self.assertEqual(saved[0].get_pixmap(clip=moved).samples,appearance)
                    window.undo_manager.undo()
                    page=window.doc[0]
                    self.assertEqual(page.load_annot(xref).rect*page.rotation_matrix,original)
                    window.undo_manager.redo()
                    page=window.doc[0]
                    self.assertEqual(page.load_annot(xref).rect*page.rotation_matrix,moved)

    def test_stamp_drag_clamps_to_page_and_cancellation_keeps_pdf_unchanged(self):
        from pdflx.stamp_interaction import StampInteraction
        window=self.window(rotation=90)
        window.stamp_interaction=StampInteraction(window)
        xref=tools.place_stamp(window.doc,0,(60,60),width=160)
        page=window.doc[0]
        original=page.load_annot(xref).rect*page.rotation_matrix
        center=(original.tl+original.br)/2
        self.assertTrue(window.stamp_interaction.begin(center.x,center.y))
        window.stamp_interaction.end(0,0)
        self.assertIsNotNone(window.stamp_interaction.selected)
        self.assertEqual(window.undo_manager.undo_stack,[])
        self.assertTrue(window.stamp_interaction.begin(center.x,center.y))
        window.stamp_interaction.update(10000,-10000)
        target=window.stamp_interaction.drag['target']
        self.assertAlmostEqual(target.x1,page.rect.width)
        self.assertAlmostEqual(target.y0,0)
        preview=window.stamp_interaction.preview_doc
        window.stamp_interaction.cancel()
        self.assertTrue(preview.is_closed)
        page=window.doc[0]
        self.assertEqual(page.load_annot(xref).rect*page.rotation_matrix,original)
        self.assertEqual(window.undo_manager.undo_stack,[])
        window.view_mode=True
        self.assertFalse(window.stamp_interaction.begin(center.x,center.y))
        window.view_mode=False
        window._active_session.can_edit=False
        self.assertFalse(window.stamp_interaction.begin(center.x,center.y))

    def test_stamp_corner_resize_preserves_shape_appearance_and_undo(self):
        from pdflx.stamp_interaction import StampInteraction
        for rotation in (0,90,180,270):
            for shape in (None,'circle','ribbon'):
                for handle in ('nw','ne','se','sw'):
                    with self.subTest(rotation=rotation,shape=shape,handle=handle):
                        window=self.window(rotation=rotation)
                        window.zoom_level=1.5
                        window.current_pdf_page_width=window.doc[0].rect.width*1.5
                        window.current_pdf_page_height=window.doc[0].rect.height*1.5
                        window.stamp_interaction=StampInteraction(window)
                        style=(dict(shape=shape,text='APPROVED',color=(0.1,0.6,0.2),
                                    font_family='DejaVu Sans',font_size=20,bold=True,
                                    border='Double',opacity=0.65) if shape else None)
                        xref=tools.place_stamp(window.doc,0,(180,220),width=160,style=style)
                        page=window.doc[0]
                        original=page.load_annot(xref).rect*page.rotation_matrix
                        ap=window.doc.xref_get_key(xref,'AP')
                        metadata=window.doc.xref_get_key(xref,'PdfLXStampStyle')
                        note=tools.annotations(window.doc)[0]
                        window.stamp_interaction.select(note)
                        corner=dict(nw=original.tl,ne=original.tr,se=original.br,sw=original.bl)[handle]
                        gesture=SimpleNamespace(set_state=Mock())
                        window.on_drag_begin(gesture,corner.x*1.5+50,corner.y*1.5+40)
                        self.assertEqual(window.stamp_interaction.drag['handle'],handle)
                        dx=(-1 if 'w' in handle else 1)*original.width*0.5
                        dy=(-1 if 'n' in handle else 1)*original.height*0.5
                        window.on_drag_update(gesture,dx*1.5,dy*1.5)
                        self.assertEqual(page.load_annot(xref).rect*page.rotation_matrix,original)
                        window.on_drag_end(gesture,dx*1.5,dy*1.5)
                        page=window.doc[0]
                        resized=page.load_annot(xref).rect*page.rotation_matrix
                        self.assertAlmostEqual(resized.width,original.width*1.5,places=3)
                        self.assertAlmostEqual(resized.height,original.height*1.5,places=3)
                        anchor=dict(nw='br',ne='bl',se='tl',sw='tr')[handle]
                        self.assertLess(abs(getattr(resized,anchor)-getattr(original,anchor)),0.001)
                        self.assertEqual(window.doc.xref_get_key(xref,'AP'),ap)
                        self.assertEqual(window.doc.xref_get_key(xref,'PdfLXStampStyle'),metadata)
                        raster=page.get_pixmap(clip=resized).samples
                        self.assertTrue(any(value<240 for value in raster))
                        self.assertEqual(len(window.undo_manager.undo_stack),1)
                        with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as saved:
                            self.assertEqual(saved[0].get_pixmap(clip=resized).samples,raster)
                        window.undo_manager.undo()
                        page=window.doc[0]
                        self.assertEqual(page.load_annot(xref).rect*page.rotation_matrix,original)
                        window.undo_manager.redo()
                        page=window.doc[0]
                        self.assertEqual(page.load_annot(xref).rect*page.rotation_matrix,resized)

    def test_stamp_resize_limits_and_cancellation(self):
        from pdflx.stamp_interaction import StampInteraction
        window=self.window()
        window.stamp_interaction=StampInteraction(window)
        xref=tools.place_stamp(window.doc,0,(180,220),width=160,
            style=dict(shape='circle',text='OK',font_size=20))
        note=tools.annotations(window.doc)[0]
        window.stamp_interaction.select(note)
        original=fitz.Rect(note['rect'])
        self.assertTrue(window.stamp_interaction.begin(original.x1,original.y1))
        window.stamp_interaction.update(-10000,-10000)
        target=window.stamp_interaction.drag['target']
        self.assertAlmostEqual(target.width,24)
        self.assertAlmostEqual(target.height,24)
        window.stamp_interaction.update(10000,10000)
        target=window.stamp_interaction.drag['target']
        self.assertLessEqual(target.x1,window.doc[0].rect.width)
        self.assertLessEqual(target.y1,window.doc[0].rect.height)
        self.assertAlmostEqual(target.width,target.height)
        window._load_page(0)
        self.assertIsNone(window.stamp_interaction.preview_doc)
        self.assertEqual(window.undo_manager.undo_stack,[])
        page=window.doc[0]
        self.assertEqual(page.load_annot(xref).rect,original)

    def test_stamp_tilt_survives_compact_save_resize_and_undo(self):
        import math
        from pdflx.stamp_rotation import rotate_stamp,tilt_info,stamp_dimensions
        for rotation in (0,90,180,270):
            for shape in (None,'seal','ribbon'):
                with self.subTest(rotation=rotation,shape=shape):
                    window=self.window(rotation=rotation)
                    style=(dict(shape=shape,text='APPROVED',font_size=24,opacity=0.65) if shape else None)
                    xref=tools.place_stamp(window.doc,0,(180,220),width=160,style=style)
                    page=window.doc[0]
                    original=page.load_annot(xref).rect*page.rotation_matrix
                    info=page.load_annot(xref).info
                    opacity=page.load_annot(xref).opacity
                    before=page.get_pixmap(clip=original).samples
                    self.assertTrue(window._mutate_document(lambda:rotate_stamp(window.doc,0,xref,30)))
                    page=window.doc[0]
                    tilted=page.load_annot(xref).rect*page.rotation_matrix
                    self.assertAlmostEqual(tilted.width,original.width*math.cos(math.pi/6)+original.height*0.5,places=3)
                    self.assertAlmostEqual(tilted.height,original.width*0.5+original.height*math.cos(math.pi/6),places=3)
                    self.assertEqual(page.load_annot(xref).info,info)
                    self.assertEqual(page.load_annot(xref).opacity,opacity)
                    self.assertEqual(tilt_info(window.doc,xref),30)
                    raster=page.get_pixmap(clip=tilted).samples
                    self.assertTrue(any(value<240 for value in raster))
                    with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as export:
                        compact=export.tobytes(garbage=4,deflate=True)
                    with fitz.open(stream=compact,filetype='pdf') as saved:
                        saved_page=saved[0]
                        note=next(saved_page.annots())
                        saved_xref=note.xref
                        self.assertEqual(saved_page.get_pixmap(clip=tilted).samples,raster)
                        rotate_stamp(saved,0,saved_xref,90)
                        width,height=stamp_dimensions(saved,0,saved_xref)
                        self.assertAlmostEqual(width,original.width,places=3)
                        self.assertAlmostEqual(height,original.height,places=3)
                        saved_page=saved[0]
                        bounds=saved_page.load_annot(saved_xref).rect*saved_page.rotation_matrix
                        enlarged=fitz.Rect(bounds.x0,bounds.y0,bounds.x0+bounds.width*1.2,bounds.y0+bounds.height*1.2)
                        tools.resize_stamp(saved,0,saved_xref,enlarged)
                        rotate_stamp(saved,0,saved_xref,0)
                        width,height=stamp_dimensions(saved,0,saved_xref)
                        self.assertAlmostEqual(width,original.width*1.2,places=3)
                        self.assertAlmostEqual(height,original.height*1.2,places=3)
                    window.undo_manager.undo()
                    page=window.doc[0]
                    self.assertEqual(page.get_pixmap(clip=original).samples,before)
                    window.undo_manager.redo()
                    self.assertEqual(window.doc[0].get_pixmap(clip=tilted).samples,raster)

    def test_stamp_rotation_handle_previews_without_changing_live_pdf(self):
        import math
        from pdflx.stamp_interaction import StampInteraction
        from pdflx.stamp_rotation import tilt_info
        window=self.window(rotation=90)
        window.stamp_interaction=StampInteraction(window)
        xref=tools.place_stamp(window.doc,0,(180,220),width=160,style={'text':'APPROVED','font_size':24})
        note=tools.annotations(window.doc)[0]
        window.stamp_interaction.select(note)
        original=fitz.Rect(note['rect'])*window.doc[0].rotation_matrix
        handle=fitz.Point((original.x0+original.x1)/2,original.y0-24)
        center=(original.tl+original.br)/2
        radius=center.y-handle.y
        dx=radius*0.5
        dy=radius*(1-math.cos(math.pi/6))
        self.assertTrue(window.stamp_interaction.begin(handle.x,handle.y))
        self.assertEqual(window.stamp_interaction.drag['handle'],'rotate')
        window.stamp_interaction.update(dx,dy)
        self.assertEqual(tilt_info(window.doc,xref),0)
        self.assertAlmostEqual(tilt_info(window.stamp_interaction.preview_doc,xref),30,places=3)
        window.stamp_interaction.end(dx,dy)
        self.assertAlmostEqual(tilt_info(window.doc,xref),30,places=3)
        self.assertEqual(len(window.undo_manager.undo_stack),1)
        window.undo_manager.undo()
        self.assertEqual(tilt_info(window.doc,xref),0)

    def test_context_duplicates_and_deletes_grouped_tables(self):
        from pdflx.element_menu import ElementMenu
        from pdflx.stamp_interaction import StampInteraction
        window=self.window()
        window.document_tools=SimpleNamespace(editable=lambda:not window.view_mode and window._active_session.can_edit,close_note_bubble=Mock())
        window.stamp_interaction=StampInteraction(window)
        originals=self.insert_table(window)
        menu=ElementMenu(window)
        menu.duplicate('table',TableSelection(originals))
        groups={getattr(obj,'table_id',None) for obj in window.editable_shapes}
        self.assertEqual(len(groups),2)
        copied=[obj for obj in window.editable_shapes+window.editable_texts if obj.table_id!=originals[0].table_id]
        self.assertEqual(len(copied),len(originals))
        self.assert_text(window.doc,'Alpha','123')
        menu.delete('table',TableSelection(copied))
        self.assertEqual(len(window.editable_shapes+window.editable_texts),len(originals))
        window.undo_manager.undo()
        self.assertEqual(len(window.editable_shapes+window.editable_texts),len(originals)*2)
        window.undo_manager.undo()
        self.assertEqual(len(window.editable_shapes+window.editable_texts),len(originals))
        window.view_mode=True
        menu.duplicate('table',TableSelection(originals))
        self.assertEqual(len(window.editable_shapes+window.editable_texts),len(originals))

    def test_annotation_context_duplicate_preserves_native_geometry_and_style(self):
        from pdflx.element_menu import duplicate_annotation
        from pdflx.stamp_rotation import tilt_info
        for rotation in (0,90,180,270):
            for kind in ('note','highlight','underline','stamp','ink'):
                with self.subTest(rotation=rotation,kind=kind):
                    window=self.window(rotation=rotation)
                    if kind=='stamp':
                        xref=tools.place_stamp(window.doc,0,(180,220),style={'text':'APPROVED','shape':'seal','angle':30})
                    elif kind=='ink':
                        page=window.doc[0]
                        annot=page.add_ink_annot([[(180,220),(240,260),(270,220)]])
                        xref=annot.xref
                    else:
                        xref=tools.add_review(window.doc,0,kind,(180,220,300,250),'Review')
                    page=window.doc[0];original=page.load_annot(xref)
                    bounds=fitz.Rect(original.rect)
                    original_type=original.type
                    content=original.info['content']
                    self.assertTrue(window._mutate_document(lambda:duplicate_annotation(window.doc,0,xref)))
                    page=window.doc[0]
                    copies=list(page.annots())
                    self.assertEqual(len(copies),2)
                    new=next(annot for annot in copies if annot.xref!=xref)
                    self.assertEqual(new.type,original_type)
                    self.assertEqual(new.info['content'],content)
                    self.assertNotEqual(new.rect,bounds)
                    if kind=='stamp': self.assertEqual(tilt_info(window.doc,new.xref),30)
                    if kind=='highlight':
                        self.assertNotEqual(new.vertices,page.load_annot(xref).vertices)
                    with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as saved:
                        self.assertEqual(len(tools.annotations(saved)),2)
                        saved[0].get_pixmap()
                    window.undo_manager.undo()
                    self.assertEqual(len(tools.annotations(window.doc)),1)
                    window.undo_manager.redo()
                    self.assertEqual(len(tools.annotations(window.doc)),2)

    def test_form_context_duplicate_gets_unique_name_and_preserves_value(self):
        from pdflx.element_menu import ElementMenu
        window=self.window()
        window.document_tools=SimpleNamespace(editable=lambda:True,close_note_bubble=Mock())
        xref=tools.create_form_field(window.doc,0,'Customer','text',(100,200,300,240),required=True)
        document_features.update_form_fields(window.doc,{(0,xref):'Jane'})
        tools.edit_form_field(window.doc,0,xref,'Customer',True,30)
        field=document_features.list_form_fields(window.doc)[0]
        menu=ElementMenu(window)
        menu.duplicate('field',field)
        menu.duplicate('field',field)
        fields=document_features.list_form_fields(window.doc)
        self.assertEqual({f['name'] for f in fields},{'Customer','Customer copy','Customer copy 2'})
        self.assertTrue(all(f['value']=='Jane' and f['required'] and f['max_length']==30 for f in fields))
        window.undo_manager.undo()
        self.assertEqual(len(document_features.list_form_fields(window.doc)),2)

    def test_form_geometry_properties_and_deletion_round_trip(self):
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation):
                window=self.window(rotation=rotation)
                visual=fitz.Rect(60,100,240,132)
                native=visual*window.doc[0].derotation_matrix
                window._mutate_document(lambda:tools.create_form_field(window.doc,0,'Name','text',native))
                field=document_features.list_form_fields(window.doc)[0]
                center=fitz.Point((native.x0+native.x1)/2,(native.y0+native.y1)/2)
                self.assertEqual(tools.form_field_at_point(window.doc,0,center)['name'],'Name')
                window._mutate_document(lambda:document_features.update_form_fields(window.doc,{(0,field['xref']):'Jane'}))
                moved=fitz.Rect(100,200,320,240)*window.doc[0].derotation_matrix
                window._mutate_document(lambda:tools.edit_form_field(window.doc,0,field['xref'],'Customer',True,20,moved))
                updated=document_features.list_form_fields(window.doc)[0]
                self.assertEqual((updated['name'],updated['value'],updated['required'],updated['max_length']),('Customer','Jane',True,20))
                self.assertEqual(updated['rect'],tuple(moved))
                with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as saved:
                    self.assertEqual(document_features.list_form_fields(saved)[0]['value'],'Jane')
                window._mutate_document(lambda:tools.delete_form_field(window.doc,0,field['xref']))
                self.assertEqual(document_features.list_form_fields(window.doc),[])
                window.undo_manager.undo()
                self.assertEqual(document_features.list_form_fields(window.doc)[0]['rect'],tuple(moved))
                window.undo_manager.undo()
                self.assertEqual(document_features.list_form_fields(window.doc)[0]['name'],'Name')

    def test_form_length_and_readonly_errors_are_atomic(self):
        window=self.window()
        xref=tools.create_form_field(window.doc,0,'Name','text',(20,20,200,50))
        window._mutate_document(lambda:tools.edit_form_field(window.doc,0,xref,'Name',False,3))
        history=len(window.undo_manager.undo_stack)
        self.assertFalse(window._mutate_document(lambda:document_features.update_form_fields(window.doc,{(0,xref):'Too long'})))
        self.assertEqual(len(window.undo_manager.undo_stack),history)
        page=window.doc[0]
        widget=page.load_widget(xref)
        widget.field_flags|=fitz.PDF_FIELD_IS_READ_ONLY
        widget.update()
        window.doc._reset_page_refs()
        self.assertFalse(window._mutate_document(lambda:document_features.update_form_fields(window.doc,{(0,xref):'New'})))
        self.assertEqual(document_features.list_form_fields(window.doc)[0]['value'],'')

    def test_bookmark_rename_preserves_nested_destinations_and_history(self):
        window=self.window(pages=2)
        window.doc.set_toc([[1,'Invoice',1],[2,'Payment details',2]])
        self.assertTrue(window._mutate_document(lambda:window.doc.set_toc_item(0,title='Customer invoice')))
        self.assertEqual(window.doc.get_toc(),[[1,'Customer invoice',1],[2,'Payment details',2]])
        window.undo_manager.undo()
        self.assertEqual(window.doc.get_toc()[0],[1,'Invoice',1])
        window.undo_manager.redo()
        with fitz.open(stream=window.doc.tobytes(),filetype='pdf') as saved:
            self.assertEqual(saved.get_toc(),[[1,'Customer invoice',1],[2,'Payment details',2]])

    def test_choice_field_options_can_be_edited_with_undo(self):
        window=self.window()
        xref=tools.create_form_field(window.doc,0,'Department','combo',(30,30,210,60),['One','Two'])
        window._mutate_document(lambda:document_features.update_form_fields(window.doc,{(0,xref):'Two'}))
        self.assertTrue(window._mutate_document(lambda:tools.edit_form_field(window.doc,0,xref,'Department',choices=['Sales','Engineering'])))
        field=document_features.list_form_fields(window.doc)[0]
        self.assertEqual(field['choices'],['Sales','Engineering'])
        self.assertEqual(field['value'],'Sales')
        window.undo_manager.undo()
        self.assertEqual(document_features.list_form_fields(window.doc)[0]['value'],'Two')
        self.assertFalse(window._mutate_document(lambda:tools.edit_form_field(window.doc,0,xref,'Department',choices=['Only one'])))
        self.assertEqual(document_features.list_form_fields(window.doc)[0]['choices'],['One','Two'])

    def test_table_drag_updates_only_preview_until_release(self):
        window=self.window()
        objects=self.insert_table(window)
        cell=objects[0]
        x0,y0,x1,y1=cell.bbox
        gesture=SimpleNamespace(set_state=Mock())
        window.on_drag_begin(gesture,(x0+x1)/2+50,(y0+y1)/2+40)
        before=window.doc[0].get_pixmap().samples
        with patch.object(pdf_handler,'rebuild_page',wraps=pdf_handler.rebuild_page) as rebuild:
            window.on_drag_update(gesture,10,10)
            window.on_drag_update(gesture,20,20)
            rebuild.assert_not_called()
            self.assertEqual(window.doc[0].get_pixmap().samples,before)
            window.on_drag_end(gesture,20,20)
            self.assertEqual(rebuild.call_count,1)
        self.assertIsNone(window._table_preview_doc)
        self.assertNotEqual(window.doc[0].get_pixmap().samples,before)

    def test_document_metadata_bookmarks_and_forms_have_undo_redo(self):
        window=self.window(pages=2)
        tools.create_form_field(window.doc,1,'Name','text',(30,30,200,60))
        page=window.doc[1]
        xref=next(page.widgets()).xref
        for mutate,check in (
            (lambda:document_features.update_metadata(window.doc,{'title':'New title'}),lambda:window.doc.metadata['title']=='New title'),
            (lambda:document_features.add_bookmark(window.doc,'Second page',1),lambda:len(window.doc.get_toc())==1),
            (lambda:document_features.update_form_fields(window.doc,{(1,xref):'Jane'}),lambda:window.doc[1].load_widget(xref).field_value=='Jane'),
        ):
            command=DocumentMutationCommand(window,mutate)
            self.assertIsNot(command.execute(),False)
            window.undo_manager.add_command(command)
            self.assertTrue(check())
            window._load_page(1)
            window.undo_manager.undo()
            self.assertFalse(check())
            window.undo_manager.redo()
            self.assertTrue(check())

    def test_character_selection_uses_actual_widths_and_page_rotations(self):
        for rotation in (0,90,180,270):
            window=self.window(rotation=rotation)
            window.doc[0].insert_text((100,100),'WiWi',fontsize=24)
            obj=pdf_handler.extract_editable_text(window.doc,0)[0][0]
            quads=text_geometry.selection_quads(window.doc,obj,0,1)
            first=quads[0].rect
            half_width=(obj.bbox[2]-obj.bbox[0])/4
            self.assertGreater(first.width,half_width*1.3)
            second=text_geometry.selection_quads(window.doc,obj,1,2)[0].rect
            self.assertLess(second.width,half_width)
            index=text_geometry.character_at_point(window.doc,obj,*((second.tl+second.br)/2))
            self.assertEqual(index,1)
            tools.add_review(window.doc,0,'underline',obj.bbox,quads=quads)
            page=window.doc[0]
            annot=next(page.annots())
            self.assertEqual(len(annot.vertices),4)

    def test_review_annotations_save_and_undo(self):
        window=self.window()
        for kind in ('note','underline','strikeout','squiggle','stamp'):
            command=DocumentMutationCommand(window,lambda kind=kind:tools.add_review(window.doc,0,kind,(30,50,180,80),'Review this','Reviewer'))
            self.assertIsNot(command.execute(),False)
            self.assertEqual(len(tools.annotations(window.doc)),1)
            self.assertIsNot(command.undo(),False)
            self.assertEqual(tools.annotations(window.doc),[])
            self.assertIsNot(command.execute(),False)
            saved=fitz.open(stream=window.doc.tobytes(),filetype='pdf')
            self.assertEqual(tools.annotations(saved)[0]['author'],'Reviewer')
            saved.close()
            self.assertIsNot(command.undo(),False)

    def test_watermarks_numbering_and_crop_are_undoable(self):
        window=self.window(pages=2,rotation=90)
        window.doc[0].insert_text((20,40),'Original')
        pdf_handler.save_page_snapshot(window.doc,0,force=True)
        self.insert_table(window)
        command=DocumentMutationCommand(window,lambda:tools.decorate_pages(window.doc,0,1,text='DRAFT',numbering=True,start_number=5),rebase_pages=(0,1))
        self.assertIsNot(command.execute(),False)
        self.assert_text(window.doc,'DRAFT','5 / 2','6 / 2','Alpha','Original')
        self.assertEqual(window.doc[0].get_text().count('Alpha'),1)
        self.assertIsNot(command.undo(),False)
        self.assertNotIn('DRAFT',window.doc[0].get_text())
        self.assert_text(window.doc,'Alpha')
        crop=DocumentMutationCommand(window,lambda:tools.crop_pages(window.doc,0,1,(10,20,30,40)))
        before=window.doc[0].cropbox
        self.assertIsNot(crop.execute(),False)
        self.assertEqual(window.doc[0].cropbox.width,before.width-40)
        self.assertIsNot(crop.undo(),False)
        self.assertEqual(window.doc[0].cropbox,before)

    def test_redaction_removes_native_text_and_managed_table_text_from_saved_state(self):
        window=self.window()
        window.doc[0].insert_text((20,40),'Secret native')
        window.doc[0].insert_text((20,60),'Keep native')
        pdf_handler.save_page_snapshot(window.doc,0,force=True)
        self.insert_table(window)
        targets=tools.redaction_targets(window.doc,0,0,'Secret')+tools.redaction_targets(window.doc,0,0,'Alpha')
        command=DocumentMutationCommand(window,lambda:tools.apply_redactions(window,targets),rebase_pages=(0,))
        self.assertIsNot(command.execute(),False)
        self.assertNotIn('Secret',window.doc[0].get_text())
        self.assertNotIn('Alpha',window.doc[0].get_text())
        self.assert_text(window.doc,'Keep native','123')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'redacted.pdf'
            self.assertEqual(pdf_handler.save_document(window.doc,str(path)),(True,None))
            with fitz.open(path) as saved:
                self.assertNotIn('Secret',saved[0].get_text())
                self.assertNotIn('Alpha',saved[0].get_text())
                groups=load_state(saved,0)
                states=repr([obj.__dict__ for group in groups for obj in group])
                self.assertNotIn('Secret',states)
                self.assertNotIn('Alpha',states)
        self.assertIsNot(command.undo(),False)
        self.assert_text(window.doc,'Secret','Alpha')
        self.assertIsNot(command.execute(),False)
        self.assertNotIn('Alpha',window.doc[0].get_text())

    def test_form_creation_filling_and_flattening_preserve_appearance_and_comments(self):
        window=self.window()
        for index,kind in enumerate(('text','checkbox','combo','list')):
            tools.create_form_field(window.doc,0,f'field{index}',kind,(30,30+index*50,180,60+index*50),('One','Two'))
        page=window.doc[0]
        values={(0,widget.xref):('Filled' if widget.field_type==fitz.PDF_WIDGET_TYPE_TEXT else True if widget.field_type==fitz.PDF_WIDGET_TYPE_CHECKBOX else 'Two') for widget in page.widgets()}
        document_features.update_form_fields(window.doc,values)
        tools.add_review(window.doc,0,'note',(250,30,300,60),'Keep comment')
        pdf_handler.save_page_snapshot(window.doc,0,force=True)
        before=window.doc[0].get_pixmap().samples
        command=DocumentMutationCommand(window,lambda:tools.flatten_forms(window.doc),rebase_pages=(0,))
        self.assertIsNot(command.execute(),False)
        self.assertEqual(list(window.doc[0].widgets() or ()),[])
        self.assertEqual(window.doc[0].get_pixmap().samples,before)
        self.assertEqual(tools.annotations(window.doc)[0]['content'],'Keep comment')
        self.assertIsNot(command.undo(),False)
        self.assertEqual(len(list(window.doc[0].widgets())),4)

    def test_insert_delete_and_reorder_preserve_models_and_document_history(self):
        window=self.window(pages=2)
        objects=self.insert_table(window)
        self.assertTrue(window._change_pages(lambda:pdf_handler.insert_blank_page(window.doc,0),lambda page:page+1)[0])
        self.assertEqual(window.doc.page_count,3)
        self.assertEqual(objects[0].page_number,1)
        window.undo_manager.undo()
        self.assertEqual(window.doc.page_count,2)
        self.assertEqual(objects[0].page_number,0)
        self.assert_text(window.doc,'Alpha')
        window.undo_manager.redo()
        self.assertEqual(window.doc.page_count,3)
        self.assertEqual(objects[0].page_number,1)
        self.assertTrue(window._change_pages(lambda:pdf_handler.delete_page(window.doc,0),lambda page:None if page==0 else page-1)[0])
        self.assertEqual(window.doc.page_count,2)
        self.assertEqual(objects[0].page_number,0)
        window.undo_manager.undo()
        self.assertEqual(window.doc.page_count,3)
        self.assertEqual(objects[0].page_number,1)
        self.assertTrue(window._change_pages(lambda:pdf_handler.move_page(window.doc,1,2),lambda page:2 if page==1 else 1 if page==2 else page)[0])
        self.assertEqual(objects[0].page_number,2)
        window.undo_manager.undo()
        self.assertEqual(objects[0].page_number,1)
        self.assert_text(window.doc,'Alpha')

    def test_regular_save_keeps_undo_snapshots_valid(self):
        window=self.window()
        self.insert_table(window)
        window._mutate_document(lambda:tools.add_review(window.doc,0,'note',(20,20,40,40),'Note'))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'saved.pdf'
            self.assertEqual(pdf_handler.save_document(window.doc,str(path)),(True,None))
            window.undo_manager.undo()
            self.assertEqual(tools.annotations(window.doc),[])
            self.assert_text(window.doc,'Alpha')
            window.undo_manager.undo()
            self.assertNotIn('Alpha',window.doc[0].get_text())
            window.undo_manager.redo()
            self.assert_text(window.doc,'Alpha')

    def test_encrypted_save_preserves_password_and_live_history(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'encrypted.pdf'
            with fitz.open() as plain:
                page=plain.new_page()
                page.insert_text((20,40),'Encrypted original')
                plain.save(source,encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='reader',
                           permissions=fitz.PDF_PERM_MODIFY|fitz.PDF_PERM_COPY|fitz.PDF_PERM_PRINT)
            opened,error=pdf_handler.load_pdf_document(str(source),'reader')
            self.assertIsNone(error)
            window=self.window()
            original=window.doc
            window.doc=opened
            pdf_handler.save_page_snapshot(opened,0,force=True)
            window.editable_texts,window.editable_shapes,window.editable_images,window.editable_strokes=[],[],[],[]
            self.cache(window)
            self.insert_table(window)
            output=Path(directory)/'saved.pdf'
            self.assertEqual(pdf_handler.save_document(opened,str(output)),(True,None))
            with fitz.open(output) as saved:
                self.assertTrue(saved.needs_pass)
                self.assertGreater(saved.authenticate('reader'),0)
                self.assert_text(saved,'Encrypted original','Alpha')
            window.undo_manager.undo()
            self.assertNotIn('Alpha',opened[0].get_text())
            opened.close()
            window.doc=original

    def test_failed_table_resize_restores_pdf_geometry_and_model(self):
        window=self.window()
        objects=self.insert_table(window)
        old=[copy.deepcopy(obj.__dict__) for obj in objects]
        table=TableSelection(objects)
        x0,y0,x1,y1=table.bbox
        table.transform(old,table.bbox,(x0,y0,x1+100,y1+100))
        command=EditTableCommand(window,objects,old,[copy.deepcopy(obj.__dict__) for obj in objects])
        with patch.object(pdf_handler,'_apply_single_object_to_page',return_value=(False,'Failed renderer')):
            self.assertIs(command.execute(),False)
        self.assertEqual([obj.bbox for obj in objects],[state['bbox'] for state in old])
        self.assert_text(window.doc,'Alpha')
        self.assertEqual(len(window.undo_manager.undo_stack),1)

    def test_native_rotated_text_has_exact_character_positions(self):
        window=self.window()
        window.doc[0].insert_text((200,300),'WiWi',fontsize=24,rotate=90)
        obj=pdf_handler.extract_editable_text(window.doc,0)[0][0]
        quads=text_geometry.selection_quads(window.doc,obj,1,2)
        char=quads[0].rect
        self.assertEqual(text_geometry.character_at_point(window.doc,obj,*((char.tl+char.br)/2)),1)
        tools.add_review(window.doc,0,'highlight',obj.bbox,quads=quads)
        annot=tools.annotations(window.doc)[0]
        self.assertLess(fitz.Rect(annot['rect']).height,fitz.Rect(obj.bbox).height/2)

    def test_invalid_tools_roll_back_without_history(self):
        window=self.window()
        before=window.doc[0].get_pixmap().samples
        bad=(lambda:tools.crop_pages(window.doc,0,0,(999,0,999,0)),
             lambda:tools.create_form_field(window.doc,0,'','text',(0,0,100,20)),
             lambda:tools.decorate_pages(window.doc,0,0,numbering=True,template='{unknown}'))
        for mutate in bad:
            command=DocumentMutationCommand(window,mutate)
            self.assertIs(command.execute(),False)
            window.undo_manager.add_command(command)
            self.assertEqual(window.doc[0].get_pixmap().samples,before)
            self.assertEqual(window.undo_manager.undo_stack,[])


if __name__=='__main__':
    unittest.main()
