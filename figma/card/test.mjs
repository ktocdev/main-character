// SPDX-License-Identifier: AGPL-3.0-or-later
// Runs code.js against the stand-in Figma API (figma/components/test/
// fake-figma.mjs) with card.figma.json and the sample picture, the way the
// plugin runs in Figma, after the Icons plugin has built its set (as in
// figma/icons/test.mjs). Fails on any error the API would throw, or a set
// that isn't what the spec describes: every card visual a slot holding
// what its variant says. Also checks it stops, with a message, in a file
// with no Icon set.
//
//   node figma/card/test.mjs   (Playwright isn't needed; the fake is plain JS)

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeFigma } from '../components/test/fake-figma.mjs';
import { buildIcons, fitIcons } from '../icons/test.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = f => fs.readFileSync(path.join(HERE, f), 'utf8');

let failed = false;
const fail = m => { console.log('FAIL ' + m); failed = true; };

const fonts = [
  { family: 'CMU Sans Serif Demi Condensed', style: 'Regular' },
  { family: 'Old Standard TT', style: 'Regular' },
  { family: 'Old Standard TT', style: 'Bold' },
];
const variables = [
  { name: 'text-accent', type: 'COLOR', collection: 'Semantic' },
  { name: 'bg-muted', type: 'COLOR', collection: 'Semantic' },
];
const spec = JSON.parse(read('card.figma.json'));
const runCard = async (figma, image) => {
  globalThis.figma = figma;
  globalThis.__html__ = '';
  new Function(read('code.js'))();
  await figma.ui.onmessage({ type: 'build', spec: read('card.figma.json'), image });
};

// a file without the Icon set: a clear error, nothing left behind
{
  const bare = makeFigma({ fonts, variables });
  await runCard(bare, null);
  const err = bare.logs.find(l => l.kind === 'error');
  if (!err || !/Run Main Character: Icons/.test(err.text)) fail('no "run Icons first" error in a file without the Icon set');
  if (bare.currentPage.children.length) fail(`${bare.currentPage.children.length} node(s) left behind in a file without the Icon set`);
}

const figma = makeFigma({ fonts, variables });
const iconSet = await buildIcons(figma);
if (!iconSet) fail('the Icons plugin built no set');
figma.logs.length = 0;
const image = new Uint8Array(fs.readFileSync(path.join(HERE, '..', '..', 'static', 'images', 'sergey-pesterev-tMvuB9se2uQ-unsplash.jpg')));
await runCard(figma, image);

for (const l of figma.logs) if (l.kind === 'error') fail(l.text);
const sets = figma.currentPage.children.filter(n => n.type === 'COMPONENT_SET');
const cardSet = sets.find(n => n.name === spec.name);
const stray = figma.currentPage.children.filter(n => n.type !== 'COMPONENT_SET');
if (stray.length) fail(`${stray.length} stray node(s) left on the page: ${stray.map(n => n.type).join(', ')}`);

if (!cardSet) fail('no Card set');
else {
  const want = spec.variants.length * spec.visuals.length;
  if (cardSet.children.length !== want) fail(`Card: ${cardSet.children.length} variants, want ${want}`);
  for (const c of cardSet.children) {
    const kind = c.name.match(/visual=(\w+)/)[1];
    const visual = c.children.find(n => n.name === 'card-visual');
    if (!visual || visual.type !== 'SLOT' || c.children[0] !== visual) { fail(`${c.name}: card-visual isn't the first child, a slot`); continue; }
    const inner = visual.children;
    if (inner.length !== 1) { fail(`${c.name}: the visual holds ${inner.length} layers`); continue; }
    const [x] = inner;
    if (kind === 'icon' && !(x.type === 'INSTANCE' && x.isExposedInstance && iconSet.children.includes(x.mainComponent))) fail(`${c.name}: no exposed Icon instance`);
    if (kind === 'initials' && !(x.type === 'TEXT' && x.characters === spec.sample.initials && visual.paddingTop > 0)) fail(`${c.name}: initials not a dropped text layer`);
    if (kind === 'image' && !(x.type === 'RECTANGLE' && x.fills[0].type === 'IMAGE' && x.layoutSizingHorizontal === 'FILL' && x.layoutSizingVertical === 'FILL')) fail(`${c.name}: no image filling the visual`);
  }
  const defs = cardSet.componentPropertyDefinitions;
  const props = Object.keys(defs).map(k => k.split('#')[0]);
  if (props.includes('glyph') || props.includes('initials')) fail(`Card props: ${props.join(', ')}`);
  const slotKeys = Object.keys(defs).filter(k => defs[k].type === 'SLOT');
  if (slotKeys.length !== 1 || !slotKeys[0].startsWith('card-visual#')) fail(`Card slot properties: ${slotKeys.join(', ') || 'none'}`);
  else if (!(defs[slotKeys[0]].preferredValues || []).some(p => p.type === 'COMPONENT_SET' && p.key === iconSet.key)) fail('the slot doesn\'t suggest the Icon set');
  console.log(`${spec.name.padEnd(10)} ${cardSet.children.length} variants  props: ${props.join(', ')}`);
}
// Fit icons. The stand-in, like Figma, won't let a plugin move a layer in
// an instance, and lays an instance's layers out from its main when it's
// resized. A style switch is staged by leaving each card icon's vector at
// its main's unscaled 24 px place (through the stand-in's own fields), as
// the switched outline icons seemed to be.
if (cardSet) {
  const cardIcons = cardSet.findAll(n => n.name === 'card-icon');
  const stale = i => { const mv = i.mainComponent.children[0], iv = i.children[0]; iv._x = mv.x; iv._y = mv.y; iv.width = mv.width; iv.height = mv.height; };
  const scaled = () => cardIcons.every(i => {
    const m = i.mainComponent, mv = m.children[0], iv = i.children[0], k = i.width / m.width;
    return Math.abs(iv.x - mv.x * k) < 0.01 && Math.abs(iv.y - mv.y * k) < 0.01 && Math.abs(iv.width - mv.width * k) < 0.01;
  });
  const sizes = cardIcons.map(i => i.width);
  const lines = () => figma.logs.map(l => l.text.split('\n')[0]).join(' | ');
  const warns = figma.logs.filter(l => l.kind === 'warn');
  if (!scaled()) fail('the card plugin left a card icon unscaled');
  figma.logs.length = 0;
  await fitIcons(figma);
  if (!figma.logs.some(l => l.text.startsWith(`Checked ${cardIcons.length} "Icon" instances on this page; fitted 0.`))) fail('fit: on fresh cards ' + lines());
  cardIcons.forEach(stale);
  figma.logs.length = 0;
  await fitIcons(figma);
  const report = figma.logs.find(l => /^Checked/.test(l.text));
  if (!report || !report.text.startsWith(`Checked ${cardIcons.length} "Icon" instances on this page; fitted ${cardIcons.length}.`) || report.kind !== 'ok') fail('fit: ' + lines());
  if (!scaled()) fail('fit: a card icon is still not scaled to its instance');
  if (cardIcons.some((i, n) => i.width !== sizes[n])) fail('fit: a card icon changed size');
  figma.logs.length = 0;
  await fitIcons(figma);
  if (!figma.logs.some(l => /fitted 0\./.test(l.text))) fail('fit: a second run still fitted icons');
  console.log(`fit icons: ${cardIcons.length} switched card icons laid out again, none the second time`);

  // a file using the library may know the set by the name it was published
  // with (once "Card icon"): Fit icons still finds its instances by shape
  iconSet.name = 'Card icon';
  stale(cardIcons[0]);
  figma.logs.length = 0;
  await fitIcons(figma);
  if (!figma.logs.some(l => l.text.startsWith(`Checked ${cardIcons.length} "Icon" instances on this page; fitted 1.`))) fail('fit: missed icons in a set under its old name: ' + lines());
  if (!scaled()) fail('fit: an icon in a set under its old name is still not scaled');
  iconSet.name = 'Icon';
  console.log('fit icons: found by shape in a set under its old name');

  // an icon that resizing doesn't fix (its box in place, its paths drawn
  // off inside it): reported with its numbers, as a warning, nothing thrown
  const v = cardIcons[1].children[0];
  v._ink = { dx: -1.5, dy: -1.5 };
  figma.logs.length = 0;
  await fitIcons(figma);
  const r = figma.logs.find(l => /^Checked/.test(l.text));
  if (!r || r.kind !== 'warn' || !/fitted 0\./.test(r.text) || !/still off after resizing: box .*, draws .*; should draw /.test(r.text)) fail('fit: an icon still off isn\'t reported with its numbers: ' + (r ? r.text : lines()));
  if (figma.logs.some(l => l.kind === 'error')) fail('fit: ' + lines());
  delete v._ink;
  console.log('fit icons: an icon resizing can\'t fix is reported with its numbers');
  figma.logs.length = 0;
  figma.logs.push(...warns);
}
for (const l of figma.logs) if (l.kind === 'warn') console.log('warn: ' + l.text.split('\n')[0]);
console.log(failed ? 'FAILED' : 'ok');
process.exit(failed ? 1 : 0);
