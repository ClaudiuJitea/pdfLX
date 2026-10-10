"""Fast form authoring: quick field presets with a shared style and labels, smart guides,
multiple copies, style matching, tab order and field detection."""
import re

import pymupdf as fitz

from .i18n import get_setting, set_setting

DEFAULT_STYLE = {
    'font': 'Helv', 'font_size': 10.0, 'text_color': (0.1, 0.1, 0.1),
    'border_color': (0.8, 0.85, 0.9), 'fill_color': (0.97, 0.98, 0.99), 'border_width': 1.0,
    'border_style': 'solid', 'label_position': 'above', 'label_size': 9.0, 'label_color': (0.2, 0.2, 0.2),
    'label_font': 'Liberation Sans', 'date_format': 'yyyy-mm-dd', 'snap': True,
}
APPEARANCE_KEYS = ('font', 'font_size', 'text_color', 'border_color', 'fill_color', 'border_width', 'border_style')

# key, label key, PDF field kind, default size (w, h)
PRESETS = (
    ('text', 'qf_text', 'text', (200, 22)),
    ('multiline', 'qf_multiline', 'text', (300, 60)),
    ('date', 'qf_date', 'text', (120, 22)),
    ('email', 'qf_email', 'text', (220, 22)),
    ('phone', 'qf_phone', 'text', (160, 22)),
    ('number', 'qf_number', 'text', (100, 22)),
    ('dropdown', 'qf_dropdown', 'combo', (160, 22)),
    ('list', 'qf_list', 'list', (160, 60)),
    ('checkbox', 'qf_checkbox', 'checkbox', (14, 14)),
    ('radio', 'qf_radio', 'radio', (200, 16)),
    ('signature', 'qf_signature', 'signature', (200, 40)),
    ('submit', 'qf_submit', 'button', (180, 32)),
    ('button', 'qf_button', 'button', (140, 28)),
)
BUTTON_PRESETS = ('submit', 'button')
# Actions offered for a generic button, in the order the panel lists them.
BUTTON_ACTIONS = ('reset', 'print', 'url', 'goto', 'javascript')
# Default button colours: green sends, grey clears, blue navigates, prints or runs a script.
BUTTON_COLORS = {'submit': (0.08, 0.58, 0.45), 'reset': (0.42, 0.45, 0.5), 'print': (0.13, 0.36, 0.7),
                 'url': (0.13, 0.36, 0.7), 'goto': (0.13, 0.36, 0.7), 'javascript': (0.13, 0.36, 0.7)}
PRESET_BY_KEY = {key: (label, kind, size) for key, label, kind, size in PRESETS}
EMAIL_SCRIPT = ('if (event.value && !/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(event.value)) '
                '{ app.alert("Enter a valid email address."); event.rc = false; }')
PHONE_SCRIPT = 'if (!event.willCommit && !/^[0-9+()\\-\\s.]*$/.test(event.change)) event.rc = false;'


def style():
    stored = get_setting('form_style') or {}
    result = dict(DEFAULT_STYLE)
    for key, value in stored.items():
        if key in result:
            result[key] = tuple(value) if isinstance(value, list) else value
    return result


def save_style(values):
    current = style()
    current.update(values)
    set_setting('form_style', {k: list(v) if isinstance(v, tuple) else v for k, v in current.items()})


def field_name(text, existing, fallback='field'):
    """A unique snake_case field name from a label."""
    text = re.sub(r'\([^)]*\)|\[[^\]]*\]', ' ', text or '')  # drop hints like "(YYYY-MM-DD)"
    words = re.sub(r'[^0-9a-zA-Z]+', ' ', text).lower().split()
    base = ''
    for word in words:
        if base and len(base) + 1 + len(word) > 32:
            break
        base = f'{base}_{word}' if base else word
    base = base[:32] or fallback
    name, number = base, 2
    while name in existing:
        name, number = f'{base}_{number}', number + 1
    return name


def clean_label(text):
    return re.sub(r'[\s:*]+$', '', re.sub(r'\s+', ' ', text or '')).strip()


def nearby_label(page, rect, right=False):
    """Text that labels a field: the line to its left, else the nearest line just above.
    With right=True (checkboxes) the text just right of the box wins."""
    rect = fitz.Rect(rect)
    lines = {}
    for x0, y0, x1, y1, word, block, line, _n in page.get_text('words'):
        lines.setdefault((block, line), []).append((fitz.Rect(x0, y0, x1, y1), word))
    candidates = []
    for words in lines.values():
        box = fitz.Rect(words[0][0])
        for rect_, _word in words:
            box |= rect_
        candidates.append((box, [word for _r, word in words], 0))
        # Labels of side-by-side fields can share one text line; also try the part above this field.
        above = [(r, w) for r, w in words if r.x0 >= rect.x0 - 4 and r.x0 < rect.x1]
        if above and len(above) < len(words):
            part = fitz.Rect(above[0][0])
            for rect_, _word in above:
                part |= rect_
            candidates.append((part, [word for _r, word in above], -0.5))
    best, best_score = None, None
    for box, words, bonus in candidates:
        if box.intersects(rect):
            continue
        middle = (box.y0 + box.y1) / 2
        beside = rect.y0 - 4 <= middle <= rect.y1 + 4
        if right and beside and box.x0 >= rect.x1 - 2 and box.x0 - rect.x1 < 60:
            score = -100 + (box.x0 - rect.x1)
        elif beside and box.x1 <= rect.x0 + 2 and rect.x0 - box.x1 < 160:
            score = rect.x0 - box.x1
        elif box.y1 <= rect.y0 + 2 and rect.y0 - box.y1 < 28 and box.x0 < rect.x1 and box.x1 > rect.x0 - 4:
            score = (rect.y0 - box.y1) * 2 + abs(box.x0 - rect.x0) * 0.2
        else:
            continue
        score += bonus
        if best_score is None or score < best_score:
            best, best_score = ' '.join(words), score
    return clean_label(best) if best else ''


# ---------------------------------------------------------------- snapping

def snap_rect(rect, others, threshold, edges=('x0', 'x1', 'y0', 'y1'), move=False, page=None):
    """Snap a rect to edges/centres of other rects. Returns (rect, guides).

    With move=True the whole rect shifts so its nearest edge or centre lines up;
    otherwise only the given (moving) edges snap. Guides are ('v', x) / ('h', y).
    """
    rect = fitz.Rect(rect)
    xs, ys = [], []
    for other in others:
        o = fitz.Rect(other)
        xs += [o.x0, o.x1, (o.x0 + o.x1) / 2]
        ys += [o.y0, o.y1, (o.y0 + o.y1) / 2]
    if page is not None:
        xs.append(page.width / 2)
        ys.append(page.height / 2)
    guides = []

    def nearest(value, candidates):
        found = min(candidates, key=lambda c: abs(c - value), default=None)
        return found if found is not None and abs(found - value) <= threshold else None

    if move:
        best = None
        for value in (rect.x0, rect.x1, (rect.x0 + rect.x1) / 2):
            target = nearest(value, xs)
            if target is not None and (best is None or abs(target - value) < abs(best[0] - best[1])):
                best = (target, value)
        if best:
            rect += (best[0] - best[1], 0, best[0] - best[1], 0)
            guides.append(('v', best[0]))
        best = None
        for value in (rect.y0, rect.y1, (rect.y0 + rect.y1) / 2):
            target = nearest(value, ys)
            if target is not None and (best is None or abs(target - value) < abs(best[0] - best[1])):
                best = (target, value)
        if best:
            rect += (0, best[0] - best[1], 0, best[0] - best[1])
            guides.append(('h', best[0]))
        return rect, guides
    for edge in edges:
        horizontal = edge in ('x0', 'x1')
        target = nearest(getattr(rect, edge), xs if horizontal else ys)
        if target is not None:
            setattr(rect, edge, target)
            guides.append(('v' if horizontal else 'h', target))
    return rect, guides


def draw_guides(cr, guides, page_rect, zoom):
    if not guides:
        return
    cr.save()
    cr.set_source_rgba(0.93, 0.2, 0.55, 0.9)
    cr.set_line_width(1 / zoom)
    cr.set_dash([4 / zoom, 3 / zoom], 0)
    for kind, value in guides:
        if kind == 'v':
            cr.move_to(value, 0)
            cr.line_to(value, page_rect.height)
        else:
            cr.move_to(0, value)
            cr.line_to(page_rect.width, value)
    cr.stroke()
    cr.restore()


# ---------------------------------------------------------------- tab order

def tab_order(doc, page_number, mode='rows'):
    """Reorder a page's widgets by rows (left to right, top to bottom) or columns."""
    page = doc[page_number]
    kind, value = doc.xref_get_key(page.xref, 'Annots')
    if kind == 'xref':
        annots_xref = int(value.split()[0])
        value = doc.xref_object(annots_xref)
    else:
        annots_xref = None
    refs = [int(r) for r in re.findall(r'(\d+)\s+0\s+R', value)]
    widgets = {w.xref: fitz.Rect(w.rect) for w in page.widgets()}
    ordered = [r for r in refs if r in widgets]
    if not ordered:
        return 0
    heights = sorted(widgets[r].height for r in ordered)
    tolerance = max(4.0, heights[len(heights) // 2] / 2)
    if mode == 'columns':
        key = lambda r: (round(widgets[r].x0 / tolerance), widgets[r].y0)
    else:
        key = lambda r: (round(widgets[r].y0 / tolerance), widgets[r].x0)
    ordered.sort(key=key)
    others = [r for r in refs if r not in widgets]
    array = '[' + ' '.join(f'{r} 0 R' for r in ordered + others) + ']'
    if annots_xref:
        doc.update_object(annots_xref, array)
    else:
        doc.xref_set_key(page.xref, 'Annots', array)
    doc.xref_set_key(page.xref, 'Tabs', '/C' if mode == 'columns' else '/R')
    doc._reset_page_refs()
    return len(ordered)


# ---------------------------------------------------------------- detection

def detect_fields(page):
    """Find empty boxes, checkbox squares and fill-in lines on a page without form fields there."""
    existing = [fitz.Rect(w.rect) for w in page.widgets()]
    words = [fitz.Rect(w[:4]) for w in page.get_text('words')]
    found = []

    def free(rect):
        return not any(rect.intersects(e) for e in existing + [f[1] for f in found])

    def empty(rect):
        inner = rect + (2, 2, -2, -2)
        return not any(inner.intersects(w) for w in words)
    for drawing in page.get_drawings():
        for item in drawing['items']:
            if item[0] == 're':
                rect = fitz.Rect(item[1])
            elif item[0] == 'qu':
                rect = fitz.Quad(item[1]).rect
            elif item[0] == 'l':
                p, q = item[1], item[2]
                if abs(p.y - q.y) < 0.8 and abs(q.x - p.x) >= 60:
                    x0, x1 = sorted((p.x, q.x))
                    rect = fitz.Rect(x0, p.y - 16, x1, p.y - 1)
                    if free(rect) and empty(rect):
                        found.append(('text', rect))
                continue
            else:
                continue
            w, h = rect.width, rect.height
            fill = drawing.get('fill')
            # Large dark fills are decoration (headers, buttons), not input boxes.
            if fill is not None and sum(fill[:3]) / 3 < 0.85:
                continue
            if 7 <= w <= 22 and 7 <= h <= 22 and abs(w - h) <= 3:
                kind = 'checkbox'
            elif w >= 40 and 12 <= h <= 120 and w > h:
                kind = 'multiline' if h > 34 else 'text'
            else:
                continue
            if free(rect) and empty(rect):
                found.append((kind, rect))
    return found


# ---------------------------------------------------------------- creation

def field_options(preset, values):
    """create_form_field options for a preset using the form style."""
    s = style()
    options = {key: s[key] for key in APPEARANCE_KEYS}
    if preset == 'multiline':
        options['multiline'] = True
    if preset in BUTTON_PRESETS:
        action = 'submit' if preset == 'submit' else values.get('action', 'reset')
        if action not in BUTTON_COLORS:
            raise ValueError('Choose a supported button action.')
        options['fill_color'] = values.get('button_color', BUTTON_COLORS[action])
        options['text_color'] = (1, 1, 1)
        options['border_color'] = None
        options['font_size'] = 0
        page = int(values.get('page', 0) or 0)
        default = {'submit': 'Submit', 'reset': 'Reset form', 'print': 'Print', 'url': 'Open link',
                   'goto': f'Go to page {page + 1}', 'javascript': 'Run'}[action]
        options['button_caption'] = values.get('caption') or default
        options['button_action'] = action
        if action == 'submit':
            options.update(button_url=values.get('url', ''), button_format=values.get('format', 'html'))
        elif action == 'url':
            options['button_url'] = values.get('url', '')
        elif action == 'goto':
            options['button_page'] = page
        elif action == 'javascript':
            options['button_script'] = values.get('script', '')
    if preset == 'checkbox':
        options.pop('font', None)
        options.pop('font_size', None)
    return options


def behaviour(preset):
    if preset == 'date':
        return {'format': 'date', 'date_format': style()['date_format']}
    if preset == 'number':
        return {'format': 'number', 'decimals': 2, 'separator': 0, 'negative': 0, 'currency': ''}
    return None


def add_scripts(doc, xref, preset):
    from .ops.javascript import set_field_script
    if preset == 'email':
        set_field_script(doc, xref, 'V', EMAIL_SCRIPT)
    elif preset == 'phone':
        set_field_script(doc, xref, 'K', PHONE_SCRIPT)


def label_object(text, rect, page_number, required=False):
    """A canvas text label placed above or left of a field rect (None if labels are off)."""
    s = style()
    if s['label_position'] == 'none' or not text:
        return None
    from .ai.tools import new_text_object, _font, match_font
    family, _note = match_font(s['label_font'])
    size = float(s['label_size'])
    label = text + (' *' if required else '')
    font = _font(family)
    height = (font.ascender - font.descender) * size
    rect = fitz.Rect(rect)
    if s['label_position'] == 'left':
        width = font.text_length(label, fontsize=size)
        x, y = rect.x0 - width - 6, (rect.y0 + rect.y1) / 2 - height / 2
    else:
        x, y = rect.x0, rect.y0 - height - 2
    color = '#' + ''.join(f'{round(c * 255):02x}' for c in s['label_color'])
    return new_text_object({'text': label, 'x': max(0, x), 'y': max(0, y), 'size': size, 'font': family,
                            'color': color}, page_number, [])
