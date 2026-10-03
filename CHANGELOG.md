# Changelog

## 0.3 — 2026-10-03

- One consistent design for every dialog: Cancel · Title · Action header bars, titled
  sections of boxed-list rows, inline error banners, and live previews beside the settings.
- Rebuilt Image Tools inspector with grouped Appearance, Border, and Effects sections; crop
  and drop shadow expand in place.
- New print dialog with a live sheet preview, printer choice, page ranges, paper size,
  automatic orientation, scaling, grayscale, two-sided printing, and Print to File. The
  system dialog remains available for printer-specific options. Faster page rendering.
- Redesigned stamp designer: 14 templates, colour swatches, visual shape picker, details
  line with date/time/author fields, dashed and true double borders, tinted backgrounds,
  italic text, saved custom templates, and Escape to stop placing stamps.
- Element Properties grouped into Position, Size & rotation, Text, and Appearance.
- Fixed text overflowing its box after increasing the font size, and a crash when changing
  the font while editing text without a selection.

## 0.2 — 2026-10-02

- Reorganized the main menu into Document, Pages, Annotate, Insert and Forms, Protect, Tools,
  Export, and View submenus; new libadwaita dialogs with previews, progress, and cancel.
- Page tools: insert from file, extract, split, reverse, collate scans, remove blank pages,
  make rotation permanent, resize, N-up and booklet, backgrounds/overlays, headers, footers
  and Bates numbering, page labels, and page boxes.
- Protection: password and permission settings, sanitization, hidden-text scan, flattening
  to images, signature verification (with PKCS#7 and certificate export), and an
  append-only save that keeps existing signatures valid.
- Conversion and export: open XPS/EPUB/MOBI/FB2/CBZ/SVG/text/images, images to PDF, file
  size optimization, grayscale/CMYK conversion, page images with DPI/colour/transparency,
  structured text (Markdown, JSON, HTML, XML), tables, diagrams, and vector SVG.
- Review: shapes, arrows, text boxes, callouts, insertion marks and attachments as native
  annotations; replies, review states, filters, properties, JSON import/export, comment
  summaries, and annotation flattening.
- Forms: password, comb, and multi-select list fields, border styles, field grouping, and a
  JavaScript inspector/editor. pdfLX does not run scripts.
- Authoring: compose documents from Markdown/HTML with a linked table of contents, formatted
  text boxes, rotated/tiled watermarks, shape primitives, a node editor for drawings, and
  CSV mail merge.
- Document tools: information and statistics, viewer settings, nested and generated
  bookmarks, links, attachments, layers, compare, advanced search, text properties,
  snapshots, e-invoice attachment, portfolios, and a raw object inspector.
- Viewer: two-page and book layouts, presentation mode (F5), measurement, side-by-side pane,
  night/sepia/high-contrast reading, and faster rendering with viewport tiles at high zoom.
- Autosave crash recovery, remembered reading position, recent-file thumbnails, batch
  processing, and the `pdflx-cli` command-line tool.
- Form scripts: filling runs the form's calculation, validation, and format scripts in MuPDF's
  sandboxed JavaScript engine (toggle in the main menu); a Format & Calculation tab writes
  standard Acrobat scripts; date fields get a calendar; buttons can submit, open a URL, print,
  or run JavaScript; form data imports/exports as FDF, XFDF, JSON, or CSV (also `pdflx-cli
  form-import` / `form-export`).
- Requires libadwaita 1.5 or newer and PyMuPDF 1.26 or newer.

## 0.1 — 2026-09-30

- Introduced pdfLX branding and a new application icon.
- Redesigned the document header, sidebar tools, and color selectors.
- Replaced the About dialog with a focused product window and separate license view.

- Added drawn/imported visible signatures and PKCS#12 certificate signing to a separate PDF copy.
