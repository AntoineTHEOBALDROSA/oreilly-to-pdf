import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import offline

BASE = "https://learning.oreilly.com/library/view/test/123/"


class ExportTests(unittest.TestCase):
    def test_chapter_tags_are_recognized_by_html_based_readers(self):
        from bs4 import BeautifulSoup
        root, body = offline.xhtml_document("Formatted chapter")
        body.append(offline.clean_fragment('<h1>Heading</h1><ul><li>Item</li></ul><pre>    indented\nnext</pre>', BASE+'a.xhtml', {}, {}))
        html = offline.xml(root).decode()
        ET.fromstring(html)
        reader = BeautifulSoup(html, "html.parser")
        self.assertEqual(reader.find('h1').get_text(), 'Heading')
        self.assertEqual(reader.find('li').get_text(), 'Item')
        self.assertEqual(reader.find('pre').get_text(), '    indented\nnext')
    def test_multichapter_epub_with_unicode_code_image_and_links(self):
        paths = {BASE + "a.xhtml": "chapter-001.xhtml", BASE + "b.xhtml": "chapter-002.xhtml"}
        image_url = BASE + "diagram.png"
        fragment = offline.clean_fragment(
            '<h1 id="start">Café &amp; code</h1><pre>a &lt; b\n  x()</pre>'
            '<p><a href="b.xhtml#next">Next</a><img src="diagram.png" alt="Diagram"></p>'
            '<script>alert(1)</script><p onclick="bad()">Readable</p>',
            BASE + "a.xhtml", paths, {image_url: "images/diagram.png"})
        root, body = offline.xhtml_document("Chapter 1")
        body.append(fragment)
        root2, body2 = offline.xhtml_document("Chapter 2")
        ET.SubElement(body2, "h1", {"id": "next"}).text = "Second chapter"
        chapters = [("chapter-001.xhtml", "Chapter 1", offline.xml(root)),
                    ("chapter-002.xhtml", "Chapter 2", offline.xml(root2))]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "book.epub"
            offline.write_epub(path, "Sample Book", chapters, {"images/diagram.png": ("image/png", b"sample")}, "sample-id", BASE)
            with zipfile.ZipFile(path) as archive:
                self.assertEqual(archive.namelist()[0], "mimetype")
                self.assertEqual(archive.getinfo("mimetype").compress_type, zipfile.ZIP_STORED)
                self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
                for name in archive.namelist():
                    if name.endswith((".xml", ".opf", ".xhtml")):
                        ET.fromstring(archive.read(name))
                package = ET.fromstring(archive.read("EPUB/package.opf"))
                ns = {"o": offline.OPF}
                manifest = package.find("o:manifest", ns)
                for item in manifest:
                    self.assertIn("EPUB/" + item.attrib["href"], archive.namelist())
                self.assertEqual(len(package.find("o:spine", ns)), 2)
                doc = ET.fromstring(archive.read("EPUB/chapter-001.xhtml"))
                self.assertIn("Café & code", "".join(doc.itertext()))
                self.assertEqual(doc.find(".//{" + offline.XHTML + "}a").attrib["href"], "chapter-002.xhtml#next")
                self.assertFalse(any(el.tag.endswith("script") or "onclick" in el.attrib for el in doc.iter()))
            with self.assertRaises(FileExistsError):
                offline.write_epub(path, "Other", [], {}, "id", BASE)

    def test_preview_never_exported(self):
        for marker in offline.PREVIEW_MARKERS:
            with self.assertRaises(RuntimeError):
                offline.guard("Book content " + marker)

    def test_missing_publisher_index_anchors_fall_back_to_chapter(self):
        root, body = offline.xhtml_document("Index")
        ET.SubElement(body, "a", {"href": "other.xhtml#absent"}).text = "Entry"
        other, other_body = offline.xhtml_document("Chapter")
        ET.SubElement(other_body, "p").text = "Chapter content"
        fixed = offline.repair_chapter_links([("index.xhtml", "Index", offline.xml(root)),
                                               ("other.xhtml", "Chapter", offline.xml(other))])
        self.assertEqual(ET.fromstring(fixed[0][2]).find(".//{" + offline.XHTML + "}a").get("href"), "other.xhtml")

    def test_missing_image_aborts_instead_of_silently_losing_diagram(self):
        with self.assertRaises(RuntimeError):
            offline.clean_fragment('<img src="diagram.png">', BASE + "a.xhtml", {}, {})

    def test_toc_order_fragments_and_other_books(self):
        links = [{"href": BASE + "b.xhtml#one", "label": "B"},
                 {"href": BASE.replace("learning.", "www.") + "b.xhtml#two", "label": "B2"},
                 {"href": BASE + "a.xhtml", "label": "A"},
                 {"href": "https://learning.oreilly.com/library/view/other/456/a.xhtml", "label": "Other"},
                 {"href": "https://learning.oreilly.com.evil.test/library/view/test/123/a.xhtml", "label": "Bad"}]
        self.assertEqual(offline.toc_links(links, BASE), [(BASE + "b.xhtml", "B"), (BASE + "a.xhtml", "A")])


if __name__ == "__main__":
    unittest.main()
