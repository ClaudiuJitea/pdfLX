"""Conversation state and the tool-calling loop. Network calls run on a worker thread;
tools run on the GTK main thread."""
import base64
import json
import threading

import pymupdf as fitz
from gi.repository import GLib

from . import client, config
from .tools import tool_definitions

SYSTEM_PROMPT = """You are the AI assistant inside pdfLX, a PDF editor. You change the user's open \
document directly with the tools; the editor shows every change live, and the user can undo your whole \
request with one Ctrl+Z.

How the document works:
- Pages are numbered from 1. Positions and sizes are PDF points (1 pt = 1/72 inch) on the unrotated \
page, origin at the top-left corner, y growing downwards. A4 is 595 x 842 pt, US Letter 612 x 792 pt.
- list_elements gives every editable element on a page with an id. Ids change when a page reloads, so \
list a page again after adding or deleting pages and before editing elements you have not listed.
- Change many elements in one edit_elements or add_elements call rather than one call per element.
- A text element's x, y is the top-left of its box. Lines in one text element are 1.2 x size apart; for \
other spacing use one text element per line. Give a width to wrap or center/right-align text in a box.
- Elements stack: later ones are drawn over earlier ones. Filled rectangles and ellipses go behind \
everything by default (layer "back"), so draw colored bands and cell backgrounds, then put text on them. \
Use layer "front" for a shape that must cover something, and edit_elements with layer to restack.
- Shapes: rectangle (corner_radius for rounded corners), ellipse, polygon (sides), right_triangle, star, \
arrow, callout, checkmark, cross, with fill, outline, opacity and dashed/dotted outlines; lines can have \
arrowheads.
- Colors are hex strings. Fonts must be installed; list_fonts shows them, and unknown fonts are replaced \
by a close match (reported back to you).
- Some page content (annotations, complex vector art) is not listed; render_page shows the whole page.

Working style:
- If the user's request is clear, act; do not ask for confirmation. Ask only when a request is genuinely \
ambiguous or would destroy content the user did not mention.
- Do not delete or change content the user did not ask about.
- When elements are selected in the editor, the message says which ones; "this", "these" or "the \
selection" refer to them.
- After visual changes, check the result with render_page and fix problems before answering.
- Finish with one or two plain sentences saying what you changed. No markdown headings.

Forms:
- Form fields are real fillable PDF fields: put a text label above or beside each one (text elements), \
then the field. Match the picture's box colors with fill_color/border_color; use format for dates, \
emails, phones and numbers, multiline for large text areas, combo for dropdowns, and a button with \
action "submit" or "reset" for buttons. Give fields short unique snake_case names from their labels.
- When recreating a form from a picture, make every input box a field (not a rectangle), and \
section headers, labels and decorations ordinary elements.

Recreating a page from a picture:
1. Work out the page size from the picture's aspect ratio (a document photo or scan is usually A4 or \
Letter). Use new_document when no document is open or the user wants a new PDF; otherwise add_page, \
unless the user asks to draw on an existing page.
2. Convert picture pixels to points: pt = px x page_width_pt / picture_width_px (same factor for y when \
the aspect ratio matches).
3. Measure carefully: use view_attachment with grid=true, and zoom into regions for small text, table \
lines and colors. Estimate font size from the height of capital letters (cap height is about 0.7 x size).
4. Rebuild everything as editable elements: backgrounds, colored areas and table header bands \
(rectangles), borders and table rules (lines or thin rectangles), and every piece of text - including \
text inside colored bands, table headers and cells - as text elements with its exact wording, line \
breaks, weight, size, color and alignment. Use serif, sans or mono fonts matching the picture.
   Image elements cropped from the attachment are only for logos, photos, signatures, stamps, QR codes, \
barcodes and illustrations, cropped tightly. Never crop text, tables or bands into images: the user \
must be able to edit the result.
5. render_page (with grid when useful) and compare it to the picture region by region; correct \
positions, sizes and colors. Repeat until the page closely matches."""

IMAGE_TYPES = {'png': 'image/png', 'jpeg': 'image/jpeg', 'jpg': 'image/jpeg', 'webp': 'image/webp',
               'gif': 'image/gif', 'bmp': 'image/bmp', 'tiff': 'image/tiff', 'tif': 'image/tiff'}
SEND_LONG_SIDE = 2048
KEEP_TOOL_IMAGES = 3


def load_attachment(name, data):
    """Turn an uploaded file into attachments: images as-is, PDF pages as images."""
    kind = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    if kind == 'pdf' or data[:4] == b'%PDF':
        items = []
        with fitz.open(stream=data, filetype='pdf') as pdf:
            for number, page in enumerate(pdf):
                if number >= 10:
                    break
                pix = page.get_pixmap(dpi=150, alpha=False)
                items.append({'name': f'{name} p{number + 1}', 'type': 'png', 'bytes': pix.tobytes('png'),
                              'width': pix.width, 'height': pix.height})
        return items
    try:
        pix = fitz.Pixmap(data)
    except Exception:
        raise client.AiError(f'{name} is not an image or PDF that can be attached.') from None
    if kind not in IMAGE_TYPES or kind in ('bmp', 'tiff', 'tif', 'gif'):
        if pix.alpha:
            pix = fitz.Pixmap(pix, 0)
        data, kind = pix.tobytes('png'), 'png'
    return [{'name': name, 'type': 'jpeg' if kind == 'jpg' else kind, 'bytes': data,
             'width': pix.width, 'height': pix.height}]


def image_part(data, kind='png'):
    """An OpenAI-style image part, downscaled for upload if it is very large."""
    try:
        pix = fitz.Pixmap(data)
        if max(pix.width, pix.height) > SEND_LONG_SIDE:
            factor = SEND_LONG_SIDE / max(pix.width, pix.height)
            if pix.alpha:
                pix = fitz.Pixmap(pix, 0)
            pix = fitz.Pixmap(pix, int(pix.width * factor), int(pix.height * factor), None)
            data, kind = pix.tobytes('jpeg', jpg_quality=90), 'jpeg'
    except Exception:
        pass
    url = f'data:{IMAGE_TYPES.get(kind, "image/png")};base64,{base64.b64encode(data).decode("ascii")}'
    return {'type': 'image_url', 'image_url': {'url': url}}


class Conversation:
    def __init__(self):
        self.messages = []
        self.attachments = []
        self.cost = 0.0
        self.tokens = 0

    def add_user(self, text, attachments, context):
        parts = []
        lines = []
        for item in attachments:
            self.attachments.append(item)
            number = len(self.attachments)
            lines.append(f'Attachment {number}: {item["name"]}, {item["width"]}x{item["height"]} px.')
            parts.append(image_part(item['bytes'], item['type']))
        header = '\n'.join(filter(None, [context, *lines]))
        parts.insert(0, {'type': 'text', 'text': (f'[{header}]\n\n' if header else '') + text})
        self.messages.append({'role': 'user', 'content': parts})

    def payload(self):
        """Messages to send, keeping only the most recent tool screenshots."""
        recent = [i for i, m in enumerate(self.messages) if m.get('_tool_images')][-KEEP_TOOL_IMAGES:]
        result = [{'role': 'system', 'content': SYSTEM_PROMPT}]
        for index, message in enumerate(self.messages):
            if message.get('_tool_images') and index not in recent:
                result.append({'role': 'user', 'content': '[An earlier screenshot was removed to save space.]'})
                continue
            result.append({key: value for key, value in message.items() if not key.startswith('_')})
        return result


class AgentRun:
    """One user request: loops model -> tools until the model answers or the step limit."""
    def __init__(self, conversation, tools, emit):
        self.conversation = conversation
        self.tools = tools
        self.emit = emit  # called on the main thread with (kind, data)
        self.cancelled = False
        self.key = config.api_key()
        self.base_url = config.base_url()
        self.model = config.model()
        self.max_steps = config.max_steps()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def cancel(self):
        self.cancelled = True

    def _post(self, kind, data=None):
        GLib.idle_add(lambda: (self.emit(kind, data), False)[1])

    def _main(self, function):
        """Run function on the main thread and wait for its result."""
        done = threading.Event()
        box = {}
        def call():
            try:
                box['value'] = function()
            except Exception as error:
                box['error'] = error
            done.set()
            return False
        GLib.idle_add(call)
        done.wait()
        if 'error' in box:
            raise box['error']
        return box['value']

    def _run(self):
        definitions = tool_definitions()
        try:
            for step in range(self.max_steps):
                if self.cancelled:
                    break
                self._post('thinking', step + 1)
                message, usage, finish = client.chat(self.base_url, self.key, self.model,
                                                     self.conversation.payload(), definitions)
                self.conversation.cost += float(usage.get('cost') or 0)
                self.conversation.tokens += int(usage.get('total_tokens') or 0)
                calls = message.get('tool_calls') or []
                stored = {key: value for key, value in message.items() if value is not None}
                stored['role'] = 'assistant'
                stored.setdefault('content', '')
                self.conversation.messages.append(stored)
                text = message.get('content')
                if isinstance(text, list):
                    text = ''.join(part.get('text', '') for part in text if isinstance(part, dict))
                if text and text.strip():
                    self._post('assistant', text.strip())
                if not calls:
                    if finish == 'length':
                        self._post('error', 'The answer was cut off by the model output limit.')
                    break
                self._answer_calls(calls)
            else:
                self._post('error', f'Stopped after {self.max_steps} steps. Ask again to continue, '
                                    'or raise the step limit in AI settings.')
        except client.AiError as error:
            self._post('error', str(error))
        except Exception as error:
            self._post('error', f'{type(error).__name__}: {error}')
        finally:
            self._close_open_calls()
            self._post('done', None)

    def _answer_calls(self, calls):
        images = []
        for call in calls:
            function = call.get('function') or {}
            name = function.get('name', '')
            if self.cancelled:
                result = json.dumps({'error': 'Cancelled by the user.'})
            else:
                try:
                    arguments = json.loads(function.get('arguments') or '{}')
                    if not isinstance(arguments, dict):
                        raise ValueError
                except ValueError:
                    result, shots = json.dumps({'error': 'Arguments were not valid JSON.'}), []
                else:
                    self._post('tool', (name, arguments))
                    result, shots = self._main(lambda: self.tools.run(name, arguments))
                    self._post('tool_result', (name, arguments, result))
                    images.extend(shots)
            self.conversation.messages.append({'role': 'tool', 'tool_call_id': call.get('id'), 'content': result})
        if images:
            # Tool messages are text-only for most providers; screenshots follow as a user turn.
            parts = [{'type': 'text', 'text': 'Screenshot(s) from the last tool call:'}]
            parts.extend(image_part(image) for image in images)
            self.conversation.messages.append({'role': 'user', 'content': parts, '_tool_images': True})

    def _close_open_calls(self):
        """Keep the history valid if a run stopped between a tool request and its results."""
        messages = self.conversation.messages
        answered = {m.get('tool_call_id') for m in messages if m.get('role') == 'tool'}
        for message in reversed(messages):
            if message.get('role') == 'assistant':
                for call in message.get('tool_calls') or []:
                    if call.get('id') not in answered:
                        messages.append({'role': 'tool', 'tool_call_id': call.get('id'),
                                         'content': json.dumps({'error': 'Not run.'})})
                break
