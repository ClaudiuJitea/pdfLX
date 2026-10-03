"""Validate and maintain the document's AcroForm field array."""
import re

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
