// SPDX-License-Identifier: AGPL-3.0-or-later
// Runs code.js against the stand-in Figma API (fake-figma.mjs) with every
// spec in specs/, the way the plugin runs them in Figma: all at once, in
// order. The variables are the ones the Main Character Figma file has
// (figma/main-character-figma-fonts-v3/figma-variables). Fails on any error
// the API would throw, any instance that had to be drawn in place, and any
// set whose variant count or properties don't match its spec.
//
//   node test/run.mjs

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeFigma } from './fake-figma.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, '..');
const VARS = path.join(ROOT, '..', 'main-character-figma-fonts-v3', 'figma-variables');

function tokens(file, collection) {
  const out = [];
  const walk = (o, p) => {
    for (const [k, v] of Object.entries(o)) {
      if (k.startsWith('$')) continue;
      if (v && typeof v === 'object' && '$value' in v) {
        const type = v.$type === 'color' ? 'COLOR' : v.$type === 'number' ? 'FLOAT' : 'STRING';
        out.push({ name: [...p, k].join('/'), type, collection });
      } else if (v && typeof v === 'object') walk(v, [...p, k]);
    }
  };
  walk(JSON.parse(fs.readFileSync(path.join(VARS, file), 'utf8')), []);
  return out;
}
const variables = fs.existsSync(VARS)
  ? [...tokens('Color.Dark.tokens.json', 'Color'), ...tokens('Scale.Default.tokens.json', 'Scale')]
  : [];

const fonts = [
  { family: 'CMU Sans Serif Demi Condensed', style: 'Regular' },
  { family: 'Old Standard TT', style: 'Regular' },
  { family: 'Old Standard TT', style: 'Bold' },
  { family: 'Old Standard TT', style: 'Italic' },
  { family: 'Roboto Mono', style: 'Regular' },
  { family: 'Inter', style: 'Regular' },
];

const figma = makeFigma({ fonts, variables });
globalThis.figma = figma;
globalThis.__html__ = '';
const code = fs.readFileSync(path.join(ROOT, 'code.js'), 'utf8');
new Function(code)();

const specDir = path.join(ROOT, 'specs');
const files = fs.readdirSync(specDir).filter(f => f.endsWith('.figma.json'));
const texts = files.map(f => fs.readFileSync(path.join(specDir, f), 'utf8'));
// shuffle, to prove the plugin orders them itself
texts.reverse();
await figma.ui.onmessage({ type: 'build', specs: texts });

let failed = false;
const fail = m => { console.log('FAIL ' + m); failed = true; };
for (const l of figma.logs) {
  if (l.kind === 'error') fail(l.text);
  if (l.kind === 'warn' && /drew it in place/.test(l.text)) fail(l.text);
}
const sets = figma.currentPage.children.filter(n => n.type === 'COMPONENT_SET');
const specs = texts.map(t => JSON.parse(t)).sort((a, b) => a.order - b.order);
for (const spec of specs) for (const s of spec.sets) {
  const node = sets.find(n => n.name === s.name);
  if (!node) { fail(`no set "${s.name}"`); continue; }
  if (node.children.length !== s.variants.length) fail(`${s.name}: ${node.children.length} variants, spec has ${s.variants.length}`);
  const props = Object.keys(node.componentPropertyDefinitions).map(k => k.split('#')[0]);
  const instances = node.findAll(n => n.type === 'INSTANCE').length;
  console.log(`${s.name.padEnd(18)} ${String(node.children.length).padStart(2)} variants  ${instances ? instances + ' instances  ' : ''}${props.length ? 'props: ' + props.join(', ') : ''}`);
}
for (const l of figma.logs) if (l.kind === 'warn') console.log('warn: ' + l.text.split('\n')[0]);
const bound = figma.logs.find(l => /^Bound/.test(l.text || ''));
if (bound) console.log(bound.text.split('\n')[0]);
console.log(failed ? 'FAILED' : `ok: ${sets.length} sets built`);
process.exit(failed ? 1 : 0);
