# Card → Figma

A local Figma plugin that builds the **Card** component set from
`card.figma.json`, the Figma-side copy of `static/css/card.css`. Its icons
come from the file's **Icon** set, which the Icons plugin builds
(`figma/icons/`).

## Run it
1. Figma desktop app → open the file with the Main Character variables (in
   the file itself or in an enabled library).
2. Build the **Icon** set first, if the file doesn't have one: see
   `figma/icons/README.md`. The Card plugin looks for a set named `Icon` on
   this page, then the rest of the file, and stops with a message if there
   isn't one.
3. Plugins → Development → **Import plugin from manifest…** → pick
   `figma/card/manifest.json` (once).
4. Plugins → Development → **Main Character: Card** → choose
   `card.figma.json`. To fill the image variants with a picture, choose one
   too (for example `static/images/sergey-pesterev-tMvuB9se2uQ-unsplash.jpg`);
   without one they get a flat placeholder. Then **Build Card**.

The log lists every token it bound and the variable it bound to. Anything it
couldn't find stays a literal and is listed as missing. It never creates or
edits variables. Running it again makes `Card (import)` if a `Card` set
exists; it doesn't update the old one in place, because swapping in a new
set would break existing instances.

## Card
Eighteen variants: `size` (sm, md, lg) × `orientation` (vertical,
horizontal) × `visual`, which is what the visual starts with.

`card-visual` is a native Figma **slot** (its own `card-visual` property),
so in an instance you can delete what's there and drop in anything: another
icon, a photo, a frame you're trying out. It clips what you drop in, and
suggests the **Icon** set first. On a Figma without slots the plugin
falls back to a plain frame and says so in the log.

| visual | the slot starts with |
|---|---|
| icon | `card-icon`, an **Icon** instance at .9em of the glyph size. It is exposed when Figma allows that inside a slot, so its `for` and `style` pickers show in the card's own properties; if not, the log says so and you select the icon to change it. |
| initials | a text layer, Old Standard bold, dropped .25em (the slot's top padding) so its baseline meets the icons' bottom (`card.css` `.card-visual.text`). Edit the text in the slot: Figma doesn't let slot content be bound to a text property, so there is no `initials` property. |
| image | `card-image`, a rectangle filling the visual. Replace its image fill, or the rectangle, to try a picture (experimental, like the web's `image` option). |

The item type is not a variant. These are component properties:

| property | kind | what it drives |
|---|---|---|
| card-visual | slot | the visual's content (above) |
| type | text | the eyebrow (summary, category, dream category, favorite, person, place, project, thing) |
| title | text | card-title |
| description | text | card-desc (md and lg only; sm has none) |
| show description | boolean | |
| badge 1–4 | text | the badge labels; each size only has its maximum number of slots (sm 2, md 3, lg 4) |
| show badge 2–4 | boolean | |
| more / show more | text / boolean | the plain "+N" after the last badge |

Layer names match the CSS parts (`card-visual`, `card-icon`, `card-image`,
`card-body`, `card-type`, `card-title`, `card-desc`, `card-badges`,
`card-badge`, `card-more`). Vertical variants are at each size's minimum
width; horizontal ones are 46rem wide. Resize an instance to fill its column
the way the CSS grid track does.

## Not modelled
- Hover (`--card-thumb-hover`, the image's dimming, title to
  `--text-accent`) and the focus ring. These could be a `state` variant later.
- Badge ellipsis when a badge is too wide: Figma auto layout can't shrink a
  hugging pill, so the row clips instead.
- The automatic flip to horizontal below 670px: in Figma, pick the variant.

## Keeping it in step
- The icons belong to `figma/icons/`: after changing an icon, follow its
  README. The Card only needs the `Icon` set to have a `for` variant for
  `sample.icon` and the `iconSet` name in `card.figma.json` to match.
- `card.figma.json` repeats the values in `card.css`; change both together.
  Token names are the CSS custom properties without the dashes. If your
  Figma variables use a group name the matcher doesn't know (e.g. `Gaps/4`
  for `space-4`), add it to `STEMS` in `code.js`.
- `node figma/card/test.mjs` builds the Icon set with the Icons plugin and
  then the Card, against the stand-in Figma API in
  `figma/components/test/fake-figma.mjs`, and checks the Card stops with a
  message in a file without the Icon set. The stand-in models slots as
  documented (a direct child of the component, one SLOT property per slot
  name across the variants, no property bindings inside); the slot version
  hasn't been run in real Figma yet.
