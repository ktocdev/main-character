# Card → Figma

A local Figma plugin that builds the **Card** component set from
`card.figma.json`, the Figma-side copy of `static/css/card.css`.

## Run it
1. Figma desktop app → open the file with the Main Character variables (in
   the file itself or in an enabled library).
2. Plugins → Development → **Import plugin from manifest…** → pick
   `figma/card/manifest.json` (once).
3. Plugins → Development → **Main Character: Card** → choose
   `card.figma.json` → **Build Card**.

The log lists every token it bound and the variable it bound to. Anything it
couldn't find stays a literal and is listed as missing. It never creates or
edits variables. Running it again makes a new set (`Card (import)` if `Card`
exists); it doesn't update the old one in place, because swapping in a new set
would break existing instances.

## What it builds
Six variants: `size` (sm, md, lg) × `orientation` (vertical, horizontal).
The item type is not a variant. These are component properties:

| property | kind | what it drives |
|---|---|---|
| type | text | the eyebrow (summary, category, dream category, favorite, person, place, project) |
| glyph | text | the visual: ¶ § ☾ ★ ◎ ◇, or a person's initials |
| title | text | card-title |
| description | text | card-desc (md and lg only; sm has none) |
| show description | boolean | |
| badge 1–4 | text | the badge labels; each size only has its maximum number of slots (sm 2, md 3, lg 4) |
| show badge 2–4 | boolean | |
| more / show more | text / boolean | the plain "+N" after the last badge |

Layer names match the CSS parts (`card-visual`, `card-body`, `card-type`,
`card-title`, `card-desc`, `card-badges`, `card-badge`, `card-more`). Vertical
variants are at each size's minimum width; horizontal ones are 46rem wide.
Resize an instance to fill its column the way the CSS grid track does.

## Not modelled
- Hover (`--card-thumb-hover`, title to `--text-accent`) and the focus ring.
  These could be a `state` variant later.
- Badge ellipsis when a badge is too wide: Figma auto layout can't shrink a
  hugging pill, so the row clips instead.
- The automatic flip to horizontal below 670px: in Figma, pick the variant.

## Keeping it in step
`card.figma.json` repeats the values in `card.css`; change both together.
Token names are the CSS custom properties without the dashes. If your
Figma variables use a group name the matcher doesn't know (e.g. `Gaps/4`
for `space-4`), add it to `STEMS` in `code.js`.
