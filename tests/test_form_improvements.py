"""Native field appearance, behaviour, permissions, validation and undo."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pymupdf as fitz
import test_document_tools as fixtures
from pdflx import document_tools as tools,document_features as features,pdf_handler
from pdflx.form_properties import missing_required,choice_options
from pdflx.form_ui import FormController
from pdflx.extended_form_fields import reset_form_fields
from pdflx.form_inline_editor import FormInlineEditor,field_geometry
from pdflx.form_interaction import FormInteraction
from pdflx.form_duplication import duplicate_form_field
from gi.repository import Gtk


class FormImprovementsTests(unittest.TestCase):
    window=fixtures.DocumentToolsTests.window
    cache=fixtures.DocumentToolsTests.cache

    def test_creation_repairs_string_field_array_without_losing_widgets(self):
        for indirect in (False,True):
            w=self.window()
            x=tools.create_form_field(w.doc,0,'Existing','text',(50,80,280,120),value='Keep this')
            tools.create_form_field(w.doc,0,'Consent','checkbox',(50,140,70,160),value=True)
            _,array=w.doc.xref_get_key(w.doc.pdf_catalog(),'AcroForm/Fields')
            malformed=fitz.get_pdf_str(array)
            if indirect:
                ref=w.doc.get_new_xref();w.doc.update_object(ref,malformed);malformed=f'{ref} 0 R'
            w.doc.xref_set_key(w.doc.pdf_catalog(),'AcroForm/Fields',malformed)
            w.doc.xref_set_key(w.doc.pdf_catalog(),'AcroForm/SigFlags','3')
            self.assertTrue(w._mutate_document(lambda:tools.create_form_field(w.doc,0,'New','text',(50,180,280,220))))
            self.assertEqual(w.doc.xref_get_key(w.doc.pdf_catalog(),'AcroForm/Fields')[0],'array')
            self.assertEqual(w.doc.xref_get_key(w.doc.pdf_catalog(),'AcroForm/SigFlags'),('int','3'))
            fields=features.list_form_fields(w.doc)
            self.assertEqual([f['name'] for f in fields],['Existing','Consent','New'])
            self.assertEqual(fields[0]['value'],'Keep this');self.assertEqual(fields[1]['value'],fields[1]['on_state'])
            w.undo_manager.undo();self.assertEqual(len(features.list_form_fields(w.doc)),2)
            w.undo_manager.redo();self.assertEqual(len(features.list_form_fields(w.doc)),3)

    def test_duplicate_then_create_and_reopen_with_indirect_field_array(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Existing','text',(50,80,280,120),value='Original')
        _,array=w.doc.xref_get_key(w.doc.pdf_catalog(),'AcroForm/Fields')
        ref=w.doc.get_new_xref();w.doc.update_object(ref,array)
        w.doc.xref_set_key(w.doc.pdf_catalog(),'AcroForm/Fields',f'{ref} 0 R')
        duplicate_form_field(w.doc,0,x)
        tools.create_form_field(w.doc,0,'New','text',(50,180,280,220))
        self.assertEqual(w.doc.xref_get_key(w.doc.pdf_catalog(),'AcroForm/Fields'),('xref',f'{ref} 0 R'))
        self.assertTrue(w.doc.xref_object(ref).strip().startswith('['))
        with fitz.open(stream=w.doc.tobytes(),filetype='pdf') as saved:
            tools.create_form_field(saved,0,'After reopening','checkbox',(50,260,70,280))
            self.assertEqual([f['name'] for f in features.list_form_fields(saved)],['Existing','Existing copy','New','After reopening'])

    def test_radio_creation_preserves_existing_indirect_field_array(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Existing','text',(50,80,280,120),value='Original')
        _,array=w.doc.xref_get_key(w.doc.pdf_catalog(),'AcroForm/Fields')
        ref=w.doc.get_new_xref();w.doc.update_object(ref,array)
        w.doc.xref_set_key(w.doc.pdf_catalog(),'AcroForm/Fields',f'{ref} 0 R')
        radio=tools.create_form_field(w.doc,0,'Group','radio',(50,180,280,250),choices=['One','Two'])
        roots=w.doc.xref_object(ref)
        parent=int(w.doc.xref_get_key(radio,'Parent')[1].split()[0])
        self.assertEqual(w.doc.xref_get_key(w.doc.pdf_catalog(),'AcroForm/Fields'),('xref',f'{ref} 0 R'))
        self.assertIn(f'{x} 0 R',roots);self.assertIn(f'{parent} 0 R',roots)
        self.assertNotIn(f'{ref} 0 R',roots)
        tools.create_form_field(w.doc,0,'Later','text',(50,280,280,320))
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Original')

    def test_repair_reconstructs_radio_parent_from_widget_tree(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Group','radio',(50,80,280,150),choices=['One','Two'],value='One')
        parent=w.doc.xref_get_key(x,'Parent')[1]
        w.doc.xref_set_key(w.doc.pdf_catalog(),'AcroForm/Fields',fitz.get_pdf_str('broken field list'))
        duplicate_form_field(w.doc,0,x)
        _,roots=w.doc.xref_get_key(w.doc.pdf_catalog(),'AcroForm/Fields')
        self.assertIn(parent,roots)
        self.assertNotIn(f'{x} 0 R',roots)
        self.assertEqual([f['value'] for f in features.list_form_fields(w.doc)],['One','Off','One','Off'])

    def test_duplicate_text_settings_values_defaults_and_independent_appearance(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Notes','text',(50,80,300,150),value='Initial',
            options=dict(multiline=True,readonly=True,tooltip='Instructions',font_size=13,fill_color=(.9,.95,1)))
        tools.edit_form_field(w.doc,0,x,'Notes',True,500)
        result=[]
        self.assertTrue(w._mutate_document(lambda:result.extend(duplicate_form_field(w.doc,0,x))))
        copy=features.list_form_fields(w.doc)[1]
        self.assertEqual(copy['name'],'Notes copy');self.assertEqual(copy['value'],'Initial')
        self.assertTrue(copy['readonly']);self.assertTrue(copy['required']);self.assertTrue(copy['multiline'])
        self.assertEqual(copy['tooltip'],'Instructions');self.assertEqual(copy['max_length'],500)
        self.assertEqual(w.doc.xref_get_key(copy['xref'],'DV'),('string','Initial'))
        source_ap=w.doc.xref_get_key(x,'AP/N')[1]
        copy_ap=w.doc.xref_get_key(copy['xref'],'AP/N')[1]
        self.assertNotEqual(source_ap,copy_ap)
        before=w.doc.xref_stream(int(source_ap.split()[0]))
        tools.edit_form_field(w.doc,0,copy['xref'],copy['name'],options=dict(readonly=False))
        features.update_form_fields(w.doc,{(0,copy['xref']):'Changed copy'})
        self.assertEqual(w.doc.xref_stream(int(source_ap.split()[0])),before)
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Initial')
        with fitz.open(stream=w.doc.tobytes(),filetype='pdf') as saved:
            self.assertEqual([f['value'] for f in features.list_form_fields(saved)],['Initial','Changed copy'])
        w.undo_manager.undo();self.assertEqual(len(features.list_form_fields(w.doc)),1)

    def test_duplicate_button_keeps_label_action_and_unique_names(self):
        w=self.window(pages=2)
        x=tools.create_form_field(w.doc,0,'Next','button',(50,80,250,120),
            options=dict(button_caption='Continue',button_action='goto',button_page=1))
        duplicate_form_field(w.doc,0,x);duplicate_form_field(w.doc,0,x)
        fields=features.list_form_fields(w.doc)
        self.assertEqual([f['name'] for f in fields],['Next','Next copy','Next copy 2'])
        self.assertTrue(all(f['button_caption']=='Continue' and f['button_action']=='goto' and f['button_page']==1 for f in fields))
        tools.edit_form_field(w.doc,0,fields[1]['xref'],'Next copy',options=dict(button_caption='Reset',button_action='reset'))
        self.assertEqual(features.list_form_fields(w.doc)[0]['button_caption'],'Continue')
        self.assertEqual(features.list_form_fields(w.doc)[0]['button_action'],'goto')

    def test_duplicate_radio_group_is_independent(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Delivery','radio',(50,80,280,150),choices=['Collect at depot','Post / courier'],value='Collect at depot',required=True)
        duplicate_form_field(w.doc,0,x)
        fields=features.list_form_fields(w.doc)
        copies=[f for f in fields if f['name']=='Delivery copy']
        self.assertEqual(len(copies),2)
        features.update_form_fields(w.doc,{(0,copies[1]['xref']):True})
        fields=features.list_form_fields(w.doc)
        self.assertEqual([f['value'] for f in fields if f['name']=='Delivery'],['Collect at depot','Off'])
        self.assertEqual([f['value'] for f in fields if f['name']=='Delivery copy'],['Off','Post / courier'])
        self.assertEqual(missing_required(fields),[])
        self.assertEqual(fields[3]['rect'][1]-fields[1]['rect'][1],16)
        reset_form_fields(w.doc)
        self.assertEqual([f['value'] for f in features.list_form_fields(w.doc)],
                         ['Collect at depot','Off','Collect at depot','Off'])

    def test_duplicate_dropdown_preserves_export_values(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Country','combo',(50,80,280,120),choices=[('RO','Romania'),('DE','Germany')],value='DE')
        duplicate_form_field(w.doc,0,x)
        copied=features.list_form_fields(w.doc)[1]
        self.assertEqual(choice_options(copied['choices']),[('RO','Romania'),('DE','Germany')])
        self.assertEqual(copied['value'],'DE')
        features.update_form_fields(w.doc,{(0,copied['xref']):'RO'})
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'DE')

    def test_quick_duplicate_flushes_pending_values_and_selects_copy(self):
        w=self.window();c=self.controller(w);c.editable=Mock(return_value=True);c.refresh=Mock()
        c.interaction=FormInteraction(c)
        x=tools.create_form_field(w.doc,0,'Name','text',(50,80,280,120))
        c.pending[(0,x)]='Current draft'
        self.assertTrue(c.duplicate_field(features.list_form_fields(w.doc)[0]))
        self.assertEqual([f['value'] for f in features.list_form_fields(w.doc)],['Current draft','Current draft'])
        self.assertEqual(c.interaction.current()['name'],'Name copy')
        w.undo_manager.undo();self.assertEqual(len(features.list_form_fields(w.doc)),1)

    def test_duplicate_stays_in_rotated_page_bounds(self):
        for angle in (0,90,180,270):
            w=self.window(rotation=angle)
            x=tools.create_form_field(w.doc,0,'Name','text',(450,750,600,800))
            duplicate_form_field(w.doc,0,x)
            copied=features.list_form_fields(w.doc)[1]
            self.assertTrue(fitz.Rect(copied['rect']) in w.doc[0].rect*w.doc[0].derotation_matrix)

    def test_field_resize_and_move_are_previewed_then_committed_with_undo(self):
        for rotation in (0,90,180,270):
            w=self.window(rotation=rotation);c=self.controller(w);c.editable=Mock(return_value=True)
            c.interaction=FormInteraction(c)
            tools.create_form_field(w.doc,0,'Name','text',(100,100,300,140),value='Keep me')
            field=features.list_form_fields(w.doc)[0];c.interaction.select(field)
            visual=fitz.Rect(field['rect'])*w.doc[0].rotation_matrix
            handle=c.interaction.points(visual)['se']
            self.assertTrue(c.interaction.begin(*handle))
            c.interaction.update(30,25)
            self.assertEqual(features.list_form_fields(w.doc)[0]['rect'],field['rect'])
            c.interaction.end(30,25)
            changed=features.list_form_fields(w.doc)[0]
            self.assertNotEqual(changed['rect'],field['rect']);self.assertEqual(changed['value'],'Keep me')
            self.assertEqual(len(w.undo_manager.undo_stack),1)
            w.undo_manager.undo()
            self.assertEqual(features.list_form_fields(w.doc)[0]['rect'],field['rect'])
            c.interaction.select(field)
            self.assertTrue(c.interaction.begin(*c.interaction.points(visual)['move']))
            c.interaction.end(20,10)
            moved=fitz.Rect(features.list_form_fields(w.doc)[0]['rect'])*w.doc[0].rotation_matrix
            self.assertAlmostEqual(moved.x0,visual.x0+20);self.assertAlmostEqual(moved.y0,visual.y0+10)

    def test_small_button_interior_is_clickable_between_resize_handles(self):
        w=self.window();w.zoom_level=.5;c=self.controller(w);c.editable=Mock(return_value=True)
        c.interaction=FormInteraction(c)
        tools.create_form_field(w.doc,0,'Reset','button',(100,100,180,124))
        field=features.list_form_fields(w.doc)[0];c.interaction.select(field)
        self.assertIsNone(c.interaction.handle_at(140,112))
        self.assertEqual(c.interaction.handle_at(*c.interaction.points(field['rect'])['se']),'se')

    def test_button_label_and_navigation_action_survive_resize_reopen(self):
        w=self.window(pages=2);c=self.controller(w)
        x=tools.create_form_field(w.doc,0,'Next','button',(50,100,250,140),
            options=dict(font_size=0,button_caption='Continue',button_action='goto',button_page=1))
        self.assertTrue(w._mutate_document(lambda:tools.edit_form_field(w.doc,0,x,'Next',rect=(50,100,300,145),
            options=dict(button_caption='Next page',button_action='goto',button_page=1,font_size=0))))
        with fitz.open(stream=w.doc.tobytes(),filetype='pdf') as saved:
            field=features.list_form_fields(saved)[0]
            self.assertEqual(field['button_caption'],'Next page')
            self.assertEqual(field['button_action'],'goto');self.assertEqual(field['button_page'],1)
            self.assertGreaterEqual(field['font_size'],5)
            self.assertIn('Next page',saved[0].get_text())
        c.click_field(features.list_form_fields(w.doc)[0])
        self.assertEqual(w.current_page_index,1)

    def test_invalid_button_destination_rolls_back(self):
        w=self.window()
        self.assertFalse(w._mutate_document(lambda:tools.create_form_field(w.doc,0,'Next','button',
            (50,100,250,140),options=dict(button_action='goto',button_page=99))))
        self.assertEqual(features.list_form_fields(w.doc),[])

    def controller(self,w):
        c=FormController.__new__(FormController);c.window=w;c.pending={};c.source=w.doc
        c.inline_editor=None;c.save=Mock();c.message=Mock()
        w.form_tools=c
        return c

    def test_form_drag_claims_shared_click_and_does_not_move_content(self):
        w=self.window();self.controller(w)
        tools.create_form_field(w.doc,0,'Reset','button',(20,20,150,60))
        gesture=SimpleNamespace(set_state=Mock())
        w.on_drag_begin(gesture,90,80)
        gesture.set_state.assert_called_once_with(Gtk.EventSequenceState.CLAIMED)
        self.assertTrue(w._form_drag_active)
        w.on_drag_update(gesture,100,100)
        w.on_drag_end(gesture,100,100)
        self.assertFalse(w._form_drag_active)
        self.assertEqual(features.list_form_fields(w.doc)[0]['rect'],(20,20,150,60))

    def test_reset_button_click_dispatches_native_reset_and_undo(self):
        w=self.window();c=self.controller(w)
        text=tools.create_form_field(w.doc,0,'Name','text',(20,20,200,50),value='Initial')
        tools.create_form_field(w.doc,0,'Reset','button',(20,70,150,100))
        features.update_form_fields(w.doc,{(0,text):'Changed'})
        c.click_field(features.list_form_fields(w.doc)[1])
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Initial')
        w.undo_manager.undo()
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Changed')

    def inline(self,w,c,key):
        editor=FormInlineEditor.__new__(FormInlineEditor)
        editor.controller=c;editor.window=w;editor.source=w.doc;editor.key=key
        editor.closed=False;editor.focus_source=None;editor.resize_handler=1
        editor.frame=Mock();editor.had_draft=False;editor.previous_draft=None;editor.was_modified=False
        w.pdf_overlay=Mock();w.pdf_view.disconnect=Mock();c.inline_editor=editor
        w.get_focus=Mock(return_value=None);w.set_focus=Mock()
        return editor

    def test_inline_commit_saves_value_and_undo_without_dialog(self):
        w=self.window();c=self.controller(w)
        x=tools.create_form_field(w.doc,0,'Name','text',(20,20,200,50))
        editor=self.inline(w,c,(0,x));c.pending[(0,x)]='Typed directly';w.document_modified=True
        self.assertTrue(editor.finish())
        self.assertIsNone(c.inline_editor);self.assertEqual(c.pending,{})
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Typed directly')
        w.undo_manager.undo();self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'')

    def test_inline_escape_discards_draft(self):
        w=self.window();c=self.controller(w)
        x=tools.create_form_field(w.doc,0,'Name','text',(20,20,200,50),value='Original')
        editor=self.inline(w,c,(0,x));c.pending[(0,x)]='Unwanted';w.document_modified=True
        self.assertTrue(editor.finish(False))
        self.assertEqual(c.pending,{});self.assertFalse(w.document_modified)
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Original')

    def test_inline_geometry_tracks_rotation_zoom_and_centering(self):
        w=self.window()
        for angle in (0,90,180,270):
            w.doc[0].set_rotation(angle);page=w.doc[0];rect=fitz.Rect(20,30,200,60)
            for zoom in (.5,1,2):
                visual=rect*page.rotation_matrix
                geometry=field_geometry(page,rect,zoom,page.rect.width*zoom+100,page.rect.height*zoom+80)
                self.assertEqual(geometry,(round(50+visual.x0*zoom),round(40+visual.y0*zoom),round(visual.width*zoom),round(visual.height*zoom)))

    def test_new_document_failure_returns_translated_error(self):
        with patch('pdflx.pdf_handler.fitz.open',side_effect=ValueError('Invalid page')):
            doc,error=pdf_handler.create_new_pdf()
        self.assertIsNone(doc)
        self.assertIsInstance(error,str)
        self.assertIn('Invalid page',error)

    def test_field_properties_reaches_dialog_with_translated_title(self):
        w=self.window()
        tools.create_form_field(w.doc,0,'Name','text',(20,20,220,50))
        field=features.list_form_fields(w.doc)[0]
        controller=FormController.__new__(FormController)
        controller.window=w;controller.editable=Mock(return_value=True)
        w._load_page=Mock()
        # Stop at the GTK boundary so this regression runs without a display.
        class DialogReached(Exception):pass
        with patch('pdflx.form_ui.ToolDialog',side_effect=DialogReached) as dialog:
            with self.assertRaises(DialogReached):controller.edit_field(field)
        dialog.assert_called_once()
        self.assertIs(dialog.call_args.args[0],w)
        self.assertIsInstance(dialog.call_args.args[1],str)
        self.assertTrue(dialog.call_args.args[1])

    def test_radio_group_selection_appearance_reopen_and_undo(self):
        w=self.window()
        self.assertTrue(w._mutate_document(lambda:tools.create_form_field(w.doc,0,'Delivery','radio',
            (20,50,240,125),choices=['Collection','Post'],required=True,value='Collection')))
        fields=features.list_form_fields(w.doc)
        self.assertEqual([f['on_state'] for f in fields],['Collection','Post'])
        self.assertEqual(missing_required(fields),[])
        self.assertTrue(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,fields[1]['xref']):True})))
        fields=features.list_form_fields(w.doc)
        self.assertEqual([f['value'] for f in fields],['Off','Post'])
        self.assertIn('Collection',w.doc[0].get_text());self.assertIn('Post',w.doc[0].get_text())
        with fitz.open(stream=w.doc.tobytes(),filetype='pdf') as saved:
            self.assertEqual([f['value'] for f in features.list_form_fields(saved)],['Off','Post'])
        w.undo_manager.undo()
        self.assertEqual([f['value'] for f in features.list_form_fields(w.doc)],['Collection','Off'])
        w.undo_manager.undo();self.assertEqual(features.list_form_fields(w.doc),[])

    def test_reset_button_restores_defaults_and_skips_readonly(self):
        w=self.window()
        name=tools.create_form_field(w.doc,0,'Name','text',(20,20,220,45),value='Initial')
        fixed=tools.create_form_field(w.doc,0,'Fixed','text',(20,50,220,75),value='Locked',options=dict(readonly=True))
        tools.create_form_field(w.doc,0,'Choice','radio',(20,90,220,155),choices=['First','Second'],value='First')
        reset=tools.create_form_field(w.doc,0,'Reset','button',(20,180,150,215),value='Clear changes')
        self.assertEqual(w.doc.xref_get_key(reset,'A/S')[1],'/ResetForm')
        fields=features.list_form_fields(w.doc)
        self.assertTrue(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,name):'Changed',(0,fields[3]['xref']):True})))
        self.assertTrue(w._mutate_document(lambda:reset_form_fields(w.doc)))
        fields=features.list_form_fields(w.doc)
        self.assertEqual([f['value'] for f in fields[:4]],['Initial','Locked','First','Off'])
        w.undo_manager.undo()
        self.assertEqual([f['value'] for f in features.list_form_fields(w.doc)[:4]],['Changed','Locked','Off','Second'])

    def test_radio_group_properties_preserve_labelled_appearance(self):
        w=self.window();xref=tools.create_form_field(w.doc,0,'Group','radio',(30,80,240,155),choices=['Yes','No'])
        tools.edit_form_field(w.doc,0,xref,'Renamed',True,rect=(30,80,260,115),options=dict(readonly=True))
        fields=features.list_form_fields(w.doc)
        self.assertEqual([f['name'] for f in fields],['Renamed','Renamed'])
        self.assertTrue(all(f['readonly'] and f['required'] for f in fields))
        self.assertIn('Yes',w.doc[0].get_text());self.assertIn('No',w.doc[0].get_text())

    def test_radio_group_invalid_geometry_is_atomic(self):
        w=self.window();before=w.doc.tobytes()
        self.assertFalse(w._mutate_document(lambda:tools.create_form_field(w.doc,0,'Group','radio',
            (20,40,220,60),choices=['Yes','No'])))
        self.assertEqual(features.list_form_fields(w.doc),[])

    def test_multiline_style_readonly_and_save_reopen(self):
        w=self.window()
        options=dict(multiline=True,tooltip='Delivery instructions',font='Helv',font_size=12,
                     fill_color=None,text_color=(.1,.2,.4),border_color=(.2,.5,.6),border_width=2)
        x=tools.create_form_field(w.doc,0,'Notes','text',(50,60,350,180),required=True,options=options)
        self.assertTrue(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,x):'Line one\nLine two'})))
        self.assertTrue(w._mutate_document(lambda:tools.edit_form_field(w.doc,0,x,'Notes',True,500,options=dict(readonly=True))))
        with fitz.open(stream=w.doc.tobytes(),filetype='pdf') as saved:
            f=features.list_form_fields(saved)[0]
            self.assertTrue(f['multiline']);self.assertTrue(f['readonly'])
            self.assertEqual(f['tooltip'],'Delivery instructions')
            self.assertEqual(f['value'],'Line one\nLine two')
            self.assertIsNone(f['fill_color'])
            self.assertAlmostEqual(f['font_size'],12)
            self.assertAlmostEqual(f['border_width'],2)
            self.assertIn('Line two',saved[0].get_text())
        w.undo_manager.undo()
        self.assertFalse(features.list_form_fields(w.doc)[0]['readonly'])
        self.assertTrue(features.list_form_fields(w.doc)[0]['multiline'])

    def test_view_filling_uses_form_permission_and_undo(self):
        w=self.window();x=tools.create_form_field(w.doc,0,'Name','text',(20,50,200,80))
        w.view_mode=True;w._active_session.can_edit=False;w.doc.editor_can_fill_forms=True
        self.assertFalse(w._mutate_document(lambda:tools.delete_form_field(w.doc,0,x),allow_view=True))
        self.assertTrue(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,x):'Jane'}),allow_view=True,allow_form=True))
        self.assertTrue(w.undo_manager.undo_stack[-1].view_mode_allowed)
        self.assertTrue(w.undo_manager.undo_stack[-1].form_fill_allowed)
        w.undo_manager.undo();self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'')
        w.undo_manager.redo();self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Jane')
        w.doc.editor_can_fill_forms=False
        self.assertFalse(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,x):'Other'}),allow_view=True,allow_form=True))

    def test_editable_dropdown_preserves_custom_values(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Department','combo',(20,50,200,80),['Sales','Engineering'],options=dict(editable_choice=True))
        self.assertTrue(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,x):'Operations'})))
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Operations')
        tools.edit_form_field(w.doc,0,x,'Department',options=dict(editable_choice=False))
        self.assertFalse(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,x):'New custom choice'})))
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Operations')
        self.assertEqual(choice_options([('NY','New York'),'London']),[('NY','New York'),('London','London')])

    def test_dropdown_export_values_and_labels_remain_distinct(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Country','combo',(10,10,200,40),[('UK','United Kingdom'),('RO','Romania')])
        self.assertTrue(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,x):'RO'})))
        field=features.list_form_fields(w.doc)[0]
        self.assertEqual(field['choices'],[('UK','United Kingdom'),('RO','Romania')])
        self.assertEqual(field['value'],'RO')
        self.assertFalse(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,x):'Romania'})))

    def test_imported_empty_export_placeholder_is_preserved_when_filling(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Country','combo',(10,10,200,40),['Choose','Romania'])
        w.doc.xref_set_key(x,'Opt','[ [() (Choose a country)] [(RO) (Romania)] ]')
        w.doc.xref_set_key(x,'V','()')
        w.doc._reset_page_refs()
        self.assertTrue(w._mutate_document(lambda:features.update_form_fields(w.doc,{(0,x):'RO'})))
        self.assertEqual(features.list_form_fields(w.doc)[0]['choices'],[('', 'Choose a country'),('RO','Romania')])
        self.assertTrue(w._mutate_document(lambda:tools.edit_form_field(w.doc,0,x,'Country',options=dict(font_size=12))))
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'RO')

    def test_required_check_respects_pending_values_and_readonly(self):
        w=self.window()
        a=tools.create_form_field(w.doc,0,'Name','text',(10,10,200,40),required=True)
        b=tools.create_form_field(w.doc,0,'Consent','checkbox',(10,50,30,70),required=True)
        tools.create_form_field(w.doc,0,'Fixed','text',(10,90,200,120),required=True,options=dict(readonly=True))
        fields=features.list_form_fields(w.doc)
        self.assertEqual([f['name'] for f in missing_required(fields)],['Name','Consent'])
        self.assertEqual(missing_required(fields,{(0,a):'Jane',(0,b):True}),[])
        self.assertEqual([f['name'] for f in missing_required(fields,{(0,a):'   '})],['Name','Consent'])

    def test_bad_style_rolls_back_widget_and_appearance(self):
        w=self.window();x=tools.create_form_field(w.doc,0,'Name','text',(10,10,200,40))
        before=features.list_form_fields(w.doc);pixels=w.doc[0].get_pixmap().samples
        self.assertFalse(w._mutate_document(lambda:tools.edit_form_field(w.doc,0,x,'Renamed',options=dict(font_size=-1))))
        self.assertEqual(features.list_form_fields(w.doc),before)
        self.assertEqual(w.doc[0].get_pixmap().samples,pixels)

    def test_sidebar_pending_values_save_in_view_and_undo_immediately(self):
        w=self.window();x=tools.create_form_field(w.doc,0,'Name','text',(10,10,200,40))
        w.view_mode=True
        form=SimpleNamespace(window=w,source=w.doc,pending={(0,x):'Jane'},save=Mock(),message=Mock(),cancel_drag=Mock())
        form.fillable=lambda:True
        form.save_values=FormController.save_values.__get__(form)
        w.form_tools=form
        w.undo_manager.undo()
        self.assertEqual(form.pending,{})
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'')
        w.undo_manager.redo()
        self.assertEqual(features.list_form_fields(w.doc)[0]['value'],'Jane')

    def test_initial_values_work_for_readonly_fields_and_checkboxes(self):
        w=self.window()
        tools.create_form_field(w.doc,0,'Reference','text',(10,10,200,40),options=dict(readonly=True),value='REF-001')
        tools.create_form_field(w.doc,0,'Consent','checkbox',(10,50,30,70),value=True)
        fields=features.list_form_fields(w.doc)
        self.assertEqual(fields[0]['value'],'REF-001')
        self.assertTrue(fields[0]['readonly'])
        self.assertEqual(fields[1]['value'],fields[1]['on_state'])

    def test_pending_sidebar_values_survive_document_switches(self):
        w=self.window()
        first=w.doc
        x=tools.create_form_field(first,0,'Name','text',(10,10,200,40))
        second=fitz.open();second.new_page();self.addCleanup(second.close)
        tools.create_form_field(second,0,'Other','text',(10,10,200,40))
        class Node:
            def __init__(self,*args,**kwargs):pass
            def append(self,*args):pass
            def get_first_child(self):return None
            def add_css_class(self,*args):pass
            def connect(self,*args):pass
            def set_sensitive(self,*args):pass
        w.get_focus=lambda:None
        form=SimpleNamespace(window=w,source=first,pending={(0,x):'Draft'},
             sidebar=SimpleNamespace(get_reveal_child=lambda:True),rows=Node(),create=Mock(),save=Mock(),
             message=Mock(),validate=Mock(),editable=lambda:True,fillable=lambda:True,
             value_control=lambda f:(Mock(),lambda:f['value'],'changed'))
        with patch('pdflx.form_ui.Gtk.Box',Node),patch('pdflx.form_ui.Gtk.Label',Node),patch('pdflx.form_ui.Gtk.Button',Node),patch('pdflx.form_ui.Gtk.Separator',Node):
            w.doc=second
            FormController.refresh(form)
            self.assertEqual(form.pending,{})
            w.doc=first
            FormController.refresh(form)
            self.assertEqual(form.pending,{(0,x):'Draft'})
        self.assertEqual(features.list_form_fields(first)[0]['value'],'')

    def test_checkbox_uses_native_tick_font(self):
        w=self.window()
        x=tools.create_form_field(w.doc,0,'Accept','checkbox',(10,10,30,30),options=dict(font='Helv',font_size=11))
        page=w.doc[0];widget=page.load_widget(x)
        self.assertEqual(widget.text_font,'ZaDb')
        self.assertEqual(widget.text_fontsize,0)
