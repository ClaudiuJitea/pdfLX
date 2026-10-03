"""Validated form appearance and behaviour settings shared by creation/editing."""
import math
import pymupdf as fitz
from urllib.parse import unquote_to_bytes


def decode_button_state(value):
    """PyMuPDF returns escaped PDF names for radio appearance states."""
    if not isinstance(value,str):return value
    raw=unquote_to_bytes(value.replace('%','%25').replace('#','%'))
    try:return raw.decode('utf-8')
    except UnicodeDecodeError:return raw.decode('latin-1')


def apply_options(widget,options=None):
    options=options or {}
    allowed={'font_size','font','text_color','fill_color','border_color','border_width',
             'tooltip','multiline','readonly','editable_choice','button_caption','button_action','button_page',
             'button_url','button_format','button_script',
             'password','comb','multi_select','border_style','max_length'}
    if set(options)-allowed:raise ValueError('Unknown form field setting.')
    for key in ('font_size','border_width'):
        if key in options and (not math.isfinite(float(options[key])) or options[key]<0):
            raise ValueError('Font and border sizes must be non-negative finite numbers.')
    for key in ('text_color','fill_color','border_color'):
        value=options.get(key)
        if value is not None and (len(value) not in (1,3,4) or any(not math.isfinite(c) or not 0<=c<=1 for c in value)):
            raise ValueError('Invalid form field color.')
    for key,attr in (('font_size','text_fontsize'),('font','text_font'),('text_color','text_color'),
                     ('fill_color','fill_color'),('border_color','border_color'),('border_width','border_width'),
                     ('tooltip','field_label')):
        if key in options:setattr(widget,attr,options[key])
    if widget.field_type==fitz.PDF_WIDGET_TYPE_CHECKBOX:
        widget.text_font='ZaDb';widget.text_fontsize=0
    if widget.field_type==fitz.PDF_WIDGET_TYPE_BUTTON:
        if 'button_caption' in options:
            caption=str(options['button_caption']).strip()
            if not caption:raise ValueError('Enter a button label.')
            widget.button_caption=caption
        if not widget.text_fontsize:
            widget.text_fontsize=max(5,min(11,widget.rect.height*.5,
                widget.rect.width/max(1,len(widget.button_caption or 'Reset form'))/0.6))
    if 'border_style' in options:
        style=BORDER_STYLES.get(options['border_style'])
        if style is None:raise ValueError('Unknown border style.')
        widget.border_style=style
        widget.border_dashes=[3,2] if style=='D' else None
    if 'max_length' in options and widget.field_type==fitz.PDF_WIDGET_TYPE_TEXT:
        limit=int(options['max_length'])
        if limit<0:raise ValueError('The character limit cannot be negative.')
        widget.text_maxlen=limit
    for key,flag,kind in (('readonly',fitz.PDF_FIELD_IS_READ_ONLY,None),
                          ('multiline',fitz.PDF_TX_FIELD_IS_MULTILINE,fitz.PDF_WIDGET_TYPE_TEXT),
                          ('password',fitz.PDF_TX_FIELD_IS_PASSWORD,fitz.PDF_WIDGET_TYPE_TEXT),
                          ('comb',fitz.PDF_TX_FIELD_IS_COMB,fitz.PDF_WIDGET_TYPE_TEXT),
                          ('multi_select',fitz.PDF_CH_FIELD_IS_MULTI_SELECT,fitz.PDF_WIDGET_TYPE_LISTBOX),
                          ('editable_choice',fitz.PDF_CH_FIELD_IS_EDIT,fitz.PDF_WIDGET_TYPE_COMBOBOX)):
        if key in options and (kind is None or widget.field_type==kind):
            widget.field_flags=(widget.field_flags|flag) if options[key] else (widget.field_flags&~flag)
    if widget.field_type==fitz.PDF_WIDGET_TYPE_TEXT:
        flags=widget.field_flags
        if flags&fitz.PDF_TX_FIELD_IS_COMB:
            # PDF 32000 12.7.4.3: comb fields need /MaxLen and exclude these flags.
            if not widget.text_maxlen:raise ValueError('A comb field needs a character limit.')
            if flags&(fitz.PDF_TX_FIELD_IS_MULTILINE|fitz.PDF_TX_FIELD_IS_PASSWORD):
                raise ValueError('A comb field cannot also be multiline or a password field.')
        if flags&fitz.PDF_TX_FIELD_IS_PASSWORD and flags&fitz.PDF_TX_FIELD_IS_MULTILINE:
            raise ValueError('A password field cannot be multiline.')


BORDER_STYLES={'solid':'S','dashed':'D','beveled':'B','inset':'I','underline':'U'}


def border_style_key(value):
    """Map PyMuPDF's border style ('Solid', 'D', …) to a BORDER_STYLES key."""
    initial=(value or 'S')[0].upper()
    return next((key for key,letter in BORDER_STYLES.items() if letter==initial),'solid')


def choice_options(values):
    """PDF choice pairs are (export value, display label)."""
    return [(str(value[0]),str(value[1])) if isinstance(value,(tuple,list)) and len(value)==2
            else (str(value),str(value)) for value in values or ()]


def color_rgb(value,default=(0,0,0)):
    if not value:return default
    if len(value)==1:return (value[0],)*3
    if len(value)==4:
        c,m,y,k=value
        return (1-min(1,c+k),1-min(1,m+k),1-min(1,y+k))
    return tuple(value)


def missing_required(fields,pending=None):
    pending=pending or {}
    missing=[]
    checked_groups=set()
    for field in fields:
        if not field['required'] or field['readonly'] or field['type'] in (fitz.PDF_WIDGET_TYPE_SIGNATURE,fitz.PDF_WIDGET_TYPE_BUTTON):continue
        if field['type']==fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            if field['name'] in checked_groups:continue
            checked_groups.add(field['name'])
            members=[f for f in fields if f['type']==fitz.PDF_WIDGET_TYPE_RADIOBUTTON and f['name']==field['name']]
            if not any(pending.get((f['page'],f['xref']),f['value']==f['on_state']) is True for f in members):missing.append(field)
            continue
        value=pending.get((field['page'],field['xref']),field['value'])
        if value is None or value is False or value==[] or (isinstance(value,str) and (not value.strip() or value=='Off')):
            missing.append(field)
    return missing


def normalize_choices(values):
    result=[]
    for value in values:
        if isinstance(value,(tuple,list)) and len(value)==2:
            item=(str(value[0]).strip(),str(value[1]).strip())
            if not all(item):raise ValueError('Choice labels and export values cannot be empty.')
        else:
            item=str(value).strip()
            if not item:continue
        if item not in result:result.append(item)
    exports=[pair[0] for pair in choice_options(result)]
    if len(set(exports))<2:raise ValueError('Enter at least two different choices, one per line.')
    if len(set(exports))!=len(exports):raise ValueError('Choice export values must be unique.')
    return result
