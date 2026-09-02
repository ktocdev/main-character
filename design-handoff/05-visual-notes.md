# Visual Notes (from screenshots)

Two screenshot sets exist. `../screenshot/` (outside this folder) is the
original round — six tabs, but taken before the rename and still shows the
old "journal" wordmark; treat it as superseded. `screenshots/` (in this
folder) is the current, thorough set: all ten tabs plus several
interaction states (dropdowns, a native tooltip, a long autocomplete list)
— all dark mode, mock/seed instance (hence the amber banner on every
shot), correctly showing "main character." The notes below are keyed to
the current set; the wordmark/logo notes further up this file were written
against the first round and still hold.

## The wordmark

`header h1` (**"main character"**, renamed from "journal" — screenshots in
`../screenshot/` still show the old name) renders as a genuine wordmark,
not just a styled heading — italic serif, accent-colored, and visually the
anchor of every screen. There is no logo asset anywhere in the codebase;
this styled text *is* the brand mark today. If Claude Design is building a
landing page, app icon, or any marketing surface, this is the thing to
either extend into a real logo or deliberately keep as typographic-only —
worth an explicit decision rather than defaulting to inventing an icon.
Also worth a beat of thought: "main character" is a two-word wordmark where
"journal" was one — check line-wrapping/kerning at the header's fixed
`1.15rem` size before assuming it drops in with no layout changes.

Directly under it, `.status` ("31 entries · 49 entities") sits as a small
dim stat line — call this the **masthead** pattern: wordmark + one-line
live stat, top-left, on every screen.

## Logo concept (`source/mask.png`, `source/mask2.png`) — an evolving idea, not a finished mark

Two iterations of the same two-tone theatrical mask, one half warm skin
tone, one half white, black hair-cap, a single black teardrop on the pale
half.

**Thematically it fits both versions** — a mask is a natural symbol for
"playing a character," and the left/right split could map cleanly onto
something the app already has a concept for: "you" vs. "companion," or the
version of yourself performing vs. the one journaling in private.

**`mask.png` (v1)** is glossy vector clip-art — gradient shading, heavy
black outlines, photoreal red lips — against an app that is flat, warm,
and restrained everywhere else.

**`mask2.png` (v2)** dropped the red lips and flattened the shading —
closer to how the rest of the app looks, but still exported as plain RGB
with a baked-in white background (no transparency), and still off-palette.

**`mask3.png` (v3) is a genuine single-color mark**: solid black linework
(hair silhouette, split-face outline, brows, eyes, nose, lips, one
teardrop) on a real transparent background (confirmed via the PNG's alpha
channel — it's RGBA, alpha ranges 0–254, not a flattened white/black
square). It only *looked* broken in this editor at first because it was
being previewed against this UI's own dark background — black-on-black.
Once composited against white or the app's actual charcoal, the mark is
there and reads clearly.

**`source/mask-mark.svg`** — I vectorized `mask3.png` locally (via
`potrace`, a bitmap-to-vector tracer) rather than relying on an image
generator to hand-write SVG path data, which isn't something that kind of
tool actually does reliably. The result is a true single `<path>` element,
`fill="currentColor"`, transparent background, 13 subpaths as real bezier
curves — confirmed to recolor correctly by swapping `currentColor` for
`#d99e5b` (`--accent` dark) and rendering it, see `mask-mark-preview.png`.
I also rendered it down to 32px wide (roughly the wordmark's scale) and
the silhouette, split, eyes, and teardrop all still read clearly — the
"too detailed for small sizes" risk I flagged earlier didn't materialize.

**`source/mask-mark-twotone.svg`** — the two-tone follow-up, reinterpreting
the original artwork's left/right face split (which was always the core
concept — "you" vs. "companion," or the version of yourself performing vs.
the one journaling in private) as two independently colorable paths
instead of one flat color. Built by splitting `mask3.png`'s alpha bitmap
at its exact horizontal center (the ink bounding box is symmetric, center
column 512 of 1024 — confirmed before splitting, not assumed) and tracing
each half separately, so the seam lands exactly on the artwork's existing
center line rather than an arbitrary cut. Left half is `style="fill:
var(--accent, #d99e5b)"`, right half `style="fill: var(--accent-dim,
#8a6a44)"` — each references the real token by name with a literal
fallback, so it picks up the live theme automatically when embedded in the
app (the fallback only matters when the file is opened standalone, outside
any element with those custom properties defined). Same small-size check
as the single-tone version: still reads clearly at 32px wide.
`mask-mark-twotone.png` shows it rendered on the app's charcoal background.

**Where this leaves it**: both `mask-mark.svg` (one flat color, use
`style="color: var(--accent)"` on the `<svg>` since it's `currentColor`)
and `mask-mark-twotone.svg` (two colors baked into the two paths directly)
are usable today. The two-tone version is the closer match to what the
original mask concept was actually about; the single-tone version is the
safer pick if the mark ever needs to sit somewhere the two-color contrast
would be too busy (e.g. very small favicon sizes, or a monochrome context).

## The accent color is a small-surface color, with one loud exception

Across all six screenshots, `--accent`/`--accent-dim` are used exclusively
for: text (headings, labels, italic emphasis), borders, small pill/chip
fills, and the send button. They never cover a large surface. The one
exception is the seed/mock banner (`#seed-banner-bar`), which fills the
entire top strip in solid `--accent` — and it reads as loud *because*
nothing else in the app does that. If new layouts (landing page, empty
states, etc.) reach for a big accent-colored surface, know that it will
read as an alert/banner in this system's visual language, not as
"brand color, used generously" — that's a deliberate contrast worth
preserving or consciously breaking.

## Buttons read rounder than their radius number suggests

`button.send` is `border-radius: 10px` in source, but at its actual
rendered height (~2.2rem) that reads as substantially rounded — close to
pill, not "slightly rounded rectangle." Same for `.quiet` buttons at 8px.
When Claude Design builds a button specimen at true size, expect it to
look more pill-like than the raw px value implies; don't second-guess the
token value, the CSS is correct — just render at real size, not oversized,
when showing radius examples.

## Native browser chrome breaks the illusion in three places — fixing this is in scope

This is the one real, actionable finding from the new set — not a style
nuance, a functional gap, and **confirmed in scope to fix as part of the
Claude Design iteration**, not a gap to leave alone. Three UI elements
render in raw OS/browser default appearance, because none of it is
stylable via CSS, and all three look jarring against the app's warm dark
theme:

1. **`<select>` dropdown *options*** (`entities-select.png` — the "ask
   claude…" retype/suggest dropdown open). The closed control is properly
   skinned (`--bg-input`, `--border`, UI-sans font, per `02-components.md`
   § Inputs), but the instant it opens, the option list is plain OS
   chrome: white background, black text, generic sans-serif, blue
   highlight on the focused row. Every `<select>` in the app — retype,
   suggest-kind, date display, time zone, language, companion model,
   companion effort, processing model (settings.png has seven of these) —
   has this same gap.
2. **`<datalist>` autocomplete** (`entities-long-select.png` — the
   "target name…" merge field's suggestion list, showing ~15 entity/place
   names). Also plain OS chrome, no border-radius, generic font. This one
   happened to render dark (OS-level dark mode), but it's still not the
   app's palette or type — it's a coincidence, not a themed result.
3. **`title`-attribute tooltips** (`title-tooltip.jpg` — hovering "detect
   patterns" shows "re-read the weekly arcs + domain documents and detect
   recurring patterns…"). White box, black text, default sans, hard
   rectangular corners. This one matters most by volume: `index.html` uses
   `title="..."` for help text dozens of times — nearly every icon button,
   ambiguous label, and menu item leans on it as the *only* explanation
   offered. Every one of those hovers currently breaks theme.

None of these three can be restyled with CSS — this is a real platform
limitation, not an oversight in the app's stylesheets. **Direction for
Claude Design**: design real custom components to replace all three — a
styled listbox/combobox for `<select>`, a styled autocomplete panel for
`<datalist>`, and a styled tooltip component for every `title` attribute
currently doing that job — as part of this round of work, not a follow-up.
Given how often `title` is used throughout `index.html`, the tooltip
component is likely the one with the biggest visible payoff.

## A few things the richer content revealed

- **Settings has more structure than the bare markup showed.** With real
  `.env` values rendered, Settings turns out to have a "MODELS & COST"
  section with a repeating sub-pattern not documented in `02-components.md`:
  a field label → `<select>` → descriptive help sentence, sometimes
  followed by a **live-updating figure** ("This session: $0.03 of $10.00,"
  "Session cost: $0.03 · 5,140 tokens · 1 call"). One of these lines uses
  `.set-warn` styling (accent-colored, no border, "Mock mode: the month's
  figure is whatever real use last recorded…") — confirms `.set-warn` is a
  plain colored inline sentence, distinct from the bordered `.set-feedback`
  callout card.
- **Dream cards can carry more than one tone chip.** Both dreams in
  `dreams.png` show two pills each ("surreal" + "anxiety," "nightmare" +
  "anxiety"), not the single chip `02-components.md` assumed from the CSS
  alone — `.dream-tone` is a repeatable tag, not a single classification.
- **A session view opens with a short italic summary before the
  transcript** (`history.png` — "After work, Jordan met Mika for a run at
  Greenwood Park…"). It's dim, italic, and *not* in `--you` color, so it
  reads as distinct from both `.session-part` (the actual written content)
  and the `.carried` variant documented in `03-layout-patterns.md` — worth
  checking the actual markup/JS (`history.js`) if this pattern matters for
  new work, since it isn't fully accounted for by the CSS this bundle
  inventoried.
- **Search snippet highlighting confirmed live**: matched terms render in
  bold `--accent` inside the snippet (`search.png`, "bass" highlighted
  across several results) — matches `.search-snip mark` exactly.
- **The `⋯` write-actions menu confirmed in the open state**
  (`write-dropdown.png`): a floating `--bg-raised` card, stacked `.quiet`
  buttons, drop shadow — matches spec.
- **The close-progress checklist, caught mid-run** (`write-progress.png`):
  "UPDATING MEMORY" as the tracked-uppercase label, then five steps —
  the active one prefixed `…`, the rest prefixed `·`. It *looks* like the
  running step is bold and brighter-colored than the pending ones, but I
  pixel-sampled it rather than trust the eye: both are the exact same
  `--accent-dim` (the container's inherited `color`, per
  `#close-progress` in `conversation.css`), and the pending lines' sampled
  pixel matches `0.5 × accent-dim + 0.5 × background` to the integer —
  i.e. it's pure `opacity: .5 → 1`, no color or weight change on the text
  itself, exactly as `02-components.md` § Status/progress already
  documented from the CSS alone. Confirmation, not a correction — but the
  `…`/`·` marker glyphs are a real detail worth having on record for
  anyone rebuilding this checklist.
- **A live settings save/backup flow, start to finish**
  (`settings-save-1.png` → `settings-save-2.png`): the button's own label
  changes to a present-participle while working ("save a dated zip" →
  "zipping…" — not just disabled/spinner), paired with a `.set-feedback`
  callout that changes character as it resolves: `.working` renders
  italic, dim, no emphasis ("working…"); once done, the card switches to
  upright, full-`--text` copy with the `--accent` left rule brightening,
  reporting a real result ("0.3 MB written to
  `C:\apps\rag-journal\backups\journal_backup_20260902_113207.zip`…").
  Confirms the `.working`/`.ok` split in `02-components.md` § Cards
  exactly, plus a detail not in the CSS at all: the button-label swap is a
  second, independent signal of "in progress" alongside the callout —
  worth carrying into any custom async-button component for the
  Claude Design work.
- **The cost popover, actually open** (`cost-dropdown.png`, updated after
  an initial capture that missed it): a plain two-line card under the nav's
  `◌` icon — `$0.00 · 0 tokens · 0 calls` in full `--text`, then `since
  this journal started, estimated` smaller and dimmer. Confirms
  `.cost-line`/`.cost-note` from the source CSS, and adds a component-
  inventory note: this is a *third*, simpler mechanism for "small card
  anchored under a trigger" alongside the `<details>` menu and the native
  `popover` restart-tooltip — see the new entry in `02-components.md`
  § Disclosure. Three different native mechanisms for the same visual
  idiom is a real consolidation opportunity for a custom popover
  component, not just a documentation nit.

## Confirmed patterns, seen in context

- **Category proposal cards** (`categories.png`): dashed `--accent-dim`
  border, bold italic accent title, small dim meta ("medium confidence ·
  8 weeks"), italic reasoning line, three quiet buttons (confirm/not
  now/dismiss). Matches `.cat-proposal` in `02-components.md` exactly.
- **Entity detail view** (`entities.png`): entity name as italic accent
  serif heading (same treatment as the wordmark, one size down), a row of
  five quiet action buttons plus one `<select>` for retype, then
  chronological observations grouped under uppercase tracked date labels
  in `--accent-dim`. The date-label idiom (`JULY 30, 2026` style) recurs
  identically in dream cards, session history, and here — it's the
  system's one "temporal grouping" convention, worth naming as a
  component (`DateLabel` / eyebrow) in its own right.
- **Triage card** (`triage.png`): the one screen where a heading breaks
  the italic-serif-accent convention — `#triage-card h2` is upright, not
  italic, plain `--text` color, and notably larger (1.4rem) than any other
  in-app heading. This is intentional per the CSS (`font-style: normal`
  override) — triage is the one place the design wants a name to read as
  "a fact being confirmed," not as a styled label.
- **Settings section headers** ("APPEARANCE", "JOURNAL"): uppercase,
  tracked, `--accent-dim`, small, with a full-width bottom border —
  distinct from the italic-accent heading style used for h2 elsewhere.
  Two heading idioms coexist in Settings alone (section eyebrows vs. plain
  serif field labels like "Theme," "Date display") — both are already in
  `02-components.md` but the screenshot makes clear they're visually quite
  different weights, worth keeping distinct rather than consolidating.
- **Segmented control** (`settings.png`, Theme: Auto/Light/Dark): matches
  spec — bordered container, dividers between segments, active segment
  gets `--bg-input` fill + `--accent` text.

Nothing here contradicts `01-tokens.md`–`04-type-and-spacing-scale.md` —
the token/component/layout inventory holds up. The native-chrome gap above
is the one genuine finding that wasn't visible from CSS alone (CSS can't
show you what a browser does when it takes over); everything else in this
file is confirmation plus a few naming opportunities (masthead, DateLabel
eyebrow) rather than corrections.
