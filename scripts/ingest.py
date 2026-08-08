#!/usr/bin/env python3
"""Ingest an ebook into a library folder: clean chapter text + a batching plan.

This step uses zero model tokens. It does the deterministic work — unzip, strip
markup, split into chapters, count tokens, pack chapters into balanced batches —
so the indexing subagents only ever spend tokens on reading prose.

Formats: .epub, .txt, .md, .html/.xhtml natively; .mobi/.azw3 via Calibre's
ebook-convert and .pdf via pdftotext/pypdf if those happen to be installed.

Usage:
    python ingest.py BOOK [--library DIR] [--slug NAME] [--exact]
                          [--batch-tokens N] [--max-chapter-tokens N]
"""
import argparse
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

DC = "{http://purl.org/dc/elements/1.1/}"
NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
}
SKIP_TAGS = {"script", "style", "head", "svg"}
BLOCK_TAGS = {"p", "div", "br", "li", "tr", "blockquote",
              "h1", "h2", "h3", "h4", "h5", "h6", "section"}

# Chapter-ish headings in plain text: "CHAPTER IV.", "Part Two", "17.", "PROLOGUE"
# The numeral is required after a keyword — otherwise a stray line reading just
# "part." inside the prose gets mistaken for a chapter break.
_WORD_NUM = (r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
             r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty")
_SEP = r"[\s.:,—–-]*"
HEADING_RE = re.compile(
    rf"^\s{{0,8}}(?:"
    rf"(?:chapter|part|book|section|canto|act|volume)\b{_SEP}"
    rf"(?:[ivxlcdm]{{1,9}}|\d{{1,3}}|{_WORD_NUM})\b{_SEP}"
    rf"|(?:prologue|epilogue|introduction|foreword|afterword|preface|conclusion)\b{_SEP}"
    rf"|\d{{1,3}}{_SEP}"
    rf")$",
    re.IGNORECASE,
)
GUTENBERG_START = re.compile(r"\*\*\*\s*START OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.I)
GUTENBERG_END = re.compile(r"\*\*\*\s*END OF (THE|THIS) PROJECT GUTENBERG.*?\*\*\*", re.I)


# ---------------------------------------------------------------- text cleanup

class TextExtractor(HTMLParser):
    """Strip (X)HTML to plain text, recording where each id= anchor lands.

    The anchor offsets are what let the table of contents drive chapter
    splitting: a ToC entry pointing at `ch03.xhtml#chapter_5` needs to know
    where in the extracted text `chapter_5` actually begins. Offsets are kept
    against the *unnormalized* buffer, so slicing happens first and whitespace
    normalization second — normalizing first would shift every offset.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip_depth = [], 0
        self.anchors = {}
        self._len = 0

    def _emit(self, s):
        self.parts.append(s)
        self._len += len(s)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ("id", "name"):
            if (val := attrs.get(key)) and val not in self.anchors:
                self.anchors[val] = self._len
        if tag in SKIP_TAGS:
            self.skip_depth += 1
        elif tag in BLOCK_TAGS:
            self._emit("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
        elif tag in BLOCK_TAGS:
            self._emit("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            self._emit(data)

    def raw(self):
        return "".join(self.parts)

    def text(self):
        return normalize(self.raw())


class NavParser(HTMLParser):
    """Pull ordered (depth, title, href) entries out of an EPUB 3 nav document."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries = []
        self._in_toc = self._seen_toc = False
        self._depth = 0
        self._href = None
        self._label = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "nav":
            # Prefer the nav explicitly typed as the toc; fall back to the first.
            if attrs.get("epub:type") == "toc" or not self._seen_toc:
                self._in_toc, self._seen_toc = True, True
        elif self._in_toc and tag == "ol":
            self._depth += 1
        elif self._in_toc and tag == "a" and attrs.get("href"):
            self._href, self._label = attrs["href"], []

    def handle_endtag(self, tag):
        if tag == "nav":
            self._in_toc = False
        elif self._in_toc and tag == "ol":
            self._depth = max(0, self._depth - 1)
        elif tag == "a" and self._href is not None:
            title = re.sub(r"\s+", " ", "".join(self._label)).strip()
            self.entries.append((max(1, self._depth), title, self._href))
            self._href, self._label = None, []

    def handle_data(self, data):
        if self._href is not None:
            self._label.append(data)


def strip_bracket_blocks(text, keywords=("Illustration", "Music", "Sidenote")):
    """Remove [Illustration: ...] style editorial blocks, including nested ones.

    These are pure noise for indexing — they inflate token counts and, worse,
    can land inside an extracted quote, which then fails verification against
    the text a human would actually read.
    """
    out, i = [], 0
    while i < len(text):
        if text[i] == "[" and any(text.startswith("[" + k, i) for k in keywords):
            depth, j = 0, i
            while j < len(text):
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            i = j + 1  # unbalanced bracket just runs to end of text, which is fine
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def normalize(raw):
    """Collapse whitespace and rejoin words hyphenated across a line break.

    De-hyphenation matters for quote verification later: a review that quotes
    "light-\nhouse" should still match "lighthouse" in the source.
    """
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    raw = strip_bracket_blocks(raw)
    raw = re.sub(r"(\w)-\n(\w)", r"\1\2", raw)
    raw = re.sub(r"[ \t\f\v ]+", " ", raw)
    raw = re.sub(r" *\n *", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def strip_gutenberg(text):
    if m := GUTENBERG_START.search(text):
        text = text[m.end():]
    if m := GUTENBERG_END.search(text):
        text = text[: m.start()]
    return text.strip()


def html_to_text(raw):
    p = TextExtractor()
    p.feed(raw)
    return p.text()


# ------------------------------------------------------------------- splitting

def split_plain_text(text, target_words=3000):
    """Split loose text into chapters, falling back to fixed-size chunks.

    Prefer real headings; if the book has none (common in converted files),
    chunk on paragraph boundaries so digests still line up with readable units.
    """
    lines = text.split("\n")
    cuts, titles = [], []
    for i, line in enumerate(lines):
        if HEADING_RE.match(line) and len(line.strip()) < 60:
            cuts.append(i)
            titles.append(line.strip())

    chunks = []
    if len(cuts) >= 3:  # 1-2 matches is usually a false positive, not a structure
        cuts.append(len(lines))
        for n, start in enumerate(cuts[:-1]):
            body = "\n".join(lines[start + 1 : cuts[n + 1]]).strip()
            if body:
                chunks.append((titles[n], body))
        head = "\n".join(lines[: cuts[0]]).strip()
        if head and len(head.split()) > 200:
            chunks.insert(0, ("Front matter", head))
    else:
        paras, buf, count = text.split("\n\n"), [], 0
        for para in paras:
            buf.append(para)
            count += len(para.split())
            if count >= target_words:
                chunks.append((None, "\n\n".join(buf).strip()))
                buf, count = [], 0
        if buf:
            chunks.append((None, "\n\n".join(buf).strip()))
    return [(t, b) for t, b in chunks if len(b.split()) >= 20]


def split_oversized(chapters, max_tokens):
    """Break any chapter too large for one digest into 'Part N' pieces.

    A single digest covering 40k tokens of text gets thin and lossy; capping the
    unit keeps every part of the book at comparable resolution.
    """
    out = []
    for title, body in chapters:
        if est_tokens(body) <= max_tokens:
            out.append((title, body))
            continue
        paras = body.split("\n\n")
        budget = max_tokens
        buf, count, part = [], 0, 1
        for para in paras:
            buf.append(para)
            count += est_tokens(para)
            if count >= budget:
                out.append((f"{title or 'Section'} (part {part})", "\n\n".join(buf)))
                buf, count, part = [], 0, part + 1
        if buf:
            out.append((f"{title or 'Section'} (part {part})", "\n\n".join(buf)))
    return out


# -------------------------------------------------------------------- readers

def resolve(base_dir, href):
    """Resolve an href against the directory of the document that contains it.

    Hrefs are URIs, so they must be percent-decoded before they can be matched
    against zip entry names. Skipping this looks harmless until a book contains
    a filename with a reserved character: the ToC then says
    `CR%21A6VK..._split_000.html` while the spine says `CR!A6VK..._split_000.html`,
    nothing matches, and the whole table of contents is silently discarded.
    """
    href = unquote(href.split("#")[0])
    if not href:
        return None
    return posixpath.normpath(posixpath.join(base_dir, href)).lstrip("./")


def parse_ncx(raw):
    """Ordered (depth, title, href) from an EPUB 2 NCX navMap."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    ncx = "{http://www.daisy.org/z3986/2005/ncx/}"
    out = []

    def walk(node, depth):
        for pt in node.findall(f"{ncx}navPoint"):
            label = pt.find(f"{ncx}navLabel/{ncx}text")
            content = pt.find(f"{ncx}content")
            if content is not None and content.get("src"):
                title = (label.text or "") if label is not None else ""
                out.append((depth, re.sub(r"\s+", " ", title).strip(),
                            content.get("src")))
            walk(pt, depth + 1)

    if (nav_map := root.find(f"{ncx}navMap")) is not None:
        walk(nav_map, 1)
    return out


FONT_EXT = (".ttf", ".otf", ".woff", ".woff2", ".eot")


def encryption_scope(z):
    """Classify META-INF/encryption.xml as font obfuscation or real encryption.

    Publishers obfuscate embedded fonts using the very same encryption.xml
    mechanism as DRM, so the file's presence proves nothing on its own — it is
    extremely common in books that read perfectly well. What matters is *what*
    is listed: if every encrypted resource is a font, the text is plain and the
    book parses normally. Treating the file itself as a DRM signal rejects a
    large slice of an ordinary library for no reason.

    Returns (scope, non_font_uris) where scope is "none", "fonts" or "content".
    """
    try:
        raw = z.read("META-INF/encryption.xml")
    except KeyError:
        return "none", []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return "unknown", []

    uris = [el.get("URI", "") for el in root.iter()
            if el.tag.endswith("CipherReference") and el.get("URI")]
    if not uris:
        return "none", []
    non_font = [u for u in uris if not u.lower().split("?")[0].endswith(FONT_EXT)]
    return ("content" if non_font else "fonts"), non_font


def load_toc(z, base, items):
    """Return [(depth, title, file, anchor)] from the nav document or the NCX.

    EPUB 3 books carry a nav document flagged `properties="nav"`; EPUB 2 books
    carry an NCX. Either one is authored by the publisher and states the real
    chapter structure, which is why it beats inferring structure from the spine.
    """
    candidates = [("nav", i.get("href")) for i in items
                  if "nav" in (i.get("properties") or "").split()]
    candidates += [("ncx", i.get("href")) for i in items
                   if i.get("media-type") == "application/x-dtbncx+xml"]

    for kind, href in candidates:
        if not href or not (path := resolve(base, href)):
            continue
        try:
            raw = z.read(path).decode("utf-8", "replace")
        except KeyError:
            continue
        if kind == "nav":
            parser = NavParser()
            parser.feed(raw)
            entries = parser.entries
        else:
            entries = parse_ncx(raw)

        doc_dir = posixpath.dirname(path)
        resolved = []
        for depth, title, ref in entries:
            if file := resolve(doc_dir, ref):
                anchor = unquote(ref.split("#", 1)[1]) if "#" in ref else None
                resolved.append((depth, title, file, anchor))
        if resolved:
            return resolved
    return []


def split_by_toc(z, spine, toc):
    """Slice spine documents at the anchor positions the ToC points to.

    This handles the two shapes the spine alone gets wrong: several chapters
    packed into one XHTML file (split at their anchors) and one chapter spread
    across several files (a file no ToC entry points into is a continuation of
    the chapter before it, so it gets appended rather than starting a new one).
    """
    by_file = {}
    for depth, title, file, anchor in toc:
        by_file.setdefault(file, []).append((depth, title, anchor))

    chapters = []
    for path in spine:
        try:
            raw = z.read(path).decode("utf-8", "replace")
        except KeyError:
            continue
        parser = TextExtractor()
        parser.feed(raw)
        body, anchors = parser.raw(), parser.anchors

        entries = by_file.get(path, [])
        if not entries:
            if chapters and body.strip():
                chapters[-1][1].append(body)
            elif body.strip():
                chapters.append([None, [body]])
            continue

        # A part-level ToC entry usually links to the same anchor as its first
        # chapter. Those aren't two boundaries, they're one — so collapse
        # entries that resolve to the same offset and keep the deepest, which is
        # the specific chapter title rather than the navigational parent.
        best = {}
        for depth, title, anchor in entries:
            off = anchors.get(anchor, 0) if anchor else 0
            if off not in best or depth > best[off][0]:
                best[off] = (depth, title)
        points = sorted((off, title) for off, (_, title) in best.items())
        # Text before the first anchor still belongs to the preceding chapter.
        if points[0][0] > 0 and chapters and body[: points[0][0]].strip():
            chapters[-1][1].append(body[: points[0][0]])

        for i, (off, title) in enumerate(points):
            end = points[i + 1][0] if i + 1 < len(points) else len(body)
            chapters.append([title, [body[off:end]]])

    return [(t, normalize("\n\n".join(parts))) for t, parts in chapters]


def segmentation_failed(chapters, dominance=0.35, min_for_ratio=5):
    """True when one chapter holds so much of the book that no real split happened.

    Counting ToC entries is a poor test — a stub nav with a single "Start" link
    parses perfectly well. What matters is whether the entries actually divided
    the text, which this measures directly and independently of entry count.

    The share test only means something once there are enough chapters for a
    dominant one to be surprising: in a three-chapter book the longest chapter
    is *expected* to hold a third or more. Below that threshold, only a single
    chapter swallowing the whole book counts as a failure.
    """
    words = [len(b.split()) for _, b in chapters]
    total = sum(words)
    if not words or total == 0:
        return True
    if len(words) < min_for_ratio:
        return len(words) == 1
    return max(words) / total > dominance


def read_epub_by_spine(z, spine):
    """Fallback when a book has no usable ToC: one chapter per spine document."""
    chapters = []
    for path in spine:
        try:
            raw = z.read(path).decode("utf-8", "replace")
        except KeyError:
            continue
        text = html_to_text(raw)
        if len(text.split()) < 20:  # nav docs, title pages, blank sections
            continue
        chapters.append((guess_spine_title(raw, text), text))
    return chapters


def guess_spine_title(raw, text):
    """Name a spine document, preferring sources that vary between chapters.

    `<title>` is checked last on purpose: converters routinely stamp the book's
    own title into every split file, which yields sixty chapters all sharing one
    name. A real heading element is best; failing that, converted files usually
    leave the chapter label as the first line of the body text.
    """
    if m := re.search(r"<h[1-6][^>]*>(.*?)</h[1-6]>", raw, re.S | re.I):
        if heading := re.sub(r"\s+", " ", html_to_text(m.group(1))).strip():
            return heading[:120]

    first, _, rest = text.partition("\n")
    first = first.strip()
    # A short opening line above further text is a heading in all but name.
    if rest.strip() and 0 < len(first) <= 60:
        return first[:120]

    if m := re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I):
        if t := html_to_text(m.group(1)).strip():
            return t[:120]
    return first[:120] or "Untitled section"


def read_epub(path):
    with zipfile.ZipFile(path) as z:
        scope, encrypted = encryption_scope(z)
        if scope == "content":
            sys.exit(f"{path.name} has encrypted content ({encrypted[0]}), so its "
                     f"text can't be read.\nThis looks like DRM rather than the "
                     f"font obfuscation that is normally harmless.")

        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//c:rootfile", NS).get("full-path")
        base = posixpath.dirname(opf_path)
        opf = ET.fromstring(z.read(opf_path))

        meta = {}
        for key in ("title", "creator", "language", "publisher", "date"):
            el = opf.find(f".//{DC}{key}")
            if el is not None and el.text:
                meta[key] = el.text.strip()

        items = opf.findall(".//opf:manifest/opf:item", NS)
        by_id = {i.get("id"): i for i in items}
        spine = []
        for ref in opf.findall(".//opf:spine/opf:itemref", NS):
            item = by_id.get(ref.get("idref"))
            if item is not None and item.get("href"):
                if p := resolve(base, item.get("href")):
                    spine.append(p)

        chapters = []
        if toc := load_toc(z, base, items):
            chapters = [(t, b) for t, b in split_by_toc(z, spine, toc)
                        if len(b.split()) >= 20]
            meta["_structure"] = f"toc ({len(toc)} entries)"

        # A stub ToC — one "Start" entry, common in converted files — parses
        # fine but segments nothing, leaving the whole book as a single chapter
        # that later gets chopped into arbitrary fixed-size parts. Judge the ToC
        # by whether it actually divided the text, not by whether it parsed.
        if chapters and segmentation_failed(chapters):
            spine_chapters = read_epub_by_spine(z, spine)
            if len(spine_chapters) > len(chapters):
                meta["_structure"] = (f"spine ({len(spine_chapters)} docs; "
                                      f"toc had {len(toc)} entries and did not segment)")
                chapters = spine_chapters

        if not chapters:
            chapters = read_epub_by_spine(z, spine)
            meta["_structure"] = "spine (no usable toc)"
        return meta, chapters


def read_via_calibre(path):
    if not shutil.which("ebook-convert"):
        sys.exit(
            f"{path.suffix} files need Calibre's `ebook-convert`, which is third-party\n"
            f"software and is not bundled with this skill.\n\n"
            f"  install:  brew install --cask calibre   (or calibre-ebook.com)\n"
            f"  then:     ebook-convert '{path}' '{path.with_suffix('.epub')}'\n\n"
            f"Converting to EPUB is worth doing anyway — EPUB carries a table of\n"
            f"contents, which gives far more reliable chapter splitting."
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "converted.epub"
        subprocess.run(["ebook-convert", str(path), str(out)],
                       check=True, capture_output=True)
        return read_epub(out)


def read_pdf(path):
    if shutil.which("pdftotext"):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.txt"
            subprocess.run(["pdftotext", "-layout", str(path), str(out)],
                           check=True, capture_output=True)
            text = out.read_text("utf-8", errors="replace")
    else:
        try:
            from pypdf import PdfReader
        except ImportError:
            sys.exit("PDF needs `pdftotext` (poppler-utils) or `pip install pypdf`.")
        text = "\n\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)
    return {"title": path.stem}, split_plain_text(normalize(text))


def read_book(path):
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return read_epub(path)
    if suffix in (".mobi", ".azw", ".azw3", ".fb2", ".lit", ".pdb"):
        return read_via_calibre(path)
    if suffix == ".pdf":
        return read_pdf(path)
    if suffix in (".html", ".htm", ".xhtml"):
        text = html_to_text(path.read_text("utf-8", errors="replace"))
    else:  # .txt, .md, anything else plain
        text = normalize(path.read_text("utf-8", errors="replace"))
    return {"title": path.stem}, split_plain_text(strip_gutenberg(text))


# --------------------------------------------------------------------- tokens

def est_tokens(text):
    """Offline estimate. Within ~15% for English prose on current tokenizers."""
    return round(len(text) / 3.6)


def exact_tokens(texts, model):
    """Exact counts via the count_tokens endpoint (free, but needs a key)."""
    try:
        from anthropic import Anthropic
    except ImportError:
        print("  (anthropic package not installed — using estimates)", file=sys.stderr)
        return None
    try:
        client = Anthropic()
        counts = []
        for text in texts:
            resp = client.messages.count_tokens(
                model=model, messages=[{"role": "user", "content": text}])
            counts.append(resp.input_tokens)
        return counts
    except Exception as exc:
        print(f"  (count_tokens unavailable: {exc} — using estimates)", file=sys.stderr)
        return None


# ------------------------------------------------------------ mode + batching

def detect_mode(chapters):
    """Guess fiction vs nonfiction so the right digest template gets used.

    Dialogue density is the strongest cheap signal: novels are full of quoted
    speech, argument-driven books are not. Returned with the evidence so the
    agent reading this can overrule it after seeing actual prose.
    """
    sample = "\n\n".join(body for _, body in chapters[: max(3, len(chapters) // 3)])
    paras = [p for p in sample.split("\n") if len(p.split()) > 3]
    if not paras:
        return "unknown", 0.0, []

    speech = re.compile(r'["“”‘’\']\s*[A-Z]|\b(said|asked|replied|whispered|shouted)\b')
    dialogue_ratio = sum(bool(speech.search(p)) for p in paras) / len(paras)

    titles = " ".join((t or "") for t, _ in chapters).lower()
    scholarly = [w for w in ("bibliography", "references", "endnotes", "appendix",
                             "index", "notes", "acknowledgments") if w in titles]

    if dialogue_ratio >= 0.12 and len(scholarly) < 3:
        mode = "fiction"
    elif dialogue_ratio < 0.05:
        mode = "nonfiction"
    else:
        mode = "fiction" if dialogue_ratio >= 0.08 else "nonfiction"
    return mode, round(dialogue_ratio, 3), scholarly


def pack_batches(chapters, budget):
    """Greedily pack chapters into batches under a token budget.

    Each batch becomes one indexing subagent, so this is what determines the
    fan-out width. Chapters stay in reading order within a batch — a digest is
    much better when the agent sees what immediately preceded.
    """
    batches, current, total = [], [], 0
    for ch in chapters:
        if current and total + ch["tokens"] > budget:
            batches.append(current)
            current, total = [], 0
        current.append(ch)
        total += ch["tokens"]
    if current:
        batches.append(current)
    return batches


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "book").lower()).strip("-")
    return slug[:60] or "book"


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("--library",
                    default=os.environ.get("EBOOK_LIBRARY",
                                           str(Path.home() / ".ebook-library")))
    ap.add_argument("--slug")
    ap.add_argument("--model", default="claude-opus-5")
    ap.add_argument("--exact", action="store_true",
                    help="use the count_tokens API for exact counts")
    ap.add_argument("--batch-tokens", type=int, default=30000,
                    help="token budget per indexing subagent")
    ap.add_argument("--max-chapter-tokens", type=int, default=12000,
                    help="split any chapter larger than this into parts")
    args = ap.parse_args()

    path = Path(args.book).expanduser()
    if not path.exists():
        sys.exit(f"No such file: {path}")

    meta, chapters = read_book(path)
    if not chapters:
        sys.exit("No readable text found. If this is a scanned PDF it needs OCR first.")

    chapters = [(t, strip_gutenberg(b)) for t, b in chapters]
    chapters = split_oversized(chapters, args.max_chapter_tokens)

    slug = args.slug or slugify(meta.get("title") or path.stem)
    root = Path(args.library).expanduser() / slug
    (root / "chapters").mkdir(parents=True, exist_ok=True)
    (root / "index").mkdir(exist_ok=True)
    (root / "reviews").mkdir(exist_ok=True)

    bodies = [b for _, b in chapters]
    counts = exact_tokens(bodies, args.model) if args.exact else None
    counted = "exact" if counts else "estimated"

    records = []
    for n, (title, body) in enumerate(chapters, start=1):
        name = f"{n:04d}.txt"
        (root / "chapters" / name).write_text(body, encoding="utf-8")
        records.append({
            "id": n,
            "file": f"chapters/{name}",
            "title": (title or f"Section {n}").strip(),
            "words": len(body.split()),
            "chars": len(body),
            "tokens": counts[n - 1] if counts else est_tokens(body),
        })

    batches = pack_batches(records, args.batch_tokens)
    for i, batch in enumerate(batches, start=1):
        for ch in batch:
            ch["batch"] = i

    mode, dialogue_ratio, scholarly = detect_mode(chapters)
    total = sum(c["tokens"] for c in records)

    manifest = {
        "slug": slug,
        "source": str(path.resolve()),
        "title": meta.get("title", path.stem),
        "author": meta.get("creator", "unknown"),
        "metadata": meta,
        "structure_source": meta.get("_structure", "n/a"),
        "mode": mode,
        "mode_evidence": {"dialogue_ratio": dialogue_ratio,
                          "scholarly_sections": scholarly},
        "token_count_method": counted,
        "total_tokens": total,
        "total_words": sum(c["words"] for c in records),
        "chapters": records,
        "batches": [{"batch": i,
                     "chapters": [c["id"] for c in b],
                     "tokens": sum(c["tokens"] for c in b)}
                    for i, b in enumerate(batches, start=1)],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print(f"{manifest['title']} — {manifest['author']}")
    print(f"  library:  {root}")
    print(f"  mode:     {mode} (dialogue ratio {dialogue_ratio}"
          f"{', scholarly: ' + ','.join(scholarly) if scholarly else ''})")
    print(f"  structure: {manifest['structure_source']}")
    print(f"  chapters: {len(records)}   words: {manifest['total_words']:,}")
    print(f"  tokens:   {total:,} ({counted})")
    print(f"  batches:  {len(batches)} indexing subagents "
          f"(~{total // max(1, len(batches)):,} tokens each)")
    print(f"\nRead-once cost at $5/MTok: ${total * 5 / 1e6:,.2f}")


if __name__ == "__main__":
    main()
