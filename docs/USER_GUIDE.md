# pdfLX user guide

Detailed reference for pdfLX features. See the [README](../README.md) for an overview and installation.

## Signatures

- **Add Signature…** in Document Tools (or the signature button in the sidebar)
  opens a pad for drawing or importing a PNG/JPEG signature. Choose **Place
  Signature**, click the page, and then move or resize it. Undo works as usual.
- **Sign with Certificate…** in Document Tools and the Add Signature dialog accepts a `.p12`/`.pfx` certificate
  and its password. It saves a separate signed PDF, including current edits,
  and can fill an existing unsigned signature field or place a new visible one.
  Place a visible signature first if you want handwriting on the signed copy.
- Certificate signatures include Modern, Minimal, and Formal designs showing
  the certificate holder, local signing time, and optional reason. Choose a design,
  click **Place on Document…**, then drag a rectangle to set its position and size.
  Click Place again to reposition or resize before signing. Existing unsigned
  signature fields can also use these designs. The signed copy opens in a new tab;
  the source document stays unchanged.
- Certificate signing uses pyHanko, installed with the app's dependencies.
  For an existing environment, run `python -m pip install -e .`.
- Passwords are not saved. Existing digital signatures are preserved when
  countersigning an unmodified original. Editing a signed document requires
  a separate workflow because rewriting its signed bytes invalidates signatures.

## Tables

Choose **Add Table…** from the sidebar or **Document Tools** menu. Set rows,
columns, page width, font size, and minimum row height, then enter cell text.
Use **Paste Table** for cells copied from a spreadsheet, or **Import CSV…** for
a CSV/TSV file. Separators are detected automatically; choose comma, semicolon,
tab, or pipe explicitly if needed. Review the imported cells before inserting.
**Size columns to fit their contents** gives longer descriptions more room.
The table is centered on the current page; text wraps and rows grow to fit.
The first row can use bold text as a header.

Select a table to move the whole group or resize it from a corner. Double-click
cell text to edit it. Dragging uses a preview and commits one undoable change on
release. Table groups survive page navigation and saving/reopening in pdfLX.
In Edit mode, right-click a table and choose **Table Style…**. Choose Plain,
Blue, Green, or Striped presets, or adjust header/row backgrounds, text colors,
border color and width, header emphasis, and font size. The dialog previews the
table before Apply; the entire style change is one undo step. Font sizes that
would overflow cells are rejected, and styles survive saving and reopening.
Right-click a table and choose **Copy Table**, or select it and press Ctrl+C.
Paste it into a spreadsheet or use **Document Tools → Paste Table** in another
document. Empty cells, leading zeroes, and edited cell contents are preserved.
Tables support up to 100 rows, 20 columns, and 1,000 cells, and must fit on one
page; oversized imports are rejected without truncating data.

Rectangle, ellipse, checkmark, and cross tools share one **Shapes and marks**
picker in the sidebar with a square, circle, checkmark, and cross icon. Drawing shortcuts
continue to work.

The arrow selects text in View mode and selects, moves, or resizes objects in
Edit mode. The hand pans the document without changing any objects. Both work
in View mode; **S** selects the arrow and **M** selects the hand. Panning is most
useful when zoomed in or scrolling continuously.

## Document tools

Enter **Edit** mode and open **Document Tools** to:

- Redact selected regions or search matches, with a preview before applying.
  Saving removes the redacted content from the PDF.
- Add notes, underline, strikeout, squiggles, highlights, and stamps. Use the
  comments sidebar to navigate, edit, or delete review annotations.
  **Add Sticky Note…** lets you click the page to choose a location and write a
  multiline comment. Click a note icon to read it in View mode or edit it in
  Edit mode.
- Apply text or logo watermarks and page numbering across a page range.
- Crop margins or reset page boundaries. Cropping changes visibility; it does
  not remove hidden content.
- Create text fields, checkboxes, dropdowns, and list boxes. **Flatten Forms**
  turns their current appearance into page content and removes interactive
  fields while retaining review comments.

Undo and redo cover the document across page navigation, including form values,
metadata, bookmarks, and these document tools. Failed edits restore the prior
PDF and object state without adding a history entry.

The **Forms** icon in the left toolbar opens Create, Fill, and Flatten options
in Edit mode. It is disabled in View mode; existing fields can still be filled
directly on the page when the document permits it.
Creation supports text fields, checkboxes, dropdowns, list boxes, radio groups,
reset buttons, and certificate signature fields.
Choose **Create Form Field…**, enter a name and type, then drag a rectangle on
the page to place it. The **Field**, **Behaviour**, and **Appearance** tabs
separate basic settings, field behaviour, and styling. The **Options** tab
appears for dropdowns, lists, and radio groups; enter one choice per line.
Draw enough height for a radio group (at least 16 pt per option). Selecting an
option clears the other buttons in its group. Button settings include an editable
**Button label** and **Button action**: **Reset form** restores initial values
and leaves read-only and signature fields intact; **Go to page** jumps to a
chosen page. These are real PDF actions saved with the button. Click an unsigned
signature field in Edit mode to open certificate signing with that field selected.
Click text or choice fields to edit their values directly on the page. **Enter**
commits a single-line value; **Ctrl+Enter** commits multiline text. Clicking away
also commits, **Escape** cancels, and **Tab / Shift+Tab** moves between fields.
Click checkboxes to toggle them and radio options to select them. The Forms
sidebar lists fields by name and page, allows saving values or
deleting fields, and opens their properties. In Edit mode, use **Select on page**
or click a field to reveal resize handles. Drag the outside square handles to
resize and the round grip above the field to move it. **Properties…** edits
button labels/actions, field settings, and precise position. **Move / resize
on page** also lets you redraw a field's bounds. Names, required flags, and
text character limits can be edited with undo/redo support.

Duplicate a form field with its sidebar copy icon, **right-click → Duplicate**,
or **Ctrl+D** while selected or filling it. The copy gets a unique name, a small
position offset, and independent values while retaining appearance, defaults,
and button actions. Radio groups duplicate together. Unsigned signature fields
can be copied; signed certificate fields cannot. Duplication supports undo/redo.

The **Bookmarks** icon opens document bookmarks. In Edit mode, add a title for
the current or another page, rename entries, or remove them. Click a bookmark
to navigate directly to its page; navigation is available in View mode too.
The **Stamp** icon opens the stamp designer. Pick one of 14 templates (Approved,
Not approved, Draft, Confidential, Final, For comment, Reviewed, Received, Paid,
Completed, Urgent, Void, Copy, and Sign here) or design your own: main text, an
optional details line, font, bold/italic, colour swatches or a custom colour,
rectangle, rounded, oval, circle, seal, shield, or ribbon shapes, solid, double,
dashed, or no border, a tinted background, opacity, width, and tilt. The **+**
button in the details line inserts the date, time, or author; they are filled in
when the stamp is placed. **Save as Template** keeps a design in the gallery.
Choose **Place on Page**, then click the page to place the stamp; click additional
locations to repeat it, and press Escape or choose Select to finish.
Stamps are native PDF annotations and support undo/redo and saving.
In Edit mode, drag a placed stamp to move it; select it and drag
a corner handle to resize it proportionally, or the round handle above it to tilt.
Double-click to change its style or enter a precise tilt angle in degrees.
Movement, resizing, and tilt preview on the page and support undo/redo.
Text selection borders expand as you type, including multiline text. After
finishing an edit, drag a corner handle to resize the text proportionally.
Resizing scales the font and supports saving and undo/redo.
Editing a tilted text box hides its previous rendered text during typing.
Right-click an element in Edit mode for **Delete**, **Duplicate**, and
**Properties**. This works for text, shapes, images/signatures, pen strokes,
grouped tables, annotations/stamps, and form fields. Deletion is immediate and
undoable; properties expose geometry and the relevant text, color, and style
settings. Text menus also include copy, editing, and highlighting actions.
Select an image in Edit mode to open **Image Tools**, a floating panel on the
right. The image button in the top bar toggles this panel; **Edit Image…** in
the context menu opens it too. Changes appear directly on the document.
The toolbar rotates, flips, restores the source image's proportions, resets, and
replaces the image. The **Appearance** section controls opacity, tilt, and rounded
corners, **Border** sets width and colour, and **Effects** expands **Crop** (edge
percentages) and **Drop shadow** (distance and strength) in place. Crop keeps the
original pixels available, and Reset clears the crop and appearance settings.
Changes support undo/redo. The panel follows the selected image and refreshes
after moving, resizing, undo, or redo. It hides in View mode. Styling follows
movement, resizing, duplication, and saving.
Images remain embedded at their original resolution; clipping and framing use
native PDF graphics rather than permanently changing the source image.
Opened and newly created documents default to Fit View, showing the whole page.
The fit follows window and sidebar resizing until you change the zoom manually.
Each tab preserves subsequent zoom adjustments.
In View mode, double-click the page for document-only fullscreen, hiding the
header, tabs, toolbars, sidebars, and status bar. Double-click again or press Escape
to restore the normal layout. F11 also toggles this view. The mouse wheel scrolls within a page and turns pages at the
edge. Ctrl+wheel zooms around the pointer. The scrolling control beside the zoom
buttons uses a single-page icon for **Page by page** and stacked-page icons for
**Continuous** in View and Edit modes. Click the icon to switch modes; its tooltip shows the
current mode and the mode clicking will select.
Continuous mode stacks pages in a smooth scrollable column, follows the current
page in the sidebar and page counter, and retains selection and highlighting.
Each tab remembers its scrolling mode across View and Edit. Continuous mode
keeps the active page editable and pauses automatic page switching during
typing, drawing, or object dragging.
Drag over text in View mode to select characters, then press Ctrl+C or use
right-click Copy. Ctrl+A selects the current page’s text, including in fullscreen.
Highlighting, highlight color, removal, and undo/redo for highlights are available
in View mode. Select text and click Highlight, or activate Highlight and drag over
text. Press H to highlight a selection or toggle the text highlighter. The
right-click menu also provides highlighting and removal in document-only fullscreen.
Form creation and styling, Document Tools, other annotation editing, bookmark
changes, and page changes require Edit mode. Filling existing fields is also
available in View mode when the PDF permits it, including undo/redo of values. Navigation, searching, exporting, printing, and reading comments
remain available in View mode, subject to the document's permissions.

Form field settings have **Field**, **Appearance**, and (for existing fields)
**Position** tabs with precise bounds and on-page redrawing. They support
initial values, multiline text, required/read-only flags, help text, character
limits, editable dropdowns, font/automatic font sizing, text/background/border
colors, transparent backgrounds, and border width. Checkboxes use a native PDF
tick mark. Imported dropdown labels preserve their separate export values.
Use **Check Required Fields** in the forms sidebar to find unfinished required
fields; incomplete drafts can still be saved. Normal Save includes pending sidebar
values, and flattening applies them before baking the fields. Unsaved sidebar
values are retained when switching document tabs.

## Document export

Word export uses the local `pdf2docx` engine to reconstruct editable text,
images, and tables from digital PDFs. Plain-text export uses MuPDF. There is no
upload or remote conversion service. Current unsaved edits are included and the
open PDF is unchanged. Failed pages abort Word export; an existing output file
is replaced only after the complete conversion succeeds.

Install or update the dependencies with `python -m pip install -e .`.
pdf2docx can also be used independently:

```sh
pdf2docx convert input.pdf output.docx
```

## Build

`build-appimage.sh` builds the AppImage and portable packages.
`org.pdflx.Editor.json` is the Flatpak manifest, and `debian/` contains
Debian packaging definitions.

Build an installable Debian/Ubuntu package and standalone icons with:

```sh
./build-deb.sh
sudo apt install ./dist/pdflx_0.3_all.deb
```

The package installs the pdfLX launcher, the `pdflx-cli` command-line tool, and
PNG/SVG desktop icons. Installation requires internet access to set up the PDF,
DOCX export, and signing libraries in `/opt/pdflx/.venv`. Python 3.10 or later,
GTK 4, and libadwaita 1.5 or later (Ubuntu 24.04+, Debian 13+) are required; APT
installs these system dependencies automatically.

`./build-deb.sh --offline` additionally bundles the Python wheels for the build
machine's Python version and CPU, producing an architecture-specific package that
installs without internet access on matching systems (it falls back to PyPI when
the bundled wheels do not fit).

Command-line examples:

```sh
pdflx-cli merge a.pdf b.pdf -o merged.pdf
pdflx-cli optimize scans/*.pdf -d smaller/
pdflx-cli bates contract.pdf --prefix ACME- -o numbered.pdf
pdflx-cli verify signed.pdf
pdflx-cli --help
```

OCR is optional: install Tesseract and a language package (for example
`sudo apt install tesseract-ocr tesseract-ocr-eng`) to enable Tools ▸ Recognize Text.

## Printing

**Print** opens a dialog with a live preview of each sheet. Choose the printer (or
**Print to File** to save a PDF), copies and collation, a page range, reverse order,
paper size, orientation (automatic per page, portrait, or landscape), scaling (fit,
shrink oversized pages, or actual size), colour or grayscale, and two-sided printing.
**System print dialog** opens the desktop print dialog for printer-specific options
such as trays and quality. Your choices are remembered.

## Sticky notes

Sticky notes open as anchored comment bubbles with a speech-bubble marker, author heading, and scrollable text. View mode keeps comments read-only; Edit mode provides author editing and Save.
