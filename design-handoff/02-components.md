# Component Inventory

Every reusable UI pattern in rag-journal, grouped by kind. Class name, where it's defined, what it looks like, and its states. Everything uses the tokens from `01-tokens.md` — no component below introduces its own color.

## Buttons

Two button "voices," no more:

- **`button.send`** (`conversation.css:22`) — the primary/affirmative action. Solid `--accent` fill, `--bg` text (i.e. dark text on the amber fill in dark mode), no border, `10px` radius, `0 1.2rem` padding, UI-sans font at `.95rem`. `:disabled` → `opacity: .45`. Used for: save entry, send, search, triage apply, settings save. There is exactly one of these per screen/composer at a time — it's reserved for *the* primary action.
- **`button.quiet`** (`conversation.css:27`) — everything else. Transparent background, `1px solid var(--border)`, `--text-dim` text, `8px` radius, `.3rem .7rem` padding, `.8rem` UI-sans. This is the workhorse — cancel, clear, undo/redo, menu items, triage actions, category ops, settings restart/seed. Hover states are mostly implicit (browser default) except where a component overrides (e.g. entity list buttons darken to `--bg-raised` on hover).
- **Icon-only round buttons**: `#cost-toggle` (nav, opacity-based hover/active) and `.set-info` (`settings.css:104`, a `?` in a circular `1.35rem` button, `border-radius: 50%`) — same quiet-button DNA, just circular and content-less save one glyph.

No `button.danger`/destructive variant exists — delete actions (entity delete, category ×) currently render as plain `.quiet` buttons or bare `×` glyphs, relying on the confirm-less-but-undoable safety net rather than a visual warning color. **Ask Claude Design to design a third button voice for this** — `--error` already exists as a token and is otherwise unused on buttons, so it's the natural fill/border color for it. Not just a documentation gap: an upcoming "reprocess journal" action (a real, harder-to-undo operation, unlike the undoable deletes above) will need a properly destructive-looking button, so this variant should land before that button does, not be retrofitted after.

## Inputs

- **Text inputs / textareas** (`conversation.css:16`) — shared styling for `textarea` and `input[type=text]`: `--bg-input` fill, `1px solid var(--border)`, `10px` radius, `.7rem .9rem` padding, no resize handle, focus state is a border color shift to `--accent-dim` (no glow/ring). Every text entry point in the app — the composer, search, filters, triage, settings — uses this one look.
- **Smaller/inline inputs** override padding/width locally rather than via a variant class (e.g. `.grp-newform input`, `.ent-actions input`, `#cat-newform input` each set their own `padding`/`font-size`/`width`) — there's no `input.compact` class, just ad hoc overrides. Worth formalizing into a size variant if new layouts need it.
- **`select`** elements (`.grp-editor select`, `.ent-actions select`, `#suggest-kind`, `#retype-kind`) — same `--bg-input`/`--border` recipe as text inputs, smaller radius (`6px`–`8px`), `.75rem`–`.8rem` UI-sans.
- **Segmented control** — `#theme-toggle` (`settings.css:76`): a row of borderless buttons inside one bordered, radius-clipped container, divided by `1px solid var(--border)` between items, active item gets `--bg-input` fill + `--accent` text. This is the one segmented-control pattern in the app (Auto/Light/Dark) — reusable wherever a small mutually-exclusive choice needs to sit inline.
- **Checkbox rows** — plain native checkboxes with a label, e.g. `#noreply-wrap` (`conversation.css:41`), `.ent-check` (`entities.css:103`, `accent-color: var(--accent)` to tint the native control). No custom checkbox skin exists — the native control is tinted and left alone.
- **`label.set-check`** (`settings.css:56`, confirmed in `screenshots/settings-categories.png`) — a richer checkbox row, one per life-domain category under Settings → Categories, generated into `#set-categories-control` (a `.set-control` wrapper repurposed to hold a stack of these instead of its usual single select/input). Each row is `align-items: flex-start` (not centered) so a multi-line definition can wrap under the label without the checkbox drifting off-baseline: bold `--text` category name (`work`, `relationships`, …) inline with a `--text-dim` dash-separated definition, `--font-xs`, checkbox nudged down `.15rem` to meet the first text line rather than centering on the full block. This is a second, more content-heavy checkbox pattern alongside the plain `#noreply-wrap`/`.ent-check` one above — reach for this version when the row needs a name + explanation, not just a label.

## Chips / pills

One visual family, several use cases, no shared base class (each context defines its own — worth consolidating):

- **`.chip`** (`entities.css:65`) — `--bg-input` fill, full pill radius, `.1rem .6rem`, optional trailing `×` button.
- **`.chip-toggle`** (used as a modifier alongside `.quiet`, e.g. `#flt-unreviewed`, `#search-mode`) — `.on` state fills `--accent-dim` with `--bg` text. This is the toggle-chip pattern: a `.quiet` button that can be "pressed."
- **`.cat-chip`** (`categories.css:6`) — pill, `--border` outline, `.active` → `--accent` outline + text. `.custom` modifier gets a dashed border. Label includes a live count (`emotional 30`, `family 3`) — the count is part of the chip text, not a separate badge element.
- **`.kw-chip`** (`categories.css:52`) — smaller pill (keyword tags inside a category), `.member` modifier tints it accent.
- **`.dream-tone`** (`dreams.css:15`) — same pill shape, `--bg-input` fill, `--accent` text, used for a single descriptive word (e.g. "uneasy").

All chips share: pill radius (`999px`), `.7rem`–`.85rem` UI-sans, and a binary "neutral / accent-emphasized" state model. A future `Chip` component in the design system should unify these into one base + modifiers (`active`, `dashed`, `removable`) rather than the five parallel near-duplicates that exist today.

## Inline tag editor (category-tagged entry list)

Confirmed in `screenshots/categories.png` — selecting a category chip reveals a chronological list of its tagged entries below the domain summary card, and each entry doubles as a live tag editor, not just a display:

- **`.cat-entry`** (`categories.css:12`) — one row per entry, `1px solid var(--border)` bottom rule only (no card chrome — this is a list, not a stack of cards), `.7rem 0` padding.
  - `h3` — `date — title` (e.g. "September 6, 2026 — Mom's visit"), `--font-base`, `600` weight, clickable (hover → `--accent`) to expand the full entry text inline (`.cat-full`, `categories.css:21` — `--bg-input` fill, `--radius-base`, appends below the row; holds a `.cat-summary` sub-block, italic `--text-dim` with a bottom rule, if the entry has an AI summary).
  - `.cat-ev` — one italic `--text-dim` line: the model's stated reason this entry matches the selected category (e.g. "Mom's Labor Day weekend visit").
  - `.cat-tags` — a row of the shared **`.chip`** component (same class as entities' chips, `entities.css:65`), one per category this entry carries, each with its usual trailing `×` to untag; followed by a native `<select class="cat-add">` styled to read as a "+ tag…" affordance (its own first `<option>`) rather than a real dropdown control — choosing an option tags the entry immediately, no separate confirm step. This is a native `<select>` doing double duty as an "add" button, distinct from every other `select` in `02-components.md` § Inputs, which are always genuine multi-choice fields.

This is the one place in the app where tags are edited inline, per-item, in a list context — distinct from the category page's own top-level chip row (`.cat-chip`, browse/filter) and from entity tagging (`02-components.md` § Chips' `.chip`/`.chip-toggle`). Worth naming as its own small pattern (`TagEditor`: chip list + trailing add-affordance) if Claude Design wants to reuse the "removable tags on a list item" idiom elsewhere.

## Cards / panels / callouts

All eight share `background: var(--bg-raised)` — that line is the one thing every one of them agrees on. Everything else varies enough that merging them into a real shared class would mean *deciding*, not just renaming — see the note after the table.

| Selector | Border | Radius | Padding |
|---|---|---|---|
| `.dream-card` (`dreams.css:6`) | `1px solid var(--border)` + `3px solid var(--accent-dim)` left rule | `--radius-lg` (10px) | `var(--space-6) var(--space-7)` |
| `.pattern` (`patterns.css:5`) | `1px solid var(--border)`, plain | `--radius-lg` (10px) | `1rem 1.2rem` |
| `.cat-domain` (`categories.css:25`) | `1px solid var(--border)`, plain | `--radius-lg` (10px) | `var(--space-6) var(--space-7)` |
| `.cat-proposal` (`categories.css:35`) | `1px dashed var(--accent-dim)`, all sides | `--radius-lg` (10px) | `var(--space-5) var(--space-6)` |
| `#cat-paused` (`categories.css:77`) | `1px dashed var(--accent-dim)`, all sides | `--radius-lg` (10px) | `var(--space-6) var(--space-7)` |
| `#triage-card` (`entities.css:146`) | `1px solid var(--border)`, plain | `12px` (raw — the one card bigger than the norm, deliberately not tokenized) | `1.3rem var(--space-8)` |
| `#suggest-panel` (`entities.css:81`) | `1px solid var(--border)`, plain | `--radius-lg` (10px) | `var(--space-6)` |
| `.set-feedback` (`settings.css:138`) | `1px solid var(--border)` + `3px solid var(--accent-dim)` left rule | **`--radius-sm` (6px)** — smaller than every other card here | `.55rem var(--space-5)` |

`.set-feedback` is the one card in this set that's visibly smaller-radius than its siblings. Worth a deliberate call from Claude Design: was `6px` intentional (a status callout reads as more compact, less card-like, than content cards) or drift that should join the `10px` norm? Either answer is fine, it just shouldn't be inherited silently.

`.cat-domain`'s content has its own internal pattern, confirmed in `screenshots/categories.png`: an uppercase tracked eyebrow title (`.cat-domain-title`, `--font-2xs`, `--text-dim`, `.05em` tracking — e.g. "FAMILY (3 ENTRIES, THROUGH 2026-09-06)") over a prose body (`.cat-domain-body`, `.92rem`, no font-family override so it inherits the page's `--font-body` serif, not `--font-ui`) — an AI-generated summary of that category's entries. Despite being machine-generated, it reads as *content* (serif) rather than *chrome* (UI-sans), consistent with the serif = "writing" / sans = "machinery" split in `01-tokens.md`.

`.set-feedback` also carries the app's only semantic-status vocabulary: modifiers `.working` (dim italic), `.ok` (left rule → `--accent`), `.error` (left rule + text → `var(--error)`).

**Pattern to name for the design system**: "raised card, `--border` frame, optional `3px` left accent rule (solid) or all-round dashed border (pending/disabled), radius mostly `10px` with one smaller outlier and one larger one" is one component (`Card`) with `tone` (neutral/pending/status) and `size` props, not eight unrelated classes. Unifying the actual CSS selectors means picking answers (does `.set-feedback` grow to `10px`? does `.cat-proposal`'s all-round dash become a left rule instead?) that are Claude Design's to make, not this document's.

## Banners

One full-width, centered-text strip above the header, in the body's flex column (so it pushes content down rather than overlaying it):

- **`#app-banner`** (`base.css:64`) — `--bg` text, `--font-xs`. Hidden by default; the markup lives in `index.html` unconditionally and JS decides the rest. `body.seed-instance` shows it with an `--accent` fill. That one class is the whole condition: as of `JRNL-43` canned replies cannot run against a real journal, so the demo (and the published web demo, which sets the same flag) is the only state the bar has to announce. It previously had two tones, an `--accent-dim` fill for plain mock mode and `--accent` for the demo on top of it; the dim variant no longer has a state to belong to. Its copy is set in `core.js` from `/api/status`, not baked into the markup.

  The banner text is: **"demo journal — sample entries, and the replies are canned. Restart to go back to yours."** (`core.js` also holds a separate plain "mock mode…" string today, but mock-on-real-journal mode is planned for removal — so the demo copy above is the banner text going forward.)

A separate, smaller inline banner exists — **`#seed-banner`** (`conversation.css:100`) — appears on the write tab between the log and the composer, once a new seed-summary candidate is ready to review. Unlike the full-width strip above, it sits inside the normal `46rem` centered column (same width as the composer), has no fill of its own (just `--accent-dim` text on the page background), and holds one line of copy plus three actions: a `button.send` ("edit seed summary") and two `button.quiet` ("download," "upload edited seed"). Smaller and quieter on purpose — it's a nudge about content ready for review, not a mode-of-the-whole-app notice like `#app-banner`.

## Disclosure

- **`<details>/<summary>` as an accordion**, used twice with slightly different skins:
  - `#help .help-sec` (`help.css:7`) — summary styled as the old section heading (italic accent, `1.15rem`), custom `›` marker that rotates 90° on `[open]`, default marker hidden.
  - `#write-actions` (`conversation.css:80`) — summary styled as a `.quiet` button (the `⋯` menu trigger), popover content is an absolutely positioned `.menu` list of `.quiet` buttons with a drop shadow.

  This is the app's native alternative to a JS dropdown/accordion — no custom JS component backs either one.
- **Native `popover`** — `.set-popover` (`settings.css:120`), used for the restart-info tooltip. Fixed position (JS-positioned on `toggle`), `--bg-raised` card, no backdrop tint.
- **`#cost-panel`** (`base.css` — the `◌` cost-toggle icon in the nav, confirmed open in `screenshots/cost-dropdown.png`) — a third floating-panel idiom, simpler than the other two: plain `hidden`-attribute toggle (not `<details>`, not the native `popover` API), absolutely positioned under the toggle icon, `--bg-input` fill (a shade quieter than the `.menu`/`.set-popover` pair's `--bg-raised`), holding exactly two lines — `.cost-line` ("$0.00 · 0 tokens · 0 calls," `--text`, tabular numerals so the figures don't jitter as they update) and `.cost-note` ("since this journal started, estimated," smaller, `--text-dim`). Three different floating-panel mechanisms (`<details>`, native `popover`, plain `hidden`) for what are visually the same "small card anchored under a trigger" shape — worth consolidating into one custom popover component in the Claude Design work rather than carrying all three forward.

## Navigation / tabs

- **Top nav** (`base.css:24`) — flat list of `nav button`, `--text-dim` default, `.active` → `--bg-input` fill + `--text`. No underline/indicator style — active state is purely a filled pill-corner rect (`border-radius: 6px`, not full pill).

  **Planned refactor — megamenu.** The flat row is to be regrouped into a megamenu (or equivalent), not carried forward as a widening list of buttons. It's already at eleven tabs (the ten original plus Settings), with a standalone cost-meter icon alongside, and grows further by release (the Publish panel's entry point, Phase 5), and a bar that wide has no phone-width answer. This is the one nav pattern flagged as needing real interaction design rather than just a breakpoint — see `03-layout-patterns.md` § Responsive behavior.
- **Tab panes** — `.tab` / `.tab.active` (`base.css:51`) toggle `display: none` / `flex`; every tab is a full-height flex column.
- **Sidebar list items** (entities, history, session-toc) — a recurring shape: full-width text-left button, `--text` default, hover/selected → `--bg-input` fill, `6px`–`8px` radius, secondary meta line in `--text-dim` at a smaller size directly below. This exact recipe repeats as `.session-item`, `#entity-list .ent`, `.grp-row .grp-btn`, `#session-toc .toc-date` — it's the de facto `ListItem` component.

## Tables

Two ad hoc tables, no shared table styling beyond borrow-from-help: `#help table` (`help.css:26`) — border-collapse, `.decision` variant adds a bottom border per row. Used only in the help tab (keyboard-shortcut and decision-guide tables). Not a component that appears elsewhere yet, but a candidate if new layouts need tabular data.

## Status / progress

- **`.cp-step`** close-pipeline checklist (`conversation.css:58`) — opacity-based state machine: pending (`.5` opacity) → `.cp-running` (full opacity, `--accent` mark, lamp-pulse animation) → `.cp-done` (full opacity, `--accent-dim` mark) / `.cp-failed` (`--you` colored mark). This is the only multi-step-progress UI in the app.
- **`.msg.thinking::after`** (`conversation.css:139`, confirmed in `screenshots/write-companion-loading-response.png`) — the companion-reply loading state: a `···` marker on the pending message bubble, animated with the same `lamp-pulse` keyframe as `.cp-running` above (`1.6s ease-in-out infinite`, opacity `.25` ↔ `1`). No spinner, no skeleton — the app's one "waiting on the model" idiom is this slow breathing fade, reused identically for both async states in the app.
- **`.saved-note`** / **`#triage-toast`** — a transient inline confirmation string in `--accent-dim`/`--accent`, no toast container, no timeout visual — just text that appears/disappears via JS.
- **`.set-warn`** (`settings.css:47`) — an inline warning line, `--accent` text, `--font-xs`, capped `34rem` wide. Same idea as `.saved-note` (plain text, no container, JS-toggled) but for "heads up" rather than "done" — used for the settings restart notice and, in the seed editor (see below), the "you have an unsaved draft" note.

## Composer

The write/chat input pattern (`conversation.css:12`): a `textarea` (or `input`) plus a `button.send` in a flex row, `max-width: 46rem`, centered. The write tab's composer (`.write-composer`, `conversation.css:35`) is the richer variant — stacked column with a controls row underneath containing the send button, a quiet "reflect" button, a checkbox, a saved-note, and the `⋯` menu. Any new "compose something" screen should start from this shape.

## Document editor (seed summary)

Confirmed in `screenshots/write-edit-seed.png` — `#seed-editor` (`conversation.css:113`, markup at `#tab-seed`) is a distinct fourth content shape, not a variant of the composer above: one full-height plain-text document instead of a scrolling log-plus-input. It's a sub-view of write, not a nav tab — there's no top-nav button for it; it's reached via `#seed-banner`'s "edit seed summary" button or the write `⋯` menu, and left via its own `← back to write` button, `#seed-back`.

- **Header** (`#seed-editor-head`) — a `flex-wrap` row: `#seed-back` (fixed, never wraps), `#seed-editor-meta` (flex:1, `--text-dim`, `--font-xs` — "last saved …" timestamp), and `#seed-editor-draftnote` (a `.set-warn`, `hidden` by default, forces its own line via `flex-basis: 100%` when a draft note is showing).
- **Body** — `#seed-editor-text`, one `textarea` set to `flex: 1` so it fills all remaining height and scrolls internally, `line-height: 1.6`. No autosize-to-content behavior like the composer's inputs — this one is a fixed viewport onto a potentially long document, styled with the same shared textarea look from `02-components.md` § Inputs (`--bg-input`, `--border`, `--radius-lg`).
- **Footer** (`#seed-editor-actions`) — another `flex-wrap` row, one `button.send` ("save seed summary" — the single commit point) followed by four `button.quiet` (copy / download / upload / open with Claude), a conditional fifth quiet "discard draft," and an inline `.saved-note` at the end. Five-plus actions in one row is more than any other toolbar in the app (compare the composer's controls row) — this is the pattern to reach for if a new layout needs "one document, several export/import actions, one save."

Same `46rem` centered column as every Pattern A screen (`03-layout-patterns.md`) — this is that pattern's shape with the scrolling content swapped for a single textarea and the composer swapped for a wider action row.

## Native browser UI (in scope to fix in the Claude Design iteration)

Three widgets fall back to native OS/browser rendering the app can't fully control. In dark mode they land close — the `<select>` list is genuinely themed; the `<datalist>` and `title` tooltip only render dark because the OS is set that way — but the parts outside the app's reach (selection highlight, exact type, light-mode/other-browser appearance) aren't guaranteed. **Real replacement components are in scope for the new-layout work** — for full control and cross-theme/browser fidelity, not because anything looks broken in dark mode today:

- **`<select>` options** (`entities-select.png`) — themed: dark rounded container, the app's warm serif option text, not white/black OS chrome. Only the active-row highlight is still OS default (light blue). Every `<select>` shares this — retype, suggest-kind, and the seven in Settings (theme uses a custom segmented control instead).
- **`<datalist>` autocomplete** (the merge fields' name suggestions) — renders dark, but only as an OS dark-mode coincidence; not the app's palette or type.
- **`title`-attribute tooltips** — a native tooltip CSS can't touch at all; renders as a dark rounded box (again, OS dark mode, not the app). The highest-volume case: `index.html` leans on `title="..."` for help text on nearly every icon button and ambiguous control.

See the native-chrome section in `05-visual-notes.md` for the screenshots. CSS reaches only part of each — the `<select>` options, but not the highlight, a `<datalist>` panel, or a `title` tooltip — so full fidelity means custom components: a listbox/combobox for `<select>`, an autocomplete panel for `<datalist>`, a tooltip component for every `title`. The tooltip is the highest-leverage, given how heavily `title` is used.

## Layout-level components

See `03-layout-patterns.md` for the three-pane / centered-column shells these components live inside.
