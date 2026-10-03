"""Page-based form creation, filling, and field management."""
import pymupdf as fitz
from gi.repository import Gtk, Pango
from . import document_tools as tools, document_features as features
from .document_tool_ui import ToolDialog
from .dialogs import FormRows
from .models import EditableShape
from .i18n import _


class FormController:
    def __init__(self,window):
        self.window=window
        self.pending={}
        self.source=None
        self.inline_editor=None
        from .form_interaction import FormInteraction
        self.interaction=FormInteraction(self)
        self.sidebar=Gtk.Revealer(hexpand=False,reveal_child=False,
                                  transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10,
                    margin_start=12,margin_end=12,margin_top=12,margin_bottom=12)
        # Margins add another 24px. Keep labels' natural width below this
        # content width so the sidebar does not take space from the page.
        box.set_size_request(296,-1)
        header=Gtk.Box(spacing=8)
        title=Gtk.Label(label=_("menu_form_fields"),xalign=0,hexpand=True)
        title.add_css_class('heading')
        header.append(title)
        close=Gtk.Button(icon_name='window-close-symbolic')
        close.add_css_class('flat')
        close.connect('clicked',lambda button:self.sidebar.set_reveal_child(False))
        header.append(close)
        box.append(header)
        self.create=Gtk.Button(label=_("tool_create_field"))
        self.create.connect('clicked',lambda button:self.create_field())
        box.append(self.create)
        self.validate=Gtk.Button(label='Check Required Fields')
        self.validate.connect('clicked',lambda *_:self.validate_fields())
        box.append(self.validate)
        self.message=Gtk.Label(xalign=0,wrap=True,max_width_chars=34,
                               wrap_mode=Pango.WrapMode.WORD_CHAR)
        box.append(self.message)
        scroll=Gtk.ScrolledWindow(vexpand=True,hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.rows=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        scroll.set_child(self.rows)
        box.append(scroll)
        self.save=Gtk.Button(label=_("form_save_values"))
        self.save.add_css_class('suggested-action')
        self.save.connect('clicked',lambda button:self.save_values())
        box.append(self.save)
        self.sidebar.set_child(box)

    def editable(self):
        return self.window.document_tools.editable()

    def fillable(self):
        doc=self.window.doc
        return bool(doc and getattr(doc,'editor_can_fill_forms',getattr(self.window._active_session,'can_edit',True)))

    def cancel_drag(self):
        if hasattr(self,'interaction') and self.interaction.drag:
            self.interaction.drag=None
            self.window.dragging_to_create=False
        if getattr(self,'drag_start',None):
            self.window.temp_shape=None
        self.drag_start=None

    def draw_fields(self,cr):
        if hasattr(self,'interaction'):self.interaction.draw(cr)
        if not self.sidebar.get_reveal_child() or not self.fillable():
            return
        cr.save()
        cr.set_source_rgba(0.1,0.45,0.9,0.8)
        cr.set_line_width(1.5/self.window.zoom_level)
        for field in features.list_form_fields(self.window.doc,[self.window.current_page_index]):
            x0,y0,x1,y1=field['rect']
            cr.rectangle(x0,y0,x1-x0,y1-y0)
            cr.stroke()
        cr.restore()

    def show(self):
        self.window.document_tools.sidebar.set_reveal_child(False)
        self.sidebar.set_reveal_child(True)
        self.refresh()

    def _button_controls(self,dialog,source,field=None):
        """Action controls for push buttons. Returns (read, set_visible)."""
        from .form_buttons import ACTIONS,SUBMIT_FORMATS
        labels={'reset':_("button_action_reset"),'goto':_("button_action_goto"),'submit':_("button_action_submit"),
                'url':_("button_action_url"),'print':_("button_action_print"),'javascript':_("button_action_javascript")}
        action=dialog.dropdown(_("button_action"),[labels[key] for key in ACTIONS])
        current=(field or {}).get('button_action') or 'reset'
        target=(field or {}).get('button_target')
        action.set_selected(ACTIONS.index(current) if current in ACTIONS else 0)
        rows=[(action,dialog.grid.get_child_at(0,dialog.row-1))]
        page=dialog.spin(_("button_destination_page"),((field or {}).get('button_page') or self.window.current_page_index)+1,1,source.page_count)
        rows.append((page,dialog.grid.get_child_at(0,dialog.row-1)))
        url=dialog.entry(_("button_url"),target.get('url','') if isinstance(target,dict) else target if current=='url' else '')
        url.set_placeholder_text('https://example.org/form')
        rows.append((url,dialog.grid.get_child_at(0,dialog.row-1)))
        formats=list(SUBMIT_FORMATS)
        fmt=dialog.dropdown(_("button_submit_format"),[key.upper() for key in formats])
        if isinstance(target,dict) and target.get('format') in formats:fmt.set_selected(formats.index(target['format']))
        rows.append((fmt,dialog.grid.get_child_at(0,dialog.row-1)))
        script=dialog.multiline(_("button_script"),target if current=='javascript' and isinstance(target,str) else '')
        script_widget=dialog.grid.get_child_at(1,dialog.row-1)
        rows.append((script_widget,dialog.grid.get_child_at(0,dialog.row-1)))
        state={'shown':True}
        def update(*args):
            key=ACTIONS[action.get_selected()]
            wanted={'goto':(page,),'url':(url,),'submit':(url,fmt),'javascript':(script_widget,)}.get(key,())
            for widget,label in rows:
                visible=state['shown'] and (widget is action or widget in wanted)
                widget.set_visible(visible)
                if label is not None:label.set_visible(visible)
        action.connect('notify::selected',update)
        def set_visible(shown):
            state['shown']=shown;update()
        update()
        def read():
            key=ACTIONS[action.get_selected()]
            return dict(button_action=key,button_page=page.get_value_as_int()-1,button_url=url.get_text(),
                        button_format=formats[fmt.get_selected()],
                        button_script=script.get_text(script.get_start_iter(),script.get_end_iter(),True))
        return read,set_visible

    def _date_picker(self,entry,pattern):
        """Calendar button that writes the chosen date into ``entry`` using the field's format."""
        import datetime
        from .ops.formbehaviour import date_format_to_strftime
        button=Gtk.MenuButton(icon_name='x-office-calendar-symbolic',tooltip_text=_("form_pick_date"))
        calendar=Gtk.Calendar()
        popover=Gtk.Popover(child=calendar)
        button.set_popover(popover)
        def chosen(widget):
            day=widget.get_date()
            date=datetime.date(day.get_year(),day.get_month(),day.get_day_of_month())
            entry.set_text(date.strftime(date_format_to_strftime(pattern)))
            popover.popdown()
        calendar.connect('day-selected',chosen)
        box=Gtk.Box(spacing=4)
        entry.set_hexpand(True)
        box.append(entry);box.append(button)
        return box

    def value_control(self,field,picker=None):
        if picker is None:
            picker=getattr(self,'_sidebar_controls',False)
        kind=field['type']
        if kind==fitz.PDF_WIDGET_TYPE_TEXT:
            if field.get('multiline'):
                view=Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,left_margin=6,right_margin=6,
                                  top_margin=6,bottom_margin=6)
                buffer=view.get_buffer();buffer.set_text(str(field['value'] or ''))
                control=Gtk.ScrolledWindow(min_content_height=90,max_content_height=150,
                                          propagate_natural_height=True)
                control.set_child(view)
                read=lambda:buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True)
                signal='changed'
                control._form_signal_source=buffer
            else:
                control=Gtk.Entry(hexpand=True,text=str(field['value'] or ''))
                if field['flags']&fitz.PDF_TX_FIELD_IS_PASSWORD:control.set_visibility(False)
                if field['max_length']:control.set_max_length(field['max_length'])
                entry=control
                read=lambda:entry.get_text()
                signal='changed'
                if picker and self.window.doc is not None:
                    from .ops.formbehaviour import parse
                    behaviour=parse(self.window.doc,field['xref'])
                    if behaviour.get('format')=='date':
                        control=self._date_picker(entry,behaviour.get('date_format','dd/mm/yyyy'))
                        control._form_signal_source=entry
        elif kind in (fitz.PDF_WIDGET_TYPE_CHECKBOX,fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
            control=Gtk.CheckButton(label=field['on_state'] if kind==fitz.PDF_WIDGET_TYPE_RADIOBUTTON else _("form_checked"),active=field['value']==field['on_state'])
            read=lambda:control.get_active()
            signal='toggled'
        elif kind==fitz.PDF_WIDGET_TYPE_LISTBOX and field['flags']&fitz.PDF_CH_FIELD_IS_MULTI_SELECT:
            from .form_properties import choice_options
            pairs=choice_options(field['choices'])
            current=set(field['value'] or ())
            control=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2)
            checks=[]
            for export,label in pairs:
                check=Gtk.CheckButton(label=label,active=export in current)
                control.append(check);checks.append((export,check))
            read=lambda:[export for export,check in checks if check.get_active()]
            signal='toggled'
            # Emit one 'toggled'-style signal for the whole group.
            relay=checks[0][1] if checks else control
            for _export,check in checks[1:]:
                check.connect('toggled',lambda *_a,relay=relay:relay.emit('toggled'))
            control._form_signal_source=relay
        elif kind in (fitz.PDF_WIDGET_TYPE_COMBOBOX,fitz.PDF_WIDGET_TYPE_LISTBOX) and not field['flags']&fitz.PDF_CH_FIELD_IS_MULTI_SELECT:
            from .form_properties import choice_options
            pairs=choice_options(field['choices'])
            if kind==fitz.PDF_WIDGET_TYPE_COMBOBOX and field['flags']&fitz.PDF_CH_FIELD_IS_EDIT:
                control=Gtk.Entry(hexpand=True,text=str(field['value'] or ''))
                read=lambda:control.get_text();signal='changed'
            else:
                exports=[item[0] for item in pairs]
                labels=[item[1] for item in pairs]
                current=str(field['value'] or '')
                if current not in exports:
                    exports.insert(0,current);labels.insert(0,current or 'Choose…')
                control=Gtk.DropDown.new_from_strings(labels)
                control.set_selected(exports.index(current))
                read=lambda:exports[control.get_selected()]
                signal='notify::selected'
        else:
            label=_("signature_signed") if field.get('signed') else _("signature_unsigned") if kind==fitz.PDF_WIDGET_TYPE_SIGNATURE else str(field['value'] or '')
            control=Gtk.Label(label=label,xalign=0,wrap=True,max_width_chars=34,
                              wrap_mode=Pango.WrapMode.WORD_CHAR)
            return control,None,None
        control.set_sensitive(self.fillable() and not field['readonly'])
        control.set_tooltip_text(field.get('tooltip') or field['name'])
        return control,read,signal

    def refresh(self):
        editor=getattr(self,'inline_editor',None)
        if editor and editor.source is not self.window.doc:editor.finish()
        if not self.sidebar.get_reveal_child():
            return
        doc=self.window.doc
        fields=features.list_form_fields(doc) if doc else []
        state=(id(doc),self.editable(),self.fillable(),fields)
        if state==getattr(self,'state',None):
            return
        self.state=state
        if doc is not self.source:
            if self.source is not None:self.source.editor_form_drafts=dict(self.pending)
            self.pending=dict(getattr(doc,'editor_form_drafts',{})) if doc is not None else {}
        self.source=doc
        keys={(f['page'],f['xref']) for f in fields}
        self.pending={key:value for key,value in self.pending.items() if key in keys}
        focus=self.window.get_focus()
        if focus and focus.is_ancestor(self.rows):
            self.window.set_focus(None)
        while child:=self.rows.get_first_child():
            self.rows.remove(child)
        self.create.set_sensitive(self.editable())
        self.validate.set_sensitive(self.fillable() and bool(fields))
        self.message.set_text('Click a field to fill it. Select to move or resize; Properties edits labels and actions.' if fields else _("form_empty_hint"))
        self.save.set_sensitive(self.fillable() and bool(self.pending))
        for field in fields:
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6)
            title=Gtk.Label(label=field['name']+(' *' if field['required'] else '')+(' · read-only' if field['readonly'] else ''),xalign=0,wrap=True,
                            max_width_chars=30,wrap_mode=Pango.WrapMode.WORD_CHAR)
            title.add_css_class('heading')
            box.append(title)
            types={7:'Text',2:'Checkbox',3:'Dropdown',4:'List',5:'Radio',6:'Signature',1:'Button'}
            box.append(Gtk.Label(label=_("form_page_label",field['page']+1)+' · '+types.get(field['type'],'Field'),xalign=0))
            key=(field['page'],field['xref'])
            current=dict(field)
            if key in self.pending:
                value=self.pending[key]
                current['value']=field['on_state'] if value is True else 'Off' if value is False else value
            self._sidebar_controls=True
            try:
                control,read,signal=self.value_control(current)
            finally:
                self._sidebar_controls=False
            if field['type']==fitz.PDF_WIDGET_TYPE_BUTTON:
                from .form_buttons import describe
                control=Gtk.Label(label=(field.get('button_caption') or '(Unlabelled button)')+' · '+
                    describe(field.get('button_action'),field.get('button_target')),xalign=0,wrap=True,
                    max_width_chars=34,wrap_mode=Pango.WrapMode.WORD_CHAR)
            box.append(control)
            if read:
                def changed(*args,key=key,read=read):
                    self.pending[key]=read()
                    self.save.set_sensitive(self.fillable())
                    self.window.document_modified=True
                    self.window._update_ui_state()
                getattr(control,'_form_signal_source',control).connect(signal,changed)
            actions=Gtk.Box(spacing=4)
            go=Gtk.Button(label='Select' if self.editable() else 'Fill',
                          tooltip_text='Select on page' if self.editable() else 'Fill on page')
            def select(button,field=field):
                self.window._load_page(field['page'])
                if self.editable():self.interaction.select(field)
                else:self.fill_field(field)
            go.connect('clicked',select)
            actions.append(go)
            properties=Gtk.Button(label='Properties…',sensitive=self.editable())
            properties.connect('clicked',lambda button,field=field:self.edit_field(field))
            actions.append(properties)
            duplicate=Gtk.Button(icon_name='edit-copy-symbolic',tooltip_text='Duplicate radio group (Ctrl+D)' if field['type']==fitz.PDF_WIDGET_TYPE_RADIOBUTTON else 'Duplicate field (Ctrl+D)')
            duplicate.set_sensitive(self.editable() and not field.get('signed'))
            duplicate.connect('clicked',lambda button,field=field:self.duplicate_field(field))
            actions.append(duplicate)
            remove=Gtk.Button(icon_name='user-trash-symbolic',tooltip_text=_("form_delete_field"))
            remove.set_sensitive(self.editable() and field['type']!=fitz.PDF_WIDGET_TYPE_SIGNATURE)
            remove.connect('clicked',lambda button,field=field:self.window._mutate_document(
                lambda:tools.delete_form_field(doc,field['page'],field['xref']),page_num=field['page']))
            actions.append(remove)
            box.append(actions)
            box.append(Gtk.Separator())
            self.rows.append(box)

    def duplicate_field(self,field):
        if not self.editable() or field.get('signed'):return False
        if not self.save_values():return False
        from .form_duplication import duplicate_form_field
        source=self.window.doc;created=[]
        def mutate():created.extend(duplicate_form_field(source,field['page'],field['xref']))
        if not self.window._mutate_document(mutate,page_num=field['page']):return False
        number,xref=next((item for item in created if item[0]==field['page']),created[0])
        copy=next(f for f in features.list_form_fields(source,[number]) if f['xref']==xref)
        self.window._load_page(number)
        if hasattr(self,'interaction'):self.interaction.select(copy)
        if hasattr(self.window.pdf_view,'grab_focus'):self.window.pdf_view.grab_focus()
        self.window.status_label.set_text('Duplicated '+copy['name'])
        self.state=None;self.refresh()
        return True

    def save_values(self):
        editor=getattr(self,'inline_editor',None)
        if editor:return editor.finish()
        if not self.pending:return True
        if self.source is not self.window.doc:return True
        if not self.fillable():return False
        values=dict(self.pending)
        try:
            ok=self.window._mutate_document(lambda:features.update_form_fields(self.source,values),allow_view=True,allow_form=True)
        except ValueError as error:
            # Like other readers: report the script's rejection and restore the previous value.
            source=self.window.doc
            for key in list(self.pending):
                try:
                    widget_name=source.xref_get_key(key[1],'T')[1]
                except Exception:
                    widget_name=''
                if widget_name and widget_name in str(error):
                    self.pending.pop(key,None)
            source.editor_form_drafts=dict(self.pending)
            self.message.set_text(str(error))
            from .dialogs import message
            message(self.window,_("form_value_rejected"),str(error))
            self.refresh()
            self.window.pdf_view.queue_draw()
            return False
        if ok:
            self.pending.clear()
            self.source.editor_form_drafts={}
            self.save.set_sensitive(False)
            self.message.set_text(_("form_values_saved"))
            return True
        return False

    def validate_fields(self):
        if self.source is not self.window.doc:return
        from .form_properties import missing_required
        missing=missing_required(features.list_form_fields(self.source),self.pending)
        if missing:
            self.message.set_text('Missing required fields: '+', '.join(field['name'] for field in missing))
            self.window._load_page(missing[0]['page'])
            self.window.pdf_view.queue_draw()
        else:self.message.set_text('All required fields are complete.')

    def create_field(self):
        if not self.editable():
            return
        source=self.window.doc
        fields=features.list_form_fields(source)
        existing={f['name'] for f in fields}
        number=1
        while f'Field {number}' in existing:
            number+=1
        kinds=('text','checkbox','combo','list','radio','button','signature')
        def choose():
            field_name=name.get_text().strip()
            if not field_name or field_name in existing:
                raise ValueError(_("form_unique_name"))
            options=[choice.strip() for choice in choices.get_text(choices.get_start_iter(),choices.get_end_iter(),True).splitlines() if choice.strip()]
            selected=kinds[kind.get_selected()]
            if selected=='list' and read_settings().get('multi_select'):
                # Several initial selections are separated by semicolons.
                values=[part.strip() for part in default.get_text().split(';') if part.strip()]
                missing=[value for value in values if value not in options]
                if missing:raise ValueError('Choose listed initial values.')
            if selected in ('combo','list','radio') and len(set(options))<2:
                raise ValueError(_("form_choices_hint"))
            initial=checked.get_active() if selected=='checkbox' else default.get_text() or ('' if selected=='text' else None)
            if selected=='list' and read_settings().get('multi_select'):
                initial=values
            elif selected in ('combo','list','radio') and initial is not None and initial not in options and not (selected=='combo' and read_settings()['editable_choice']):
                raise ValueError('Choose a listed initial value.')
            settings=read_settings()
            if selected=='signature':settings['readonly']=False
            if selected=='button':
                settings.update(button_caption=default.get_text().strip() or 'Reset form',**read_button())
            behaviour=read_behaviour() if selected in ('text','combo') else None
            if behaviour is not None:
                from .ops import formbehaviour
                formbehaviour.build_scripts(behaviour)  # validate before drawing
            self.draw_settings=dict(source=source,name=field_name,kind=selected,choices=options,
                required=required.get_active() and selected not in ('button','signature'),options=settings,value=initial,
                behaviour=behaviour)
            self.window.on_tool_selected(None,'form_create')
            self.window.status_label.set_text(_("form_draw_hint"))
        dialog=ToolDialog(self.window,_("tool_create_field"),choose)
        dialog.get_widget_for_response(Gtk.ResponseType.APPLY).set_label(_("form_draw_field"))
        name=dialog.entry(_("tool_field_name"),f'Field {number}')
        kind=dialog.dropdown(_("tool_field_type"),[_("tool_text_field"),_("tool_checkbox"),_("tool_combo"),_("tool_list"),'Radio group',_("button_kind"),'Certificate signature'])
        required=dialog.check(_("tool_required"))
        default=dialog.entry('Initial value')
        default_label=dialog.grid.get_child_at(0,dialog.row-1)
        read_button,show_button=self._button_controls(dialog,source)
        checked=dialog.check('Checked initially')
        from .form_style_ui import add_form_settings
        read_settings,multiline,editable_choice=add_form_settings(dialog)
        appearance_grid=dialog.grid
        behaviour_grid=multiline.get_ancestor(FormRows)
        dialog.grid=dialog.new_grid()
        dialog.row=0
        choices_page=dialog.grid
        choices_tab=Gtk.Label(label='Options')
        dialog.form_notebook.append_page(choices_page,choices_tab)
        choices=dialog.multiline(_("form_choices_lines"),'Option 1\nOption 2')
        saved_grid,saved_row=dialog.grid,dialog.row
        from .form_behaviour_ui import add_behaviour_tab
        read_behaviour,behaviour_page=add_behaviour_tab(dialog,source,None)
        behaviour_tab=dialog.form_notebook.get_tab_label(behaviour_page)
        dialog.grid,dialog.row=saved_grid,saved_row
        hint=Gtk.Label(label='One option per line. Radio groups place one button for each option inside the area you draw.',wrap=True,xalign=0)
        dialog.grid.attach(hint,0,dialog.row,2,1)
        def behaviour_changed(*args):
            selected=kinds[kind.get_selected()]
            show_default=selected in ('text','combo','list','radio','button')
            default.set_visible(show_default)
            default_label.set_visible(show_default)
            default_label.set_text('Button label' if selected=='button' else 'Initial value')
            default.set_placeholder_text('Reset form' if selected=='button' else 'Optional')
            show_button(selected=='button')
            checked.set_visible(kind.get_selected()==1)
            dialog.form_kind_controls(selected)
            choices_page.set_visible(selected in ('combo','list','radio'))
            choices_tab.set_visible(selected in ('combo','list','radio'))
            behaviour_page.set_visible(selected in ('text','combo'))
            behaviour_tab.set_visible(selected in ('text','combo'))
            required.set_sensitive(selected not in ('button','signature'))
            appearance_grid.set_sensitive(selected!='signature')
            behaviour_grid.set_sensitive(selected!='signature')
            dialog.message.set_text('Click the placed field to sign it with a certificate.' if selected=='signature' else
                                    'Click the placed button to restore form defaults.' if selected=='button' else _("form_create_hint"))
        kind.connect('notify::selected',behaviour_changed)
        behaviour_changed()
        dialog.present()

    def edit_field(self,field):
        if not self.editable():
            return
        source=self.window.doc
        self.window._load_page(field['page'])
        # Refresh from the PDF instead of retaining stale widget wrappers.
        field=next((f for f in features.list_form_fields(source) if f['xref']==field['xref'] and f['page']==field['page']),None)
        if not field:
            return
        def apply():
            def mutate():
                if read and not field['readonly']:
                    features.update_form_fields(source,{(field['page'],field['xref']):read()})
                if field['type']!=fitz.PDF_WIDGET_TYPE_SIGNATURE:
                    options=choices.get_text(choices.get_start_iter(),choices.get_end_iter(),True).splitlines() if choices else None
                    if options==choice_labels:options=None
                    settings=read_settings()
                    if field['type']==fitz.PDF_WIDGET_TYPE_BUTTON:
                        settings.update(button_caption=control.get_text(),**read_button())
                    tools.edit_form_field(source,field['page'],field['xref'],name.get_text(),required.get_active() and field['type']!=fitz.PDF_WIDGET_TYPE_BUTTON,limit.get_value_as_int(),
                        rect=(x.get_value(),y.get_value(),x.get_value()+width.get_value(),y.get_value()+height.get_value()) if
                             (x.get_value(),y.get_value(),x.get_value()+width.get_value(),y.get_value()+height.get_value())!=tuple(field['rect']) else None,
                        choices=options,options=settings)
                    if read_behaviour is not None:
                        from .ops import formbehaviour
                        formbehaviour.apply(source,field['xref'],read_behaviour())
            result=self.window._mutate_document(mutate,page_num=field['page'])
            if result:
                self.pending.pop((field['page'],field['xref']),None)
                self.state=None
                self.refresh()
            return result
        dialog=ToolDialog(self.window,_("form_edit_field"),apply)
        name=dialog.entry(_("tool_field_name"),field['name'])
        name.set_sensitive(field['type']!=fitz.PDF_WIDGET_TYPE_SIGNATURE)
        control,read,signal=self.value_control(field)
        if field['type']==fitz.PDF_WIDGET_TYPE_BUTTON:
            control=Gtk.Entry(text=field.get('button_caption') or 'Reset form',hexpand=True);read=None
        dialog.field('Button label' if field['type']==fitz.PDF_WIDGET_TYPE_BUTTON else _("form_value"),control)
        required=dialog.check(_("tool_required"),field['required'])
        required.set_sensitive(field['type'] not in (fitz.PDF_WIDGET_TYPE_SIGNATURE,fitz.PDF_WIDGET_TYPE_BUTTON))
        limit=dialog.spin(_("form_char_limit"),field['max_length'],0,100000)
        limit.set_visible(field['type']==fitz.PDF_WIDGET_TYPE_TEXT)
        dialog.grid.get_child_at(0,3).set_visible(field['type']==fitz.PDF_WIDGET_TYPE_TEXT)
        from .form_properties import choice_options
        choice_labels=[pair[1] for pair in choice_options(field['choices'])]
        choices=dialog.multiline(_("form_choices_lines"),'\n'.join(choice_labels)) if field['type'] in (fitz.PDF_WIDGET_TYPE_COMBOBOX,fitz.PDF_WIDGET_TYPE_LISTBOX) else None
        if field['type']==fitz.PDF_WIDGET_TYPE_BUTTON:
            read_button,_show=self._button_controls(dialog,source,field)
        from .form_style_ui import add_form_settings
        read_settings,multiline_control,editable_choice_control=add_form_settings(dialog,field)
        read_behaviour=None
        if field['type'] in (fitz.PDF_WIDGET_TYPE_TEXT,fitz.PDF_WIDGET_TYPE_COMBOBOX):
            from .form_behaviour_ui import add_behaviour_tab
            read_behaviour,_tab=add_behaviour_tab(dialog,source,field)
        if field['type']==fitz.PDF_WIDGET_TYPE_SIGNATURE:dialog.grid.set_sensitive(False)
        if field['type']==fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            dialog.grid.set_sensitive(False)
            dialog.message.set_text('Radio appearance is set when creating the group. Position and behaviour remain editable.')
        dialog.grid=dialog.new_grid()
        dialog.row=0
        dialog.form_notebook.append_page(dialog.grid,Gtk.Label(label='Position'))
        x0,y0,x1,y1=field['rect']
        x=dialog.spin('X · pt',x0,-100000,100000)
        y=dialog.spin('Y · pt',y0,-100000,100000)
        width=dialog.spin('Width · pt',x1-x0,1,100000)
        height=dialog.spin('Height · pt',y1-y0,1,100000)
        if field['type']==fitz.PDF_WIDGET_TYPE_SIGNATURE:dialog.grid.set_sensitive(False)
        move=Gtk.Button(label=_("form_redraw_bounds"))
        move.set_sensitive(field['type']!=fitz.PDF_WIDGET_TYPE_SIGNATURE)
        def redraw(button):
            if apply() is False:return
            updated=next(f for f in features.list_form_fields(source) if f['page']==field['page'] and f['xref']==field['xref'])
            self.draw_settings=dict(source=source,field=updated)
            dialog.destroy()
            self.window.on_tool_selected(None,'form_reposition')
            self.window.status_label.set_text(_("form_redraw_hint"))
        move.connect('clicked',redraw)
        dialog.grid.attach(move,0,dialog.row,2,1)
        if field['type']==fitz.PDF_WIDGET_TYPE_SIGNATURE:dialog.get_widget_for_response(Gtk.ResponseType.APPLY).set_sensitive(False)
        dialog.message.set_text('Radio appearance is set when creating the group. Position and behaviour remain editable.'
                                if field['type']==fitz.PDF_WIDGET_TYPE_RADIOBUTTON else _("form_edit_hint"))
        dialog.present()

    def fill_field(self,field):
        if not self.fillable():return
        editor=getattr(self,'inline_editor',None)
        if editor:
            if editor.source is self.window.doc and editor.key==(field['page'],field['xref']):
                editor.focus();return
            if not editor.finish():return
        if self.window.current_page_index!=field['page']:self.window._load_page(field['page'])
        if field['readonly']:return
        if field['type'] not in (2,3,4,5,7):return
        if field['type']==fitz.PDF_WIDGET_TYPE_LISTBOX and field['flags']&fitz.PDF_CH_FIELD_IS_MULTI_SELECT:
            self.fill_multi_select(field)
            return
        if self.source is not self.window.doc:
            if self.source is not None:self.source.editor_form_drafts=dict(self.pending)
            self.refresh()
            self.source=self.window.doc
            self.pending=dict(getattr(self.source,'editor_form_drafts',{}))
        from .form_inline_editor import FormInlineEditor
        self.inline_editor=FormInlineEditor(self,field)

    def run_button_script(self,field):
        """Run a button's JavaScript with the sandboxed engine (undoable)."""
        from .i18n import get_setting
        from .ops import formjs
        if not get_setting('form_scripts',True) or not formjs.available():
            self.window.status_label.set_text(_("form_scripts_disabled"))
            return
        if not self.save_values():return
        doc=self.window.doc
        try:
            ok=self.window._mutate_document(lambda:formjs.run_button(doc,field['page'],field['xref']),
                                            page_num=field['page'],allow_view=True,allow_form=True)
        except Exception as error:
            from .dialogs import message
            message(self.window,_("err_title"),str(error))
            return
        if ok:
            self.refresh()
            self.window.status_label.set_text(_("form_script_ran"))

    def fill_multi_select(self,field):
        """Choose several list values in a dialog (inline editing shows one value)."""
        from .dialogs import OperationDialog
        source=self.window.doc
        dialog=OperationDialog(self.window,field['name'],_("btn_apply"))
        group=dialog.group(field.get('tooltip') or None,_("form_multi_select_hint"))
        control,read,_signal=self.value_control(field)
        group.add(control)
        def apply(_dialog):
            if source is not self.window.doc:raise ValueError(_("tool_document_changed"))
            values=read()
            ok=self.window._mutate_document(lambda:features.update_form_fields(source,{(field['page'],field['xref']):values}),
                                            page_num=field['page'],allow_view=True,allow_form=True)
            if not ok:raise ValueError(_("err_edit_not_allowed"))
            self.pending.pop((field['page'],field['xref']),None)
            self.refresh()
        dialog.on_apply=apply
        dialog.show()

    def finish_inline(self):
        editor=getattr(self,'inline_editor',None)
        return editor.finish() if editor else True

    def focus_next(self,field,backwards=False):
        fields=[f for f in features.list_form_fields(self.window.doc) if not f['readonly'] and f['type'] in (2,3,4,5,7)]
        index=next((i for i,f in enumerate(fields) if f['xref']==field['xref']),None)
        if fields and index is not None:self.fill_field(fields[(index+(-1 if backwards else 1))%len(fields)])

    def click_field(self,field):
        if hasattr(self,'interaction') and self.editable():self.interaction.select(field)
        if not self.fillable() or field['readonly']:return
        editor=getattr(self,'inline_editor',None)
        if editor and editor.key!=(field['page'],field['xref']) and not editor.finish():return
        if field['type']==fitz.PDF_WIDGET_TYPE_SIGNATURE:
            if self.editable() and not field.get('signed'):
                self.window.on_sign_certificate(field_name=field['name'])
            elif self.editable():self.edit_field(field)
            return
        if field['type']==fitz.PDF_WIDGET_TYPE_BUTTON:
            from .form_buttons import details
            action,destination=details(self.window.doc,field['xref'])
            if action=='reset':
                from .extended_form_fields import reset_form_fields
                if not self.save_values():return
                if self.window._mutate_document(lambda:reset_form_fields(self.window.doc),allow_view=True,allow_form=True):
                    self.window.status_label.set_text('Form reset to its initial values.')
            elif action=='goto':
                if self.save_values():self.window._load_page(destination)
            elif action=='url':
                launcher=Gtk.UriLauncher.new(destination)
                launcher.launch(self.window,None,None,None)
            elif action=='print':
                if self.save_values():self.window.lookup_action('print').activate(None)
            elif action=='javascript':
                self.run_button_script(field)
            elif action=='submit':
                from .dialogs import alert
                def answer(response):
                    if response=='export':self.window.lookup_action('form_export_data').activate(None)
                alert(self.window,_("button_submit_title"),_("button_submit_body",(destination or {}).get('url','')),
                      [('close',_("btn_close"),None),('export',_("button_submit_export"),'suggested')],'export','close',answer)
            else:self.window.status_label.set_text(_("button_no_action"))
            return
        if field['type'] in (fitz.PDF_WIDGET_TYPE_CHECKBOX,fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
            value=True if field['type']==fitz.PDF_WIDGET_TYPE_RADIOBUTTON else field['value']!=field['on_state']
            self.window._mutate_document(lambda:features.update_form_fields(self.window.doc,
                {(field['page'],field['xref']):value}),page_num=field['page'],allow_view=True,allow_form=True)
        else:self.fill_field(field)

    def begin_drag(self,x,y):
        settings=getattr(self,'draw_settings',None)
        if not settings or settings['source'] is not self.window.doc or not self.editable():
            return False
        page=self.window.doc[self.window.current_page_index]
        if 'field' in settings and settings['field']['page']!=self.window.current_page_index:
            return False
        if not fitz.Point(x,y) in page.rect:
            return False
        self.drag_start=(x,y)
        self.window.temp_shape=EditableShape('rectangle',(0,0,0,0),stroke_color=(0.1,0.45,0.9),stroke_width=1)
        return True

    def update_drag(self,dx,dy):
        x,y=self.drag_start
        visible=self.window.doc[self.window.current_page_index].rect
        endx=max(0,min(visible.width,x+dx))
        endy=max(0,min(visible.height,y+dy))
        self.visual_bounds=fitz.Rect(min(x,endx),min(y,endy),max(x,endx),max(y,endy))
        self.window.temp_shape.bbox=tuple(self.visual_bounds*self.window.doc[self.window.current_page_index].derotation_matrix)
        self.window.pdf_view.queue_draw()

    def end_drag(self,dx,dy):
        self.update_drag(dx,dy)
        bounds=self.visual_bounds
        self.window.temp_shape=None
        if bounds.width<10 or bounds.height<10:
            self.window.status_label.set_text(_("form_draw_too_small"))
            self.window.pdf_view.queue_draw()
            return
        settings=self.draw_settings
        source=settings['source']
        number=self.window.current_page_index
        native=bounds*source[number].derotation_matrix
        if 'field' in settings:
            field=settings['field']
            mutate=lambda:tools.edit_form_field(source,field['page'],field['xref'],field['name'],field['required'],field['max_length'],native)
        else:
            def mutate():
                xref=tools.create_form_field(source,number,settings['name'],settings['kind'],native,settings['choices'],settings['required'],options=settings.get('options'),value=settings.get('value'))
                if settings.get('behaviour'):
                    from .ops import formbehaviour
                    formbehaviour.apply(source,xref,settings['behaviour'])
                return xref
        if self.window._mutate_document(mutate,page_num=number):
            self.window.on_tool_selected(None,'select')
            self.show()
            self.window.status_label.set_text(_("form_field_created"))
