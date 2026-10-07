// SPDX-License-Identifier: AGPL-3.0-or-later
// Builds the Card component set in Figma from card.figma.json. It uses the
// file's Icon set, which the Icons plugin (figma/icons/) builds; run that
// first.
//
// Card: size (sm | md | lg) x orientation (vertical | horizontal) x visual
// (icon | initials | image), what the visual starts with. card-visual is a
// native slot, so an instance can swap in anything; it suggests the Icon
// set. The icon variants start with an Icon instance (exposed when
// Figma allows it, so its "for" and "style" pickers show on the card), the
// initials variants with a text layer (edited in the slot: slot content
// can't be bound to a property), the image variants with a rectangle whose
// image fill can be replaced. Which kind of item a card points at is
// otherwise not a variant: type, title,
// description and badges are component properties. Layers are named after
// the CSS parts in static/css/card.css (card-visual, card-icon, card-image,
// card-body, card-type, card-title, card-desc, card-badges, card-badge,
// card-more) so the two stay mappable.
//
// Colors, radii, and every length the spec marks with a token are bound to
// the file's existing variables, found by name (local variables first, then
// enabled libraries). Nothing here creates or edits a variable. A token the
// file doesn't have falls back to the spec's literal and is listed in the
// report, so a naming mismatch is visible rather than silent.

figma.showUI(__html__, { width: 380, height: 540, themeColors: true });

function log(text, kind) {
  figma.ui.postMessage({ type: 'log', text: text, kind: kind || 'info' });
}

figma.ui.onmessage = async function (msg) {
  if (msg.type !== 'build') return;
  try {
    await build(JSON.parse(msg.spec), msg.image || null);
  } catch (e) {
    log(String((e && e.stack) || e), 'error');
    figma.notify('Card import failed: ' + (e && e.message), { error: true });
  }
};

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

async function build(spec, imageBytes) {
  const px = function (rem) { return Math.round(rem * spec.remPx * 100) / 100; };
  const find = await tokenFinder();
  const bound = {}, missing = {};
  const note = function (token, hit) {
    if (hit) bound[token] = hit.label; else missing[token] = true;
  };

  async function paint(token) {
    const hex = spec.fallback[token];
    const base = { type: 'SOLID', color: hexToRgb(hex || '#ff00ff') };
    const hit = await find(token, 'COLOR');
    note(token, hit);
    return hit ? figma.variables.setBoundVariableForPaint(base, 'color', hit.variable) : base;
  }

  // A length: a bare rem number, or {rem, token} to bind.
  async function length(node, fields, value) {
    const rem = typeof value === 'number' ? value : value.rem;
    for (const f of fields) node[f] = px(rem);
    if (typeof value !== 'object' || !value.token) return;
    const hit = await find(value.token, 'FLOAT');
    note(value.token, hit);
    if (hit) for (const f of fields) bindField(node, f, hit.variable);
  }

  // Text size binds through the range API on builds that don't take it on
  // the node itself.
  function bindField(node, field, variable) {
    try {
      node.setBoundVariable(field, variable);
    } catch (e) {
      if (node.type !== 'TEXT') throw e;
      node.setRangeBoundVariable(0, node.characters.length, field, variable);
    }
  }

  async function radius(node, r) {
    const corners = ['topLeftRadius', 'topRightRadius', 'bottomLeftRadius', 'bottomRightRadius'];
    for (const f of corners) node[f] = r.px;
    const hit = await find(r.token, 'FLOAT');
    note(r.token, hit);
    if (hit) for (const f of corners) node.setBoundVariable(f, hit.variable);
  }

  // CSS padding shorthand: 1, 2 or 4 values, top right bottom left.
  async function padding(node, p) {
    const [t, r, b, l] = p.length === 1 ? [p[0], p[0], p[0], p[0]]
      : p.length === 2 ? [p[0], p[1], p[0], p[1]] : p;
    await length(node, ['paddingTop'], t);
    await length(node, ['paddingRight'], r);
    await length(node, ['paddingBottom'], b);
    await length(node, ['paddingLeft'], l);
  }

  // ---- fonts ----
  const available = await figma.listAvailableFontsAsync();
  // An installed font's family is whatever its file says, so a CMU file can
  // list as "CMU Sans Serif Demi Condensed" while CSS calls it "CMU Sans Demi
  // Condensed". Exact name first; otherwise a family holding every word of it.
  function findFamily(family) {
    const families = available.map(function (f) { return f.fontName.family; });
    if (families.indexOf(family) >= 0) return family;
    const words = family.toLowerCase().split(/\s+/);
    return families.find(function (x) {
      const l = x.toLowerCase();
      return words.every(function (w) { return l.indexOf(w) >= 0; });
    });
  }
  function pickFont(wantedFamily, wanted) {
    const family = findFamily(wantedFamily);
    if (!family) throw new Error('The font "' + wantedFamily + '" is not available in Figma.');
    const styles = available.filter(function (f) { return f.fontName.family === family; })
      .map(function (f) { return f.fontName.style; });
    for (const w of wanted) {
      const s = styles.find(function (x) { return x.toLowerCase() === w.toLowerCase(); });
      if (s) return { family: family, style: s };
    }
    return { family: family, style: styles[0] };
  }
  const F = {
    serif: pickFont(spec.fonts.serif, ['Regular']),
    serifBold: pickFont(spec.fonts.serif, ['Bold']),
    // the face is demi-weight in its regular style; CSS asks for 600 and
    // gets this same file
    ui: pickFont(spec.fonts.ui, ['Regular', 'Demi Condensed', 'DemiCondensed', 'Medium', 'SemiBold']),
  };
  for (const k in F) await figma.loadFontAsync(F[k]);
  log('Fonts: ' + Object.keys(F).map(function (k) { return F[k].family + ' ' + F[k].style; }).join(', '));

  async function text(name, chars, font, size, color, lineHeight) {
    const t = figma.createText();
    t.name = name;
    t.fontName = font;
    t.characters = chars;
    await length(t, ['fontSize'], size);
    t.fills = [await paint(color)];
    t.lineHeight = { value: lineHeight, unit: 'PERCENT' };
    return t;
  }

  function autoFrame(name, mode) {
    const f = figma.createFrame();
    f.name = name;
    f.layoutMode = mode;
    f.fills = [];
    f.clipsContent = false;
    return f;
  }

  const s = spec.sample, C = spec.colors, T = spec.text;

  // A set's name, or "<name> (import)" when the page already has one: a
  // new set never replaces an old one, since that would break its
  // instances.
  function setName(name, set) {
    const taken = figma.currentPage.findOne(function (n) {
      return n.type === 'COMPONENT_SET' && n.name === name && n !== set;
    });
    if (taken) log('A component set named "' + name + '" already exists on this page, so this one is "' + name + ' (import)". Swap or delete as you like.');
    return taken ? name + ' (import)' : name;
  }

  // ---- the Icon set, built by the Icons plugin (figma/icons/) ----
  // Found by name, this page first and then the rest of the file. The icon
  // variants' slot starts with one of its instances, and the slot suggests
  // the set.
  async function findIcons() {
    const isSet = function (n) { return n.type === 'COMPONENT_SET' && n.name === spec.iconSet; };
    let set = figma.currentPage.findOne(isSet);
    if (!set && figma.loadAllPagesAsync) {
      await figma.loadAllPagesAsync();
      set = figma.root.findOne(isSet);
    }
    if (!set) throw new Error('No "' + spec.iconSet + '" component set in this file. Run Main Character: Icons (figma/icons/) first.');
    const comps = {};
    for (const c of set.children) {
      let p = c.variantProperties;
      if (!p) {
        p = {};
        for (const kv of c.name.split(', ')) { const i = kv.indexOf('='); p[kv.slice(0, i)] = kv.slice(i + 1); }
      }
      (comps[p.for] = comps[p.for] || {})[p.style] = c;
    }
    if (!comps[s.icon] || !comps[s.icon].filled) throw new Error('"' + set.name + '" has no for=' + s.icon + ', style=filled variant; rebuild it with the Icons plugin.');
    log('Using "' + set.name + '" (' + set.children.length + ' variants) for the icon variants.');
    return { set: set, comps: comps };
  }
  const icons = await findIcons();

  // The picture the image variants show: the one chosen with the specs, or
  // a flat --bg-muted placeholder to replace in Figma.
  let imageFill = null;
  if (imageBytes) {
    imageFill = { type: 'IMAGE', imageHash: figma.createImage(new Uint8Array(imageBytes)).hash, scaleMode: 'FILL' };
    log('Image: ' + Math.round(imageBytes.length / 1024) + ' KB.');
  } else {
    log('No image chosen; the image variants show a placeholder fill.');
  }

  // ---- one variant ----
  async function variant(v, kind) {
    const horiz = v.orientation === 'horizontal';
    const c = figma.createComponent();
    c.name = 'size=' + v.size + ', orientation=' + v.orientation + ', visual=' + kind;
    c.layoutMode = horiz ? 'HORIZONTAL' : 'VERTICAL';
    c.primaryAxisSizingMode = 'FIXED';
    c.counterAxisSizingMode = 'FIXED';
    c.itemSpacing = 0;
    c.resize(px(v.card.width), px(v.card.height));
    if (!horiz) c.minWidth = px(v.card.width);  // the card fills its track; this is the floor
    c.clipsContent = true;
    c.fills = [await paint(C.card)];
    c.strokes = [await paint(C.border)];
    c.strokeAlign = 'INSIDE';
    c.strokeWeight = 1;
    await radius(c, spec.radius.card);

    // card-visual: a native slot (an instance can swap in any content), its
    // content centred, a hairline on the side facing the body. Naming the
    // slot names its SLOT property. A Figma without slots gets a plain frame.
    const slotted = typeof c.createSlot === 'function';
    const visual = slotted ? c.createSlot() : figma.createFrame();
    visual.name = 'card-visual';
    visual.layoutMode = 'HORIZONTAL';
    visual.clipsContent = true;  // whatever is dropped in stays inside
    visual.primaryAxisAlignItems = 'CENTER';
    visual.counterAxisAlignItems = 'CENTER';
    visual.primaryAxisSizingMode = 'FIXED';
    visual.counterAxisSizingMode = 'FIXED';
    visual.fills = [await paint(C.visual)];
    visual.strokes = [await paint(C.border)];
    visual.strokeAlign = 'INSIDE';
    visual.strokeTopWeight = 0; visual.strokeLeftWeight = 0;
    visual.strokeRightWeight = horiz ? 1 : 0;
    visual.strokeBottomWeight = horiz ? 0 : 1;
    if (!slotted) c.appendChild(visual);
    if (horiz) {
      visual.resize(px(v.visual.size), visual.height);
      visual.layoutSizingVertical = 'FILL';
    } else {
      visual.resize(visual.width, px(v.visual.size));
      visual.layoutSizingHorizontal = 'FILL';
    }
    // what the slot starts with: an icon at .9em of the glyph size; initials
    // dropped .25em so their baseline meets the icons' bottom (card.css
    // .card-visual.text); or a picture covering the whole visual
    let icon = null, initials = null;
    if (kind === 'icon') {
      icon = icons.comps[s.icon].filled.createInstance();
      icon.name = 'card-icon';
      visual.appendChild(icon);
      const side = px(v.visual.glyph * spec.visual.iconScale);
      icon.resize(side, side);
    } else if (kind === 'initials') {
      visual.paddingTop = px(v.visual.glyph * spec.visual.initialsDrop);
      initials = await text('initials', s.initials, F.serifBold, v.visual.glyph, C.glyph, 100);
      visual.appendChild(initials);
    } else {
      const img = figma.createRectangle();
      img.name = 'card-image';
      img.fills = [imageFill || await paint(C.visual)];
      visual.appendChild(img);
      img.layoutSizingHorizontal = 'FILL';
      img.layoutSizingVertical = 'FILL';
    }

    // card-body
    const body = autoFrame('card-body', 'VERTICAL');
    c.appendChild(body);
    body.layoutSizingHorizontal = 'FILL';
    body.layoutSizingVertical = 'FILL';
    await padding(body, v.body.padding);
    await length(body, ['itemSpacing'], v.body.gap);

    const type = await text('card-type', s.type, F.ui, v.type.font, C.type, T.typeLineHeight);
    type.textCase = 'UPPER';
    type.letterSpacing = { value: T.typeLetterSpacing, unit: 'PERCENT' };
    body.appendChild(type);
    type.layoutSizingHorizontal = 'FILL';
    type.textAutoResize = 'HEIGHT';

    const title = await text('card-title', s.title, F.serifBold, v.title.font, C.title, T.titleLineHeight);
    body.appendChild(title);
    title.layoutSizingHorizontal = 'FILL';
    title.textAutoResize = 'HEIGHT';
    title.textTruncation = 'ENDING';
    title.maxLines = v.title.lines;

    let desc = null;
    if (v.desc) {
      desc = await text('card-desc', s.description, F.serif, v.desc.font, C.desc, v.desc.lineHeight);
      body.appendChild(desc);
      desc.layoutSizingHorizontal = 'FILL';
      desc.textAutoResize = 'HEIGHT';
      desc.textTruncation = 'ENDING';
      desc.maxLines = v.desc.lines;
    }

    // card-badges: takes the rest of the body and sits its row on the
    // bottom edge, which is CSS's margin-top:auto; clips, never wraps
    const B = v.badges;
    const badges = autoFrame('card-badges', 'HORIZONTAL');
    badges.counterAxisAlignItems = 'MAX';
    badges.clipsContent = true;
    body.appendChild(badges);
    badges.layoutSizingHorizontal = 'FILL';
    badges.layoutSizingVertical = 'FILL';
    await length(badges, ['itemSpacing'], B.gap);

    const pills = [];
    for (let i = 0; i < B.max; i++) {
      const pill = autoFrame('card-badge', 'HORIZONTAL');
      pill.fills = [await paint(C.badgeFill)];
      pill.strokes = [await paint(C.border)];
      pill.strokeAlign = 'INSIDE';
      pill.strokeWeight = 1;
      await radius(pill, spec.radius.badge);
      await length(pill, ['paddingLeft', 'paddingRight'], B.padX);
      await length(pill, ['paddingTop', 'paddingBottom'], B.padY);
      const label = await text('label', s.badges[i] || 'badge', F.ui, B.font, C.badgeText, T.badgeLineHeight);
      pill.appendChild(label);
      badges.appendChild(pill);
      pill.layoutSizingHorizontal = 'HUG';
      pill.layoutSizingVertical = 'HUG';
      pills.push({ pill: pill, label: label });
    }
    const more = autoFrame('card-more', 'HORIZONTAL');
    await length(more, ['paddingLeft', 'paddingRight'], B.morePadX);
    await length(more, ['paddingTop', 'paddingBottom'], B.padY);
    const moreLabel = await text('label', s.more, F.ui, B.font, C.badgeText, T.badgeLineHeight);
    more.appendChild(moreLabel);
    badges.appendChild(more);
    more.layoutSizingHorizontal = 'HUG';
    more.layoutSizingVertical = 'HUG';

    return { component: c, slotted: slotted, icon: icon, initials: initials, type: type, title: title, desc: desc, pills: pills, more: more, moreLabel: moreLabel };
  }

  // ---- build, lay out, combine ----
  // a block per visual, side by side; in each, the vertical sizes in a row
  // and the horizontal forms stacked beneath
  const built = [];
  const gap = 40;
  let blockX = 0;
  for (const kind of spec.visuals) {
    let x = 0, y = 0, rowH = 0, blockW = 0;
    for (let i = 0; i < spec.variants.length; i++) {
      const v = spec.variants[i];
      const b = await variant(v, kind);
      built.push(b);
      if (v.orientation === 'horizontal' && x > 0 && spec.variants[i - 1].orientation === 'vertical') {
        x = 0; y += rowH + gap; rowH = 0;
      }
      b.component.x = blockX + x; b.component.y = y;
      if (v.orientation === 'horizontal') { y += b.component.height + gap; blockW = Math.max(blockW, b.component.width); }
      else { x += b.component.width + gap; rowH = Math.max(rowH, b.component.height); blockW = Math.max(blockW, x - gap); }
    }
    blockX += blockW + gap * 2;
    log('Built the ' + kind + ' variants.');
  }

  const set = figma.combineAsVariants(built.map(function (b) { return b.component; }), figma.currentPage);
  set.name = setName(spec.name, set);
  set.layoutMode = 'NONE';
  const pad = 40;
  for (const child of set.children) { child.x += pad; child.y += pad; }
  const maxX = Math.max.apply(null, set.children.map(function (n) { return n.x + n.width; }));
  const maxY = Math.max.apply(null, set.children.map(function (n) { return n.y + n.height; }));
  set.resizeWithoutConstraints(maxX + pad, maxY + pad);
  set.x = Math.round(figma.viewport.center.x - set.width / 2);
  set.y = Math.round(figma.viewport.center.y - set.height / 2);

  // ---- component properties ----
  // Layers in a slot can't be bound to properties, so with slots the
  // initials are edited in the instance's slot, like any slot content.
  const slotted = built.every(function (b) { return b.slotted; });
  const P = {
    type: set.addComponentProperty('type', 'TEXT', s.type),
    initials: slotted ? null : set.addComponentProperty('initials', 'TEXT', s.initials),
    title: set.addComponentProperty('title', 'TEXT', s.title),
    description: set.addComponentProperty('description', 'TEXT', s.description),
    showDescription: set.addComponentProperty('show description', 'BOOLEAN', true),
    more: set.addComponentProperty('more', 'TEXT', s.more),
    showMore: set.addComponentProperty('show more', 'BOOLEAN', false),
    badge: [], showBadge: [],
  };
  const maxSlots = Math.max.apply(null, spec.variants.map(function (v) { return v.badges.max; }));
  for (let i = 0; i < maxSlots; i++) {
    P.badge.push(set.addComponentProperty('badge ' + (i + 1), 'TEXT', s.badges[i] || 'badge'));
    // badge 1 always shows; the rest can be switched off
    P.showBadge.push(i === 0 ? null
      : set.addComponentProperty('show badge ' + (i + 1), 'BOOLEAN', i < s.shownBadges));
  }
  let exposeFailed = null;
  for (const b of built) {
    if (b.initials && P.initials) b.initials.componentPropertyReferences = { characters: P.initials };
    // the icon's own "for" and "style" pickers show on the card; inside a
    // slot Figma may refuse, and the icon is then picked by selecting it
    if (b.icon) {
      try { b.icon.isExposedInstance = true; } catch (e) { exposeFailed = e; }
    }
    b.type.componentPropertyReferences = { characters: P.type };
    b.title.componentPropertyReferences = { characters: P.title };
    if (b.desc) b.desc.componentPropertyReferences = { characters: P.description, visible: P.showDescription };
    b.pills.forEach(function (pill, i) {
      pill.label.componentPropertyReferences = { characters: P.badge[i] };
      if (P.showBadge[i]) pill.pill.componentPropertyReferences = { visible: P.showBadge[i] };
    });
    b.moreLabel.componentPropertyReferences = { characters: P.more };
    b.more.componentPropertyReferences = { visible: P.showMore };
  }
  if (exposeFailed) log('The Icon in the slot couldn\'t be exposed (' + exposeFailed.message + '); select the icon inside a card to change it.', 'warn');

  // the slot: one SLOT property for all the variants, suggesting the Icon
  // set first (anything else can still be dropped in)
  if (slotted) {
    const slotKeys = Object.keys(set.componentPropertyDefinitions).filter(function (k) {
      return set.componentPropertyDefinitions[k].type === 'SLOT';
    });
    if (slotKeys.length !== 1) log('Expected one slot property, found ' + slotKeys.length + ': ' + slotKeys.join(', '), 'warn');
    for (const k of slotKeys) {
      try { set.editComponentProperty(k, { preferredValues: [{ type: 'COMPONENT_SET', key: icons.set.key }] }); }
      catch (e) { log('Couldn\'t suggest the Icon set for the slot: ' + e.message, 'warn'); }
    }
    log('card-visual is a slot' + (slotKeys.length ? ' (' + slotKeys.join(', ') + ')' : '') + '; drop anything into it in an instance.');
  } else {
    log('This Figma has no slots, so card-visual is a plain frame.', 'warn');
  }

  figma.currentPage.selection = [set];
  figma.viewport.scrollAndZoomIntoView([set]);

  // ---- report ----
  const boundList = Object.keys(bound).sort();
  const missingList = Object.keys(missing).filter(function (t) { return !bound[t]; }).sort();
  log('Bound ' + boundList.length + ' tokens:\n' + boundList.map(function (t) { return '  ' + t + ' → ' + bound[t]; }).join('\n'));
  if (missingList.length) {
    log('No variable found for ' + missingList.length + ' tokens; they are literal values:\n  '
      + missingList.join(', ') + '\nRename the variable or add the name to STEMS in code.js, then run again.', 'warn');
  }
  log('Done: "' + set.name + '" with ' + built.length + ' variants, using "' + icons.set.name + '".', 'ok');
  figma.notify('Card built: ' + built.length + ' variants, ' + boundList.length + ' tokens bound'
    + (missingList.length ? ', ' + missingList.length + ' missing' : ''));
}
