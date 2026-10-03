"""Selection lifecycle and immediate undo for the non-modal image tools."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_canvas_drag import CanvasHarness
from test_image_rotation import image_bytes
from pdflx.models import EditableImage
from pdflx.element_menu import ElementMenu
from pdflx.image_editor_dialog import ImageEditorPanel, ImageToolsController
from pdflx.undo_manager import AddObjectCommand


class PanelStub:
    def __init__(self,menu,image):
        self.menu,self.image,self.source=menu,image,menu.source
        self.initial=copy.deepcopy(image.__dict__)
        self.committing=False
        self.timer=None
        self.finish=Mock()


class ImageToolsTests(unittest.TestCase):
    def setUp(self):
        self.image=EditableImage((50,60,250,160),0,None,image_bytes(),is_new=True)
        self.window=CanvasHarness(self.image)
        self.addCleanup(self.window.doc.close)
        self.window.document_tools=SimpleNamespace(editable=lambda:not self.window.view_mode)
        self.window.image_tools_button=Mock()
        self.window.selected_image=self.image

    def controller(self):
        controller=ImageToolsController.__new__(ImageToolsController)
        controller.window=self.window
        controller.frame=Mock()
        controller.scroll=Mock()
        controller.panel=None
        controller.hidden_target=None
        controller.syncing=False
        self.window.image_tools=controller
        return controller

    @patch('pdflx.image_editor_dialog.ImageEditorPanel',PanelStub)
    def test_selection_opens_tools_close_then_top_button_reopens(self):
        controller=self.controller()
        controller.sync()
        self.assertIs(controller.panel.image,self.image)
        controller.frame.set_visible.assert_called_with(True)
        controller.close()
        controller.frame.set_visible.assert_called_with(False)
        controller.sync()
        controller.frame.set_visible.assert_called_with(False)
        controller.open()
        controller.frame.set_visible.assert_called_with(True)
        self.window.selected_image=None
        controller.sync()
        self.assertIsNone(controller.panel)
        self.window.image_tools_button.set_sensitive.assert_called_with(False)

    @patch('pdflx.image_editor_dialog.ImageEditorPanel',PanelStub)
    def test_selection_geometry_undo_and_view_mode_refresh(self):
        controller=self.controller();controller.sync()
        previous=controller.panel
        self.image.bbox=(60,70,260,170)
        controller.sync()
        self.assertIsNot(controller.panel,previous)
        self.assertEqual(controller.panel.initial['bbox'],self.image.bbox)
        replacement=EditableImage((20,30,80,90),0,None,image_bytes(),is_new=True)
        self.window.editable_images.append(replacement)
        self.window.selected_image=replacement
        controller.sync()
        self.assertIs(controller.panel.image,replacement)
        self.window.view_mode=True
        controller.sync()
        self.assertIsNone(controller.panel)
        controller.frame.set_visible.assert_called_with(False)
        self.window.image_tools_button.set_visible.assert_called_with(False)

    def test_pending_change_is_committed_before_immediate_undo(self):
        AddObjectCommand(self.window,self.image).execute()
        self.window.undo_manager.clear()
        panel=SimpleNamespace(menu=ElementMenu(self.window),owner=self.window,image=self.image,
                              initial=copy.deepcopy(self.image.__dict__),timer=42,committing=False)
        def clone():
            edited=copy.deepcopy(self.image)
            edited.opacity=.4
            return edited
        panel.clone=clone
        panel.commit=ImageEditorPanel.commit.__get__(panel)
        panel.finish=ImageEditorPanel.finish.__get__(panel)
        self.window.image_tools=SimpleNamespace(panel=panel)
        with patch('pdflx.image_editor_dialog.GLib.source_remove'):
            self.window.undo_manager.undo()
        self.assertEqual(self.image.opacity,1)
        self.assertEqual(len(self.window.undo_manager.redo_stack),1)
        self.assertIsNone(panel.timer)
        self.window.undo_manager.redo()
        self.assertEqual(self.image.opacity,.4)

    def test_deleted_image_cannot_receive_a_delayed_change(self):
        panel=SimpleNamespace(menu=ElementMenu(self.window),owner=self.window,image=self.image,timer=42)
        panel.clone=Mock()
        self.window.editable_images=[]
        ImageEditorPanel.commit(panel)
        panel.clone.assert_not_called()
        self.assertEqual(len(self.window.undo_manager.undo_stack),0)
