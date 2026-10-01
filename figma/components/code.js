// SPDX-License-Identifier: AGPL-3.0-or-later
// Builds Main Character component sets in Figma from the specs that
// extract.mjs writes (specs/*.figma.json). One builder for every component:
// a spec is a list of sets, each a list of variants, each a layout tree read
// off the live design page (walk.js). Frames become auto-layout frames,
// text keeps its styled runs, and a part that is itself a component (the
// search bar's button) becomes an instance of that set.
//
// Colours, padding, gaps, radii and type sizes that match a token are bound
// to the file's existing variables, found by name (local variables first,
// then enabled libraries); the matcher is the Card plugin's. Nothing here
// creates or edits a variable. A token the file doesn't have falls back to
// the spec's value and is listed in the report.
//
// Choose several specs at once; they build in dependency order (the spec's
// `order`), so a set is on the page before anything that holds instances of
// it. A set whose name is taken gets " (import)" and the old one is left
// alone, because swapping a set in place would break its instances.

figma.showUI(__html__, { width: 420, height: 540, themeColors: true });

function log(text, kind) {
  figma.ui.postMessage({ type: 'log', text: text, kind: kind || 'info' });
}

figma.ui.onmessage = async function (msg) {
  if (msg.type !== 'build') return;
  try {
    const specs = msg.specs.map(function (s) { return JSON.parse(s); });
    specs.sort(function (a, b) { return (a.order || 0) - (b.order || 0); });
    await buildAll(specs);
  } catch (e) {
    log(String((e && e.stack) || e), 'error');
    figma.notify('Import failed: ' + (e && e.message), { error: true });
  }
};

// ---- finding the token variables by name (as in figma/card/code.js) ----
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
      const score = i * 2 + (e.local ? 0 : 1);
      if (score < bestScore) { best = e; bestScore = score; }
    }
    cache[id] = best ? { variable: await best.load(), label: best.label } : null;
    return cache[id];
  };
}

function hexToRgb(hex) {
  const n = parseInt(hex.replace('#', ''), 16);
  return { r: (n >> 16 & 255) / 255, g: (n >> 8 & 255) / 255, b: (n & 255) / 255 };
}
const pxOf = function (v) { return typeof v === 'number' ? v : v ? v.px : 0; };

// every node of a tree, instance trees included
function each(n, fn) {
  fn(n);
  if (n.type === 'instance') each(n.tree, fn);
  for (const c of n.children || []) each(c, fn);
}

async function buildAll(specs) {
  const find = await tokenFinder();
  const bound = {}, missing = {};
  const note = function (token, hit) { if (hit) bound[token] = hit.label; else missing[token] = true; };

  // ---- fonts: every face any spec asks for, loaded up front ----
  const available = await figma.listAvailableFontsAsync();
  function findFamily(family) {
    const families = available.map(function (f) { return f.fontName.family; });
    if (families.indexOf(family) >= 0) return family;
    const words = family.toLowerCase().split(/\s+/);
    return families.find(function (x) {
      const l = x.toLowerCase();
      return words.every(function (w) { return l.indexOf(w) >= 0; });
    });
  }
  function styleOf(family, wanted) {
    const styles = available.filter(function (f) { return f.fontName.family === family; })
      .map(function (f) { return f.fontName.style; });
    for (const w of wanted) {
      const s = styles.find(function (x) { return x.toLowerCase() === w.toLowerCase(); });
      if (s) return s;
    }
    return styles[0];
  }
  const families = {};
  const fonts = specs[0].fonts;
  for (const k of ['ui', 'serif', 'mono']) {
    const tries = k === 'mono' ? [fonts.mono, 'JetBrains Mono', 'Roboto Mono', 'Source Code Pro', 'Courier New', 'Inter'] : [fonts[k]];
    for (const t of tries) { const f = t && findFamily(t); if (f) { families[k] = f; break; } }
    if (!families[k] && k !== 'mono') throw new Error('The font "' + fonts[k] + '" is not available in Figma.');
  }
  // CSS weight and style to a Figma face. The UI face is one demi-weight
  // file that CSS serves for 400 and 600 alike.
  const faceCache = {};
  function face(style) {
    const fam = families[style.font] || families.ui;
    const id = fam + '|' + style.weight + '|' + style.italic;
    if (faceCache[id]) return faceCache[id];
    let wanted;
    if (style.font === 'ui') wanted = ['Regular', 'Demi Condensed', 'DemiCondensed', 'Medium', 'SemiBold'];
    else if (style.weight >= 600 && style.italic) wanted = ['Bold Italic', 'Bold', 'Italic'];
    else if (style.weight >= 600) wanted = ['Bold', 'SemiBold'];
    else if (style.italic) wanted = ['Italic', 'Regular'];
    else wanted = ['Regular'];
    return (faceCache[id] = { family: fam, style: styleOf(fam, wanted) });
  }
  const toLoad = {};
  for (const spec of specs) for (const set of spec.sets) for (const v of set.variants)
    each(v.tree, function (n) { if (n.type === 'text') for (const r of n.runs) { const f = face(r.style); toLoad[f.family + '|' + f.style] = f; } });
  for (const k in toLoad) await figma.loadFontAsync(toLoad[k]);
  log('Fonts: ' + Object.keys(toLoad).map(function (k) { return k.replace('|', ' '); }).join(', '));

  // ---- paints and lengths, bound where a token matches ----
  async function paint(p) {
    const base = { type: 'SOLID', color: hexToRgb(p.hex || '#ff00ff') };
    if (p.opacity != null) base.opacity = p.opacity;
    if (!p.token) return base;
    const hit = await find(p.token, 'COLOR');
    note(p.token, hit);
    return hit ? figma.variables.setBoundVariableForPaint(base, 'color', hit.variable) : base;
  }
  async function paints(list) {
    const out = [];
    for (const p of list || []) if (p) out.push(await paint(p));
    return out;
  }
  function bindField(node, field, variable) {
    try {
      node.setBoundVariable(field, variable);
    } catch (e) {
      if (node.type !== 'TEXT') throw e;
      node.setRangeBoundVariable(0, node.characters.length, field, variable);
    }
  }
  async function length(node, fields, v) {
    for (const f of fields) node[f] = pxOf(v);
    if (typeof v !== 'object' || !v || !v.token) return;
    const hit = await find(v.token, 'FLOAT');
    note(v.token, hit);
    if (hit) for (const f of fields) bindField(node, f, hit.variable);
  }

  // ---- text ----
  const CASE = { UPPER: 'UPPER', LOWER: 'LOWER' };
  async function makeText(n) {
    const t = figma.createText();
    t.name = n.name || 'text';
    t.fontName = face(n.runs[0].style);
    t.characters = n.chars;
    await styleRuns(t, n.runs);
    t.lineHeight = n.lineHeight ? { value: n.lineHeight, unit: 'PIXELS' } : { unit: 'AUTO' };
    if (n.align) t.textAlignHorizontal = n.align;
    return t;
  }
  async function styleRuns(t, runs) {
    let at = 0;
    for (const r of runs) {
      const s = at, e = at + r.len;
      at = e;
      if (e <= s) continue;
      const st = r.style;
      t.setRangeFontName(s, e, face(st));
      t.setRangeFontSize(s, e, pxOf(st.size));
      if (st.size && st.size.token) {
        const hit = await find(st.size.token, 'FLOAT');
        note(st.size.token, hit);
        if (hit) t.setRangeBoundVariable(s, e, 'fontSize', hit.variable);
      }
      if (st.color) t.setRangeFills(s, e, [await paint(st.color)]);
      if (st.letterSpacing) t.setRangeLetterSpacing(s, e, { value: st.letterSpacing, unit: 'PIXELS' });
      if (CASE[st.case]) t.setRangeTextCase(s, e, CASE[st.case]);
      if (st.decoration) t.setRangeTextDecoration(s, e, st.decoration);
    }
  }

  // ---- frames ----
  const ALIGN_P = { MIN: 'MIN', MAX: 'MAX', CENTER: 'CENTER', SPACE_BETWEEN: 'SPACE_BETWEEN', BASELINE: 'MIN' };
  const ALIGN_C = { MIN: 'MIN', MAX: 'MAX', CENTER: 'CENTER', BASELINE: 'BASELINE', SPACE_BETWEEN: 'MIN' };
  async function dress(f, n) {
    f.name = n.name || 'frame';
    f.fills = await paints(n.fills);
    f.clipsContent = !!n.clip;
    if (n.mode === 'H' || n.mode === 'V') {
      f.layoutMode = n.mode === 'H' ? 'HORIZONTAL' : 'VERTICAL';
      const p = n.padding || [0, 0, 0, 0];
      await length(f, ['paddingTop'], p[0]);
      await length(f, ['paddingRight'], p[1]);
      await length(f, ['paddingBottom'], p[2]);
      await length(f, ['paddingLeft'], p[3]);
      await length(f, ['itemSpacing'], n.gap || 0);
      if (n.wrap && n.mode === 'H') {
        f.layoutWrap = 'WRAP';
        await length(f, ['counterAxisSpacing'], n.crossGap || 0);
      }
      f.primaryAxisAlignItems = ALIGN_P[n.justify] || 'MIN';
      let c = ALIGN_C[n.align] || 'MIN';
      if (c === 'BASELINE' && n.mode !== 'H') c = 'MIN';
      f.counterAxisAlignItems = c;
    } else {
      f.layoutMode = 'NONE';
    }
    if (n.stroke) {
      f.strokes = [await paint(n.stroke.paint)];
      f.strokeAlign = 'INSIDE';
      const w = n.stroke.weights;
      if (w[0] === w[1] && w[1] === w[2] && w[2] === w[3]) f.strokeWeight = w[0];
      else { f.strokeTopWeight = w[0]; f.strokeRightWeight = w[1]; f.strokeBottomWeight = w[2]; f.strokeLeftWeight = w[3]; }
      if (n.stroke.dash) f.dashPattern = n.stroke.dash;
    }
    if (n.radius) {
      const corners = ['topLeftRadius', 'topRightRadius', 'bottomRightRadius', 'bottomLeftRadius'];
      for (let i = 0; i < 4; i++) await length(f, [corners[i]], n.radius[i]);
    }
    if (n.opacity != null) f.opacity = n.opacity;
    if (n.shadows) {
      const fx = [];
      for (const s of n.shadows) {
        const p = s.paint || { hex: '#000000', opacity: 0.25 };
        const c = hexToRgb(p.hex);
        fx.push({
          type: s.inset ? 'INNER_SHADOW' : 'DROP_SHADOW', visible: true, blendMode: 'NORMAL',
          color: { r: c.r, g: c.g, b: c.b, a: p.opacity != null ? p.opacity : 1 },
          offset: { x: s.x, y: s.y }, radius: s.blur, spread: s.spread || 0,
        });
      }
      f.effects = fx;
    }
  }

  // a CSS outline: a ring frame outside the box that follows its size
  async function ring(f, n) {
    const o = n.outline;
    const r = figma.createFrame();
    r.name = 'focus-ring';
    r.fills = [];
    r.strokes = [await paint(o.paint)];
    r.strokeAlign = 'INSIDE';
    r.strokeWeight = o.width;
    const out = o.offset + o.width;
    const rad = n.radius ? pxOf(n.radius[0]) : 0;
    r.cornerRadius = rad ? Math.min(rad + out, 999) : 0;
    f.appendChild(r);
    if (f.layoutMode !== 'NONE') r.layoutPositioning = 'ABSOLUTE';
    r.resize(Math.max(1, n.w + out * 2), Math.max(1, n.h + out * 2));
    r.x = -out; r.y = -out;
    r.constraints = { horizontal: 'STRETCH', vertical: 'STRETCH' };
    f.clipsContent = false;
  }

  async function checkbox(n, asComponent) {
    const f = asComponent ? figma.createComponent() : figma.createFrame();
    f.name = n.name || 'checkbox';
    f.resize(n.w, n.h);
    f.cornerRadius = n.radius || 2;
    f.fills = n.fill ? [await paint(n.fill)] : [];
    if (n.rule) { f.strokes = [await paint(n.rule)]; f.strokeAlign = 'INSIDE'; f.strokeWeight = 1; }
    if (n.checked && n.mark) {
      const v = figma.createVector();
      v.name = 'mark';
      const s = n.w / 16;
      v.vectorPaths = [{ windingRule: 'NONE', data: 'M ' + 3.5 * s + ' ' + 8.2 * s + ' L ' + 6.6 * s + ' ' + 11.3 * s + ' L ' + 12.5 * s + ' ' + 4.8 * s }];
      v.strokes = [await paint(n.mark)];
      v.strokeWeight = 2 * s;
      v.strokeCap = 'ROUND';
      v.strokeJoin = 'ROUND';
      f.appendChild(v);
    }
    return f;
  }

  async function chevron(n) {
    const v = figma.createVector();
    v.name = 'chevron';
    v.vectorPaths = [{ windingRule: 'NONE', data: 'M 0 0 L ' + n.w / 2 + ' ' + n.h + ' L ' + n.w + ' 0' }];
    v.strokes = n.paint ? [await paint(n.paint)] : [];
    v.strokeWeight = 1.5;
    v.strokeCap = 'ROUND';
    v.strokeJoin = 'ROUND';
    return v;
  }

  // ---- sets this run has built, for instances ----
  const built = {};
  function findSet(name) {
    if (built[name]) return built[name];
    return figma.currentPage.findOne(function (x) { return x.type === 'COMPONENT_SET' && x.name === name; });
  }
  function variantKey(s) {
    return s.split(',').map(function (p) { return p.trim().replace(/\s*=\s*/, '='); }).filter(Boolean).sort().join(', ');
  }
  const warnings = [];

  async function instance(n) {
    const set = findSet(n.set);
    const want = variantKey(n.variant);
    const comp = set && set.children.find(function (c) { return variantKey(c.name) === want; });
    if (!comp) {
      warnings.push('No "' + n.set + '" variant ' + n.variant + ' to instance; drew it in place instead.');
      return null;
    }
    const inst = comp.createInstance();
    inst.name = n.name || n.set;
    // the instance's texts, in order, as the page has them. New characters
    // take the first character's style, so a text with several styles (a
    // check row's name and definition) gets its runs again.
    const textNodes = inst.findAll(function (x) { return x.type === 'TEXT'; });
    for (let i = 0; i < textNodes.length && i < n.texts.length; i++) {
      const t = textNodes[i], want = n.texts[i];
      if (t.characters === want.chars) continue;
      const faces = t.characters.length ? t.getRangeAllFontNames(0, t.characters.length) : [t.fontName];
      for (const fn of faces) await figma.loadFontAsync(fn);
      t.characters = want.chars;
      if (want.runs.length > 1) await styleRuns(t, want.runs);
    }
    return inst;
  }

  // Build a node and its children. `refs` collects the layers wired to the
  // set's component properties.
  async function make(n, refs, asComponent) {
    let node;
    if (n.type === 'text') node = await makeText(n);
    else if (n.type === 'checkbox') node = await checkbox(n, asComponent);
    else if (n.type === 'chevron') node = await chevron(n);
    else if (n.type === 'spacer') { node = figma.createFrame(); node.name = 'spacer'; node.fills = []; node.resize(1, 1); }
    else if (n.type === 'instance') {
      node = await instance(n);
      if (!node) return make(n.tree, [], false);   // drawn instead, with no properties of its own
    } else {
      node = asComponent ? figma.createComponent() : figma.createFrame();
      await dress(node, n);
      // Size the frame before its children go in: a child can only fill an
      // axis its parent doesn't hug. An axis that will fill its own parent
      // is fixed at its measured size until then (place() sets FILL).
      presize(node, n);
      for (const c of n.children || []) {
        const child = await make(c, refs, false);
        node.appendChild(child);
        place(child, c, node);
      }
      if (n.outline) await ring(node, n);
    }
    if (n.prop && n.type === 'text') refs.push({ node: node, text: n.prop, value: n.chars });
    if (n.bool) refs.push({ node: node, bool: n.bool });
    return node;
  }

  function presize(node, n) {
    const s = n.sizing || { h: 'FIXED', v: 'FIXED' };
    const auto = node.layoutMode !== 'NONE';
    const h = s.h === 'HUG' && auto ? 'HUG' : 'FIXED';
    const v = s.v === 'HUG' && auto ? 'HUG' : 'FIXED';
    if (h === 'FIXED' || v === 'FIXED') node.resize(Math.max(1, n.w || 1), Math.max(1, n.h || 1));
    if (auto) { node.layoutSizingHorizontal = h; node.layoutSizingVertical = v; }
  }

  // Size and position a child that is now in its parent.
  function place(node, n, parent) {
    const auto = parent.layoutMode !== 'NONE';
    if (n.type === 'spacer') {
      if (auto) node.layoutGrow = 1;
      return;
    }
    if (n.absolute || n.at) {
      if (auto && n.absolute) node.layoutPositioning = 'ABSOLUTE';
      const at = n.absolute || n.at;
      node.x = at.x; node.y = at.y;
      if (n.type === 'frame' && n.at) node.resize(Math.max(1, n.w), Math.max(1, n.h));
      if (!auto || n.absolute) return size(node, n, false);
    }
    size(node, n, auto);
  }

  function size(node, n, parentAuto) {
    const s = n.sizing || { h: 'FIXED', v: 'FIXED' };
    const isText = node.type === 'TEXT';
    const isAuto = (node.type === 'FRAME' || node.type === 'COMPONENT') && node.layoutMode !== 'NONE';
    const canHug = isText || isAuto || node.type === 'INSTANCE';
    let h = s.h, v = s.v;
    if (h === 'FILL' && !parentAuto) h = 'FIXED';
    if (v === 'FILL' && !parentAuto) v = 'FIXED';
    if (h === 'HUG' && !canHug) h = 'FIXED';
    if (v === 'HUG' && !canHug) v = 'FIXED';
    if (isText) {
      if (h === 'HUG') node.textAutoResize = 'WIDTH_AND_HEIGHT';
      else {
        node.textAutoResize = 'HEIGHT';
        node.resize(Math.max(1, n.w || node.width), node.height);
        if (h === 'FILL') node.layoutSizingHorizontal = 'FILL';
      }
      if (n.truncate) { node.textTruncation = 'ENDING'; node.maxLines = 1; }
      return;
    }
    if (node.type === 'INSTANCE') {
      if (h === 'FILL') node.layoutSizingHorizontal = 'FILL';
      else if (h === 'FIXED') node.resize(Math.max(1, n.w), node.height);
      if (v === 'FILL') node.layoutSizingVertical = 'FILL';
      else if (v === 'FIXED') node.resize(node.width, Math.max(1, n.h));
      return;
    }
    if (h === 'FIXED' || v === 'FIXED') node.resize(Math.max(1, h === 'FIXED' ? n.w : node.width), Math.max(1, v === 'FIXED' ? n.h : node.height));
    if (isAuto || parentAuto) {
      node.layoutSizingHorizontal = h;
      node.layoutSizingVertical = v;
    }
    if (isAuto || parentAuto) {
      if (n.minW) node.minWidth = n.minW;
      if (n.minH) node.minHeight = n.minH;
    }
  }

  // ---- one set ----
  let cursorY = Math.round(figma.viewport.center.y);
  const left = Math.round(figma.viewport.center.x);
  const made = [];
  async function buildSet(spec, set) {
    const comps = [], allRefs = [];
    for (const v of set.variants) {
      const refs = [];
      let c = await make(v.tree, refs, true);
      if (c.type !== 'COMPONENT') {
        // a bare text root: give it a hugging component to live in
        const wrap = figma.createComponent();
        wrap.layoutMode = 'HORIZONTAL';
        wrap.fills = [];
        wrap.appendChild(c);
        size(c, v.tree, true);
        c = wrap;
      } else size(c, v.tree, false);
      c.name = v.name;
      comps.push(c);
      allRefs.push(refs);
    }
    // a grid: one row per value of every property but the last
    const keyOf = function (name) { const parts = name.split(', '); parts.pop(); return parts.join(', '); };
    const gap = 32;
    let x = 0, y = 0, rowH = 0, row = null;
    comps.forEach(function (c) {
      const k = keyOf(c.name);
      if (row !== null && k !== row) { x = 0; y += rowH + gap; rowH = 0; }
      row = k;
      c.x = x; c.y = y;
      x += c.width + gap; rowH = Math.max(rowH, c.height);
    });
    const node = figma.combineAsVariants(comps, figma.currentPage);
    const taken = figma.currentPage.findOne(function (n) { return n.type === 'COMPONENT_SET' && n.name === set.name && n !== node; });
    node.name = taken ? set.name + ' (import)' : set.name;
    if (taken) log('"' + set.name + '" already exists on this page, so this one is "' + node.name + '".');
    node.layoutMode = 'NONE';
    // room above for anything drawn outside its variant (an open menu)
    const pad = 40;
    let above = 0;
    for (const v of set.variants) for (const c of v.tree.children || [])
      if (c.absolute && c.absolute.y < 0) above = Math.max(above, -c.absolute.y);
    for (const child of node.children) { child.x += pad; child.y += pad + above; }
    const maxX = Math.max.apply(null, node.children.map(function (n) { return n.x + n.width; }));
    const maxY = Math.max.apply(null, node.children.map(function (n) { return n.y + n.height; }));
    node.resizeWithoutConstraints(maxX + pad, maxY + pad);
    node.fills = [];
    node.strokes = [{ type: 'SOLID', color: { r: 0.6, g: 0.27, b: 1 } }];
    node.dashPattern = [10, 5];
    node.x = left; node.y = cursorY;
    cursorY += node.height + 80;

    // component properties: TEXT for each data-figma-text, BOOLEAN for each
    // data-figma-bool, defined once per set from the first value seen
    const keys = {};
    allRefs.forEach(function (refs) {
      refs.forEach(function (r) {
        const name = r.text || r.bool;
        const id = (r.text ? 'T:' : 'B:') + name;
        if (!keys[id]) keys[id] = node.addComponentProperty(name, r.text ? 'TEXT' : 'BOOLEAN', r.text ? r.value : true);
      });
    });
    allRefs.forEach(function (refs) {
      refs.forEach(function (r) {
        const refsNow = Object.assign({}, r.node.componentPropertyReferences || {});
        if (r.text) refsNow.characters = keys['T:' + r.text];
        if (r.bool) refsNow.visible = keys['B:' + r.bool];
        r.node.componentPropertyReferences = refsNow;
      });
    });
    built[set.name] = node;
    made.push(node);
    log('Built ' + node.name + ': ' + comps.length + ' variant' + (comps.length === 1 ? '' : 's')
      + (Object.keys(keys).length ? ', properties ' + Object.keys(keys).map(function (k) { return k.slice(2); }).join(', ') : '') + '.');
  }

  for (const spec of specs) {
    log('— ' + spec.name + ' (' + spec.page + ')');
    for (const set of spec.sets) await buildSet(spec, set);
  }

  figma.currentPage.selection = made;
  figma.viewport.scrollAndZoomIntoView(made);

  // ---- report ----
  const boundList = Object.keys(bound).sort();
  const missingList = Object.keys(missing).filter(function (t) { return !bound[t]; }).sort();
  log('Bound ' + boundList.length + ' tokens:\n' + boundList.map(function (t) { return '  ' + t + ' → ' + bound[t]; }).join('\n'));
  if (missingList.length) {
    log('No variable found for ' + missingList.length + ' tokens; they are literal values:\n  '
      + missingList.join(', ') + '\nRename the variable or add the name to STEMS in code.js, then run again.', 'warn');
  }
  for (const w of warnings) log(w, 'warn');
  log('Done: ' + made.length + ' component sets.', 'ok');
  figma.notify('Built ' + made.length + ' component sets, ' + boundList.length + ' tokens bound'
    + (missingList.length ? ', ' + missingList.length + ' missing' : ''));
}
