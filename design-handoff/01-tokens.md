# Tokens

Source: `static/css/tokens.css`, `static/css/base.css:1-37`.

All nine semantic tokens are defined once and reused everywhere — no
component CSS file declares a raw color for a themeable surface. `color`
declarations elsewhere in the app are one of these nine, always via `var()`.

## Theme model

Dark is the default palette, not a toggle-on extra:

- No `data-theme` attribute + OS prefers dark → dark (the `:root` block).
- No `data-theme` attribute + OS prefers light → light (the
  `@media (prefers-color-scheme: light) :root:not([data-theme="dark"])`
  block).
- `data-theme="light"` or `data-theme="dark"` on `<html>` → that theme,
  full stop, regardless of OS. Set by a settings toggle
  (`#theme-toggle` in Settings) via `localStorage`, applied before first
  paint by an inline script in `<head>` so there's no flash of the wrong
  theme.

`color-scheme: light dark` on `:root` lets native controls (scrollbars,
date/time inputs, select popups) follow suit automatically.

## Color tokens

| Token | Dark (default) | Light | Role |
|---|---|---|---|
| `--bg` | `#211d19` (warm charcoal, deliberately not pure black) | `#faf7f2` | page background |
| `--bg-raised` | `#2a2521` | `#ffffff` | cards, panels, dropdown menus — one step up from `--bg` |
| `--bg-input` | `#322c27` | `#f1ece4` | text inputs, textareas, chips, pressed/active nav state |
| `--text` | `#e8e0d5` | `#38322b` | primary text |
| `--text-dim` | `#a99f92` | `#8c8378` | secondary/meta text — timestamps, labels, help copy |
| `--accent` | `#d99e5b` ("lamp amber") | `#b5732f` | the app's one accent hue — links-as-color, active states, the send button, headings styled as italic accent text |
| `--accent-dim` | `#8a6a44` | `#d9b183` | quieter accent — section labels, borders that should read as "accent family" without shouting |
| `--border` | `#3d362f` | `#e4dcd0` | all hairline borders/dividers |
| `--you` | `#c9b99f` | `#6b5d49` | the color of the user's own words in a conversation (`.msg.you`, `.session-part`) — distinct from `--text` so a transcript visually separates "you" from "companion" without needing a chat-bubble layout |

Note the light palette isn't `--bg`/`--text` inverted 1:1 — `--accent` and
`--accent-dim` swap emphasis (light mode's `--accent-dim` is *lighter* than
its `--accent`, the reverse of dark mode), because in a bright cream
background the muted amber needs to carry more weight to stay legible.

### Two colors that escaped the system

- `#6b5b3e` — `base.css:73`, fallback value in
  `background: var(--accent-dim, #6b5b3e)` for the mock-mode banner. Only
  matters if `--accent-dim` is ever undefined, which it never is in practice.
  Safe to drop, or worth folding into the accent scale as a documented
  fallback.
- `#c96a5a` (a muted terracotta red) — `settings.css`, used 3× for
  `.set-note.error` and `.set-feedback.error` / `.set-feedback.error`. This
  is the *only* error/danger color anywhere in the app. It has no token and
  no light-mode variant defined (it's used as-is in both themes). If
  Claude Design is proposing new layouts with any kind of destructive or
  error state, this is the color to formalize as `--error` (and give it a
  light-mode counterpart).

## Typography tokens (implicit — not variablized)

There are no `--font-*` custom properties; font stacks are written inline
per rule. Two families:

- **Body / prose**: `'Old Standard TT', Georgia, 'Times New Roman', serif`
  — `base.css:15`, the default `body` font. Used for journal entries,
  companion replies, help prose — anything meant to read as writing.
- **UI chrome**: `'CMU Sans Demi Condensed', system-ui, sans-serif` — used
  explicitly on nearly every interactive/meta element: nav buttons, status
  text, all buttons (`.send`, `.quiet`), chips, labels, table meta, cost
  panel, triage help, settings rows. This is the tell for "this is
  interface, not journal content."

The split is the core typographic idea of the app: serif = your words and
the companion's words; condensed sans = the machinery around them. Any new
component should pick one of these two, not introduce a third family.

## Motion

One keyframe animation exists, `lamp-pulse` (`conversation.css:110`):

```css
@keyframes lamp-pulse { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
```

Used at `1.6s ease-in-out infinite` for: the "companion is thinking" `···`
indicator (`.msg.thinking::after`) and a running step in the close-pipeline
checklist (`.cp-running .cp-mark`). It's the app's one motion idiom — a slow
breathing fade in the accent color — standing in for "something is
happening, wait." New async-state UI should reuse this rather than
inventing a spinner.

Transitions elsewhere are simple, un-tokenized, one-off
`transition: transform .15s ease` (help disclosure caret) — there's no
motion-duration scale to speak of.
