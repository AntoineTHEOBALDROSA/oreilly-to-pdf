#!/usr/bin/env python3
"""Prototype: package readable O'Reilly browser chapters as an offline EPUB.

Uses a fresh browser session, ordinary page navigation, and visible TOC links.
No private API, credential export, preview bypass, or DRM removal.
"""
import argparse
import asyncio
import hashlib
import posixpath
import re
import sys
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

XHTML = "http://www.w3.org/1999/xhtml"
ET.register_namespace("", XHTML)
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
CSS = """html{color:#202936;background:#fff}
body{font-family:Georgia,'Noto Serif',serif;font-size:1em;line-height:1.6;margin:5%;overflow-wrap:break-word}
p{margin:.65em 0;text-align:left}strong,b{font-weight:bold}em,i{font-style:italic}
h1,h2,h3,h4,h5,h6{font-family:'Noto Sans',Arial,sans-serif;line-height:1.25;page-break-after:avoid;break-after:avoid}
h1{font-size:1.65em;margin:1.6em 0 .6em;color:#183653}
h2{font-size:1.3em;margin:1.5em 0 .55em;color:#183653}
h3{font-size:1.12em;margin:1.3em 0 .45em}h4{font-size:1em;margin:1.1em 0 .4em}
h1.chapterNumber{font-size:1.1em;color:#526c88;margin:0 0 .5em;letter-spacing:.08em}
h1.chapterTitle{font-size:2.1em;margin:0 0 1.2em;padding-bottom:.5em;border-bottom:3px solid #183653}
ul,ol{padding-left:1.5em;margin:.8em 0}li{margin:.35em 0}li p{margin:.3em 0}
ul ul,ol ul,ul ol,ol ol{margin:.3em 0}a{color:#245b88;text-decoration:underline}
code{font-family:'DejaVu Sans Mono',monospace;font-size:.88em}
code.inlineCode{background:#edf1f5;padding:.1em .25em;border-radius:.2em}
pre{font-family:'DejaVu Sans Mono',monospace;font-size:.78em;line-height:1.45;white-space:pre-wrap;overflow-wrap:anywhere;tab-size:4;background:#f2f5f8;border:1px solid #d6dfe8;border-left:3px solid #456887;padding:.9em;margin:1em 0}
pre code{font:inherit;background:none;padding:0;white-space:inherit}
.hljs-keyword,.hljs-selector-tag{color:#764394;font-weight:bold}.hljs-string{color:#166654}
.hljs-number,.hljs-literal{color:#934f19}.hljs-comment{color:#68717d;font-style:italic}
.hljs-built_in,.hljs-title{color:#205a91}.hljs-meta{color:#764394}
figure{margin:1.3em 0;text-align:center;break-inside:avoid;page-break-inside:avoid}
img,svg{max-width:100%;height:auto}figure img{display:block;margin:0 auto}
.packt_figref,.tablepackt_figref,figcaption{font-family:Arial,sans-serif;font-size:.85em;color:#526274;line-height:1.4;margin:.5em 0 1.2em}
table{border-collapse:collapse;table-layout:fixed;width:100%;font-family:Arial,sans-serif;font-size:.85em;line-height:1.4;margin:1em 0}
td,th{border:1px solid #c5d0dc;padding:.6em;vertical-align:top;overflow-wrap:anywhere}
th{background:#e5edf5;color:#183653;font-weight:bold}td p,th p{margin:.3em 0}
tbody tr:nth-child(even){background:#f6f8fa}thead{display:table-header-group}tr{break-inside:avoid;page-break-inside:avoid}
.note,.information,.tip,.warning{background:#edf4fa;border-left:3px solid #557b9f;padding:.75em 1em;margin:1em 0}
.infonormal{margin:.4em 0}.orm-ChapterReader-snippetButtonContainer{display:none}
.pagenum{margin-left:.7em;font-size:.9em}
.section-letter{font:700 1.5em Arial,sans-serif;margin:1em 0 .4em;color:#183653}
.term{margin:.5em 0}.subterm{margin:.2em 0 .2em 1.2em}
"""
ALLOWED = set("section article div p h1 h2 h3 h4 h5 h6 span a strong em b i u s small sup sub pre code blockquote ul ol li dl dt dd table caption colgroup col thead tbody tfoot tr th td figure figcaption img br hr aside header footer abbr cite q time".split())
ATTRS = {"id", "class", "title", "lang", "dir", "alt", "colspan", "rowspan", "scope", "start", "value", "type"}
PREVIEW_MARKERS = ("Content preview from", "Become an O’Reilly member", "Become an O'Reilly member", "Unlock full access")


def canonical(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in {"learning.oreilly.com", "www.oreilly.com", "oreilly.com"} or p.port not in {None, 443} or p.username:
        raise ValueError("Use an HTTPS O'Reilly book link.")
    return urlunsplit(("https", "learning.oreilly.com", p.path, "", ""))


def book_root(url):
    url = canonical(url)
    match = re.match(r"^(/library/view/[^/]+/[^/]+/)", urlsplit(url).path)
    if not match:
        raise ValueError("Expected a /library/view/title/book-id/ link.")
    return "https://learning.oreilly.com" + match.group(1)


def toc_links(links, root):
    seen, result = set(), []
    for item in links:
        try:
            url = canonical(urljoin(root, item["href"]))
        except (ValueError, KeyError):
            continue
        if url.startswith(root) and re.search(r"\.(?:xhtml|html|htm)$", urlsplit(url).path, re.I) and url not in seen:
            seen.add(url)
            result.append((url, item.get("label", "").strip() or unquote(urlsplit(url).path.rsplit("/", 1)[-1])))
    return result


def guard(text):
    if any(marker.casefold() in text.casefold() for marker in PREVIEW_MARKERS):
        raise RuntimeError("O'Reilly is showing a preview. Sign in and open the full reader; nothing was exported.")


def xml(element):
    if element.tag in {"html", "{" + XHTML + "}html"}:
        # Default XHTML namespaces keep standard tag names in the serialized book.
        # Prefixed names such as html:p lose their semantics in HTML-based readers.
        for node in element.iter():
            if isinstance(node.tag, str) and not node.tag.startswith("{"):
                node.tag = "{" + XHTML + "}" + node.tag
            if node.get("xmlns") == XHTML:
                node.attrib.pop("xmlns")
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(element, encoding="utf-8")


def xhtml_document(title):
    root = ET.Element("{" + XHTML + "}html", {"lang": "en"})
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = title
    ET.SubElement(head, "link", {"rel": "stylesheet", "type": "text/css", "href": "style.css"})
    return root, ET.SubElement(root, "body")


def clean_fragment(html, source_url, chapter_paths, images):
    """Convert HTML to well-formed XHTML and remove active/remote resources."""
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(["script", "style", "iframe", "form", "button", "input", "object", "embed", "link", "video", "audio"]):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.name not in ALLOWED:
            node.unwrap()
    for node in soup.find_all(True):
        original = dict(node.attrs)
        node.attrs = {k: v for k, v in original.items() if k in ATTRS}
        if node.name == "col" and "style" in original:
            width = re.search(r"(?:^|;)\s*width\s*:\s*(\d+(?:\.\d+)?%)\s*(?:;|$)", original["style"])
            if width and float(width.group(1)[:-1]) <= 100:
                node["style"] = "width:" + width.group(1)
        if node.name == "img":
            src = original.get("src", "")
            target = images.get(urljoin(source_url, src))
            if not target:
                raise RuntimeError("An image was not downloaded; refusing to create an incomplete EPUB.")
            node["src"] = target
        if node.name == "a":
            if "name" in original and not node.get("id"):
                node["id"] = original["name"]
            href = urljoin(source_url, original.get("href", ""))
            parsed = urlsplit(href)
            if parsed.scheme not in {"https", "http", "mailto"}:
                continue
            try:
                target = chapter_paths.get(canonical(href))
            except ValueError:
                target = None
            node["href"] = target + ("#" + parsed.fragment if parsed.fragment else "") if target else href
    # Minimal formatting escapes XML's special characters and leaves Unicode intact.
    wrapper = ET.fromstring('<div xmlns="' + XHTML + '">' + soup.decode(formatter="minimal") + '</div>')
    return wrapper


def repair_chapter_links(chapters):
    """Keep index links usable when the publisher's target anchor is absent."""
    documents = {name: ET.fromstring(content) for name, label, content in chapters}
    identifiers = {name: {node.get("id") for node in doc.iter() if node.get("id")}
                   for name, doc in documents.items()}
    for name, doc in documents.items():
        for node in doc.iter():
            href = node.get("href", "")
            parsed = urlsplit(href)
            if parsed.scheme or not parsed.fragment:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parsed.path))) if parsed.path else name
            if target in identifiers and unquote(parsed.fragment) not in identifiers[target]:
                node.set("href", parsed.path or posixpath.basename(name))
    return [(name, label, xml(documents[name])) for name, label, content in chapters]


def write_epub(path, title, chapters, assets, identifier, source):
    path = Path(path)
    if path.exists():
        raise FileExistsError("Output already exists; choose a different --output path.")
    chapters = repair_chapter_links(chapters)
    path.parent.mkdir(parents=True, exist_ok=True)
    package = ET.Element("package", {"xmlns": OPF, "version": "3.0", "unique-identifier": "book-id"})
    metadata = ET.SubElement(package, "metadata", {"xmlns:dc": DC})
    ET.SubElement(metadata, "dc:identifier", {"id": "book-id"}).text = identifier
    ET.SubElement(metadata, "dc:title").text = title
    ET.SubElement(metadata, "dc:language").text = "en"
    ET.SubElement(metadata, "dc:source").text = source
    ET.SubElement(metadata, "meta", {"property": "dcterms:modified"}).text = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = ET.SubElement(package, "manifest")
    ET.SubElement(manifest, "item", {"id": "nav", "href": "nav.xhtml", "media-type": "application/xhtml+xml", "properties": "nav"})
    ET.SubElement(manifest, "item", {"id": "style", "href": "style.css", "media-type": "text/css"})
    spine = ET.SubElement(package, "spine")
    nav, body = xhtml_document(title)
    navigation = ET.SubElement(body, "nav", {"xmlns:epub": "http://www.idpf.org/2007/ops", "epub:type": "toc", "id": "toc"})
    ET.SubElement(navigation, "h1").text = title
    ordered = ET.SubElement(navigation, "ol")
    for i, (name, label, content) in enumerate(chapters):
        ET.SubElement(manifest, "item", {"id": f"c{i}", "href": name, "media-type": "application/xhtml+xml"})
        ET.SubElement(spine, "itemref", {"idref": f"c{i}"})
        ET.SubElement(ET.SubElement(ordered, "li"), "a", {"href": name}).text = label
    for i, (name, (mime, data)) in enumerate(assets.items()):
        ET.SubElement(manifest, "item", {"id": f"a{i}", "href": name, "media-type": mime})
    temporary = path.with_suffix(path.suffix + ".partial-" + uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
            archive.writestr("EPUB/package.opf", xml(package))
            archive.writestr("EPUB/nav.xhtml", xml(nav))
            archive.writestr("EPUB/style.css", CSS)
            for name, label, content in chapters:
                archive.writestr("EPUB/" + name, content)
            for name, (mime, data) in assets.items():
                archive.writestr("EPUB/" + name, data)
        # Exclusive creation prevents overwriting another process's output.
        with path.open("xb") as output, temporary.open("rb") as incoming:
            import shutil
            shutil.copyfileobj(incoming, output)
    finally:
        temporary.unlink(missing_ok=True)


async def capture(page, selector=None):
    selectors = [selector] if selector else ["#sbo-rt-content", "#book-content", '[data-testid="book-content"]']
    # O'Reilly initially renders a spinner before inserting the chapter HTML.
    # Wait for actual readable content rather than relying on page load alone.
    deadline = asyncio.get_running_loop().time() + 30
    while asyncio.get_running_loop().time() < deadline:
        guard(await page.locator("body").inner_text())
        for frame in page.frames:
            if frame != page.main_frame and not frame.url.startswith("https://"):
                continue
            for candidate in selectors:
                node = frame.locator(candidate)
                if await node.count() == 1 and await node.is_visible():
                    content = await node.evaluate("el => ({html:el.innerHTML, base:el.baseURI, text:el.innerText})")
                    if content["text"].strip():
                        return content
        await asyncio.sleep(0.5)
    raise RuntimeError("Could not identify the chapter container. The reader needs inspection; use --selector only for the actual chapter element.")


async def export(args):
    from playwright.async_api import async_playwright
    root = book_root(args.url)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(accept_downloads=False)
        page = await context.new_page()
        try:
            await page.goto(canonical(args.url), wait_until="domcontentloaded")
            print("In the browser, sign in to your O'Reilly account.")
            print("The session is temporary; this tool does not save login cookies or passwords.")
            print("If the page stays loading after sign-in, the tool will open your book directly.")
            await asyncio.to_thread(input, "After signing in, press Enter here: ")
            # The sign-in redirect can stall even after authentication succeeds.
            # Navigate directly to the selected book in the same session.
            await page.goto(canonical(args.url), wait_until="domcontentloaded")
            first = await capture(page, args.selector)
            if len(first["text"].strip()) < 100:
                raise RuntimeError("The selected element does not contain enough chapter text.")
            if args.chapter_only:
                links = [(canonical(args.url), "Chapter")]
            else:
                toggle = page.get_by_role("button", name="Table of contents", exact=True)
                if await toggle.count() == 1 and await toggle.get_attribute("aria-expanded") != "true":
                    await toggle.click()
                sidebar = page.get_by_role("complementary", name="Table of contents", exact=True)
                if await sidebar.count() == 1:
                    await sidebar.wait_for(state="visible")
                    link_container = sidebar
                else:
                    await page.goto(root, wait_until="domcontentloaded")
                    # Expand visible contents, never enumerate guessed endpoints.
                    for _ in range(20):
                        more = page.get_by_role("button", name="Show More", exact=True)
                        if await more.count() != 1 or not await more.is_visible():
                            break
                        await more.click()
                    link_container = page
                links = toc_links(await link_container.locator("a[href]").evaluate_all("els => els.map(e => ({href:e.href,label:e.innerText}))"), root)
                if not links:
                    raise RuntimeError("No chapter links found in the visible table of contents. Try --chapter-only or inspect the reader.")
                if canonical(args.url) != root and canonical(args.url) not in dict(links):
                    raise RuntimeError("The selected chapter is absent from the table of contents; refusing an uncertain book export.")
            title = args.title or (await page.title()).split(" [Book]")[0]
            paths = {url: f"chapter-{i+1:03}.xhtml" for i, (url, _) in enumerate(links)}
            chapters, assets, images = [], {}, {}
            for i, (url, label) in enumerate(links):
                print(f"Reading chapter {i+1}/{len(links)}: {label}")
                response = await page.goto(url, wait_until="domcontentloaded")
                if response and response.status >= 400:
                    raise RuntimeError(f"Chapter request returned HTTP {response.status}; stopped.")
                captured = await capture(page, args.selector)
                soup = BeautifulSoup(captured["html"], "html.parser")
                if soup.find("svg"):
                    raise RuntimeError("This chapter has inline SVG. SVG support needs inspection; export stopped to avoid losing diagrams.")
                for image in soup.find_all("img"):
                    src = urljoin(captured["base"], image.get("src", ""))
                    if src in images:
                        continue
                    p = urlsplit(src)
                    host = p.hostname or ""
                    if p.scheme != "https" or not any(host == domain or host.endswith("." + domain) for domain in ("oreilly.com", "oreillystatic.com")):
                        raise RuntimeError("A chapter image uses an unsupported source; stopped before creating an incomplete book.")
                    result = await context.request.get(src, max_redirects=0)
                    if result.status != 200:
                        raise RuntimeError(f"Image request returned HTTP {result.status}; stopped.")
                    mime = result.headers.get("content-type", "").split(";", 1)[0].strip()
                    extension = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}.get(mime)
                    if not extension:
                        raise RuntimeError(f"Unsupported image format {mime}; stopped.")
                    name = "images/" + hashlib.sha256(src.encode()).hexdigest()[:20] + "." + extension
                    assets[name] = (mime, await result.body())
                    images[src] = name
                    await result.dispose()
                document, body = xhtml_document(label)
                body.append(clean_fragment(captured["html"], captured["base"], paths, images))
                chapters.append((paths[url], label, xml(document)))
                await asyncio.sleep(args.delay)
            write_epub(args.output, title, chapters, assets, "urn:uuid:" + str(uuid.uuid4()), root)
            print(f"Saved {len(chapters)} chapters and {len(assets)} images to {args.output}")
        finally:
            await context.close()
            await browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="O'Reilly book or chapter URL")
    parser.add_argument("--output", default="book.epub", help="New EPUB file (existing files are never replaced)")
    parser.add_argument("--title")
    parser.add_argument("--chapter-only", action="store_true", help="Test one chapter before exporting a book")
    parser.add_argument("--selector", help="CSS selector of the full chapter container, if the reader changes")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between chapters (minimum 1)")
    args = parser.parse_args()
    if args.delay < 1:
        parser.error("--delay must be at least 1 second")
    try:
        canonical(args.url)
        if Path(args.output).exists():
            raise FileExistsError("Output exists; choose another --output path.")
        asyncio.run(export(args))
    except (RuntimeError, ValueError, FileExistsError) as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
