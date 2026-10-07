# Icons → Figma

A local Figma plugin that builds the **Icon** component set from
`icons.figma.json`: the SVGs in `static/icons/`, bundled by `icons.mjs` with
the mapping in `static/js/card.js`. It is a set of icons, not a component of
its own, so other components (the Card's visual slot first) use it from the
file.

## Run it
1. Figma desktop app → open the file with the Main Character variables (in
   the file itself or in an enabled library).
2. Plugins → Development → **Import plugin from manifest…** → pick
   `figma/icons/manifest.json` (once).
3. Plugins → Development → **Main Character: Icons** → choose
   `icons.figma.json` → **Build Icons**.

Run it before the Card plugin, which looks for a set named `Icon` in the
file. Running it again with an `Icon` set already on the page **updates
that set in place**: each variant's vector is redrawn, a use the set lacks
is added, and a variant no longer in `icons.figma.json` is left alone and
listed in the log. The components stay the same nodes, so every instance
(in cards, and in other files once you publish) picks up the change. Any
override on an icon's vector inside an instance is reset. To keep an old
copy, rename it first.

## Fit icons
An Icon instance bigger or smaller than 24 (the Card's are 24.5–46 px) should
show its vector scaled to match. Figma does that when you resize an
instance, but not when you switch a resized instance to another variant
(set a card's icon to outline): the new vector keeps its 24 px size and
position, so it sits up and to the left. **Fit icons**, the plugin's second
button, needs no file. It checks every Icon instance on the current page
(including ones nested in cards and in the Card set's own variants) against
where its main's vector draws, scaled (render bounds, the pixels, not just
the box). Figma won't let a plugin move a layer inside an instance, so for
one that's off it resizes the instance a pixel and back, which has Figma lay
the vector out again from the main. The log lists every icon with its
numbers: fixed, in place, or still off. Run it again after switching icons. It knows an icon by its shape (a `for` × `style`
variant holding one vector named `icon`), not its set's name, since a file
using the library has the name last published (once `Card icon`). When it
finds none, it lists the instances it did find.

## The set
Variants: `for` × `style` (filled, outline). `for` is what the icon stands
for, not its file: the seven card types with an icon (summary, category,
dream, favorite, place, project, thing), the ten built-in categories, the
eight dream tones and the five thing categories. Each variant's description
names its group and file. A thing category without its own icon (other)
uses `thing`.

Each icon is one flattened vector named `icon`, 24 × 24, painted with the
file's `text-accent` variable (found by name; `#d99e5b` if there is none)
and set to scale with its instance. The outline icons' strokes are outlined
first, so a larger icon gets thicker lines, as the web's CSS mask does. A
path an SVG doesn't paint (a `fill="none"` helper) is dropped.

Every shape is put back exactly where the SVG drew it before flattening:
an outline onto its stroke's render bounds, a filled shape onto its bounds
from before it left its `<g>`. The first real build put the stroked outline
icons (all TDesign, and the only ones that are outlined or grouped) up and
to the left. The log says how far it moved them.

## Keeping it in step
- `icons.figma.json` is generated: after changing an icon or the mapping in
  `card.js`, run `node figma/icons/icons.mjs`. `--check` exits 1 while it is
  stale, and `node figma/icons/test.mjs` fails.
- `node figma/icons/test.mjs` builds the set against the stand-in Figma API
  in `figma/components/test/fake-figma.mjs`. `figma/card/test.mjs` builds it
  too, before the Card.
