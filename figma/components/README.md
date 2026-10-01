# Components → Figma

A local Figma plugin that builds the Main Character component library from
the design system pages (`static/design/`). There's one plugin for all
thirteen components. It reads specs that are **generated from the live pages**
rather than written by hand, so the Figma side can't drift from the CSS
without `--check` saying so.

```
static/design/*.html ──extract.mjs + walk.js──▶ specs/*.figma.json ──code.js (plugin)──▶ Figma
       (the app's own CSS)        (Chrome)          (committed)                component sets
```

Card is the exception. Its plugin and hand-kept spec stay in `figma/card/`.

## Run it in Figma
1. Open the Figma desktop app and the file with the Main Character
   variables (in the file itself or in an enabled library).
2. Plugins → Development → **Import plugin from manifest…** → pick
   `figma/components/manifest.json`. You only do this once.
3. Plugins → Development → **Main Character: Components** → choose the specs
   in `figma/components/specs/` (select them all for the whole library) →
   **Build components**.

The plugin builds the sets in dependency order. Button comes before the
Search bar that holds a Button instance, and so on, whatever order you pick
the files in. The log lists every token it bound and the variable it bound
to. Anything it couldn't find stays a literal and is listed as missing.
Like Card's plugin, it never creates or edits a variable. If a set's name is
already taken, the new set gets " (import)" and the old one is left alone.

One known miss in the current file: there's no variable for `accent-hover`
(the send button's hover, added in JRNL-60). Add a colour variable with that
name and run the plugin again to bind it.

## What it builds
22 sets from 13 pages. Each variant's name is its page caption.

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

Composite sets use **instances** of the simpler ones wherever the page marks
it, so a change to Button carries through:

- Check row and Check inline hold a Checkbox.
- Checkbox fieldset holds Check rows.
- Filter row and Jump pills hold Chips; Jump pills also holds a text Button.
- Menu holds Menu items, and an open Action menu holds a Menu.
- Search bar holds a send-pill Button.
- Chat bar holds a Toggle and two Buttons.

An instance's text, as the page has it, is an override on the instance.

Layer names are the CSS classes (`chat-bar-row`, `controls`, `mark`).
Colours, padding, gaps, radii and type sizes that equal a token are bound to
it. Values that aren't on the scale (the bars' 12px radius, the 17px body
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
  set doesn't have. It uses the variable names from
  `../main-character-figma-fonts-v3/figma-variables`.
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
  Select list set is built from a drawing on the select page. The highlight
  (`#99c8ff`, with `#3b3b3b` text) and the list's rule (`#858585`) are
  Chrome's colours on Windows in dark, not tokens.
- **The checkbox's own colours.** It's the browser's checkbox
  (`accent-color` is its only styling), so the extractor samples it from a
  screenshot. Unchecked, it's `#3b3b3b` in a `#858585` rule, which is right
  only in dark. Checked, the fill binds to `accent`. The tick is drawn
  approximately, as is the select's chevron.
- **Light mode.** Bound colours follow the file's variable modes. Literals
  (the native colours above and the shadows) stay dark.
- **Behaviour.** Tooltip placement, the pulse animation, and the search
  bar's controls wrapping under the query below about 28rem. In Figma, place
  or resize the instance.
- **Text in a resized row.** Text that ends a row is set to fill it, so it
  wraps when the instance is made narrower. Other text keeps its width.
