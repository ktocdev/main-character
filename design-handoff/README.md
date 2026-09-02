# rag-journal — Design System Handoff

Purpose: formalize the design language that already exists in rag-journal's
CSS into a documented system, and give Claude Design what it needs to (a)
build design-system "relic" reference pages (tokens / components / patterns,
in the style of the GPS2 design kit at `gps2-design/design/*.html`) and
(b) design new layouts that stay consistent with this look.

**Direction: formalize, don't reinvent.** The palette, type choices, and
component shapes below are the target — not a starting sketch. New layouts
should read as more of the same app, not a rebrand.

**Scope of "new layouts"** — confirmed as all three of:
1. **Refreshed existing screens** — the ten tabs as they exist today, same
   content and IA, a more polished pass in this design language.
2. **New screens with no current equivalent** — onboarding/first-run, empty
   states, a landing/marketing page for "Main Character." These have zero
   CSS to reference; `01`–`05` are the constraint set (tokens, components,
   type/spacing, the wordmark/masthead), not a template to adapt.
3. **Mobile/responsive** — the app today has essentially none (one
   breakpoint, one rule, see `03-layout-patterns.md` § Responsive
   behavior). Any mobile layout is new design work from the token/component
   level up, not an adaptation of an existing responsive pattern.

**Known, deliberately left alone:** the app's only error/danger color
(`#c96a5a`, no token, no light-mode variant — see "Known gaps" below) stays
exactly as-is in the real codebase. It's documented as a gap for Claude
Design to resolve (a proper `--error` token with a light variant) as part
of the new work, not something already fixed here.

## Files in this bundle

1. [`01-tokens.md`](01-tokens.md) — every design token in use: color (dark +
   light), typography, the two fonts, motion. Includes the two hardcoded
   colors that escaped the token system and should probably get one.
2. [`02-components.md`](02-components.md) — full inventory of UI components
   and patterns with class names, states, and source references
   (`file.css:line`), organized by category (actions, inputs, cards/callouts,
   chips, disclosure, navigation).
3. [`03-layout-patterns.md`](03-layout-patterns.md) — the page shell and the
   handful of layout patterns every screen is built from (three-pane,
   composer-anchored single column, centered reading column).
4. [`04-type-and-spacing-scale.md`](04-type-and-spacing-scale.md) — the
   *observed* type and spacing values in use today (this app has no formal
   scale — sizes were hand-tuned per component). **Decided**: fold these
   into the named `--font-*`/`--space-*` scale documented there — every
   step matches an existing value, so this changes nothing visually.
5. [`tokens-reference.css`](tokens-reference.css) — the actual token source
   (`tokens.css`) plus the two light-mode override blocks from `base.css`,
   consolidated into one file with every value annotated. Drop this straight
   into a relic page.
6. [`05-visual-notes.md`](05-visual-notes.md) — notes from cross-checking
   the above against real screenshots. Two sets exist:
   `../screenshot/` (outside this folder, six tabs, pre-rename — still
   shows "journal," superseded) and **[`screenshots/`](screenshots/)**
   (in this folder, the current thorough set: all ten tabs plus several
   interaction states — open dropdowns, a native tooltip, a long
   autocomplete list, a checklist mid-run, and a save/backup flow caught
   working → done). Mostly confirms 01–04, plus one real finding: three
   native browser widgets (`<select>` options, `<datalist>` autocomplete,
   `title` tooltips) can't be restyled with CSS and break theme on every
   use — see "Native browser chrome breaks the illusion" in that file and
   the matching section in `02-components.md`. **Confirmed in scope**:
   Claude Design should design real replacement components for all three
   (custom listbox/combobox, custom autocomplete, custom tooltip) as part
   of this round, not leave it as a known gap.
7. [`source/`](source/) — the actual, unmodified source files these docs
   describe, copied here so this folder is self-contained and safe to hand
   off in full (unlike the rest of the `rag-journal` repo, which holds real
   journal content and shouldn't leave the machine):
   - `source/index.html` — the app's one page, every tab's markup.
   - `source/css/*.css` — all 11 stylesheets (`tokens.css`, `base.css`, and
     one file per tab).
   - `source/fonts/cmu-sans-demicondensed.ttf` — the self-hosted UI font.
     This one matters: it isn't on Google Fonts, so without this file
     Claude Design has no way to render CMU Sans Demi Condensed at all.
     (Old Standard TT doesn't need a copy — it's Google Fonts-hosted and
     loadable by name, see `index.html`'s `<link>` tags.)
   - `source/mask.png`, `source/mask2.png`, `source/mask3.png` — three
     iterations of a candidate logo concept (a two-tone theatrical mask),
     each closer to the system's style than the last; see "Logo concept"
     in `05-visual-notes.md` for the play-by-play.
   - **`source/mask-mark.svg`** — `mask3.png` vectorized into a true
     single-`<path>` SVG, transparent background, `fill="currentColor"` so
     it recolors via CSS to whatever token it's given, confirmed legible
     down to ~32px. `source/mask-mark-preview.png` shows it in `--accent`
     on the app's charcoal background.
   - **`source/mask-mark-twotone.svg`** — the same mark split into two
     independently colorable paths along its existing center seam (left
     half `--accent`, right half `--accent-dim`), reinterpreting the
     original concept's left/right duality instead of flattening it to one
     color. `source/mask-mark-twotone.png` shows it rendered. This is the
     closer match to the original idea — probably the one to lead with for
     Claude Design, with the single-tone version as the fallback for
     contexts where two colors would be too busy (very small favicon sizes,
     monochrome contexts). The three PNGs (`mask.png`/`mask2.png`/
     `mask3.png`) are process history, not something to hand off directly.

## What this app is

A single-page journal app (`static/index.html`) with one shell and ten tabs
(write, chat, search, entities, categories, patterns, dreams, history,
triage, settings, help), each tab's styling in its own CSS file, all keyed
off `static/css/tokens.css`. Dark is the default and considered the primary
theme; light is a secondary palette for the same semantic tokens, not a
separate design.

## Reference model for the relic pages

`gps2-design/design/GPS2 Tokens.html`, `GPS2 Buttons.html`,
`GPS2 Design Elements.html`, and `GPS2 Patterns.html` are a good structural
model for the deliverable format: a sticky sidebar nav + a scrolling main
column of sections, each section showing live specimens (a rendered swatch
or component next to its var name, value, and usage note). Note that GPS2's
*content* (pink/violet game palette, Gaegu/Nunito fonts, wood-and-parchment
game chrome) is unrelated to rag-journal — only borrow the page structure,
not the visual style.

## Fonts

- **Old Standard TT** (body serif) — loaded from Google Fonts in
  `index.html`, not self-hosted:
  `https://fonts.googleapis.com/css2?family=Old+Standard+TT:ital,wght@0,400;0,700;1,400&display=swap`
- **CMU Sans Demi Condensed** (UI/chrome sans) — self-hosted,
  `static/fonts/cmu-sans-demicondensed.ttf`, weight range 400–600. This is
  the font on every button, label, nav item, meta line, and chip — it's what
  makes the UI chrome feel distinct from the journal prose.

## Known gaps worth flagging to Claude Design

- No formal type or spacing scale — every screen's CSS hand-picked rem
  values. See `04-type-and-spacing-scale.md`.
- Two colors never made it into tokens: `#6b5b3e` (mock-banner fallback,
  `base.css:73`) and `#c96a5a` (error red, `settings.css` — used 3×). Both
  read as "this should have been a token" — likely `--accent-dim` fallback
  and a new `--error`/`--danger` semantic token respectively.
- No dedicated "danger" or "success" semantic color exists at all today —
  `.set-note.error` / `.set-feedback.error` invent `#c96a5a` locally because
  there's nothing to reach for.
