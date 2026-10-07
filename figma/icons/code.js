// SPDX-License-Identifier: AGPL-3.0-or-later
// Builds the Icon component set in Figma from icons.figma.json (the SVGs in
// static/icons/, bundled by icons.mjs with the mapping in
// static/js/card.js).
//
// One variant per thing an icon stands for (a card type, a category, a
// dream tone, a thing category) x style (filled | outline). Each is a
// single flattened vector that scales with its instance, the way the web's
// CSS mask does, painted with the file's text-accent variable (found by
// name, as the other plugins do; nothing here creates or edits a variable).
// Other components use the set from the file: the Card plugin puts these in
// its visual slot.

figma.showUI(__html__, { width: 360, height: 420, themeColors: true });

function log(text, kind) {
  figma.ui.postMessage({ type: 'log', text: text, kind: kind || 'info' });
}

figma.ui.onmessage = async function (msg) {
  try {
    if (msg.type === 'build') await build(JSON.parse(msg.icons));
    else if (msg.type === 'fit') await fit(msg.name || 'Icon');
  } catch (e) {
    log(String((e && e.stack) || e), 'error');
    figma.notify('Icon plugin failed: ' + (e && e.message), { error: true });
  }
};

// ---- fitting resized instances ----
// An Icon instance bigger or smaller than 24 should show its vector scaled
// to match (the vector's constraints are SCALE). Figma does that when an
// instance is resized, but not, it turns out, when a resized instance is
// switched to another variant (a card's icon set to outline): the new
// vector keeps its 24 px size and position, up and to the left. Or its box
// is in place and the paths inside it are not. This checks every Icon
// instance's vector on this page against where its main's draws, scaled
// (going by render bounds, the pixels), nested ones in cards included;
// resizes the instance to have Figma lay one that's off out again; and
// reports each one, with its numbers, fixed or not.
async function fit(name) {
  const insts = figma.currentPage.findAll(function (n) { return n.type === 'INSTANCE'; });
  let checked = 0, fitted = 0, failed = 0;
  const lines = [], others = {};
  for (const inst of insts) {
    let main = null;
    try { main = inst.getMainComponentAsync ? await inst.getMainComponentAsync() : inst.mainComponent; } catch (e) {}
    const set = main && main.parent;
    // an icon by its shape, not its set's name: in a file using the library,
    // the set has the name it was last published with (once "Card icon")
    let vp = null;
    try { vp = main && main.variantProperties; } catch (e) {}
    const isIcon = main && main.children && main.children.length === 1 && main.children[0].name === 'icon'
      && ((set && set.type === 'COMPONENT_SET' && set.name === name) || (vp && 'for' in vp && 'style' in vp));
    if (!isIcon) {
      const what = main ? (set && set.type === 'COMPONENT_SET' ? set.name + ' / ' : '') + main.name : '(no main component)';
      others[what] = (others[what] || 0) + 1;
      continue;
    }
    const mv = main.children[0], iv = inst.children[0];
    if (!iv || inst.children.length !== 1) { lines.push('  ' + main.name + ': the instance holds ' + inst.children.length + ' layers, not one vector; skipped'); continue; }
    checked++;
    const sx = inst.width / main.width, sy = inst.height / main.height;
    const r = function (v) { return Math.round(v * 100) / 100; };
    const rect = function (b) { return r(b.w) + '×' + r(b.h) + ' at ' + r(b.x) + ',' + r(b.y); };
    // the outermost component or instance it sits in: the card
    let card = null;
    for (let p = inst.parent; p && p.type !== 'PAGE'; p = p.parent) if (p.type === 'COMPONENT' || p.type === 'INSTANCE') card = p;
    const label = '  ' + main.name + (card ? ' in ' + card.name : '') + ' (' + r(inst.width) + ' px): ';
    // where the vector draws, relative to its icon: Figma's render bounds
    // (the pixels), or the node's box when there are none. A switched
    // outline icon can have its box in place and its paths off inside it.
    const ink = function (v, icon) {
      const b = v.absoluteRenderBounds, ib = icon.absoluteBoundingBox;
      if (!b || !ib || !isFinite(b.x) || !isFinite(b.y)) return null;
      return { x: b.x - ib.x, y: b.y - ib.y, w: b.width, h: b.height };
    };
    const mInk = ink(mv, main);
    const byInk = !!(mInk && ink(iv, inst));
    const want = byInk ? { x: mInk.x * sx, y: mInk.y * sy, w: mInk.w * sx, h: mInk.h * sy }
      : { x: mv.x * sx, y: mv.y * sy, w: mv.width * sx, h: mv.height * sy };
    const measure = function () {
      const b = { x: iv.x, y: iv.y, w: iv.width, h: iv.height };
      const d = byInk ? ink(iv, inst) : b;
      const off = Math.max(Math.abs(d.x - want.x), Math.abs(d.y - want.y), Math.abs(d.w - want.w), Math.abs(d.h - want.h));
      return { off: off, text: 'box ' + rect(b) + (byInk ? ', draws ' + rect(d) : '') };
    };
    const before = measure();
    const should = (byInk ? 'should draw ' : 'should be ') + rect(want);
    if (before.off < 0.01) { lines.push(label + 'in place; ' + before.text); continue; }
    // Figma won't let a plugin move or size a layer inside an instance
    // ("cannot be overridden in an instance: relative-transform"): it lays
    // the vector out from the main, by its constraints, when the instance
    // is resized. So resize the instance a pixel and back.
    try {
      const w = inst.width, h = inst.height;
      inst.resize(w + 1, h + 1);
      inst.resize(w, h);
    } catch (e) {
      failed++;
      lines.push(label + before.text + '; ' + should + '; couldn\'t resize it: ' + e.message);
      continue;
    }
    const after = measure();
    if (after.off < 0.01) {
      fitted++;
      lines.push(label + before.text + ' → ' + after.text);
    } else {
      failed++;
      lines.push(label + 'still off after resizing: ' + after.text + '; ' + should + ' (before: ' + before.text + ')');
    }
  }
  log('Checked ' + checked + ' "' + name + '" instances on this page; fitted ' + fitted + '.' + (lines.length ? '\n' + lines.join('\n') : ''), failed ? 'warn' : 'ok');
  if (!checked) {
    const seen = Object.keys(others).map(function (k) { return '  ' + others[k] + ' × ' + k; });
    log(insts.length + ' instance(s) on this page, none an icon' + (seen.length ? ':\n' + seen.join('\n') : '.'), 'warn');
  }
  figma.notify('Fitted ' + fitted + ' of ' + checked + ' icons');
}

// ---- finding the token variables by name ----
// Token names are the CSS custom properties without the dashes (bg-raised,
// space-4, font-sm, radius-lg). Figma names vary by file, so a variable
// matches on any trailing run of its path ("Colors/bg-raised",
// "bg/raised"), and group names that stand for a token prefix count as that
// prefix ("Spacing/4" -> space-4, "Font size/sm" -> font-sm).
const STEMS = {
  spacing: 'space', space: 'space', spaces: 'space',
  radius: 'radius', radii: 'radius', 'corner-radius': 'radius',
  font: 'font', 'font-size': 'font', 'font-sizes': 'font', type: 'font', typography: 'font',
};

function norm(s) {
  return s.trim().toLowerCase().replace(/^--/, '').replace(/[\s_.]+/g, '-');
}

function keysFor(collection, name) {
  const segs = [collection].concat(name.split('/')).map(norm).filter(Boolean);
  const last = segs[segs.length - 1];
  const keys = [];
  for (let i = segs.length - 1; i >= 0; i--) keys.push(segs.slice(i).join('-'));
  for (const s of segs.slice(0, -1)) {
    const stem = STEMS[s] || STEMS[s.split('-')[0]];
    if (stem) keys.push(stem + '-' + last);
  }
  return keys;
}

async function tokenFinder() {
  const entries = [];
  const cols = {};
  for (const c of await figma.variables.getLocalVariableCollectionsAsync()) cols[c.id] = c.name;
  for (const v of await figma.variables.getLocalVariablesAsync()) {
    const col = cols[v.variableCollectionId] || '';
    entries.push({
      label: col + ' / ' + v.name, type: v.resolvedType, local: true,
      keys: keysFor(col, v.name), load: async function () { return v; },
    });
  }
  try {
    for (const c of await figma.teamLibrary.getAvailableLibraryVariableCollectionsAsync()) {
      for (const lv of await figma.teamLibrary.getVariablesInLibraryCollectionAsync(c.key)) {
        entries.push({
          label: c.libraryName + ': ' + c.name + ' / ' + lv.name, type: lv.resolvedType, local: false,
          keys: keysFor(c.name, lv.name),
          load: function () { return figma.variables.importVariableByKeyAsync(lv.key); },
        });
      }
    }
  } catch (e) {
    log('Could not read libraries (' + e.message + '); using local variables only.');
  }
  log('Found ' + entries.length + ' variables to match against.');

  const cache = {};
  return async function find(token, type) {
    const id = type + ':' + token;
    if (id in cache) return cache[id];
    let best = null, bestScore = Infinity;
    for (const e of entries) {
      if (e.type !== type) continue;
      const i = e.keys.indexOf(token);
      if (i < 0) continue;
      // the Semantic collection first (an older Color/bg-raised loses), then
      // closest name, then local before library
      const score = (/(^|: )Semantic \//.test(e.label) ? 0 : 100) + i * 2 + (e.local ? 0 : 1);
      if (score < bestScore) { best = e; bestScore = score; }
    }
    cache[id] = best ? { variable: await best.load(), label: best.label } : null;
    return cache[id];
  };
}

// ---- helpers ----
function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  return { r: (n >> 16 & 255) / 255, g: (n >> 8 & 255) / 255, b: (n & 255) / 255 };
}

async function build(iconSpec) {
  const find = await tokenFinder();
  const color = iconSpec.color;
  const hit = await find(color.token, 'COLOR');
  const base = { type: 'SOLID', color: hexToRgb(color.fallback) };
  async function paint() {
    return hit ? figma.variables.setBoundVariableForPaint(base, 'color', hit.variable) : base;
  }

  // An SVG as one vector: strokes outlined into shapes (so they scale with
  // the instance, as the web's mask does), shapes with no visible paint
  // dropped (a fill="none" helper path would otherwise fill in), the rest
  // flattened together and painted --text-accent.
  //
  // Every shape is put back exactly where the SVG drew it: an outline's
  // bounds onto its stroked path's render bounds, a filled shape's onto its
  // own from before it left its <g>. Without this the stroked (TDesign)
  // outline icons, the only ones outlined or grouped, came out up and to
  // the left in real Figma, while the rest sat right.
  let maxShift = 0;
  function bounds(n, render) {
    const b = render ? n.absoluteRenderBounds : n.absoluteBoundingBox;
    return b && isFinite(b.x) && isFinite(b.y) ? b : null;
  }
  function align(outline, drawn) {
    const ob = bounds(outline, false);
    if (!drawn || !ob) return;
    const dx = drawn.x - ob.x, dy = drawn.y - ob.y;
    if (Math.abs(dx) < 0.001 && Math.abs(dy) < 0.001) return;
    outline.x += dx; outline.y += dy;
    maxShift = Math.max(maxShift, Math.abs(dx), Math.abs(dy));
  }
  const SHAPES = ['VECTOR', 'BOOLEAN_OPERATION', 'RECTANGLE', 'ELLIPSE', 'POLYGON', 'STAR', 'LINE'];
  function visible(paints) {
    return Array.isArray(paints) && paints.some(function (p) { return p.visible !== false && (p.opacity == null || p.opacity > 0); });
  }
  async function iconVector(svg, size) {
    const frame = figma.createNodeFromSvg(svg
      .replace(/currentColor/g, '#000000')
      .replace(/<svg\b[^>]*>/, function (tag) {
        return tag.replace(/\s(width|height)="[^"]*"/g, '').replace('<svg', '<svg width="' + size + '" height="' + size + '"');
      }));
    // each shape with where it drew, taken before anything moves
    const shapes = [], at = [];
    for (const n of frame.findAll(function (x) { return SHAPES.indexOf(x.type) >= 0; })) {
      if (visible(n.strokes)) {
        const drawn = bounds(n, true);
        const o = n.outlineStroke();
        if (o) {
          if (o.parent !== n.parent) n.parent.insertChild(n.parent.children.indexOf(n) + 1, o);
          shapes.push(o); at.push(drawn);
        }
        n.strokes = [];
      }
      if (visible(n.fills)) { shapes.push(n); at.push(bounds(n, false)); } else n.remove();
    }
    if (!shapes.length) throw new Error('an icon SVG drew nothing');
    // out of any <g> groups, so they share a parent, then each back where
    // it drew
    for (const n of shapes) if (n.parent !== frame) frame.appendChild(n);
    shapes.forEach(function (n, i) { align(n, at[i]); });
    const v = figma.flatten(shapes, frame);
    v.name = 'icon';
    v.fills = [await paint()];
    v.strokes = [];
    return { frame: frame, vector: v };
  }

  // A variant's for and style, from Figma or from its name.
  function props(c) {
    if (c.variantProperties) return c.variantProperties;
    const p = {};
    for (const kv of c.name.split(', ')) { const i = kv.indexOf('='); p[kv.slice(0, i)] = kv.slice(i + 1); }
    return p;
  }

  // With an Icon set already on the page, its variants are redrawn in place,
  // so every instance (in cards, in other files once published) picks up
  // the change; a use the set lacks is added to it. Otherwise a new set.
  async function buildIcons() {
    const size = iconSpec.size;
    const old = figma.currentPage.findOne(function (n) {
      return n.type === 'COMPONENT_SET' && n.name === iconSpec.name;
    });
    const have = {};
    if (old) for (const c of old.children) have[props(c).for + '/' + props(c).style] = c;
    const comps = {};
    const list = [], added = [];
    let redrawn = 0;
    for (const u of iconSpec.uses) {
      comps[u.for] = {};
      const svg = iconSpec.svgs[u.icon];
      for (const style of ['filled', 'outline']) {
        let c = have[u.for + '/' + style];
        delete have[u.for + '/' + style];
        if (c) {
          for (const child of c.children.slice()) child.remove();
          redrawn++;
        } else {
          c = figma.createComponent();
          c.name = 'for=' + u.for + ', style=' + style;
          c.resize(size, size);
          c.fills = [];
          c.clipsContent = false;
          if (old) { old.appendChild(c); added.push(c); }
        }
        const file = u.icon + (style === 'filled' && svg.filled !== svg.outline ? '-filled' : '');
        c.description = u.group + ' · static/icons/' + file + '.svg';
        const built = await iconVector(svg[style], size);
        const x = built.vector.x, y = built.vector.y;
        c.appendChild(built.vector);
        built.vector.x = x; built.vector.y = y;
        built.vector.constraints = { horizontal: 'SCALE', vertical: 'SCALE' };
        built.frame.remove();
        comps[u.for][style] = c;
        list.push(c);
      }
    }
    // a column per use, filled above outline
    const gap = 16, pad = 24;
    const place = function () {
      iconSpec.uses.forEach(function (u, i) {
        comps[u.for].filled.x = pad + i * (size + gap); comps[u.for].filled.y = pad;
        comps[u.for].outline.x = pad + i * (size + gap); comps[u.for].outline.y = pad + size + gap;
      });
    };
    let set = old;
    if (old) {
      if (added.length) {
        place();
        old.resizeWithoutConstraints(Math.max(old.width, pad * 2 + iconSpec.uses.length * (size + gap) - gap), Math.max(old.height, pad * 2 + size * 2 + gap));
      }
      const left = Object.keys(have);
      log('Updated "' + old.name + '" in place: ' + redrawn + ' variants redrawn, ' + added.length + ' added.'
        + (left.length ? ' Left alone (no longer in icons.figma.json): ' + left.join(', ') + '.' : ''));
    } else {
      place();
      set = figma.combineAsVariants(list, figma.currentPage);
      set.name = iconSpec.name;
      set.layoutMode = 'NONE';
      set.resizeWithoutConstraints(pad * 2 + iconSpec.uses.length * (size + gap) - gap, pad * 2 + size * 2 + gap);
      set.x = Math.round(figma.viewport.center.x - set.width / 2);
      set.y = Math.round(figma.viewport.center.y - set.height / 2);
      log('Built "' + set.name + '": ' + iconSpec.uses.length + ' icons, filled and outline.');
    }
    if (maxShift) log('Outlined strokes were put back where they drew (moved up to ' + Math.round(maxShift * 100) / 100 + ' px).');
    return { set: set, comps: comps };
  }
  const icons = await buildIcons();
  figma.currentPage.selection = [icons.set];
  figma.viewport.scrollAndZoomIntoView([icons.set]);

  if (hit) log('Bound ' + color.token + ' → ' + hit.label);
  else log('No variable found for ' + color.token + '; the icons are painted ' + color.fallback + '.\nRename the variable or add the name to STEMS in code.js, then run again.', 'warn');
  log('Done: "' + icons.set.name + '" with ' + icons.set.children.length + ' variants.', 'ok');
  figma.notify('Icons built: ' + icons.set.children.length + ' variants' + (hit ? '' : ', ' + color.token + ' missing'));
}
