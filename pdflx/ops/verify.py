"""Cryptographic verification of existing PDF signatures with pyHanko.

``widget.is_signed`` only reports that a signature value exists. This module
checks integrity (the signed bytes are unchanged), the signature value, the
certificate chain against trust roots, and what changed after signing.
"""
import io
import os

# System CA bundles, first match wins. Users can add their own roots.
CA_BUNDLES = ('/etc/ssl/certs/ca-certificates.crt', '/etc/pki/tls/certs/ca-bundle.crt',
              '/etc/ssl/ca-bundle.pem', '/etc/ssl/cert.pem')


def available():
    try:
        from pyhanko.sign.validation import validate_pdf_signature  # noqa: F401
        return True
    except ImportError:
        return False


def _load_certificates(data):
    from asn1crypto import pem, x509
    certificates = []
    if pem.detect(data):
        for kind, _headers, der in pem.unarmor(data, multiple=True):
            if kind == 'CERTIFICATE':
                certificates.append(x509.Certificate.load(der))
    else:
        certificates.append(x509.Certificate.load(data))
    return certificates


def trust_roots(extra_paths=(), use_system=True):
    roots = []
    if use_system:
        for path in CA_BUNDLES:
            if os.path.isfile(path):
                try:
                    roots.extend(_load_certificates(open(path, 'rb').read()))
                except Exception:
                    pass
                break
    for path in extra_paths:
        roots.extend(_load_certificates(open(path, 'rb').read()))
    return roots


def verify(pdf_bytes, password=None, extra_roots=(), use_system_roots=True, roots=None):
    """Verify every embedded signature. Returns a list of result dicts.

    Each result: field, signer, issuer, signing_time, intact, valid, trusted,
    coverage, modification_level, docmdp_ok, revoked, status ('valid',
    'untrusted', 'modified', 'invalid'), summary, details.
    """
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext

    reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    if reader.encrypted:
        if not password:
            raise ValueError('The document is encrypted; its password is required for verification.')
        reader.decrypt(password)
    anchors = list(roots) if roots is not None else trust_roots(extra_roots, use_system_roots)
    results = []
    for signature in reader.embedded_regular_signatures:
        context = ValidationContext(trust_roots=anchors, allow_fetching=False)
        entry = {'field': signature.field_name, 'signer': '', 'issuer': '', 'signing_time': ''}
        try:
            cert = signature.signer_cert
            entry['signer'] = cert.subject.human_friendly
            entry['issuer'] = cert.issuer.human_friendly
        except Exception:
            pass
        try:
            status = validate_pdf_signature(signature, signer_validation_context=context)
        except Exception as error:
            entry.update(intact=False, valid=False, trusted=False, coverage='', modification_level='',
                         docmdp_ok=False, revoked=False, status='invalid', summary=str(error), details=str(error))
            results.append(entry)
            continue
        when = status.signer_reported_dt
        entry.update(
            signing_time=when.isoformat(sep=' ', timespec='seconds') if when else '',
            intact=bool(status.intact), valid=bool(status.valid), trusted=bool(status.trusted),
            coverage=getattr(status.coverage, 'name', str(status.coverage)),
            modification_level=getattr(status.modification_level, 'name', str(status.modification_level)),
            docmdp_ok=status.docmdp_ok is not False, revoked=bool(getattr(status, 'revoked', False)),
            summary=status.summary(), details=status.pretty_print_details(),
        )
        if not (status.intact and status.valid):
            entry['status'] = 'invalid'
        elif status.docmdp_ok is False or entry['coverage'] not in ('ENTIRE_FILE', 'ENTIRE_REVISION'):
            entry['status'] = 'modified'
        elif entry['coverage'] == 'ENTIRE_REVISION' and entry['modification_level'] not in ('NONE', 'LTA_UPDATES'):
            entry['status'] = 'modified'
        elif not status.trusted:
            entry['status'] = 'untrusted'
        else:
            entry['status'] = 'valid'
        results.append(entry)
    return results


STATUS_TEXT = {
    'valid': 'Valid: the document is unchanged since signing and the signer is trusted.',
    'untrusted': 'Intact, but the signer certificate does not chain to a trusted root.',
    'modified': 'The signature is cryptographically intact, but the document was changed after signing.',
    'invalid': 'Invalid: the signed content was altered or the signature cannot be verified.',
}


def _fingerprint(cert):
    import hashlib
    return hashlib.sha256(cert.dump()).hexdigest().upper()


def _cert_info(cert):
    validity = cert['tbs_certificate']['validity']
    return {'subject': cert.subject.human_friendly, 'issuer': cert.issuer.human_friendly,
            'serial': format(cert.serial_number, 'X'),
            'not_before': validity['not_before'].native.isoformat(sep=' '),
            'not_after': validity['not_after'].native.isoformat(sep=' '),
            'sha256': _fingerprint(cert), 'self_signed': cert.self_signed != 'no',
            'pem': _pem(cert)}


def _pem(cert):
    from asn1crypto import pem
    return pem.armor('CERTIFICATE', cert.dump()).decode('ascii')


def inspect(pdf_bytes, password=None):
    """Describe the PKCS#7/CMS structure of every signature without validating it.

    Each item: field, subfilter, digest_algorithm, signature_algorithm,
    byte_range, signing_time, has_timestamp, certificates (signer first),
    and pkcs7 (DER bytes of the signature container, for export as .p7s).
    """
    from pyhanko.pdf_utils.reader import PdfFileReader
    reader = PdfFileReader(io.BytesIO(pdf_bytes), strict=False)
    if reader.encrypted:
        if not password:
            raise ValueError('The document is encrypted; its password is required.')
        reader.decrypt(password)
    items = []
    for signature in reader.embedded_signatures:
        sig_object = signature.sig_object
        signed_data = signature.signed_data
        signer_info = signature.signer_info
        contents = sig_object.get('/Contents')
        der = bytes(contents) if contents is not None else b''
        # /Contents is zero-padded to its reserved size; keep only the CMS structure.
        try:
            from asn1crypto import cms
            der = cms.ContentInfo.load(der).dump()
        except Exception:
            pass
        signer = signature.signer_cert
        others = [cert for cert in signed_data['certificates'] or () if getattr(cert, 'chosen', None) is not None]
        chain = [signer] + [c.chosen for c in others if c.chosen.dump() != signer.dump()]
        unsigned = signer_info['unsigned_attrs']
        has_timestamp = bool(unsigned.native and any(attr['type'] == 'signature_time_stamp_token'
                                                     for attr in unsigned.native)) if unsigned.native else False
        when = signature.self_reported_timestamp
        items.append({
            'field': signature.field_name,
            'subfilter': str(sig_object.get('/SubFilter', '')).lstrip('/'),
            'digest_algorithm': signer_info['digest_algorithm']['algorithm'].native,
            'signature_algorithm': signer_info['signature_algorithm']['algorithm'].native,
            'byte_range': [int(v) for v in sig_object.get('/ByteRange', [])],
            'signing_time': when.isoformat(sep=' ', timespec='seconds') if when else '',
            'has_timestamp': has_timestamp,
            'reason': str(sig_object.get('/Reason', '') or ''),
            'location': str(sig_object.get('/Location', '') or ''),
            'certificates': [_cert_info(cert) for cert in chain],
            'pkcs7': der,
        })
    return items
