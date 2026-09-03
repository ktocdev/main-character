# Observed Type & Spacing Values

`--font-3xs` through `--font-xl` and `--space-1` through `--space-8` are real custom properties in `static/css/tokens.css`, wired into every component file only where a declaration's value was an *exact* match to a named step. The frequency table below is the record of *why* these particular eight-and-eight steps were chosen.

Non-matching values (the long tail described below, e.g. `.72rem`, `.92rem`, `.45rem`, and radius's own `4px`/`12px` one-offs) are left as literals rather than rounded to the nearest step, so no rem/px value in the app changed. Radius is tokenized too (see below).

## Font sizes in use (rem, all `font-size:` or `font:` shorthand declarations)

| rem | px (16px root) | Frequency | Where |
|---|---|---|---|
| .68 | 10.9 | 1 | rare micro-label |
| .7 | 11.2 | 19 | uppercase tracked labels (`.kind`, `.pat-kind`, section eyebrows) |
| .72 | 11.5 | 9 | tight meta (dream-tone, group counts) |
| .75 | 12.0 | 16 | small buttons, meta text |
| .78 | 12.5 | 6 | small meta variants |
| .8 | 12.8 | **34** | **the workhorse size** — nearly every `.quiet` button, status line, meta text |
| .82 | 13.1 | 2 | one-off |
| .85 | 13.6 | 19 | secondary body text, help copy, chip labels |
| .88 | 14.1 | 3 | session item text |
| .9 | 14.4 | 17 | list-item primary text, table text |
| .92 | 14.7 | 4 | domain-doc body, triage obs |
| .95 | 15.2 | 18 | primary UI text, form labels, message text |
| 1 | 16.0 | 2 | set-row labels, cost line |
| 1.05 | 16.8 | 5 | tab-pane `h2` (settings, session head) |
| 1.15 | 18.4 | 3 | header `h1`, help `h2` |
| 1.4 | 22.4 | 1 | triage-card `h2` (the one large focal heading) |

## Named type scale (real, in `tokens.css`)

Covers every value above without moving anything (values in **bold** are exact matches to existing usage; others are the nearest existing value already doing that job):

| Name | rem | Covers |
|---|---|---|
| `--font-3xs` | **.7** | eyebrow/tracked labels |
| `--font-2xs` | **.75** | small buttons/meta |
| `--font-xs` | **.8** | default UI text (buttons, status, meta) — the base size for chrome |
| `--font-sm` | **.85** | secondary body text |
| `--font-base` | **.95** | primary UI text, message/entry text |
| `--font-md` | **1.05** | tab sub-headings |
| `--font-lg` | **1.15** | section headings (header h1, help h2) |
| `--font-xl` | **1.4** | the one focal heading (triage) |

Note the *body prose* size is set separately at the `body` level (`17px/1.65`, `base.css:15`) and isn't part of this UI-chrome scale — prose inside journal entries and companion replies should keep using that, not `--font-base`.

## Spacing values in use (rem, padding/margin/gap)

Most frequent: `.4rem` (37×), `1rem` (23×), `.5rem` (22×), `.8rem` (18×), `.6rem` (19×), `.3rem` (18×), `.25rem` (10×), `.2rem` (10×), `1.5rem` (16×). The long tail (`.05`–`.18rem`, `1.1`–`1.6rem`) is mostly one-off micro-adjustments (icon nudges, a specific gap that needed to be 1px tighter than its neighbor) rather than a second scale — don't treat those as scale steps.

## Named spacing scale (real, in `tokens.css`)

| Name | rem | px | Covers |
|---|---|---|---|
| `--space-1` | .25 | 4 | icon/inline nudges |
| `--space-2` | .4 | 6.4 | tight inline gaps (most common value in the app) |
| `--space-3` | .5 | 8 | compact padding, chip padding |
| `--space-4` | .6 | 9.6 | composer gaps, button rows |
| `--space-5` | .8 | 12.8 | standard component padding |
| `--space-6` | 1 | 16 | section spacing, pane padding |
| `--space-7` | 1.2 | 19.2 | header gaps |
| `--space-8` | 1.5 | 24 | pane outer padding (`padding: 1.5rem` on nearly every tab container), major section margins |

## Radius — now a named scale too

| Value | Frequency | Where | Token |
|---|---|---|---|
| 4px | 2 | `.set-control select/input`, `#help code` | left as a raw literal — one-off, not part of the named scale |
| 6px | 14 | small interactive elements — chips-as-buttons, list-item hover states, segmented control | `--radius-sm` |
| 8px | 8 | default component radius — buttons, cards' close cousins, `.quiet` button | `--radius-base` |
| 10px | 10 | the primary card/input radius — `button.send`, textareas, `.dream-card`, most raised panels | `--radius-lg` |
| 12px | 1 | `#triage-card` only — the one "bigger than normal" card | left as a raw literal — deliberately not merged into `--radius-lg` |
| 50% | 2 | circular icon buttons (`.set-info`, `.step-n`) | `--radius-circle` |
| 999px | 8 | pills/chips | `--radius-pill` |

This is a real 3-tier system: **6px** (small controls), **8–10px** (default components, roughly interchangeable), **pill/circle** (chips and icon buttons), with the two genuine one-offs (`4px`, `12px`) left as literals rather than folded in. See `02-components.md` § Cards: `.set-feedback` uses `6px` (`--radius-sm`) while every other card in that family uses `10px` — a real inconsistency worth a deliberate call.
