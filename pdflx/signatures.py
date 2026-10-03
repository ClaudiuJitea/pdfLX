"""Visible signature assets and certificate signing, independent of the UI."""

import io
import os
from pathlib import Path
import tempfile

try:
    import pymupdf as fitz
except ImportError:
    import fitz


class SignatureError(ValueError):
    pass


def drawn_signature_png(strokes, width=600, height=200):
    """Crop pen strokes and render them to a transparent PNG with PyMuPDF."""
    paths = [[(max(0, min(width, float(x))), max(0, min(height, float(y))))
              for x, y in stroke] for stroke in strokes if len(stroke) >= 2]
    if not paths:
        raise SignatureError("Draw a signature first.")
    points = [point for stroke in paths for point in stroke]
    xs, ys = zip(*points)
    if max(xs) - min(xs) < 2 and max(ys) - min(ys) < 2:
        raise SignatureError("Draw a signature first.")
    clip = fitz.Rect(max(0, min(xs)-4), max(0, min(ys)-4),
                     min(width, max(xs)+4), min(height, max(ys)+4))
    with fitz.open() as doc:
        page = doc.new_page(width=width, height=height)
        for stroke in paths:
            shape = page.new_shape()
            shape.draw_polyline(stroke)
            shape.finish(color=(0.08, 0.08, 0.1), width=2, lineCap=1, lineJoin=1,
                         closePath=False)
            shape.commit()
        return page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=True).tobytes("png")


def signature_image_png(data):
    """Normalize a signature image to PNG, preserving its transparency."""
    try:
        pixmap = fitz.Pixmap(data)
        if pixmap.width < 1 or pixmap.height < 1:
            raise SignatureError("The image is empty.")
        if pixmap.colorspace is None:
            raise SignatureError("The image has no color information.")
        if pixmap.colorspace.n != 3:
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)
        return pixmap.tobytes("png")
    except Exception as error:
        raise SignatureError("Choose a valid PNG or JPEG signature image.") from error


def signature_placement(page, image_bytes, x, y):
    """Return a page-space rectangle and rotation for an upright visible signature."""
    pixmap = fitz.Pixmap(image_bytes)
    visible = page.rect
    width = min(180, visible.width * 0.6)
    height = width * pixmap.height / pixmap.width
    if height > visible.height * 0.4:
        width *= (visible.height * 0.4) / height
        height = visible.height * 0.4
    x = max(0, min(x, visible.width - width))
    y = max(0, min(y, visible.height - height))
    rect = fitz.Rect(x, y, x+width, y+height) * page.derotation_matrix
    return tuple(rect), page.rotation


def prepare_signing_pdf(doc, source_path=None, modified=False):
    """Keep existing signed revisions intact; serialize edits only for unsigned PDFs."""
    signed = any(widget.field_type == fitz.PDF_WIDGET_TYPE_SIGNATURE and widget.is_signed
                 for page in doc for widget in page.widgets() or ())
    if signed and modified:
        raise SignatureError("This PDF already has digital signatures and has been edited. Reopen its unmodified original before adding another digital signature.")
    if source_path and Path(source_path).is_file() and not modified:
        return Path(source_path).read_bytes()
    if signed:
        raise SignatureError("The original signed PDF file is required to preserve its signatures.")
    return doc.tobytes(garbage=0, deflate=True, encryption=fitz.PDF_ENCRYPT_KEEP)


def certificate_signing_available():
    try:
        from pyhanko.sign import signers  # noqa: F401
        return True
    except ImportError:
        return False


def sign_pdf_copy(pdf_bytes, output_path, certificate_path, password="", *,
                  source_path=None, field_name=None, reason="", pdf_password="",
                  placement=None, template=None):
    """Sign a separate PDF atomically, using PKCS#12 and an incremental revision."""
    try:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.pdf_utils.crypt import AuthStatus
        from pyhanko.sign import fields, signers
    except ImportError as error:
        raise SignatureError("Certificate signing requires pyHanko. Install the app dependencies with: pip install -e .") from error
    destination = Path(output_path)
    for input_path in (source_path, certificate_path):
        if input_path and os.path.realpath(input_path) == os.path.realpath(destination):
            raise SignatureError("Choose a separate PDF file for the signed copy.")
    signer = signers.SimpleSigner.load_pkcs12(str(certificate_path),
                                            passphrase=password.encode("utf-8") or None)
    if signer is None:
        raise SignatureError("Could not unlock the certificate. Check the .p12/.pfx file and its password.")
    stream = io.BytesIO(pdf_bytes)
    reader = PdfFileReader(stream, strict=False)
    try:
        encrypted = reader.encrypted
    except Exception as error:
        raise SignatureError("The signing library cannot read this PDF's encryption settings. Use an unencrypted copy for signing.") from error
    if encrypted:
        result = reader.decrypt(pdf_password)
        if result.status == AuthStatus.FAILED:
            raise SignatureError("The PDF password is incorrect.")
    writer = IncrementalPdfFileWriter(stream, prev=reader, strict=False)
    if encrypted:
        writer.encrypt(pdf_password)
    existing = {name: value for name, value, ref in fields.enumerate_sig_fields(writer)}
    use_existing = field_name is not None
    if use_existing:
        if field_name not in existing or existing[field_name] is not None:
            raise SignatureError("Choose an unsigned signature field.")
    else:
        number = 1
        while f"pdfLXSignature{number}" in existing:
            number += 1
        field_name = f"pdfLXSignature{number}"
    metadata = signers.PdfSignatureMetadata(field_name=field_name, md_algorithm="sha256",
                                           reason=reason.strip() or None)
    output = io.BytesIO()
    new_field=None
    if placement is not None:
        if use_existing:raise SignatureError('Choose either an existing field or a new placement.')
        from .certificate_appearance import pdf_box
        new_field=fields.SigFieldSpec(field_name,on_page=placement['page'],
                                     box=pdf_box(pdf_bytes,placement,pdf_password))
    if template is not None:
        from .certificate_appearance import template_pdf
        from pyhanko import stamp
        from pyhanko.pdf_utils.content import ImportedPdfPage
        name=signer.signing_cert.subject.native.get('common_name') or signer.signing_cert.subject.human_friendly
        with tempfile.TemporaryDirectory(prefix='pdflx-appearance-') as folder:
            appearance=Path(folder)/'appearance.pdf'
            rotation=0
            with fitz.open(stream=pdf_bytes,filetype='pdf') as source:
                if source.needs_pass:source.authenticate(pdf_password)
                if placement is not None:rotation=source[placement['page']].rotation
                elif use_existing:
                    for page in source:
                        if any(widget.field_name==field_name for widget in page.widgets() or ()):
                            rotation=page.rotation;break
            appearance.write_bytes(template_pdf(name,reason,template,rotation=rotation))
            style=stamp.StaticStampStyle(border_width=0,background=ImportedPdfPage(str(appearance)))
            pdf_signer=signers.PdfSigner(metadata,signer=signer,stamp_style=style,new_field_spec=new_field)
            pdf_signer.sign_pdf(writer,existing_fields_only=use_existing,output=output)
    else:
        signers.sign_pdf(writer, metadata, signer=signer, existing_fields_only=use_existing,
                         new_field_spec=new_field,output=output)
    # Complete the signature before touching the destination, including on errors.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".pdflx-sign-",
                                         suffix=".pdf", delete=False) as file:
            temporary = file.name
            file.write(output.getvalue())
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary:
            os.unlink(temporary)
    return field_name
