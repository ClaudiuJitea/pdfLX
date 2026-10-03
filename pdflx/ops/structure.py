"""Document structure: attachments, links, layers, viewer settings, properties, fonts, XMP."""
import datetime
import html
import os

import pymupdf as fitz

from .common import OperationError, signed_signature_fields

# ---------------------------------------------------------------- embedded files


def attachments(doc):
    """List document-level embedded files."""
    rows = []
    for name in doc.embfile_names():
        info = doc.embfile_info(name)
        rows.append({'name': name, 'filename': info.get('filename') or name,
                     'description': info.get('description') or info.get('desc') or '',
                     'size': info.get('size', info.get('length', 0)),
                     'created': info.get('creationDate', ''), 'modified': info.get('modDate', '')})
    return rows


def add_attachment(doc, path, description=''):
    data = open(path, 'rb').read()
    base = os.path.basename(path)
    name, suffix, existing = base, 2, set(doc.embfile_names())
    while name in existing:
        stem, ext = os.path.splitext(base)
        name = f'{stem} ({suffix}){ext}'
        suffix += 1
    doc.embfile_add(name, data, filename=base, ufilename=base, desc=description or base)
    return name


def attachment_bytes(doc, name):
    return doc.embfile_get(name)


def remove_attachment(doc, name):
    doc.embfile_del(name)


def rename_attachment(doc, name, description):
    doc.embfile_upd(name, desc=description)


# ---------------------------------------------------------------- links

LINK_KINDS = (
    ('uri', fitz.LINK_URI, 'Web address (URI)'),
    ('goto', fitz.LINK_GOTO, 'Page in this document'),
    ('gotor', fitz.LINK_GOTOR, 'Page in another PDF'),
    ('launch', fitz.LINK_LAUNCH, 'Open a file'),
    ('named', fitz.LINK_NAMED, 'Named action'),
)
NAMED_ACTIONS = ('FirstPage', 'LastPage', 'NextPage', 'PrevPage')


def links(doc, pages=None):
    """List links with a normalized description. Rects are in unrotated coordinates."""
    kinds = {value: key for key, value, _label in LINK_KINDS}
    rows = []
    for number in (range(doc.page_count) if pages is None else pages):
        for link in doc[number].get_links():
            kind = kinds.get(link['kind'], 'other')
            target = ''
            if kind == 'uri':
                target = link.get('uri', '')
            elif kind == 'goto':
                target = f"page {link.get('page', -1) + 1}"
            elif kind == 'gotor':
                target = f"{link.get('file', '')} page {link.get('page', 0) + 1}"
            elif kind == 'launch':
                target = link.get('file', '')
            elif kind == 'named':
                target = link.get('name', '') or link.get('nameddest', '')
            rows.append({'page': number, 'xref': link.get('xref', 0), 'kind': kind, 'target': target,
                         'rect': tuple(link['from'] * doc[number].derotation_matrix), 'raw': link})
    return rows


def _link_dict(kind, rect, target, page_target=0, file=''):
    rect = fitz.Rect(rect)
    if rect.is_empty:
        raise OperationError('Select the clickable area first.')
    if kind == 'uri':
        target = target.strip()
        if not target:
            raise OperationError('Enter a web address.')
        if '://' not in target and not target.startswith('mailto:'):
            target = 'https://' + target
        return {'kind': fitz.LINK_URI, 'from': rect, 'uri': target}
    if kind == 'goto':
        return {'kind': fitz.LINK_GOTO, 'from': rect, 'page': int(page_target), 'to': fitz.Point(0, 0), 'zoom': 0}
    if kind == 'gotor':
        if not file:
            raise OperationError('Choose the target PDF file.')
        return {'kind': fitz.LINK_GOTOR, 'from': rect, 'file': file, 'page': int(page_target), 'to': fitz.Point(0, 0)}
    if kind == 'launch':
        if not file:
            raise OperationError('Choose the file to open.')
        return {'kind': fitz.LINK_LAUNCH, 'from': rect, 'file': file}
    if kind == 'named':
        if target not in NAMED_ACTIONS:
            raise OperationError('Choose a named action.')
        return {'kind': fitz.LINK_NAMED, 'from': rect, 'name': target}
    raise OperationError(f'Unknown link type: {kind}.')


def add_link(doc, page_number, rect, kind, target='', page_target=0, file=''):
    """Add a link. ``rect`` is in unrotated page coordinates."""
    page = doc[page_number]
    if kind == 'goto' and not 0 <= int(page_target) < doc.page_count:
        raise OperationError('The target page does not exist.')
    # insert_link takes unrotated coordinates; get_links reports rotated ones.
    link = _link_dict(kind, rect, target, page_target, file)
    page.insert_link(link)
    doc._reset_page_refs()


def delete_link(doc, page_number, xref):
    page = doc[page_number]
    for link in page.get_links():
        if link.get('xref') == xref:
            page.delete_link(link)
            doc._reset_page_refs()
            return
    raise OperationError('The link no longer exists.')


def update_link(doc, page_number, xref, kind, target='', page_target=0, file=''):
    page = doc[page_number]
    for link in page.get_links():
        if link.get('xref') == xref:
            rect = link['from'] * page.derotation_matrix
            page.delete_link(link)
            doc._reset_page_refs()
            add_link(doc, page_number, rect, kind, target, page_target, file)
            return
    raise OperationError('The link no longer exists.')


def detect_urls(doc, pages=None):
    """Create URI links for plain-text web addresses that are not already linked."""
    import re
    pattern = re.compile(r'^(https?://|www\.)[^\s<>"]+[^\s<>".,;:!?)]$', re.IGNORECASE)
    created = 0
    for number in (range(doc.page_count) if pages is None else pages):
        page = doc[number]
        existing = [link['from'] * page.derotation_matrix for link in page.get_links()]
        for x0, y0, x1, y1, word, *_rest in page.get_text('words'):
            if not pattern.match(word):
                continue
            rect = fitz.Rect(x0, y0, x1, y1)
            if any(rect.intersects(r) for r in existing):
                continue
            uri = word if '://' in word else 'https://' + word
            page.insert_link({'kind': fitz.LINK_URI, 'from': rect, 'uri': uri})
            created += 1
    doc._reset_page_refs()
    return created


# ---------------------------------------------------------------- layers (optional content)

def layers(doc):
    """List optional-content groups with their current visibility in the default config."""
    groups = doc.get_ocgs() or {}
    ui = {item.get('text'): item for item in (doc.layer_ui_configs() or ())}
    rows = []
    for xref, info in sorted(groups.items()):
        entry = ui.get(info.get('name'), {})
        rows.append({'xref': xref, 'name': info.get('name', f'Layer {xref}'), 'on': bool(info.get('on', True)),
                     'intent': info.get('intent', []), 'usage': info.get('usage', ''),
                     'locked': bool(entry.get('locked', False))})
    return rows


def set_layer_visibility(doc, visibility):
    """``visibility`` maps OCG xref → bool for the default configuration.

    The stored configuration is rewritten with complete ON/OFF lists (passing
    only OFF would leave the group in both), then the live UI state is updated
    so rendering reflects the change without reopening the document.
    """
    state = {xref: bool(info.get('on', True)) for xref, info in (doc.get_ocgs() or {}).items()}
    state.update({xref: bool(visible) for xref, visible in visibility.items()})
    doc.set_layer(-1, on=[x for x, v in state.items() if v], off=[x for x, v in state.items() if not v])
    names = {(doc.get_ocgs() or {}).get(xref, {}).get('name'): visible for xref, visible in state.items()}
    for item in doc.layer_ui_configs() or ():
        if item.get('text') in names and bool(item.get('on')) != names[item['text']]:
            doc.set_layer_ui_config(item['number'], 1 if names[item['text']] else 2)


def add_layer(doc, name, on=True):
    if not name.strip():
        raise OperationError('Enter a layer name.')
    return doc.add_ocg(name.strip(), on=on)


def move_content_to_new_layer(doc, page_number, name, text, rect, font_size=12):
    """Create a layer and place text on it — a simple way to author layered content."""
    xref = add_layer(doc, name)
    page = doc[page_number]
    page.insert_textbox(fitz.Rect(rect), text, fontsize=font_size, oc=xref)
    return xref


# ---------------------------------------------------------------- viewer settings

PAGE_MODES = (('UseNone', 'Page only'), ('UseOutlines', 'Bookmarks panel'), ('UseThumbs', 'Thumbnails panel'),
              ('FullScreen', 'Full screen'), ('UseOC', 'Layers panel'), ('UseAttachments', 'Attachments panel'))
PAGE_LAYOUTS = (('SinglePage', 'Single page'), ('OneColumn', 'Continuous'), ('TwoColumnLeft', 'Two pages, continuous'),
                ('TwoColumnRight', 'Two pages, continuous, cover'), ('TwoPageLeft', 'Two pages'),
                ('TwoPageRight', 'Two pages, cover'))


def _catalog_name(doc, key, default):
    kind, value = doc.xref_get_key(doc.pdf_catalog(), key)
    return value.lstrip('/') if kind == 'name' else default


def viewer_settings(doc):
    catalog = doc.pdf_catalog()
    lang_kind, lang = doc.xref_get_key(catalog, 'Lang')
    prefs = {}
    for key in ('HideToolbar', 'HideMenubar', 'FitWindow', 'CenterWindow', 'DisplayDocTitle'):
        kind, value = doc.xref_get_key(catalog, f'ViewerPreferences/{key}')
        prefs[key] = value == 'true'
    return {'page_mode': _catalog_name(doc, 'PageMode', 'UseNone'),
            'page_layout': _catalog_name(doc, 'PageLayout', 'SinglePage'),
            'language': lang.strip('()') if lang_kind == 'string' else '',
            'tagged': (doc.markinfo or {}).get('Marked', False),
            'need_appearances': bool(doc.need_appearances()) if doc.is_form_pdf else False,
            'preferences': prefs}


def set_viewer_settings(doc, page_mode=None, page_layout=None, language=None, preferences=None,
                        need_appearances=None):
    if page_mode:
        doc.set_pagemode(page_mode)
    if page_layout:
        doc.set_pagelayout(page_layout)
    if language is not None:
        # Write /Lang directly: set_language() reduces tags such as de-DE to de.
        catalog = doc.pdf_catalog()
        tag = language.strip()
        doc.xref_set_key(catalog, 'Lang', fitz.get_pdf_str(tag) if tag else 'null')
    if preferences:
        catalog = doc.pdf_catalog()
        if doc.xref_get_key(catalog, 'ViewerPreferences')[0] == 'null':
            doc.xref_set_key(catalog, 'ViewerPreferences', '<<>>')
        for key, value in preferences.items():
            doc.xref_set_key(catalog, f'ViewerPreferences/{key}', 'true' if value else 'false')
    if need_appearances is not None and doc.is_form_pdf:
        doc.need_appearances(bool(need_appearances))


# ---------------------------------------------------------------- properties

def properties(doc, path=None):
    """Collect read-only facts for the Properties dialog."""
    meta = doc.metadata or {}
    sizes = {}
    for page in doc:
        key = (round(page.rect.width), round(page.rect.height))
        sizes[key] = sizes.get(key, 0) + 1
    info = {
        'format': meta.get('format', ''), 'producer': meta.get('producer', ''), 'creator': meta.get('creator', ''),
        'created': _pdf_date(meta.get('creationDate', '')), 'modified': _pdf_date(meta.get('modDate', '')),
        'encryption': meta.get('encryption') or '', 'pages': doc.page_count,
        'page_sizes': sizes, 'fast_web_view': bool(doc.is_fast_webaccess),
        'repaired': bool(getattr(doc, 'is_repaired', False)),
        'form': bool(doc.is_form_pdf), 'signature_flags': doc.get_sigflags() if doc.is_pdf else -1,
        'signed_fields': signed_signature_fields(doc), 'tagged': bool((doc.markinfo or {}).get('Marked')),
        'attachments': doc.embfile_count(), 'layers': len(doc.get_ocgs() or {}),
        'javascript': _has_javascript(doc), 'file_size': os.path.getsize(path) if path and os.path.isfile(path) else None,
        'xmp': bool(doc.get_xml_metadata()),
    }
    return info


def _pdf_date(value):
    if not value:
        return ''
    text = value[2:] if value.startswith('D:') else value
    try:
        return datetime.datetime.strptime(text[:14], '%Y%m%d%H%M%S').strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return value


def _has_javascript(doc):
    for xref in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xref, 'S') == ('name', '/JavaScript') or doc.xref_get_key(xref, 'JS')[0] != 'null':
                return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------- fonts

def fonts(doc):
    """List fonts across pages, de-duplicated by xref."""
    seen = {}
    for page in doc:
        for xref, ext, kind, basefont, name, encoding, *_rest in page.get_fonts(full=True):
            entry = seen.setdefault(xref, {'xref': xref, 'name': basefont or name, 'type': kind,
                                           'encoding': encoding, 'extension': ext,
                                           'embedded': ext not in ('n/a', ''),
                                           'subset': '+' in (basefont or '')[:7], 'pages': []})
            entry['pages'].append(page.number)
    return sorted(seen.values(), key=lambda item: item['name'].lower())


def extract_font(doc, xref):
    """Return (filename, bytes) for an embedded font program."""
    name, ext, _kind, buffer = doc.extract_font(xref)
    if not buffer or ext in ('n/a', ''):
        raise OperationError('This font is not embedded in the PDF.')
    clean = name.split('+', 1)[-1] if '+' in name[:7] else name
    return f'{clean or "font"}.{ext}', buffer


# ---------------------------------------------------------------- XMP

def build_xmp(metadata, existing=''):
    """Produce an XMP packet mirroring the document information dictionary."""
    def esc(value):
        return html.escape(value or '', quote=False)
    title, author = esc(metadata.get('title')), esc(metadata.get('author'))
    subject, keywords = esc(metadata.get('subject')), esc(metadata.get('keywords'))
    creator, producer = esc(metadata.get('creator')), esc(metadata.get('producer'))
    now = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec='seconds')
    return f'''<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about=""
    xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:pdf="http://ns.adobe.com/pdf/1.3/"
    xmlns:xmp="http://ns.adobe.com/xap/1.0/">
   <dc:format>application/pdf</dc:format>
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">{title}</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>{author}</rdf:li></rdf:Seq></dc:creator>
   <dc:description><rdf:Alt><rdf:li xml:lang="x-default">{subject}</rdf:li></rdf:Alt></dc:description>
   <pdf:Keywords>{keywords}</pdf:Keywords>
   <pdf:Producer>{producer}</pdf:Producer>
   <xmp:CreatorTool>{creator}</xmp:CreatorTool>
   <xmp:MetadataDate>{now}</xmp:MetadataDate>
   <xmp:ModifyDate>{now}</xmp:ModifyDate>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>'''


def sync_xmp(doc):
    """Rewrite XMP so it matches the information dictionary (keeps PDF/A tools consistent)."""
    doc.set_xml_metadata(build_xmp(doc.metadata or {}, doc.get_xml_metadata()))
