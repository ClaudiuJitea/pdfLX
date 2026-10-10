"""Native PDF redaction, review, page decoration, cropping, and forms."""
import math
import pymupdf as fitz


def page_range(doc, first, last):
    if not 0 <= first <= last < doc.page_count:
        raise ValueError('Choose a valid page range.')
    return range(first, last+1)


IMAGE_MODES = {'pixels': fitz.PDF_REDACT_IMAGE_PIXELS, 'remove': fitz.PDF_REDACT_IMAGE_REMOVE,
               'keep': fitz.PDF_REDACT_IMAGE_NONE}


def redaction_targets(doc, first, last, query='', selection=None, regex=False, case_sensitive=False):
    targets = []
    for number in page_range(doc, first, last):
        if query.strip() and regex:
            from .ops.text import search_page
            for _match, quads in search_page(doc[number], query.strip(), regex=True, case_sensitive=case_sensitive):
                targets.extend((number, tuple(quad.rect)) for quad in quads)
        elif query.strip():
            targets.extend((number, tuple(quad.rect)) for quad in doc[number].search_for(query.strip(), quads=True))
        elif selection is not None and number == first:
            rect = fitz.Rect(selection)
            if not rect.is_empty and rect.is_valid:
                targets.append((number, tuple(rect)))
    if not targets:
        raise ValueError('Select an area or enter text that occurs in the selected pages.')
    return targets


def apply_redactions(window, targets, fill=(0,0,0), replacement='', text_color=(1,1,1), font_size=0,
                     image_mode='remove'):
    """Redact baseline content and intersecting managed objects, preserving survivors.

    ``fill`` None leaves the area transparent; ``replacement`` is overlay text
    (e.g. "[REDACTED]"), sized to fit when ``font_size`` is 0. ``image_mode``:
    'pixels' blanks only covered image pixels, 'remove' deletes every image
    placement that overlaps, 'keep' leaves images untouched (not secure).
    """
    from . import pdf_handler
    images = IMAGE_MODES.get(image_mode)
    if images is None:
        raise ValueError('Unknown image redaction mode.')
    for number in sorted({number for number, _ in targets}):
        rects = [fitz.Rect(rect) for page, rect in targets if page == number]
        page = window.doc[number]
        for rect in rects:
            size = font_size or max(4, min(11, rect.height * 0.7))
            page.add_redact_annot(rect, text=replacement or None, fontname='helv', fontsize=size,
                                  fill=fill if fill is not None else False, text_color=text_color,
                                  cross_out=False)
        page.apply_redactions(images=images, graphics=2, text=0)
        window.doc._reset_page_refs()
        groups = window._active_session.page_objects.get(number)
        if groups:
            for group in groups:
                for obj in list(group):
                    managed = getattr(obj,'is_new',False) or getattr(obj,'_ghost_redacted',False)
                    if not managed or not any(rect.intersects(fitz.Rect(obj.bbox)) for rect in rects):
                        continue
                    with fitz.open() as scratch:
                        source = window.doc[number]
                        single = scratch.new_page(width=source.mediabox.width,height=source.mediabox.height)
                        success, error = pdf_handler._apply_single_object_to_page(scratch,single,obj)
                        if not success:
                            raise ValueError(error)
                        for rect in rects:
                            single.add_redact_annot(rect,fill=False,cross_out=False)
                        single.apply_redactions(images=fitz.PDF_REDACT_IMAGE_REMOVE,graphics=2,text=0)
                        extractors = (pdf_handler.extract_editable_text,pdf_handler.extract_editable_shapes,
                                      pdf_handler.extract_editable_images,pdf_handler.extract_editable_strokes)
                        # Text/image fragments may survive; fully intersected vector borders are removed.
                        index = groups.index(group)
                        survivors,error = extractors[index](scratch,0)
                        if error:
                            raise ValueError(error)
                        for survivor in survivors:
                            survivor.page_number=number
                            survivor.is_new=True
                            survivor.is_baked=True
                            if hasattr(obj,'table_id'):
                                survivor.table_id=obj.table_id
                        group.remove(obj)
                        group.extend(survivors)
        # The obsolete editor stream may retain redacted data: drop it before save.
        window.doc.xref_set_key(window.doc[number].xref,'PdfLXEditor','null')


def set_note_appearance(doc, annot):
    """Use a flat vector comment bubble while retaining a native Text annotation."""
    xref = doc.get_new_xref()
    doc.update_object(xref, '<< /Type /XObject /Subtype /Form /BBox [0 0 18 18] /Resources << >> >>')
    doc.update_stream(xref, b'''q
1 0.95 0.68 rg 0.64 0.44 0.06 RG 1.1 w 1 j 1 J
4.5 16.5 m 13.5 16.5 l
15.2 16.5 16.5 15.2 16.5 13.5 c
16.5 7.5 l 16.5 5.8 15.2 4.5 13.5 4.5 c
9 4.5 l 5 1.5 l 5 4.5 l 4.5 4.5 l
2.8 4.5 1.5 5.8 1.5 7.5 c
1.5 13.5 l 1.5 15.2 2.8 16.5 4.5 16.5 c h B
0.64 0.44 0.06 RG 1.1 w
5 12.5 m 13 12.5 l S
5 10 m 13 10 l S
5 7.5 m 10.5 7.5 l S
Q
''')
    doc.xref_set_key(annot.xref, 'AP', f'<< /N {xref} 0 R >>')
    doc.xref_set_key(annot.xref, 'Name', '/Comment')


def add_review(doc, page_number, kind, rect, content='', author='', quads=None, stamp='Approved'):
    page = doc[page_number]
    rect = fitz.Rect(rect)
    if rect.is_empty or not rect.is_valid or rect not in (page.rect*page.derotation_matrix):
        raise ValueError('Select an area inside the visible page.')
    if kind == 'note':
        if not content.strip():
            raise ValueError('Enter a comment.')
        annot = page.add_text_annot(rect.tl,content.strip(),icon="Comment")
        annot.set_colors(stroke=(1,0.82,0.2))
    elif kind == 'stamp':
        stamps={'Approved':fitz.STAMP_Approved,'Draft':fitz.STAMP_Draft,'Confidential':fitz.STAMP_Confidential,
                'Final':fitz.STAMP_Final,'Not approved':fitz.STAMP_NotApproved}
        annot = page.add_stamp_annot(rect,stamp=stamps[stamp])
    else:
        functions={'underline':page.add_underline_annot,'strikeout':page.add_strikeout_annot,
                   'squiggle':page.add_squiggly_annot,'highlight':page.add_highlight_annot}
        annot = functions[kind](quads or [rect.quad])
    annot.set_info(content=content.strip(),title=author.strip())
    annot.update()
    if kind == 'note':
        set_note_appearance(doc, annot)
    elif kind == 'highlight':
        from .highlight_tools import style
        style(doc, annot)
    xref = annot.xref
    doc._reset_page_refs()
    return xref


def annotations(doc):
    rows=[]
    for number in range(doc.page_count):
        page=doc[number]
        for annot in page.annots() or ():
            rows.append({'page':number,'xref':annot.xref,'kind':annot.type[1],
                         'content':annot.info.get('content',''),'author':annot.info.get('title',''),
                         'rect':tuple(annot.rect)})
    return rows


def note_at_point(doc, page_number, point, tolerance=4, include_stamps=False):
    """Hit-test native sticky-note icons in unrotated page coordinates."""
    page=doc[page_number]
    for annot in page.annots() or ():
        if annot.type[0] not in ((fitz.PDF_ANNOT_TEXT,fitz.PDF_ANNOT_STAMP) if include_stamps else (fitz.PDF_ANNOT_TEXT,)):
            continue
        # Replies and hidden annotations are not clickable page icons.
        if annot.irt_xref or annot.flags&fitz.PDF_ANNOT_IS_HIDDEN:
            continue
        rect=annot.rect+(-tolerance,-tolerance,tolerance,tolerance)
        if fitz.Point(point) in rect:
            return {'page':page_number,'xref':annot.xref,'kind':annot.type[1],
                    'content':annot.info.get('content',''),'author':annot.info.get('title',''),
                    'rect':tuple(annot.rect)}
    return None


NATIVE_STAMPS={'Approved':fitz.STAMP_Approved,'Draft':fitz.STAMP_Draft,
               'Confidential':fitz.STAMP_Confidential,'Final':fitz.STAMP_Final,
               'Not approved':fitz.STAMP_NotApproved,'For comment':fitz.STAMP_ForComment,
               'Experimental':fitz.STAMP_Experimental,'Expired':fitz.STAMP_Expired,
               'As is':fitz.STAMP_AsIs,'Departmental':fitz.STAMP_Departmental,
               'Sold':fitz.STAMP_Sold,'Top secret':fitz.STAMP_TopSecret}


def expand_stamp_fields(text,author=''):
    """Replace {date}, {time}, {datetime}, {author} and {user} in stamp text."""
    import getpass
    import time
    if '{' not in text:
        return text
    now=time.localtime()
    try:
        user=getpass.getuser()
    except Exception:
        user=''
    values={'date':time.strftime('%d %b %Y',now),'time':time.strftime('%H:%M',now),
            'datetime':time.strftime('%d %b %Y %H:%M',now),'author':author.strip() or user,'user':user}
    for key,value in values.items():
        text=text.replace('{'+key+'}',value)
    return text


STAMP_TEXT_LIMIT=40
STAMP_DETAILS_LIMIT=48
STAMP_LINE_SPACING=1.05


def stamp_lines(font,text,box,limit):
    """Lay stamp text out on one or two lines, whichever allows larger type.

    Returns the lines and the font size that fits them in ``box``.
    """
    height=max(font.ascender-font.descender,1e-6)
    def size_for(lines):
        widest=max(font.text_length(line,fontsize=1) for line in lines)
        return min(limit,box.width/max(widest,1e-6),
                   box.height/(height*(1 if len(lines)==1 else 2*STAMP_LINE_SPACING)))
    best=[text],size_for([text])
    words=text.split()
    for cut in range(1,len(words)):
        lines=[' '.join(words[:cut]),' '.join(words[cut:])]
        size=size_for(lines)
        # Prefer one line unless wrapping gives clearly larger type.
        if size>best[1]*(1.15 if len(best[0])==1 else 1):
            best=lines,size
    return best


def place_stamp(doc,page_number,point,stamp='Approved',width=160,author='',style=None):
    """Place an upright native stamp at a visible-page position.

    Without ``style`` the stamp must be one of the standard PDF stamps. With a
    style, ``stamp`` names the template and any name is accepted.
    """
    presets=NATIVE_STAMPS
    if (stamp not in presets and style is None) or not math.isfinite(width) or width<24:
        raise ValueError('Choose a valid stamp and a width of at least 24 pt.')
    page=doc[page_number]
    visible=page.rect
    x,y=point
    if not (0<=x<visible.width and 0<=y<visible.height):
        raise ValueError('Click inside the visible page.')
    details=''
    if style is not None:
        from . import stamp_shapes
        style=dict(style)
        shape=style.get('shape','rectangle')
        label=expand_stamp_fields(str(style.get('text',stamp.upper())).strip(),author)
        details=expand_stamp_fields(str(style.get('details','') or '').strip(),author)
        # Store the expanded text so later edits keep the original date.
        style['text'],style['details']=label,details
        color=tuple(style.get('color',(0.8,0,0)))
        fontsize=float(style.get('font_size',24))
        opacity=float(style.get('opacity',1))
        border=style.get('border','Solid')
        angle=float(style.get('angle',0))
        if (not label or len(color)!=3 or any(not math.isfinite(c) or not 0<=c<=1 for c in color)
                or not math.isfinite(fontsize) or not 6<=fontsize<=96
                or not math.isfinite(opacity) or not 0.05<=opacity<=1 or border not in stamp_shapes.BORDERS
                or shape not in stamp_shapes.SHAPES or not math.isfinite(angle)):
            raise ValueError('Enter stamp text and valid style settings.')
    width=min(width,visible.width,visible.height*3.8)
    height=min(visible.height,max(width/3.8,fontsize*1.5+12) if style is not None else width/3.8)
    if style is not None:
        width,height=stamp_shapes.dimensions(shape,width,fontsize,visible.height,bool(details))
    x=min(x,visible.width-width)
    y=min(y,visible.height-height)
    center=fitz.Point(x+width/2,y+height/2)*page.derotation_matrix
    rect=fitz.Rect(center.x-width/2,center.y-height/2,center.x+width/2,center.y+height/2)
    annot=page.add_stamp_annot(rect,stamp=presets.get(stamp,fitz.STAMP_Approved))
    # Annotation appearance matrices use PDF coordinates (Y points upward),
    # so matching the page's clockwise rotation keeps the displayed text upright.
    annot.set_rotation(page.rotation)
    annot.set_info(content=stamp,title=author.strip())
    if style is not None:
        annot.set_info(content=label,title=author.strip())
        annot.set_opacity(opacity)
    annot.update()
    if style is not None:
        annot.set_rect(fitz.Rect(x,y,x+width,y+height)*page.derotation_matrix)
    xref=annot.xref
    if style is not None:
        _,original_ap=doc.xref_get_key(xref,'AP/N')
        _,appearance_matrix=doc.xref_get_key(int(original_ap.split()[0]),'Matrix')
        from .models import EditableText
        from .pdf_handler import _get_font_args_for_pymupdf
        text_model=EditableText(0,0,label,font_family=style.get('font_family','DejaVu Sans'))
        text_model.is_bold=bool(style.get('bold',True))
        text_model.is_italic=bool(style.get('italic',False))
        font_args,error=_get_font_args_for_pymupdf(text_model)
        if error:
            raise ValueError(error)
        font=fitz.Font(**font_args)
        detail_model=EditableText(0,0,details or 'x',font_family=style.get('font_family','DejaVu Sans'))
        detail_model.is_italic=text_model.is_italic
        detail_args,error=_get_font_args_for_pymupdf(detail_model)
        if error:
            raise ValueError(error)
        detail_font=fitz.Font(**detail_args)
        region=stamp_shapes.text_region(shape,width,height)
        if border=='Double' and shape in ('rectangle','rounded'):
            pad=min(5,min(width,height)*0.08)
            region=fitz.Rect(region.x0+pad,region.y0+pad,region.x1-pad,region.y1-pad)
        text_box,detail_box=stamp_shapes.split_region(region,bool(details))

        def fit(face,text,box,limit):
            return min(limit,box.width/max(face.text_length(text,fontsize=1),1e-6),
                       box.height/max(face.ascender-face.descender,1))
        lines,size=stamp_lines(font,label,text_box,fontsize)
        if size<3:
            raise ValueError('The stamp text is too long for this width.')
        with fitz.open() as layer:
            canvas=layer.new_page(width=width,height=height)
            stamp_shapes.draw_border(canvas,shape,width,height,color,border,bool(style.get('fill',False)))

            def write(face,args,text,box,text_size):
                text_width=face.text_length(text,fontsize=text_size)
                baseline=(box.y0+box.y1+text_size*(face.ascender+face.descender))/2
                canvas.insert_text(((box.x0+box.x1-text_width)/2,baseline),text,fontsize=text_size,color=color,**args)
            if len(lines)==1:
                write(font,font_args,label,text_box,size)
            else:
                # Two lines share the box, centred around its middle.
                step=size*STAMP_LINE_SPACING*(font.ascender-font.descender)
                middle=(text_box.y0+text_box.y1)/2
                for index,line in enumerate(lines):
                    centre=middle+(index-0.5)*step
                    write(font,font_args,line,fitz.Rect(text_box.x0,centre-step/2,text_box.x1,centre+step/2),size)
            if details:
                detail_size=fit(detail_font,details,detail_box,max(size*0.45,5))
                if detail_size<2.5:
                    raise ValueError('The details line is too long for this width.')
                write(detail_font,detail_args,details,detail_box,detail_size)
            # Import the appearance's font resources, then remove its temporary
            # page. The annotation retains the imported resources on saving.
            temporary=doc.page_count
            doc.insert_pdf(layer)
            imported=doc[temporary]
            _,resources=doc.xref_get_key(imported.xref,'Resources')
            appearance=b'\n'.join(doc.xref_stream(ref) for ref in imported.get_contents())
            doc.delete_page(temporary)
        ap=doc.get_new_xref()
        if opacity<1:
            # Viewers paint appearance streams as they are and ignore the
            # annotation's /CA, so the transparency lives in the appearance.
            body=doc.get_new_xref()
            doc.update_object(body,f'<< /Type /XObject /Subtype /Form /BBox [0 0 {width} {height}] /Resources {resources} >>')
            doc.update_stream(body,appearance)
            doc.update_object(ap,f'<< /Type /XObject /Subtype /Form /BBox [0 0 {width} {height}] /Matrix {appearance_matrix} '
                                 f'/Resources << /ExtGState << /Fade << /CA {opacity:g} /ca {opacity:g} >> >> '
                                 f'/XObject << /Body {body} 0 R >> >> >>')
            doc.update_stream(ap,b'q /Fade gs /Body Do Q')
        else:
            doc.update_object(ap,f'<< /Type /XObject /Subtype /Form /BBox [0 0 {width} {height}] /Matrix {appearance_matrix} /Resources {resources} >>')
            doc.update_stream(ap,appearance)
        doc.xref_set_key(xref,'AP',f'<< /N {ap} 0 R >>')
        import json
        doc.xref_set_key(xref,'PdfLXStampStyle',fitz.get_pdf_str(json.dumps(dict(style,stamp=stamp))))
    doc._reset_page_refs()
    if style is not None and angle%360:
        from .stamp_rotation import rotate_stamp
        rotate_stamp(doc,page_number,xref,angle)
    return xref


def move_stamp(doc,page_number,xref,visual_rect):
    """Move a native stamp while retaining its appearance and style metadata."""
    _transform_stamp(doc,page_number,xref,visual_rect,preserve_size=True)


def resize_stamp(doc,page_number,xref,visual_rect):
    """Resize a native stamp by scaling its existing vector appearance."""
    _transform_stamp(doc,page_number,xref,visual_rect,preserve_size=False)


def _transform_stamp(doc,page_number,xref,visual_rect,preserve_size):
    page=doc[page_number]
    target=fitz.Rect(visual_rect)
    if not target.is_valid or target.is_empty or target not in page.rect:
        raise ValueError('The stamp must fit inside the visible page.')
    annot=page.load_annot(xref)
    movable=(fitz.PDF_ANNOT_STAMP,fitz.PDF_ANNOT_TEXT) if preserve_size else (fitz.PDF_ANNOT_STAMP,)
    if annot.type[0] not in movable:
        raise ValueError('Select a stamp or note to move.')
    original=annot.rect*page.rotation_matrix
    if preserve_size and (abs(target.width-original.width)>0.01 or abs(target.height-original.height)>0.01):
        raise ValueError('Moving a stamp must preserve its size.')
    # Calling update() regenerates custom appearances; set_rect preserves them.
    annot.set_rect(target*page.derotation_matrix)
    doc._reset_page_refs()


def edit_annotation(doc,page_number,xref,content,author):
    page=doc[page_number]
    annot=page.load_annot(xref)
    annot.set_info(content=content.strip(),title=author.strip())
    annot.update()
    if annot.type[0] == fitz.PDF_ANNOT_TEXT:
        set_note_appearance(doc, annot)
    doc._reset_page_refs()


def delete_annotation(doc,page_number,xref):
    page=doc[page_number]
    page.delete_annot(page.load_annot(xref))
    doc._reset_page_refs()


def decorate_pages(doc,first,last,text='',font_size=36,opacity=0.15,position='center',
                   numbering=False,start_number=1,template='{page} / {total}',logo=None):
    if not math.isfinite(font_size) or not 6 <= font_size <= 144 or not 0 <= opacity <= 1:
        raise ValueError('Choose a font size from 6 to 144 pt and valid opacity.')
    if not text.strip() and not logo and not numbering:
        raise ValueError('Enter watermark text, choose a logo, or enable page numbering.')
    pages=page_range(doc,first,last)
    for number in pages:
        page=doc[number]
        visible=page.rect
        # Render decorations upright on the visible page, independent of rotation.
        with fitz.open() as layer:
            mark=layer.new_page(width=visible.width,height=visible.height)
            if text.strip():
                from .models import EditableText
                from .pdf_handler import _get_font_args_for_pymupdf
                font_args,error=_get_font_args_for_pymupdf(EditableText(0,0,text,font_family='DejaVu Sans'))
                if error:
                    raise ValueError(error)
                font=fitz.Font(**font_args)
                # Keep the imported layer's font resource separate from canvas
                # fonts, whose encoding can differ after a page is rebuilt.
                font_args['fontname']='pdflx_watermark_unicode'
                width=font.text_length(text,fontsize=font_size)
                if width > visible.width-32:
                    raise ValueError('The watermark is too wide. Reduce its font size.')
                y = visible.height/2 if position=='center' else (32+font_size if position=='top' else visible.height-32)
                mark.insert_text(((visible.width-width)/2,y),text,fontsize=font_size,
                                 color=(0.25,0.25,0.25),fill_opacity=opacity,**font_args)
            if logo:
                image=fitz.Pixmap(logo)
                width=min(180,visible.width*0.35)
                height=min(visible.height*0.25,width*image.height/image.width)
                width=height*image.width/image.height
                y=(visible.height-height)/2 if position=='center' else (24 if position=='top' else visible.height-height-24)
                import numpy as np
                rgba = fitz.Pixmap(image,1) if not image.alpha else image
                alpha = np.frombuffer(rgba.samples,dtype=np.uint8).reshape(-1,rgba.n)[:,-1]
                rgba.set_alpha((alpha.astype(float)*opacity).astype(np.uint8).tobytes(),premultiply=1)
                mark.insert_image(fitz.Rect((visible.width-width)/2,y,(visible.width+width)/2,y+height),pixmap=rgba)
            if numbering:
                try:
                    label=template.format(page=start_number+number-first,total=last-first+1)
                except (KeyError,ValueError,IndexError) as error:
                    raise ValueError('Use {page} and {total} in the numbering format.') from error
                width=fitz.get_text_length(label,fontsize=10)
                mark.insert_text(((visible.width-width)/2,visible.height-16),label,fontsize=10)
            page.show_pdf_page(page.rect*page.derotation_matrix,layer,0,rotate=page.rotation,overlay=True)


def crop_pages(doc,first,last,margins,reset=False):
    values=tuple(float(value) for value in margins)
    if len(values)!=4 or not all(math.isfinite(value) and value>=0 for value in values):
        raise ValueError('Margins must be finite, nonnegative point values.')
    planned=[]
    for number in page_range(doc,first,last):
        page=doc[number]
        box=page.cropbox
        if reset:
            media=page.mediabox
            target=fitz.Rect(media.x0,0,media.x1,media.height)
        else:
            left,top,right,bottom=values
            target=fitz.Rect(box.x0+left,box.y0+top,box.x1-right,box.y1-bottom)
            if target.width<10 or target.height<10:
                raise ValueError(f'The crop leaves page {number+1} too small.')
        planned.append((number,target))
    for number,target in planned:
        doc[number].set_cropbox(target)


def create_form_field(doc,page_number,name,kind,rect,choices=(),required=False,*,options=None,value=None):
    name=name.strip()
    if not name:
        raise ValueError('Enter a field name.')
    from .form_tree import ensure_form_fields
    ensure_form_fields(doc)
    if any(widget.field_name==name for page in doc for widget in page.widgets() or ()):
        raise ValueError('A field with this name already exists.')
    page=doc[page_number]
    bounds=fitz.Rect(rect)
    if bounds.is_empty or not bounds.is_valid or bounds not in (page.rect*page.derotation_matrix):
        raise ValueError('The field must fit inside the visible page.')
    if kind=='radio':
        from .extended_form_fields import create_radio_group
        return create_radio_group(doc,page_number,name,bounds,choices,required,options,value)
    types={'text':fitz.PDF_WIDGET_TYPE_TEXT,'checkbox':fitz.PDF_WIDGET_TYPE_CHECKBOX,
           'combo':fitz.PDF_WIDGET_TYPE_COMBOBOX,'list':fitz.PDF_WIDGET_TYPE_LISTBOX,
           'button':fitz.PDF_WIDGET_TYPE_BUTTON,'signature':fitz.PDF_WIDGET_TYPE_SIGNATURE}
    widget=fitz.Widget()
    widget.field_name=name
    widget.field_type=types[kind]
    widget.rect=bounds
    widget.border_color=(0.45,0.45,0.45)
    widget.border_width=1
    widget.fill_color=(1,1,1)
    widget.text_color=(0,0,0)
    widget.text_fontsize=11
    if required:
        widget.field_flags |= fitz.PDF_FIELD_IS_REQUIRED
    if kind in ('combo','list'):
        from .form_properties import normalize_choices,choice_options
        choices=normalize_choices(choices)
        widget.choice_values=choices
        widget.field_value=choice_options(choices)[0][0]
    elif kind=='checkbox':
        widget.field_value='Off'
    else:
        widget.field_value=None if kind in ('button','signature') else ''
    if kind=='button':widget.button_caption=str(value or 'Reset form')
    if kind=='checkbox':widget.text_font='ZaDb';widget.text_fontsize=0
    from .form_properties import apply_options
    apply_options(widget,options)
    multi_value=None
    if kind=='list' and widget.field_flags&fitz.PDF_CH_FIELD_IS_MULTI_SELECT and isinstance(value,(list,tuple)):
        # Several selections are written as a /V array after the widget exists.
        multi_value,value=list(value),None
    if value is not None and kind not in ('button','signature'):
        if kind=='text':widget.field_value=str(value)
        elif kind=='checkbox':widget.field_value='Yes' if value else 'Off'
        else:
            exports=[pair[0] for pair in choice_options(choices)]
            if value not in exports and not (kind=='combo' and widget.field_flags&fitz.PDF_CH_FIELD_IS_EDIT):
                raise ValueError('Choose a listed initial value.')
            widget.field_value=value
    page.add_widget(widget)
    xref=next(field.xref for field in page.widgets() if field.field_name==name)
    from .form_appearance import write_values,refresh_appearance
    if multi_value is not None:
        write_values(doc,xref,multi_value)
    refresh_appearance(doc,xref)
    if kind=='button':
        from .form_buttons import configure
        configure(doc,xref,dict(options or {},button_action=(options or {}).get('button_action','reset')))
    if kind not in ('button','signature'):
        default_type,default=doc.xref_get_key(xref,'V')
        if default_type=='string':default=fitz.get_pdf_str(default)
        if default!='null':doc.xref_set_key(xref,'DV',default)
    doc._reset_page_refs()
    return xref


def form_field_at_point(doc,page_number,point):
    from .document_features import list_form_fields
    return next((field for field in list_form_fields(doc,[page_number])
                 if field['page']==page_number and fitz.Point(point) in fitz.Rect(field['rect'])),None)


def edit_form_field(doc,page_number,xref,name,required=False,max_length=0,rect=None,choices=None,*,options=None):
    from .form_tree import ensure_form_fields
    ensure_form_fields(doc)
    name=name.strip()
    if not name:
        raise ValueError('Enter a field name.')
    page=doc[page_number]
    widget=page.load_widget(xref)
    if widget is None or widget.field_type==fitz.PDF_WIDGET_TYPE_SIGNATURE:
        raise ValueError('Select an editable field.')
    if name!=widget.field_name and any(w.field_name==name for p in doc for w in p.widgets() or ()):
        raise ValueError('A field with this name already exists.')
    if rect is not None:
        bounds=fitz.Rect(rect)
        if bounds.is_empty or bounds not in page.rect*page.derotation_matrix:
            raise ValueError('The field must fit inside the visible page.')
        widget.rect=bounds
    widget.field_name=name
    if widget.field_type==fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
        # Keep the group's native labelled appearances and shared identity.
        from .form_properties import apply_options
        apply_options(widget,options)
        flags=(widget.field_flags|fitz.PDF_FIELD_IS_REQUIRED) if required else (widget.field_flags&~fitz.PDF_FIELD_IS_REQUIRED)
        parent_type,parent=doc.xref_get_key(xref,'Parent')
        target=int(parent.split()[0]) if parent_type=='xref' else xref
        doc.xref_set_key(target,'T',fitz.get_pdf_str(name))
        doc.xref_set_key(target,'Ff',str(flags))
        if rect is not None:
            native=widget.rect*~page.transformation_matrix
            doc.xref_set_key(xref,'Rect',f'[{native.x0} {native.y0} {native.x1} {native.y1}]')
        if options and 'tooltip' in options:doc.xref_set_key(xref,'TU',fitz.get_pdf_str(options['tooltip']))
        doc._reset_page_refs()
        return
    if choices is not None and widget.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX,fitz.PDF_WIDGET_TYPE_LISTBOX):
        from .form_properties import normalize_choices,choice_options
        choice_opts=normalize_choices(choices)
        widget.choice_values=choice_opts
        exports=[pair[0] for pair in choice_options(choice_opts)]
        editable=widget.field_type==fitz.PDF_WIDGET_TYPE_COMBOBOX and widget.field_flags&fitz.PDF_CH_FIELD_IS_EDIT
        if widget.field_value and widget.field_value not in exports and not editable:
            widget.field_value=exports[0]
    widget.field_flags=(widget.field_flags | fitz.PDF_FIELD_IS_REQUIRED) if required else (widget.field_flags & ~fitz.PDF_FIELD_IS_REQUIRED)
    if widget.field_type==fitz.PDF_WIDGET_TYPE_TEXT:
        if max_length<0 or (max_length and len(str(widget.field_value or ''))>max_length):
            raise ValueError('The character limit must allow the current value.')
        widget.text_maxlen=max_length
    from .form_properties import apply_options
    apply_options(widget,options)
    if choices is None and widget.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX,fitz.PDF_WIDGET_TYPE_LISTBOX):
        widget.choice_values=None
    from .form_appearance import is_multi_select,read_values,write_values,refresh_appearance
    selected=read_values(doc,xref) if is_multi_select(widget) else None
    widget.update()
    if widget.field_type==fitz.PDF_WIDGET_TYPE_BUTTON:
        from .form_buttons import configure
        configure(doc,xref,options)
    if selected is not None:
        # widget.update() rewrites /V as a string; restore the selection array.
        from .form_properties import choice_options
        exports=[pair[0] for pair in choice_options(widget.choice_values or ())] if widget.choice_values else None
        write_values(doc,xref,[value for value in selected if exports is None or value in exports])
    refresh_appearance(doc,xref)
    doc._reset_page_refs()


def delete_form_field(doc,page_number,xref):
    page=doc[page_number]
    widget=page.load_widget(xref)
    if widget is None or widget.field_type==fitz.PDF_WIDGET_TYPE_SIGNATURE:
        raise ValueError('Select an editable field.')
    page.delete_widget(widget)
    doc._reset_page_refs()


def flatten_forms(doc):
    if not any(list(page.widgets() or ()) for page in doc):
        raise ValueError('This document has no form fields.')
    if any(widget.field_type==fitz.PDF_WIDGET_TYPE_SIGNATURE and widget.is_signed
           for page in doc for widget in page.widgets() or ()):
        raise ValueError('Flatten an unsigned copy; signed fields must remain intact.')
    doc.bake(annots=False,widgets=True)
