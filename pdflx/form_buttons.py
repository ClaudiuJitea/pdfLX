"""Explicit native actions for user-created PDF form buttons."""
import re

import pymupdf as fitz

ACTIONS = ('reset', 'goto', 'submit', 'url', 'print', 'javascript')
# SubmitForm /Flags (PDF 32000-1 table 237): ExportFormat=4 (HTML), XFDF=32, SubmitPDF=256.
SUBMIT_FORMATS = {'fdf': 0, 'html': 4, 'xfdf': 32, 'pdf': 256}


def _string(doc, xref, key):
    kind, value = doc.xref_get_key(xref, key)
    if kind == 'string':
        return value
    if kind == 'xref':
        try:
            return doc.xref_stream(int(value.split()[0])).decode('utf-8', 'replace')
        except Exception:
            return ''
    return ''


def details(doc,xref):
    """Return (action, target) for a button: target is the page, URL, submit dict, or script."""
    prefix='A'
    action=doc.xref_get_key(xref,prefix+'/S')[1]
    if action=='null':prefix='AA/U';action=doc.xref_get_key(xref,prefix+'/S')[1]
    if action=='/ResetForm':return 'reset',None
    if action=='/GoTo':
        _,destination=doc.xref_get_key(xref,prefix+'/D')
        match=re.match(r'\[\s*(\d+)\s+0\s+R',destination)
        if match:
            target=int(match.group(1))
            for number in range(doc.page_count):
                if doc.page_xref(number)==target:return 'goto',number
    if action=='/URI':
        return 'url',_string(doc,xref,prefix+'/URI')
    if action=='/Named' and doc.xref_get_key(xref,prefix+'/N')[1]=='/Print':
        return 'print',None
    if action=='/SubmitForm':
        url=_string(doc,xref,prefix+'/F/F') or _string(doc,xref,prefix+'/F')
        flags_kind,flags=doc.xref_get_key(xref,prefix+'/Flags')
        flags=int(flags) if flags_kind=='int' else 0
        fmt=next((key for key,value in sorted(SUBMIT_FORMATS.items(),key=lambda item:-item[1]) if value and flags&value),'fdf')
        return 'submit',{'url':url,'format':fmt}
    if action=='/JavaScript':
        return 'javascript',_string(doc,xref,prefix+'/JS')
    return None,None


def configure(doc,xref,options):
    if not options or 'button_action' not in options:return
    action=options['button_action']
    if action=='reset':value='<< /S /ResetForm >>'
    elif action=='goto':
        number=options.get('button_page')
        if not isinstance(number,int) or not 0<=number<doc.page_count:raise ValueError('Choose an existing destination page.')
        value=f'<< /S /GoTo /D [{doc.page_xref(number)} 0 R /Fit] >>'
    elif action=='url':
        url=str(options.get('button_url','')).strip()
        if not url:raise ValueError('Enter the web address to open.')
        if '://' not in url and not url.startswith('mailto:'):url='https://'+url
        value=f'<< /S /URI /URI {fitz.get_pdf_str(url)} >>'
    elif action=='print':
        value='<< /S /Named /N /Print >>'
    elif action=='submit':
        url=str(options.get('button_url','')).strip()
        if not re.match(r'^(https?://|mailto:)',url):raise ValueError('Enter an http(s):// or mailto: address to submit to.')
        fmt=options.get('button_format','fdf')
        if fmt not in SUBMIT_FORMATS:raise ValueError('Choose a submission format.')
        value=(f'<< /S /SubmitForm /F << /FS /URL /F {fitz.get_pdf_str(url)} >> '
               f'/Flags {SUBMIT_FORMATS[fmt]} >>')
    elif action=='javascript':
        script=str(options.get('button_script','')).strip()
        if not script:raise ValueError('Enter the script to run.')
        value=f'<< /S /JavaScript /JS {fitz.get_pdf_str(script)} >>'
    else:raise ValueError('Choose a supported button action.')
    doc.xref_set_key(xref,'A',value)
    # A newly chosen action replaces any legacy mouse-up action.
    doc.xref_set_key(xref,'AA/U','null')


def describe(action,target):
    if action=='reset':return 'Reset form'
    if action=='goto':return f'Go to page {target+1}'
    if action=='url':return f'Open {target}'
    if action=='print':return 'Print document'
    if action=='submit':return f"Submit ({(target or {}).get('format','fdf').upper()}) to {(target or {}).get('url','')}"
    if action=='javascript':return 'Run JavaScript'
    return 'No supported action configured'
