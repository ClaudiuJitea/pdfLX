"""AcroForm structure: hierarchy listing and parent/child grouping.

A dotted name such as "address.city" is only a fully qualified name; the
hierarchy itself is the /Parent and /Kids links. Grouping here creates a real
parent field dictionary so names, value inheritance, and the /Fields array stay
consistent.
"""
import re

import pymupdf as fitz

from .common import OperationError

_REF = re.compile(r'(\d+)\s+0\s+R')


def _refs(text):
    return [int(ref) for ref in _REF.findall(text or '')]


def _fields_array(doc):
    from ..form_tree import ensure_form_fields, _array
    ensure_form_fields(doc)
    holder, array = _array(doc)
    return holder, array


def _set_fields(doc, holder, refs):
    value = '[' + ' '.join(f'{ref} 0 R' for ref in refs) + ']'
    if holder is not None:
        doc.update_object(holder, value)
    else:
        doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/Fields', value)


def field_tree(doc):
    """Return the AcroForm hierarchy as nested dicts: name, full_name, xref, kind, children."""
    kind, value = doc.xref_get_key(doc.pdf_catalog(), 'AcroForm/Fields')
    if kind == 'xref':
        value = doc.xref_object(int(value.split()[0]))
        kind = 'array' if value.strip().startswith('[') else kind
    if kind != 'array':
        return []
    seen = set()

    def node(xref, parent_name):
        if xref in seen:
            return None
        seen.add(xref)
        t_kind, title = doc.xref_get_key(xref, 'T')
        name = title if t_kind == 'string' else ''
        full = f'{parent_name}.{name}' if parent_name and name else (name or parent_name)
        ft = doc.xref_get_key(xref, 'FT')
        kids_kind, kids = doc.xref_get_key(xref, 'Kids')
        children = []
        if kids_kind == 'array':
            for ref in re.findall(r'(\d+)\s+0\s+R', kids):
                child = node(int(ref), full)
                if child:
                    children.append(child)
        return {'name': name, 'full_name': full, 'xref': xref, 'kind': ft[1].lstrip('/') if ft[0] == 'name' else '',
                'widget': doc.xref_get_key(xref, 'Subtype') == ('name', '/Widget'), 'children': children}
    return [item for item in (node(int(ref), '') for ref in re.findall(r'(\d+)\s+0\s+R', value)) if item]


def full_names(doc):
    names = {}

    def walk(items):
        for item in items:
            names[item['xref']] = item['full_name']
            walk(item['children'])
    walk(field_tree(doc))
    return names


def group_fields(doc, xrefs, parent_name):
    """Make top-level fields children of a new parent field named ``parent_name``."""
    parent_name = parent_name.strip()
    if not parent_name or '.' in parent_name:
        raise OperationError('Enter a parent name without dots.')
    holder, array = _fields_array(doc)
    roots = _refs(array)
    xrefs = list(dict.fromkeys(xrefs))
    if not xrefs:
        raise OperationError('Select at least one field.')
    for xref in xrefs:
        if xref not in roots:
            raise OperationError('Only top-level fields can be grouped; ungroup nested fields first.')
    top_names = {doc.xref_get_key(ref, 'T')[1] for ref in roots if ref not in xrefs}
    if parent_name in top_names:
        raise OperationError(f'A top-level field named "{parent_name}" already exists.')
    names = [doc.xref_get_key(xref, 'T')[1] for xref in xrefs]
    if len(set(names)) != len(names):
        raise OperationError('Grouped fields need different names.')
    parent = doc.get_new_xref()
    doc.update_object(parent, f'<</T {fitz.get_pdf_str(parent_name)}/Kids [' +
                      ' '.join(f'{xref} 0 R' for xref in xrefs) + ']>>')
    for xref in xrefs:
        doc.xref_set_key(xref, 'Parent', f'{parent} 0 R')
    position = min(roots.index(xref) for xref in xrefs)
    remaining = [ref for ref in roots if ref not in xrefs]
    remaining.insert(min(position, len(remaining)), parent)
    _set_fields(doc, holder, remaining)
    doc._reset_page_refs()
    return parent


def ungroup_field(doc, parent):
    """Move a parent's children to the top level and delete the parent field.

    Values held only by the parent (/V, /DA, /FT, /Ff) are copied to children
    that do not override them, so the children keep their effective state.
    """
    kids_kind, kids = doc.xref_get_key(parent, 'Kids')
    if kids_kind != 'array' or doc.xref_get_key(parent, 'Subtype') == ('name', '/Widget'):
        raise OperationError('Select a parent field that groups other fields.')
    children = _refs(kids)
    if any(doc.xref_get_key(child, 'T')[0] == 'null' for child in children):
        raise OperationError('This field has unnamed widget children (for example a radio group); '
                             'it cannot be ungrouped.')
    if doc.xref_get_key(parent, 'Parent')[0] != 'null':
        raise OperationError('Ungroup the outer parent first.')
    holder, array = _fields_array(doc)
    roots = _refs(array)
    if parent not in roots:
        raise OperationError('The field is not in the form field list.')
    parent_name = doc.xref_get_key(parent, 'T')[1]
    top = {doc.xref_get_key(ref, 'T')[1] for ref in roots if ref != parent}
    clashes = [doc.xref_get_key(child, 'T')[1] for child in children if doc.xref_get_key(child, 'T')[1] in top]
    if clashes:
        raise OperationError(f'Top-level fields already use: {", ".join(clashes)}.')
    for key in ('FT', 'Ff', 'V', 'DV', 'DA', 'Q'):
        kind, value = doc.xref_get_key(parent, key)
        if kind == 'null':
            continue
        for child in children:
            if doc.xref_get_key(child, key)[0] == 'null':
                doc.xref_set_key(child, key, value)
    for child in children:
        doc.xref_set_key(child, 'Parent', 'null')
    index = roots.index(parent)
    roots[index:index + 1] = children
    _set_fields(doc, holder, roots)
    doc.update_object(parent, '<<>>')
    doc._reset_page_refs()
    return parent_name
