# Digest templates

One digest per chapter, written to `index/NNNN.md` matching the chapter number.

A digest is the only thing that will be read later instead of the chapter. Write
it for a reader who will never see the original — but who may need to find the
original fast, which is why locations matter.

Aim for **600–900 tokens**. A short chapter deserves a short digest — don't pad
to hit a number.

Where to spend the budget matters more than the total. `Beats` should be tight:
it's the part most easily reconstructed from the chapter and the least useful
later. `Craft`, `Striking`, and `Quotes` are worth the words, because they capture
things that genuinely cannot be recovered without re-reading the book — and
they're what a review is built from.

## The one hard constraint: quotes

Everything under `## Quotes` is checked character-for-character against the book
by `scripts/verify.py`. Copy from the file; never reconstruct from memory.

Format exactly like this — the verifier reads markdown blockquote lines:

```
## Quotes
> It is a truth universally acknowledged, that a single man in possession of a good fortune, must be in want of a wife. — ch 1
```

Punctuation, capitalisation, and line wrapping are normalised before comparison,
so you don't need to preserve line breaks. Wording must be exact.

If a passage is worth flagging but too long to quote in full, describe it in
prose under another heading instead. A partial quote with an ellipsis in the
middle is fine; an approximated one is not.

---

## Fiction template

```markdown
# Ch {N} — {title}

## Beats
- What actually happens, 3–6 bullets, in order. Concrete: who does what, and
  what changes. "They argue about money and she leaves" beats "tension rises."

## Characters
- **Name** — first appearance / what they do here / how their situation or a
  relationship shifts. Mark first appearances so the cast can be assembled later.

## Craft
POV and tense, structural moves (time jumps, framing, withheld information),
pacing, and anything distinctive in the prose. This is the material a review
draws on, and it's the thing that vanishes if you only summarise plot.

## Motifs and themes
Which threads this chapter touches, and how — an image recurring, a stated idea
being tested, a symbol paying off. Note callbacks to earlier chapters explicitly.

## Quotes
> Verbatim, 2–4 per chapter. Choose lines a reader would underline — voice,
> image, or turn of phrase — not lines that merely summarise the plot. — ch {N}

## Striking
One or two sentences: what would stay with a reader from this chapter, and
anything genuinely surprising. Be honest if the answer is "nothing much" — a
flat chapter is real information about the book's shape.

## Spoiler level
none | minor | major — how much this chapter's content would spoil for a new
reader. Used later to keep spoilers out of blurbs and to tell a deliberate
omission from an oversight.
```

## Nonfiction template

```markdown
# Ch {N} — {title}

## Thesis
The chapter's central claim in one sentence. If it has none — it's setup,
narrative, or a digression — say that plainly.

## Argument
How the case is built: the claim, the moves supporting it, the conclusion drawn.
Note where the reasoning leans on assertion rather than evidence.

## Evidence
Studies, data, anecdotes, sources — with the actual numbers and names. Specifics
are what make this index useful; "cites research on memory" is close to useless
next to "Ebbinghaus, 1885 — 42% retention loss within 20 minutes."

## Concepts
Terms introduced or redefined, with the author's definition.

## Quotes
> Verbatim, 2–4 per chapter. Prefer passages that state the argument crisply or
> that a review would want to reproduce. — ch {N}

## Striking
What's surprising, contestable, or memorable. Note explicitly where you think a
claim is weak or overreaches — a review needs that, and it can't be recovered
from a neutral summary later.

## Spoiler level
Usually `none` for nonfiction. Use `minor`/`major` for narrative nonfiction,
memoir, or anything with a revealed outcome.
```

## Choosing between them

Follow the `mode` in `manifest.json` unless the prose contradicts it. Mixed books
are common and fine to handle as mixed: narrative nonfiction and memoir often
deserve the fiction template's **Craft** and **Quotes** sections alongside the
nonfiction **Thesis** and **Evidence**. Combine rather than forcing a bad fit,
and note at the top of the digest what you did so it reads consistently later.
