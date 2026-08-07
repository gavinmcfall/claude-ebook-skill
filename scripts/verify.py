#!/usr/bin/env python3
"""Check an index for coverage gaps and unverifiable quotes.

Two failure modes matter and both are cheap to catch mechanically:

  1. A chapter got skipped during indexing, so the book-level summary is
     silently missing a chunk of the story.
  2. A digest quotes a line that isn't actually in the book. Paraphrase drifting
     into quotation marks is the single most damaging error here, because a
     review built on it states something false in the author's voice.

Exact string matching is far more reliable than asking a model to re-check its
own work, so this runs as a gate between indexing and anything user-facing.

Usage:
    python verify.py LIBRARY/SLUG [--json] [--quiet]
"""
import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

# Quotes are extracted from markdown blockquote lines, which is what the digest
# template asks for. Anything else in the digest is treated as paraphrase.
QUOTE_LINE = re.compile(r"^\s*>\s*(.+?)\s*$")
# Anchored, with a bounded tail so a trailing "— ch 12" is stripped but an em-dash
# inside the quoted prose is not treated as the start of an attribution.
ATTRIBUTION = re.compile(
    r"\s*[—–-]{1,2}\s*(?:ch(?:apter)?|loc|p{1,2})\.?\s*\d+[^—–]{0,24}$", re.I)


def fold(text):
    """Normalize away differences that don't change what a reader sees.

    Smart quotes, dashes, ligatures, casing and line wrapping all vary between
    a digest and the source without the quote being wrong, so they're folded out
    before comparison. Anything left that doesn't match is a real discrepancy.
    """
    text = unicodedata.normalize("NFKD", text)
    for a, b in [("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"),
                 ("—", "-"), ("–", "-"), ("…", "...")]:
        text = text.replace(a, b)
    text = re.sub(r"[^\w\s]", "", text)      # punctuation varies harmlessly
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def extract_quotes(digest_text):
    """Every blockquote line is checked independently.

    Digests routinely list several distinct quotes on consecutive lines, so
    merging adjacent lines into one quote would splice unrelated passages
    together — and, worse, could let later quotes escape checking entirely.
    Checking line by line is also safe for a single quote wrapped across lines:
    whitespace is folded before comparison, so each line is still a contiguous
    run of the source text and has to match on its own.
    """
    cleaned = []
    for line in digest_text.split("\n"):
        if not (m := QUOTE_LINE.match(line)):
            continue
        q = ATTRIBUTION.sub("", m.group(1)).strip()
        q = q.strip('"“”‘’\'').strip()
        if len(q.split()) >= 3:   # shorter fragments match by chance; not worth flagging
            cleaned.append(q)
    return cleaned


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    args = ap.parse_args()

    root = Path(args.book_dir).expanduser()
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"No manifest.json in {root} — run ingest.py first.")
    manifest = json.loads(manifest_path.read_text())

    # Fold the whole book once; quotes are checked against all of it rather than
    # just their own chapter, since a digest may legitimately quote a callback.
    full = fold("\n".join((root / c["file"]).read_text(encoding="utf-8")
                          for c in manifest["chapters"]))

    missing, checked, bad = [], 0, []
    for ch in manifest["chapters"]:
        digest = root / "index" / f"{ch['id']:04d}.md"
        if not digest.exists() or len(digest.read_text().strip()) < 80:
            missing.append(ch)
            continue
        for quote in extract_quotes(digest.read_text(encoding="utf-8")):
            checked += 1
            if fold(quote) not in full:
                bad.append({"chapter": ch["id"], "title": ch["title"], "quote": quote})

    book_md = root / "index" / "book.md"
    result = {
        "slug": manifest["slug"],
        "chapters_total": len(manifest["chapters"]),
        "chapters_missing_digest": [{"id": c["id"], "title": c["title"]} for c in missing],
        "book_summary_present": book_md.exists() and len(book_md.read_text().strip()) > 200,
        "quotes_checked": checked,
        "quotes_unverified": bad,
        "ok": not missing and not bad and book_md.exists(),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1

    if missing:
        print(f"MISSING DIGESTS ({len(missing)}):")
        for c in missing:
            print(f"  ch {c['id']:>3}  {c['title'][:60]}")
    if bad:
        print(f"\nUNVERIFIED QUOTES ({len(bad)}) — not found in the book text:")
        for b in bad:
            print(f"  ch {b['chapter']:>3}  {b['quote'][:100]}")
        print("\n  Fix by re-reading the chapter and correcting or dropping the quote.")
        print("  Do not carry an unverified quote into a review.")
    if not result["book_summary_present"]:
        print("\nMISSING: index/book.md (the whole-book synthesis)")

    if result["ok"] and not args.quiet:
        print(f"OK — {result['chapters_total']} chapters indexed, "
              f"{checked} quotes verified against the source, book.md present.")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
