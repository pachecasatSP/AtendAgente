---
target: poc/landing-page
total_score: 30
max_score: 36
na_heuristics: 9
p0_count: 0
p1_count: 1
target_identity: "file:C:\\Users\\adolf\\repos\\bot-hermes\\poc\\landing-page\\index.html"
target_fingerprint: "sha256:d9333c7b50e3a629c2a39137c0c4c7b437581c145c0254078650a11e609aec3b"
target_path: "C:\\Users\\adolf\\repos\\bot-hermes\\poc\\landing-page\\index.html"
timestamp: 2026-09-02T18-21-28Z
slug: poc-landing-page-index-html
---
Method: dual-agent (A: general-purpose design review · B: general-purpose detector/browser evidence) — follow-up run after applying the 5 priority fixes from the prior critique.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Cookie banner, FAQ open/close, sticky nav all give clear feedback. |
| 2 | Match System / Real World | 4 | "Conversa" now explicitly defined; carioca-informal voice intact. |
| 3 | User Control and Freedom | 3 | FAQ + anchors give real control; cookie banner still has no persistent revisit path. |
| 4 | Consistency and Standards | 4 | New FAQ component reuses the existing card/eyebrow/radius vocabulary exactly — well integrated, not bolted on. |
| 5 | Error Prevention | 3 | Pricing footnote now prevents the "why is this crossed out" confusion. |
| 6 | Recognition Rather Than Recall | 4 | Every plan card still repeats all needed info; pricing note re-explains "conversa" right where needed. |
| 7 | Flexibility and Efficiency | 2 | Scored this run (was n/a before) — single fixed path, only 2 nav anchors, acceptable for Persuade but no shortcuts. |
| 8 | Aesthetic and Minimalist Design | 4 | Still restrained — one accent color, flat-by-default, Earned Shadow Rule intact through the new additions. |
| 9 | Error Recovery | n/a | No form inputs on this page; genuinely inapplicable. |
| 10 | Help and Documentation | 3 | Real change: FAQ answers the 5 most common objections in plain language, keyboard-accessible. Docked one point — no link out for questions the FAQ doesn't cover. |
| **Total** | | **30/36** | **Good (83%)** |

Heuristic 9 scored n/a as genuinely inapplicable; heuristic 7 was scored this run rather than exempted, which is why the applicable maximum moved from /32 (previous run) to /36 — the two runs are not a strict like-for-like denominator, but the underlying issues are directly comparable.

## Design Specificity Verdict

**LLM assessment**: Verdict holds from the prior run — specific, not generic-template. The fixes were integrated as real system tokens (a new `--leaf-btn` custom property, documented in DESIGN.md with its own named rule) rather than one-off inline overrides, and the new FAQ component matches the incumbent card/eyebrow/radius vocabulary exactly. This is a design system being extended correctly, not patched around.

**Deterministic scan**: The CLI scan ran degraded again (same parser-dependency gap as before) and reports 0 contrast findings — a false negative, since degraded mode doesn't compute contrast at all. The browser-injected detector (real DOM, real computed contrast) still flags 3 `low-contrast` instances, all on `div.step-num` (1.1:1, the pale outlined "01/02/03" numerals) — unchanged from the first run and, as noted then, most likely a decorative large-numeral treatment rather than body text meant to be read at normal contrast; still worth a deliberate look since it keeps surfacing. Critically, **nav-cta, cookie-btn-accept, and phone-avatar were not flagged this run** — consistent with those specific fixes holding. `hero-eyebrow-chip`, `kicker-above-heading`, `cream-palette`, and `pulsing-dot` are still flagged and are still false positives against DESIGN.md's own documented, intentional choices.

**Visual overlays**: Detector ran successfully in a live browser tab; both temporary servers were confirmed stopped after evidence capture. Mobile-width screenshot was unobtainable again this session (window resize not taking effect) — desktop evidence is reliable, mobile evidence is source/breakpoint-based only.

## Overall Impression

The five originally-reported issues are meaningfully addressed: strikethrough pricing now has an explanation, the previously-failing white-on-green and muted-gray-on-cream contrast pairs now clear WCAG AA, a real keyboard-accessible FAQ exists, the Crescendo tier has framing copy, and the three "talk to Duda" CTAs now read as three distinct moments instead of one repeated button. The contrast fix in particular was done the right way — as a token, with the reasoning documented, not a spot patch. What's left is smaller and mostly new-since-last-time: one real contrast miss in the one section of the page that deliberately opts out of the token system (`features-band`), and the Crescendo justification is emotional/segmentation framing rather than the numeric reassurance a skeptical buyer doing mental math would want.

## What's Working

1. **The contrast fix is architecturally sound.** `--leaf-btn` is a real, separate, per-theme token (not a reused alias that would have broken in dark mode) — DESIGN.md documents why it had to be separate from `--leaf-deep`. This is the difference between fixing a bug and papering over a symptom.
2. **The FAQ is a genuinely well-executed addition.** Native `<details>/<summary>`, zero JS, verified interactively (both tested items opened and closed correctly), and visually indistinguishable in quality from components that existed before it.
3. **CTA differentiation reads as three distinct emotional registers**, not just three different strings: nav is generic contact, hero is action/urgency, final CTA is personal + risk-reversal ("sem compromisso"). This is copywriting that understands why the duplication was a problem, not just a search-and-replace fix.

## Priority Issues

**[P1] A real, unaddressed contrast failure in `features-band` body text — missed because that section deliberately opts out of the token system.**
- **Why it matters**: `.features-band .section-head p` (`rgba(255,255,255,0.78)` on `#0E7A3D`) computes to ≈3.96:1, and `.feature-card p` (`rgba(255,255,255,0.85)`) computes to ≈4.39:1 — both below the 4.5:1 AA threshold for normal-size text, both real body copy (the "what it does" feature descriptions, 6 cards' worth). Because the Fixed Band Rule intentionally routes this section around the token system, it was outside the sweep that fixed the other contrast failures.
- **Fix**: Bump both to ≥0.90 alpha (or a solid near-white like `#F2FBF6`); ~0.90 alpha gets `.feature-card p` to ≈4.9:1.
- **Suggested command**: `/impeccable audit`

**[P2] Crescendo price jump is reframed, not numerically explained**
- **Why it matters**: The added blurb explains *who* the tier is for, not *why* the price is 4.5x for a 5x volume increase — which is actually a defensible volume discount, but the page doesn't show the math. A numerically-inclined reader still has to take it on faith.
- **Fix**: Surface the per-conversation cost trend near the pricing grid (e.g., a one-line "quanto mais conversa, menor o custo por conversa"), turning the jump into a visible discount instead of an unexplained cliff.
- **Suggested command**: `/impeccable clarify`

**[P3] `summary` isn't covered by the branded focus-visible ring**
- **Why it matters**: The FAQ's `<summary>` elements are keyboard-focusable and toggleable, but the global `a:focus-visible, button:focus-visible` rule doesn't include `summary` — browsers supply a default ring, but it's visually inconsistent with every other interactive element on the page.
- **Fix**: Add `summary:focus-visible` to that selector.
- **Suggested command**: `/impeccable polish`

**[P3] Reassurance phrasing repeats three times in the page's last third**
- **Why it matters**: "Grátis, sem compromisso" (trial banner), "sem fidelidade" (FAQ), "sem compromisso" (final CTA) each close a genuinely different objection (trial risk, cancellation risk, sales pressure), but stacked this close together it starts to read as protesting too much rather than confident.
- **Fix**: Vary the phrasing, or trim one instance.
- **Suggested command**: `/impeccable clarify`

**[P3] Pricing footnote's register doesn't match the page's voice**
- **Why it matters**: "Referência de posicionamento frente ao mercado brasileiro de chatbots agênticos via API oficial Meta (jul/2026) — valores sujeitos a ajuste" is legalistic/corporate phrasing dropped into a page that's deliberately colloquial everywhere else ("Cê dorme," "sem enrolação"). It reads like a different author wrote it.
- **Fix**: Tighten to match the page's register without losing the necessary disclaimer.
- **Suggested command**: `/impeccable clarify`

## Status of the 5 originally-reported issues (verified from scratch, not assumed)

1. **Strikethrough pricing footnote** — RESOLVED.
2. **Contrast (buttons + muted text)** — RESOLVED for the exact instances originally flagged (nav-cta, cookie-accept, phone-avatar, labels/price-notes/footer). Not a complete sweep — see P1 above.
3. **No FAQ** — RESOLVED, verified interactively working.
4. **Crescendo price jump unexplained** — PARTIALLY RESOLVED — see P2 above.
5. **Near-identical CTA copy** — RESOLVED, three distinct registers now.

## Persona Red Flags

**Jordan (skeptical small-business owner)**: Will likely still pause and do rough mental math at the Crescendo jump — the blurb won't fully stop that pause, though the FAQ right after pricing now gives Jordan somewhere to resolve doubt before bouncing.

**Riley (fast mobile scroller)**: Benefits most from the FAQ — objections that previously required messaging Duda directly now self-resolve on-page. The features-band contrast dip is a small but real tax on exactly this persona, reading body copy fast on a phone screen.

**Casey (accessibility-conscious user)**: Benefits from the native `<details>` FAQ (real keyboard support, nothing custom to get wrong), but would flag both the features-band text and the missing `summary:focus-visible` as rough edges in an otherwise accessible build.

## Minor Observations

- `div.step-num`'s 1.1:1 contrast keeps surfacing in the detector across both runs — still most likely a decorative oversized-numeral treatment, not body text, but worth a deliberate one-time decision (document it as an intentional exception, or lighten the stroke) so it stops showing up as an open question every run.
- Pricing card vertical rhythm has a minor seam: the Crescendo blurb adds a line the other cards don't have, so price blocks don't all start at the same y-offset across the grid — not a functional bug.
- Cookie banner correctly gates GTM/GA4 behind consent — verified in the script logic, matches the page's own LGPD claim.
