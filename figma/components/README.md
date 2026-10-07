# Components → Figma

A local Figma plugin that builds the Main Character component library from
the design system pages (`static/design/`). There's one plugin for all
fourteen components. It reads specs that are **generated from the live pages**
rather than written by hand, so the Figma side can't drift from the CSS
without `--check` saying so.

```
static/design/*.html ──extract.mjs + walk.js──▶ specs/*.figma.json ──code.js (plugin)──▶ Figma
       (the app's own CSS)        (Chrome)          (committed)                component sets
```

Card and Icon are the exceptions. Each has its own plugin: `figma/card/` (a
hand-kept spec) and `figma/icons/` (generated from `static/icons/`).

## Run it in Figma
1. Open the Figma desktop app and the Main Character file.
2. Plugins → Development → **Import plugin from manifest…** → pick
   `figma/components/manifest.json`. You only do this once.
3. Plugins → Development → **Main Character: Components** → choose the specs
   in `figma/components/specs/` (select them all for the whole library,
   `variables.figma.json` included) → **Build components**.

`variables.figma.json` is the colour palette from `tokens.css`. With it
chosen, the plugin first creates or updates three collections, by name:

| collection | modes | holds |
|---|---|---|
| Primitives | Value | the ramps, `umber/950`, `amber/600`, `rust/500`…; hidden from the pickers |
| Semantic | Dark, Light | the roles, `bg/page`, `text/accent-muted`, `fill/accent`…; each an alias to a ramp step, scoped to fills, text or strokes |
| Platform | Dark, Light | the browser's own colours the select and checkbox pages draw: `highlight`, `highlight-text`, `rule`, `well` |

It never deletes a variable, and this is the only variable writing it does.
The old ten-name Color collection (`bg`, `accent-dim`…) can stay or go:
nothing binds to it once Semantic exists. Choose the variables spec alone to
sync the palette without building anything.

The plugin builds the sets in dependency order. Button comes before the
Search bar that holds a Button instance, and so on, whatever order you pick
the files in. The log lists every token it bound and the variable it bound
to, preferring the Semantic and Platform collections over any other of the
same name. Anything it couldn't find stays a literal and is listed as
missing. If a set's name is already taken, the new set gets " (import)" and
the old one is left alone.

## What it builds
23 sets from 14 pages. Each variant's name is its page caption.

| page | sets | variants | properties |
|---|---|---|---|
| button | Button | kind × size × state | label, show return |
| input | Input | size × state | value |
| textarea | Textarea | state | value |
| select | Select · Select list | kind × state · kind | value · option 1–3, show option 3 |
| checkbox | Checkbox · Check inline · Check row · Checkbox fieldset | checked · checked · kind × checked × state · kind | label · text |
| toggle | Toggle | tone × pressed × state | label |
| badge | Badge | tone × size | label |
| button group | Chip · Filter row · Jump pills · Segmented | kind × selected × state · selected · action · selected | label, count, show count · option 1–3 |
| tooltip | Tooltip | lines | text |
| action menu | Menu item · Menu · Action menu | state · items · open × state | label |
| disclosure | Disclosure | open | title, body |
| search bar | Search bar | state × mode | query, mode |
| chat bar | Chat bar | state × smart | message |
| modal | Modal | actions | eyebrow, title, body |

Composite sets use **instances** of the simpler ones wherever the page marks
it, so a change to Button carries through:

- Check row and Check inline hold a Checkbox.
- Checkbox fieldset holds Check rows.
- Filter row and Jump pills hold Chips; Jump pills also holds a text Button.
- Menu holds Menu items, and an open Action menu holds a Menu.
- Search bar holds a send-pill Button.
- Chat bar holds a Toggle and two Buttons.
- Modal holds one or two Buttons.

An instance's text, as the page has it, is an override on the instance.

Layer names are the CSS classes (`chat-bar-row`, `controls`, `mark`).
Padding, gaps, radii and type sizes that equal a token are bound to it.
Colours bind to roles, never ramp steps. Several roles share a value
(amber-600 is muted text, the accent border, the muted fill and the focus
ring), so the extractor walks every page in dark and in light, and names
the role whose two values both match, among the roles for that kind of
property: text colour, fill, rule or outline. Values that aren't on the scale (the bars' 12px radius, the 17px body
size) stay literal, as they are in the CSS.

## Keeping it in step
The specs are generated, so don't edit them. Change the CSS or the design
page, then:

```
cd figma/components
npm install                                  # once (Playwright)
node extract.mjs                             # rewrite specs/ from the pages
node extract.mjs --check                     # exit 1 if specs/ is out of date
node test/run.mjs                            # build every spec against a stand-in Figma API
node test/render.mjs [out-dir]               # ...and draw what was built, to eyeball
```

The extractor and the render need the app running. Both default to
`http://localhost:8144`; pass `--base` for another port. They only load the
static design pages.

- `test/run.mjs` builds every spec with `test/fake-figma.mjs`. That's a
  stand-in that throws where Figma's API throws: a child filling a parent
  that hugs that axis, text changed before its fonts are loaded, variants
  naming different properties, and a property reference to a property the
  set doesn't have. The file starts with the variables
  from `../main-character-figma-fonts-v3/figma-variables` (the old colour
  names among them), then `variables.figma.json` syncs the three
  collections into it. It also fails if a role isn't an alias to its ramp
  step in both modes, or if any colour binds outside Semantic and Platform.
- `test/render.mjs` draws the built nodes back as HTML (auto layout as
  flexbox) and screenshots each set, so you can hold it up against the
  design page.

Neither replaces a first look in Figma: the plugin has only run against the
stand-in.

## Marking up a page
The extractor builds what the page marks:

| attribute | on | means |
|---|---|---|
| `data-figma-set="Button"` | a specimen | this element is one variant of that set |
| `data-figma-variant="kind=send, state=hover"` | a specimen or an instance | the variant (every variant of a set names the same properties) |
| `data-figma-instance="Button"` | an element inside a specimen | an instance of that set, not a drawing of it |
| `data-figma-text="label"` | any element | the first text inside it is a TEXT property |
| `data-figma-bool="show count"` | any element | its visibility is a BOOLEAN property |
| `data-figma-name` / `data-figma-skip` | any element | a layer name / leave it out |

Hover and focus are captured from the `.is-hover`, `.is-focus`,
`.is-focus-visible` and `.is-focus-within` twins that `design.js` makes of
the app's own state rules. Add a page to `PAGES` in `extract.mjs` (in
dependency order) and in `static/design/design.js`.

## Not modelled
- **The select's open list.** The browser draws it off the page, so the
  Select list set is built from a drawing on the select page. Its highlight,
  highlight text and rule are Chrome's colours on Windows, sampled in both
  themes: the `--platform-*` tokens in `static/design/design.css`, bound to
  the Platform collection.
- **The checkbox's own colours.** It's the browser's checkbox
  (`accent-color` is its only styling), so the extractor samples it from a
  screenshot in each theme. Unchecked, it's a `platform-well` well in a
  `platform-rule` rule; checked, the fill binds to `fill-accent` and the
  tick to `platform-well` (Chrome draws it in the well's colour). The tick
  is drawn approximately, as is the select's chevron.
- **Shadows.** The four `--shadow-*` tokens are whole box-shadows, not
  colours, so they stay literal effects, in their dark values.
- **Behaviour.** Tooltip placement, the pulse animation, and the search
  bar's controls wrapping under the query below about 28rem. In Figma, place
  or resize the instance.
- **Text in a resized row.** Text that ends a row is set to fill it, so it
  wraps when the instance is made narrower. Other text keeps its width.
