"""GTK-free document operations built on PyMuPDF.

Each submodule works on ``pymupdf.Document`` objects or PDF bytes and never
touches the UI, so features can be tested headlessly and reused by a CLI.
Operations that would invalidate the editor's managed page state return a new
document or bytes instead of mutating the live document.
"""
