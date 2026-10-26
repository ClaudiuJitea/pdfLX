"""Validate and maintain the document's AcroForm field array."""
import re
import pymupdf as fitz

_REF=re.compile(r'(\d+)\s+0\s+R')


def _array(doc):
    kind,value=doc.xref_get_key(doc.pdf_catalog(),'AcroForm/Fields')
    holder=None;seen=set()
    while kind=='xref':
        holder=int(value.split()[0])
        if holder in seen:return None,None
        seen.add(holder);value=doc.xref_object(holder).strip()
        if value.startswith('[') and value.endswith(']'):return holder,value
        if re.fullmatch(r'\d+\s+0\s+R',value):continue
        return None,None
    return (None,value) if kind=='array' else (None,None)


def ensure_form_fields(doc):
    """Repair invalid field lists from actual widgets, retaining their parent trees.

    Valid direct and indirect arrays are left untouched. This only runs before
    an authorised form mutation, so its repair is included in the same undo.
    """
    if _array(doc)[1] is not None:return False
    roots=[]
    kind,value=doc.xref_get_key(doc.pdf_catalog(),'AcroForm/Fields')
    # Some PDFs accidentally store the array's PDF syntax in a text string.
    if kind=='string' and re.fullmatch(r'\[\s*(?:\d+\s+0\s+R\s*)*\]',value):
        roots.extend(int(match) for match in _REF.findall(value) if 0<int(match)<doc.xref_length())
    elif kind=='xref':
        obj=doc.xref_object(int(value.split()[0])).strip()
        match=re.fullmatch(r'\(\s*(\[\s*(?:\d+\s+0\s+R\s*)*\])\s*\)',obj)
        if match:roots.extend(int(ref) for ref in _REF.findall(match[1]) if 0<int(ref)<doc.xref_length())
    for page in doc:
        for widget in page.widgets() or ():
            root=widget.xref;seen=set()
            while root not in seen:
                seen.add(root);parent_type,parent=doc.xref_get_key(root,'Parent')
                if parent_type!='xref':break
                candidate=int(parent.split()[0])
                if not 0<candidate<doc.xref_length():break
                root=candidate
            else:raise ValueError('The form field hierarchy contains a cycle.')
            roots.append(root)
    roots=list(dict.fromkeys(roots))
    doc.xref_set_key(doc.pdf_catalog(),'AcroForm/Fields','['+' '.join(f'{ref} 0 R' for ref in roots)+']')
    if _array(doc)[1] is None:raise ValueError('Could not repair the PDF form field list.')
    doc._reset_page_refs()
    return True


def update_form_roots(doc,add=(),remove=()):
    ensure_form_fields(doc)
    holder,array=_array(doc)
    if remove:
        removed=set(remove)
        array=_REF.sub(lambda match:'' if int(match[1]) in removed else match[0],array)
    value=array.rstrip()[:-1]+' '+' '.join(f'{ref} 0 R' for ref in add)+']'
    if holder is not None:doc.update_object(holder,value)
    else:doc.xref_set_key(doc.pdf_catalog(),'AcroForm/Fields',value)
    if _array(doc)[1] is None:raise ValueError('The PDF form field list must be an array.')


def add_widget(page,widget):
    """Add a widget as one merged field-widget object, as PyMuPDF 1.x did.

    PyMuPDF 2 always wraps a new widget in a separate parent field and then
    ignores field-level changes (name, flags, limits, scripts) made through
    Widget.update(). Fields with dotted names or shared names keep the parent.
    """
    doc=page.parent;annot=page.add_widget(widget);xref=annot.xref
    kind,value=doc.xref_get_key(xref,'Parent')
    if kind!='xref':return annot
    parent=int(value.split()[0])
    if _REF.findall(doc.xref_get_key(parent,'Kids')[1])!=[str(xref)] or doc.xref_get_key(parent,'Parent')[0]!='null':
        return annot
    for key in doc.xref_get_keys(parent):
        if key=='Kids' or doc.xref_get_key(xref,key)[0]!='null':continue
        kind,value=doc.xref_get_key(parent,key)
        doc.xref_set_key(xref,key,fitz.get_pdf_str(value) if kind=='string' else value)
    doc.xref_set_key(xref,'Parent','null')
    holder,array=_array(doc)
    array=_REF.sub(lambda match:f'{xref} 0 R' if int(match[1])==parent else match[0],array)
    if holder is not None:doc.update_object(holder,array)
    else:doc.xref_set_key(doc.pdf_catalog(),'AcroForm/Fields',array)
    doc.update_object(parent,'<<>>')
    return annot


def owning_field(doc,xref):
    """Return the field that owns a widget: the widget itself or its nearest named ancestor."""
    current,seen=xref,set()
    while current not in seen:
        seen.add(current)
        if doc.xref_get_key(current,'T')[0]!='null':return current
        kind,value=doc.xref_get_key(current,'Parent')
        if kind!='xref':break
        current=int(value.split()[0])
    return xref


def partial_name(doc,field,name):
    """Turn a fully qualified name into the /T of ``field``, dropping its parents' prefix."""
    names,current,seen=[],field,{field}
    while True:
        kind,value=doc.xref_get_key(current,'Parent')
        if kind!='xref':break
        current=int(value.split()[0])
        if current in seen:break
        seen.add(current)
        kind,title=doc.xref_get_key(current,'T')
        if kind=='string' and title:names.insert(0,title)
    prefix='.'.join(names)
    return name[len(prefix)+1:] if prefix and name.startswith(prefix+'.') else name


def write_field_keys(doc,widget,name):
    """Write the field-level settings that Widget.update() skips under PyMuPDF 2.

    PyMuPDF 2 only writes the name, flags, character limit, tooltip, choices and
    default appearance when the widget has no /Parent. Widgets of grouped fields
    and of fields split into field and widget objects (as other editors save
    them) need these written to the field that owns them.
    """
    xref=widget.xref
    if doc.xref_get_key(xref,'Parent')[0]!='xref':return
    field=owning_field(doc,xref)
    doc.xref_set_key(field,'T',fitz.get_pdf_str(partial_name(doc,field,name)))
    doc.xref_set_key(field,'Ff',str(widget.field_flags))
    if widget.field_type==fitz.PDF_WIDGET_TYPE_TEXT and widget.text_maxlen:
        doc.xref_set_key(field,'MaxLen',str(widget.text_maxlen))
    if widget.field_label is not None:
        doc.xref_set_key(field,'TU',fitz.get_pdf_str(widget.field_label))
    if widget.choice_values is not None and widget.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX,fitz.PDF_WIDGET_TYPE_LISTBOX):
        items=[f'[{fitz.get_pdf_str(item[0])} {fitz.get_pdf_str(item[1])}]' if isinstance(item,(list,tuple))
               else fitz.get_pdf_str(item) for item in widget.choice_values]
        doc.xref_set_key(field,'Opt','['+' '.join(items)+']')
    color=' '.join(f'{c:g}' for c in widget.text_color or (0,))
    operator={1:'g',3:'rg',4:'k'}[len(widget.text_color or (0,))]
    doc.xref_set_key(xref,'DA',fitz.get_pdf_str(f'{color} {operator} /{widget.text_font} {widget.text_fontsize:g} Tf'))
    if field!=xref:
        # Settings an older pdfLX wrote onto the widget would override the field's.
        for key in ('Ff','MaxLen','TU','Opt'):
            if doc.xref_get_key(xref,key)[0]!='null':doc.xref_set_key(xref,key,'null')
