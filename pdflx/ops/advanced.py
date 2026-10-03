"""Advanced structure: portfolios, e-invoice attachments, and a raw object inspector."""
import datetime
import html
import os
import re

import pymupdf as fitz

from .common import OperationError


def _pdf_date():
    return fitz.get_pdf_now()


def _mime_name(filename):
    extension = os.path.splitext(filename)[1].lower()
    mime = {'.xml': 'text/xml', '.pdf': 'application/pdf', '.txt': 'text/plain', '.csv': 'text/csv',
            '.json': 'application/json', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg'}
    return mime.get(extension, 'application/octet-stream').replace('/', '#2F')


def _embedded_names(doc):
    """Return the EmbeddedFiles Names array as text, creating an empty tree if needed."""
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, 'Names')[0] == 'null':
        doc.xref_set_key(catalog, 'Names', '<<>>')
    kind, value = doc.xref_get_key(catalog, 'Names/EmbeddedFiles')
    if kind == 'null':
        doc.xref_set_key(catalog, 'Names/EmbeddedFiles', '<</Names[]>>')
        return '[]'
    if doc.xref_get_key(catalog, 'Names/EmbeddedFiles/Kids')[0] != 'null':
        raise OperationError('This document stores attachments in a name tree with kids; '
                             'pdfLX cannot safely add an associated file to it.')
    kind, names = doc.xref_get_key(catalog, 'Names/EmbeddedFiles/Names')
    return names if kind == 'array' else '[]'


def add_associated_file(doc, data, filename, description='', relationship='Alternative'):
    """Embed a file as an indirect filespec listed in the catalog /AF array (PDF 2.0 / PDF/A-3)."""
    if relationship not in ('Source', 'Data', 'Alternative', 'Supplement', 'Unspecified'):
        raise OperationError('Unknown file relationship.')
    stream = doc.get_new_xref()
    doc.update_object(stream, f'<</Type/EmbeddedFile/Subtype/{_mime_name(filename)}'
                              f'/Params<</Size {len(data)}/ModDate{fitz.get_pdf_str(_pdf_date())}>>>>')
    doc.update_stream(stream, data, compress=True)
    spec = doc.get_new_xref()
    name = fitz.get_pdf_str(filename)
    doc.update_object(spec, f'<</Type/Filespec/F{name}/UF{name}/Desc{fitz.get_pdf_str(description or filename)}'
                            f'/AFRelationship/{relationship}/EF<</F {stream} 0 R/UF {stream} 0 R>>>>')
    names = _embedded_names(doc)
    if f'{name}' in names:
        raise OperationError(f'An attachment named {filename} already exists.')
    doc.xref_set_key(doc.pdf_catalog(), 'Names/EmbeddedFiles/Names', names.rstrip()[:-1] + f' {name} {spec} 0 R]')
    kind, existing = doc.xref_get_key(doc.pdf_catalog(), 'AF')
    refs = re.findall(r'\d+\s+0\s+R', existing) if kind == 'array' else []
    doc.xref_set_key(doc.pdf_catalog(), 'AF', '[' + ' '.join(refs + [f'{spec} 0 R']) + ']')
    return spec


FACTURX_PROFILES = ('MINIMUM', 'BASIC WL', 'BASIC', 'EN 16931', 'EXTENDED', 'XRECHNUNG')


def attach_einvoice(doc, xml_data, profile='EN 16931', filename='factur-x.xml', version='1.0'):
    """Attach Factur-X/ZUGFeRD XML with the associated-file relationship and XMP metadata.

    This does not make the document PDF/A-3 conformant, which Factur-X also
    requires (embedded fonts, colour profile, PDF/A identification). Validate
    the result with a Factur-X validator before sending it.
    """
    if profile not in FACTURX_PROFILES:
        raise OperationError('Unknown Factur-X profile.')
    text = xml_data.decode('utf-8', 'replace') if isinstance(xml_data, bytes) else xml_data
    if 'CrossIndustryInvoice' not in text:
        raise OperationError('The XML is not a UN/CEFACT Cross Industry Invoice (Factur-X/ZUGFeRD).')
    data = xml_data if isinstance(xml_data, bytes) else xml_data.encode('utf-8')
    spec = add_associated_file(doc, data, filename, 'Factur-X invoice', 'Alternative')
    _write_facturx_xmp(doc, profile, filename, version)
    return spec


def _write_facturx_xmp(doc, profile, filename, version):
    meta = doc.metadata or {}
    esc = lambda value: html.escape(value or '', quote=False)
    packet = f'''<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:pdf="http://ns.adobe.com/pdf/1.3/" xmlns:xmp="http://ns.adobe.com/xap/1.0/"
    xmlns:fx="urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#"
    xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"
    xmlns:pdfaSchema="http://www.aiim.org/pdfa/ns/schema#"
    xmlns:pdfaProperty="http://www.aiim.org/pdfa/ns/property#">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{esc(meta.get('title'))}</rdf:li></rdf:Alt></dc:title>
   <pdf:Producer>{esc(meta.get('producer'))}</pdf:Producer>
   <xmp:ModifyDate>{datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec='seconds')}</xmp:ModifyDate>
   <fx:DocumentType>INVOICE</fx:DocumentType>
   <fx:DocumentFileName>{esc(filename)}</fx:DocumentFileName>
   <fx:Version>{esc(version)}</fx:Version>
   <fx:ConformanceLevel>{esc(profile)}</fx:ConformanceLevel>
   <pdfaExtension:schemas><rdf:Bag><rdf:li rdf:parseType="Resource">
    <pdfaSchema:schema>Factur-X PDFA Extension Schema</pdfaSchema:schema>
    <pdfaSchema:namespaceURI>urn:factur-x:pdfa:CrossIndustryDocument:invoice:1p0#</pdfaSchema:namespaceURI>
    <pdfaSchema:prefix>fx</pdfaSchema:prefix>
    <pdfaSchema:property><rdf:Seq>
     <rdf:li rdf:parseType="Resource"><pdfaProperty:name>DocumentFileName</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Name of the embedded XML invoice file</pdfaProperty:description></rdf:li>
     <rdf:li rdf:parseType="Resource"><pdfaProperty:name>DocumentType</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>INVOICE</pdfaProperty:description></rdf:li>
     <rdf:li rdf:parseType="Resource"><pdfaProperty:name>Version</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Version of the Factur-X XML schema</pdfaProperty:description></rdf:li>
     <rdf:li rdf:parseType="Resource"><pdfaProperty:name>ConformanceLevel</pdfaProperty:name><pdfaProperty:valueType>Text</pdfaProperty:valueType><pdfaProperty:category>external</pdfaProperty:category><pdfaProperty:description>Factur-X profile</pdfaProperty:description></rdf:li>
    </rdf:Seq></pdfaSchema:property>
   </rdf:li></rdf:Bag></pdfaExtension:schemas>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''
    doc.set_xml_metadata(packet)


def einvoice_xml(doc):
    """Return (filename, bytes) of an embedded Factur-X/ZUGFeRD/XRechnung XML, or None."""
    for name in doc.embfile_names():
        lower = name.lower()
        if lower in ('factur-x.xml', 'zugferd-invoice.xml', 'xrechnung.xml') or lower.endswith('-invoice.xml'):
            return name, doc.embfile_get(name)
    return None


# ---------------------------------------------------------------- portfolios

def create_portfolio(paths, title='Portfolio'):
    """Build a PDF Portfolio (collection): a cover page plus embedded files shown as a list."""
    if not paths:
        raise OperationError('Choose files for the portfolio.')
    doc = fitz.open()
    page = doc.new_page()
    lines = [f'<h1>{html.escape(title)}</h1>', '<p>This PDF is a portfolio. Open the attachments panel to see '
                                               'the files it contains.</p><ul>']
    seen = set()
    for path in paths:
        name = os.path.basename(path)
        base, extension = os.path.splitext(name)
        counter = 2
        while name in seen:
            name = f'{base} ({counter}){extension}'
            counter += 1
        seen.add(name)
        with open(path, 'rb') as handle:
            doc.embfile_add(name, handle.read(), filename=os.path.basename(path), desc=name)
        lines.append(f'<li>{html.escape(name)}</li>')
    lines.append('</ul>')
    page.insert_htmlbox(page.rect + (56, 56, -56, -56), ''.join(lines))
    catalog = doc.pdf_catalog()
    doc.xref_set_key(catalog, 'Collection', '<</Type/Collection/View/D>>')
    doc.set_pagemode('UseAttachments')
    doc.set_metadata({'title': title})
    return doc


def is_portfolio(doc):
    return doc.xref_get_key(doc.pdf_catalog(), 'Collection')[0] != 'null'


# ---------------------------------------------------------------- raw objects

def object_summary(doc, xref):
    try:
        kind = doc.xref_get_key(xref, 'Type')
        subtype = doc.xref_get_key(xref, 'Subtype')
        text = doc.xref_object(xref, compressed=True)
    except Exception:
        return {'xref': xref, 'type': '(free)', 'subtype': '', 'stream': False, 'preview': ''}
    return {'xref': xref, 'type': kind[1].lstrip('/') if kind[0] == 'name' else '',
            'subtype': subtype[1].lstrip('/') if subtype[0] == 'name' else '',
            'stream': doc.xref_is_stream(xref), 'preview': text[:120].replace('\n', ' ')}


def find_objects(doc, query='', limit=500):
    """Find objects by number, /Type, /Subtype, or text contained in the object."""
    query = query.strip()
    results = []
    if query.isdigit():
        xref = int(query)
        return [object_summary(doc, xref)] if 0 < xref < doc.xref_length() else []
    needle = query.lstrip('/').lower()
    for xref in range(1, doc.xref_length()):
        summary = object_summary(doc, xref)
        haystack = f"{summary['type']} {summary['subtype']} {summary['preview']}".lower()
        if not needle or needle in haystack:
            results.append(summary)
            if len(results) >= limit:
                break
    return results


def read_object(doc, xref):
    source = doc.xref_object(xref, compressed=False)
    stream = None
    if doc.xref_is_stream(xref):
        data = doc.xref_stream(xref) or b''
        if data and sum(byte < 9 or 13 < byte < 32 for byte in data[:2048]) < len(data[:2048]) * 0.02:
            stream = data.decode('latin-1')
        else:
            stream = None if data else ''
    return source, stream


def write_object(doc, xref, source, stream_text=None):
    """Replace an object's dictionary (and decoded stream text). Raises on invalid syntax."""
    source = source.strip()
    if not source:
        raise OperationError('The object source is empty.')
    if xref == doc.pdf_catalog() and '/Pages' not in source:
        raise OperationError('The document catalog must keep its /Pages entry.')
    previous = doc.xref_object(xref, compressed=False)
    try:
        doc.update_object(xref, source)
        if stream_text is not None:
            doc.update_stream(xref, stream_text.encode('latin-1'))
    except Exception as error:
        doc.update_object(xref, previous)
        raise OperationError(f'MuPDF rejected the object: {error}')
    doc._reset_page_refs()
