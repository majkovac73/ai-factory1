# Step 100e — Recalibrate marketing-consistency: reject different subject, accept incidental style variance

**Date:** 2026-07-09

## Problem

`check_marketing_consistency()` was too strict. Real prod task **73fb29ba** was
blocked because a marketing image had "a different background and a different font
style" and another had "a blue watercolor background not present in the delivery
file" — **same text/subject, only incidental styling differed**.

Hero/lifestyle marketing images come from a **separate, independent text-to-image
call** from the delivery asset (this is not img2img — there is no way to force
pixel-identical background/font/style across independent generations). And since
the digital delivery asset is already prepended as the actual primary/featured
listing photo (step 96/100b), the secondary hero/lifestyle shots were only ever
meant to be supporting context of the same core design. The check was treating
**any** visual difference as a mismatch, wasting 1–2 remake cycles on cosmetic
differences and then hard-blocking real, correct products.

## Fix — tiered prompt (only `_build_consistency_prompt` changed)

`app/services/content_quality_service.py` — the vision-model prompt now
distinguishes **different subject/design (REJECT)** from **incidental generation
variance (ACCEPT)**. Parsing, schema, and every other method are unchanged.

### Old prompt (strict — the middle judgment paragraph)

> For EACH marketing image, decide whether it plausibly depicts the SAME
> product/design as the delivery file. Presentation may differ (a flat design vs
> the same design shown framed, in a room, cropped, or on a different background)
> — that is fine. But if a marketing image shows a clearly DIFFERENT, unrelated
> design/content (different artwork, a different pattern or border, different
> text, a generic stock mockup unrelated to the real file), THAT specific image
> is a mismatch and must be reported individually.

### New prompt (tiered)

> IMPORTANT: these images come from SEPARATE, independent image generations. They
> are NOT expected to be pixel-identical to the delivery file. Judge ONLY whether
> each marketing image depicts the SAME core subject, design, and content as the
> delivered product — not whether it looks stylistically identical.
>
> REPORT A MISMATCH only if the marketing image shows a genuinely DIFFERENT
> design/theme/subject than the delivered product — something a buyer would
> reasonably feel MISLED by. For example:
> - different text/wording than the delivered design,
> - a different illustrated scene, character, object, or motif,
> - a different pattern, or a clearly different core design,
> - a generic stock mockup unrelated to the actual delivered file,
> - the wrong product entirely.
>
> DO NOT report a mismatch for INCIDENTAL variation that any two independent
> generations of the same underlying design naturally produce, including:
> - a different background color, texture, or backdrop,
> - a different font style/rendering of the SAME words,
> - different lighting, color grading, or saturation,
> - decorative embellishment style, framing, cropping, or overall artistic
>   treatment,
> - showing the design flat vs. in a room / on a surface / held in a hand.
> These are acceptable and MUST NOT be flagged.
>
> When you DO flag a mismatch, the "issue" field MUST describe what is different
> about the CORE SUBJECT / DESIGN / CONTENT specifically (e.g. "shows a different
> illustrated animal", "the title text reads differently"). Do NOT flag something
> whose only difference is the background, font, color, lighting, or styling — if
> that is all that differs, it is a MATCH, not a mismatch.

The response schema is unchanged (`{"consistent": bool, "mismatches":
[{"image_index", "issue"}]}`) except the `issue` field's instruction now requires
it to name the **core subject** difference — anchoring the model's own reasoning
to the right tier instead of letting it fall back to cosmetic nitpicks.

## Tests

### `test_step96_content_quality.py` (now 9/9)

- **[6]** the recalibration itself: asserts the prompt now tells the model the
  images are independent/not-pixel-identical, names the ACCEPT tier
  (background/font/lighting/color grading) with "do not report a mismatch", and
  reserves REJECT for a different **core subject** the buyer would be "misled" by.
  *(This is the definitive test of the change — the doubles below verify plumbing.)*
- **[7]** task 73fb29ba scenario (same text/design, only background+font differ)
  → **PASS** (no mismatch, no needless remake).
- **[8]** GUARANTEE — a genuinely different core subject/design (different mandala
  pattern / illustrated subject) → **still FAILS**, and the issue names the
  subject difference. This is the one guarantee that must never regress; the
  original garbled-recipe-card / wrong-mandala bug class stays caught.
- **[9]** borderline (below).

### `test_step100b_consistency_remake.py` (now 8/8)

- **[7]** cost win: incidental-only variance returns `consistent` on the FIRST
  check → **0 remakes**, 0 wasted regeneration cost, task proceeds. (Tasks that
  used to burn 1–2 remake attempts on cosmetic differences now pass immediately.)

### Borderline decision (test [9])

**Grayscale delivery vs. vividly colored marketing shot, same subject → resolved
as ACCEPT.** Reasoning: color grading/saturation is explicitly in the ACCEPT
tier; the delivery asset is already the primary featured photo so the buyer sees
the true version first; and it's the same situation the step-100d coloring_page
fix intentionally embraces (colored marketing context vs. line-art delivery of
the same design). A pure color-palette difference of the **same subject** is
incidental variance, not misrepresentation. (If the *subject/design* itself
differed, tier-1 REJECT still fires — that's [8].)

## Guarantee confirmed

Detecting a **genuinely different design** (the original bug class this gate
exists for) still works — test [8] proves a different core subject/design is
still flagged and fails. The recalibration loosens only the **cosmetic** tier;
it does not loosen subject/content detection.

Full regression: steps **89–96, 98, 100b, 100d** all green.

## Verify (pending, cheap)

Once deployed, a task with only incidental marketing-image style variation should
pass on the first consistency check without triggering remakes — reducing wasted
remake-cycle cost. Real deploy + live confirmation remains pending Maj's go-ahead
(git push to prod), consistent with 100c/100d.
