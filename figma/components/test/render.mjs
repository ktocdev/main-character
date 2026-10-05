// SPDX-License-Identifier: AGPL-3.0-or-later
// Builds every spec with the stand-in Figma API, then draws what was built
// as HTML (auto layout as flexbox, which is what it is) and screenshots each
// set, so the result can be held up against the design pages. A check of the
// round trip page -> spec -> Figma nodes, short of opening Figma.
//
//   node test/render.mjs [--base http://localhost:8144] [out-dir]
//
// The fonts come from the running app, so it needs the server up.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { makeFigma } from './fake-figma.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');
const args = process.argv.slice(2);
const bi = args.indexOf('--base');
const base = bi >= 0 ? args[bi + 1] : 'http://localhost:8144';
const out = args.find((a, i) => !a.startsWith('--') && args[i - 1] !== '--base') || path.join(HERE, 'out');
fs.mkdirSync(out, { recursive: true });

const fonts = [
  { family: 'CMU Sans Serif Demi Condensed', style: 'Regular' },
  { family: 'Old Standard TT', style: 'Regular' }, { family: 'Old Standard TT', style: 'Bold' },
  { family: 'Old Standard TT', style: 'Italic' }, { family: 'Roboto Mono', style: 'Regular' },
];
const figma = makeFigma({ fonts, variables: [] });
globalThis.figma = figma;
globalThis.__html__ = '';
new Function(fs.readFileSync(path.join(ROOT, 'code.js'), 'utf8'))();
const specDir = path.join(ROOT, 'specs');
const texts = fs.readdirSync(specDir).filter(f => f.endsWith('.figma.json')).map(f => fs.readFileSync(path.join(specDir, f), 'utf8'));
await figma.ui.onmessage({ type: 'build', specs: texts });
for (const l of figma.logs) if (l.kind === 'error') { console.log(l.text); process.exit(1); }

const rgb = p => {
  const c = p.color, a = p.opacity == null ? 1 : p.opacity;
  return `rgba(${Math.round(c.r * 255)},${Math.round(c.g * 255)},${Math.round(c.b * 255)},${a})`;
};
const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;');
const FAM = { 'CMU Sans Serif Demi Condensed': "'CMU Sans Demi Condensed'", 'Old Standard TT': "'Old Standard TT'" };

function css(n, parent) {
  const st = [];
  const auto = parent && parent.layoutMode && parent.layoutMode !== 'NONE';
  const absolute = n.layoutPositioning === 'ABSOLUTE' || (parent && !auto);
  if (absolute && parent) st.push(`position:absolute;left:${n.x}px;top:${n.y}px`);
  else st.push('position:relative');
  const row = parent && parent.layoutMode === 'HORIZONTAL';
  const sz = (axis, mode, val) => {
    const prop = axis === 'h' ? 'width' : 'height';
    if (mode === 'FIXED') st.push(`${prop}:${val}px;flex-shrink:0`);
    if (mode === 'FILL' && auto) {
      if ((axis === 'h') === row) st.push('flex:1 1 0;min-width:0');
      else st.push('align-self:stretch');
    }
  };
  if (n.type === 'TEXT') {
    const mode = n.textAutoResize === 'WIDTH_AND_HEIGHT' ? 'HUG' : (n.layoutSizingHorizontal === 'FILL' ? 'FILL' : 'FIXED');
    sz('h', mode, n.width);
    if (mode === 'HUG') st.push('white-space:pre');
    else st.push('white-space:pre-wrap');
    if (n.textTruncation === 'ENDING') st.push('white-space:nowrap;overflow:hidden;text-overflow:ellipsis');
    if (n.lineHeight && n.lineHeight.unit === 'PIXELS') st.push(`line-height:${n.lineHeight.value}px`);
    else st.push('line-height:normal');
    if (n.textAlignHorizontal) st.push(`text-align:${n.textAlignHorizontal.toLowerCase()}`);
    return st.join(';');
  }
  sz('h', n.layoutSizingHorizontal || 'FIXED', n.width);
  sz('v', n.layoutSizingVertical || 'FIXED', n.height);
  if (n.layoutGrow) st.push('flex:1 1 0');
  if (n.layoutMode && n.layoutMode !== 'NONE') {
    st.push(`display:flex;flex-direction:${n.layoutMode === 'HORIZONTAL' ? 'row' : 'column'}`);
    st.push(`gap:${n.layoutWrap === 'WRAP' ? n.counterAxisSpacing + 'px ' : ''}${n.itemSpacing}px`);
    if (n.layoutWrap === 'WRAP') st.push('flex-wrap:wrap');
    st.push(`padding:${n.paddingTop}px ${n.paddingRight}px ${n.paddingBottom}px ${n.paddingLeft}px`);
    const J = { MIN: 'flex-start', MAX: 'flex-end', CENTER: 'center', SPACE_BETWEEN: 'space-between' };
    const A = { MIN: 'flex-start', MAX: 'flex-end', CENTER: 'center', BASELINE: 'baseline' };
    st.push(`justify-content:${J[n.primaryAxisAlignItems]};align-items:${A[n.counterAxisAlignItems]}`);
  }
  if (n.minWidth) st.push(`min-width:${n.minWidth}px`);
  if (n.minHeight) st.push(`min-height:${n.minHeight}px`);
  st.push('box-sizing:border-box');
  const fill = (n.fills || [])[0];
  if (fill) st.push(`background:${rgb(fill)}`);
  const stroke = (n.strokes || [])[0];
  if (stroke) {
    const w = ['strokeTopWeight', 'strokeRightWeight', 'strokeBottomWeight', 'strokeLeftWeight'].map(k => n[k] != null ? n[k] : n.strokeWeight);
    const shadows = [];
    if (w[0]) shadows.push(`inset 0 ${w[0]}px 0 0 ${rgb(stroke)}`);
    if (w[2]) shadows.push(`inset 0 -${w[2]}px 0 0 ${rgb(stroke)}`);
    if (w[3]) shadows.push(`inset ${w[3]}px 0 0 0 ${rgb(stroke)}`);
    if (w[1]) shadows.push(`inset -${w[1]}px 0 0 0 ${rgb(stroke)}`);
    if (n.dashPattern) st.push(`outline:${w[0]}px dashed ${rgb(stroke)};outline-offset:-${w[0]}px`);
    else if (shadows.length) st.push(`box-shadow:${shadows.join(',')}`);
  }
  const r = ['topLeftRadius', 'topRightRadius', 'bottomRightRadius', 'bottomLeftRadius'].map(k => n[k] != null ? n[k] : n.cornerRadius || 0);
  if (r.some(Boolean)) st.push(`border-radius:${r.map(v => Math.min(v, 999) + 'px').join(' ')}`);
  if (n.opacity != null && n.opacity < 1) st.push(`opacity:${n.opacity}`);
  if (n.clipsContent) st.push('overflow:hidden');
  const fx = (n.effects || []).filter(e => e.type === 'DROP_SHADOW');
  if (fx.length) st.push(`filter:${fx.map(e => `drop-shadow(${e.offset.x}px ${e.offset.y}px ${e.radius / 2}px rgba(${Math.round(e.color.r * 255)},${Math.round(e.color.g * 255)},${Math.round(e.color.b * 255)},${e.color.a}))`).join(' ')}`);
  return st.join(';');
}

function textHtml(n) {
  // apply the ranges in order onto per-character styles
  const chars = [...n.characters];
  const styles = chars.map(() => ({}));
  const units = n.characters.split('');
  void units;
  for (const r of n.ranges) for (let i = r.s; i < r.e && i < styles.length; i++) {
    if (r.font) styles[i].font = r.font;
    if (r.size) styles[i].size = r.size;
    if (r.fills) styles[i].fill = r.fills[0];
  }
  let html = '', cur = null, buf = '';
  const flush = () => {
    if (!buf) return;
    const s = cur || {};
    const f = s.font || n.fontName;
    const fam = FAM[f.family] || 'serif';
    const st = [`font-family:${fam}`, `font-size:${s.size || 12}px`, `font-weight:${/Bold/.test(f.style) ? 700 : 400}`,
      `font-style:${/Italic/.test(f.style) ? 'italic' : 'normal'}`, s.fill ? `color:${rgb(s.fill)}` : ''];
    html += `<span style="${st.join(';')}">${esc(buf)}</span>`;
    buf = '';
  };
  chars.forEach((c, i) => {
    const k = JSON.stringify(styles[i]);
    if (cur === null || k !== JSON.stringify(cur)) { flush(); cur = styles[i]; }
    buf += c;
  });
  flush();
  return html;
}

function draw(n, parent) {
  if (n.visible === false) return '';
  if (n.type === 'TEXT') return `<div style="${css(n, parent)}">${textHtml(n)}</div>`;
  if (n.type === 'VECTOR') {
    const s = (n.strokes || [])[0];
    return `<svg style="${parent && parent.layoutMode !== 'NONE' ? 'flex:none' : `position:absolute;left:0;top:0`};overflow:visible" width="${n.name === 'chevron' ? 9 : 16}" height="${n.name === 'chevron' ? 5 : 16}"><path d="${n.vectorPaths[0].data}" fill="none" stroke="${s ? rgb(s) : '#000'}" stroke-width="${n.strokeWeight || 1.5}" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
  }
  const kids = (n.children || []).map(c => draw(c, n)).join('');
  return `<div title="${esc(n.name)}" style="${css(n, parent)}">${kids}</div>`;
}

const sets = figma.currentPage.children.filter(n => n.type === 'COMPONENT_SET');
const browser = await chromium.launch({ channel: 'chrome' }).catch(() => chromium.launch());
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
for (const set of sets) {
  const cells = set.children.map(c => `<div style="display:flex;flex-direction:column;gap:6px;align-items:flex-start">${draw(c, null).replace('position:relative', 'position:relative')}<small>${esc(c.name)}</small></div>`).join('');
  const html = `<!doctype html><html><head><link rel="stylesheet" href="${base}/static/css/tokens.css"></head>
<body style="margin:0;background:#211d19;color:#a99f92;font:11px sans-serif"><div id="sheet" style="display:inline-block;padding:24px;max-width:1352px">
<h3 style="margin:0 0 12px;color:#d99e5b;font-family:sans-serif">${esc(set.name)} (as built)</h3>
<div style="display:flex;flex-wrap:wrap;gap:28px 32px;align-items:flex-start;padding-top:${set.name === 'Action menu' ? 190 : 0}px">${cells}</div></div></body></html>`;
  await page.goto(`${base}/static/design/index.html`);
  await page.setContent(html, { waitUntil: 'networkidle' });
  await page.evaluate(() => document.fonts.ready);
  const file = path.join(out, set.name.replace(/\s+/g, '-').toLowerCase() + '.png');
  await page.locator('#sheet').screenshot({ path: file });
}
await browser.close();
console.log(`rendered ${sets.length} sets to ${out}`);
