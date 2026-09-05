# Layout Patterns

rag-journal is a single `index.html`, one app shell, eleven tabs. Every tab's content area resolves to one of the layout shapes below.

## App shell

```
body (flex column, 100dvh)
├─ #app-banner                       (conditional, full-width strip — mock/demo)
├─ header (flex row, baseline-aligned)
│   ├─ h1 "main character" (masthead position, styled as italic accent serif)
│   ├─ .status
│   └─ nav (pushed right via margin-left: auto)
│       ├─ tab buttons ×11
│       └─ #cost-toggle + #cost-panel (absolutely positioned popover, nav is position:relative)
└─ main (flex: 1, overflow: hidden)
    └─ section.tab × N   (only .active one is display:flex, rest display:none)
```

Header and nav never scroll; `main` clips overflow and hands scrolling down to whichever pane inside the active tab needs it. This is why every tab's own top-level container repeats `overflow-y: auto` — the scroll boundary is deliberately pushed as deep as possible so the header/nav never move.

## Pattern A — Centered reading column

Used by: write, chat, search, categories, patterns, dreams, settings, help, triage.

```
.tab.active (flex column, full height)
└─ #<pane> { max-width: 46rem; margin: 0 auto; width: 100%; padding: 1.5rem; overflow-y: auto; }
```

A single scrolling column, capped at `46rem` (~736px) and centered, `1.5rem` padding. This is the dominant shape in the app — anything that's "read/write one thing at a time" uses it. The composer (where present) sits outside this scroll region, pinned at the bottom of the flex column, same `46rem` max-width so its edges line up with the content above it.

Triage (`#triage`) is a variant: same centered column but narrower (`44rem`) and built around one focal `#triage-card` rather than a scrolling list.

## Pattern B — Three-pane

Used by: entities, history (search looks similar but is Pattern A).

```
#<pane>-pane (flex row, flex:1, overflow:hidden)
├─ list/sidebar   (fixed width ~19-21rem, border-right, own overflow-y:auto)
├─ detail/view    (flex:1, own overflow-y:auto, inner content re-capped e.g. 46rem centered)
└─ toc/rail       (optional, fixed width ~11rem, border-left, hidden below 900px)
```

- **Entities** (`#entities-pane`): `#entity-list` (21rem) + `#entity-detail` (flex:1) — no third rail. Both entities and history are also the app's most compact list-item screens; every filter chip / sort control lives stacked at the top of the left pane before the scrolling list starts.
- **History** (`#history-pane`): `#session-list` (19rem) + `#session-view` (flex:1, inner content re-capped to 46rem) + `#session-toc` (11rem, drops out entirely under 900px via media query — it's a nice-to-have, not core).

This is the pattern to reach for if a new layout needs "browse a list, read/edit the selected one," optionally with a third "jump to a section within the current item" rail.

## Composer-anchored column (write/chat specific)

A refinement of Pattern A where the scrolling log and the input are two separate flex children of the same tab, not one scrolling document:

```
.tab.active
├─ #write-log / #chat-log   (flex:1, overflow-y:auto, 46rem centered)
├─ (write only) #seed-banner, #close-progress   — conditional, same 46rem centering, sit between log and composer
└─ .composer / .write-composer   (fixed at bottom, 46rem centered, does not scroll)
```

The messages themselves (`.msg`) are not chat bubbles — no background, no per-message card. They're differentiated purely by a small-caps uppercase label (`.msg::before`, "you" / "companion", optionally with a timestamp) in `--accent-dim`, and by text color (`--you` for the user's lines vs `--text` for the companion's). This keeps the transcript reading like a manuscript rather than a messaging app — worth preserving explicitly if Claude Design is tempted to bubble-ify it.

## Pattern D — Full-height document editor (seed summary)

Used by: the seed-summary editor only (`#tab-seed`), confirmed in `screenshots/write-edit-seed.png`.

```
.tab.active
└─ #seed-editor { flex:1; display:flex; flex-direction:column; max-width:46rem; margin:0 auto; padding:1.5rem; }
    ├─ #seed-editor-head   (flex row, wraps: back button + meta + optional inline warning)
    ├─ #seed-editor-text   (textarea, flex:1, scrolls internally — the one document)
    └─ #seed-editor-actions (flex row, wraps: 1 send + up to 5 quiet buttons + inline saved-note)
```

A variant of Pattern A's `46rem` centered column, not a new width or shell — the difference is what fills the middle: a single full-height `textarea` acting as the document, instead of a scrolling list/log. It's also the one screen in the app with no nav-tab entry point of its own; it's a sub-view reached from write (via `#seed-banner` or the `⋯` menu) and returned from via its own back button, so it always assumes it was pushed on top of write rather than being a standalone destination. See `02-components.md` § Document editor for the header/footer element breakdown.

## Responsive behavior

The app today is desktop-only; the only responsive rule in the whole CSS surface is `history.css:31` (`#session-toc` disappears under 900px). There is no mobile nav pattern, no stacking of the three-pane layout on narrow viewports, and no breakpoint system.

**This is a gap to close, not a boundary to respect.** Every new layout Claude Design produces — refreshed existing screens, new screens, all of it — should be designed with phone-width-and-up support in mind from the start, not as a desktop layout with mobile bolted on after. Concretely, that means for each pattern above:

- **Pattern A (centered column)** — degrades most easily: drop the `46rem` cap and side padding down to something phone-sized, keep it a single column. Should be the least effortful of the three to take to phone width.
- **Pattern B (three-pane)** — has no mobile answer today at all. Needs a real decision: collapse to one pane with a way back (list → detail drill-in, a back control), not just hide the rail like the existing 900px rule does for the toc. This is the pattern most likely to need actual new interaction design, not just a breakpoint.
- **Composer-anchored column** — the fixed-bottom composer is already close to a mobile-native shape (sticky input above the keyboard); mainly needs the same width/padding treatment as Pattern A.
- **Nav** — eleven tab buttons in a flat top row (the ten original plus Settings), with the cost-meter icon alongside, has no phone-width answer (they'd overflow or wrap badly below ~500-600px), and by release it grows further still (the Publish panel's entry point, Phase 5). Needs a real mobile nav pattern — a megamenu or equivalent grouping, not just smaller buttons or a bare hamburger list.
- **Pattern D (document editor)** — same treatment as Pattern A (it's already that column, just with a textarea instead of a scrolling list); the multi-button footer row is the one thing to watch, since five-plus actions in a `flex-wrap` row will stack awkwardly narrow unless it's given real mobile treatment (icon-only buttons, an overflow menu) rather than left to wrap on its own.

Treat "does this work on a phone" as a question to answer for every new screen, the same way "does this match the existing token/component language" already is — see `README.md` § Scope of "new layouts" for how this fits the three kinds of new-layout work in scope.
