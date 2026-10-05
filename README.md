# O’Reilly offline EPUB prototype for Linux

This is an **experimental browser exporter**, not an official O’Reilly downloader.
It packages full chapters that your signed-in browser can read into a local EPUB,
including chapter navigation, code blocks, tables, and downloaded images.

**Status:** The EPUB packager and formatted PDF converter have been checked.
The standalone browser exporter has not yet been exercised end to end with a
separate login, so that command remains experimental. Different books may use
different reader layouts or unsupported image formats.

## Install

Open a terminal in this folder:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

If your distribution does not have Python’s virtual environment support,
install its `python3-venv` package first. If Chromium reports missing Linux
libraries, Playwright’s `install-deps chromium` command lists/installs them
and may require administrator access.

## Test one chapter

```sh
.venv/bin/python offline.py \
  'PASTE_YOUR_OREILLY_CHAPTER_URL_HERE' \
  --chapter-only --output chapter-1.epub \
  --title 'Your Book Title — Chapter 1'
```

Replace the URL placeholder with the first chapter's link from your book.
A browser opens. Sign in **inside that browser**, then return to the terminal
and press Enter. If O’Reilly’s post-login page keeps loading, press Enter after
submitting your login; the tool opens the book directly in the same session.
You do not need to share your
password, edit credentials into the script, or export your browser cookies.
The temporary login session closes when the tool finishes.

If the reader’s chapter container differs from the defaults, the tool stops.
The `--selector` option can specify its actual CSS selector after inspection.
Do not select the whole page: it includes navigation and account controls.

## Export the book

Once the chapter test works:

```sh
.venv/bin/python offline.py \
  'PASTE_YOUR_OREILLY_CHAPTER_URL_HERE' \
  --output your-book.epub \
  --title 'Your Book Title'
```

Open the EPUB in your preferred reader. To produce the formatted PDF:

```sh
.venv/bin/python -m pip install -r requirements-pdf.txt
.venv/bin/python to_pdf.py your-book.epub \
  your-book.pdf \
  --authors 'First Author; Second Author'
```

## Scope and limitations

- Downloads one explicitly selected book, following links in its table of contents.
- Visits chapters sequentially, with a delay between them.
- Stops on previews, access errors, missing images, unsupported image formats,
  or an unrecognized reader. No EPUB is produced until all selected chapters succeed.
- Does not bypass a paywall, extract encrypted mobile-app files, or remove DRM.
- Preserves heading hierarchy, lists, code indentation, syntax colors, note boxes,
  tables and images. The PDF adds chapter page breaks, contents with page numbers,
  bookmarks, embedded fonts, and index references updated to the PDF's pages.
  It reflows the book rather than duplicating the
  publisher’s original printed page layout or interactive widgets.
- If the publisher’s index points to an anchor absent from the chapter HTML,
  the exported link opens the chapter instead. Other index anchors retain their
  exact destinations.
- Currently supports PNG, JPEG, and GIF chapter images hosted on O’Reilly domains.
  Inline SVG, WebP, and external image hosts cause a stop, pending inspection.
- The EPUB exporter refuses to replace an existing output. The PDF converter
  replaces its specified output so you can regenerate it after adjusting styles.
- EPUB files contain the exported book text, not login information.

O’Reilly officially supports offline reading through its mobile apps and says
desktop reading requires an internet connection:
https://www.oreilly.com/online-learning/support/content.html
Subscription access and separately purchased ebook downloads are different.
Use this prototype only for content you are permitted to save; a subscription
alone does not establish permission for a standalone export.

## Check the packager

```sh
.venv/bin/python -m unittest discover -s tests -v
```
# oreilly-to-pdf
