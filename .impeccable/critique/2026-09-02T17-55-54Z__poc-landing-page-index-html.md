---
target: poc/landing-page
total_score: 25
max_score: 32
na_heuristics: 7,9
p0_count: 1
p1_count: 2
target_identity: "file:C:\\Users\\adolf\\repos\\bot-hermes\\poc\\landing-page\\index.html"
target_fingerprint: "sha256:f22a384c1f88aa85214eb075df964870d6918f0d6311dfd3d4bf1755a1e2a906"
target_path: "C:\\Users\\adolf\\repos\\bot-hermes\\poc\\landing-page\\index.html"
timestamp: 2026-09-02T17-55-54Z
slug: poc-landing-page-index-html
---
Method: dual-agent (A: general-purpose design review · B: general-purpose detector/browser evidence)

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|-----------|-------|-----------|
| 1 | Visibility of System Status | 3 | Chat "digitando…" and nav "online" badge communicate liveness well; no other status signal needed on a static page. |
| 2 | Match System / Real World | 4 | Carioca-informal Portuguese, "conversa"/PIX/LGPD/Meta terms match the target buyer's real vocabulary exactly. |
| 3 | User Control and Freedom | 2 | Cookie consent has no persistent way to revisit after dismissal (no footer settings link). |
| 4 | Consistency and Standards | 4 | Pill shapes, one-accent-color rule, and card radii applied with real discipline — matches DESIGN.md closely. |
| 5 | Error Prevention | 3 | Unexplained strikethrough "de R$X" pricing reads as an unverifiable discount claim (see P0 below). |
| 6 | Recognition Rather Than Recall | 4 | Every plan card repeats all needed info; nothing requires recalling an earlier section. |
| 7 | Flexibility and Efficiency | n/a | Genuinely inapplicable — single-path, first-visit Persuade surface. |
| 8 | Aesthetic and Minimalist Design | 4 | Restrained single-accent discipline, generous whitespace, no competing visual noise. |
| 9 | Error Recovery | n/a | No form inputs on this page to produce errors; signup happens off-page. |
| 10 | Help and Documentation | 1 | Checked before marking n/a: zero FAQ/docs; only escape valve is messaging a stranger on WhatsApp. |
| **Total** | | **25/32** | **Good (78%)** |

Heuristics 7 and 9 scored n/a as genuinely inapplicable to this static Persuade surface; the total is renormalized to /32.

## Design Specificity Verdict

**LLM assessment**: Specifically authored, with one soft seam. The copy voice (carioca-informal: "Cê dorme," "sem enrolação," "bora nessa"), the "conversa/mês" billing unit (specific to WhatsApp Business API economics, not a generic SaaS seat model), the named human handoff ("Duda," AC Soluções), and the LGPD/Meta Tech Provider/anti-"número clonado" trust framing are all product-truth-specific per PRODUCT.md — a competitor couldn't swap this in unchanged. The one generic seam: every piece of visual "proof" (hero chat, all 4 mockup cards) is explicitly labeled illustrative/fictional. PRODUCT.md confirms real production tenants exist (novo-negocio, linda-ana-calcados); none of that reality appears on the page, so the proof layer is persuasive in form but not backed by anything verifiable.

**Deterministic scan**: The CLI scan ran in degraded mode (parser deps unavailable, regex fallback) and undercounts; the browser-injected detector (real DOM) is the more reliable pass and found 23 anti-pattern instances. Several are real, several are false positives once cross-checked against DESIGN.md and the file's actual intent:

- **Real, actionable**: a cluster of WCAG AA contrast failures — white-on-green text at 2.9:1 (`a.nav-cta`, `div.phone-avatar`, `button.cookie-btn-accept`, all need 4.5:1) and muted-gray-on-cream text at ~3.0–3.1:1 across `div.label`, `span.mockup-tag`, `div.plan-price-de`, `p.pricing-note`, `p.footer-tagline` (all need 4.5:1 for normal-size text). This is systemic — the same `--ink-faint`/`--tinta-fraca` token is under-contrast everywhere it's used for legible (non-decorative) text, and `#fff` on `--leaf`/`--mata-atlantica` is under-contrast on every green-filled button. Neither assessment's manual review flagged the exact ratios; the detector caught something the LLM pass only gestured at generically under "accessibility."
- **Likely false positives** (confirmed against DESIGN.md, which was derived from this exact file and documents these as deliberate): `kicker-above-heading` (×6) is the intentional Eyebrow/Label token, not an anti-pattern; `cream-palette` is the deliberate Marfim background choice DESIGN.md names as its anti-corporate-blue stance; `pulsing-dot` is the signature "digitando…" typing indicator both assessments independently called out as a strength; `hero-eyebrow-chip` is the same intentional eyebrow component. `dark-glow` flagging the CTA's tinted box-shadow as "on a dark page" is a miscategorization — the page background is the light Marfim cream, not dark.
- **Genuinely low-priority**: `design-system-font-size`/`-radius` drift (18 and 3 occurrences) is largely expected — DESIGN.md deliberately records scale roles, not every literal rem value used in the file, per its own "don't extract every token" discipline. `design-system-color` flags `#211500` (near-black text on yellow) at 5 locations — same hex every time, which reads as one legitimate token DESIGN.md simply never named, not five instances of drift. `em-dash-overuse` (19–20 in body copy) and `bounce-easing` are stylistic notes, not defects.

**Visual overlays**: The detector ran successfully in a live browser tab (not `file://`) via a temporary static server, which has since been stopped. No standing overlay remains open for you to view — Assessment B's browser mutation succeeded and captured the console findings above, but window-resize between desktop and mobile widths did not take effect in this environment (a known limitation this session), so the mobile-specific evidence is source/breakpoint-based rather than a genuinely distinct mobile screenshot. Treat the CSS breakpoint analysis (below, from source) as more reliable than the mobile screenshot for this run.

## Overall Impression

This is a landing page with real authorial discipline — the one-accent-color rule, the pill/rounded-corner language, and the WhatsApp chat mockup as literal (not decorative) hero are all executed with more restraint than most marketing pages manage. The biggest opportunity isn't visual: it's that the page spends an entire section building "we're not shady, sem gambiarra, número seguro" trust — and then immediately undercuts it with an unexplained strikethrough discount price, on top of a real, systemic contrast problem across its secondary text that a screen-reader-adjacent or low-vision buyer would hit on nearly every section.

## What's Working

1. **The chat mockup is a functioning micro-demo, not an illustration.** Staggered `animation-delay` (0.1s → 2.1s) with a `.typing` indicator timed at 1.85s mimics real WhatsApp latency, and it cleanly degrades under `prefers-reduced-motion`. It's the single most persuasive artifact on the page because it *shows* rather than *claims* responsiveness.
2. **The One Accent Rule is enforced, not just documented.** Sol da Tarde (yellow) appears only at true decision points (primary CTA, trial banner, featured-plan badge/CTA) — confirmed by both assessments independently. Most marketing pages over-use their accent; this one doesn't.
3. **Copy voice discipline end-to-end.** "Cê dorme," "bora nessa," "Fala com a Duda no zap" — the informal register is sustained into microcopy, not just the headline, which is exactly what makes the page feel authored for this specific brand rather than templated.

## Priority Issues

**[P0] Unexplained strikethrough pricing contradicts the page's own trust argument**
- **Why it matters**: Every paid plan shows a crossed-out "de R$X" beside the real price with zero footnote explaining what that price was or when it applied. This is the exact fake-anchor-discount pattern a small-business owner has learned to distrust from shady WhatsApp-bot resellers — and the trust section immediately above pricing explicitly promises "sem gambiarra, sem número clonado." The page undoes its own credibility work one section later.
- **Fix**: Add a one-line footnote near `.pricing-note` ("preço de lançamento, válido até [data]") or drop the strikethrough — the 7-day free trial is already a strong enough incentive without an unverifiable discount illusion.
- **Suggested command**: `/impeccable clarify`

**[P1] Systemic WCAG AA contrast failures across secondary text and green-filled buttons**
- **Why it matters**: `a.nav-cta`, `div.phone-avatar`, and `button.cookie-btn-accept` all put white text on `--leaf`/`--mata-atlantica` green at ~2.9:1 (need 4.5:1). Muted `--ink-faint` gray text on cream backgrounds — `div.label`, `span.mockup-tag`, `div.plan-price-de`, `p.pricing-note`, `p.footer-tagline` — sits at 3.0–3.1:1 (also need 4.5:1). This isn't a one-off; it's the same two token pairings reused across the whole page, so it affects low-vision and older users on nearly every section, not an edge case.
- **Fix**: Darken `--ink-faint`/`--tinta-fraca` enough to clear 4.5:1 on both `--paper` and `--paper-soft`, and either darken `--leaf` or switch to dark text on the green buttons/badge to clear 4.5:1 for white-on-green usages. Re-run the contrast check after.
- **Suggested command**: `/impeccable audit`

**[P1] No FAQ/help content for a self-described non-technical, risk-averse buyer**
- **Why it matters**: PRODUCT.md frames the primary user as someone with no dedicated support staff, deciding whether to connect their one real business number to a third party. The page answers "what does it do" and "is it safe" in broad strokes but never "what happens if I exceed my plan," "can I cancel," "what happens to my number if I stop paying." The only escape valve for any doubt is messaging a stranger on WhatsApp — raising the cost of resolving hesitation for exactly the buyer PRODUCT.md says is most hesitant.
- **Fix**: Add a compact 4-5 item FAQ block before the final CTA (data privacy, over-quota behavior, cancellation, "does it replace me entirely").
- **Suggested command**: `/impeccable shape`

**[P2] Crescendo plan is a 4.5x price cliff with no bridging logic**
- **Why it matters**: Começando → Crescendo jumps from R$157.90 to R$717.90 (4.5x) while the other tier-to-tier jumps are ~1.9x. Nothing in the copy softens or explains the jump (no "most businesses stay on Começando," no note on what changes operationally at that volume). It reads as an enterprise price parked inside an otherwise self-serve grid.
- **Fix**: Add one line of context under Crescendo explaining the operational jump, or visually de-emphasize it relative to the three self-serve tiers.
- **Suggested command**: `/impeccable clarify`

**[P2] Six CTAs, two destinations, near-identical copy — no signal for "chat" vs. "start trial"**
- **Why it matters**: Nav, hero, and final CTA all route to the same WhatsApp number with nearly identical text ("Falar/Fala com a Duda no zap"), while plan cards route to a separate onboarding signup — but nothing distinguishes "talk first, low commitment" from "start the trial, higher commitment." A skimming visitor sees six interaction-equivalent buttons.
- **Fix**: Differentiate copy/visual weight so the two destinations read as two distinct decision tiers.
- **Suggested command**: `/impeccable clarify`

## Persona Red Flags

**Jordan (confused first-timer)**: "Meta Tech Provider" badge (nav + trust section) is asserted twice with zero explanation of what it means or why it's hard to get — reads as marketing filler, not credibility, to someone who's never heard the term. The "conversa ≠ mensagem" pricing clarification is good defensive copy but is the only place billing mechanics are explained, buried in a dense paragraph easy to skim past before hitting the pricing cards.

**Riley (stress tester)**: The `.phone` chat mockup — the single most persuasive element on the page — is `aria-hidden="true"` with no text-equivalent summary, so a screen-reader user gets zero access to it. Every piece of visual "proof" on the page (hero chat + all 4 mockups) is explicitly labeled fictional/illustrative; combined with the unexplained strikethrough pricing, a skeptical buyer has nothing verifiable to grab onto — no real, named, or even anonymized production tenant appears anywhere, despite PRODUCT.md confirming real ones exist.

**Casey (distracted mobile user)**: `.pricing-grid` collapses to a single column under 560px — four stacked price cards with 5 line items each, no comparison aid, the highest-friction moment on the page for a rushed mobile visit. `.mockups-grid` also collapses to 1 column with `loading="lazy"` images, so a fast scroll on a slow connection can hit blank placeholders right as the "proof" section needs to land.

## Minor Observations

- `alt` text on the mockup images is genuinely good and specific ("Bot respondendo cliente automaticamente às 23h52") — accessibility done right, not just adequate, worth preserving in any future edit.
- `#211500` (near-black text on yellow surfaces) is reused identically at 5 locations but isn't a named DESIGN.md token — worth promoting to a documented token rather than treating as drift.
- Cookie banner has no persistent "manage cookies" link once dismissed — minor but real gap given the page's own LGPD trust claim.
- Hero `.lead` paragraph does four jobs (responds/shows catalog/books/charges) in one ~40-word sentence — the densest single passage on the page, right after the headline.
- `em-dash-overuse` (19–20 in body copy) and the `dark-glow`/`kicker-above-heading`/`cream-palette`/`pulsing-dot` detector flags are stylistic or false positives against the file's own documented, intentional choices — no action needed.

## Questions to Consider

- What if the strikethrough pricing were removed entirely — would the 7-day trial alone convert as well without the discount-illusion risk?
- What if the chat mockup's flawless three-turn exchange included one small, human-feeling hiccup — would that read as more trustworthy to a buyer who's been burned by chatbots before?
- What if one real, anonymized tenant screenshot replaced one of the four fictional mockup cards — would that single piece of verifiable proof outweigh the other three combined?
