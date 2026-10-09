# Changelog

## 0.4.2 — 2026-10-09

- Clicking an element to select it no longer moves it: a drag now starts only after the
  pointer travels a few pixels, so a slight hand movement no longer nudges or snaps
  text, images, shapes, lines, or tables.
- Moving, resizing, or deleting an original table cell or shape keeps the text above it
  visible and no longer adds black outlines to fill-only cells.
- Editing a shape no longer erases or reorders neighbouring graphics: adjacent cells,
  table grids, background bars, page backgrounds, and links stay exactly as they were.
- Shapes on top of background images stay visible after editing.
- Rounded boxes, triangles, and other original outlines keep their exact shape when
  edited, and curved lines stay curved.
- Thin rules and separator lines drawn as narrow rectangles can now be selected and
  edited like any other line.

## 0.4.1 — 2026-10-06

- Redesigned the PDF search bar with integrated result counts, navigation,
  and a single menu for match options, Find and Replace, and Advanced Search.
- Replaced the arrow-shaped clear-search icon with a simple close icon.
- Added a GitHub release update check inside About, with a compact status
  and a link to a newer release when available.
- Restyled the license viewer with readable paragraphs, section headings,
  improved spacing, and a dedicated reading area.

## 0.4 — 2026-10-04

- **AI assistant** (Ctrl+K): a floating or docked bar that edits the open document through
  OpenRouter with the model you choose. It changes one or many elements at once, adds and
  deletes pages, opens new documents, builds fillable forms, and recreates a page from an
  attached picture or PDF. Each request is a single undo step; the API key is kept in the
  system keyring.
- **Form builder**: Build & Fill Forms opens a sidebar where you type a label, pick a field
  type (text, paragraph, date, email, phone, number, dropdown, list, checkbox, radio,
  signature, submit, reset), and drag on the page. Fields get names, labels, tooltips, a
  shared form style, and input checks. Also multiple copies, apply/match style, tab order,
  field detection on printed forms, and a per-field JavaScript editor with examples.
- **Form field arrangement**: select several fields with Shift/Ctrl-click to align,
  distribute, and make them the same size; drag them together.
- **Form submission**: submit buttons send the form (HTML, FDF, XFDF, or PDF) after
  confirmation and show the server's answer; mailto buttons open the email app.
- **Shapes and lines**: rounded rectangles, triangles, diamonds, pentagons, hexagons,
  stars, block arrows, and speech callouts; corner radius, opacity, dashed and dotted
  outlines, no-outline fills; straight Line and Arrow tools with angle snapping.
- **Stacking order**: Bring to Front and Send to Back (Ctrl+] / Ctrl+[), including
  shapes placed behind the PDF's own text.
- **Find and Replace** (Ctrl+H) with a reviewable list of changes, regular expressions,
  and one undo step.
- **Sensitive-data redaction**: find emails, web addresses, IBANs and card numbers
  (checksum-verified), dates, phone numbers, and ID numbers.
- **Organize Pages**: a thumbnail grid to reorder by drag and drop, rotate, duplicate,
  delete, insert, and extract pages.
- **Alignment guides** for text, images, shapes, drawings, and form fields.
- Reorganized main menu and left toolbar, grouped by task with the most used tools first.
- Fixed editing text making it jump from its position, and an undo problem that could
  stop the next text edit with "cannot find object in xref".

## 0.3.1 — 2026-10-03

- Fixed table borders just below a cell's text being mistaken for an underline. Editing
  such text no longer erases part of the border or adds an underline to the text.

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
