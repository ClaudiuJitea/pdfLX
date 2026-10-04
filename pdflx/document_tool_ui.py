"""Dialogs and comments sidebar for native PDF document tools."""
import pymupdf as fitz
from gi.repository import Adw, Gtk, Gdk, GLib
from . import document_tools as tools, text_geometry, pdf_handler
from .ui_components import show_error_dialog, show_open_file_dialog
from .dialogs import FormRows, PreviewPane, SheetDialog, ToggleSwitch, watch_changes
from .i18n import _


class ToolDialog(SheetDialog):
    """Editing dialog for document tools, built from boxed-list rows.

    Controls are added top to bottom with ``field``/``entry``/``spin``/...;
    ``section`` starts a titled group. Widgets keep their usual GTK API.
    """

    def __init__(self, owner, title, apply, preview=None, destructive=False):
        super().__init__(owner, title, _("btn_apply") if not destructive else _("tool_apply_redaction"),
                         destructive, width=520 + (340 if preview else 0))
        self.owner=owner
        self.source=owner.doc
        self.apply_callback=apply
        self.preview_callback=preview
        self.preview_timer=None
        self.box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12,
                         margin_top=18,margin_bottom=24,margin_start=18,margin_end=18)
        self.grid=FormRows()
        self.box.append(self.grid)
        self.row=0
        self.message=Gtk.Label(wrap=True,xalign=0,margin_start=4)
        self.message.add_css_class('dim-label')
        self.message.add_css_class('caption')
        self.box.append(self.message)
        scroll=Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,propagate_natural_height=True,
                                  child=Adw.Clamp(maximum_size=640,child=self.box))
        scroll.set_size_request(440,-1)
        self.preview_pane=PreviewPane(self.preview if preview else None) if preview else None
        if self.preview_pane:
            self.picture=self.preview_pane.picture
        self.scroll=scroll
        self.set_body(scroll,self.preview_pane)
        if apply is None:
            self.set_informational()
        self.connect('closed',self._closed)

    # -- rows ---------------------------------------------------------------
    def new_grid(self):
        """A fresh row container, e.g. for a notebook page."""
        return FormRows(margin_top=14)

    def section(self,title):
        return self.grid.heading(title)

    def _add(self,row,control=None):
        if isinstance(self.grid,FormRows):
            self.grid.add(row,control,self.row)
        else:
            self.grid.attach(row,0,self.row,2,1)
        self.row+=1
        self._watch(control if control is not None else row)
        return row

    def _watch(self,widget):
        if self.preview_callback:
            watch_changes(widget,self.schedule_preview)

    def field(self,label,widget):
        row=Adw.ActionRow(title=label,use_markup=False)
        row.set_text=row.set_title  # Callers rename captions like Gtk.Label.
        widget.set_valign(Gtk.Align.CENTER)
        row.add_suffix(widget)
        if isinstance(widget,(Gtk.Switch,Gtk.CheckButton)):
            row.set_activatable_widget(widget)
        self._add(row,widget)
        return widget

    def entry(self,label,value=''):
        widget=Gtk.Entry(hexpand=True,text=value,width_chars=16)
        return self.field(label,widget)

    def multiline(self,label,value=''):
        view=Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR,
                          top_margin=8,bottom_margin=8,left_margin=10,right_margin=10)
        view.get_buffer().set_text(value)
        scroll=Gtk.ScrolledWindow(hexpand=True,min_content_height=120,hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class('pdflx-textarea')
        scroll.set_child(view)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8,
                    margin_top=10,margin_bottom=12,margin_start=12,margin_end=12)
        caption=Gtk.Label(label=label,xalign=0)
        box.append(caption)
        box.append(scroll)
        row=Gtk.ListBoxRow(activatable=False,child=box)
        row.set_text=caption.set_text
        self._add(row,scroll)
        self._watch(view.get_buffer())
        return view.get_buffer()

    def spin(self,label,value,low,high,step=1):
        widget=Gtk.SpinButton.new_with_range(low,high,step)
        widget.set_value(value)
        return self.field(label,widget)

    def dropdown(self,label,values):
        row=Adw.ComboRow(title=label,use_markup=False,model=Gtk.StringList.new(list(values)))
        row.set_text=row.set_title
        self._add(row)
        return row

    def check(self,label,active=False):
        widget=ToggleSwitch(active=active)
        self.field(label,widget)
        return widget

    def range(self):
        first=self.spin(_("range_from"),self.owner.current_page_index+1,1,self.source.page_count)
        last=self.spin(_("range_to"),self.owner.current_page_index+1,1,self.source.page_count)
        return first,last

    def add_preview(self,widget):
        """Show ``widget`` in a preview column beside the settings."""
        if self.preview_pane is None:
            self.preview_pane=PreviewPane()
            self.set_content_width(self.get_content_width()+340)
            self.set_body(self.scroll,self.preview_pane)
        self.preview_pane.add(widget)

    # -- Gtk.Dialog compatibility ---------------------------------------------
    def set_default_size(self,width,height=-1):
        self.set_content_width(width+(340 if self.preview_pane else 0))

    def get_widget_for_response(self,response):
        return self.apply_button

    def response(self,response):
        if response==Gtk.ResponseType.APPLY:
            self._on_apply_clicked(None)
        else:
            self.force_close()

    def destroy(self):
        self.force_close()

    def present(self,parent=None):
        super().present(parent or self.owner)
        if self.preview_callback:
            GLib.idle_add(lambda:(self.preview(),False)[1])

    # -- behaviour ------------------------------------------------------------
    def schedule_preview(self):
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
        def fire():
            self.preview_timer=None
            self.preview()
            return False
        self.preview_timer=GLib.timeout_add(450,fire)

    def _closed(self,*args):
        if self.preview_timer:
            GLib.source_remove(self.preview_timer)
            self.preview_timer=None

    def preview(self,button=None):
        try:
            if self.source is not self.owner.doc:
                raise ValueError(_("tool_document_changed"))
            with fitz.open(stream=self.source.tobytes(),filetype='pdf') as scratch:
                number=self.preview_callback(scratch)
                pix=scratch[number].get_pixmap(matrix=fitz.Matrix(0.6,0.6),alpha=False)
                self.picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(pix.tobytes('png'))))
            self.clear_error()
        except Exception as error:
            self.error(error)

    def _on_apply_clicked(self,button):
        if self.apply_callback is None:
            return
        if self.source is not self.owner.doc:
            self.error(_("tool_document_changed"))
            return
        self.clear_error()
        try:
            if self.apply_callback() is not False:
                self.force_close()
        except Exception as error:
            self.error(error)


class DocumentToolsController:
    def __init__(self,window):
        self.window=window
        self.sidebar=Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                  reveal_child=False,hexpand=False)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8,
                    margin_start=12,margin_end=12,margin_top=12,margin_bottom=12)
        box.set_size_request(290,-1)
        header=Gtk.Box(spacing=8)
        title=Gtk.Label(label=_("tool_comments"),xalign=0,hexpand=True)
        title.add_css_class('heading')
        header.append(title)
        close=Gtk.Button(icon_name='window-close-symbolic')
        close.add_css_class('flat')
        close.connect('clicked',lambda button:self.sidebar.set_reveal_child(False))
        header.append(close)
        box.append(header)
        add=Gtk.Button(label=_("tool_add_note"))
        add.connect('clicked',lambda button:self.start_note())
        self.add_review_button=add
        box.append(add)
        # Filters: text/author search, type, review status, and page scope.
        self.filter_entry=Gtk.SearchEntry(placeholder_text=_("comments_filter_placeholder"))
        self.filter_entry.connect('search-changed',lambda entry:self.refresh_comments(force=True))
        box.append(self.filter_entry)
        filters=Gtk.Box(spacing=6)
        self.filter_kinds=['all','Text','FreeText','Highlight','Underline','StrikeOut','Squiggly','Square',
                           'Circle','Line','Polygon','PolyLine','Ink','Stamp','Caret','FileAttachment']
        self.filter_kind=Gtk.DropDown.new_from_strings([_("comments_filter_all_types")]+self.filter_kinds[1:])
        self.filter_states=['all','none','Accepted','Rejected','Cancelled','Completed']
        self.filter_state=Gtk.DropDown.new_from_strings([_("comments_filter_all_states"),_("comments_state_none")]+
                                                        [_(f"comments_state_{key}") for key in self.filter_states[2:]])
        for control in (self.filter_kind,self.filter_state):
            control.set_hexpand(True)
            control.connect('notify::selected',lambda *args:self.refresh_comments(force=True))
            filters.append(control)
        box.append(filters)
        self.filter_page=Gtk.CheckButton(label=_("comments_filter_current_page"))
        self.filter_page.connect('toggled',lambda button:self.refresh_comments(force=True))
        box.append(self.filter_page)
        scroll=Gtk.ScrolledWindow(vexpand=True,hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.rows=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        scroll.set_child(self.rows)
        box.append(scroll)
        self.sidebar.set_child(box)

    def editable(self):
        window=self.window
        return bool(window.doc and not window.view_mode and window._active_session.can_edit)

    def selection(self):
        window=self.window
        obj=window.selected_text or window.selected_image or window.selected_shape or window.selected_stroke
        if window.selected_table:
            return window.selected_table.bbox
        if obj:
            if obj is window.selected_text and getattr(window,'word_selection_mode',False):
                return text_geometry.selection_bounds(window.doc,obj,window.selected_word_start_char,window.selected_word_end_char)
            return obj.bbox
        if window.view_sel_rect:
            return tuple(fitz.Rect(window.view_sel_rect)*window.doc[window.current_page_index].derotation_matrix)
        return None

    def show_comments(self):
        if hasattr(self.window,'form_tools'):
            self.window.form_tools.sidebar.set_reveal_child(False)
        self.sidebar.set_reveal_child(not self.sidebar.get_reveal_child())
        self.refresh_comments()

    def refresh_comments(self,force=False):
        if not self.sidebar.get_reveal_child():
            return
        from .ops import annotations as annotation_ops
        doc=self.window.doc
        items=annotation_ops.thread(doc) if doc is not None else []
        query=self.filter_entry.get_text().strip().lower()
        kind=self.filter_kinds[self.filter_kind.get_selected()]
        state=self.filter_states[self.filter_state.get_selected()]
        page_only=self.filter_page.get_active()
        def visible(item):
            if kind!='all' and item['kind']!=kind:return False
            if state=='none' and item['state']:return False
            if state not in ('all','none') and item['state']!=state:return False
            if page_only and item['page']!=self.window.current_page_index:return False
            if query:
                haystack=' '.join([item['content'] or '',item['author'] or '']+
                                  [f"{r['content']} {r['author']}" for r in item['replies']]).lower()
                if query not in haystack:return False
            return True
        rows=[item for item in items if visible(item)]
        snapshot=(id(doc),self.editable(),repr(rows),self.window.current_page_index if page_only else None)
        if snapshot==getattr(self,'_comments_state',None) and not force:
            return
        self._comments_state=snapshot
        focus=self.window.get_focus()
        if focus and (focus is self.rows or focus.is_ancestor(self.rows)):
            self.window.set_focus(None)
        while child:=self.rows.get_first_child():
            self.rows.remove(child)
        self.add_review_button.set_sensitive(self.editable())
        if doc is None:
            return
        if not rows:
            self.rows.append(Gtk.Label(label=_("tool_no_comments") if not items else _("comments_no_match"),
                                       wrap=True,xalign=0))
        for info in rows:
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=5)
            header=Gtk.Box(spacing=6)
            title=Gtk.Label(label=f"{info['page']+1} · {info['kind']} · {info['author']}",xalign=0,wrap=True,hexpand=True)
            title.add_css_class('heading')
            header.append(title)
            if info['state']:
                badge=Gtk.Label(label=_(f"comments_state_{info['state']}"))
                badge.add_css_class('caption')
                badge.add_css_class({'Accepted':'success','Completed':'success','Rejected':'error'}.get(info['state'],'dim-label'))
                header.append(badge)
            box.append(header)
            box.append(Gtk.Label(label=info['content'] or '—',xalign=0,wrap=True,selectable=True))
            for reply in info['replies']:
                text=Gtk.Label(label=f"↳ {reply['author'] or '?'}: {reply['content']}",xalign=0,wrap=True,
                               selectable=True,margin_start=12)
                text.add_css_class('dim-label')
                box.append(text)
            controls=Gtk.Box(spacing=4)
            go=Gtk.Button(label=_("tool_go_to"))
            go.connect('clicked',lambda button,info=info:self.window._load_page(info['page']))
            controls.append(go)
            edit=Gtk.Button(icon_name='document-edit-symbolic',tooltip_text=_("mode_edit"))
            edit.set_sensitive(self.editable())
            edit.connect('clicked',lambda button,info=info:self.edit_comment(info))
            controls.append(edit)
            controls.append(self._reply_button(info))
            controls.append(self._state_button(info))
            properties=Gtk.Button(icon_name='preferences-system-symbolic',tooltip_text=_("comments_properties"))
            properties.set_sensitive(self.editable())
            properties.connect('clicked',lambda button,info=info:self._properties(info))
            controls.append(properties)
            remove=Gtk.Button(icon_name='user-trash-symbolic',tooltip_text=_("bookmark_remove"))
            remove.set_sensitive(self.editable())
            remove.connect('clicked',lambda button,info=info:self.window._mutate_document(
                lambda:tools.delete_annotation(doc,info['page'],info['xref']),page_num=info['page']))
            controls.append(remove)
            box.append(controls)
            box.append(Gtk.Separator())
            self.rows.append(box)

    def _review_author(self):
        from .i18n import get_setting
        import os
        return get_setting('review_author','') or os.environ.get('USER','')

    def _reply_button(self,info):
        from .ops import annotations as annotation_ops
        button=Gtk.MenuButton(icon_name='mail-reply-sender-symbolic',tooltip_text=_("comments_reply"))
        button.set_sensitive(self.editable())
        popover=Gtk.Popover()
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=6,margin_top=8,margin_bottom=8,
                    margin_start=8,margin_end=8)
        entry=Gtk.Entry(placeholder_text=_("comments_reply_placeholder"),width_chars=26)
        send=Gtk.Button(label=_("comments_reply"),halign=Gtk.Align.END)
        send.add_css_class('suggested-action')
        def submit(*args):
            text=entry.get_text().strip()
            if not text:return
            popover.popdown()
            doc=self.window.doc
            self.window._mutate_document(lambda:annotation_ops.add_reply(doc,info['page'],info['xref'],text,
                                                                          self._review_author()),page_num=info['page'])
            self.refresh_comments(force=True)
        entry.connect('activate',submit)
        send.connect('clicked',submit)
        box.append(entry);box.append(send)
        popover.set_child(box)
        button.set_popover(popover)
        return button

    def _state_button(self,info):
        from .ops import annotations as annotation_ops
        button=Gtk.MenuButton(icon_name='emblem-ok-symbolic',tooltip_text=_("comments_set_state"))
        button.set_sensitive(self.editable())
        popover=Gtk.Popover()
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2,margin_top=6,margin_bottom=6,
                    margin_start=6,margin_end=6)
        for state in annotation_ops.REVIEW_STATES:
            item=Gtk.Button(label=_(f"comments_state_{state}"))
            item.add_css_class('flat')
            def choose(button,state=state):
                popover.popdown()
                doc=self.window.doc
                self.window._mutate_document(lambda:annotation_ops.set_review_state(doc,info['page'],info['xref'],
                                                                                     state,self._review_author()),
                                             page_num=info['page'])
                self.refresh_comments(force=True)
            item.connect('clicked',choose)
            box.append(item)
        popover.set_child(box)
        button.set_popover(popover)
        return button

    def _properties(self,info):
        features=getattr(self.window,'features',None)
        if features is not None:
            from .features.review_ui import annotation_properties
            features._run(lambda controller:annotation_properties(controller,info))

    def close_note_bubble(self):
        bubble=getattr(self,'note_bubble',None)
        if bubble:
            focus=self.window.get_focus()
            if focus and (focus is bubble or focus.is_ancestor(bubble)):
                self.window.set_focus(None)
            bubble.popdown()

    def open_note_bubble(self,info):
        """Show a page-anchored comment without covering it with a modal window."""
        window=self.window
        source=window.doc
        number=info['page']
        if number!=window.current_page_index:
            window._load_page(number)
        self.close_note_bubble()
        bubble=getattr(self,'note_bubble',None)
        if bubble is None:
            bubble=Gtk.Popover(autohide=True,has_arrow=True,position=Gtk.PositionType.RIGHT)
            bubble.add_css_class('comment-bubble')
            bubble.set_parent(window.pdf_view)
            self.note_bubble=bubble
            self._bubble_css=Gtk.CssProvider()
            self._bubble_css.load_from_data(b"""
                .comment-bubble > contents { border-radius: 14px; padding: 0;
                    border: 1px solid alpha(@window_fg_color, .14);
                    box-shadow: 0 5px 18px alpha(black, .20); }
                .comment-bubble .comment-header { border-radius: 14px 14px 0 0;
                    background: alpha(@accent_bg_color, .09); padding: 12px; }
                .comment-bubble textview, .comment-bubble textview text { background: transparent; }
            """)
            Gtk.StyleContext.add_provider_for_display(window.get_display(),self._bubble_css,
                                                       Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        editable=self.editable()
        self._bubble_context=(source,number,editable)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        box.set_size_request(300,-1)
        header=Gtk.Box(spacing=10)
        header.add_css_class('comment-header')
        header.append(Gtk.Image(icon_name='user-available-symbolic',pixel_size=24))
        title=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3,hexpand=True)
        author=Gtk.Label(label=info['author'] or _('tool_comment'),xalign=0,ellipsize=3)
        author.add_css_class('heading')
        title.append(author)
        detail=Gtk.Label(label=f"{_('tool_note')} · {number+1}",xalign=0)
        detail.add_css_class('dim-label')
        title.append(detail)
        header.append(title)
        close=Gtk.Button(icon_name='window-close-symbolic',tooltip_text=_('btn_close'))
        close.add_css_class('flat')
        close.connect('clicked',lambda button:self.close_note_bubble())
        header.append(close)
        box.append(header)
        body=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10,
                     margin_start=14,margin_end=14,margin_bottom=14)
        text=Gtk.TextView(editable=editable,cursor_visible=editable,wrap_mode=Gtk.WrapMode.WORD_CHAR,
                          top_margin=4,bottom_margin=4)
        text.get_buffer().set_text(info['content'])
        scroll=Gtk.ScrolledWindow(min_content_height=100,max_content_height=240,
                                  propagate_natural_height=True,hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(text)
        body.append(scroll)
        if editable:
            entry=Gtk.Entry(text=info['author'],placeholder_text=_('tool_author'))
            entry.set_tooltip_text(_('tool_author'))
            body.append(entry)
            message=Gtk.Label(wrap=True,xalign=0)
            message.add_css_class('error')
            body.append(message)
            save=Gtk.Button(label=_('btn_save'),halign=Gtk.Align.END)
            save.add_css_class('suggested-action')
            def save_note(button):
                if source is not window.doc or number!=window.current_page_index or not self.editable():
                    self.close_note_bubble()
                    return
                buffer=text.get_buffer()
                content=buffer.get_text(buffer.get_start_iter(),buffer.get_end_iter(),True)
                if not content.strip():
                    message.set_text(_('tool_comment'))
                    return
                self.close_note_bubble()
                window._mutate_document(lambda:tools.edit_annotation(source,number,info['xref'],
                    content,entry.get_text()),page_num=number)
            save.connect('clicked',save_note)
            self.note_save_button=save
            body.append(save)
        box.append(body)
        bubble.set_child(box)
        rect=fitz.Rect(info['rect'])*source[number].rotation_matrix
        offset_x=max(0,(window.pdf_view.get_width()-window.current_pdf_page_width)/2)
        offset_y=max(0,(window.pdf_view.get_height()-window.current_pdf_page_height)/2)
        anchor=Gdk.Rectangle()
        anchor.x=int(rect.x0*window.zoom_level+offset_x)
        anchor.y=int(rect.y0*window.zoom_level+offset_y)
        anchor.width=max(1,int(rect.width*window.zoom_level))
        anchor.height=max(1,int(rect.height*window.zoom_level))
        bubble.set_pointing_to(anchor)
        bubble.popup()

    def edit_comment(self,info):
        if info['kind']=='Stamp' and self.editable():
            self.stamp(info)
            return
        if not self.editable():
            dialog=ToolDialog(self.window,_("tool_note"),None)
            if info['author']:
                dialog.section(info['author'])
            text=Gtk.TextView(editable=False,cursor_visible=False,wrap_mode=Gtk.WrapMode.WORD_CHAR,
                              top_margin=10,bottom_margin=10,left_margin=12,right_margin=12)
            text.get_buffer().set_text(info['content'])
            scroll=Gtk.ScrolledWindow(min_content_height=180,hscrollbar_policy=Gtk.PolicyType.NEVER)
            scroll.add_css_class('pdflx-textarea')
            scroll.set_child(text)
            dialog.box.insert_child_after(scroll,dialog.grid)
            dialog.present()
            return
        dialog=ToolDialog(self.window,_("tool_edit_comment"),lambda:self.window._mutate_document(
            lambda:tools.edit_annotation(self.window.doc,info['page'],info['xref'],
                                         content.get_text(content.get_start_iter(),content.get_end_iter(),True),author.get_text()),
            page_num=info['page']))
        content=dialog.multiline(_("tool_comment"),info['content'])
        author=dialog.entry(_("tool_author"),info['author'])
        dialog.present()

    def start_note(self):
        if self.editable():
            self.window.on_tool_selected(None,'sticky_note')
            self.window.status_label.set_text(_("tool_note_place_hint"))

    def place_note(self,x,y):
        source=self.window.doc
        number=self.window.current_page_index
        visible=source[number].rect
        if not (0<=x<visible.width and 0<=y<visible.height):
            return
        # Native note icons occupy 18 points; keep the whole icon inside the page.
        visual=fitz.Rect(min(x,max(0,visible.width-20)),min(y,max(0,visible.height-20)),
                         min(x,max(0,visible.width-20))+18,min(y,max(0,visible.height-20))+18)
        rect=visual*source[number].derotation_matrix
        def add():
            result=self.window._mutate_document(lambda:tools.add_review(source,number,'note',rect,
                content.get_text(content.get_start_iter(),content.get_end_iter(),True),author.get_text()),page_num=number)
            if result:
                self.window.on_tool_selected(None,'select')
                self.sidebar.set_reveal_child(True)
                self.refresh_comments()
            return result
        dialog=ToolDialog(self.window,_("tool_note"),add)
        content=dialog.multiline(_("tool_comment"))
        author=dialog.entry(_("tool_author"))
        dialog.present()

    def redaction(self):
        if not self.editable():
            return
        selection=self.selection()
        source=self.window.doc
        review=[]
        def targets():
            from .ops import redact_patterns
            start,end=first.get_value_as_int()-1,last.get_value_as_int()-1
            items=[]
            if query.get_text().strip() or selection is not None:
                try:
                    items=tools.redaction_targets(source,start,end,query.get_text(),selection,
                                                  regex.get_active(),case.get_active())
                except ValueError:
                    items=[]
            keys={key for key,check in patterns.items() if check.get_active()}
            found,review[:]=redact_patterns.targets(source,start,end,keys) if keys else ([],[])
            items=items+found
            if not items:
                raise ValueError(_("redact_nothing_found"))
            return items
        def options():
            rgba=fill_color.get_rgba()
            return dict(fill=None if transparent.get_active() else (rgba.red,rgba.green,rgba.blue),
                        replacement=replacement.get_text().strip(),
                        text_color=(0,0,0) if transparent.get_active() or rgba.red+rgba.green+rgba.blue>1.5 else (1,1,1),
                        image_mode=('pixels','remove','keep')[image_mode.get_selected()])
        def preview(scratch):
            items=targets()
            settings=options()
            for number,rect in items:
                scratch[number].add_redact_annot(rect,text=settings['replacement'] or None,
                    fill=settings['fill'] if settings['fill'] is not None else False,
                    text_color=settings['text_color'],cross_out=True)
            summary=_("tool_redaction_count",len(items))
            if review:
                names={key:_(f'redact_pattern_{key}') for key in patterns}
                lines=[f'p.{number+1} · {names[key]}: {text}' for number,key,text in review[:8]]
                more=_("redact_review_more",len(review)-8) if len(review)>8 else ''
                summary+='\n'+'\n'.join(lines)+('\n'+more if more else '')
            dialog.message.set_text(summary)
            return items[0][0]
        def apply():
            items=targets()
            settings=options()
            pages=sorted({page for page,_ in items})
            return self.window._mutate_document(lambda:tools.apply_redactions(self.window,items,**settings),
                                                rebase_pages=pages)
        dialog=ToolDialog(self.window,_("tool_redact"),apply,preview,destructive=True)
        dialog.section(_("print_group_pages"))
        first,last=dialog.range()
        dialog.section(_("redact_section_search"))
        query=dialog.entry(_("tool_redact_search"))
        regex=dialog.check(_("search_regex"),False)
        case=dialog.check(_("search_case"),False)
        dialog.section(_("redact_section_patterns"))
        from .ops.redact_patterns import ORDER
        patterns={key:dialog.check(_(f'redact_pattern_{key}'),False) for key in ORDER}
        dialog.section(_("props_appearance"))
        replacement=dialog.entry(_("redact_replacement"),'')
        replacement.set_placeholder_text('[REDACTED]')
        fill_color=Gtk.ColorButton()
        black=Gdk.RGBA();black.parse('#000000');fill_color.set_rgba(black)
        dialog.field(_("redact_fill"),fill_color)
        transparent=dialog.check(_("redact_no_fill"),False)
        image_mode=dialog.dropdown(_("redact_images"),[_("redact_images_pixels"),_("redact_images_remove"),
                                                       _("redact_images_keep")])
        if selection is not None:
            def selection_range(entry):
                searching=bool(entry.get_text().strip())
                first.set_sensitive(searching)
                last.set_sensitive(searching)
                if not searching:
                    first.set_value(self.window.current_page_index+1)
                    last.set_value(self.window.current_page_index+1)
            query.connect('changed',selection_range)
            selection_range(query)
        dialog.message.set_text(_("tool_redaction_hint"))
        dialog.present()

    def stamp(self,info=None):
        if not self.editable():
            return
        if info:
            self.window.stamp_interaction.cancel()
        from .stamp_dialog import StampDialog
        dialog=StampDialog(self,info)
        dialog.present(self.window)
        return dialog

    def place_stamp(self,x,y):
        if not self.editable():
            return
        settings=getattr(self,'stamp_settings',None)
        if not settings or settings[0] is not self.window.doc:
            self.stamp()
            return
        source,stamp,width,author,style=settings
        number=self.window.current_page_index
        if not fitz.Point(x,y) in source[number].rect:
            return
        self.window._mutate_document(lambda:tools.place_stamp(source,number,(x,y),stamp,width,author,style),page_num=number)
        self.window.status_label.set_text(_("tool_stamp_place"))

    def review(self):
        if not self.editable():
            return
        window=self.window
        source=window.doc
        page_number=window.current_page_index
        selection=self.selection()
        kinds=('note','underline','strikeout','squiggle','highlight','stamp')
        stamps=('Approved','Draft','Confidential','Final','Not approved')
        def bounds():
            if selection:
                return selection
            visual=fitz.Rect(x.get_value(),y.get_value(),x.get_value()+width.get_value(),y.get_value()+height.get_value())
            return tuple(visual*source[page_number].derotation_matrix)
        def add(doc):
            selected_kind=kinds[kind.get_selected()]
            quads=None
            if selected_kind in ('underline','strikeout','squiggle','highlight') and window.selected_text:
                start=window.selected_word_start_char if getattr(window,'word_selection_mode',False) else 0
                end=window.selected_word_end_char if getattr(window,'word_selection_mode',False) else len(window.selected_text.text)
                quads=text_geometry.selection_quads(source,window.selected_text,start,end)
            tools.add_review(doc,page_number,selected_kind,bounds(),
                             comment.get_text(comment.get_start_iter(),comment.get_end_iter(),True),
                             author.get_text(),quads,stamps[stamp.get_selected()])
        dialog=ToolDialog(window,_("tool_add_review"),lambda:window._mutate_document(lambda:add(source),page_num=page_number),
                          lambda scratch:(add(scratch),page_number)[1])
        kind=dialog.dropdown(_("tool_review_type"),[_(key) for key in ('tool_note','tool_underline','tool_strikeout','tool_squiggle','menu_highlight','tool_stamp')])
        comment=dialog.multiline(_("tool_comment"))
        author=dialog.entry(_("tool_author"))
        stamp=dialog.dropdown(_("tool_stamp"),list(stamps))
        visible=source[page_number].rect
        x=dialog.spin('X (pt)',min(48,visible.width/4),0,visible.width)
        y=dialog.spin('Y (pt)',min(48,visible.height/4),0,visible.height)
        width=dialog.spin(_("tool_width"),min(180,visible.width/2),10,visible.width)
        height=dialog.spin(_("tool_height"),32,10,visible.height)
        for control in (x,y,width,height):
            control.set_sensitive(selection is None)
        dialog.message.set_text(_("tool_review_hint"))
        dialog.present()

    def decorations(self):
        if not self.editable():
            return
        source=self.window.doc
        self.logo=None
        def options():
            return dict(first=first.get_value_as_int()-1,last=last.get_value_as_int()-1,
                        text=text.get_text(),font_size=size.get_value(),opacity=opacity.get_value()/100,
                        position=('center','top','bottom')[position.get_selected()],numbering=numbering.get_active(),
                        start_number=start.get_value_as_int(),template=template.get_text(),logo=self.logo)
        def preview(scratch):
            values=options()
            tools.decorate_pages(scratch,**values)
            return values['first']
        def apply():
            values=options()
            pages=tuple(tools.page_range(source,values['first'],values['last']))
            return self.window._mutate_document(lambda:tools.decorate_pages(source,**values),rebase_pages=pages)
        dialog=ToolDialog(self.window,_("tool_decorate"),apply,preview)
        first,last=dialog.range()
        text=dialog.entry(_("tool_watermark"),'')
        size=dialog.spin(_("tool_font_size"),36,6,144)
        opacity=dialog.spin(_("tool_opacity"),15,0,100)
        position=dialog.dropdown(_("tool_position"),[_("tool_center"),_("tool_top"),_("tool_bottom")])
        logo=Gtk.Button(label=_("tool_choose_logo"))
        def choose(button):
            image_filter=Gtk.FileFilter(name='PNG / JPEG')
            image_filter.add_mime_type('image/png')
            image_filter.add_mime_type('image/jpeg')
            def chosen(file):
                if file:
                    from pathlib import Path
                    data=Path(file.get_path()).read_bytes()
                    fitz.Pixmap(data)
                    self.logo=data
                    logo.set_label(file.get_basename())
            show_open_file_dialog(self.window,_("tool_choose_logo"),filters=[image_filter],callback=chosen)
        logo.connect('clicked',choose)
        dialog.field(_("tool_logo"),logo)
        numbering=dialog.check(_("tool_number_pages"),False)
        start=dialog.spin(_("tool_start_number"),1,1,999999)
        template=dialog.entry(_("tool_number_format"),'{page} / {total}')
        dialog.message.set_text(_("tool_number_hint"))
        dialog.present()

    def crop(self):
        if not self.editable():
            return
        source=self.window.doc
        def options():
            return first.get_value_as_int()-1,last.get_value_as_int()-1,tuple(control.get_value() for control in margins),reset.get_active()
        def preview(scratch):
            a,b,values,restore=options()
            tools.crop_pages(scratch,a,b,values,restore)
            return a
        def apply():
            a,b,values,restore=options()
            pages=tuple(tools.page_range(source,a,b))
            def mutate():
                old={number:source[number].cropbox for number in pages}
                tools.crop_pages(source,a,b,values,restore)
                for number in pages:
                    crop=source[number].cropbox
                    dx,dy=crop.x0-old[number].x0,crop.y0-old[number].y0
                    for group in self.window._active_session.page_objects.get(number,()):
                        for obj in group:
                            if not (getattr(obj,'is_new',False) or getattr(obj,'_ghost_redacted',False)):
                                continue
                            x0,y0,x1,y1=obj.bbox
                            obj.bbox=(x0-dx,y0-dy,x1-dx,y1-dy)
                            obj.original_bbox=obj.bbox
                            obj.x,obj.y=obj.bbox[:2]
                            if hasattr(obj,'baseline'):
                                obj.baseline-=dy
                            if hasattr(obj,'points'):
                                obj.points=[(x-dx,y-dy) for x,y in obj.points]
            return self.window._mutate_document(mutate,rebase_pages=pages)
        dialog=ToolDialog(self.window,_("tool_crop"),apply,preview)
        first,last=dialog.range()
        margins=[dialog.spin(_(key),0,0,2000) for key in ('tool_left','tool_top','tool_right','tool_bottom')]
        reset=dialog.check(_("tool_reset_crop"))
        dialog.message.set_text(_("tool_crop_hint"))
        dialog.present()

    def create_field(self):
        return self.window.form_tools.create_field()

    def flatten(self):
        if not self.editable():
            return
        if not self.window.form_tools.save_values():return
        source=self.window.doc
        pages=tuple(range(source.page_count))
        dialog=ToolDialog(self.window,_("tool_flatten"),lambda:self.window._mutate_document(
            lambda:tools.flatten_forms(source),rebase_pages=pages),
            lambda scratch:(tools.flatten_forms(scratch),self.window.current_page_index)[1])
        dialog.message.set_text(_("tool_flatten_hint"))
        dialog.present()
