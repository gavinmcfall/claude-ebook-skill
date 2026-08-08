# claude-ebook-skill

A [Claude Code](https://claude.com/claude-code) skill that reads a book **once**,
converts it into a compact structured index, and then answers questions, writes
summaries, and sanity-checks your own reviews against the text.

Its main job is the last one: you write a review, Claude writes one independently,
and it reports what you missed — memorable moments and lines, craft observations,
factual slips, and whether your coverage thins out toward the end.

## Why

A novel is 150k–250k tokens. Reading it into context to answer one question is
slow and expensive; doing it again for the next question is worse.

So this indexes the book once, in parallel, into two tiers: per-chapter digests
(~3× smaller than the book) and a whole-book synthesis (~100× smaller). Most
questions are answered from the synthesis alone, dropping to digests or raw
chapter text only when the question needs that resolution.

Measured on a 198k-token novel:

| Phase | Cost |
|---|---|
| Ingest | 0 tokens — pure Python |
| Index | ~200k, spread across parallel subagents (one-time) |
| Typical question | ~2–6k |
| Writing a review | ~64k |

Break-even against re-reading the book is the *second* question.

## Install

```bash
git clone https://github.com/gavinmcfall/claude-ebook-skill ~/.claude/skills/ebook
```

That's it — `SKILL.md` sits at the repo root, so cloning into a skills directory
installs it. For a single project instead, clone into `.claude/skills/ebook/`
inside that project.

Python 3.8+, no dependencies. EPUB, TXT, MD and HTML work out of the box; MOBI
and AZW3 need [Calibre](https://calibre-ebook.com)'s `ebook-convert`, and PDF
needs `pdftotext` or `pip install pypdf`.

## Use

Just talk to Claude:

- *"index ~/Books/dune.epub"*
- *"what does the book say about the Bene Gesserit?"*
- *"write a review of it"*
- *"here's my review — what did I miss?"*

Or drive the scripts directly:

```bash
python3 scripts/ingest.py book.epub          # book -> chapters + batch plan
python3 scripts/verify.py ~/.ebook-library/dune   # coverage + quote checking
python3 scripts/scan_library.py ~/Books --suggest 8
```

## How it works

1. **Ingest** (no model tokens) — unzip, strip markup, split into chapters, count
   tokens, and pack chapters into balanced batches sized for one subagent each.
2. **Index** — one subagent per batch, all spawned concurrently. Each reads prose
   and writes digests to disk, returning only a confirmation line. The
   orchestrator never pulls chapter text into its own context; that single rule
   is what makes the architecture worth having.
3. **Verify** — a mechanical gate. Every chapter must have a digest, and every
   quoted line must exist verbatim in the source.
4. **Use** — read the synthesis, pull individual digests when needed, grep raw
   chapter text for exact wording.

### Quote verification

Digest quotes are checked character-for-character against the book, with
whitespace, smart quotes and dashes normalised so re-wrapping doesn't cause false
failures. A paraphrase that drifted into quotation marks gets caught here.

This matters because the whole point is checking a review: a review built on a
quote the author never wrote is worse than no review at all.

## Chapter structure

Chapter splitting comes from the best source the file offers, recorded in
`manifest.json` as `structure_source`:

| Source | Meaning |
|---|---|
| `toc (N entries)` | EPUB 3 nav document or EPUB 2 NCX — publisher-authored |
| `spine (…)` | ToC missing or not segmenting; fell back to spine documents |
| `n/a` | Plain text or PDF — heading detection |

The ToC is preferred because it's written by a human and points at exact anchors.
The spine is only a packaging detail and gets two common layouts wrong: several
chapters inside one XHTML file, and one chapter spread across several files.

But a ToC is trusted only if it actually *segments* the book. Converted files
often carry a stub nav with a single "Start" entry, which parses perfectly and
divides nothing; that's detected by measuring whether any single chapter swallows
the book, and falls back to the spine.

### Surveying a library

```bash
python3 scripts/scan_library.py ~/Books --suggest 8
```

Walks a folder tree and classifies every book — `toc-ok`, `toc-stub`, `no-toc`,
`drm`, `needs-calibre`, `error` — using the same rule `ingest.py` applies, so it
predicts real behaviour. Reads metadata and stored zip sizes without
decompressing, so it's fast on large collections. `--suggest N` returns a
structurally diverse sample, which is what you want for testing.

## Status

Working and tested end to end, but young.

Verified: EPUB 3 nav and EPUB 2 NCX parsing, anchor-split chapters, chapters
spanning multiple files, stub-ToC fallback, plain-text heading detection, and a
full index of a 110k-word novel where all 252 extracted quotes verified against
the source.

Scanning a real 778-EPUB library classifies 710 as `toc-ok` and 65 as needing the
spine fallback, with no parse errors. That library also shook out four bugs, all
fixed: `encryption.xml` was read as DRM when it usually just marks obfuscated
fonts (35 books wrongly refused); ToC hrefs weren't percent-decoded, so a book
with `!` in its filenames silently lost its entire ToC (9 books); narrative
nonfiction was misread as fiction because it quotes sources constantly; and index
files used the platform default encoding, which breaks on Windows.

Not yet exercised: the MOBI and PDF paths, the nonfiction digest template against
real nonfiction (detection works — the template itself hasn't been run), and
`--exact` token counting via the
[count_tokens API](https://platform.claude.com/docs/en/build-with-claude/token-counting).

Book text and indexes live in `~/.ebook-library` by default — outside the repo,
deliberately. Override with `EBOOK_LIBRARY`.

## License

MIT
