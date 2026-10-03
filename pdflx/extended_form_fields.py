"""Radio groups and standard PDF reset buttons, including older MuPDF engines."""
import re
import uuid
import pymupdf as fitz


def create_radio_group(doc,page_number,name,rect,choices,required=False,options=None,value=None):
    from .form_tree import ensure_form_fields
    ensure_form_fields(doc)
    from .form_properties import normalize_choices,choice_options,apply_options
    pairs=choice_options(normalize_choices(choices))
    if value not in (None,'') and value not in [export for export,label in pairs]:
        raise ValueError('Choose a listed initial value.')
    rect=fitz.Rect(rect)
    row_height=rect.height/len(pairs)
    if row_height<16 or rect.width<50:
        raise ValueError('Draw a larger area: allow at least 16 pt per radio option and 50 pt width.')
    page=doc[page_number]
    refs=[]
    states=[]
    for index,(export,label) in enumerate(pairs):
        widget=fitz.Widget();widget.field_type=fitz.PDF_WIDGET_TYPE_RADIOBUTTON
        # Distinct temporary names avoid version-dependent automatic grouping.
        widget.field_name=f'pdfLXRadio_{uuid.uuid4().hex}'
        widget.rect=fitz.Rect(rect.x0,rect.y0+index*row_height,rect.x1,rect.y0+(index+1)*row_height)
        widget.field_value='Off';widget.text_color=(0,0,0);widget.border_color=(.45,.45,.45)
        widget.border_width=1;widget.fill_color=(1,1,1)
        apply_options(widget,options)
        annot=page.add_widget(widget);xref=annot.xref;refs.append(xref)
        # Native appearance streams include the option label inside the widget.
        appearances={}
        for selected in (False,True):
            with fitz.open() as appearance:
                ap=appearance.new_page(width=rect.width,height=row_height)
                radius=min(6,row_height/3);centre=fitz.Point(radius+3,row_height/2)
                ap.draw_circle(centre,radius,color=widget.border_color or (.45,.45,.45),
                               fill=widget.fill_color,width=widget.border_width)
                if selected:ap.draw_circle(centre,radius*.5,color=None,fill=widget.text_color)
                font=widget.text_font if widget.text_font in ('Helv','HeBo','TiRo','Cour') else 'Helv'
                size=min(widget.text_fontsize or 11,row_height*.65)
                ap.insert_textbox((2*radius+8,0,rect.width,row_height),label,fontsize=size,
                                  fontname=font,color=widget.text_color)
                # Use a Form XObject imported through MuPDF, preserving fonts/resources.
                holder=doc.new_page(width=rect.width,height=row_height)
                form=holder.show_pdf_page(holder.rect,appearance,0)
                stream=doc.get_new_xref()
                doc.update_object(stream,f'<< /Type /XObject /Subtype /Form /BBox [0 0 {rect.width} {row_height}] /Resources << /XObject << /Content {form} 0 R >> >> >>')
                doc.update_stream(stream,b'/Content Do')
                doc.delete_page(doc.page_count-1)
                page=doc[page_number]
                appearances[selected]=stream
        state='/' + ''.join('#%02X'%byte for byte in export.encode('utf-8'))
        states.append(state)
        doc.xref_set_key(xref,'AP',f'<< /N << /Off {appearances[False]} 0 R {state} {appearances[True]} 0 R >> >>')
    flags=fitz.PDF_BTN_FIELD_IS_RADIO | (fitz.PDF_FIELD_IS_REQUIRED if required else 0)
    if options and options.get('readonly'):flags|=fitz.PDF_FIELD_IS_READ_ONLY
    parent=doc.get_new_xref()
    selected=states[[p[0] for p in pairs].index(value)] if value in [p[0] for p in pairs] else '/Off'
    doc.update_object(parent,f'<< /FT /Btn /Ff {flags} /T {fitz.get_pdf_str(name)} /Kids [{' '.join(f'{x} 0 R' for x in refs)}] /V {selected} /DV {selected} >>')
    for xref,state in zip(refs,states):
        doc.xref_set_key(xref,'Parent',f'{parent} 0 R')
        for key in ('T','V','Ff'):doc.xref_set_key(xref,key,'null')
        doc.xref_set_key(xref,'AS',state if state==selected else '/Off')
    from .form_tree import update_form_roots
    update_form_roots(doc,add=[parent],remove=refs)
    doc._reset_page_refs()
    return refs[0]


def select_radio(doc,widget,value):
    state='/' + ''.join('#%02X'%byte for byte in str(value).encode('utf-8'))
    kind,parent=doc.xref_get_key(widget.xref,'Parent')
    if kind=='xref':
        parent=int(parent.split()[0])
        _,kids=doc.xref_get_key(parent,'Kids')
        refs=[int(n) for n in re.findall(r'(\d+)\s+0\s+R',kids)]
        for ref in refs:doc.xref_set_key(ref,'AS',state if ref==widget.xref else '/Off')
        doc.xref_set_key(parent,'V',state)
    else:
        doc.xref_set_key(widget.xref,'AS',state)
        doc.xref_set_key(widget.xref,'V',state)


def reset_form_fields(doc):
    """Restore native defaults; skip read-only and signature widgets."""
    from .form_tree import ensure_form_fields
    ensure_form_fields(doc)
    for number in range(doc.page_count):
        page=doc[number]
        for widget in page.widgets() or ():
            if widget.field_flags&fitz.PDF_FIELD_IS_READ_ONLY or widget.field_type in (1,6):continue
            if widget.field_type==fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
                parent_type,parent=doc.xref_get_key(widget.xref,'Parent')
                target=int(parent.split()[0]) if parent_type=='xref' else widget.xref
                default_type,default=doc.xref_get_key(target,'DV')
                if default_type!='name':default='/Off'
                selected=default[1:]
                state='/' + ''.join('#%02X'%byte for byte in selected.encode('utf-8'))
                doc.xref_set_key(target,'V',state)
                from .form_properties import decode_button_state
                doc.xref_set_key(widget.xref,'AS',state if decode_button_state(widget.on_state())==selected else '/Off')
                continue
            widget.reset()
            # reset() changes the PDF, not the Python wrapper's cached value.
            fresh=page.load_widget(widget.xref)
            if fresh.field_type in (3,4):fresh.choice_values=None
            fresh.update()
    doc._reset_page_refs()
