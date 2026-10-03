"""Lay out native editable table cells with measured text and column widths."""
import math
import uuid

import pymupdf as fitz

from .models import EditableShape, EditableText
from . import pdf_handler
from .table_data import MAX_ROWS, MAX_COLUMNS, MAX_CELLS


def _wrap(text, font, size, width):
    lines = []
    for paragraph in text.split('\n'):
        line = ''
        for character in paragraph:
            if font.text_length(line + character, fontsize=size) > width:
                if not line:
                    raise ValueError('The columns are too narrow for this font size.')
                split = line.rfind(' ')
                if split > 0:
                    lines.append(line[:split])
                    line = line[split+1:] + character
                else:
                    lines.append(line)
                    line = character
            else:
                line += character
        lines.append(line)
    return lines


def create_table_objects(page, cells, width_percent=80, font_size=10,
                         header=True, row_height=32, fit_columns=False):
    """Return native shapes/text, centered and upright on the visible page.

    Validate and lay out everything before changing the PDF. Rows grow to fit
    wrapped content; oversized tables are rejected instead of clipping text.
    """
    if (not cells or not cells[0] or len(cells) > MAX_ROWS
            or len(cells[0]) > MAX_COLUMNS or len(cells)*len(cells[0]) > MAX_CELLS):
        raise ValueError(f'Choose up to {MAX_ROWS} rows, {MAX_COLUMNS} columns, and {MAX_CELLS} cells.')
    columns = len(cells[0])
    if any(len(row) != columns for row in cells):
        raise ValueError('All rows must have the same number of cells.')
    if any(not isinstance(value, str) for row in cells for value in row):
        raise ValueError('Cell contents must be text.')
    if not all(math.isfinite(v) for v in (width_percent, font_size, row_height)):
        raise ValueError('Table dimensions must be finite.')
    if not 20 <= width_percent <= 95 or not 6 <= font_size <= 24 or not 10 <= row_height <= 100:
        raise ValueError('Invalid table dimensions or font size.')
    width = page.rect.width * width_percent / 100
    padding = 6
    content_width = width / columns - 2 * padding
    if content_width <= 0:
        raise ValueError('The columns are too narrow. Use fewer columns or a wider table.')
    fonts = {}
    all_text = '\n'.join(value for row in cells for value in row)
    for bold in (False, True):
        sample = EditableText(0, 0, all_text, font_family='DejaVu Sans')
        sample.is_bold = bold
        args, error = pdf_handler._get_font_args_for_pymupdf(sample)
        if error:
            raise ValueError(error)
        fonts[bold] = fitz.Font(**args)
    widths = [width/columns]*columns
    if fit_columns:
        minimum = 2*padding + max(font.text_length('W', fontsize=font_size) for font in fonts.values())
        if minimum*columns >= width:
            raise ValueError('The columns are too narrow. Use fewer columns or a wider table.')
        weights = [math.sqrt(max(1, max(fonts[bool(header and r == 0)].text_length(
            line[:200], fontsize=font_size) for r, row in enumerate(cells)
            for line in row[c].split('\n')))) for c in range(columns)]
        widths = [minimum+(width-minimum*columns)*weight/sum(weights) for weight in weights]
    wrapped, heights = [], []
    for row_index, row in enumerate(cells):
        font = fonts[bool(header and row_index == 0)]
        lines = [_wrap(value, font, font_size, widths[c]-2*padding) for c, value in enumerate(row)]
        wrapped.append(lines)
        text_height = ((max(len(cell) for cell in lines)-1) * font_size * 1.2
                       + (font.ascender-font.descender) * font_size)
        heights.append(max(row_height, text_height + 2*padding))
    height = sum(heights)
    if height > page.rect.height - 32:
        raise ValueError('This table is too tall for the page. Reduce rows, text, row height, or font size.')
    left = (page.rect.width-width)/2
    top = (page.rect.height-height)/2
    shapes, texts = [], []
    for row_index, (row, height) in enumerate(zip(wrapped, heights)):
        cell_left = left
        for cell_index, cell_width in enumerate(widths):
            cell_rect = fitz.Rect(cell_left, top, cell_left+cell_width, top+height)
            cell_left += cell_width
            shape = EditableShape('rectangle', tuple(cell_rect * page.derotation_matrix),
                                  stroke_color=(0.45, 0.47, 0.5), stroke_width=0.75,
                                  page_number=page.number, is_new=True, is_transparent=True)
            shape.table_row, shape.table_column = row_index, cell_index
            shapes.append(shape)
            content = '\n'.join(row[cell_index])
            if content.strip():
                bold = bool(header and row_index == 0)
                font = fonts[bold]
                text_rect = cell_rect + (padding, padding, -padding, -padding)
                center = (text_rect.tl + text_rect.br)/2 * page.derotation_matrix
                bounds = fitz.Rect(center.x-text_rect.width/2, center.y-text_rect.height/2,
                                   center.x+text_rect.width/2, center.y+text_rect.height/2)
                text = EditableText(bounds.x0, bounds.y0, content, font_size=font_size,
                                    font_family='DejaVu Sans', is_new=True,
                                    baseline=bounds.y0+font.ascender*font_size,
                                    rotation=(-page.rotation) % 360, page_number=page.number)
                text.is_bold = bold
                text.bbox = tuple(bounds)
                text.original_bbox = text.bbox
                text.table_source_text = cells[row_index][cell_index]
                text.table_wrapped_text = content
                text.table_row, text.table_column = row_index, cell_index
                texts.append(text)
        top += height
    objects = shapes + texts
    group_id = uuid.uuid4().hex
    for obj in objects:
        obj.table_id = group_id
    return objects


class TableSelection:
    """Selection bounds for a table's native cell and text objects."""
    def __init__(self, objects):
        self.objects = list(objects)
        self.page_number = self.objects[0].page_number
        self.rotation = 0

    @property
    def bbox(self):
        cells = [obj for obj in self.objects if isinstance(obj, EditableShape)]
        return (min(obj.bbox[0] for obj in cells), min(obj.bbox[1] for obj in cells),
                max(obj.bbox[2] for obj in cells), max(obj.bbox[3] for obj in cells))

    def transform(self, states, start_bbox, new_bbox):
        """Transform from the gesture's initial geometry, without accumulating offsets."""
        x0, y0, x1, y1 = start_bbox
        nx0, ny0, nx1, ny1 = new_bbox
        sx, sy = (nx1-nx0)/(x1-x0), (ny1-ny0)/(y1-y0)
        for obj, state in zip(self.objects, states):
            bx0, by0, bx1, by1 = state['bbox']
            obj.bbox = (nx0+(bx0-x0)*sx, ny0+(by0-y0)*sy,
                        nx0+(bx1-x0)*sx, ny0+(by1-y0)*sy)
            obj.x, obj.y = obj.bbox[:2]
            if isinstance(obj, EditableText):
                obj.font_size = state['font_size'] * min(sx, sy)
                obj.baseline = ny0 + (state['baseline']-y0)*sy
            else:
                obj.stroke_width = state['stroke_width'] * min(sx, sy)
