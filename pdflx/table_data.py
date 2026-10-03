"""Delimited table interchange shared by CSV imports and the clipboard."""
import csv
import io

MAX_ROWS = 100
MAX_COLUMNS = 20
MAX_CELLS = 1000
MAX_BYTES = 2 * 1024 * 1024


def validate_cells(cells):
    if not cells or not cells[0]:
        raise ValueError('The table is empty.')
    columns = max(len(row) for row in cells)
    if len(cells) > MAX_ROWS or columns > MAX_COLUMNS or len(cells)*columns > MAX_CELLS:
        raise ValueError(f'Use up to {MAX_ROWS} rows, {MAX_COLUMNS} columns, and {MAX_CELLS} cells. Split larger tables before importing.')
    return [list(row) + ['']*(columns-len(row)) for row in cells]


def parse_table(text, delimiter=None):
    """Read quoted CSV/TSV without stripping whitespace, zeroes, or empty cells."""
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Choose a table smaller than 2 MB.')
    text = text.lstrip('\ufeff')
    if not text.strip():
        raise ValueError('The table is empty.')
    if '\x00' in text:
        raise ValueError('The table contains invalid text.')
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:50000], delimiters='\t,;|').delimiter
        except csv.Error:
            # Irregular spreadsheet rows often omit trailing cells.
            delimiter = '\t' if '\t' in text else ','
    if delimiter not in ('\t', ',', ';', '|'):
        raise ValueError('Choose a supported separator.')
    try:
        reader = csv.reader(io.StringIO(text, newline=''), delimiter=delimiter, strict=True)
        cells = []
        for row in reader:
            if row:  # Ignore blank records, preserving explicitly empty cells.
                cells.append(row)
                if len(cells) > MAX_ROWS or len(row) > MAX_COLUMNS:
                    raise ValueError(f'Use up to {MAX_ROWS} rows and {MAX_COLUMNS} columns.')
    except csv.Error as error:
        raise ValueError(f'Could not read the table: {error}') from error
    return validate_cells(cells)


def read_csv(path, delimiter=None):
    with open(path, 'rb') as source:
        data = source.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Choose a CSV smaller than 2 MB.')
    encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        # Common CSV export encoding from older Windows spreadsheet applications.
        text = data.decode('cp1252')
    return parse_table(text, delimiter)


def to_tsv(cells):
    out = io.StringIO(newline='')
    csv.writer(out, delimiter='\t', lineterminator='\n').writerows(cells)
    return out.getvalue()


def selection_cells(table, page):
    """Recover current cells, including edited text and tables saved by older builds."""
    from .models import EditableShape, EditableText
    import pymupdf as fitz
    shapes = [obj for obj in table.objects if isinstance(obj, EditableShape)]
    texts = [obj for obj in table.objects if isinstance(obj, EditableText)]
    if not shapes:
        raise ValueError('No table cells were found.')
    bounds = [fitz.Rect(obj.bbox)*page.rotation_matrix for obj in shapes]
    # Geometry also handles tables created before cell coordinates were persisted.
    def positions(values):
        result = []
        for value in sorted(values):
            if not result or abs(value-result[-1]) > .5:
                result.append(value)
        return result
    rows = positions(rect.y0 for rect in bounds)
    columns = positions(rect.x0 for rect in bounds)
    indexed = all(isinstance(getattr(obj, 'table_row', None), int)
                  and isinstance(getattr(obj, 'table_column', None), int) for obj in shapes)
    if indexed:
        rows = range(max(obj.table_row for obj in shapes)+1)
        columns = range(max(obj.table_column for obj in shapes)+1)
    cells = [['' for _ in columns] for _ in rows]
    for shape, rect in zip(shapes, bounds):
        row = shape.table_row if indexed else min(range(len(rows)), key=lambda i: abs(rows[i]-rect.y0))
        column = shape.table_column if indexed else min(range(len(columns)), key=lambda i: abs(columns[i]-rect.x0))
        values = []
        for text in texts:
            center = (fitz.Rect(text.bbox).tl+fitz.Rect(text.bbox).br)/2
            coordinates = (getattr(text, 'table_row', None), getattr(text, 'table_column', None))
            belongs = coordinates == (row, column) if indexed and None not in coordinates else center in fitz.Rect(shape.bbox)
            if belongs:
                value = text.text
                if value == getattr(text, 'table_wrapped_text', None):
                    value = text.table_source_text
                values.append(value)
        cells[row][column] = '\n'.join(values)
    return cells
