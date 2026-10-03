"""Independent native widget copies, preserving values, appearance and actions."""
import re
import uuid
import pymupdf as fitz
from .document_features import list_form_fields


def _offset(doc,page_number,rect):
    page=doc[page_number];visual=fitz.Rect(rect)*page.rotation_matrix
    dx=min(16,max(0,page.rect.width-visual.x1));dy=min(16,max(0,page.rect.height-visual.y1))
    if dx==0 and dy==0:dx=-min(16,max(0,visual.x0));dy=-min(16,max(0,visual.y0))
    matrix=page.derotation_matrix
    return matrix.a*dx+matrix.c*dy,matrix.b*dx+matrix.d*dy


def _copy_appearance(doc,value,copies):
    def clone(match):
        old=int(match.group(1))
        if old not in copies:
            new=doc.get_new_xref();copies[old]=new
            obj=doc.xref_object(old)
            if doc.xref_is_stream(old):
                # Resources (fonts/images) remain shared, but appearance streams
                # must be independent: editing a copy can rebuild these streams.
                doc.update_object(new,obj);doc.update_stream(new,doc.xref_stream(old))
            else:doc.update_object(new,_copy_appearance(doc,obj,copies))
        return f'{copies[old]} 0 R'
    return re.sub(r'(\d+)\s+0\s+R',clone,value)


def _inherited(doc,xref,key):
    seen=set()
    while xref not in seen:
        seen.add(xref);kind,value=doc.xref_get_key(xref,key)
        if kind!='null':return kind,value
        kind,parent=doc.xref_get_key(xref,'Parent')
        if kind!='xref':break
        xref=int(parent.split()[0])
    return 'null','null'


def _pdf_value(kind,value):
    if kind=='string':return fitz.get_pdf_str(value)
    if kind=='name':return '/' + ''.join('#%02X'%byte for byte in value[1:].encode('utf-8'))
    return value


def duplicate_form_field(doc,page_number,xref):
    from .form_tree import ensure_form_fields,update_form_roots
    fields=list_form_fields(doc)
    source=next((f for f in fields if f['page']==page_number and f['xref']==xref),None)
    if not source:raise ValueError('Select a form field to duplicate.')
    if source.get('signed'):raise ValueError('A signed certificate field cannot be duplicated.')
    ensure_form_fields(doc)
    names={f['name'] for f in fields};name=source['name']+' copy';number=2
    while name in names:name=source['name']+f' copy {number}';number+=1
    radio=source['type']==fitz.PDF_WIDGET_TYPE_RADIOBUTTON
    members=[f for f in fields if f['name']==source['name'] and f['type']==source['type']] if radio else [source]
    offsets={}
    for number in {f['page'] for f in members}:
        group=[f for f in members if f['page']==number]
        bounds=fitz.Rect(group[0]['rect'])
        for field in group[1:]:bounds|=fitz.Rect(field['rect'])
        offsets[number]=_offset(doc,number,bounds)
    appearance_copies={};new_fields=[]
    parent=None
    if radio:
        parent=doc.get_new_xref();doc.update_object(parent,'<< /FT /Btn >>')
        for key in ('Ff','V','DV','TU'):
            kind,value=_inherited(doc,xref,key)
            if kind!='null':doc.xref_set_key(parent,key,_pdf_value(kind,value))
        doc.xref_set_key(parent,'T',fitz.get_pdf_str(name))
    for field in members:
        old=field['xref'];new=doc.get_new_xref()
        doc.update_object(new,doc.xref_object(old))
        for key in ('FT','Ff','V','DV','Opt','DA','Q','MaxLen','TU','AA'):
            kind,value=_inherited(doc,old,key)
            if kind!='null':doc.xref_set_key(new,key,_pdf_value(kind,value))
        for key in ('MK','BS','AA'):
            kind,value=doc.xref_get_key(new,key)
            if kind=='xref':
                clone=doc.get_new_xref()
                doc.update_object(clone,doc.xref_object(int(value.split()[0])))
                doc.xref_set_key(new,key,f'{clone} 0 R')
        doc.xref_set_key(new,'Parent',f'{parent} 0 R' if parent else 'null')
        doc.xref_set_key(new,'T','null' if radio else fitz.get_pdf_str(name))
        if radio:
            for key in ('V','DV','Ff'):doc.xref_set_key(new,key,'null')
        doc.xref_set_key(new,'NM',fitz.get_pdf_str(str(uuid.uuid4())))
        doc.xref_set_key(new,'Kids','null')
        kind,ap=doc.xref_get_key(old,'AP')
        if kind!='null':doc.xref_set_key(new,'AP',_copy_appearance(doc,ap,appearance_copies))
        number=field['page'];page=doc[number];dx,dy=offsets[number]
        rect=(fitz.Rect(field['rect'])+(dx,dy,dx,dy))*~page.transformation_matrix
        doc.xref_set_key(new,'Rect',f'[{rect.x0} {rect.y0} {rect.x1} {rect.y1}]')
        doc.xref_set_key(new,'P',f'{page.xref} 0 R')
        kind,annots=doc.xref_get_key(page.xref,'Annots')
        if kind=='xref':
            array_ref=int(annots.split()[0]);array=doc.xref_object(array_ref)
            doc.update_object(array_ref,array.rstrip()[:-1]+f' {new} 0 R]')
        else:doc.xref_set_key(page.xref,'Annots',(annots.rstrip()[:-1] if kind=='array' else '[')+f' {new} 0 R]')
        new_fields.append((number,new))
    if radio:doc.xref_set_key(parent,'Kids','['+' '.join(f'{ref} 0 R' for number,ref in new_fields)+']')
    additions=[parent] if radio else [ref for number,ref in new_fields]
    update_form_roots(doc,add=additions)
    doc._reset_page_refs()
    return new_fields
