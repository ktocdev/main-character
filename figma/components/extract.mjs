// SPDX-License-Identifier: AGPL-3.0-or-later
// Writes the Figma specs (specs/*.figma.json) from the live design pages.
//
//   npm install                      once, in figma/components/
//   node extract.mjs                 rewrite every spec
//   node extract.mjs --check         exit 1 if any spec differs from the pages
//   node extract.mjs button badge    only these pages ("variables" too)
//   --base http://localhost:8144     the running app (default 8144); the
//                                    pages are static, nothing is written
//
// Each page is loaded in Chrome twice, dark and light. walk.js turns its
// specimens into layout trees, and the native checkboxes are sampled from a
// screenshot (no style says their colours). A colour is then named by the
// role whose dark *and* light values both match it, among the roles for the
// kind of property it came from: several roles share a value in one theme
// or both (amber-600 is muted text, the accent border, the muted fill and
// the focus ring), so neither theme alone can tell them apart.
//
// It also writes specs/variables.figma.json, the colour variables (ramps,
// roles, platform colours) read from tokens.css and design.css, which the
// plugin syncs into the file's Primitives, Semantic and Platform
// collections. Everything here is generated, never edited: change the CSS
// or the page, then run this again. --check is the drift test.

import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, 'specs');

// In build order: a set must come after any set it holds instances of.
export const PAGES = [
  ['button', 'Button'],
  ['input', 'Input'],
  ['textarea', 'Textarea'],
  ['select', 'Select'],
  ['checkbox', 'Checkbox'],
  ['toggle', 'Toggle'],
  ['badge', 'Badge'],
  ['button-group', 'Button group'],
  ['tooltip', 'Tooltip'],
  ['action-menu', 'Action menu'],
  ['disclosure', 'Disclosure'],
  ['search-bar', 'Search bar'],
  ['chat-bar', 'Chat bar'],
];

const args = process.argv.slice(2);
const check = args.includes('--check');
const bi = args.indexOf('--base');
const base = bi >= 0 ? args[bi + 1] : 'http://localhost:8144';
const only = args.filter((a, i) => !a.startsWith('--') && args[i - 1] !== '--base');

const browser = await chromium.launch({ channel: 'chrome' }).catch(() => chromium.launch());
async function themedPage(theme) {
  const p = await browser.newPage({ viewport: { width: 1400, height: 1000 }, deviceScaleFactor: 1 });
  await p.addInitScript(t => { try { localStorage.setItem('rag_theme', t); } catch (e) {} }, theme);
  return p;
}
const pages = { dark: await themedPage('dark'), light: await themedPage('light') };
const walkSrc = fs.readFileSync(path.join(HERE, 'walk.js'), 'utf8');

// Sample a native checkbox: its well, its rule and (checked) its mark.
async function sampleCheckbox(page, n) {
  const el = page.locator(`[data-figma-cb="${n.cb}"]`);
  const shot = (await el.screenshot({ scale: 'device', animations: 'disabled' })).toString('base64');
  return page.evaluate(async ([src, checked]) => {
    const img = await new Promise(r => { const i = new Image(); i.onload = () => r(i); i.src = 'data:image/png;base64,' + src; });
    const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
    const x = c.getContext('2d'); x.drawImage(img, 0, 0);
    const at = (a, b) => { const d = x.getImageData(a, b, 1, 1).data; return [d[0], d[1], d[2]]; };
    const css = v => `rgb(${v[0]}, ${v[1]}, ${v[2]})`;
    const W = img.width, H = img.height, mid = Math.floor(H / 2);
    const well = at(Math.max(2, Math.round(W * 0.12)), Math.round(H * 0.12));
    // the rule: of the first three columns, the one furthest from the well
    // (the box can sit at a fractional x, putting the page in column 0)
    const dist = v => Math.abs(v[0] - well[0]) + Math.abs(v[1] - well[1]) + Math.abs(v[2] - well[2]);
    const rule = [0, 1, 2].map(i => at(Math.min(i, W - 1), mid)).reduce((a, v) => dist(v) > dist(a) ? v : a);
    const out = { fill: window.__mcPaint(css(well), 'bg'), rule: window.__mcPaint(css(rule), 'border') };
    if (checked) {
      // the mark: the pixel furthest from the fill, inside the rounded
      // corners (where the page shows through)
      let best = null, far = 0;
      const i0 = Math.round(W * 0.2), i1 = Math.round(W * 0.8);
      const all = x.getImageData(i0, i0, i1 - i0, i1 - i0).data;
      for (let i = 0; i < all.length; i += 4) {
        const dd = Math.abs(all[i] - well[0]) + Math.abs(all[i + 1] - well[1]) + Math.abs(all[i + 2] - well[2]);
        if (dd > far) { far = dd; best = [all[i], all[i + 1], all[i + 2]]; }
      }
      // Chrome draws the tick in the unchecked box's well colour
      out.mark = window.__mcPaint(css(best), 'mark');
      // a checked box has no rule of its own: the edge pixel is the fill,
      // or the page through the rounded corner
      out.rule = null;
    }
    return out;
  }, [shot, n.checked]);
}

function* nodes(n) {
  yield n;
  if (n.type === 'instance') yield* nodes(n.tree);
  for (const c of n.children || []) yield* nodes(c);
}

// ---- naming a colour by its role ----
// For each kind of property, the token prefixes it may bind to, in order of
// preference: a rule that matches its own fill (the filled button) is a
// fill; the card title's focus ring is its hover text colour.
const KINDS = {
  text: ['text-', 'platform-highlight-text'],
  bg: ['bg-', 'fill-', 'platform-highlight', 'platform-well'],
  border: ['border-', 'fill-', 'platform-rule'],
  focus: ['focus-', 'border-', 'text-'],
  mark: ['platform-well'],
  shadow: [],
};
function candidates(cat, names) {
  const out = [];
  for (const p of KINDS[cat] || []) for (const n of names)
    if ((p.endsWith('-') ? n.startsWith(p) : n === p) && !out.includes(n)) out.push(n);
  return out;
}
// every paint ({hex, cat}) in a spec tree, keyed by its path
function* paints(o, at = '') {
  if (!o || typeof o !== 'object') return;
  if ('hex' in o && 'cat' in o) { yield [at, o]; return; }
  for (const [k, v] of Object.entries(o)) yield* paints(v, at + '/' + k);
}

async function walkPage(theme, slug) {
  const page = pages[theme];
  await page.goto(`${base}/static/design/${slug}.html`, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
  await page.addScriptTag({ content: walkSrc });
  const res = await page.evaluate(() => window.__mcWalk());
  for (const s of res.sets) for (const v of s.variants)
    for (const n of nodes(v.tree)) if (n.type === 'checkbox') Object.assign(n, await sampleCheckbox(page, n));
  for (const s of res.sets) for (const v of s.variants)
    for (const n of nodes(v.tree)) if (n.type === 'checkbox') delete n.cb;
  return res;
}

// The colour variables: ramps, roles and platform colours, read from the
// rules tokens.css and design.css write (the tokens page loads both).
async function variables() {
  const page = pages.dark;
  await page.goto(`${base}/static/design/tokens.html`, { waitUntil: 'networkidle' });
  return page.evaluate(() => {
    const rules = [...document.styleSheets].flatMap(s => { try { return [...s.cssRules]; } catch (e) { return []; } })
      .flatMap(r => r instanceof CSSStyleRule ? [r] : r.cssRules ? [...r.cssRules] : []).filter(r => r instanceof CSSStyleRule);
    const decls = sel => {
      const out = {};
      for (const r of rules) if (r.selectorText === sel)
        for (let i = 0; i < r.style.length; i++) if (r.style[i].startsWith('--')) out[r.style[i].slice(2)] = r.style.getPropertyValue(r.style[i]).trim();
      return out;
    };
    const dark = decls(':root'), light = decls(':root[data-theme="light"]');
    const primitives = {}, semantic = {}, platform = {};
    const step = v => (/^var\(--([a-z]+)-(\d+)\)$/.exec(v) || []).slice(1).join('/');
    for (const [k, v] of Object.entries(dark)) {
      const m = /^([a-z]+)-(\d+)$/.exec(k);
      if (m && /^#[0-9a-f]{6}$/i.test(v)) primitives[`${m[1]}/${m[2]}`] = v.toLowerCase();
    }
    for (const [k, v] of Object.entries(dark)) {
      if (/^platform-/.test(k)) { platform[k.slice(9)] = { dark: v.toLowerCase(), light: (light[k] || v).toLowerCase() }; continue; }
      const m = /^(bg|text|fill|border|focus)-(.+)$/.exec(k);
      if (!m || !step(v)) continue;
      semantic[`${m[1]}/${m[2]}`] = { dark: step(v), light: step(light[k] || v) };
    }
    return { primitives, semantic, platform };
  });
}

function write(file, data, label) {
  const text = JSON.stringify(data, null, 1) + '\n';
  if (check) {
    // git may check the file out with CRLF (core.autocrlf); compare the text
    const old = fs.existsSync(file) ? fs.readFileSync(file, 'utf8').replace(/\r\n/g, '\n') : '';
    if (old !== text) { console.log(`${label}: differs from ${path.relative(process.cwd(), file)}`); return false; }
    console.log(`${label}: ok`);
  } else {
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(file, text);
  }
  return true;
}

let failed = false;
if (!only.length || only.includes('variables')) {
  const v = await variables();
  const spec = {
    $comment: "Generated by figma/components/extract.mjs from static/css/tokens.css and static/design/design.css. Do not edit. The plugin syncs these into three collections: Primitives (the ramps, one mode, hidden from the pickers), Semantic (the roles, Dark and Light modes, each an alias to a ramp step) and Platform (the browser's colours, Dark and Light, plain values).",
    kind: 'variables',
    ...v,
  };
  if (!write(path.join(OUT, 'variables.figma.json'), spec, 'variables')) failed = true;
  else if (!check) console.log(`variables: ${Object.keys(v.primitives).length} primitives, ${Object.keys(v.semantic).length} roles, ${Object.keys(v.platform).length} platform`);
}
for (const [slug, title] of PAGES) {
  if (only.length && !only.includes(slug)) continue;
  const res = await walkPage('dark', slug);
  const lit = await walkPage('light', slug);
  for (const w of res.warnings) console.warn(`${slug}: ${w}`);

  // pair each dark paint with the same paint in light, then name its role
  const lightAt = new Map(paints(lit.sets));
  const names = Object.keys(res.colorTokens);
  const used = new Set();
  for (const [at, p] of paints(res.sets)) {
    const q = lightAt.get(at);
    if (!q) console.warn(`${slug}: ${at} has no light twin; matched on dark alone`);
    const hit = candidates(p.cat, names).find(n => res.colorTokens[n] === p.hex && (!q || lit.colorTokens[n] === q.hex));
    if (hit) { p.token = hit; used.add(hit); }
    else if (p.cat !== 'shadow') console.warn(`${slug}: no role for ${p.cat} ${p.hex}${q ? ' / ' + q.hex : ''} at ${at}`);
    delete p.cat;
  }
  const fallback = {};
  for (const t of [...used].sort()) fallback[t] = res.colorTokens[t];

  const spec = {
    $comment: `Generated by figma/components/extract.mjs from static/design/${slug}.html. Do not edit: change the CSS or the page and run the extractor again. Lengths are px at ${res.remPx}px per rem; {px, token} binds to the variable of that token, a bare number is a literal. Colours are {hex, token?, opacity?}: the token is the role whose dark and light values both match, among those for the kind of property it paints; hex is the dark value, used when the file has no variable by that name.`,
    name: title,
    order: PAGES.findIndex(p => p[0] === slug),
    page: `static/design/${slug}.html`,
    remPx: res.remPx,
    fonts: { ui: 'CMU Sans Serif Demi Condensed', serif: 'Old Standard TT', mono: 'Roboto Mono' },
    fallback,
    sets: res.sets,
  };
  if (!write(path.join(OUT, `${slug}.figma.json`), spec, slug)) failed = true;
  else if (!check) console.log(`${slug}: ${res.sets.map(s => `${s.name} ${s.variants.length}`).join(', ')}`);
}
await browser.close();
if (failed) { console.log('Specs are out of date: run `node extract.mjs` and commit the result.'); process.exit(1); }
