# Handoff: Main Character tokens → Figma

Everything on `Main Character Tokens.dc.html`, packaged to build a Figma library: variables (with Dark/Light modes), text styles, and notes for what Figma can't express.

## Contents
- `figma-variables/` — DTCG JSON for Figma's native variable import. One file per collection mode.
  - `Color.Dark.tokens.json`, `Color.Light.tokens.json` → collection **Color**, modes **Dark** (default) and **Light**
  - `Scale.Default.tokens.json` → collection **Scale** (font-size, space, radius, font-family, line-height), single mode. Scopes pre-set so values only show in the relevant pickers.
- `tokens-studio.json` — same values in Tokens Studio format, with Dark/Light `$themes`. Use this instead if you work in Tokens Studio.
- `fonts/cmu-sans-demicondensed.ttf` — install locally before creating text styles. Old Standard TT is on Google Fonts (Figma has it built in).
- `reference/` — the tokens page (screenshot + live `.dc.html`, open in a browser).

## Import
1. Install the CMU font.
2. Figma → Local variables → **Import** → pick `Color.Dark.tokens.json`, then import `Color.Light.tokens.json` into the same collection as a second mode. Import `Scale.Default.tokens.json` as its own collection.
3. Rename modes to Dark / Light; set Dark as default.
4. Create text styles below, binding size and family to variables.

Units: Figma is px-only. The app is rem at 16px root — every value is rem × 16 (e.g. `--font-xs` .8rem = 12.8px). Descriptions keep the rem value.

## Colors (10 semantic names, no primitives layer)
| token | dark | light | role |
|---|---|---|---|
| bg | #211d19 | #faf7f2 | page background |
| bg-raised | #2a2521 | #ffffff | cards, panels, menus |
| bg-input | #322c27 | #f1ece4 | inputs, chips, active nav |
| text | #e8e0d5 | #38322b | primary text |
| text-dim | #a99f92 | #6b6258 | meta, labels, help |
| accent | #d99e5b | #9c6021 | lamp amber — small surfaces only |
| accent-dim | #8a6a44 | #827020 | eyebrows, dashed borders, dates |
| border | #3d362f | #e4dcd0 | hairlines |
| you | #c9b99f | #6a4a2a | user's words in transcripts |
| error | #c96a5a | #a8493a | only error state |

Four light values differ from the repo's `tokens.css` (contrast fixes): text-dim, accent, accent-dim, you. The Figma file should use the values above.

## Text styles
Two families only. Serif = writing; condensed sans = interface.

| style | family | weight | size | line height | extras |
|---|---|---|---|---|---|
| Prose / Body | Old Standard TT | 400 | 17 | 28.05 (165%) | literal, not on the scale |
| Prose / Bold title | Old Standard TT | 700 | 15.2 | 165% | entry titles, category names |
| Wordmark | Old Standard TT | 400 italic | 18.4 | 130% | color accent |
| Heading / Section | Old Standard TT | 400 italic | 16.8 | 130% | color accent |
| Heading / Focal | Old Standard TT | 400 | 22.4 | 130% | triage card only |
| UI / Base | CMU Sans DC | 400 | 15.2 | 130% | |
| UI / Base strong | CMU Sans DC | 600 | 15.2 | 130% | button labels, token names |
| UI / Small | CMU Sans DC | 400 | 13.6 | 145% | chips |
| UI / XS | CMU Sans DC | 400 | 12.8 | 130% | default UI text — the workhorse |
| UI / 2XS | CMU Sans DC | 400 | 12 | 130% | small buttons |
| UI / Eyebrow | CMU Sans DC | 600 | 11.2 | 130% | UPPERCASE, letter-spacing 10% (8% in table headers) |

Full scale (font-size variables): 3xs 11.2 · 2xs 12 · xs 12.8 · sm 13.6 · base 15.2 · md 16.8 · lg 18.4 · xl 22.4.

## Spacing
space-1 4 · space-2 6.4 · space-3 8 · space-4 9.6 · space-5 12.8 · space-6 16 · space-7 19.2 · space-8 24. Fractional px are intentional (they're the app's real rem values); bind auto-layout gap/padding to them rather than rounding. One-off nudges stay literal.

## Radius
sm 6 · base 8 · lg 10 · pill 999 · circle (CSS 50% — use 999 on square frames). Two literals are deliberately *not* tokens: 4px (select/input in settings, code) and 12px (triage card, raised Card).

## Motion (not variable-able — document on the page)
- **lamp-pulse**: opacity 0.25 ↔ 1, 1.6s ease-in-out, infinite, in accent. The only loading idiom — no spinners or skeletons. In prototypes: two variants (25% / 100% opacity) linked with After delay 800ms, Smart Animate, ease-in-out.
- Help caret: transform 0.15s ease. No duration scale.

## Suggested Figma page layout
Mirror the reference page: Color (token/role · dark swatch · light swatch rows + the two transcript sample cards), Typography (two family cards + scale table), Spacing (bars), Radius (5 cards at 2.2rem/35.2px control height), Motion.
