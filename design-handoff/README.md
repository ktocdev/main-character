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
   behavior). This is **not optional or a follow-up phase** — every new
   layout in categories 1 and 2 above should be designed with phone-width-
   and-up support in mind from the start, since none of the existing
   patterns have a mobile answer to fall back on. See
   `03-layout-patterns.md` § Responsive behavior for what each layout
   pattern needs specifically (the three-pane pattern and the top nav are
   the two with no mobile story at all today, and need real interaction
   design, not just a breakpoint).

**Update:** the token gaps this bundle originally flagged as "known,
deliberately left alone" — no `--font-*` family tokens, no `--error`
token, an untokenized fallback color, no named type/spacing scale — have
since been fixed directly in the real codebase (`static/css/tokens.css`
and every component file), not just documented. The reasoning: these are
mechanical value-aliasing (same colors, same fonts, same sizes, just
named), not a redesign, so fixing them before Claude Design starts means
the relic pages reference tokens that actually exist. See `01-tokens.md`
and `04-type-and-spacing-scale.md` for what changed. Radius has since been
tokenized too (`--radius-sm`/`--radius-base`/`--radius-lg`/`--radius-pill`/
`--radius-circle`), the same way and for the same reason.

## Files in this bundle

1. [`01-tokens.md`](01-tokens.md) — every design token in use: color (dark +
   light, now ten tokens including `--error`), typography (`--font-ui`/
   `--font-body`), the named type/spacing scale, motion.
2. [`02-components.md`](02-components.md) — full inventory of UI components
   and patterns with class names, states, and source references
   (`file.css:line`), organized by category (actions, inputs, cards/callouts,
   chips, disclosure, navigation).
3. [`03-layout-patterns.md`](03-layout-patterns.md) — the page shell and the
   handful of layout patterns every screen is built from (three-pane,
   composer-anchored single column, centered reading column).
4. [`04-type-and-spacing-scale.md`](04-type-and-spacing-scale.md) — the
   frequency analysis behind the named `--font-*`/`--space-*` scale, which
   is now real (wired into `tokens.css` and every component file, exact
   matches only — nothing was rounded, so nothing changed visually).
5. [`tokens-reference.css`](tokens-reference.css) — the actual token source
   (`tokens.css`) plus the two light-mode override blocks from `base.css`,
   consolidated into one file with every value annotated. Drop this straight
   into a relic page.
6. [`05-visual-notes.md`](05-visual-notes.md) — notes from cross-checking
   the above against real screenshots in **[`screenshots/`](screenshots/)**
   (in this folder): all ten tabs plus several interaction states — open
   dropdowns, a native tooltip, a long autocomplete list, a checklist
   mid-run, a save/backup flow caught working → done, and the cost
   popover. Mostly confirms 01–04, plus one real finding: three
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
   - **`source/mask-mark.svg`** — a logo mark, vectorized from a three-round
     theatrical-mask concept (process history — the intermediate PNGs and
     preview renders have since been deleted; see "Logo concept" in
     `05-visual-notes.md` for the play-by-play). True single-`<path>` SVG,
     transparent background, `fill="currentColor"` so it recolors via CSS
     to whatever token it's given, confirmed legible down to ~32px.
   - **`source/mask-mark-twotone.svg`** — the same mark split into two
     independently colorable paths along its existing center seam (left
     half `--accent`, right half `--accent-dim`), reinterpreting the
     original concept's left/right duality instead of flattening it to one
     color. This is the closer match to the original idea — probably the
     one to lead with for Claude Design, with the single-tone version as
     the fallback for contexts where two colors would be too busy (very
     small favicon sizes, monochrome contexts).

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

## Deliverable sequence

Do this in order, not all at once:

1. **One consolidated token specimen page, first.** Every token in
   `01-tokens.md`/`tokens-reference.css` on a single page — color (dark
   *and* light, all ten), both font families at each step of the
   `--font-*` scale, the `--space-*` scale, the `--radius-*` scale, motion
   — live-rendered swatches next to var name, value, and usage note. This
   is the `GPS2 Tokens.html` equivalent, and it's the foundation everything
   else points back to, so it comes before any screen redesign work
   starts, not after.
2. **Then redesign the screenshot pages, one at a time**, using `01`–`05`
   as the constraint set and referencing the token page rather than
   restating token values inline on each screen.
3. **Spin off a specimen page for a component only once it's actually
   recurring** — e.g. once a button shows up in two or more of the
   redesigned screens, that's the point to build a `Buttons` page
   collecting every variant (`button.send`, `button.quiet`, the icon-only
   round buttons) in one place, same pattern as `GPS2 Buttons.html` /
   `GPS2 Design Elements.html` / `GPS2 Patterns.html`. Don't front-load a
   full set of component pages before the screen work has shown which
   components actually repeat enough to earn one — `02-components.md`
   already flags likely candidates (`Card`, `Chip`, `ListItem`, the new
   `TagEditor` pattern) but let the redesigns confirm which of those are
   worth a dedicated page versus staying a one-off.

## Fonts

- **Old Standard TT** (body serif) — loaded from Google Fonts in
  `index.html`, not self-hosted:
  `https://fonts.googleapis.com/css2?family=Old+Standard+TT:ital,wght@0,400;0,700;1,400&display=swap`
- **CMU Sans Demi Condensed** (UI/chrome sans) — self-hosted,
  `static/fonts/cmu-sans-demicondensed.ttf`, weight range 400–600. This is
  the font on every button, label, nav item, meta line, and chip — it's what
  makes the UI chrome feel distinct from the journal prose.

## Known gaps worth flagging to Claude Design

- **The eight card-like components don't actually share enough to merge
  in code** — border treatment (solid+left-rule / plain / all-round
  dashed), radius, and padding all vary. See `02-components.md` § Cards
  for the full comparison table. This needs an actual design decision
  (one `Card` component, `tone`/`size` props), not a find-and-replace —
  which is why it's still a gap rather than something fixed alongside the
  tokens.
- **`.set-feedback` uses `--radius-sm` (6px) while its seven card
  siblings all use `--radius-lg` (10px)** — a real inconsistency the
  original draft of this doc had actually missed (it claimed a blanket
  "10px–12px" range for the whole group). Worth a deliberate call: keep
  the smaller radius as a real "this is a compact status callout, not a
  content card" distinction, or normalize it to `10px`.
- No dedicated "success" semantic color exists — only `--error` (added in
  the tokens pass) and `--accent` (doing double duty as the "ok"/positive
  color in `.set-note.ok` / `.set-feedback.ok`). If new layouts need a
  real success state distinct from the accent hue, that's new territory,
  not a gap in the current tokens.
- (Previously listed here: no font-family tokens, no `--error` token, an
  untokenized fallback color, no named type/spacing/radius scale. All are
  now fixed in the real codebase — see `01-tokens.md`.)
