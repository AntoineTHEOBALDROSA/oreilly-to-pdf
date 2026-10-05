#!/usr/bin/env python3
"""Render a saved EPUB as a formatted, searchable PDF without network access."""
import argparse
import html
import json
import posixpath
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from offline import CSS

PRINT_CSS = """
@page{size:A4;margin:19mm 17mm 21mm;
 @top-left{content:BOOK_TITLE_PLACEHOLDER;font:7pt 'Noto Sans';color:#64748b}
 @bottom-center{content:counter(page);font:8pt 'Noto Sans';color:#64748b}}
@page:first{@top-left{content:none}@bottom-center{content:none}}
body{font-family:'Noto Serif',serif;font-size:10.5pt;line-height:1.52;margin:0;color:#202936}
h1,h2,h3,h4,h5,h6{font-family:'Noto Sans',sans-serif;bookmark-level:none}
h1{font-size:18pt;bookmark-level:2}h2{font-size:14pt;bookmark-level:3}
h3{font-size:12pt;bookmark-level:4}h4{font-size:10.5pt;bookmark-level:5}
h1.chapterNumber{font-size:12pt;bookmark-level:none}
h1.chapterTitle{font-size:26pt;bookmark-level:1}
.book-part{break-before:page}.book-part>h1{bookmark-level:1}
pre{font-family:'DejaVu Sans Mono';font-size:8pt;line-height:1.4;white-space:pre-wrap;
 overflow-wrap:anywhere;padding:9pt;break-inside:auto;box-decoration-break:clone}
pre code{font-size:inherit}code.inlineCode{font-size:8.6pt;overflow-wrap:anywhere}
p{orphans:3;widows:3}li{orphans:2;widows:2}
table{font-family:'Noto Sans';font-size:8.5pt;line-height:1.4;table-layout:fixed}
td,th{padding:6pt;overflow-wrap:anywhere}td pre,th pre{font-size:7pt;padding:4pt}
img{max-width:100%;max-height:225mm;object-fit:contain}
figure{break-inside:avoid;margin:12pt 0 5pt}
.packt_figref{break-before:avoid;font-size:8.5pt;margin:5pt 0 14pt}
.note,.information,.tip,.warning{break-inside:auto}
.title-page{padding-top:42mm;break-after:page}
.title-page h1{font-size:36pt;line-height:1.2;border-bottom:3pt solid #183653;padding-bottom:15pt;bookmark-level:none}
.title-page .authors{font:14pt 'Noto Sans';margin-top:18pt}
.title-page .edition{font:10pt 'Noto Sans';color:#526274;margin-top:20pt}
.contents{break-after:page}.contents h1{bookmark-level:1}
.contents li{margin:9pt 0;list-style:none}.contents ol{padding:0}
.contents a{text-decoration:none}.contents a::after{content:leader('.') target-counter(attr(href),page)}
.section-letter{break-after:avoid}
.index-link{font-size:0}
.index-link::after{content:target-counter(attr(href),page);font-size:9.5pt}
.index-page-note{font:9pt 'Noto Sans';color:#526274;margin-bottom:16pt}
"""


def convert(epub_path, output_path, limit=None, authors=None):
    from weasyprint import HTML
    from weasyprint.urls import URLFetcher, FatalURLFetchingError
    epub_path, output_path = Path(epub_path), Path(output_path)
    with tempfile.TemporaryDirectory(prefix='offline-book-') as folder:
        root = Path(folder)
        with zipfile.ZipFile(epub_path) as archive:
            for item in archive.infolist():
                if not (root / item.filename).resolve().is_relative_to(root.resolve()):
                    raise ValueError('Unsafe archive path')
            archive.extractall(root)
        container = ET.parse(root/'META-INF/container.xml')
        package_path = container.find('.//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile').get('full-path')
        package = ET.parse(root/package_path)
        ns={'o':'http://www.idpf.org/2007/opf','d':'http://purl.org/dc/elements/1.1/'}
        title = package.find('.//d:title',ns).text
        author_names = authors.split(';') if authors else [node.text for node in package.findall('.//d:creator',ns)]
        package_folder = (root/package_path).parent
        manifest = {node.get('id'):node.get('href') for node in package.findall('.//o:manifest/o:item',ns)}
        names=[manifest[node.get('idref')] for node in package.findall('.//o:spine/o:itemref',ns)]
        if limit: names=names[:limit]
        destinations={name:f'part-{i}' for i,name in enumerate(names)}
        parts, contents = [], []
        for i,name in enumerate(names):
            soup=BeautifulSoup((package_folder/name).read_text(),'html.parser')
            label=soup.title.get_text()
            body=soup.body
            index_heading=body.select_one('.index > h1')
            if index_heading:
                note=soup.new_tag('p',attrs={'class':'index-page-note'})
                note.string="Page references use this PDF's page numbers. Click a reference to jump to its section."
                index_heading.insert_after(note)
            for node in body.select('[id]'):
                node['id']=destinations[name]+'-'+node['id']
            for anchor in body.find_all('a',href=True):
                ref=urlsplit(anchor['href'])
                if not ref.scheme:
                    target=posixpath.normpath(posixpath.join(posixpath.dirname(name),unquote(ref.path))) if ref.path else name
                    if target in destinations:
                        anchor['href']='#'+destinations[target]+('-'+unquote(ref.fragment) if ref.fragment else '')
            for image in body.find_all('img',src=True):
                image['src']=(package_folder/posixpath.dirname(name)/image['src']).resolve().as_uri()
            contents.append(f'<li><a href="#{destinations[name]}">{html.escape(label)}</a></li>')
            parts.append(f'<section class="book-part" id="{destinations[name]}">'+body.decode_contents()+'</section>')
        author_line = '<p class="authors">'+'<br>'.join(html.escape(name.strip()) for name in author_names)+'</p>' if author_names else ''
        cover=f'<section class="title-page"><h1>{html.escape(title)}</h1>'+author_line+'<p class="edition">Offline reading edition</p></section>'
        toc='<section class="contents"><h1>Contents</h1><ol>'+''.join(contents)+'</ol></section>'
        print_css = PRINT_CSS.replace("BOOK_TITLE_PLACEHOLDER", json.dumps(title,ensure_ascii=False))
        document='<!doctype html><html lang="en"><head><meta charset="utf-8"><title>'+html.escape(title)+'</title><style>'+CSS+print_css+'</style></head><body>'+cover+toc+''.join(parts)+'</body></html>'
        class LocalFetcher(URLFetcher):
            def fetch(self,url,headers=None):
                if urlsplit(url).scheme != 'file':
                    raise FatalURLFetchingError('PDF rendering uses local assets only')
                if not Path(unquote(urlsplit(url).path)).resolve().is_relative_to(root.resolve()):
                    raise FatalURLFetchingError('Asset is outside the extracted EPUB')
                return super().fetch(url,headers)
        output_path.parent.mkdir(parents=True,exist_ok=True)
        HTML(string=document,base_url=package_folder.as_uri()+'/',url_fetcher=LocalFetcher(allowed_protocols={'file'},fail_on_errors=True)).write_pdf(output_path,optimize_images=True,dpi=200)
        print(f'Saved {output_path} ({len(names)} book sections)')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('epub')
    parser.add_argument('pdf')
    parser.add_argument('--limit',type=int,help='Render a few sections for layout review')
    parser.add_argument('--authors',help='Author names separated by semicolons, if absent from EPUB metadata')
    args=parser.parse_args()
    convert(args.epub,args.pdf,args.limit,args.authors)
