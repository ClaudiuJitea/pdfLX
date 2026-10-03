"""Run with: python -m unittest discover -s tests -v."""
import io
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import pymupdf as fitz
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.writer import copy_into_new_writer
from pyhanko.sign.fields import SigFieldSpec, append_signature_field
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

from pdflx import signatures


class SignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'pdfLX Test')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=30))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True,
                               key_encipherment=False, data_encipherment=False, key_agreement=False,
                               key_cert_sign=True, crl_sign=True, encipher_only=False,
                               decipher_only=False), True).sign(key, hashes.SHA256()))
        cls.pfx = pkcs12.serialize_key_and_certificates(
            b'pdfLX', key, cert, None, serialization.BestAvailableEncryption(b'secret'))
        cls.trust_root = asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.certificate = self.path / 'test.p12'
        self.certificate.write_bytes(self.pfx)
        self.doc = fitz.open()
        self.doc.new_page(width=595, height=842)
        self.doc[0].insert_text((30, 30), 'Latest document edits')
        self.addCleanup(self.doc.close)

    def signed_copy(self, data=None, **options):
        output = self.path / 'signed.pdf'
        signatures.sign_pdf_copy(data or self.doc.tobytes(), output,
                                 self.certificate, 'secret', **options)
        return output

    def assert_valid(self, data, password=None, count=1):
        reader = PdfFileReader(io.BytesIO(data), strict=False)
        if password:
            reader.decrypt(password)
        self.assertEqual(len(reader.embedded_signatures), count)
        context = ValidationContext(trust_roots=[self.trust_root], allow_fetching=False)
        for signature in reader.embedded_signatures:
            result = validate_pdf_signature(signature, signer_validation_context=context)
            self.assertTrue(result.intact and result.valid and result.trusted, result.summary())
        return reader

    def test_visible_signature_transparency_and_rotated_placement(self):
        png = signatures.drawn_signature_png([[(20, 30), (45, 10), (40, 50), (100, 30)]])
        pixmap = fitz.Pixmap(png)
        self.assertEqual(pixmap.alpha, 1)
        self.assertEqual(pixmap.samples[3], 0)
        self.assertLess(pixmap.width, 1200)
        self.assertEqual(signatures.signature_image_png(png)[:8], b'\x89PNG\r\n\x1a\n')
        for angle in (0, 90, 180, 270):
            self.doc[0].set_rotation(angle)
            page = self.doc[0]
            bbox, rotation = signatures.signature_placement(page, png, 999, 999)
            self.assertTrue(fitz.Rect(bbox) in page.rect * page.derotation_matrix)
            self.assertEqual(rotation, angle)
        with self.assertRaises(signatures.SignatureError):
            signatures.drawn_signature_png([])
        with self.assertRaises(signatures.SignatureError):
            signatures.signature_image_png(b'invalid')

    def test_certificate_signature_includes_current_edits(self):
        original = self.path / 'original.pdf'
        self.doc.save(original)
        self.doc[0].insert_text((30, 60), 'Unsaved change')
        output = self.signed_copy(signatures.prepare_signing_pdf(self.doc, original, True),
                                  source_path=original)
        self.assert_valid(output.read_bytes())
        with fitz.open(output) as signed:
            self.assertIn('Unsaved change', signed[0].get_text())
        with fitz.open(original) as untouched:
            self.assertNotIn('Unsaved change', untouched[0].get_text())

    def test_created_signature_field_can_be_signed(self):
        from pdflx.document_tools import create_form_field
        create_form_field(self.doc,0,'Approval','signature',(80,100,440,212))
        output=self.signed_copy(field_name='Approval',template='Modern',reason='Reviewed')
        self.assert_valid(output.read_bytes())
        with fitz.open(output) as signed:
            page=signed[0];widget=next(page.widgets())
            self.assertTrue(widget.is_signed)
            self.assertEqual(widget.field_name,'Approval')
            self.assertEqual(tuple(widget.rect),(80,100,440,212))
            self.assertIn('pdfLX Test',page.get_text())

    def test_unsigned_signature_can_duplicate_but_signed_field_cannot(self):
        from pdflx.document_tools import create_form_field
        from pdflx.form_duplication import duplicate_form_field
        from pdflx.document_features import list_form_fields
        xref=create_form_field(self.doc,0,'Approval','signature',(80,100,440,212))
        duplicate_form_field(self.doc,0,xref)
        self.assertEqual([f['name'] for f in list_form_fields(self.doc)],['Approval','Approval copy'])
        output=self.signed_copy(field_name='Approval',template='Modern')
        with fitz.open(output) as signed:
            field=list_form_fields(signed)[0];before=signed.xref_length()
            with self.assertRaisesRegex(ValueError,'signed certificate'):
                duplicate_form_field(signed,0,field['xref'])
            self.assertEqual(signed.xref_length(),before)

    def test_visible_certificate_templates_are_signed_and_inside_field(self):
        for template in ('Modern','Minimal','Formal'):
            with self.subTest(template=template):
                output=self.signed_copy(placement=dict(page=0,rect=(80,100,440,212)),
                                        template=template,reason='Approved for release')
                self.assert_valid(output.read_bytes())
                with fitz.open(output) as signed:
                    page=signed[0]
                    widget=next(page.widgets())
                    self.assertTrue(widget.is_signed)
                    self.assertEqual(tuple(widget.rect),(80,100,440,212))
                    text=page.get_text()
                    self.assertIn('Latest document edits',text)
                    self.assertIn('pdfLX Test',text)
                    self.assertIn('Approved for release',text)
                    # The appearance is a native signature widget, including extractable text.
                    pix=page.get_pixmap(clip=widget.rect,annots=True)
                    white=page.get_pixmap(clip=widget.rect,annots=False)
                    self.assertNotEqual(pix.samples,white.samples)
                    kind,value=signed.xref_get_key(widget.xref,'AP/N')
                    self.assertEqual(kind,'xref')
                    ap=signed.xref_stream(int(value.split()[0]))
                    self.assertTrue(ap)
                    self.assertIn(b'/XObject',signed.xref_object(int(value.split()[0])).encode())
                    if template=='Modern':
                        page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),clip=widget.rect).save('/tmp/pdflx-signed-certificate.png')

    def test_visible_signature_placement_on_rotated_cropped_page(self):
        for rotation in (0,90,180,270):
            with self.subTest(rotation=rotation):
                page=self.doc[0]
                page.set_cropbox(fitz.Rect(30,40,565,802));page.set_rotation(rotation)
                visual=fitz.Rect(60,100,360,194)
                native=visual*page.derotation_matrix
                output=self.signed_copy(placement=dict(page=0,rect=tuple(native)),template='Modern')
                self.assert_valid(output.read_bytes())
                with fitz.open(output) as signed:
                    page=signed[0];widget=next(page.widgets())
                    for actual,expected in zip(widget.rect,native):self.assertAlmostEqual(actual,expected,places=3)
                    # The navy title bar must run across the top in the visible page.
                    pix=page.get_pixmap()
                    for x in (100,300):
                        rgb=pix.pixel(x,107)
                        self.assertLess(rgb[0],80)
                        self.assertLess(rgb[1],100)
                    page.get_pixmap(clip=visual).save(f'/tmp/pdflx-cert-rotation-{rotation}.png')

    def test_wrong_password_and_source_overwrite_leave_files_untouched(self):
        output = self.path / 'output.pdf'
        output.write_bytes(b'untouched')
        with self.assertLogs('pyhanko.sign.signers.pdf_cms', level='ERROR'):
            with self.assertRaises(signatures.SignatureError):
                signatures.sign_pdf_copy(self.doc.tobytes(), output, self.certificate, 'wrong')
        self.assertEqual(output.read_bytes(), b'untouched')
        with self.assertRaises(signatures.SignatureError):
            signatures.sign_pdf_copy(self.doc.tobytes(), output, self.certificate,
                                     'secret', source_path=output)
        self.assertEqual(output.read_bytes(), b'untouched')
        self.assertEqual(list(self.path.glob('.pdflx-sign-*')), [])

    def test_countersigning_preserves_existing_signature(self):
        first = self.signed_copy()
        with fitz.open(first) as signed:
            data = signatures.prepare_signing_pdf(signed, first, False)
            with self.assertRaises(signatures.SignatureError):
                signatures.prepare_signing_pdf(signed, first, True)
        second = self.path / 'second.pdf'
        signatures.sign_pdf_copy(data, second, self.certificate, 'secret', source_path=first)
        self.assertTrue(second.read_bytes().startswith(first.read_bytes()))
        self.assert_valid(second.read_bytes(), count=2)

    def test_existing_unsigned_field(self):
        writer = IncrementalPdfFileWriter(io.BytesIO(self.doc.tobytes()), strict=False)
        append_signature_field(writer, SigFieldSpec('Approval', box=(30, 30, 230, 90)))
        buffer = io.BytesIO()
        writer.write(buffer)
        output = self.signed_copy(buffer.getvalue(), field_name='Approval')
        reader = self.assert_valid(output.read_bytes())
        self.assertEqual(reader.embedded_signatures[0].field_name, 'Approval')
        with self.assertRaises(signatures.SignatureError):
            self.signed_copy(output.read_bytes(), field_name='Approval')

    def test_encrypted_pdf_and_wrong_pdf_password(self):
        writer = copy_into_new_writer(PdfFileReader(io.BytesIO(self.doc.tobytes()), strict=False))
        writer.encrypt('owner', 'reader')
        buffer = io.BytesIO()
        writer.write(buffer)
        output = self.signed_copy(buffer.getvalue(), pdf_password='reader')
        self.assert_valid(output.read_bytes(), password='reader')
        before = output.read_bytes()
        with self.assertRaises(signatures.SignatureError):
            self.signed_copy(buffer.getvalue(), pdf_password='wrong')
        self.assertEqual(output.read_bytes(), before)


    def test_verification_reports_trust_integrity_and_modification(self):
        from pdflx import pdf_handler
        from pdflx.ops import verify
        signed = self.signed_copy()
        data = signed.read_bytes()
        trusted = verify.verify(data, roots=[self.trust_root])
        self.assertEqual([r['status'] for r in trusted], ['valid'])
        self.assertIn('pdfLX Test', trusted[0]['signer'])
        self.assertEqual(verify.verify(data, roots=[])[0]['status'], 'untrusted')
        # An incremental save appends a revision: the signature stays intact,
        # and verification reports the post-signing change.
        with fitz.open(signed) as doc:
            self.assertTrue(pdf_handler.can_save_incrementally(doc, str(signed)))
            doc[0].add_text_annot((100, 100), 'Later comment')
            ok, error = pdf_handler.save_document(doc, str(signed), incremental=True)
            self.assertTrue(ok, error)
        appended = signed.read_bytes()
        self.assertTrue(appended.startswith(data))
        result = verify.verify(appended, roots=[self.trust_root])[0]
        self.assertTrue(result['intact'] and result['valid'])
        self.assertEqual(result['status'], 'modified')
        # A full rewrite moves the signed byte ranges and breaks the signature.
        rewritten = self.path / 'rewritten.pdf'
        with fitz.open(signed) as doc:
            ok, error = pdf_handler.save_document(doc, str(rewritten))
            self.assertTrue(ok, error)
            self.assertFalse(pdf_handler.can_save_incrementally(doc, str(rewritten)))
        statuses = [r['status'] for r in verify.verify(rewritten.read_bytes(), roots=[self.trust_root])]
        self.assertTrue(all(status in ('invalid', 'modified') for status in statuses), statuses)
        self.assertNotEqual(statuses, ['valid'])


    def test_pkcs7_inspection_lists_algorithms_chain_and_container(self):
        from asn1crypto import cms
        from pdflx.ops import verify
        info = verify.inspect(self.signed_copy().read_bytes())
        self.assertEqual(len(info), 1)
        item = info[0]
        self.assertIn(item['subfilter'], ('adbe.pkcs7.detached', 'ETSI.CAdES.detached'))
        self.assertTrue(item['digest_algorithm'].startswith('sha'))
        self.assertEqual(len(item['byte_range']), 4)
        self.assertIn('pdfLX Test', item['certificates'][0]['subject'])
        self.assertTrue(item['certificates'][0]['self_signed'])
        self.assertIn('BEGIN CERTIFICATE', item['certificates'][0]['pem'])
        self.assertEqual(cms.ContentInfo.load(item['pkcs7'])['content_type'].native, 'signed_data')


if __name__ == '__main__':
    unittest.main()
