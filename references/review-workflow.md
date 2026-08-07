# Reviews and the gap report

Two related jobs: writing a review from the index, and checking the user's own
review against the book. The second is the point of the skill — the first mostly
exists to make the second work.

## Write blind first

**Draft the review before reading the user's review.** This is the whole method.

If you read theirs first, you will anchor on it: their framing becomes your
framing, their omissions become your omissions, and the comparison degrades into
agreeing with them in different words. The gap report is only worth anything if
the two documents were produced independently.

So: index → your review → *then* open theirs → compare.

If the user pastes their review in the same breath as asking for the check, still
do it in this order. Say that's what you're doing, and don't read ahead.

## Writing the review

Read `index/book.md` first, then the chapter digests. Grep the chapter text for
any quote you intend to use — digest quotes are verified, but re-checking one you
lean on costs nothing.

There's no fixed template; a review's shape should follow the book. Cover what a
useful review covers: what the book is doing, whether it works, how it's built,
who it's for, and the specific moments that carry it. Ground claims in concrete
detail — a named scene or a quoted line — rather than adjectives.

Ask about length and spoiler policy if the user hasn't said. Match the register
of their existing reviews if you have any to hand; if they keep past reviews in a
folder, read two or three first to pick up voice and typical length.

Save to `reviews/claude-review-<date>.md`.

## The gap report

Read the user's review, then produce `reviews/gap-report-<date>.md` with these
five sections.

### Framing that matters

A review is a **curatorial act, not a coverage exercise.** Most things a reviewer
leaves out were left out on purpose — for length, for spoilers, because they
didn't find it interesting, or because it doesn't serve the piece. Treating every
difference as an oversight produces a long list the user has to argue with, and
they'll stop reading it.

So for each item, judge whether it looks deliberate:

- **Spoiler-shaped** — check `Spoiler level` in the digest. If their review is
  clearly spoiler-free and the item is a major spoiler, that's a policy, not a
  miss. Note it as available-if-wanted, at most.
- **Out of scope** — a thematic review skipping prose style, or a craft-focused
  one skipping plot, is doing its job.
- **Genuinely missed** — striking, in scope, consistent with what they chose to
  cover elsewhere, and simply absent.

Lead with the third category. Keep the first two short.

### 1. Memorable moments and lines

Scenes, images, and quotes flagged `Striking` in the digests that appear nowhere
in their review. Quote the line, cite the chapter, and say in one sentence why it
might be worth including.

Rank by how much you think it would improve the piece. A reviewer skimming this
section should be able to stop after three items and have gotten the value.

### 2. Craft and technique

Structural and stylistic observations from the digests' `Craft` sections that
their review doesn't touch — POV handling, pacing, motif work, structural
gambits, unreliable narration.

Be selective. Every book has technique to discuss; only surface things that are
distinctive in *this* book and that a reader of their review would be better off
knowing. A list of everything the author did is not useful.

### 3. Possible factual slips

Claims in their review checked against the book: names, events, ordering,
attributions, numbers, who-did-what-to-whom.

Be careful and be conservative here, because this is the section where being
wrong is most annoying:

- Cite the chapter and quote the contradicting text. An unsourced correction is
  useless — they can't check it.
- **Do not flag interpretation as error.** "The ending is unearned" is a
  judgement, not a mistake; "the brother dies in chapter 3" when he dies in
  chapter 30 is a mistake. If you're arguing with a reading rather than a fact,
  it doesn't belong in this section.
- Flag genuine ambiguity as ambiguity. If the text supports their version on a
  reasonable reading, say so and move on.
- If you find nothing, say "no factual issues found" — a clean result is a real
  result, and padding this section teaches them to distrust it.

### 4. Coverage balance

Map which parts of the book their review draws on. Reviews commonly lean on the
opening — it's freshest at the point of starting to write, and it's spoiler-safe.

Report the shape plainly: which thirds or acts are represented, and where the
review goes quiet. Note that thin late coverage is often a deliberate spoiler
choice, and only flag it as a gap if the review is otherwise spoiler-tolerant.

### 5. Already well covered

Three or four lines on what their review caught that yours didn't, or made
better. This is not politeness — it tells them the comparison actually understood
their piece, and it's the fastest way for them to spot when the tool has
misread the review and the rest of the report needs discounting.

## Comparing across several reviews

If the user wants patterns across a body of past reviews rather than one book,
run the gap report per book, then look for what recurs: a reviewer who
consistently under-covers endings, or rarely quotes, or always writes about theme
and never about sentences. That standing tendency is more useful than any single
report, and it's the kind of thing that's invisible from inside your own writing.
