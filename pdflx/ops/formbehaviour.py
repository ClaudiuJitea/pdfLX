"""Field behaviour as standard Acrobat form scripts (portable to other PDF readers).

The settings map to the built-in AF* functions every form-capable reader
implements (Acrobat, Okular, Firefox, Chrome, MuPDF), so no custom JavaScript
is required for formatting, range validation, or simple calculations.
"""
import re

import pymupdf as fitz

from .common import OperationError

# Number separator styles (AFNumber_Format sepStyle).
SEPARATORS = (('0', '1,234.56'), ('1', '1234.56'), ('2', '1.234,56'), ('3', '1234,56'))
NEGATIVE_STYLES = (('0', '-1,234.56'), ('1', 'Red'), ('2', '(1,234.56)'), ('3', 'Red (1,234.56)'))
DATE_FORMATS = ('dd/mm/yyyy', 'mm/dd/yyyy', 'yyyy-mm-dd', 'dd.mm.yyyy', 'd mmm yyyy', 'mmm d, yyyy', 'dd/mm/yy')
TIME_FORMATS = (('0', 'HH:MM'), ('1', 'h:MM tt'), ('2', 'HH:MM:ss'), ('3', 'h:MM:ss tt'))
SPECIAL_FORMATS = (('0', 'ZIP code'), ('1', 'ZIP+4'), ('2', 'Phone number'), ('3', 'Social Security number'))
CALCULATIONS = (('SUM', 'Sum'), ('AVG', 'Average'), ('PRD', 'Product'), ('MIN', 'Minimum'), ('MAX', 'Maximum'))
FORMAT_KINDS = ('none', 'number', 'percent', 'date', 'time', 'special')


def _js_string(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def build_scripts(behaviour):
    """Return {'K': js, 'F': js, 'V': js, 'C': js} (empty string = no script)."""
    kind = behaviour.get('format', 'none')
    scripts = {'K': '', 'F': '', 'V': '', 'C': ''}
    if kind == 'number':
        decimals = int(behaviour.get('decimals', 2))
        separator = int(behaviour.get('separator', 0))
        negative = int(behaviour.get('negative', 0))
        currency = behaviour.get('currency', '')
        prepend = 'true' if behaviour.get('currency_before', True) else 'false'
        arguments = f'{decimals}, {separator}, {negative}, 0, {_js_string(currency)}, {prepend}'
        scripts['F'] = f'AFNumber_Format({arguments});'
        scripts['K'] = f'AFNumber_Keystroke({arguments});'
    elif kind == 'percent':
        decimals = int(behaviour.get('decimals', 2))
        separator = int(behaviour.get('separator', 0))
        scripts['F'] = f'AFPercent_Format({decimals}, {separator});'
        scripts['K'] = f'AFPercent_Keystroke({decimals}, {separator});'
    elif kind == 'date':
        pattern = behaviour.get('date_format', DATE_FORMATS[0])
        scripts['F'] = f'AFDate_FormatEx({_js_string(pattern)});'
        scripts['K'] = f'AFDate_KeystrokeEx({_js_string(pattern)});'
    elif kind == 'time':
        style = int(behaviour.get('time_format', 0))
        scripts['F'] = f'AFTime_Format({style});'
        scripts['K'] = f'AFTime_Keystroke({style});'
    elif kind == 'special':
        style = int(behaviour.get('special', 0))
        scripts['F'] = f'AFSpecial_Format({style});'
        scripts['K'] = f'AFSpecial_Keystroke({style});'
    elif kind != 'none':
        raise OperationError(f'Unknown format: {kind}.')
    low, high = behaviour.get('minimum'), behaviour.get('maximum')
    if low is not None or high is not None:
        if low is not None and high is not None and float(low) > float(high):
            raise OperationError('The minimum must not exceed the maximum.')
        scripts['V'] = ('AFRange_Validate({}, {}, {}, {});'.format(
            'true' if low is not None else 'false', float(low) if low is not None else 0,
            'true' if high is not None else 'false', float(high) if high is not None else 0))
    calculation = behaviour.get('calculate', 'none')
    if calculation in dict(CALCULATIONS):
        fields = [name for name in behaviour.get('sources', ()) if name]
        if not fields:
            raise OperationError('Choose the fields to calculate from.')
        scripts['C'] = 'AFSimple_Calculate({}, new Array({}));'.format(
            _js_string(calculation), ', '.join(_js_string(name) for name in fields))
    elif calculation == 'custom':
        code = behaviour.get('custom', '').strip()
        if not code:
            raise OperationError('Enter the calculation script.')
        scripts['C'] = code
    elif calculation not in ('none', None):
        raise OperationError(f'Unknown calculation: {calculation}.')
    return scripts


def _script(doc, xref, event):
    from .javascript import _code
    kind, _value = doc.xref_get_key(xref, f'AA/{event}')
    if kind == 'null':
        return ''
    target_kind, target = doc.xref_get_key(xref, f'AA/{event}')
    if target_kind == 'xref':
        return _code(doc, int(target.split()[0]), 'JS') or ''
    return _code(doc, xref, f'AA/{event}/JS') or ''


def _args(text):
    return [part.strip().strip('"') for part in re.split(r',(?=(?:[^"]*"[^"]*")*[^"]*$)', text)]


def parse(doc, xref):
    """Read a field's scripts back into a behaviour dict (unknown scripts → 'custom')."""
    fmt, keystroke = _script(doc, xref, 'F'), _script(doc, xref, 'K')
    validate, calculate = _script(doc, xref, 'V'), _script(doc, xref, 'C')
    behaviour = {'format': 'none', 'calculate': 'none', 'minimum': None, 'maximum': None, 'sources': [],
                 'custom': '', 'unrecognized': []}
    match = re.search(r'AFNumber_Format\((.*?)\)', fmt)
    if match:
        args = _args(match.group(1))
        behaviour.update(format='number', decimals=int(float(args[0])), separator=int(float(args[1])),
                         negative=int(float(args[2])), currency=args[4] if len(args) > 4 else '',
                         currency_before=(args[5] if len(args) > 5 else 'true') == 'true')
    elif (match := re.search(r'AFPercent_Format\((.*?)\)', fmt)):
        args = _args(match.group(1))
        behaviour.update(format='percent', decimals=int(float(args[0])), separator=int(float(args[1])))
    elif (match := re.search(r'AFDate_Format(?:Ex)?\((.*?)\)', fmt)):
        behaviour.update(format='date', date_format=_args(match.group(1))[0])
    elif (match := re.search(r'AFTime_Format\((.*?)\)', fmt)):
        behaviour.update(format='time', time_format=int(float(_args(match.group(1))[0])))
    elif (match := re.search(r'AFSpecial_Format\((.*?)\)', fmt)):
        behaviour.update(format='special', special=int(float(_args(match.group(1))[0])))
    elif fmt.strip() or keystroke.strip():
        behaviour['unrecognized'].append('format')
    match = re.search(r'AFRange_Validate\((.*?)\)', validate)
    if match:
        args = _args(match.group(1))
        if args[0] == 'true':
            behaviour['minimum'] = float(args[1])
        if args[2] == 'true':
            behaviour['maximum'] = float(args[3])
    elif validate.strip():
        behaviour['unrecognized'].append('validate')
    match = re.search(r'AFSimple_Calculate\(\s*"(\w+)"\s*,\s*(?:new Array\((.*?)\)|\[(.*?)\])', calculate, re.S)
    if match:
        behaviour['calculate'] = match.group(1)
        behaviour['sources'] = re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(2) or match.group(3) or '')
    elif calculate.strip():
        behaviour.update(calculate='custom', custom=calculate)
    return behaviour


def apply(doc, xref, behaviour):
    """Write the behaviour's scripts to a text or choice field and maintain /CO."""
    from .javascript import set_field_script
    scripts = build_scripts(behaviour)
    previous = parse(doc, xref)
    for event, code in scripts.items():
        # Keep scripts the UI could not interpret unless the user changed that part.
        if event in ('F', 'K') and 'format' in previous['unrecognized'] and behaviour.get('format') == 'none':
            continue
        if event == 'V' and 'validate' in previous['unrecognized'] and behaviour.get('minimum') is None \
                and behaviour.get('maximum') is None:
            continue
        set_field_script(doc, xref, event, code)
    _update_calculation_order(doc, xref, bool(scripts['C']))
    doc._reset_page_refs()


def _calculation_order(doc):
    kind, value = doc.xref_get_key(doc.pdf_catalog(), 'AcroForm/CO')
    return [int(ref) for ref in re.findall(r'(\d+)\s+0\s+R', value)] if kind == 'array' else []


def _field_xref(doc, xref):
    """Calculation order lists terminal fields; widgets of a field with one kid share its xref."""
    return xref


def _update_calculation_order(doc, xref, calculated):
    order = _calculation_order(doc)
    target = _field_xref(doc, xref)
    if calculated and target not in order:
        order.append(target)
    elif not calculated and target in order:
        order.remove(target)
    value = '[' + ' '.join(f'{ref} 0 R' for ref in order) + ']' if order else 'null'
    doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/CO', value)


def calculation_order(doc):
    """Field names in calculation order (for display and reordering)."""
    names = []
    for xref in _calculation_order(doc):
        kind, value = doc.xref_get_key(xref, 'T')
        names.append((xref, value if kind == 'string' else f'Field {xref}'))
    return names


def set_calculation_order(doc, xrefs):
    current = set(_calculation_order(doc))
    if set(xrefs) != current:
        raise OperationError('The calculation order must list every calculated field once.')
    doc.xref_set_key(doc.pdf_catalog(), 'AcroForm/CO', '[' + ' '.join(f'{ref} 0 R' for ref in xrefs) + ']')


def describe_rejection(doc, xref, name, value):
    """Human-readable reason for a rejected value, derived from the field's scripts."""
    behaviour = parse(doc, xref)
    low, high = behaviour.get('minimum'), behaviour.get('maximum')
    if low is not None or high is not None:
        if low is not None and high is not None:
            return f'"{value}" is not allowed in {name}: enter a number from {low:g} to {high:g}.'
        if low is not None:
            return f'"{value}" is not allowed in {name}: enter a number of at least {low:g}.'
        return f'"{value}" is not allowed in {name}: enter a number up to {high:g}.'
    if behaviour.get('format') == 'date':
        return f'"{value}" is not a valid date for {name} (expected {behaviour.get("date_format")}).'
    if behaviour.get('format') in ('number', 'percent'):
        return f'"{value}" is not a valid number for {name}.'
    return f'The form\'s script rejected "{value}" for {name}.'


def date_format_to_strftime(pattern):
    """Convert an Acrobat date pattern (dd/mm/yyyy, mmm d, yyyy) to strftime."""
    tokens = (('yyyy', '%Y'), ('yy', '%y'), ('mmmm', '%B'), ('mmm', '%b'), ('mm', '%m'), ('dd', '%d'),
              ('HH', '%H'), ('MM', '%M'), ('ss', '%S'))
    out, index = '', 0
    while index < len(pattern):
        for token, replacement in tokens:
            if pattern.startswith(token, index):
                out += replacement
                index += len(token)
                break
        else:
            char = pattern[index]
            if char == 'm':
                out += '%-m'
            elif char == 'd':
                out += '%-d'
            else:
                out += char.replace('%', '%%')
            index += 1
    return out


def text_fields(doc):
    """(xref, full name) of text and choice fields, for calculation source lists."""
    from .forms import full_names
    names = full_names(doc)
    seen, result = set(), []
    for page in doc:
        for widget in page.widgets() or ():
            if widget.field_type in (fitz.PDF_WIDGET_TYPE_TEXT, fitz.PDF_WIDGET_TYPE_COMBOBOX,
                                     fitz.PDF_WIDGET_TYPE_LISTBOX, fitz.PDF_WIDGET_TYPE_CHECKBOX,
                                     fitz.PDF_WIDGET_TYPE_RADIOBUTTON):
                name = names.get(widget.xref, widget.field_name)
                if name not in seen:
                    seen.add(name)
                    result.append((widget.xref, name))
    return result


def check_value(behaviour, value):
    """Commit-time check for the standard formats (the AF*_Keystroke semantics).

    MuPDF's engine runs validation and calculation scripts, but its value API
    does not fire the commit keystroke event, so formatted fields are checked
    here. Returns the value to store (numbers are normalised so calculations
    work) or raises OperationError. Custom keystroke scripts are not emulated.
    """
    import datetime
    text = str(value).strip()
    kind = behaviour.get('format', 'none')
    if not text or kind == 'none':
        return text if kind != 'none' else value
    if kind in ('number', 'percent'):
        separator = int(behaviour.get('separator', 0))
        decimal_comma = separator in (2, 3)
        cleaned = text.replace(behaviour.get('currency', '') or '\0', '').replace('%', '').strip()
        cleaned = cleaned.replace(' ', '').replace(' ', '')
        negative = cleaned.startswith('(') and cleaned.endswith(')')
        cleaned = cleaned.strip('()')
        if decimal_comma:
            cleaned = cleaned.replace('.', '').replace(',', '.')
        else:
            cleaned = cleaned.replace(',', '')
        if not re.fullmatch(r'[-+]?(\d+\.?\d*|\.\d+)', cleaned):
            raise OperationError(f'"{value}" is not a valid number.')
        number = float(cleaned) * (-1 if negative else 1)
        if kind == 'percent' and text.endswith('%'):
            number /= 100
        return repr(number) if number != int(number) else str(int(number))
    if kind == 'date':
        pattern = date_format_to_strftime(behaviour.get('date_format', DATE_FORMATS[0])).replace('%-', '%')
        try:
            parsed = datetime.datetime.strptime(text, pattern)
        except ValueError:
            raise OperationError(f'"{value}" is not a valid date (expected {behaviour.get("date_format")}).')
        return parsed.strftime(date_format_to_strftime(behaviour.get('date_format')).replace('%-', '%'))
    if kind == 'time':
        for pattern in ('%H:%M', '%I:%M %p', '%H:%M:%S', '%I:%M:%S %p', '%I:%M%p'):
            try:
                datetime.datetime.strptime(text.upper(), pattern)
                return text
            except ValueError:
                continue
        raise OperationError(f'"{value}" is not a valid time.')
    if kind == 'special':
        digits = re.sub(r'\D', '', text)
        expected = {0: 5, 1: 9, 2: 10, 3: 9}[int(behaviour.get('special', 0))]
        if len(digits) != expected:
            raise OperationError(f'"{value}" must contain {expected} digits.')
        return digits
    return text
