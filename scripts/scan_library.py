#!/usr/bin/env python3
"""Survey an ebook library and classify every book by how it will parse.

Walks a folder tree and reports, per book, which table of contents it carries
and whether that ToC would actually segment the book into chapters — which is
the thing that silently goes wrong. Reuses ingest.py's own parsing so the
verdicts match what ingest would really do rather than being a second opinion.

Fast by default: reads the package metadata and the ToC, and estimates chapter
sizes from the zip's stored file sizes without decompressing anything. Pass
--deep to extract text instead, which is slower but exact.

Usage:
    python scan_library.py ~/Books                 # table + summary
    python scan_library.py ~/Books --suggest 8     # a diverse test sample
    python scan_library.py ~/Books --csv out.csv   # full results
    python scan_library.py ~/Books --only toc-stub # filter to one verdict
"""
import argparse
import csv
import json
import posixpath
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest import DC, NS, load_toc, resolve  # noqa: E402

NATIVE = {".epub"}
PLAINTEXT = {".txt", ".md", ".html", ".htm", ".xhtml"}
NEEDS_CALIBRE = {".mobi", ".azw", ".azw3", ".fb2", ".lit", ".pdb"}
NEEDS_PDF_TOOL = {".pdf"}
ALL_EXT = NATIVE | PLAINTEXT | NEEDS_CALIBRE | NEEDS_PDF_TOOL

# Verdicts, ordered by how much attention they deserve.
VERDICTS = ["error", "drm", "no-toc", "toc-stub", "single-doc", "toc-ok",
            "needs-calibre", "needs-pdf-tool", "plaintext"]


def dominance(spine, toc, sizes):
    """Largest chapter's share of the book, approximated from stored file sizes.

    Mirrors how ingest assigns text to chapters: a spine document no ToC entry
    points into continues the previous chapter, and a document carrying several
    entries is divided between them. A value near 1.0 means the ToC produced one
    giant chapter and is not really segmenting the book.
    """
    by_file = defaultdict(list)
    for _, _, file, anchor in toc:
        by_file[file].append(anchor)

    chunks = []
    for path in spine:
        size = sizes.get(path, 0)
        entries = by_file.get(path, [])
        if not entries:
            if chunks:
                chunks[-1] += size
            else:
                chunks.append(size)
            continue
        chunks.extend([size / len(entries)] * len(entries))

    total = sum(chunks)
    return (max(chunks) / total) if total else 1.0


def deep_dominance(z, spine, toc):
    """Exact version of the above, by actually extracting the text."""
    from ingest import split_by_toc
    chapters = [(t, b) for t, b in split_by_toc(z, spine, toc)
                if len(b.split()) >= 20]
    if not chapters:
        return 1.0, 0
    words = [len(b.split()) for _, b in chapters]
    return max(words) / sum(words), len(chapters)


def survey_epub(path, deep=False):
    info = {"format": "epub"}
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "META-INF/encryption.xml" in names:
            # Font obfuscation uses this too, so it's a hint rather than proof.
            info["verdict"] = "drm"
            info["note"] = "encryption.xml present — may be DRM or obfuscated fonts"
            return info

        sizes = {i.filename: i.file_size for i in z.infolist()}
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//c:rootfile", NS).get("full-path")
        base = posixpath.dirname(opf_path)
        opf = ET.fromstring(z.read(opf_path))

        for key, field in (("title", "title"), ("creator", "author")):
            el = opf.find(f".//{DC}{key}")
            if el is not None and el.text:
                info[field] = el.text.strip()

        items = opf.findall(".//opf:manifest/opf:item", NS)
        by_id = {i.get("id"): i for i in items}
        spine = []
        for ref in opf.findall(".//opf:spine/opf:itemref", NS):
            item = by_id.get(ref.get("idref"))
            if item is not None and item.get("href"):
                if p := resolve(base, item.get("href")):
                    spine.append(p)

        has_nav = any("nav" in (i.get("properties") or "").split() for i in items)
        has_ncx = any(i.get("media-type") == "application/x-dtbncx+xml" for i in items)
        info["toc_type"] = ({(True, True): "nav+ncx", (True, False): "nav",
                             (False, True): "ncx"}.get((has_nav, has_ncx), "none"))

        toc = load_toc(z, base, items)
        info["toc_entries"] = len(toc)
        info["spine_docs"] = len(spine)
        info["bytes"] = sum(sizes.get(p, 0) for p in spine)

        if not toc:
            info["verdict"] = "no-toc"
            info["dominance"] = 1.0
            info["chapters"] = len(spine)
        else:
            if deep:
                dom, nchap = deep_dominance(z, spine, toc)
                info["chapters"] = nchap
            else:
                dom, info["chapters"] = dominance(spine, toc, sizes), len(toc)
            info["dominance"] = round(dom, 3)
            # Same rule ingest.segmentation_failed applies, so the verdict here
            # predicts what ingest will actually do rather than second-guessing it.
            if info["chapters"] < 5:
                info["verdict"] = "toc-stub" if info["chapters"] <= 1 else "toc-ok"
            else:
                info["verdict"] = "toc-stub" if dom > 0.35 else "toc-ok"

        if len(spine) <= 1 and info["verdict"] != "toc-ok":
            info["verdict"] = "single-doc"

        # Structural features worth having represented in a test set.
        referenced = {f for _, _, f, _ in toc}
        per_file = Counter(f for _, _, f, _ in toc)
        flags = []
        if any(d > 1 for d, _, _, _ in toc):
            flags.append("nested")
        if any(a for _, _, _, a in toc):
            flags.append("anchors")
        if any(c > 1 for c in per_file.values()):
            flags.append("shared-file")
        if toc and [s for s in spine if s not in referenced]:
            flags.append("multi-file-ch")
        info["flags"] = ",".join(flags)
    return info


def survey(path, deep=False):
    suffix = path.suffix.lower()
    base = {"path": str(path), "name": path.name, "size_mb": round(path.stat().st_size / 1e6, 2),
            "title": "", "author": "", "toc_type": "", "toc_entries": "",
            "spine_docs": "", "chapters": "", "dominance": "", "flags": "", "note": ""}
    try:
        if suffix in NATIVE:
            base.update(survey_epub(path, deep))
        elif suffix in NEEDS_CALIBRE:
            base.update(format=suffix.lstrip("."), verdict="needs-calibre",
                        note="convert with Calibre's ebook-convert")
        elif suffix in NEEDS_PDF_TOOL:
            base.update(format="pdf", verdict="needs-pdf-tool",
                        note="needs pdftotext or pypdf; no ToC either way")
        else:
            base.update(format=suffix.lstrip("."), verdict="plaintext",
                        note="heading detection only")
    except Exception as exc:
        base.update(verdict="error", note=f"{type(exc).__name__}: {exc}"[:90])
    return base


def suggest(rows, n):
    """Pick a diverse sample: one book per distinct verdict+feature combination.

    Test value comes from structural variety, not volume — twenty books that all
    parse the same way exercise one code path.
    """
    buckets = defaultdict(list)
    for r in rows:
        buckets[(r["verdict"], r["flags"])].append(r)
    # Rarest combinations first: those are the ones nothing else covers.
    order = sorted(buckets.values(), key=len)
    picked, i = [], 0
    while len(picked) < n and any(len(b) > i for b in order):
        for bucket in order:
            if len(bucket) > i and len(picked) < n:
                picked.append(bucket[i])
        i += 1
    return picked


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--deep", action="store_true",
                    help="extract text for an exact segmentation check (slower)")
    ap.add_argument("--suggest", type=int, metavar="N",
                    help="print N structurally diverse books worth testing")
    ap.add_argument("--only", help="filter to one verdict, e.g. toc-stub")
    ap.add_argument("--csv", metavar="FILE")
    ap.add_argument("--json", metavar="FILE")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.exists():
        sys.exit(f"No such folder: {root}")

    files = sorted(p for p in root.rglob("*")
                   if p.is_file() and p.suffix.lower() in ALL_EXT)
    if not files:
        sys.exit(f"No ebooks found under {root}")

    print(f"Scanning {len(files)} files under {root}"
          f"{' (deep)' if args.deep else ''}...\n", file=sys.stderr)
    rows = []
    for n, path in enumerate(files, 1):
        rows.append(survey(path, args.deep))
        if n % 25 == 0:
            print(f"  {n}/{len(files)}", file=sys.stderr)

    counts = Counter(r["verdict"] for r in rows)
    print("SUMMARY")
    for verdict in VERDICTS:
        if counts.get(verdict):
            print(f"  {verdict:<16} {counts[verdict]:>4}")
    for verdict, c in counts.items():
        if verdict not in VERDICTS:
            print(f"  {verdict:<16} {c:>4}")

    shown = [r for r in rows if r["verdict"] not in ("toc-ok", "plaintext")]
    if args.only:
        shown = [r for r in rows if r["verdict"] == args.only]
    if shown:
        print(f"\nNEEDS ATTENTION ({len(shown)}) — "
              f"these are the interesting ones to test against:")
        print(f"  {'verdict':<15} {'toc':<8} {'ent':>4} {'docs':>5} {'dom':>5}  name")
        for r in sorted(shown, key=lambda r: VERDICTS.index(r["verdict"])
                        if r["verdict"] in VERDICTS else 99)[:60]:
            print(f"  {r['verdict']:<15} {str(r['toc_type']):<8} "
                  f"{str(r['toc_entries']):>4} {str(r['spine_docs']):>5} "
                  f"{str(r['dominance']):>5}  {r['name'][:44]}")
            if r["note"]:
                print(f"  {'':<15} └─ {r['note']}")

    if args.suggest:
        print(f"\nSUGGESTED TEST SET ({args.suggest} structurally distinct books):")
        for r in suggest(rows, args.suggest):
            feat = f" [{r['flags']}]" if r["flags"] else ""
            print(f"  {r['verdict']:<15}{feat}")
            print(f"    {r['path']}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {args.csv}")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")


if __name__ == "__main__":
    main()
