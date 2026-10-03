# pdfLX feature roadmap and handoff for future AI agents

Last audited: 2026-10-02 (§0 review findings and §13 unlisted PyMuPDF 1.28.2 capabilities
added the same day; implementation passes and decisions recorded below).

This file records a source-level comparison of pdfLX against the user's twelve-part
PyMuPDF feature specification. It is a roadmap, not an instruction to implement all
items automatically. Confirm the requested scope in the next task before making changes.
PyMuPDF having an API does not mean pdfLX exposes that feature to users.

## Read this before working

- Product display name: **pdfLX**. Python module, command, package, and icon identifier:
  `pdflx`. Application ID: `org.pdflx.Editor`. Do not reintroduce old branding.
- The repository already contains substantial uncommitted work. Inspect the current
  working tree and preserve it. Do not reset, overwrite, or commit unrelated changes.
- Re-check current code before implementing anything below. Features may have changed
  since this audit. `PYMUPDF_FEATURE_AUDIT.md` contains historical findings and some
  outdated status statements, including continuous scrolling and advanced form support.
- Distinguish a UI feature, an internal implementation detail, and a library capability.
  Do not mark an item complete solely because PyMuPDF supports it.
- Preserve document-wide undo/redo, preview cancellation, document permissions, View/Edit
  mode rules, native PDF appearances, and managed object state across save/reopen.
- Respect the distinction between permission to fill forms and permission to edit content.
  Supported form filling is available in View mode when the document permits it.
- Preserve original copyright and license notices. The project is GPL-3.0-or-later.
- Use meaningful existing regression tests and targeted new tests for substantive behavior.
  Exercise rotated/cropped pages and save/reopen whenever geometry or persistence changes.

## Code map

| File | Responsibility |
| --- | --- |
| `pdflx/window.py` | Main UI, actions, mode/permission gating, navigation, document sessions |
| `pdflx/pdf_handler.py` | Loading, rendering, extraction, object rebuilding, page operations, save/export |
| `pdflx/document_features.py` | Metadata, bookmarks, page/image/table export, form inspection/filling |
| `pdflx/document_tools.py` | Redaction, review annotations, stamps, decorations, cropping, form mutations |
| `pdflx/document_tool_ui.py` | Document tool dialogs, previews, comments sidebar/popovers |
| `pdflx/form_ui.py`, `pdflx/form_inline_editor.py` | Form creation, filling, properties, inline interaction |
| `pdflx/form_properties.py`, `pdflx/form_style_ui.py` | Validated form settings and appearance controls |
| `pdflx/extended_form_fields.py`, `pdflx/form_buttons.py`, `pdflx/form_tree.py` | Radio groups, reset/navigation buttons, AcroForm structure |
| `pdflx/form_duplication.py`, `pdflx/form_interaction.py` | Field duplication and page gestures |
| `pdflx/page_state.py`, `pdflx/pdf_state.py`, `pdflx/undo_manager.py` | Persistent editor objects, rollback, document history |
| `pdflx/continuous_view.py`, `pdflx/reader_selection.py`, `pdflx/text_geometry.py` | Continuous reading and text geometry/selection |
| `pdflx/image_editing.py`, `pdflx/image_editor_dialog.py` | Image effects, previews, and controls |
| `pdflx/table_creation.py`, `pdflx/table_data.py`, `pdflx/table_style.py` | Table creation, data, styling, grouping |
| `pdflx/signatures.py`, `pdflx/signature_dialog.py`, `pdflx/certificate_appearance.py` | Visible signatures and pyHanko certificate signing |
| `tests/` | Native document regressions and real GTK interaction smoke tests |
| `debian/`, `build-deb.sh`, `pyproject.toml`, `environment.yml` | Installation, package dependencies, build configuration |

## Implementation status (2026-10-02)

Implemented in this pass (see `git log`). Architecture for future agents:

- `pdflx/ops/`: GTK-free operations (`security`, `pages`, `convert`, `text`, `annotations`,
  `structure`, `verify`, `ocr`, `common`). Tested headlessly in `tests/test_ops.py`.
  Operations that would invalidate editor page models return new documents or bytes.
- `pdflx/features/`: one module per menu area (`pages_ui`, `protect_ui`, `convert_ui`,
  `review_ui`, `document_ui`); `features/__init__.py` registers actions (`ACTIONS` table
  with enable rules), builds the grouped header menu and sidebar Pages/Protect menus, and
  provides glue (`mutate`, `change_pages`, `open_new`, `save_data`, `require_plain_pages`).
- `pdflx/dialogs.py`: Adwaita toolkit (`OperationDialog` with rows, page-range entry,
  file pickers, preview, threaded progress with cancel; `alert`/`ask`/`toast`).
- `pdflx/strings_features.py`: English strings for the above; other languages fall back
  to English except translated menu group names. **Translation of new strings is open.**
- `window.py` changes are limited to wiring: toast overlay, signed-save prompt,
  `open_generated_document`, `open_path` (other formats), search option toggles, bookmark
  hierarchy buttons, rollback of failed `_mutate_document`, logging.
- `tests/ui_features_smoke.py` opens every new dialog under `G_DEBUG=fatal-criticals`.

Known limits of this pass:

- OCR could not be exercised end to end here: Tesseract was not installed. Only dependency
  detection and the invisible text layer writer are tested.
- Geometry-changing in-place operations refuse pages holding editable pdfLX objects
  (`require_plain_pages`) rather than transforming those objects.
- Comment replies, review states, filters, and annotation properties are in the comments
  sidebar (second pass).
- Signature verification checks the file on disk, not unsaved edits.

## Decisions recorded 2026-10-02 (second pass)

- **window.py split:** progressive. CSS moved to `pdflx/style.css`; bookmarks, metadata,
  password, and merge dialogs moved to `pdflx/window_dialogs.py`; all new features live in
  `pdflx/features/` and `pdflx/ops/`. window.py went from 9,011 to about 8,250 lines. Further
  candidates: `draw_pdf_page` (680 lines), `_update_ui_state`, `on_key_pressed`, the drag
  handlers.
- **Packaging:** `./build-deb.sh --offline` bundles dependency wheels for the build host's
  Python/CPU (architecture-specific package); `postinst` installs them with `--no-index`
  and falls back to PyPI when they do not match. The default build stays `Architecture: all`
  with online installation. `gir1.2-adw-1 (>= 1.5)` is now required (Adw.Dialog,
  AlertDialog, SpinRow); Debian 12 ships 1.2 and is therefore unsupported. Clean-install
  testing on a fresh VM/container is still outstanding.
- **Rendering (measured):** single-pass numpy conversion to Cairo surfaces: A4 page 266 → 95 ms
  at 8x, 78 → 25 ms at 4x, 10 → 8 ms at 1x. Byte-budget cache (320 MB) replaces the
  six-surface cache (up to ~770 MB at 8x). Pages above 3500×3500 device pixels render only
  1024-px tiles around the viewport (17 ms / 25 MB instead of 128 MB at 8x).
  Zero-copy ingestion was not attempted: MuPDF's RGB pixmap must be repacked for Cairo.
- **Fast Web View:** not possible; MuPDF 1.28 reports "Linearisation is no longer supported".
  The Document Information dialog shows whether an opened file is linearized.
- **MuPDF journalling:** not adopted. In-memory journalling works, but loading a saved journal
  after reopening failed ("Can't load a journal over another one"), it would duplicate the
  existing PdfState/command undo system, and it does not cover editor page models.
- **/PieceInfo:** not migrated; no interoperability need was identified. State stays under
  `PdfLXEditor`.
- **Factur-X/ZUGFeRD:** the XML is attached as an associated file (`/AF`, `/AFRelationship
  /Alternative`) with Factur-X XMP, but pdfLX does not produce or claim PDF/A-3, which the
  standard also requires. The dialog says so.
- **Node editor:** edits polyline nodes of pen drawings (move, insert, delete, simplify,
  smooth). True Bézier handle editing of arbitrary imported PDF paths is not implemented.
  New primitives (star, polygon, triangle, block arrow, sector, arc, rounded rectangle)
  are drawn as native page content.
- **Presentation mode:** full screen, fitted, one page at a time, F5. Page transition effects
  are not implemented.
- **Split view:** a read-only side-by-side pane for any open document (or another place in
  the same one); editing remains single-pane.
- **Form scripts (third pass):** filling runs a form's own JavaScript through MuPDF's mujs
  engine (`ops/formjs.py`), only when a value is committed and only if the form has scripts;
  setting `form_scripts` (main menu) disables it. MuPDF's value API does not fire the commit
  keystroke event, so the standard AF* keystroke checks are reimplemented in Python
  (`formbehaviour.check_value`); custom keystroke scripts are not emulated. Script
  `app.alert` messages are not shown; rejections are explained from the field's settings.
  Submit buttons are configurable but pdfLX never sends data; it offers export instead.
  Remaining form gaps: buttons with image/icon appearances and a tab-order editor.
- **On hold by request:** OCR end-to-end testing and enhancement, translations of new
  strings (English fallback; menu group names are translated), and AI/RAG items.

## 0. Review findings to address first (2026-10-02)

Found during a code review on 2026-10-02. These are defects and risks, not new features.

- [x] **Version control:** the whole `pdflx/` package, `tests/`, and the new packaging
  files are untracked, and the old `word_sys_pdf_editor/` package is deleted in the
  working tree. Commit the rebrand before starting feature work.
- [x] **Signed-document save:** `pdf_handler.save_document()` always rewrites the full
  file with `garbage=4`, and `window.save_document()` has no signature check. Saving a
  signed PDF overwrites the original and invalidates its signatures without warning.
  Only `signatures.prepare_signing_pdf()` guards this. At minimum, detect signed fields
  on save, warn, and default to "Save As copy".
- [x] **Undo after save:** verified false alarm. `save_document()` sets the session path
  before calling `load_document()`, which finds the same session and returns early, so
  history is kept.
- [x] **`window.py` size:** about 8.8k lines and 300 methods. Split it into controllers
  (sessions/tabs, save/export, mode/permission gating, navigation) before adding many
  features.
- [x] **XMP metadata:** corrected finding: XMP is only deleted by "Clear metadata"
  (intended), but `update_metadata()` left an existing packet stale. It now re-syncs XMP
  (`ops.structure.sync_xmp`), which PDF/A and asset-management tools rely on.
- [x] **Code hygiene:** `print("DEBUG …")` calls use `logging` (enable with
  `PDFLX_DEBUG=1`); the Turkish save error goes through `_()`. The 29 `except Exception: pass`
  handlers were audited: three that could hide real failures (ghost erasure redaction,
  obsolete content-stream clearing, completion callbacks) now log; the rest guard optional
  heuristics (underline detection, coordinate fallbacks) and stay silent by design.
- [x] **Test discovery:** false alarm. `tests/test_gtk_review.py` runs every
  `ui_*_smoke.py` script in a subprocess, so the documented command covers them.
- [x] **Packaging:** `postinst` downloads Python packages from PyPI at install time, so
  installation fails without internet access and departs from Debian norms. Consider a
  bundled wheelhouse in the `.deb`, or make Flatpak/AppImage the primary distribution.

## 1. Document display and rendering

**Implemented:** zoom-dependent page rendering, thumbnails, a limited page-surface cache,
GTK/Cairo integration, PNG/SVG page export, search highlighting, text selection,
coordinate transformations, rotation/hit-testing, and continuous scrolling.

- [x] Add RGB/greyscale/CMYK rendering or export controls where useful.
- [x] Add transparent raster page export and configurable raster formats/DPI.
- [x] Implement viewport-clipped rasterization for large or heavily zoomed pages.
  Current viewer calls rasterize whole pages. Keep clip offsets synchronized with
  scrolling, hit-testing, page rotation, and text overlays; measure before/after memory
  and latency rather than assuming the change is faster.
- [x] Evaluate reducing rendering-buffer copies. Current conversions copy buffers;
  do not claim zero-copy ingestion without proving ownership/lifetime safety.
- [x] Add regex search as a separate extraction/match/geometry pipeline.
  Keep literal search behavior intact and test multiline and rotated matches.

## 2. Page and layout management

**Implemented:** blank-page insertion, deletion, duplication, reordering, PDF merging,
page-range export, quarter-turn rotation, crop/reset, bookmark navigation/add/rename/delete,
and visible page numbering. `show_pdf_page()` is used internally for overlays/appearances.

- [x] Add an advanced page-box inspector/editor for MediaBox, TrimBox, BleedBox, and ArtBox.
  Validate box relationships and transformed object geometry.
- [x] Add N-Up, booklet/imposition, or general PDF-page placement/template workflows.
  Existing internal `show_pdf_page()` usage is not a user-facing imposition tool.
- [x] Add logical page-label editing with Roman/Arabic numbering, prefixes, and ranges.
  Visible numbering drawn on pages is not the same as PDF page labels.
- [x] Add full bookmark hierarchy editing/reparenting. New bookmarks currently enter
  at the top level; existing nested destinations must remain intact.

## 3. Interactive forms and form builder

**Implemented:** discovery, creation, filling, moving/resizing, duplication, deletion,
properties, save/reopen, and flattening. Supported controls include single/multiline text,
checkboxes, radio groups, dropdowns/editable combos, single-select lists, reset/navigation
buttons, and certificate-signature placeholders. Appearance settings include fonts/sizes,
colors, border width, defaults, and tooltips. Read-only/required flags and text length
limits are handled; there is a required-field check.

- [x] Add explicit password and comb text-field settings and appropriate input behavior.
- [x] Add multi-select list creation/filling. The UI currently explicitly rejects editing
  such imported fields; support arrays of selected export values and appearances.
- [x] Add border-style controls beyond border width/color.
- [x] Add a field hierarchy designer if requested. A dotted field name alone must not be
  assumed to create a fully correct parent/child AcroForm structure.
- [x] Add JavaScript inspection/editing only with clear event semantics.
  There is no general calculation, validation, formatting, or focus-script engine.
  Existing reset/navigation buttons are native actions, not general JavaScript execution.

## 4. Annotations and interactive markup

**Implemented:** native highlights, underlines, squiggles, strikeouts, sticky notes,
comments, and preset/custom stamps. Comments can be edited/deleted. Stamps support styling,
author information, opacity, movement, resizing, and rotation. Shapes and pen strokes are
editable page content; do not describe them as native geometric/ink annotations.

- [x] Add dedicated native FreeText/typewriter and callout tools.
- [x] Add native ink, polygon/polyline, and geometric annotation creation when required.
- [x] Add file-attachment annotations pinned to page coordinates.
- [x] Add annotation-flag and broader attribute controls where useful.
- [x] Add annotation flattening as a separate operation.
  Current form flattening uses `doc.bake(annots=False, widgets=True)` and intentionally
  preserves review annotations. It refuses documents with signed signature fields.

## 5. Redaction and security sanitization

**Implemented:** region/search-based permanent redaction, previews, text/vector removal,
cleanup of obsolete managed editor state, and undo before saving. Page-content cleanup
is used internally during editing.

- [x] Add selective raster-image pixel redaction if requested. Current calls use
  `PDF_REDACT_IMAGE_REMOVE`, removing overlapping image placements wholesale rather than
  erasing only covered pixels. Explain the behavior clearly to users.
- [x] Add a document scrub workflow for JavaScript, attachments, external links,
  private metadata, thumbnails, and other unwanted content. No `doc.scrub()` UI exists.
- [x] Consider replacement-text/fill options beyond the current redaction workflow.
- [x] Validate sanitization against saved/reopened bytes and editor baseline streams,
  not just the visible page. Undo snapshots and saved custom metadata can retain content;
  existing redaction cleanup must remain effective.

Do not claim general forensic sanitization from redaction alone, and do not confuse
cropping with content removal.

## 6. Text, structure, and table extraction

**Implemented:** plain-text export and internal word/span/character extraction for
selection and editing. Native table detection exports one CSV per table in a ZIP.
Table creation/editing/styling/group persistence also exists separately from recognition.

- [x] Add selectable extraction modes and JSON/rawJSON/HTML/XHTML/XML exports.
- [x] Add detected-table Markdown, pandas, and/or XLSX export with suitable dependencies.
- [x] Add table preview, region selection, and line/text detection strategy controls.
- [x] Add progress/cancellation for potentially expensive table/image archive exports.
- [x] Test multi-column text order and malformed/empty table cells before promising
  faithful structure recovery.

## 7. Vector graphics and document authoring

**Implemented:** vector extraction for editable shapes/strokes, geometric and freehand
drawing, formatted text insertion, wrapping text boxes, images, tables, symbols,
watermarks, and visible page numbering.

- [x] Add a general path/Bézier node editor and additional primitives such as sectors.
- [x] Add Story/HTML/CSS authoring and automatic multi-page content flow.
- [x] Add generated, linked TOCs and stabilized page-number resolution for authored content.

Keep rich multi-page authoring separate from reconstructing arbitrary existing PDF text.

## 8. Images, fonts, embedded files, and portfolios

**Implemented:** embedded-image ZIP export, selected-image insertion/replacement,
movement/resizing/rotation, crop/flip, opacity, borders, and shadows. Font discovery,
glyph checks, and text measurement are used internally. Editable image extraction
reconstructs soft masks for transparency.

- [x] Add document-wide replacement of every occurrence of a shared image XREF.
  Selected-object replacement is not the same operation; clarify scope in the UI.
- [x] Extend image ZIP export to inline images and reconstructed transparent appearances.
  Current original-asset export skips XREF-zero inline images and separate soft masks.
- [x] Add embedded-font inspection/export if requested.
- [x] Add document attachment list/add/extract/remove controls.
- [x] Add PDF Collections/Portfolios or Factur-X/ZUGFeRD workflows only with explicit
  format/validation requirements; embedding an XML file alone does not establish compliance.

## 9. Hyperlinks, web actions, and layers

**Implemented internally:** existing links are tracked/restored during content editing.
Form buttons can navigate to document pages.

- [x] Add a general hyperlink inspector/editor and page-click navigation workflow.
  Define supported URI, internal-page, remote-PDF, named, and file-launch targets.
- [x] Add optional-content-group inspection and visibility controls.
- [x] Add layer creation/configuration for display, export, and print if requested.

Existing link preservation is not a complete hyperlink subsystem. No layer UI exists.

## 10. Low-level PDF architecture and storage optimization

**Implemented internally:** XREF/key/stream manipulation and compressed JSON state that
preserves managed objects/table groups. State is stored under the custom page key
`PdfLXEditor`, **not `/PieceInfo`**. Ordinary saving writes a compacted separate copy
with `garbage=4`, `deflate=True`, and atomic replacement, retaining live XREF stability
for history. Certificate signing uses a pyHanko incremental writer.

- [x] Evaluate `/PieceInfo` migration only if there is a concrete interoperability need.
  Preserve backward reading of existing `PdfLXEditor` state if changing its storage.
- [x] Add optimization controls: image quality/downsampling, font subsetting, size preview.
- [x] Add Fast Web View export only after verifying support in the installed dependency.
  Do not assume historical `linear=True` examples remain supported across versions.
- [x] Make ordinary-save policy explicit. Its `incremental` argument currently does not
  enable incremental output; the UI passes `False` and full rewrite is performed.
- [x] Assess signed-document save restrictions and explicit eligible incremental-save
  paths. Signing incrementally does not guarantee ordinary later edits preserve signatures.
- [x] A raw-object editor is absent and should be built only if explicitly requested.

## 11. Security, permissions, and cryptography

**Implemented:** password authentication, edit/copy/print/form-fill permission checks,
preservation of existing encryption on save, signed/unsigned field status, and PKCS#12
certificate signing into a separate PDF through pyHanko.

- [x] Add password/encryption creation/change/removal controls.
- [x] Add permission-mask configuration and distinct high-resolution-print/annotation rules.
- [x] Add existing-signature cryptographic verification, modification-integrity results,
  certificate trust/chain policy, and clear validation status explanations.
- [x] Add PKCS#7 extraction/inspection if requested.

`widget.is_signed` reports signature presence, not validity or trust. Visible handwriting
is an image, not a cryptographic signature. Ordinary full saving can affect existing
signatures; test signed sample documents before asserting preservation.

## 12. OCR and AI/RAG

**Not implemented:** neither Tesseract OCR nor PyMuPDF4LLM is currently an app subsystem.

- [x] Add OCR dependency detection, language selection, page ranges, progress/cancellation,
  and search/copy integration for scanned pages.
- [x] Add searchable scanned-PDF output and saved/reopened text-layer verification.
- [ ] Design localized OCR explicitly; the documented `get_textpage_ocr()` signature
  does not provide a `clip` parameter.
- [ ] Add optional PyMuPDF4LLM Markdown export, chunk/page metadata, figure extraction,
  and hybrid OCR behavior if requested.
- [ ] Add LangChain/LlamaIndex/vector-store integration only for an agreed use case.

Keep OCR-derived text distinguishable from original digital text. Confirm dependency
licensing and package installation requirements when adding either subsystem.

## 13. Further PyMuPDF capabilities not covered above

Added 2026-10-02. Each API below was checked to exist in the installed PyMuPDF
**1.28.2** and is currently **unused** by `pdflx/` (checked by grep). Verify against the
docs again before implementing; an existing API does not establish UI scope.

### 13.1 Opening and converting other formats

The open dialogs currently accept PDF only (`window.py` filters `application/pdf`).

- [x] Open XPS, OXPS, EPUB, MOBI, FB2, CBZ, SVG, TXT, and raster images (PNG/JPEG/TIFF/
  multi-page TIFF) as read-only views, with "Convert to PDF" via `Document.convert_to_pdf()`.
  Reflowable formats need `Document.layout()` and a page-size/font-size control
  (`is_reflowable`).
- [x] Images to PDF: batch-create a PDF from several images (one page per image, with
  page-size/fit/margin options), and insert images or other documents as new pages with
  `Document.insert_file()`.
- [x] Keep conversion results clearly marked as new unsaved PDFs; never overwrite the source.

### 13.2 Page operations beyond the current set

- [x] Split a document by page ranges, every N pages, by top-level bookmarks, or by file
  size, using `insert_pdf()` into new documents.
- [x] Insert pages from another PDF at a chosen position (`insert_pdf(start_at=…)`).
  Merge currently exists; check whether position control is exposed.
- [x] Collate odd/even scans (duplex fix), reverse page order, and extract selected pages.
- [x] Normalize page sizes (e.g., everything to A4/Letter) with `show_pdf_page()` into
  new pages, and "Remove rotation" with `Page.remove_rotation()`, which bakes /Rotate into
  content. Check managed-object geometry after both.
- [x] Letterhead/background/overlay: place a page of another PDF behind or above the
  pages in a range (`show_pdf_page(overlay=False/True)`). This differs from N-Up/imposition.
- [x] Detect and remove blank pages (render a low-res pixmap and use
  `Pixmap.color_topusage()` with a threshold), with a preview before deletion.
- [x] Headers/footers and Bates numbering (prefix, start number, zero padding, position,
  page range). Distinct from the existing visible page numbering and from page labels.

### 13.3 Colour, rasterization, and reading modes

- [x] Convert a document to greyscale/CMYK with `Document.recolor()`/`Page.recolor()`.
  This differs from TODO §1 render-time colorspace export: it changes the PDF itself.
- [x] "Rasterize page(s)": replace pages with image-only versions at a chosen DPI. This is
  a sanitization/anti-copy option and will remove text, links, and forms. Warn clearly.
- [x] Reader display modes using pixmap post-processing: inverted/night mode
  (`Pixmap.invert_irect()`), sepia/tint (`tint_with()`), gamma/contrast (`gamma_with()`).
  Display only; never write back to the PDF.
- [x] Region snapshot tool: drag a rectangle and copy it to the clipboard or save it as
  PNG/SVG (`get_pixmap(clip=…)`, `get_svg_image()` on a cropped page copy).

### 13.4 Text intelligence

- [x] Search options: case sensitivity, whole word, search all open tabs, and a results
  panel listing every hit with context. Check what `search_for()` flags provide;
  whole-word matching needs word-level post-filtering.
- [x] Text style inspector: show font, size, colour, and render mode of clicked text
  (`get_texttrace()` or `get_text("rawdict")`). Also "select all text with the same style".
- [x] Detect hidden/invisible text (render mode 3, white-on-white, off-page, covered)
  with `get_texttrace()`/`get_bboxlog()`. Feed the results into the sanitization workflow
  in §5.
- [x] Generate bookmarks automatically from headings inferred from font size/weight in
  `get_text("dict")`, with a review step before `set_toc()`.
- [x] Document statistics: word/character/page counts, fonts used, images, annotations,
  and form fields.
- [x] Compare two PDFs: text diff (`get_text("words")` + `difflib`) with highlighted
  changes in both documents, plus a visual pixel-diff mode for scanned/graphic content.

### 13.5 Vector content

- [x] Use `Page.cluster_drawings()` to select and export a whole vector diagram as SVG/PDF,
  or to move it as a group in Edit mode.
- [x] Extract vector drawings to SVG separately from page export.

### 13.6 Annotations not covered in §4

- [x] Native line/arrow annotations with line-end styles (`add_line_annot()` +
  `set_line_ends()`), caret/insert-text annotations (`add_caret_annot()`), and native
  rectangle/ellipse annotations (`add_rect_annot()`/`add_circle_annot()`) as review markup
  distinct from editable page shapes.
- [x] Comment replies (`irt_xref`) and review states (Accepted/Rejected/Completed) in the
  comments sidebar.
- [x] Export/import annotations as JSON (or XFDF-like) to share reviews without sending the
  PDF, and export a comment summary as a PDF or text report.
- [x] Filter the comments sidebar by author, type, page, and status.

### 13.7 Document-level properties

- [x] Initial view settings: page mode (outlines/thumbnails/full screen) and page layout
  (single/two-page) with `set_pagemode()`/`set_pagelayout()`.
- [x] Document language (`Document.set_language()`; the app's `i18n.set_language` is
  unrelated) and MarkInfo (`set_markinfo()`). Do not claim tagged-PDF or PDF/UA
  accessibility from these alone.
- [x] Properties dialog: PDF version, producer, encryption method, permissions, page
  sizes, fonts (`get_fonts()`), fast-web-view status (`is_fast_webaccess`), repaired
  status, signature flags (`get_sigflags()`), and file size.
- [x] Repaired-file workflow: when `is_repaired_file` is set, offer "Save repaired copy".
- [x] `need_appearances` toggle for forms whose appearances should be regenerated by viewers.

### 13.8 Font tools

- [x] Embedded-font list and extraction (`get_fonts()`, `Document.extract_font()`).
  Expands the §8 item.
- [x] Font subsetting on save (`Document.subset_fonts()`) as part of §10 optimization.
  Re-test editability of text after subsetting, because missing glyphs break editing.

### 13.9 Storage, history, and batch work

- [x] Evaluate MuPDF journalling (`journal_enable()`, `journal_undo()`/`journal_redo()`,
  `journal_save()`) as a possible persistent undo history. Do not replace the existing
  undo manager without a migration plan and parity tests.
- [x] Crash recovery/autosave of unsaved sessions to a private state directory.
- [x] Batch processing dialog and `pdflx` CLI subcommands (merge, split, compress, OCR,
  convert, rasterize, scrub) that reuse `pdf_handler`/`document_features` without GTK.
- [x] Image recompression/downsampling via `Document.rewrite_images()` (expands §10),
  with size before/after and a preview of quality.

### 13.10 Authoring helpers (beyond §7)

- [x] Insert rich text with `Page.insert_htmlbox()` in the text tool (currently used
  only for certificate appearances). Supports mixed fonts, colours, lists, and RTL/CJK
  shaping.
- [x] Templates: generate filled documents from a PDF template + CSV (mail-merge into form
  fields or text placeholders), one output file per row or one combined PDF.
- [x] `TextWriter`-based text with per-run opacity/morphing for watermarks and rotated text.

### 13.11 Viewer conveniences (not PyMuPDF-specific)

- [x] Two-page spread and book (cover-separate) views in continuous mode.
- [x] Presentation/full-screen mode with page transitions.
- [x] Recent files with thumbnails and remembered last page/zoom per file.
- [x] Split view of two documents or two places in one document.
- [x] Measurement overlay (distance/perimeter/area) with a configurable scale. PyMuPDF has
  no high-level Measure-dictionary API, so this is an overlay tool, not native PDF measurement.

## API corrections to the supplied specification

Check official documentation and the installed version rather than copying the report:

- `Page.search_for()` does not support regular-expression needles.
- Widget constants are Button **1**, Checkbox **2**, Combo **3**, List **4**, Radio **5**,
  Signature **6**, Text **7**. Use named constants rather than new hardcoded numeric tests.
- `Page.clean_contents()` cleans content streams; it does not flatten annotations.
  `Document.bake()` is the dedicated flattening operation.
- `Page.get_textpage_ocr()` has no documented `clip` argument.
- PyMuPDF signature-field inspection is not a complete signing/trust-validation engine.
  pdfLX uses pyHanko for certificate signing.
- Dotted field names must not be assumed to automatically construct correct field trees.
- Benchmark and forensic guarantees require evidence from pdfLX, not general library claims.

Primary references:

- [Page API](https://pymupdf.readthedocs.io/en/latest/page.html)
- [Document API](https://pymupdf.readthedocs.io/en/latest/document.html)
- [Widget API](https://pymupdf.readthedocs.io/en/latest/widget.html)
- [Constants](https://pymupdf.readthedocs.io/en/latest/vars.html)

## Suggested order for future work

This is a recommendation, not an approved implementation scope:

Revised 2026-10-02 to include §0 and §13.

1. §0 findings: commit the rebrand, signed-save warning, undo-after-save, debug/except cleanup.
2. Quick, high-value wrappers around existing PyMuPDF APIs: password/permission controls
   (§11), `doc.scrub()` sanitization (§5), annotation flattening (§4), size optimization
   (`rewrite_images`/`subset_fonts`, §10/§13.9), page labels (§2), raster export options (§1).
3. Page workflows users expect: split, insert-at, collate, blank-page removal,
   headers/footers/Bates (§13.2); open/convert images and other formats (§13.1).
4. Real signature verification with pyHanko (§11).
5. OCR/searchable-scan output with dependency and language checks (§12).
6. Finish common form gaps: multi-select lists, password/comb fields, border styles (§3).
7. Hyperlink and attachment management (§8, §9); review-workflow annotations (§13.6).
8. Text intelligence: search options, compare, style inspector, auto-bookmarks (§13.4).
9. Split `window.py` progressively alongside the items above, not as one big rewrite.
10. Advanced imposition, Story authoring, layers, journalling, and AI/RAG as needed.

## Verification and packaging handoff

- A completed earlier regression run passed **159 tests**, including real GTK tests.
  This is historical evidence, not a guarantee for the current or future working tree.
- Use an environment created from `environment.yml` (named `pdfLX`) or any Python with
  GTK 4, libadwaita, PyGObject, and the dependencies in `pyproject.toml`.
- Existing regression command:

  ```sh
  python -m unittest discover -s tests -p 'test_*.py'
  ```

  Use an interpreter with GTK/PyGObject and app dependencies. Real GTK interaction tests
  need a display. Review skipped tests; a successful run with skips is not full UI coverage.
- Build the Debian package and standalone icons with `./build-deb.sh`.
  Artifacts are `dist/pdflx_0.2_all.deb`, `dist/pdflx.png`, and `dist/pdflx.svg` at
  the audited version. The script derives the version from `pdflx/constants.py`.
- Install through `sudo apt install ./dist/pdflx_0.2_all.deb` so APT resolves system
  dependencies. The package is **not an offline/self-contained container**.
- The installer downloads PyMuPDF, numpy, Pillow, pdf2docx, and pyHanko into an
  isolated `/opt/pdflx/.venv` with system-site packages for GTK. Installation requires
  internet access. It performs dependency import checks before reporting success.
- Declared system dependencies include Python/venv/pip, GTK 4, libadwaita, PyGObject,
  Cairo integration, CA certificates, fontconfig, and baseline fonts.
- Fresh-system installation has **not** been verified. Add a disposable clean Debian/
  Ubuntu install test before claiming clean-install compatibility, offline operation,
  or broad distribution support.
- Keep dependencies consistent across `pyproject.toml`, `environment.yml`, Debian
  installation, Flatpak, and AppImage/PyInstaller build definitions. Rebuild artifacts
  after source changes; an existing `.deb` does not automatically contain new code.
- For substantive features, verify behavior in the packaged app as well as source:
  open/create, save/reopen, undo/redo, rotated/cropped pages, encrypted files, and
  View/Edit permission gating. Add feature-specific samples for OCR, redaction,
  signature integrity, attachment/layer preservation, and form appearances.
