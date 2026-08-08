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
# inside the quoted prose is not treated as the start of an attribution. Named
# sections matter as much as numbered ones: a prologue is cited as "ch prologue",
# and demanding a digit there leaves the label glued to the quote, which then
# fails against a source that of course never contained it.
ATTRIBUTION = re.compile(
    r"\s*[—–-]{1,2}\s*(?:ch(?:apter)?|loc|pp?)\.?\s*"
    r"(?:\d+|prologue|epilogue|interlude|intro(?:duction)?|preface|foreword)"
    r"[^—–]{0,24}$", re.I)


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


def quote_present(quote, folded_source):
    """Check a quote against the book, allowing an ellipsis to mark elided text.

    The digest template permits cutting the middle out of a long passage, so a
    quote can be several fragments rather than one span. Treating it as a single
    contiguous string makes every legitimately elided quote fail.

    Each fragment must appear, and they must appear *in order* — otherwise a
    quote stitched together from unrelated parts of the book would pass, which
    is a worse failure than the one being fixed. Splitting happens before
    folding, since folding strips the punctuation the split relies on.
    """
    parts = [fold(p) for p in re.split(r"\.{3}|…", quote)]
    parts = [p for p in parts if len(p.split()) >= 2]
    if not parts:
        return True

    pos = 0
    for part in parts:
        found = folded_source.find(part, pos)
        if found < 0:
            return False
        pos = found + len(part)
    return True


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


def check_loose_quotes(quotes, full, labels=None):
    """Check quotes that came from somewhere other than the index.

    A finished review is the highest-stakes place a quote can appear, and it is
    the one place the index's own gate never sees. This makes the same check
    available to anything: a draft review, a blurb, a social post.
    """
    labels = labels or [""] * len(quotes)
    bad = []
    for quote, label in zip(quotes, labels):
        if not quote_present(quote, full):
            bad.append({"quote": quote, "where": label})
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book_dir")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    ap.add_argument("--quote", action="append", metavar="TEXT",
                    help="check one quote against the book; repeatable")
    ap.add_argument("--quotes-from", metavar="FILE",
                    help="check every markdown blockquote line in FILE "
                         "(use on a draft review before it goes out)")
    ap.add_argument("--inline", action="store_true",
                    help="with --quotes-from, also check quoted spans inside "
                         "prose, which is how reviews actually quote")
    args = ap.parse_args()

    root = Path(args.book_dir).expanduser()
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        sys.exit(f"No manifest.json in {root} — run ingest.py first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Fold the whole book once; quotes are checked against all of it rather than
    # just their own chapter, since a digest may legitimately quote a callback.
    full = fold("\n".join((root / c["file"]).read_text(encoding="utf-8")
                          for c in manifest["chapters"]))

    # Ad-hoc checking short-circuits the index audit: the caller is asking about
    # specific text, not about whether the index is complete.
    if args.quote or args.quotes_from:
        quotes, labels = list(args.quote or []), ["--quote"] * len(args.quote or [])
        if args.quotes_from:
            src = Path(args.quotes_from).expanduser()
            text = src.read_text(encoding="utf-8")
            for n, line in enumerate(text.split("\n"), 1):
                if m := QUOTE_LINE.match(line):
                    q = ATTRIBUTION.sub("", m.group(1)).strip().strip('"“”‘’\'').strip()
                    if len(q.split()) >= 3:
                        quotes.append(q)
                        labels.append(f"{src.name}:{n}")

            if args.inline:
                # Reviews quote inside sentences, not as blockquotes, so the
                # blockquote-only pass finds nothing in a real review. The
                # five-word floor is what separates a quotation from a scare
                # quote: "damaged" is the reviewer's word, a whole clause is the
                # author's. Below that the false-positive rate makes the check
                # worse than useless.
                for n, line in enumerate(text.split("\n"), 1):
                    if QUOTE_LINE.match(line):
                        continue  # already taken by the blockquote pass
                    for span in re.findall(r'["“]([^"“”]{12,400})["”]', line):
                        span = span.strip().strip("*_")
                        if len(span.split()) >= 5:
                            quotes.append(span)
                            labels.append(f"{src.name}:{n} (inline)")

        bad = check_loose_quotes(quotes, full, labels)
        # Inline hits are reported separately and never fail the run. Quotation
        # marks do many jobs besides quotation - scare quotes, a paraphrased
        # trope, a cited heading - and no heuristic tells them apart. Treating
        # those as errors would train the reader to ignore the output, which
        # costs more than the misses it catches.
        errors = [b for b in bad if "(inline)" not in b["where"]]
        candidates = [b for b in bad if "(inline)" in b["where"]]

        if args.json:
            print(json.dumps({"checked": len(quotes), "unverified": errors,
                              "inline_candidates": candidates}, indent=2))
        else:
            print(f"{len(quotes)} quotes checked.")
            if errors:
                print(f"\n{len(errors)} NOT FOUND in the book:")
                for b in errors:
                    print(f"  {b['where']}: {b['quote'][:96]}")
                print("\n  A quoted line that cannot be matched is either "
                      "misremembered or altered.\n  Do not publish it without "
                      "checking the text.")
            if candidates:
                print(f"\n{len(candidates)} inline span(s) to eyeball - these are "
                      f"often not quotations at all:")
                for b in candidates:
                    print(f"  {b['where']}: {b['quote'][:96]}")
            if not errors and not candidates:
                print("All found in the source text.")
        return 1 if errors else 0

    missing, checked, bad = [], 0, []
    for ch in manifest["chapters"]:
        digest = root / "index" / f"{ch['id']:04d}.md"
        if not digest.exists() or len(digest.read_text(encoding="utf-8").strip()) < 80:
            missing.append(ch)
            continue
        for quote in extract_quotes(digest.read_text(encoding="utf-8")):
            checked += 1
            if not quote_present(quote, full):
                bad.append({"chapter": ch["id"], "title": ch["title"], "quote": quote})

    book_md = root / "index" / "book.md"
    result = {
        "slug": manifest["slug"],
        "chapters_total": len(manifest["chapters"]),
        "chapters_missing_digest": [{"id": c["id"], "title": c["title"]} for c in missing],
        "book_summary_present": (book_md.exists()
                                 and len(book_md.read_text(encoding="utf-8").strip()) > 200),
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
