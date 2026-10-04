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

Choose **Add Table…** from the left toolbar or **Edit & Insert** in the main menu. Set rows,
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

The left toolbar groups tools by task, most used first: **Select** and **Pan**;
**Text**, **Image**, **Shapes**, **Pen**, **Table**, and **Symbols**;
**Highlight**, **Erase highlight**, the highlight color, **Sticky note**, and
**Stamp**; **Signature** and **Forms**; then **Pages**, **Bookmarks**,
**Protect**, and **More tools** (review marks, comments, watermarks and page
numbers, crop, and redaction). The main menu (☰) starts with New, Open,
**Create**, Save As, Print, and **Export**, followed by task menus in the same
order of use: **Edit & Insert**, **Forms**, **Annotate & Review**, **Pages**,
**Document**, **Protect**, and **Tools**, then **View** and **Preferences**
(including AI settings).

Enter **Edit** mode to:

- Redact selected regions or search matches, with a preview before applying.
  Saving removes the redacted content from the PDF. **Sensitive data** finds
  email addresses, web addresses, IBANs and card numbers (both checked with
  their checksums, so random digits are not caught), dates, phone numbers, and
  ID numbers across the chosen pages; the dialog lists what was found before
  you apply.
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

The **Forms** icon in the left toolbar has three entries in Edit mode:
**Build & Fill Forms…** opens the Form Fields sidebar, which lists, fills, and
edits fields and holds **Add fields** for building a form (on a page without
fields, it opens ready to type the first label; see *Building forms
quickly* below); **Create Form Field…** creates one field with every option set
in a dialog first; **Flatten Form Fields…** turns fields into ordinary page
content. The menu is disabled in View mode; existing fields can still be filled
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
chosen page; **Submit form** sends the values to a web address. These are real
PDF actions saved with the button. Clicking a submit button first checks the
required fields, then asks before sending anything and shows the address and
format (FDF, XFDF, HTML form data, or the whole PDF). Plain `http://` addresses
outside your computer get a warning that the data is not encrypted. The server's
answer is shown afterwards, and a failed submission offers **Try Again** or
**Export Form Data…**. `mailto:` buttons open your email app with the values. Click an unsigned
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

### Building forms quickly

The **Add fields** section of the Form Fields sidebar builds a form without a
dialog per field:

1. Type the field's label (for example "First Name") and press the **\*** button
   if the field is required.
2. Click a field type: Text, Paragraph, Date, Email, Phone, Number, Dropdown,
   List, Checkbox, Radio, Signature, Submit, or Reset.
3. Drag on the page to draw the field, or click to place it at a standard size.

Each field gets a name from its label (`first_name`), the label drawn above it
(or to its left, or none), a `*` when required, and a tooltip that screen
readers announce. Date, Number, Email, and Phone fields format or check what is
typed. Dropdowns, lists, and radio groups take their choices from the
comma-separated **Choices** box, and Submit buttons take an address. The tool
stays active for the next field; **Esc** or the Select tool ends it. Each
placement is one undo step.

- **Form style…** sets the look of new fields: font, size, text, border and
  background colors, border width, label size and color, and date format.
  **Apply style** gives the selected fields (or every field on the page) that
  style.
- **Alignment guides** shows pink guides and lines fields up with the edges
  and centers of other fields while you draw, move, or resize them.
- **Multiple copies** repeats the selected field(s) in rows and columns with a set gap,
  named `name_2`, `name_3`, and so on.
- **Tab order** orders the page's fields by rows or by columns, and can show
  each field's position in the order.
- **Detect fields** finds empty boxes, fill-in lines, and checkbox squares on
  a printed or scanned-then-OCRed form and turns them into fields named from
  their nearby labels.
- With several fields selected, the Arrange bar's last button gives them the
  first selected field's style.
- **Field scripts** (in Tools, or the `{ }` button on a field in the list) edits
  a field's JavaScript for each event — keystroke, format, validate, calculate,
  focus, blur, and mouse events — with ready-made examples such as capitals
  only, digits only, a minimum length, price × quantity, today's date, and
  showing another field. Scripts are standard Acrobat form JavaScript saved in
  the PDF. **Form Structure and Scripts…** in the main menu lists every script
  in the document.

The AI assistant can build forms the same way, including styled fields,
date/email/phone/number formats, and submit buttons.

Duplicate a form field with its sidebar copy icon, **right-click → Duplicate**,
or **Ctrl+D** while selected or filling it. The copy gets a unique name, a small
position offset, and independent values while retaining appearance, defaults,
and button actions. Radio groups duplicate together. Unsigned signature fields
can be copied; signed certificate fields cannot. Duplication supports undo/redo.

To line fields up, click one field, then **Shift+click** or **Ctrl+click** more
fields (click a selected one again to remove it). A dashed outline marks the
first field, which the others follow. An **Arrange** bar at the top of the page
then aligns left, center, right, top, middle, or bottom edges; spaces three or
more fields evenly; and matches the first field's width, height, or size.
Dragging the move handle moves every selected field together. Each action is a
single undo step.

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

## Find and replace

**Ctrl+H**, the **Replace…** button in the search bar, or **Edit & Insert →
Find and Replace…** opens a dialog that lists every change before it is made:
each line shows the old text struck through and the new text, with its page.
Untick any you want to keep, then press **Replace**. Options: match case, whole
words, regular expressions (`\1` inserts a captured group), and all pages or
the current page. All replacements are one undo step. Text is replaced line by
line, so a phrase split across two lines is not matched.

## Organize pages

**Organize Pages** (the grid button above the page thumbnails, or **Pages →
Organize Pages…**) shows every page as a thumbnail. Drag pages to reorder them;
Ctrl-click or Shift-click selects several. The toolbar rotates, duplicates,
deletes, inserts a blank page after the selection, or extracts the selection to
a new document. **Del** deletes, **Ctrl+D** duplicates, **Ctrl+A** selects
all, double-click opens a page, and **Esc** or **Done** closes the view. Each
action is one undo step.

## Alignment guides

While you move or resize text, images, shapes, drawings, or form fields, they
snap to the edges and centers of nearby objects and to the page center, and
pink guide lines show what they line up with. Turn this off with **View →
Alignment Guides**.

## Shapes and lines

The shapes button in the left toolbar opens a picker with rectangle, rounded
rectangle, ellipse, triangle, right triangle, diamond, pentagon, hexagon, star,
block arrow, speech callout, check mark, cross, **Line** and **Arrow**. Drag on
the page to draw. For lines, hold **Shift** to snap the angle to 15° steps.

With a shape selected (or a shape tool active), the toolbar sets fill,
transparency, outline color, and outline width (**0** means no outline). The
**Shape options** button adds corner radius, opacity, line style (solid,
dashed, dotted), the number of sides, and star points and inner radius. With a
line selected, **Line options** adds arrowheads at either end and the line
style. Every change can be undone.

## Stacking order

Rectangles, ellipses, text, images and drawings on a page stack on top of each
other. Right-click an element and choose **Bring to Front** (**Ctrl+]**) or
**Send to Back** (**Ctrl+[**). For example, draw a filled rectangle (turn off
**Transparent** and pick a fill color) and send it to the back to make a
colored band behind text. This also works under the PDF's original text, which
is redrawn above the shape. Both actions can be undone.

## AI assistant

The sparkle button in the header bar (or **Ctrl+K**) opens a floating AI bar
over the document. It uses [OpenRouter](https://openrouter.ai): open **AI
Settings** (gear icon), paste your OpenRouter API key, and enter the model ID
to use. The list button next to the model field shows OpenRouter's current
models that accept images and tools, with prices. Nothing is preset, so you
choose the model. The key is stored in the system keyring (or in
`~/.config/pdflx/openrouter.key`, readable only by you, if no keyring is
available). The `OPENROUTER_API_KEY` environment variable overrides it.

Type a request and press **Enter** (**Shift+Enter** adds a line break). The
assistant can read and change text, shapes, lines, images, and form fields,
change many elements at once, add or delete pages, and open a new document.
Filled rectangles and ellipses it draws go behind text automatically, so
colored headers and table bands keep their text visible.
Select elements first to talk about "this" or "these". Attach reference images
or PDFs with the paperclip, by dragging files onto the bar, or by pasting an
image (**Ctrl+V**). To recreate a page from a picture, attach the picture and
ask, for example, "Recreate this as a new PDF". The assistant measures the
picture, rebuilds the page, and compares its render with the picture.

Each request's tool steps are grouped into one collapsible row, so the
conversation stays short. The arrow button hides the conversation and leaves
only the input box. The dock button moves the assistant to a panel on the
right side of the window (and back). The conversation never takes more than
about a third of the window height while the bar floats at the bottom.

Each request is a single undo step: **Ctrl+Z** reverts everything the
assistant did for that request. **Stop** ends a request after the current step,
and the **+** button starts a new conversation. Requests send the page contents
the assistant reads, page renders, and your attachments to OpenRouter and the
model provider you chose.

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

`build-appimage.sh` builds the AppImage for the current architecture.
`org.pdflx.Editor.json` is the Flatpak manifest, and `debian/` contains
Debian packaging definitions.

Build an installable Debian/Ubuntu package and standalone icons with:

```sh
./build-deb.sh
sudo apt install ./dist/pdflx_0.4_all.deb
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
