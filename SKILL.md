---
name: ebook
description: >
  Ingest an ebook (EPUB, MOBI, PDF, TXT) into a compact searchable index using
  parallel subagents, then answer questions about it, build summaries, and write
  a review that sanity-checks against the user's own handwritten review to catch
  memorable moments, craft observations, factual slips, and coverage gaps they
  missed. Use this skill whenever the user points at a book file, mentions
  indexing or ingesting a book, asks what happens in a book they've loaded, asks
  for a summary or review of a book, or wants their own review checked against
  the text — even if they don't name this skill or say the word "index". Also
  use it for follow-up questions about any book already in the library.
---

# Ebook indexing, querying, and review sanity-checking

## The idea

A novel is 150k–250k tokens. Reading it into context to answer one question is
slow and expensive, and doing that again for the next question is worse.

So this skill pays the reading cost **once**, in parallel, and converts the book
into a two-tier index: a per-chapter digest layer roughly 3× smaller than the
book, and a whole-book synthesis about 100× smaller. Most questions are answered
from the synthesis alone, dropping to digests or raw chapter text only when the
question actually needs that resolution. The book stays on disk; it never has to
sit in context again.

Measured on a 198k-token novel (61 chapters) — treat as an order of magnitude:

| Phase | Cost | Notes |
|---|---|---|
| Ingest | 0 tokens | Pure Python — unzip, clean, split, count |
| Index | ~200k, spread across N parallel subagents | One-time; ~1k per chapter digest |
| Whole-book synthesis | ~64k in, ~2k out | One-time |
| Typical question | ~2–6k | `book.md` plus a couple of digests |
| Writing a review | ~64k | Reads the full digest layer |

The break-even against re-reading the book is the *second* question, and most
questions never touch the digest layer at all.

## The rule that makes it efficient

**Never read chapter text into your own context as the orchestrator.** Subagents
read prose and write digests to disk; they return a one-line confirmation, not
their output. If digests come back through the conversation, the whole book ends
up in your context anyway and the architecture has bought nothing.

The same applies when using the index later: read `index/book.md` first and pull
individual chapter digests only when the question needs them.

## Workflow

### 1. Ingest (no model tokens)

```bash
python scripts/ingest.py BOOK_FILE [--library DIR] [--exact]
```

**Format support.** EPUB, TXT, MD and HTML need nothing beyond the Python
standard library. Two formats depend on third-party tools that are *not* bundled
and are often absent:

| Format | Requires | Install |
|---|---|---|
| MOBI, AZW3, FB2, LIT | Calibre's `ebook-convert` | `brew install --cask calibre`, or calibre-ebook.com |
| PDF | `pdftotext`, or pypdf | `apt install poppler-utils` / `pip install pypdf` |

`ingest.py` exits with the specific install hint if one is missing, so a failure
here is a missing dependency, not a bad book file.

Prefer EPUB whenever there's a choice — it's the only format carrying a real
table of contents, which is what makes chapter splitting reliable (see below).
Converting MOBI to EPUB with Calibre once, up front, is usually better than
ingesting the MOBI directly. PDF is the weakest path: no ToC, and layout-driven
extraction that mangles footnotes, headers and multi-column pages.

Writes to `$EBOOK_LIBRARY/<slug>/` (default `~/.ebook-library`). Produces cleaned
per-chapter text, a `manifest.json` with token counts, a fiction/nonfiction guess,
and a **batch plan** — chapters packed into groups sized for one subagent each.

Token counts are offline estimates (±15%) unless you pass `--exact`, which uses
Anthropic's free `count_tokens` endpoint and needs `ANTHROPIC_API_KEY`. Estimates
are fine for batching; use `--exact` when the user asks what a book actually costs.

Chapter structure comes from the best source the file offers, and `manifest.json`
records which one was used in `structure_source`:

| Source | Used for | Reliability |
|---|---|---|
| `toc (N entries)` | EPUB 3 nav document or EPUB 2 NCX | Publisher-authored — trust it |
| `spine (no usable toc)` | EPUB with a missing or empty ToC | Titles are guesses from `<title>` tags |
| `n/a` | Plain text, PDF | Heading detection; verify before relying on titles |

The ToC is authoritative because it's written by a human and points at exact
anchors, which the spine can't do. Spine order is a *packaging* detail and gets
two common layouts wrong: several chapters inside one XHTML file (the ToC splits
them at `#fragment` anchors, the spine sees one blob) and one chapter spread
across several files (the spine emits several fake chapters, the ToC merges them).

When `structure_source` isn't `toc`, glance at the chapter titles in the manifest
before indexing. If they look wrong, say so rather than presenting a clean
structure that isn't there — and consider converting to EPUB first, since
`ebook-convert` will usually synthesise a real ToC.

Check `mode` in the manifest and sanity-check it against the opening pages. A
bibliography settles it outright, since novels don't cite sources; otherwise the
detector keys on dialogue density, which can misread memoir or heavily-quoted
interviews. It is a starting guess, not a verdict — override it if the prose says
otherwise.

## Reading in progress

Ask whether the reader has finished the book before surfacing anything. If they
are partway through, that changes every downstream step: `book.md` is a
whole-book synthesis and is the single most spoiling artifact here, so don't
generate or quote it for someone mid-read.

Each digest carries a `Spoiler level`, and digests are numbered in reading order,
so answering up to a point is straightforward — read only digests at or before
where they are. Recaps, "who is X again", and craft observations all work fine
mid-read. A full review and gap report do not: they assume a finished book.

### 2. Index (parallel subagents)

Read `manifest.json` for the batch plan, then spawn **one subagent per batch, all
in a single message** so they run concurrently. Give each subagent:

- the book directory and the exact chapter files in its batch
- the path to the right template: `references/digest-templates.md`
- the instruction to write `index/NNNN.md` per chapter and return only a
  confirmation line

A workable subagent prompt:

> Read `references/digest-templates.md` and follow the **fiction** template.
> For each of these chapters in `<book_dir>` — `chapters/0012.txt` … `chapters/0018.txt` —
> write a digest to `index/0012.md` … `index/0018.md`.
> Read the chapters in order; earlier ones give you context for later ones.
> Quote only text you can see in the file, copied character-for-character.
> Return one line: how many digests you wrote, and nothing about what happens in
> them — no plot events, names, reveals, or chapter summaries. Your reply is
> relayed to the reader, who may be partway through the book.

That last sentence is load-bearing, and "do not return their contents" is not a
substitute for it. Subagents reliably honour the instruction for the digest files
and then put the reveals in the summary line instead, because a summary doesn't
feel like "contents". The return channel is the one path that reaches the reader,
so the constraint has to name plot detail explicitly.

Chapters stay in reading order within a batch deliberately — a digest written
without knowing what just happened tends to miss callbacks and reversals, which
are exactly the details worth surfacing later.

### 3. Synthesize and verify

Spawn one more subagent to read every `index/*.md` and write `index/book.md`:
the whole-book skeleton — arc or argument structure, cast or key concepts,
timeline, themes and motifs traced across chapters, and the handful of moments
that define the book. Aim for something that stands alone as an answer to "what
is this book" in about 1,500–2,500 tokens.

Then gate on:

```bash
python scripts/verify.py LIBRARY/SLUG
```

This catches the two failures that actually matter: a chapter that never got a
digest, and a quote that isn't in the book. The second is the dangerous one — a
paraphrase that drifted into quotation marks puts false words in the author's
voice, and a review built on it is worse than no review. Fix anything it flags
before showing the user a summary or review.

### 4. Use the index

**Questions.** Start with `index/book.md`. If the question is chapter-specific,
add those digests. If it needs exact wording, `grep` the chapter text — that's
what it's there for. Say which chapters an answer came from, so the user can
check you.

**Summaries.** Ask what it's for before choosing a shape: a spoiler-free blurb,
a full-arc synopsis, a chapter-by-chapter outline, and a thematic essay are
different documents built from different parts of the index.

**Reviews and the gap report.** Read `references/review-workflow.md` — this is
the main event and has its own procedure.

## Library layout

```
<library>/<slug>/
├── manifest.json      # chapters, token counts, batch plan, mode
├── chapters/0001.txt  # cleaned text, one file per chapter
├── index/
│   ├── 0001.md        # per-chapter digest
│   └── book.md        # whole-book synthesis
└── reviews/           # generated reviews and gap reports
```

Books are copyrighted and the text is bulky — keep the library outside any repo,
or gitignore it. The `index/` folder is derived and small, so it's reasonable to
keep if the user wants their notes version-controlled.

## Working with an already-indexed book

Check the library before ingesting anything — re-indexing a book that's already
there wastes the entire ingest cost. If `manifest.json` and `index/book.md` both
exist, go straight to answering. Re-index only if the source file changed or the
user asks for it.

## When this isn't the right approach

For a short story, an article, or anything under ~15k tokens, just read the file.
The indexing overhead only pays off at book length, and a digest of a short piece
loses more than it saves.

## Surveying a whole library

Before ingesting anything from a large collection, or when deciding which books
will parse cleanly:

```bash
python scripts/scan_library.py ~/Books [--suggest N] [--only toc-stub] [--csv out.csv]
```

Walks the tree recursively and gives each book a verdict — `toc-ok`, `toc-stub`,
`no-toc`, `needs-calibre`, `needs-pdf-tool`, `drm`, `error` — using the same
segmentation rule `ingest.py` applies, so it predicts real behaviour rather than
offering a second opinion. It reads only package metadata and stored file sizes,
so it's fast on big libraries; `--deep` extracts text for an exact answer.

`--suggest N` returns a structurally diverse sample (nested ToCs, anchor-shared
files, multi-file chapters), which is what you want when assembling test cases —
variety exercises more code paths than volume.

## References

- `references/digest-templates.md` — the fiction and nonfiction extraction
  templates the indexing subagents follow. Read before spawning them.
- `references/review-workflow.md` — writing the review and the four-part gap
  report against the user's own review. Read before any review task.
