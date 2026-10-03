"""Document level PyMuPDF features exposed by the editor's Tools menu."""

import csv
import io
import os
import zipfile

try:
    import pymupdf as fitz
except ImportError:
    import fitz

from .form_properties import decode_button_state, border_style_key
from .form_appearance import is_multi_select, read_values, write_values, refresh_appearance


METADATA_FIELDS = ("title", "author", "subject", "keywords", "creator")


def update_metadata(doc, values):
    """Update editable PDF properties while preserving other standard fields."""
    metadata = {key: doc.metadata.get(key) or "" for key in
                ("title", "author", "subject", "keywords", "creator",
                 "producer", "creationDate", "modDate")}
    for key in METADATA_FIELDS:
        if key in values:
            metadata[key] = values[key].strip()
    doc.set_metadata(metadata)
    # Keep an existing XMP packet in step; PDF/A and asset tools read XMP first.
    if doc.get_xml_metadata():
        from .ops.structure import sync_xmp
        sync_xmp(doc)


def clear_metadata(doc):
    """Remove both standard and XML document metadata."""
    doc.set_metadata({})
    if doc.get_xml_metadata():
        doc.del_xml_metadata()


def add_bookmark(doc, title, page_index):
    title = title.strip()
    if not title or not 0 <= page_index < doc.page_count:
        raise ValueError("A title and valid page are required")
    toc = doc.get_toc(simple=False)
    toc.append([1, title, page_index + 1])
    doc.set_toc(toc)


def remove_bookmark(doc, index):
    """Remove a bookmark and its nested children."""
    toc = doc.get_toc(simple=False)
    if not 0 <= index < len(toc):
        raise IndexError(index)
    level = toc[index][0]
    end = index + 1
    while end < len(toc) and toc[end][0] > level:
        end += 1
    del toc[index:end]
    doc.set_toc(toc)


def _subtree_end(toc, index):
    end = index + 1
    while end < len(toc) and toc[end][0] > toc[index][0]:
        end += 1
    return end


def shift_bookmark_level(doc, index, delta):
    """Indent (delta=1) or outdent (delta=-1) a bookmark together with its children."""
    toc = doc.get_toc(simple=False)
    if not 0 <= index < len(toc):
        raise IndexError(index)
    level = toc[index][0] + delta
    if level < 1:
        raise ValueError("The bookmark is already at the top level")
    if delta > 0 and (index == 0 or level > toc[index - 1][0] + 1):
        raise ValueError("Only a bookmark that follows another can be indented")
    for item in toc[index:_subtree_end(toc, index)]:
        item[0] += delta
    doc.set_toc(toc)


def move_bookmark(doc, index, direction):
    """Move a bookmark and its children before its previous or after its next sibling.

    Returns the bookmark's new index.
    """
    toc = doc.get_toc(simple=False)
    if not 0 <= index < len(toc):
        raise IndexError(index)
    level, end = toc[index][0], _subtree_end(toc, index)
    block = toc[index:end]
    if direction < 0:
        previous = index - 1
        while previous >= 0 and toc[previous][0] > level:
            previous -= 1
        if previous < 0 or toc[previous][0] != level:
            raise ValueError("The bookmark is already first among its siblings")
        toc[previous:end] = block + toc[previous:index]
        new_index = previous
    else:
        if end >= len(toc) or toc[end][0] != level:
            raise ValueError("The bookmark is already last among its siblings")
        sibling_end = _subtree_end(toc, end)
        toc[index:sibling_end] = toc[end:sibling_end] + block
        new_index = index + (sibling_end - end)
    doc.set_toc(toc)
    return new_index


def export_page_range(doc, first, last, path):
    """Export an inclusive, zero based range to a separate PDF."""
    if not 0 <= first <= last < doc.page_count:
        raise ValueError("Invalid page range")
    if doc.name and os.path.realpath(doc.name) == os.path.realpath(path):
        raise ValueError("Choose a file different from the open document")
    with fitz.open() as output:
        output.insert_pdf(doc, from_page=first, to_page=last,
                          links=True, annots=True, widgets=True)
        output.save(path, garbage=4, deflate=True)


def export_page_visual(doc, page_index, path, image_format, dpi=200):
    """Render one page as PNG or export its vector representation as SVG."""
    if not 0 <= page_index < doc.page_count:
        raise ValueError("Invalid page index")
    page = doc.load_page(page_index)
    if image_format == "png":
        page.get_pixmap(dpi=dpi, alpha=False).save(path)
    elif image_format == "svg":
        with open(path, "w", encoding="utf-8") as output:
            output.write(page.get_svg_image())
    else:
        raise ValueError("Unsupported page image format")


def export_images_zip(doc, path):
    """Export every image to a ZIP: XObjects once per xref (soft masks merged into
    transparent PNGs) plus inline images, which have no xref."""
    from .ops.graphics import export_images_archive
    from .ops.common import OperationError
    try:
        data, count = export_images_archive(doc)
    except OperationError:
        return 0
    with open(path, "wb") as handle:
        handle.write(data)
    return count


def export_tables_zip(doc, path):
    """Find tables and export each as UTF-8 CSV in a ZIP archive."""
    if not hasattr(doc[0], "find_tables"):
        raise RuntimeError("Table detection requires a newer PyMuPDF version")
    tables = {}
    for page_index in range(doc.page_count):
        for table_index, table in enumerate(doc[page_index].find_tables().tables, 1):
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerows(table.extract())
            tables[f"page-{page_index + 1}-table-{table_index}.csv"] = buffer.getvalue()
    if not tables:
        return 0
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, contents in tables.items():
            archive.writestr(filename, contents)
    return len(tables)


def list_form_fields(doc,pages=None):
    """Describe editable form widgets and read-only signature fields."""
    fields = []
    for page_index in range(doc.page_count) if pages is None else pages:
        page = doc.load_page(page_index)
        for widget in page.widgets() or ():
            from .form_buttons import details
            button_action,button_target=details(doc,widget.xref) if widget.field_type==fitz.PDF_WIDGET_TYPE_BUTTON else (None,None)
            button_page=button_target if button_action=='goto' else None
            fields.append({
                "page": page_index,
                "xref": widget.xref,
                "name": widget.field_name or f"Field {widget.xref}",
                "type": widget.field_type,
                "value": read_values(doc, widget.xref) if is_multi_select(widget) else widget.field_value,
                "rect": tuple(widget.rect),
                "flags": widget.field_flags,
                "readonly": bool(widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY),
                "required": bool(widget.field_flags & fitz.PDF_FIELD_IS_REQUIRED),
                "max_length": widget.text_maxlen or 0,
                "multiline": bool(widget.field_flags & fitz.PDF_TX_FIELD_IS_MULTILINE) if widget.field_type==fitz.PDF_WIDGET_TYPE_TEXT else False,
                "tooltip": widget.field_label or '',
                "font_size": widget.text_fontsize or 0,
                "font": widget.text_font or 'Helv',
                "text_color": tuple(widget.text_color or (0,0,0)),
                "fill_color": tuple(widget.fill_color) if widget.fill_color else None,
                "border_color": tuple(widget.border_color) if widget.border_color else None,
                "border_width": widget.border_width or 0,
                "border_style": border_style_key(widget.border_style),
                "password": bool(widget.field_flags & fitz.PDF_TX_FIELD_IS_PASSWORD) if widget.field_type==fitz.PDF_WIDGET_TYPE_TEXT else False,
                "comb": bool(widget.field_flags & fitz.PDF_TX_FIELD_IS_COMB) if widget.field_type==fitz.PDF_WIDGET_TYPE_TEXT else False,
                "multi_select": is_multi_select(widget),
                "on_state": decode_button_state(widget.on_state()) if widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX,fitz.PDF_WIDGET_TYPE_RADIOBUTTON) else None,
                "button_caption": widget.button_caption or '',
                "button_action": button_action,
                "button_page": button_page,
                "button_target": button_target,
                "choices": list(widget.choice_values or ()) if widget.field_type in
                           (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX) else None,
                "signed": widget.is_signed if widget.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE else None,
            })
    return fields


def scripts_enabled(doc, run_scripts=None):
    """Whether filling should run the form's own scripts (setting 'form_scripts', default on)."""
    from .ops import formjs
    if run_scripts is None:
        from .i18n import get_setting
        run_scripts = bool(get_setting('form_scripts', True))
    return bool(run_scripts and formjs.available() and formjs.has_scripts(doc))


def update_form_fields(doc, values, run_scripts=None):
    """Update supported form values keyed by (page index, widget xref).

    When the form has keystroke/format/validate/calculate scripts and script
    execution is enabled, text and single-choice values are committed through
    MuPDF's JavaScript engine, calculations run afterwards, and appearances are
    rebuilt with the format scripts applied. Rejected values raise ValueError.
    """
    from .form_tree import ensure_form_fields
    from .ops import formjs
    ensure_form_fields(doc)
    changed_pages = set()
    changed_names = set()
    scripted = scripts_enabled(doc, run_scripts)
    for (page_index, xref), value in values.items():
        if not 0 <= page_index < doc.page_count:
            continue
        page = doc.load_page(page_index)
        widget = page.load_widget(xref)
        if widget is None:
            continue
        if widget.field_flags & fitz.PDF_FIELD_IS_READ_ONLY:
            raise ValueError(f'{widget.field_name} is read-only.')
        if widget.field_type == fitz.PDF_WIDGET_TYPE_TEXT:
            new_value = str(value)
            if widget.text_maxlen and len(new_value)>widget.text_maxlen:
                raise ValueError(f'{widget.field_name} allows at most {widget.text_maxlen} characters.')
        elif widget.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX,fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
            new_value = decode_button_state(widget.on_state()) if value else "Off"
        elif is_multi_select(widget):
            selected=[str(item) for item in (value if isinstance(value,(list,tuple)) else ([value] if value else []))]
            if sorted(selected)!=sorted(read_values(doc, widget.xref)):
                write_values(doc, widget.xref, selected)
                changed_pages.add(page_index)
                changed_names.add(widget.field_name)
            continue
        elif widget.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX, fitz.PDF_WIDGET_TYPE_LISTBOX):
            from .form_properties import choice_options
            exports=[item[0] for item in choice_options(widget.choice_values)]
            editable=widget.field_type==fitz.PDF_WIDGET_TYPE_COMBOBOX and widget.field_flags & fitz.PDF_CH_FIELD_IS_EDIT
            if not editable and value not in exports and value!='':
                raise ValueError(f'Choose a listed value for {widget.field_name}.')
            new_value = value
        else:
            continue
        if widget.field_value == new_value:
            continue
        if scripted and widget.field_type in (fitz.PDF_WIDGET_TYPE_TEXT, fitz.PDF_WIDGET_TYPE_COMBOBOX,
                                              fitz.PDF_WIDGET_TYPE_LISTBOX):
            del widget, page
            formjs.set_value(doc, page_index, xref, new_value)
            changed_pages.add(page_index)
            changed_names.add(doc.xref_get_key(xref, 'T')[1])
            continue
        widget.field_value = new_value
        if widget.field_type in (fitz.PDF_WIDGET_TYPE_COMBOBOX,fitz.PDF_WIDGET_TYPE_LISTBOX):
            widget.choice_values=None  # Preserve imported /Opt pairs instead of rewriting them.
        if widget.field_type==fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            from .extended_form_fields import select_radio
            select_radio(doc,widget,new_value)
        else:
            widget.update()
            refresh_appearance(doc, widget.xref)
        changed_pages.add(page_index)
        changed_names.add(widget.field_name)
    doc._reset_page_refs()
    if scripted and changed_pages:
        # Calculated fields may live on any page.
        formjs.recalculate(doc)
        changed_pages.update(number for number in range(doc.page_count) if doc[number].first_widget is not None)
    if changed_names:
        for page_index in range(doc.page_count):
            if page_index in changed_pages:
                continue
            if any(widget.field_name in changed_names for widget in doc[page_index].widgets() or ()):
                changed_pages.add(page_index)
    return changed_pages
