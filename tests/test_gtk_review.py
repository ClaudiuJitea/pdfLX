"""Exercise real GTK focus lifetimes and page-based note interaction."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

_HOME=tempfile.mkdtemp(prefix='pdflx-test-home-')
# Share caches (fontconfig, icons) so the first script does not start cold.
if os.path.isdir(os.path.expanduser('~/.cache')):
    os.symlink(os.path.expanduser('~/.cache'),os.path.join(_HOME,'.cache'))


def _environment(root):
    # A throwaway HOME keeps the scripts from editing the user's pdfLX settings.
    return dict(os.environ,G_DEBUG='fatal-criticals',PYTHONPATH=str(root),HOME=_HOME)


@unittest.skipUnless(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'),
                     'GTK interaction test requires a display')
class GtkReviewTests(unittest.TestCase):
    def test_document_operation_dialogs_and_workflows(self):
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_features_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('presentation, and node editing passed',result.stdout)

    def test_form_scripts_behaviour_and_buttons(self):
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_form_scripts_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('scripts toggle passed',result.stdout)

    def test_continuous_reader_scroll_mode(self):
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_continuous_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('mode switching passed',result.stdout)

    def test_highlight_tools_in_view_mode(self):
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_view_highlight_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('undo/redo, and save passed',result.stdout)

    def test_view_mode_text_selection_and_clipboard(self):
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_reader_selection_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('fullscreen Ctrl+A passed',result.stdout)

    def test_reader_fullscreen_and_mouse_navigation(self):
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_navigation_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('Ctrl zoom passed',result.stdout)

    def test_right_click_actions_cover_all_elements(self):
        try:
            import gi
        except ImportError:
            self.skipTest('GTK bindings required')
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_element_menu_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('properties passed',result.stdout)

    def test_text_boxes_grow_and_resize_on_the_page(self):
        try:
            import gi
        except ImportError:
            self.skipTest('GTK bindings required')
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_text_box_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('editing without duplicates passed',result.stdout)

    def test_loaded_pages_fit_and_tab_zoom_is_preserved(self):
        try:
            import gi
        except ImportError:
            self.skipTest('GTK bindings required')
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_fit_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('tab zoom is preserved',result.stdout)

    def test_notes_and_focus_lifetimes_without_gtk_criticals(self):
        try:
            import gi
        except ImportError:
            self.skipTest('GTK bindings required')
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_review_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('inline focus passed',result.stdout)

    def test_forms_and_bookmarks_can_be_used_from_the_page(self):
        try:
            import gi
        except ImportError:
            self.skipTest('GTK bindings required')
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_forms_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('bookmark navigation passed',result.stdout)

    def test_closed_side_panels_do_not_reserve_canvas_width(self):
        try:
            import gi
        except ImportError:
            self.skipTest('GTK bindings required')
        root=Path(__file__).resolve().parents[1]
        environment=_environment(root)
        result=subprocess.run([sys.executable,str(root/'tests/ui_layout_smoke.py')],
                              cwd=root,env=environment,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('use zero width',result.stdout)
