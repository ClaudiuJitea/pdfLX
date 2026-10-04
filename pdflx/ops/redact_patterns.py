"""Find sensitive data to redact: emails, phone numbers, IBANs, card numbers, dates, URLs, IDs."""
import re

from .text import search_page

# key: (regex, validator or None). Matching runs on a page's words joined by single spaces.
PATTERNS = {
    'email': (r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', None),
    'phone': (r'(?<![\w+])(?:\+\d{1,3}[ .\-]?)?(?:\(\d{1,4}\)[ .\-]?)?\d{2,4}(?:[ .\-]\d{2,4}){1,4}(?!\w)', None),
    'iban': (r'\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b', 'iban'),
    'card': (r'(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)', 'luhn'),
    'date': (r'\b(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}|'
             r'\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{4})\b', None),
    'url': (r'\b(?:https?://|www\.)[^\s<>"]+[^\s<>".,;:!?)]', None),
    'number_id': (r'\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,20}\b', None),
}
ORDER = ('email', 'url', 'iban', 'card', 'date', 'phone', 'number_id')  # specific before loose


def _digits(text):
    return re.sub(r'\D', '', text)


def luhn(text):
    digits = _digits(text)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def iban(text):
    compact = text.replace(' ', '').upper()
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    number = ''.join(str(int(char, 36)) for char in rearranged)
    return int(number) % 97 == 1


VALIDATORS = {'luhn': luhn, 'iban': iban}


def find(page, keys):
    """[(key, matched text, [quads])] for the chosen pattern keys on a page."""
    found = []
    taken = []
    for key in ORDER:
        if key not in keys:
            continue
        pattern, validator = PATTERNS[key]
        check = VALIDATORS.get(validator)
        for text, quads in search_page(page, pattern, regex=True, case_sensitive=True):
            if check and not check(text):
                continue
            if key == 'phone' and not 7 <= len(_digits(text)) <= 15:  # E.164 allows at most 15 digits
                continue
            area = [q.rect for q in quads]
            # A later, looser pattern must not re-redact what an earlier one matched (IBAN digits as a phone).
            if any(a.intersects(b) for a in area for b in taken):
                continue
            taken.extend(area)
            found.append((key, text, quads))
    return found


def targets(doc, first, last, keys):
    """[(page, rect tuple)] redaction targets and a list of (page, key, text) for review."""
    result, review = [], []
    for number in range(first, last + 1):
        for key, text, quads in find(doc[number], keys):
            result.extend((number, tuple(quad.rect)) for quad in quads)
            review.append((number, key, text))
    return result, review
