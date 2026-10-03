# PyMuPDF feature audit

This audit uses the installed PyMuPDF 1.28.2 API and the editor's current PDF workflow. It distinguishes capabilities in PyMuPDF from features exposed in this application.

| Area | App status |
| --- | --- |
| PDF rendering, zoom, thumbnails, print | Implemented; current page PNG and SVG export added |
| Text, image, vector shape, and stroke editing | Implemented; fidelity and error handling improvements identified below |
| Exact text search with match navigation | Implemented |
| Page insert, delete, reorder, rotate, duplicate, and merge | Implemented |
| Page and document export | Implemented; page range export added |
| PDF highlights | Implemented |
| Standard and XML metadata | Inspect, edit, and clear added under Document Tools |
| PDF bookmarks | Navigate, add, and remove added under Document Tools |
| Form widgets | Create/fill text, checkbox, dropdown, and list fields; flatten appearances while retaining annotations. Signature status is shown without verification |
| Embedded image extraction | Added as a ZIP of original image data |
| Table recognition | Added as one CSV per detected table in a ZIP |
| Password protected PDFs | Open with a password; editing, copying/exporting, and printing follow the supplied credentials' permissions; save preserves encryption |
| Multi-format reading and conversion | The editor opens PDFs only; formats such as EPUB and XPS require a conversion and save workflow |
| Continuous or dual-page viewing | Not implemented in the single-page canvas |
| Regex search | `Page.search_for()` searches literal text, not regular expressions; regex requires separate text extraction and coordinate mapping |
| Tables and symbol insertion | Table creation, whole-table movement/corner scaling, cell text editing, and searchable symbol/emoji palette implemented; table groups persist across navigation and saving/reopening in pdfLX |
| Review annotations, redaction, watermarks, numbering, crop | Implemented with previews, comments sidebar, and undo |
| OCR | PyMuPDF can invoke Tesseract, but Tesseract is not installed in the current environment |
| Signatures | Draw/import visible signatures, place them as editable images, and undo/resize them. Certificate signing with PKCS#12 is implemented using pyHanko, saving a signed copy and preserving existing signed revisions. PyMuPDF signature fields remain read-only. Existing signature status in Forms is not a cryptographic verification result. |
| PyMuPDF4LLM and PyMuPDF Pro | Separate packages; Pro is commercial and neither is an installed dependency |
| Raw PDF object editing | Available through lower-level APIs, but not a meaningful general-purpose editor control |

The pasted feature lists include API names that are absent from the installed version (`Document.garbage_collect`, `Document.get_xref_dict`, `Document.update_xref`, and `Page.finish_path`). The app already saves with `garbage=4` and `deflate=True`; this provides PDF cleanup without a separate garbage collection method.


## Original review findings — 30 September 2026

Reviewed the working tree against installed PyMuPDF 1.28.2 and current official
[Page](https://pymupdf.readthedocs.io/en/latest/page.html),
[Document](https://pymupdf.readthedocs.io/en/latest/document.html),
[Widget](https://pymupdf.readthedocs.io/en/latest/widget.html), and
[Journalling](https://pymupdf.readthedocs.io/en/latest/recipes-journalling.html)
documentation. The following records the original findings before the implementation update below.

### Reliability improvements to prioritize

| Priority | Finding and evidence | Proposed improvement |
| --- | --- | --- |
| High | `pdf_handler.rebuild_page()` discards the return value from `_apply_single_object_to_page()`. With font discovery deliberately disabled, inserting `Ω` returned a font error, while rebuilding returned `(True, None)` and produced an empty page. | Propagate per-object failures, restore the previous page on failure, and record undo history only after successful commits. |
| High | `_load_page(reload_objects=True)` clears the undo manager, including during page navigation. Highlights, metadata, bookmarks, and form changes also bypass the command history. | Keep a document-wide history. Evaluate MuPDF journalling, with explicit operation boundaries for every mutation and synchronization of the editor models. It cannot simply be enabled alongside unwrapped existing mutations. |
| High | Newly created tables use Python `table_id` attributes. Re-extracting a four-cell table produced eight objects and zero objects with a table ID. | Retain per-page object models while navigating; add persistent table metadata or an explicit recognition/re-grouping workflow after reopening. Recognition is approximate and should not classify every rectangle as a table cell. |
| Medium | `_update_table_drag()` rebuilds the whole page and embeds/renders all active new objects on every drag update. | Draw a transient group preview and commit one PDF rebuild on release; measure large tables before setting performance targets. |
| Medium | `save_document(..., incremental=...)` always performs a full rewrite and ignores its incremental argument. | Make the save policy explicit. Preserve the existing atomic full-save path for ordinary edits; add an eligible incremental-save path where appropriate and handle already signed PDFs explicitly. Full rewrites can invalidate existing digital signatures; this risk was identified from the code, not tested on a signed sample in this review. |
| Medium | Table/signature controls now follow Edit mode, but properties, bookmark editing, form updates, and page rotation can still be reached while viewing. | Decide which actions may change a document in View mode and enforce that rule consistently in buttons, actions, and handlers. Read-only inspection can remain available. |
| Medium | `extract_editable_images()` initializes every extracted image with rotation zero and retains its bounding box, but not its original image transform. | Preserve orientation and transforms for imported images and verify moves/rotations against rendered before-and-after samples. |
| Medium | Partial word highlighting estimates positions from character counts; glyph widths vary. | Retain character geometry and use exact text quads for selections, multiline highlights, and rotated text. |
| Medium | In the development environment, certificate signing reports unavailable; full unittest discovery fails importing `asn1crypto`. | Complete and validate the packaged signing dependencies; distinguish signature presence from cryptographic validity in the UI. |

### Improvements to existing tools

- Tables: add/remove rows and columns, cell fill and border styling, column-width controls,
  alignment, and content reflow after editing. Current corner scaling changes font size;
  it does not re-lay out content into new column widths.
- Table extraction: add a preview, region selection, and line/text detection strategy
  controls instead of only exporting all detected tables to CSV ZIP. PyMuPDF exposes
  `find_tables(clip=..., strategy=...)`; spreadsheet export would need an additional
  writer/dependency or a suitable installed package.
- Forms: honor read-only/required flags and length limits; support radio groups,
  editable combo boxes, and multi-select list boxes. Do not assume PDF JavaScript
  calculations are executed by simply updating widget values.
- Text: improve font preservation, missing-glyph feedback, wrapping, and complex-script
  shaping. Consider `insert_htmlbox()`/Story for newly created rich text; this does not
  automatically reconstruct arbitrary existing PDF paragraphs.
- Images: expose crop, replace, opacity, and aspect-ratio locking. Image ZIP extraction
  currently skips inline images with xref zero and does not reconstruct transparency
  from a separate soft mask; offer original-asset and rendered-appearance export modes.
- Symbols: translate categories and names across supported languages, add recent/favorite
  items, and check font coverage before inserting a symbol. Color emojis are raster images,
  so a reusable asset cache could reduce repeated image generation.
- Export/rendering: add DPI, region, and page-range controls for image export and progress/
  cancellation for archive/table exports, which currently run directly in UI callbacks.

### Additional PyMuPDF features suitable for the app

| Feature | User-facing result | API / dependency |
| --- | --- | --- |
| Permanent redaction | Mark areas or search hits, preview, then remove underlying content in a saved copy | `Page.add_redact_annot()`, `Page.apply_redactions()`; a covering rectangle is insufficient |
| OCR | Search/copy scanned text and save searchable scanned PDFs | `Page.get_textpage_ocr()` and `Pixmap.pdfocr_save()` / `pdfocr_tobytes()`; requires Tesseract language data |
| Comments and review | Sticky notes, underline, strikeout, squiggles, stamps, and a comments panel | Annotation creation methods and `Annot` metadata |
| Watermarks and page numbering | Apply text, logos, headers/footers, and numbering to selected pages | `Page.insert_text()`, `insert_image()`, `show_pdf_page()`; `set_page_labels()` controls logical labels separately |
| Crop and page boxes | Crop margins and manage printable page boundaries | `Page.set_cropbox()` and other page-box methods |
| Compression | Save a smaller copy with a quality/size preview | `Document.rewrite_images()`, `subset_fonts()`, and save compression options |
| PDF attachments | Add, list, extract, and remove embedded files | `Document.embfile_*()`; distinct from page image extraction |
| Form designer and flattening | Create input fields and save filled forms as permanent page content | `Page.add_widget()`, `Document.bake(widgets=True)` |
| Link editor | Add/edit web and internal page links | `Page.insert_link()`, `update_link()`, `delete_link()` |
| Layers | Show/hide optional content and manage layer configurations | Optional-content group and layer APIs |
| Additional input formats | Convert supported EPUB, XPS, and image documents into PDFs | `Document.convert_to_pdf()`; a dedicated import/conversion workflow is required |

Continuous scrolling, dual-page viewing, alignment guides, arbitrary object grouping,
and regex search are useful additions too, but require application-level behavior beyond
a single PyMuPDF call.

### Verification and recommended sequence

- 13 table/drag tests passed in the development environment.
- Full discovery additionally hit one signature-module import error: missing `asn1crypto`.
- Reproduced loss of table group IDs on extraction and silent omission of text when font
  resolution fails. Verified installed availability of journalling, attachment,
  image-rewriting, font-subsetting, flattening, conversion, and page-label methods.
- No Tesseract executable was found in the active environment. An OCR implementation
  must also check availability of the needed Tesseract language data.

Recommended order: error propagation and rollback; persistent page/table models and
history; signing dependencies/save policy; table and text editing improvements;
comments/redaction; watermarks/page numbering; OCR and compression.

## Implementation update — 30 September 2026

All six requested reliability improvements and five requested document tools
are implemented. Page rebuilds propagate rendering failures and restore PDF/model
state. History spans navigation and native document mutations. Per-page models
and compressed custom page metadata preserve table grouping after reopening.
Table drag updates render a preview, with one PDF commit on release. Partial
text selection uses actual glyph quads, including rotated text.

Document Tools now includes permanent region/search redaction, review annotations
and a comments sidebar, text/logo watermarks and numbering, crop/reset, form
creation, and form flattening. These operations participate in undo/redo.
Redaction also sanitizes saved editor models; crop only changes page boundaries.
Form creation supports text, checkbox, dropdown, and list fields. Flattening
retains review annotations and refuses signed signature fields.

Ordinary saving compacts a separate document copy so that live PDF xrefs remain
stable for undo. Signing dependencies are installed in the development environment
and included in PyInstaller/Flatpak packaging definitions. Release package builds
have not been run in this update.

Validation: 47 unittest tests passed, including six certificate-signature tests,
saved redaction, reopen/grouping, cross-page history, atomic failures, encrypted
saving, exact text geometry, all supported form types, and flattening appearance.
All six new dialogs and the comments sidebar opened successfully in GTK.

The remaining suggestions above (OCR, compression controls, attachments, links,
layers, additional input formats, image transforms, and advanced form types)
remain future work.

### Toolbar and stamp styling follow-up

Forms has a dedicated fields icon and Document Tools has a distinct wrench icon.
Both editing pickers require Edit mode. Form filling, metadata/bookmark editing,
page rotation, highlighting, and undo/redo now follow the same mode requirement.
View mode retains navigation, search, export, print, and reading comments.

The dedicated stamp tool supports custom text, three font families, font size,
bold, color, no/solid/double borders, and opacity with a live preview. Clicking
a placed stamp in Edit mode reopens its style editor. Styles persist in native
annotation appearances and editor metadata; save/reopen, undo/redo, and upright
rendering on rotated pages have regression coverage.

### Forms and bookmarks usability update

Forms now use a page-based workflow: choose a field name/type, drag a rectangle
to create it, click fields to fill/edit, and redraw bounds to move or resize.
The Forms sidebar lists fields by name and page, accepts values, and exposes
field properties and deletion. Choice options use one item per line and can be
edited after creation. Read-only flags and text character limits are honored.
Positioning no longer requires entering PDF coordinates.

A dedicated Bookmarks icon opens navigation, add, rename, and delete controls.
Navigation is available in View mode; bookmark mutations require Edit mode.
Native field geometry, value/property changes, deletion, bookmark rename,
save/reopen, and undo/redo have regression tests. Real GTK interaction tests
exercise drawing, clicking, resizing, checkbox toggles, dropdown creation/filling,
and bookmark navigation with GTK critical warnings treated as failures.

### Stamp shapes and opening zoom

Stamps now offer rectangle, rounded rectangle, oval, circle, serrated seal,
shield badge, and ribbon silhouettes. Native vector appearances preserve the
selected shape when saving, reopening, and undoing edits. The style dialog
previews each shape and allows changing existing stamps.

Newly loaded documents fit the full page within the allocated canvas, including
rotated landscape pages and new tabs. Later tab switches preserve manual zoom.
Real file-loading GTK tests cover initial fit and independent tab zoom; native
stamp tests cover all seven shapes, rotated pages, save/reopen, and undo/redo.

Placed stamps can now be selected and dragged in Edit mode, including while
the Stamp tool remains active. Double-click opens the style editor. Dragging
uses a temporary document preview and commits one undoable position change
on release; Escape, navigation, and mode changes discard the preview. Moves
stay inside the visible page and retain native appearance streams, opacity,
rotation, and custom style metadata. Tests cover preset/custom stamps at all
page rotations, zoomed dragging, cancellation, save/reopen, and undo/redo.

Selected stamps expose four corner resize handles with diagonal resize cursors.
Resizing preserves aspect ratio and scales the existing native appearance,
keeping circle/seal shapes and text proportional. The opposite corner remains
fixed; minimum size and page boundaries constrain the resize. Previewing does
not change the live PDF, and releasing commits one undoable edit. Regression
coverage exercises every corner at every page rotation, preset/custom stamps,
size limits, cancellation, native appearances after save, and GTK page gestures.

Stamps also expose a round rotation handle above the selection and a tilt-angle
control in the style dialog. Rotation wraps the existing vector appearance,
preserving text, color, opacity, and resources. Tilt survives compact saving,
later resizing, and style edits, with one undo step per completed drag. Page
rotation and user tilt remain independent; rotated bounds stay within the page.

### Text box growth and resizing

Inline text selection bounds now follow the editor's measured width and height
as typing changes the content. Preview bounds stay separate from PDF/model
geometry, so cancelling preserves the previous text and bounds. Text that wraps
at the available page boundary retains those line breaks when committed.

Selected text exposes four corner resize handles after editing. Resizing scales
the box, font, and baseline proportionally and keeps the opposite corner fixed,
including on rotated pages and for tilted text. Re-editing an already inserted
text object updates it rather than adding a duplicate. GTK and native regressions
cover typing growth, multiline/wrapped text, cancellation, resize, saved font
size, repeated edits, and undo/redo.

### Tilted text previews and element context menus

Editing managed, already rendered text rebuilds a temporary page without the
edited object, preserving other objects and original page content. Tilted text
therefore does not remain behind the inline editor. Closing or cancelling the
editor releases the preview without changing the live PDF.

Edit mode now offers fast Delete, Duplicate, and Properties context actions for
text, shapes, images/signatures, pen strokes, whole tables, annotations/stamps,
and form fields. Tables remain grouped after duplication. Native annotation
copies preserve appearance references and translate their geometry; form copies
receive unique names and retain values and flags. All mutations support undo.
Property dialogs expose geometry, rotation, text formatting, stroke/fill colors,
and native annotation/form settings. View mode and PDF edit permissions gate
mutations. Signed signature fields retain their existing protections.

Sticky-note page clicks now open a themed, rounded GTK popover with a pointer to the annotation, author header, scrollable multiline content, and explicit Save/Close controls. Native notes use the PDF Comment speech-bubble icon and a subdued blue color. Page/document/mode changes dismiss the bubble; edits continue to use document undo.
