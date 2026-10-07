// SPDX-License-Identifier: AGPL-3.0-or-later
// Runs code.js against the stand-in Figma API (figma/components/test/
// fake-figma.mjs) with icons.figma.json, the way the plugin runs in Figma.
// Fails on any error the API would throw, a stale icons.figma.json, or an
// icon that isn't one flattened vector in --text-accent that scales.
//
//   node figma/icons/test.mjs
//
// buildIcons() and fitIcons() are exported for figma/card/test.mjs, which
// needs the set in the file before the Card plugin runs, and checks Fit
// icons on the cards' icons.

import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { makeFigma } from '../components/test/fake-figma.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = f => fs.readFileSync(path.join(HERE, f), 'utf8');

async function run(figma, msg) {
  globalThis.figma = figma;
  globalThis.__html__ = '';
  new Function(read('code.js'))();
  await figma.ui.onmessage(msg);
}
export const fitIcons = figma => run(figma, { type: 'fit' });
export async function buildIcons(figma) {
  await run(figma, { type: 'build', icons: read('icons.figma.json') });
  const icons = JSON.parse(read('icons.figma.json'));
  return figma.currentPage.children.find(n => n.type === 'COMPONENT_SET' && n.name === icons.name);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  let failed = false;
  const fail = m => { console.log('FAIL ' + m); failed = true; };

  try { execFileSync(process.execPath, [path.join(HERE, 'icons.mjs'), '--check'], { stdio: 'pipe' }); }
  catch (e) { fail(String(e.stdout).trim()); }

  const figma = makeFigma({ fonts: [], variables: [{ name: 'text-accent', type: 'COLOR', collection: 'Semantic' }] });
  const icons = JSON.parse(read('icons.figma.json'));
  const set = await buildIcons(figma);

  for (const l of figma.logs) if (l.kind === 'error' || l.kind === 'warn') fail(l.text);
  const stray = figma.currentPage.children.filter(n => n.type !== 'COMPONENT_SET');
  if (stray.length) fail(`${stray.length} stray node(s) left on the page: ${stray.map(n => n.type).join(', ')}`);

  if (!set) fail(`no ${icons.name} set`);
  else {
    if (set.children.length !== icons.uses.length * 2) fail(`${set.name}: ${set.children.length} variants, want ${icons.uses.length * 2}`);
    for (const c of set.children) {
      const vs = c.children;
      if (vs.length !== 1 || vs[0].type !== 'VECTOR' || vs[0].name !== 'icon') { fail(`${c.name}: not one vector named icon`); continue; }
      const fill = vs[0].fills[0];
      if (!fill || !fill.boundVariables) fail(`${c.name}: fill not bound to a variable`);
      if (vs[0].strokes.length) fail(`${c.name}: strokes left on the vector`);
      if (!vs[0].constraints || vs[0].constraints.horizontal !== 'SCALE') fail(`${c.name}: doesn't scale with its instance`);
      if (!c.description.includes('static/icons/')) fail(`${c.name}: no source in its description`);
      if (vs[0]._misplaced) fail(`${c.name}: ${vs[0]._misplaced} shape(s) flattened away from where the SVG drew them`);
    }
    if (!figma.logs.some(l => /put back where they drew/.test(l.text))) fail('no outlined stroke needed re-aligning (the stand-in misplaces them on purpose)');
    console.log(`${set.name.padEnd(10)} ${set.children.length} variants`);

    // a second run redraws the same set in place: one set, the same
    // variants, an instance still linked, new vectors
    const variant = set.children[1];
    const inst = variant.createInstance();
    const before = variant.children[0];
    figma.logs.length = 0;
    await buildIcons(figma);
    for (const l of figma.logs) if (l.kind === 'error' || l.kind === 'warn') fail('second run: ' + l.text);
    const sets = figma.currentPage.children.filter(n => n.type === 'COMPONENT_SET');
    if (sets.length !== 1) fail(`second run: ${sets.length} sets, want the one updated in place`);
    if (set.children.length !== icons.uses.length * 2) fail(`second run: ${set.children.length} variants`);
    if (inst.mainComponent !== variant || variant.parent !== set) fail('second run: the instance lost its variant');
    if (variant.children.length !== 1 || variant.children[0] === before) fail('second run: the variant was not redrawn');
    if (!figma.logs.some(l => /in place: 60 variants redrawn, 0 added/.test(l.text))) fail('second run: no in-place report');
    else console.log('second run updated it in place');
  }
  console.log(failed ? 'FAILED' : 'ok');
  process.exit(failed ? 1 : 0);
}
