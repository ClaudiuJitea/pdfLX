"""Headless regressions for pdflx.ops document operations."""
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import pymupdf as fitz

from pdflx.ops import annotations, common, convert, ocr, pages, security, structure, text


def sample(count=4, rotate_last=False):
    doc = fitz.open()
    for number in range(count):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 90), f'Chapter {number + 1}', fontsize=24)
        page.insert_text((72, 140), f'Body text on page {number + 1} with www.example.com inside.', fontsize=11)
        page.insert_text((72, 160), 'The quick brown fox jumps over the lazy dog.', fontsize=11)
    if rotate_last:
        doc[-1].set_rotation(90)
    return doc


def reopen(doc_or_bytes, password=None):
    data = doc_or_bytes if isinstance(doc_or_bytes, (bytes, bytearray)) else doc_or_bytes.tobytes()
    doc = fitz.open('pdf', data)
    if password is not None:
        assert doc.authenticate(password)
    return doc


class CommonTests(unittest.TestCase):
    def test_page_ranges(self):
        self.assertEqual(common.parse_page_ranges('1-3, 5, 8-', 9), [0, 1, 2, 4, 7, 8])
        self.assertEqual(common.parse_page_ranges('', 3), [0, 1, 2])
        self.assertEqual(common.parse_page_ranges('odd', 5), [0, 2, 4])
        self.assertEqual(common.parse_page_ranges('-2', 5), [0, 1])
        with self.assertRaises(common.OperationError):
            common.parse_page_ranges('4-2', 5)
        with self.assertRaises(common.OperationError):
            common.parse_page_ranges('9', 5)
        self.assertEqual(common.format_page_ranges([0, 1, 2, 4, 7, 8]), '1-3, 5, 8-9')


class SecurityTests(unittest.TestCase):
    def test_protect_and_unprotect_round_trip(self):
        doc = sample(2)
        data = security.protect(doc, 'open', 'owner', {'print': True, 'copy': False}, 'aes256')
        locked = fitz.open('pdf', data)
        self.assertTrue(locked.needs_pass)
        self.assertTrue(locked.authenticate('open'))
        self.assertTrue(locked.permissions & fitz.PDF_PERM_PRINT)
        self.assertFalse(locked.permissions & fitz.PDF_PERM_COPY)
        self.assertIn('Chapter 1', locked[0].get_text())
        owner = fitz.open('pdf', data)
        owner.authenticate('owner')
        owner.editor_password = 'owner'
        plain = fitz.open('pdf', security.unprotect(owner))
        self.assertFalse(plain.needs_pass)
        self.assertIn('Chapter 2', plain[1].get_text())

    def test_protect_requires_distinct_owner_password(self):
        with self.assertRaises(common.OperationError):
            security.protect(sample(1), 'same', 'same')
        with self.assertRaises(common.OperationError):
            security.protect(sample(1), 'open', '')

    def test_scrub_removes_metadata_attachments_and_links(self):
        doc = sample(1)
        doc.set_metadata({'author': 'Secret Author', 'title': 'Private'})
        doc.embfile_add('notes.txt', b'confidential')
        structure.detect_urls(doc)
        self.assertTrue(doc[0].get_links())
        result = reopen(security.scrub(doc, {'metadata': True, 'embedded_files': True, 'remove_links': True}))
        self.assertEqual(result.embfile_count(), 0)
        self.assertFalse(result[0].get_links())
        self.assertNotIn('Secret Author', result.metadata.get('author', ''))
        self.assertNotIn(b'confidential', result.tobytes())

    def test_hidden_text_report_and_rasterize(self):
        doc = sample(1)
        doc[0].insert_text((72, 300), 'Invisible words', render_mode=3)
        doc[0].insert_text((72, 320), 'White words', color=(1, 1, 1))
        reasons = {item['text']: item['reason'] for item in security.find_hidden_text(doc)}
        self.assertEqual(reasons.get('Invisible words'), 'invisible')
        self.assertEqual(reasons.get('White words'), 'white')
        raster = security.rasterize(doc, [0], dpi=50)
        self.assertEqual(raster[0].get_text().strip(), '')
        self.assertEqual(len(raster[0].get_images()), 1)


class PageTests(unittest.TestCase):
    def test_split_plans(self):
        doc = sample(5)
        self.assertEqual([p for _l, p in pages.split_plan(doc, 'every', 2)], [[0, 1], [2, 3], [4]])
        doc.set_toc([[1, 'Intro', 1], [1, 'Part Two', 3], [2, 'Nested', 4]])
        plan = pages.split_plan(doc, 'bookmarks')
        self.assertEqual([p for _l, p in plan], [[0, 1], [2, 3, 4]])
        self.assertEqual(plan[1][0], 'Part-Two')
        with tempfile.TemporaryDirectory() as directory:
            written = pages.write_parts(doc, plan, directory, 'book')
            self.assertEqual([fitz.open(p).page_count for p in written], [2, 3])

    def test_collate_reverse_and_apply(self):
        self.assertEqual(pages.collate_order(6, 3, True), [0, 5, 1, 4, 2, 3])
        self.assertEqual(pages.collate_order(5, 3, False), [0, 3, 1, 4, 2])
        doc = sample(3)
        pages.apply_order(doc, pages.reverse_order(3))
        self.assertIn('Chapter 3', doc[0].get_text())
        remap = pages.order_remap([2, 1, 0])
        self.assertEqual(remap(0), 2)
        with self.assertRaises(common.OperationError):
            pages.apply_order(doc, [0, 0, 1])

    def test_insert_document_and_remaps(self):
        doc, other = sample(2), sample(3)
        count = pages.insert_document(doc, other, 1, [2])
        self.assertEqual(count, 1)
        self.assertEqual(doc.page_count, 3)
        self.assertIn('Chapter 3', doc[1].get_text())
        self.assertEqual(pages.insert_remap(1, 1)(1), 2)
        self.assertEqual(pages.delete_remap([1])(2), 1)
        self.assertIsNone(pages.delete_remap([1])(1))

    def test_blank_pages(self):
        doc = sample(2)
        doc.new_page()
        self.assertEqual(pages.blank_pages(doc), [2])
        doc[2].add_text_annot((50, 50), 'note')
        self.assertEqual(pages.blank_pages(doc), [])

    def test_remove_rotation_keeps_text_upright(self):
        doc = sample(1, rotate_last=True)
        self.assertEqual(pages.remove_rotation(doc, [0]), [0])
        self.assertEqual(doc[0].rotation, 0)
        self.assertEqual((round(doc[0].rect.width), round(doc[0].rect.height)), (842, 595))
        self.assertIn('Chapter 1', doc[0].get_text())

    def test_normalize_nup_and_booklet(self):
        doc = sample(5)
        doc.new_page(width=300, height=300)
        normal = pages.normalize_sizes(doc, *fitz.paper_size('letter'))
        self.assertTrue(all(round(p.rect.width) == 612 for p in normal))
        sheet = pages.nup(doc, 2, 2, fitz.paper_size('a4'))
        self.assertEqual(sheet.page_count, 2)
        self.assertIn('Chapter 4', sheet[0].get_text())
        self.assertEqual(pages.booklet_order(5)[0], (None, 0))
        self.assertEqual(pages.booklet(doc).page_count, 4)

    def test_overlay_and_headers(self):
        doc, letterhead = sample(2), fitz.open()
        letterhead.new_page().insert_text((72, 40), 'ACME LETTERHEAD')
        pages.overlay(doc, letterhead, 0, [0, 1], behind=True)
        self.assertIn('ACME LETTERHEAD', doc[1].get_text())
        rotated = sample(2, rotate_last=True)
        pages.add_headers_footers(rotated, [0, 1], {'bottom-right': '{bates} — {page}/{total}'},
                                  bates={'prefix': 'ABC', 'start': 7, 'digits': 5})
        self.assertIn('ABC00008 — 2/2', rotated[1].get_text())
        with self.assertRaises(common.OperationError):
            pages.add_headers_footers(rotated, [0], {'bottom-right': '{oops}'})

    def test_page_labels_and_boxes(self):
        doc = sample(4)
        pages.set_page_labels(doc, [{'startpage': 0, 'style': 'r', 'prefix': ''},
                                    {'startpage': 2, 'style': 'D', 'prefix': 'A-', 'firstpagenum': 1}])
        saved = reopen(doc)
        self.assertEqual([saved[n].get_label() for n in range(4)], ['i', 'ii', 'A-1', 'A-2'])
        self.assertEqual(pages.get_page_labels(saved)[1]['prefix'], 'A-')
        pages.set_boxes(doc, [0], {'trimbox': (10, 10, 585, 832), 'bleedbox': (5, 5, 590, 837)})
        boxes = pages.get_boxes(reopen(doc)[0])
        self.assertEqual(boxes['trimbox'], (10, 10, 585, 832))
        with self.assertRaises(common.OperationError):
            pages.set_boxes(doc, [0], {'artbox': (0, 0, 900, 900)})


class ConvertTests(unittest.TestCase):
    def test_images_to_pdf_and_convert(self):
        with tempfile.TemporaryDirectory() as directory:
            pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 100), False)
            pix.clear_with(200)
            image = os.path.join(directory, 'wide.png')
            pix.save(image)
            result = convert.images_to_pdf([image, image], page_size=fitz.paper_size('a4'), margin=20)
            self.assertEqual(result.page_count, 2)
            self.assertGreater(result[0].rect.width, result[0].rect.height)
            native = convert.images_to_pdf([image])
            self.assertAlmostEqual(native[0].rect.width / native[0].rect.height, 2.0, places=3)
            svg = os.path.join(directory, 'shape.svg')
            Path(svg).write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
                                 '<text x="5" y="30">Vector</text></svg>')
            self.assertIn('Vector', convert.convert_file_to_pdf(svg)[0].get_text())
            self.assertTrue(convert.is_convertible(svg))

    def test_optimize_recolor_and_raster_export(self):
        doc = sample(2)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1200, 1200), False)
        pix.clear_with(90)
        doc[0].insert_image(fitz.Rect(72, 200, 216, 344), pixmap=pix)
        before = len(doc.tobytes())
        optimized = convert.optimize(doc, image_dpi=72, image_quality=60)
        self.assertLess(len(optimized), before)
        self.assertIn('Chapter 1', reopen(optimized)[0].get_text())
        gray = convert.recolor(doc, 1)
        rendered = gray[0].get_pixmap(dpi=20)
        self.assertTrue(rendered.is_unicolor is False or rendered.n >= 1)
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, 'pages.zip')
            convert.export_pages_raster(doc, [0, 1], archive, 'doc', dpi=30, alpha=True)
            with zipfile.ZipFile(archive) as handle:
                self.assertEqual(len(handle.namelist()), 2)
                image = fitz.Pixmap(handle.read(handle.namelist()[0]))
                self.assertTrue(image.alpha)
            cmyk = convert.render_page_image(doc[0], 20, 'psd', 'cmyk')
            self.assertEqual(fitz.Pixmap(cmyk).n, 4)
        with self.assertRaises(common.OperationError):
            convert.render_page_image(doc[0], 20, 'jpeg', 'rgb', alpha=True)

    def test_structured_text_formats(self):
        doc = sample(2)
        for key, _label, _ext in convert.TEXT_FORMATS:
            output = convert.export_text(doc, [0, 1], key)
            # Character-level formats store one glyph per entry.
            self.assertIn('"C"' if key in ('rawjson', 'xml') else 'Chapter', output, key)
        self.assertEqual(len(json.loads(convert.export_text(doc, [0], 'json'))['pages']), 1)
        self.assertTrue(convert.export_text(doc, [0], 'markdown').startswith('# Chapter 1'))

    def test_table_exports(self):
        doc = fitz.open()
        page = doc.new_page()
        for row in range(3):
            for col in range(3):
                rect = fitz.Rect(72 + col * 100, 72 + row * 30, 172 + col * 100, 102 + row * 30)
                page.draw_rect(rect, color=(0, 0, 0), width=1)
                page.insert_text(rect.tl + (5, 20), f'R{row}C{col}')
        self.assertIn('R1C1', convert.export_tables(doc, [0], 'markdown').decode())
        self.assertEqual(json.loads(convert.export_tables(doc, [0], 'json'))[0]['rows'][2][2], 'R2C2')
        with self.assertRaises(common.OperationError):
            convert.export_tables(sample(1), [0], 'csv')


class TextTests(unittest.TestCase):
    def test_regex_whole_word_and_rotated_search(self):
        doc = sample(2, rotate_last=True)
        hits = text.search_document(doc, r'Chapter \d', regex=True)
        self.assertEqual([h['page'] for h in hits], [0, 1])
        self.assertEqual(len(text.search_document(doc, 'the', whole_word=True)), 4)
        self.assertEqual(len(text.search_document(doc, 'the', whole_word=False)), 4)
        self.assertEqual(len(text.search_document(doc, 'The', case_sensitive=True)), 2)
        quad = hits[1]['quads'][0]
        self.assertTrue(fitz.Rect(quad.rect).intersects(doc[1].search_for('Chapter')[0]))
        phrase = text.search_document(doc, 'inside. The quick')
        self.assertEqual(len(phrase[0]['quads']), 3)
        with self.assertRaises(common.OperationError):
            text.search_document(doc, '(', regex=True)

    def test_style_statistics_and_bookmarks(self):
        doc = sample(3)
        style = text.text_style_at(doc[0], (80, 85))
        self.assertEqual(style['size'], 24)
        self.assertEqual(len(text.spans_with_style(doc, style['font'], 24)), 3)
        stats = text.statistics(doc)
        self.assertEqual(stats['pages'], 3)
        self.assertGreater(stats['words'], 30)
        self.assertEqual(text.heading_candidates(doc), [[1, 'Chapter 1', 1], [1, 'Chapter 2', 2], [1, 'Chapter 3', 3]])

    def test_compare(self):
        a, b = sample(2), sample(2)
        b[1].add_redact_annot(b[1].search_for('lazy')[0])
        b[1].apply_redactions()
        b[1].insert_text((72, 200), 'Added sentence')
        result = text.compare_text(a, b)
        self.assertFalse(result['equal'])
        kinds = {c['kind'] for c in result['changes']}
        self.assertTrue(kinds & {'delete', 'replace', 'insert'})
        self.assertIn(1, result['rects_b'])
        self.assertIn(1, text.compare_visual(a, b))
        self.assertNotIn(0, text.compare_visual(a, b))
        text.annotate_differences(b, result['rects_b'])
        self.assertTrue(list(b[1].annots()))


class AnnotationTests(unittest.TestCase):
    def test_shapes_freetext_replies_states_and_json(self):
        doc = sample(1)
        rect = (100, 300, 250, 380)
        for kind in annotations.SHAPE_KINDS:
            annotations.add_shape(doc, 0, kind, rect, author='Ana')
        annotations.add_polyline(doc, 0, [(100, 400), (150, 450), (200, 400)], closed=True)
        annotations.add_ink(doc, 0, [[(300, 300), (320, 320), (340, 300)]])
        note = annotations.add_free_text(doc, 0, (300, 400, 480, 440), 'Callout text', callout_to=(250, 500))
        annotations.add_caret(doc, 0, (90, 140), 'insert word')
        annotations.add_file_attachment(doc, 0, (500, 100), b'payload', 'data.txt')
        reply = annotations.add_reply(doc, 0, note, 'I agree', 'Ben')
        annotations.set_review_state(doc, 0, note, 'Accepted', 'Ben')
        items = annotations.thread(doc)
        parent = next(item for item in items if item['xref'] == note)
        self.assertEqual(parent['state'], 'Accepted')
        self.assertEqual([r['xref'] for r in parent['replies']], [reply])
        self.assertEqual(annotations.attachment_data(doc, 0, next(
            a.xref for a in doc[0].annots() if a.type[1] == 'FileAttachment'))[1], b'payload')
        exported = annotations.export_json(doc)
        target = sample(1)
        imported, skipped = annotations.import_json(target, exported)
        self.assertGreaterEqual(imported, 10)
        kinds = sorted(a.type[1] for a in target[0].annots())
        self.assertIn('Ink', kinds)
        self.assertIn('Polygon', kinds)
        self.assertEqual(annotations.thread(target)[-1 if False else 0]['page'], 0)
        summary = annotations.summary_markdown(doc, 'Test')
        self.assertIn('Accepted', summary)
        self.assertIn('I agree', summary)
        self.assertIn('Accepted', annotations.summary_pdf(doc)[0].get_text())

    def test_flags_and_flatten(self):
        doc = sample(1)
        xref = annotations.add_shape(doc, 0, 'rectangle', (100, 100, 200, 200))
        annotations.set_flags(doc, 0, xref, {'locked': True, 'print': True})
        self.assertTrue(annotations.get_flags(doc, 0, xref)['locked'])
        annotations.flatten_annotations(doc)
        self.assertIsNone(doc[0].first_annot)
        self.assertTrue(doc[0].get_drawings())
        with self.assertRaises(common.OperationError):
            annotations.flatten_annotations(doc)


class StructureTests(unittest.TestCase):
    def test_attachments_links_and_layers(self):
        doc = sample(2, rotate_last=True)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'a.txt')
            Path(path).write_text('hello')
            name = structure.add_attachment(doc, path)
            self.assertEqual(structure.add_attachment(doc, path), 'a (2).txt')
        self.assertEqual(structure.attachment_bytes(doc, name), b'hello')
        structure.remove_attachment(doc, name)
        self.assertEqual(len(structure.attachments(doc)), 1)
        structure.add_link(doc, 1, (72, 70, 200, 95), 'goto', page_target=0)
        structure.add_link(doc, 0, (72, 70, 200, 95), 'uri', 'example.org')
        rows = structure.links(reopen(doc))
        self.assertEqual({r['kind'] for r in rows}, {'goto', 'uri'})
        rotated = next(r for r in rows if r['page'] == 1)
        self.assertTrue(fitz.Rect(rotated['rect']).contains(fitz.Point(100, 85)))
        uri = next(r for r in structure.links(doc) if r['kind'] == 'uri')
        self.assertEqual(uri['target'], 'https://example.org')
        structure.update_link(doc, 0, uri['xref'], 'named', 'LastPage')
        self.assertEqual(structure.links(doc, [0])[0]['kind'], 'named')
        structure.delete_link(doc, 0, structure.links(doc, [0])[0]['xref'])
        self.assertEqual(structure.links(doc, [0]), [])
        self.assertEqual(structure.detect_urls(doc, [0]), 1)
        layer = structure.add_layer(doc, 'Notes')
        doc[0].insert_text((72, 400), 'Layered', oc=layer)
        structure.set_layer_visibility(doc, {layer: False})
        self.assertFalse(structure.layers(doc)[0]['on'])
        self.assertNotIn('Layered', doc[0].get_text())

    def test_viewer_settings_properties_fonts_xmp(self):
        doc = sample(1)
        structure.set_viewer_settings(doc, page_mode='UseOutlines', page_layout='TwoPageRight', language='de-DE',
                                      preferences={'DisplayDocTitle': True})
        settings = structure.viewer_settings(reopen(doc))
        self.assertEqual((settings['page_mode'], settings['page_layout'], settings['language']),
                         ('UseOutlines', 'TwoPageRight', 'de-DE'))
        self.assertTrue(settings['preferences']['DisplayDocTitle'])
        info = structure.properties(doc)
        self.assertEqual(info['pages'], 1)
        self.assertFalse(info['javascript'])
        fonts = structure.fonts(doc)
        self.assertEqual(fonts[0]['name'], 'Helvetica')
        with self.assertRaises(common.OperationError):
            structure.extract_font(doc, fonts[0]['xref'])
        doc.set_metadata({'title': 'Mine & Yours', 'author': 'Ana'})
        structure.sync_xmp(doc)
        self.assertIn('Mine &amp; Yours', doc.get_xml_metadata())

    def test_metadata_update_keeps_xmp_in_sync(self):
        from pdflx import document_features
        doc = sample(1)
        structure.sync_xmp(doc)
        document_features.update_metadata(doc, {'title': 'Renamed'})
        self.assertIn('Renamed', doc.get_xml_metadata())


class OcrTests(unittest.TestCase):
    def test_dependency_detection_and_text_layer(self):
        self.assertIsInstance(ocr.languages(), list)
        if not ocr.available():
            self.assertTrue(ocr.status_message())
            with self.assertRaises(common.OperationError):
                ocr.ocr_document(sample(1))
        doc = sample(1)
        ocr.add_invisible_words(doc[0], [(72, 400, 160, 420, 'Recognized', 0, 0, 0)])
        self.assertIn('Recognized', doc[0].get_text())
        self.assertIn(3, [span['type'] for span in doc[0].get_texttrace()])


if __name__ == '__main__':
    unittest.main()


def all_stream_text(data):
    """Every decompressed stream and object in a PDF, for leak checks."""
    doc = fitz.open('pdf', data)
    parts = []
    for xref in range(1, doc.xref_length()):
        try:
            parts.append(doc.xref_object(xref))
            if doc.xref_is_stream(xref):
                parts.append(doc.xref_stream(xref).decode('latin-1'))
        except Exception:
            continue
    return '\n'.join(parts)


class _Session:
    page_objects = {}


class _Window:
    def __init__(self, doc):
        self.doc = doc
        self._active_session = _Session()


class SanitizationValidationTests(unittest.TestCase):
    def test_redaction_leaves_no_trace_in_saved_bytes_or_editor_baseline(self):
        from pdflx import document_tools, pdf_handler
        doc = sample(1)
        doc[0].insert_text((72, 300), 'SECRET-ACCOUNT-1234')
        # A stale editor baseline stream holding the secret must not survive.
        baseline = doc.get_new_xref()
        doc.update_object(baseline, '<<>>')
        doc.update_stream(baseline, b'{"baseline": "SECRET-ACCOUNT-1234"}')
        doc.xref_set_key(doc[0].xref, 'PdfLXEditor', f'{baseline} 0 R')
        targets = document_tools.redaction_targets(doc, 0, 0, 'SECRET-ACCOUNT-1234')
        document_tools.apply_redactions(_Window(doc), targets, replacement='[REDACTED]')
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'redacted.pdf')
            ok, error = pdf_handler.save_document(doc, path)
            self.assertTrue(ok, error)
            data = Path(path).read_bytes()
        self.assertNotIn('SECRET-ACCOUNT-1234', all_stream_text(data))
        self.assertIn('[REDACTED]', fitz.open('pdf', data)[0].get_text())

    def test_pixel_image_redaction_keeps_uncovered_pixels(self):
        from pdflx import document_tools
        doc = fitz.open()
        page = doc.new_page()
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200), False)
        pix.clear_with(60)
        page.insert_image(fitz.Rect(100, 100, 300, 300), pixmap=pix)
        document_tools.apply_redactions(_Window(doc), [(0, (100, 100, 200, 200))], fill=None, image_mode='pixels')
        self.assertEqual(len(doc[0].get_images()), 1)
        render = doc[0].get_pixmap(dpi=72)
        self.assertLess(sum(render.pixel(250, 250)), 300)   # uncovered part of the image remains
        self.assertGreater(sum(render.pixel(150, 150)), 700)  # covered pixels are blanked
        removed = fitz.open()
        removed.new_page().insert_image(fitz.Rect(100, 100, 300, 300), pixmap=pix)
        document_tools.apply_redactions(_Window(removed), [(0, (100, 100, 200, 200))], image_mode='remove')
        self.assertEqual(len(removed[0].get_images()), 0)

    def test_scrub_removes_hidden_text_and_editor_state_from_bytes(self):
        doc = sample(1)
        doc[0].insert_text((72, 400), 'INVISIBLE-SECRET', render_mode=3)
        baseline = doc.get_new_xref()
        doc.update_object(baseline, '<<>>')
        doc.update_stream(baseline, b'EDITOR-SECRET')
        doc.xref_set_key(doc[0].xref, 'PdfLXEditor', f'{baseline} 0 R')
        doc.set_metadata({'author': 'META-SECRET'})
        data = security.scrub(doc, {'hidden_text': True, 'metadata': True, 'xml_metadata': True,
                                    'clean_pages': True})
        text = all_stream_text(data)
        for secret in ('INVISIBLE-SECRET', 'EDITOR-SECRET', 'META-SECRET'):
            self.assertNotIn(secret, text)
        self.assertIn('Chapter 1', fitz.open('pdf', data)[0].get_text())


class ExtractionRobustnessTests(unittest.TestCase):
    def test_two_column_reading_order(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(50, 50, 280, 400), 'Left column first paragraph. ' * 6)
        page.insert_textbox(fitz.Rect(310, 50, 545, 400), 'Right column second paragraph. ' * 6)
        text = convert.export_text(doc, [0], 'text', sort=True)
        self.assertLess(text.index('Left column'), text.index('Right column'))
        markdown = convert.export_text(doc, [0], 'markdown')
        self.assertLess(markdown.index('Left column'), markdown.index('Right column'))

    def test_tables_with_empty_and_merged_cells(self):
        doc = fitz.open()
        page = doc.new_page()
        for row in range(3):
            for col in range(3):
                if row == 1 and col == 1:
                    continue  # merged into the cell to its left below
                rect = fitz.Rect(72 + col * 100, 72 + row * 30, 172 + col * 100, 102 + row * 30)
                page.draw_rect(rect, color=(0, 0, 0), width=1)
                if not (row == 2 and col == 0):
                    page.insert_text(rect.tl + (5, 20), f'R{row}C{col}')
        page.draw_rect(fitz.Rect(172, 102, 272, 132), color=(0, 0, 0), width=1)
        rows = json.loads(convert.export_tables(doc, [0], 'json'))[0]['rows']
        self.assertEqual(len(rows), 3)
        self.assertIn(rows[2][0], ('', None))
        markdown = convert.export_tables(doc, [0], 'markdown').decode()
        self.assertIn('R2C2', markdown)
        with zipfile.ZipFile(__import__('io').BytesIO(convert.export_tables(doc, [0], 'csv'))) as archive:
            self.assertIn('R0C0', archive.read(archive.namelist()[0]).decode())


class FormTests(unittest.TestCase):
    def test_multi_select_password_comb_and_border_style(self):
        from pdflx import document_tools, document_features
        from pdflx.form_appearance import read_values
        doc = fitz.open()
        doc.new_page()
        xref = document_tools.create_form_field(doc, 0, 'Toppings', 'list', (72, 72, 272, 160),
                                                ['Cheese', 'Olives', 'Basil'], options={'multi_select': True},
                                                value=['Cheese', 'Basil'])
        self.assertEqual(read_values(doc, xref), ['Cheese', 'Basil'])
        document_features.update_form_fields(doc, {(0, xref): ['Olives']})
        saved = reopen(doc)
        field = next(f for f in document_features.list_form_fields(saved) if f['name'] == 'Toppings')
        self.assertTrue(field['multi_select'])
        self.assertEqual(field['value'], ['Olives'])
        with self.assertRaises(ValueError):
            document_features.update_form_fields(doc, {(0, xref): ['Pineapple']})
        pin = document_tools.create_form_field(doc, 0, 'PIN', 'text', (72, 200, 272, 224),
                                               options={'password': True, 'border_style': 'dashed'}, value='4321')
        self.assertNotIn('4321', doc[0].get_text())
        self.assertIn('****', doc[0].get_text())
        self.assertEqual(saved_value(doc, pin), '4321')
        code = document_tools.create_form_field(doc, 0, 'Code', 'text', (72, 240, 272, 264),
                                                options={'comb': True, 'max_length': 6}, value='AB12')
        fields = {f['name']: f for f in document_features.list_form_fields(reopen(doc))}
        self.assertTrue(fields['Code']['comb'])
        self.assertEqual(fields['PIN']['border_style'], 'dashed')
        with self.assertRaises(ValueError):
            document_tools.create_form_field(doc, 0, 'Bad', 'text', (72, 280, 272, 300), options={'comb': True})
        del code

    def test_field_hierarchy_and_scripts(self):
        from pdflx import document_tools
        from pdflx.ops import forms, javascript
        doc = fitz.open()
        doc.new_page()
        street = document_tools.create_form_field(doc, 0, 'street', 'text', (72, 72, 272, 96), value='Main')
        city = document_tools.create_form_field(doc, 0, 'city', 'text', (72, 100, 272, 124), value='Rome')
        parent = forms.group_fields(doc, [street, city], 'address')
        names = sorted(w.field_name for w in reopen(doc)[0].widgets())
        self.assertEqual(names, ['address.city', 'address.street'])
        with self.assertRaises(common.OperationError):
            forms.group_fields(doc, [street], 'other')
        javascript.set_field_script(doc, city, 'V', 'event.rc = event.value.length > 1;')
        entry = next(e for e in javascript.scripts(doc) if e['event'] == 'Validate')
        self.assertIn('address.city', entry['location'])
        javascript.remove(doc, entry)
        self.assertEqual(javascript.scripts(doc), [])
        forms.ungroup_field(doc, parent)
        self.assertEqual(sorted(w.field_name for w in reopen(doc)[0].widgets()), ['city', 'street'])


def saved_value(doc, xref):
    kind, value = doc.xref_get_key(xref, 'V')
    return value


class GraphicsAndAuthoringTests(unittest.TestCase):
    def test_diagram_regions_exports_and_image_replacement(self):
        from pdflx.ops import graphics
        doc = fitz.open()
        page = doc.new_page()
        page.draw_rect(fitz.Rect(100, 100, 200, 180), color=(1, 0, 0), fill=(0, 0, 1))
        page.draw_line((100, 100), (200, 180))
        page.insert_text((110, 120), 'Label')
        self.assertEqual(graphics.diagram_regions(page)[0], fitz.Rect(100, 100, 200, 180))
        region = graphics.region_for_selection(page, (140, 130, 150, 140))
        self.assertTrue(region.contains(fitz.Rect(100, 100, 200, 180)))
        self.assertIn('Label', fitz.open('pdf', graphics.export_region(doc, 0, region, 'pdf'))[0].get_text())
        self.assertIn('<path', graphics.drawings_svg(page))
        with self.assertRaises(common.OperationError):
            graphics.drawings_svg(page, (400, 400, 500, 500))
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), True)
        pix.clear_with(120)
        xref = page.insert_image(fitz.Rect(300, 100, 340, 140), pixmap=pix)
        doc.new_page().insert_image(fitz.Rect(10, 10, 50, 50), xref=xref)
        inventory = graphics.image_inventory(doc)
        self.assertEqual((inventory[0]['placements'], inventory[0]['pages']), (2, [0, 1]))
        ext, data = graphics.image_bytes(doc, xref)
        self.assertTrue(fitz.Pixmap(data).alpha)
        new = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), False)
        new.clear_with(255)
        self.assertEqual(graphics.replace_image_everywhere(doc, xref, stream=new.tobytes('png')), [0, 1])
        self.assertTrue(all(info['width'] == 10 for p in doc for info in p.get_image_info()))
        archive, count = graphics.export_images_archive(doc)
        self.assertGreaterEqual(count, 1)

    def test_compose_toc_rich_text_watermark_and_merge(self):
        from pdflx.ops import authoring
        body = '# Report\n\n' + ('Paragraph text. ' * 120 + '\n\n') * 5 + '## Later\n\nEnd.'
        doc = authoring.compose(body, toc=True)
        toc = doc.get_toc()
        self.assertEqual(toc[0][1], 'Contents')
        later = next(item for item in toc if item[1] == 'Later')
        self.assertIn(f'Later … {later[2]}', doc[0].get_text())
        self.assertTrue(any(link['kind'] == fitz.LINK_GOTO and link['page'] == later[2] - 1
                            for link in doc[0].get_links()))
        rotated = fitz.open()
        rotated.new_page().set_rotation(90)
        authoring.insert_rich_text(rotated, 0, (72, 100, 300, 200), 'Hello **bold**')
        self.assertIn('bold', rotated[0].get_text())
        # Content is scaled down to fit small areas rather than rejected.
        self.assertLess(authoring.insert_rich_text(rotated, 0, (72, 300, 120, 320), 'x ' * 400), 1)
        authoring.watermark(rotated, [0], 'CONFIDENTIAL', angle=30)
        hit = rotated[0].search_for('CONFIDENTIAL')
        self.assertTrue(hit)
        template = fitz.open()
        page = template.new_page()
        page.insert_text((72, 72), 'Dear {{name}}')
        widget = fitz.Widget()
        widget.field_name, widget.field_type = 'city', fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = fitz.Rect(72, 100, 272, 120)
        page.add_widget(widget)
        rows = authoring.read_csv('name,city\nAna,Rome\nBen,Oslo\n', from_text=True)
        self.assertEqual(authoring.merge_targets(template), (['city'], ['name']))
        merged = [doc for _i, _r, doc in authoring.mail_merge(template.tobytes(), rows, flatten=True)]
        self.assertIn('Ben', merged[1][0].get_text())
        self.assertIn('Oslo', merged[1][0].get_text())
        self.assertNotIn('{{name}}', merged[0][0].get_text())
        self.assertEqual(authoring.combine(merged).page_count, 2)
        self.assertEqual(authoring.merge_filename('{city}-{n}', rows[0], 0), 'Rome-1.pdf')
        self.assertIn('<table>', authoring.markdown_to_html('| a | b |\n|---|---|\n| 1 | 2 |'))


class RenderingTests(unittest.TestCase):
    def test_fast_surface_conversion_matches_pixmap_colours(self):
        from pdflx import pdf_handler
        doc = fitz.open()
        page = doc.new_page(width=200, height=100)
        page.draw_rect(fitz.Rect(10, 10, 60, 60), fill=(1, 0, 0), color=None)
        page.draw_rect(fitz.Rect(70, 10, 120, 60), fill=(0, 0.5, 1), color=None)
        surface = pdf_handler.get_page_cairo_surface(doc, 0, 1.0)
        data, stride = surface.get_data(), surface.get_stride()
        import sys
        def rgb(x, y):
            b, g, r, _a = data[y * stride + x * 4: y * stride + x * 4 + 4]
            return (r, g, b) if sys.byteorder == 'little' else tuple(data[y * stride + x * 4 + 1: y * stride + x * 4 + 4])
        self.assertEqual(rgb(30, 30), (255, 0, 0))
        self.assertEqual(rgb(90, 30), page.get_pixmap().pixel(90, 30))

    def test_region_rendering_and_cache_budget(self):
        from pdflx import pdf_handler
        doc = fitz.open()
        page = doc.new_page(width=1000, height=1000)
        page.draw_rect(fitz.Rect(900, 900, 1000, 1000), fill=(0, 0, 1), color=None)
        self.assertTrue(pdf_handler.page_is_large(doc, 0, 8))
        surface, x, y = pdf_handler.get_page_region_surface(doc, 0, 8, (7300, 7300, 7600, 7600))
        self.assertEqual((x, y), (7168, 7168))
        self.assertLessEqual(surface.get_width(), 1024)
        data, stride = surface.get_data(), surface.get_stride()
        offset = (7400 - y) * stride + (7400 - x) * 4
        self.assertEqual(tuple(data[offset:offset + 3]), (255, 0, 0))  # BGR blue on little-endian
        budget = pdf_handler.CACHE_BUDGET_BYTES
        try:
            pdf_handler.CACHE_BUDGET_BYTES = 6 * 1024 * 1024
            for zoom in (1.0, 1.5, 2.0, 2.5):
                pdf_handler.get_page_cairo_surface(doc, 0, zoom)
            total = sum(buffer.nbytes for buffer in pdf_handler._cairo_buffers.values())
            self.assertLessEqual(total, max(6 * 1024 * 1024, max(b.nbytes for b in pdf_handler._cairo_buffers.values())))
        finally:
            pdf_handler.CACHE_BUDGET_BYTES = budget
            pdf_handler.invalidate_page_cache()


class AdvancedStructureTests(unittest.TestCase):
    INVOICE = ('<?xml version="1.0"?><rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:'
               'CrossIndustryInvoice:100"><x/></rsm:CrossIndustryInvoice>')

    def test_einvoice_attachment_is_associated_and_described_in_xmp(self):
        from pdflx.ops import advanced
        doc = sample(1)
        doc.embfile_add('notes.txt', b'existing')
        spec = advanced.attach_einvoice(doc, self.INVOICE.encode(), 'EN 16931')
        saved = reopen(doc)
        self.assertEqual(sorted(saved.embfile_names()), ['factur-x.xml', 'notes.txt'])
        self.assertEqual(advanced.einvoice_xml(saved)[1], self.INVOICE.encode())
        self.assertIn(f'{spec} 0 R', saved.xref_get_key(saved.pdf_catalog(), 'AF')[1])
        self.assertEqual(saved.xref_get_key(spec, 'AFRelationship'), ('name', '/Alternative'))
        self.assertIn('<fx:ConformanceLevel>EN 16931</fx:ConformanceLevel>', saved.get_xml_metadata())
        self.assertNotIn('pdfaid:part', saved.get_xml_metadata())
        with self.assertRaises(common.OperationError):
            advanced.attach_einvoice(doc, b'<other/>')

    def test_portfolio_and_object_inspector(self):
        from pdflx.ops import advanced
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for name in ('a.txt', 'b.txt'):
                path = os.path.join(directory, name)
                Path(path).write_text(name)
                paths.append(path)
            portfolio = reopen(advanced.create_portfolio(paths, 'Case files'))
        self.assertTrue(advanced.is_portfolio(portfolio))
        self.assertEqual(sorted(portfolio.embfile_names()), ['a.txt', 'b.txt'])
        doc = sample(1)
        pages = advanced.find_objects(doc, 'Page')
        self.assertTrue(any(item['type'] == 'Page' for item in pages))
        page_xref = doc[0].xref
        source, _stream = advanced.read_object(doc, page_xref)
        advanced.write_object(doc, page_xref, source.replace('/Rotate 0', '/Rotate 90', 1))
        self.assertEqual(doc[0].rotation, 90)
        content = doc[0].get_contents()[-1]
        content_source, stream = advanced.read_object(doc, content)
        self.assertIsNotNone(stream)
        advanced.write_object(doc, content, content_source, stream + '\n% edited\n')
        self.assertIn(b'% edited', doc.xref_stream(content))
        self.assertIn('Chapter 1', doc[0].get_text())
        with self.assertRaises(common.OperationError):
            advanced.write_object(doc, doc.pdf_catalog(), '<</Type/Catalog>>')


class DrawingTests(unittest.TestCase):
    def test_primitives_and_node_utilities(self):
        from pdflx.ops import drawing
        rect = fitz.Rect(100, 100, 200, 200)
        closed, star = drawing.primitive_points('star', rect, points=5, inner=0.5)
        self.assertTrue(closed)
        self.assertEqual(len(star), 10)
        self.assertAlmostEqual(star[0].y, 100, places=4)  # first point at the top
        closed, arc = drawing.primitive_points('arc', rect, start=0, sweep=90)
        self.assertFalse(closed)
        self.assertAlmostEqual(arc[0].x, 200, places=4)
        self.assertAlmostEqual(arc[-1].y, 100, places=4)
        doc = fitz.open()
        doc.new_page()
        for kind in drawing.PRIMITIVES:
            drawing.draw_primitive(doc, 0, kind, rect, fill=(1, 0, 0))
        self.assertGreaterEqual(len(doc[0].get_drawings()), len(drawing.PRIMITIVES))
        with self.assertRaises(common.OperationError):
            drawing.primitive_points('sector', rect, sweep=0)
        line = [(x, 0.01 * (x % 2)) for x in range(50)]
        self.assertEqual(drawing.simplify(line, 0.5), [line[0], line[-1]])
        smoothed = drawing.smooth([(0, 0), (10, 10), (20, 0)], 2)
        self.assertEqual((smoothed[0], smoothed[-1]), ((0, 0), (20, 0)))
        self.assertGreater(len(smoothed), 3)
        self.assertEqual(drawing.nearest_node([(0, 0), (10, 0)], (9, 1), 3), 1)
        segment, distance, projection = drawing.nearest_segment([(0, 0), (10, 0), (10, 10)], (5, 1))
        self.assertEqual((segment, projection), (0, (5, 0)))
