// SPDX-License-Identifier: AGPL-3.0-or-later
// Runs inside a design page (injected by extract.mjs). Turns every specimen
// -- an element with data-figma-set -- into a layout tree the Figma plugin
// (code.js) can build: frames with auto layout, text with styled runs,
// instances of other sets, and the few native controls (checkbox, select
// chevron) the browser draws itself.
//
// Everything is read from the live page: computed styles for colour, type,
// padding, gap, radius, rules and shadows; the layout for sizes; and a
// sizing probe for whether a box hugs its content, fills its parent or is
// fixed. Colours and lengths are matched back to tokens.css by value, so
// the plugin can bind them to the file's variables. The page is expected to
// be in the dark theme (the extractor pins it), and dark has no two colour
// tokens with the same value, so a colour matches at most one token.
//
// Markup the walker reads (on the design pages):
//   data-figma-set="Button"          a specimen root: one variant of a set
//   data-figma-variant="kind=send, size=md, state=hover"
//   data-figma-instance="Button"     inside a specimen: an instance of that
//                                    set (with data-figma-variant) instead
//                                    of a drawing of it
//   data-figma-text="label"          the first text inside is a TEXT property
//   data-figma-bool="show count"     this layer's visibility is a BOOLEAN one
//   data-figma-name="..."            a layer name (default: the first class)
//   data-figma-skip                  leave it out

(function () {
  'use strict';

  // ---- colour ----
  const cv = document.createElement('canvas');
  cv.width = cv.height = 1;
  const cx = cv.getContext('2d', { willReadFrequently: true });
  // Any CSS colour string to rgba bytes, via the canvas, which also
  // resolves color-mix() and oklch() the way the page painted them.
  function rgba(str) {
    if (!str || str === 'transparent') return null;
    if (/^rgba?\(/.test(str)) {
      const n = str.match(/[\d.]+/g).map(Number);
      return { r: n[0], g: n[1], b: n[2], a: n.length > 3 ? n[3] : 1 };
    }
    cx.clearRect(0, 0, 1, 1);
    cx.fillStyle = '#000';
    cx.fillStyle = str;
    cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data;
    return { r: d[0], g: d[1], b: d[2], a: +(d[3] / 255).toFixed(3) };
  }
  const hex = c => '#' + [c.r, c.g, c.b].map(v => Math.round(v).toString(16).padStart(2, '0')).join('');
  const key = c => `${Math.round(c.r)},${Math.round(c.g)},${Math.round(c.b)}`;

  // ---- tokens: every custom property tokens.css puts on :root ----
  const root = document.documentElement;
  const tokenNames = [];
  (function collect(list) {
    for (const r of list) {
      if (r instanceof CSSStyleRule) {
        if (/^:root$/.test(r.selectorText.trim()))
          for (let i = 0; i < r.style.length; i++) if (r.style[i].startsWith('--') && !tokenNames.includes(r.style[i])) tokenNames.push(r.style[i]);
      } else if (r.cssRules) collect(r.cssRules);
    }
  })([...document.styleSheets].flatMap(s => { try { return [...s.cssRules]; } catch (e) { return []; } }));

  const rootCs = getComputedStyle(root);
  const REM = parseFloat(rootCs.fontSize) || 16;
  const colorTokens = new Map();               // 'r,g,b' -> name
  const lengthTokens = { space: new Map(), font: new Map(), radius: new Map() };
  const tokenHex = {};
  for (const name of tokenNames) {
    const raw = rootCs.getPropertyValue(name).trim();
    const bare = name.slice(2);
    const m = /^(space|font|radius)-/.exec(bare);
    if (m) {
      const n = /^([\d.]+)(rem|px)$/.exec(raw);
      if (n) lengthTokens[m[1]].set(+(parseFloat(n[1]) * (n[2] === 'rem' ? REM : 1)).toFixed(2), bare);
      continue;
    }
    if (!CSS.supports('color', raw)) continue;
    const probe = document.createElement('i');
    probe.style.color = `var(${name})`;
    root.appendChild(probe);
    const c = rgba(getComputedStyle(probe).color);
    probe.remove();
    if (c && !colorTokens.has(key(c))) { colorTokens.set(key(c), bare); tokenHex[bare] = hex(c); }
  }

  const usedTokens = new Set();
  function paint(str, opacity = 1) {
    const c = rgba(str);
    if (!c || c.a * opacity === 0) return null;
    const p = { hex: hex(c) };
    const t = colorTokens.get(key(c));
    if (t) { p.token = t; usedTokens.add(t); }
    const a = +(c.a * opacity).toFixed(3);
    if (a < 1) p.opacity = a;
    return p;
  }
  function len(px, kind) {
    const v = +(+px).toFixed(2);
    const t = kind && lengthTokens[kind].get(v);
    return t ? { px: v, token: t } : v;
  }
  const px = s => parseFloat(s) || 0;

  // ---- type ----
  function family(cs) {
    const f = cs.fontFamily;
    if (/CMU/i.test(f)) return 'ui';
    if (/Old Standard/i.test(f)) return 'serif';
    if (/mono/i.test(f)) return 'mono';
    return 'ui';
  }
  function runStyle(cs, opacity) {
    const s = {
      font: family(cs),
      weight: +cs.fontWeight || 400,
      italic: cs.fontStyle === 'italic',
      size: len(px(cs.fontSize), 'font'),
      color: paint(cs.color, opacity),
    };
    const ls = cs.letterSpacing === 'normal' ? 0 : px(cs.letterSpacing);
    if (ls) s.letterSpacing = +ls.toFixed(2);
    if (cs.textTransform === 'uppercase') s.case = 'UPPER';
    if (cs.textTransform === 'lowercase') s.case = 'LOWER';
    if (cs.textDecorationLine.includes('underline')) s.decoration = 'UNDERLINE';
    if (cs.fontVariantNumeric.includes('tabular')) s.tabular = true;
    return s;
  }
  function pseudoText(el, which) {
    const c = getComputedStyle(el, which).content;
    if (!c || c === 'none' || c === 'normal') return null;
    const m = /^"(.*)"$/.exec(c);
    return m ? m[1].replace(/\\"/g, '"') : null;
  }

  // ---- inline content: one text node with styled runs ----
  const INLINE = new Set(['inline', 'contents']);
  function isInlineOnly(el) {
    for (const n of el.childNodes) {
      if (n.nodeType === 3) continue;
      if (n.nodeType !== 1) continue;
      if (n.tagName === 'BR') continue;
      const cs = getComputedStyle(n);
      if (cs.display === 'none') continue;
      if (!INLINE.has(cs.display) || /^(INPUT|SELECT|TEXTAREA|BUTTON|IMG|SVG)$/.test(n.tagName)) return false;
      if (n.dataset.figmaInstance) return false;
      if (!isInlineOnly(n)) return false;
    }
    return true;
  }
  function inlineRuns(el, cs, opacity, runs) {
    const before = pseudoText(el, '::before');
    if (before) runs.push({ text: before, style: runStyle(getComputedStyle(el, '::before'), opacity) });
    for (const n of el.childNodes) {
      if (n.nodeType === 3) runs.push({ text: n.textContent, style: runStyle(cs, opacity) });
      else if (n.nodeType === 1 && n.tagName === 'BR') runs.push({ text: '\n', style: runStyle(cs, opacity), keep: true });
      else if (n.nodeType === 1) {
        const ncs = getComputedStyle(n);
        if (ncs.display === 'none') continue;
        inlineRuns(n, ncs, opacity * +ncs.opacity, runs);
      }
    }
    const after = pseudoText(el, '::after');
    if (after) runs.push({ text: after, style: runStyle(getComputedStyle(el, '::after'), opacity) });
    return runs;
  }
  // collapse white space the way the browser did for white-space: normal
  function collapse(runs, ws) {
    if (/pre/.test(ws)) return runs.filter(r => r.text.length);
    let lastSpace = true;
    const out = [];
    for (const r of runs) {
      let t = r.keep ? r.text : r.text.replace(/[ \t\n\r\f]+/g, ' ');
      if (lastSpace && t.startsWith(' ')) t = t.slice(1);
      if (t.length) { out.push({ text: t, style: r.style }); lastSpace = t.endsWith(' ') || t.endsWith('\n'); }
    }
    if (out.length) {
      const last = out[out.length - 1];
      last.text = last.text.replace(/ +$/, '');
      if (!last.text.length) out.pop();
    }
    return out;
  }
  // adjacent runs with the same style become one
  function merge(runs) {
    const out = [];
    for (const r of runs) {
      const prev = out[out.length - 1];
      if (prev && JSON.stringify(prev.style) === JSON.stringify(r.style)) prev.text += r.text;
      else out.push({ text: r.text, style: r.style });
    }
    return out;
  }
  function lineCount(rangeOrEl) {
    const rects = [...rangeOrEl.getClientRects()].filter(r => r.width > 0.5);
    const tops = [];
    for (const r of rects) if (!tops.some(t => Math.abs(t - r.top) < 3)) tops.push(r.top);
    return Math.max(1, tops.length);
  }

  // ---- the sizing probe ----
  // Does this box hug its content on an axis? Give it its content size
  // and see whether anything moves.
  function hugs(el, axis) {
    const before = el.getBoundingClientRect();
    const st = el.style;
    const saved = [st.width, st.height, st.flex, st.alignSelf];
    if (axis === 'h') { st.width = 'max-content'; st.flex = 'none'; }
    else { st.height = 'auto'; st.flex = 'none'; st.alignSelf = 'flex-start'; }
    const after = el.getBoundingClientRect();
    [st.width, st.height, st.flex, st.alignSelf] = saved;
    return axis === 'h' ? Math.abs(after.width - before.width) < 0.6 : Math.abs(after.height - before.height) < 0.6;
  }

  // ---- boxes ----
  function box(el, cs) {
    const r = el.getBoundingClientRect();
    const b = {
      bt: px(cs.borderTopWidth), br: px(cs.borderRightWidth), bb: px(cs.borderBottomWidth), bl: px(cs.borderLeftWidth),
      pt: px(cs.paddingTop), pr: px(cs.paddingRight), pb: px(cs.paddingBottom), pl: px(cs.paddingLeft),
    };
    b.r = r;
    b.content = {
      left: r.left + b.bl + b.pl, right: r.right - b.br - b.pr,
      top: r.top + b.bt + b.pt, bottom: r.bottom - b.bb - b.pb,
    };
    return b;
  }
  function decorate(n, el, cs, b) {
    const fill = paint(cs.backgroundColor);
    n.fills = fill ? [fill] : [];
    const sides = [b.bt, b.br, b.bb, b.bl];
    if (sides.some(Boolean)) {
      const colors = ['Top', 'Right', 'Bottom', 'Left'].map(s => cs[`border${s}Color`]);
      const i = sides.findIndex(Boolean);
      const stroke = paint(colors[i]);
      if (stroke) {
        n.stroke = { paint: stroke, weights: sides };
        const style = cs[`border${['Top', 'Right', 'Bottom', 'Left'][i]}Style`];
        if (style === 'dashed') n.stroke.dash = [4, 3];
        if (style === 'dotted') n.stroke.dash = [1, 2];
      }
    }
    const w = b.r.width, h = b.r.height;
    const corner = v => {
      if (v.endsWith('%')) return Math.min(w, h) * parseFloat(v) / 100;
      return px(v);
    };
    const radii = ['TopLeft', 'TopRight', 'BottomRight', 'BottomLeft'].map(c => corner(cs[`border${c}Radius`].split(' ')[0]));
    if (radii.some(Boolean)) n.radius = radii.map(v => len(v, 'radius'));
    const op = +cs.opacity;
    if (op < 1) n.opacity = op;
    if (cs.overflowX === 'hidden' || cs.overflowY === 'hidden' || cs.overflow === 'clip') n.clip = true;
    const shadows = parseShadows(cs.boxShadow);
    if (shadows.length) n.shadows = shadows;
    if (cs.outlineStyle !== 'none' && px(cs.outlineWidth) > 0) {
      const p = paint(cs.outlineColor);
      if (p) n.outline = { width: px(cs.outlineWidth), offset: px(cs.outlineOffset), paint: p };
    }
  }
  function parseShadows(s) {
    if (!s || s === 'none') return [];
    const parts = [];
    let depth = 0, start = 0;
    for (let i = 0; i < s.length; i++) {
      if (s[i] === '(') depth++; else if (s[i] === ')') depth--;
      else if (s[i] === ',' && depth === 0) { parts.push(s.slice(start, i)); start = i + 1; }
    }
    parts.push(s.slice(start));
    return parts.map(p => {
      const color = /(rgba?\([^)]*\)|#[0-9a-f]+)/i.exec(p);
      const nums = p.replace(color ? color[0] : '', '').match(/-?[\d.]+px/g) || [];
      return {
        paint: paint(color ? color[0] : 'rgba(0,0,0,.25)'), inset: /inset/.test(p),
        x: px(nums[0]), y: px(nums[1]), blur: px(nums[2]), spread: px(nums[3]),
      };
    });
  }
  function layerName(el) {
    if (el.dataset.figmaName) return el.dataset.figmaName;
    const cls = [...el.classList].find(c => !/^is-/.test(c) && c !== 'host');
    return cls || el.tagName.toLowerCase();
  }

  const ALIGN = { 'flex-start': 'MIN', start: 'MIN', left: 'MIN', normal: 'MIN', stretch: 'MIN', 'flex-end': 'MAX', end: 'MAX', right: 'MAX', center: 'CENTER', 'space-between': 'SPACE_BETWEEN', baseline: 'BASELINE', 'first baseline': 'BASELINE' };

  // ---- the walk ----
  // ctx: { inInstance, prop (pending text property), warnings }
  let cbSeq = 0;
  function walk(el, ctx, parent) {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || 'figmaSkip' in el.dataset) return null;
    // the inside of a closed <details> has a box but isn't drawn
    if (el.checkVisibility && !el.checkVisibility({ contentVisibilityAuto: true })) return null;
    const r = el.getBoundingClientRect();
    if (r.width < 0.5 && r.height < 0.5) return null;

    // a pending text property this element carries
    const prop = !ctx.inInstance && el.dataset.figmaText ? { name: el.dataset.figmaText, used: false } : null;
    const bool = !ctx.inInstance && el.dataset.figmaBool ? el.dataset.figmaBool : null;
    const sub = prop ? { ...ctx, prop } : ctx;

    let n;
    if (el.dataset.figmaInstance && parent) {
      const tree = draw(el, cs, r, { ...ctx, inInstance: true }, parent);
      if (!tree) return null;
      n = { type: 'instance', set: el.dataset.figmaInstance, variant: el.dataset.figmaVariant || '', texts: texts(tree), tree, w: tree.w, h: tree.h, name: tree.name };
    } else {
      n = draw(el, cs, r, sub, parent);
    }
    if (!n) return null;
    if (bool) n.bool = bool;
    // margins on an item of an auto-layout parent: wrap it in a padded frame
    const m = [cs.marginTop, cs.marginRight, cs.marginBottom, cs.marginLeft].map(px);
    if (parent && parent.mode !== 'NONE' && m.some(v => Math.abs(v) > 0.5) && cs.position !== 'absolute') {
      const auto = parent.autoMargins && parent.autoMargins.get(el);
      const mm = m.map((v, i) => (auto && auto.includes(i) ? 0 : Math.max(0, v)));
      if (mm.some(Boolean)) {
        n = {
          type: 'frame', name: (n.name || 'item') + ' (margin)', mode: n.type === 'text' ? 'H' : 'V', fills: [],
          padding: mm, gap: 0, children: [n], w: n.w + mm[1] + mm[3], h: n.h + mm[0] + mm[2],
          sizing: { h: n.sizing && n.sizing.h === 'FILL' ? 'FILL' : 'HUG', v: n.sizing && n.sizing.v === 'FILL' ? 'FILL' : 'HUG' },
        };
        n.children[0].sizing = { h: n.children[0].sizing && n.children[0].sizing.h === 'FILL' ? 'FILL' : n.children[0].sizing ? n.children[0].sizing.h : 'HUG', v: n.children[0].sizing ? n.children[0].sizing.v : 'HUG' };
      }
    }
    if (cs.position === 'absolute' || cs.position === 'fixed') {
      const pr = parent && parent.rect;
      if (pr) n.absolute = { x: +(r.left - pr.left).toFixed(2), y: +(r.top - pr.top).toFixed(2) };
    }
    return n;
  }

  function draw(el, cs, r, ctx, parent) {
    if (el.matches('input[type=checkbox]')) {
      el.dataset.figmaCb = String(++cbSeq);
      return { type: 'checkbox', name: 'checkbox', cb: cbSeq, checked: el.checked, w: +r.width.toFixed(2), h: +r.height.toFixed(2), radius: 2 };
    }
    if (el.matches('input, textarea')) return field(el, cs, ctx);
    if (el.matches('select')) return selectBox(el, cs, ctx);
    return frameOrText(el, cs, ctx, parent);
  }

  // an instance's texts in order, with their runs, so the plugin can set
  // each one and keep its styling
  function texts(n, out = []) {
    if (!n) return out;
    if (n.type === 'text') out.push({ chars: n.chars, runs: n.runs });
    for (const c of n.children || []) texts(c.type === 'instance' ? c.tree : c, out);
    return out;
  }

  function takeProp(ctx) {
    if (ctx.prop && !ctx.prop.used) { ctx.prop.used = true; return ctx.prop.name; }
    return null;
  }

  function textNode(name, runs, cs, ctx, measure) {
    runs = merge(collapse(runs, cs.whiteSpace));
    if (!runs.length) return null;
    const t = { type: 'text', name, chars: runs.map(r => r.text).join(''), runs: runs.map(r => ({ len: r.text.length, style: r.style })) };
    t.lineHeight = cs.lineHeight === 'normal' ? null : +px(cs.lineHeight).toFixed(2);
    if (/center/.test(cs.textAlign)) t.align = 'CENTER';
    else if (/right|end/.test(cs.textAlign)) t.align = 'RIGHT';
    t.w = measure.width; t.h = measure.height;
    t.lines = measure.lines;
    if (cs.textOverflow === 'ellipsis' && cs.whiteSpace === 'nowrap') t.truncate = true;
    const p = takeProp(ctx);
    if (p) t.prop = p;
    return t;
  }

  // a box: a frame, or (no decoration, inline content only) a bare text
  function frameOrText(el, cs, ctx, parent) {
    const b = box(el, cs);
    const decorated = paint(cs.backgroundColor) || [b.bt, b.br, b.bb, b.bl, b.pt, b.pr, b.pb, b.pl].some(v => v > 0.5)
      || px(cs.minWidth) > 0 || px(cs.minHeight) > 0 || +cs.opacity < 1 || cs.boxShadow !== 'none'
      || (cs.outlineStyle !== 'none' && px(cs.outlineWidth) > 0);
    const flex = /flex/.test(cs.display);
    if (!decorated && !flex && isInlineOnly(el)) {
      const range = document.createRange();
      range.selectNodeContents(el);
      const rr = range.getBoundingClientRect();
      const t = textNode(layerName(el), inlineRuns(el, cs, 1, []), cs, ctx, { width: Math.max(rr.width, b.r.width && cs.display !== 'inline' ? b.r.width : 0), height: Math.max(rr.height, b.r.height), lines: lineCount(range) });
      if (!t) return null;
      t.sizing = textSizing(el, cs, t, parent);
      return t;
    }
    return frame(el, cs, b, ctx, parent);
  }

  function textSizing(el, cs, t, parent) {
    const grow = parent && parent.mode !== 'NONE' && px(cs.flexGrow) > 0;
    const fillsWidth = parent && parent.content && Math.abs(t.w - (parent.content.right - parent.content.left)) < 1;
    let h = 'HUG';
    if (t.truncate || t.lines > 1) h = 'FIXED';
    if (grow || (parent && parent.mode === 'V' && fillsWidth && cs.display !== 'inline' && t.lines > 1)) h = 'FILL';
    if (parent && parent.mode === 'H' && grow) h = 'FILL';
    return { h, v: 'HUG' };
  }

  function frame(el, cs, b, ctx, parent) {
    const n = { type: 'frame', name: layerName(el), w: +b.r.width.toFixed(2), h: +b.r.height.toFixed(2) };
    decorate(n, el, cs, b);
    const flex = /flex/.test(cs.display);
    n.padding = [b.pt, b.pr, b.pb, b.pl].map(v => len(v, 'space'));
    const info = { rect: b.r, content: b.content, mode: 'V' };

    // children: elements (in flow and absolute) and anonymous text runs
    const kids = [];
    if (flex || el.tagName === 'BUTTON' || el.tagName === 'SUMMARY' || !isInlineOnly(el)) {
      const before = pseudoText(el, '::before');
      if (before && flex) kids.push({ pseudo: '::before', text: before });
      for (const c of el.childNodes) {
        if (c.nodeType === 3) { if (c.textContent.trim()) kids.push({ textNode: c }); }
        else if (c.nodeType === 1) kids.push({ el: c });
      }
      const after = pseudoText(el, '::after');
      if (after && flex) kids.push({ pseudo: '::after', text: after });
      // a button or summary that isn't flex but holds only inline content:
      // the browser centres it; treat it as a centred row of one text
      if (!flex && isInlineOnly(el)) { kids.length = 0; kids.push({ inline: true }); }
    } else {
      kids.push({ inline: true });
    }

    if (flex) {
      const row = /row/.test(cs.flexDirection);
      n.mode = info.mode = row ? 'H' : 'V';
      n.gap = len(px(row ? cs.columnGap : cs.rowGap), 'space');
      if (cs.flexWrap === 'wrap' && row) { n.wrap = true; n.crossGap = len(px(cs.rowGap), 'space'); }
      n.justify = ALIGN[cs.justifyContent] || 'MIN';
      n.align = ALIGN[cs.alignItems] || 'MIN';
      if (n.align === 'BASELINE' && !row) n.align = 'MIN';
      info.stretch = /normal|stretch/.test(cs.alignItems);
    } else if (el.tagName === 'BUTTON' || (el.tagName === 'SUMMARY' && kids.length === 1 && kids[0].inline)) {
      n.mode = info.mode = 'H';
      n.gap = 0;
      n.justify = /center/.test(cs.textAlign) ? 'CENTER' : /right|end/.test(cs.textAlign) ? 'MAX' : 'MIN';
      n.align = 'CENTER';
    } else {
      n.mode = info.mode = 'V';
      n.gap = 0;
      n.justify = 'MIN';
      n.align = 'MIN';
      info.stretch = true;
    }

    // auto margins in a flex row: a spacer that takes the free room
    if (flex) {
      info.autoMargins = new Map();
      for (const k of kids) {
        if (!k.el) continue;
        // computed margins are resolved px; the rules say which were auto
        const auto = [];
        if (isAuto(k.el, 'margin-left')) auto.push(3);
        if (isAuto(k.el, 'margin-right')) auto.push(1);
        if (auto.length) info.autoMargins.set(k.el, auto);
      }
    }

    n.children = [];
    for (const k of kids) {
      if (k.inline) {
        const range = document.createRange();
        range.selectNodeContents(el);
        const rr = range.getBoundingClientRect();
        const t = textNode('text', inlineRuns(el, cs, 1, []), cs, ctx, { width: rr.width, height: rr.height, lines: lineCount(range) });
        if (t) {
          const cw = b.content.right - b.content.left;
          t.sizing = { h: t.truncate || t.lines > 1 ? (Math.abs(t.w - cw) < 1 || n.mode === 'V' ? 'FILL' : 'FIXED') : 'HUG', v: 'HUG' };
          if (n.mode === 'V' && t.lines > 1) t.sizing.h = 'FILL';
          n.children.push(t);
        }
        continue;
      }
      if (k.pseudo) {
        const pcs = getComputedStyle(el, k.pseudo);
        const t = textNode(k.pseudo.slice(2), [{ text: k.text, style: runStyle(pcs, 1) }], pcs, { prop: null }, { width: 0, height: px(pcs.lineHeight) || 0, lines: 1 });
        if (t) { t.sizing = { h: 'HUG', v: 'HUG' }; n.children.push(t); }
        continue;
      }
      if (k.textNode) {
        const range = document.createRange();
        range.selectNode(k.textNode);
        const rr = range.getBoundingClientRect();
        const t = textNode('text', [{ text: k.textNode.textContent, style: runStyle(cs, 1) }], cs, ctx, { width: rr.width, height: rr.height, lines: lineCount(range) });
        if (t) { t.sizing = { h: t.lines > 1 ? 'FIXED' : 'HUG', v: 'HUG' }; n.children.push(t); }
        continue;
      }
      const auto = info.autoMargins && info.autoMargins.get(k.el);
      if (auto && auto.includes(3)) n.children.push({ type: 'spacer', name: 'spacer' });
      const c = walk(k.el, ctx, info);
      if (c) {
        if (!c.sizing) c.sizing = childSizing(k.el, c, info);
        n.children.push(c);
      }
      if (auto && auto.includes(1)) n.children.push({ type: 'spacer', name: 'spacer' });
    }

    // an auto margin only gets the room nobody grows into
    if (flex) {
      const axis = n.mode === 'H' ? 'h' : 'v';
      if (n.children.some(c => c.type !== 'spacer' && c.sizing && c.sizing[axis] === 'FILL'))
        n.children = n.children.filter(c => c.type !== 'spacer');
    }

    // a block box with several block children: stack them if they stack
    if (!flex && n.mode === 'V' && el.tagName !== 'BUTTON') stackFromLayout(el, n, b);

    n.sizing = n.sizing || null;   // the parent sets it (childSizing), the root below
    const mw = px(cs.minWidth), mh = px(cs.minHeight);
    if (mw > 0) n.minW = +mw.toFixed(2);
    if (mh > 0) n.minH = +mh.toFixed(2);
    return n;
  }

  // Which declared margins were `auto`? Computed style resolves them to
  // pixels, so ask the rules: the element's own matching declarations.
  function isAuto(el, prop) {
    if (el.style.getPropertyValue(prop) === 'auto') return true;
    let found = false;
    const visit = list => {
      for (const r of list) {
        if (r instanceof CSSStyleRule) {
          let match = false;
          try { match = el.matches(r.selectorText); } catch (e) {}
          if (!match) continue;
          const v = r.style.getPropertyValue(prop) || (prop.startsWith('margin') && /\bauto\b/.test(r.style.getPropertyValue('margin')) ? 'auto?' : '');
          if (v === 'auto') found = true;
          if (v === 'auto?') {
            const parts = r.style.getPropertyValue('margin').split(/\s+/);
            const idx = { 'margin-top': 0, 'margin-right': 1, 'margin-bottom': 2, 'margin-left': 3 }[prop];
            const full = parts.length === 1 ? [parts[0], parts[0], parts[0], parts[0]] : parts.length === 2 ? [parts[0], parts[1], parts[0], parts[1]] : parts.length === 3 ? [parts[0], parts[1], parts[2], parts[1]] : parts;
            if (full[idx] === 'auto') found = true;
          }
        } else if (r.cssRules && (r instanceof CSSMediaRule ? matchMedia(r.conditionText).matches : true)) visit(r.cssRules);
      }
    };
    for (const s of document.styleSheets) { try { visit(s.cssRules); } catch (e) {} }
    return found;
  }

  function childSizing(el, c, parent) {
    if (c.type === 'text' || c.type === 'spacer') return c.sizing;
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const cw = parent.content.right - parent.content.left;
    const ch = parent.content.bottom - parent.content.top;
    const grow = px(cs.flexGrow) > 0;
    const alignSelf = cs.alignSelf === 'auto' ? null : cs.alignSelf;
    const stretch = alignSelf ? /normal|stretch/.test(alignSelf) : parent.stretch;
    const s = {};
    if (parent.mode === 'H') {
      s.h = grow ? 'FILL' : hugs(el, 'h') ? 'HUG' : 'FIXED';
      s.v = stretch && Math.abs(r.height - ch) < 1 && cs.height === 'auto' ? 'FILL' : hugs(el, 'v') ? 'HUG' : 'FIXED';
    } else {
      s.h = stretch && Math.abs(r.width - cw) < 1 ? 'FILL' : hugs(el, 'h') ? 'HUG' : 'FIXED';
      s.v = grow ? 'FILL' : hugs(el, 'v') ? 'HUG' : 'FIXED';
    }
    if (c.type === 'checkbox') return { h: 'FIXED', v: 'FIXED' };
    return s;
  }

  // block flow: children that sit one under the other become a vertical
  // auto layout; gaps between them (margins) become its spacing when they
  // are all the same, else the frame keeps measured positions
  function stackFromLayout(el, n, b) {
    const flow = n.children.filter(c => !c.absolute);
    if (flow.length < 2) return;
    const els = [];
    for (const c of el.children) {
      const cs = getComputedStyle(c);
      if (cs.display !== 'none' && cs.position !== 'absolute' && cs.position !== 'fixed' && c.getBoundingClientRect().height > 0) els.push(c);
    }
    if (els.length !== flow.length) return;
    const rects = els.map(e => e.getBoundingClientRect());
    const gaps = [];
    for (let i = 1; i < rects.length; i++) gaps.push(rects[i].top - rects[i - 1].bottom);
    if (gaps.some(g => g < -0.5)) { toAbsolute(n, els, b); return; }
    if (gaps.some(g => Math.abs(g - gaps[0]) > 0.6)) { toAbsolute(n, els, b); return; }
    n.gap = len(+gaps[0].toFixed(2), 'space');
    const top = rects[0].top - (b.r.top + b.bt);
    const bottom = (b.r.bottom - b.bb) - rects[rects.length - 1].bottom;
    n.padding[0] = len(+top.toFixed(2), 'space');
    n.padding[2] = len(+bottom.toFixed(2), 'space');
  }
  function toAbsolute(n, els, b) {
    n.mode = 'NONE';
    n.children.filter(c => !c.absolute).forEach((c, i) => {
      const r = els[i].getBoundingClientRect();
      c.at = { x: +(r.left - b.r.left).toFixed(2), y: +(r.top - b.r.top).toFixed(2) };
      c.sizing = { h: 'FIXED', v: 'FIXED' };
    });
  }

  // input and textarea: the well, and its value or placeholder as text
  function field(el, cs, ctx) {
    const b = box(el, cs);
    const n = { type: 'frame', name: layerName(el), w: +b.r.width.toFixed(2), h: +b.r.height.toFixed(2) };
    decorate(n, el, cs, b);
    const area = el.tagName === 'TEXTAREA';
    n.mode = area ? 'V' : 'H';
    n.padding = [b.pt, b.pr, b.pb, b.pl].map(v => len(v, 'space'));
    n.gap = 0;
    n.justify = 'MIN';
    n.align = area ? 'MIN' : 'CENTER';
    n.clip = true;
    const value = el.value;
    const shown = value || el.placeholder || '';
    let style;
    if (value) style = runStyle(cs, 1);
    else {
      const ph = getComputedStyle(el, '::placeholder');
      style = runStyle(cs, 1);
      style.color = paint(ph.color, +ph.opacity);
    }
    const contentW = b.content.right - b.content.left;
    const t = { type: 'text', name: value ? 'value' : 'placeholder', chars: shown, runs: [{ len: shown.length, style }] };
    t.lineHeight = cs.lineHeight === 'normal' ? null : +px(cs.lineHeight).toFixed(2);
    t.w = contentW; t.h = t.lineHeight || px(cs.fontSize) * 1.2;
    t.sizing = { h: 'FILL', v: 'HUG' };
    if (!area) t.truncate = true;
    const p = takeProp(ctx);
    if (p) t.prop = p;
    n.children = [t];
    const mh = px(cs.minHeight);
    if (mh > 0) n.minH = +mh.toFixed(2);
    return n;
  }

  // select: the closed control, its value and the browser's chevron
  function selectBox(el, cs, ctx) {
    const n = field(el, cs, ctx);
    const opt = el.options[el.selectedIndex];
    const t = n.children[0];
    t.name = 'value';
    t.chars = opt ? opt.textContent.trim() : '';
    t.runs = [{ len: t.chars.length, style: runStyle(cs, 1) }];
    t.sizing = { h: 'FILL', v: 'HUG' };
    n.mode = 'H';
    n.align = 'CENTER';
    n.gap = 6;
    n.children.push({ type: 'chevron', name: 'chevron', w: 9, h: 5, paint: paint(cs.color) });
    return n;
  }

  // ---- specimens ----
  function rootSizing(el, n) {
    if (n.type === 'checkbox') return { h: 'FIXED', v: 'FIXED' };
    if (n.type === 'text') return { h: n.lines > 1 || n.truncate ? 'FIXED' : 'HUG', v: 'HUG' };
    return { h: hugs(el, 'h') ? 'HUG' : 'FIXED', v: hugs(el, 'v') ? 'HUG' : 'FIXED' };
  }

  // Settle sizing the way Figma needs it. A row's last text takes the rest
  // of a row that doesn't hug (so it wraps when the instance is resized),
  // and a frame that hugs an axis can't hold a child that fills it: such a
  // frame is fixed at its measured size instead.
  function settle(n) {
    const kids = (n.children || []).filter(c => !c.absolute);
    for (const c of n.children || []) settle(c);
    if (n.type !== 'frame' || !n.sizing) return;
    if (n.mode === 'H' && n.justify === 'MIN' && n.sizing.h !== 'HUG' && !kids.some(c => c.sizing && c.sizing.h === 'FILL')) {
      const last = kids[kids.length - 1];
      if (last && last.type === 'text' && last.sizing.h === 'HUG' && !last.align) last.sizing.h = 'FILL';
    }
    for (const axis of ['h', 'v'])
      if (n.sizing[axis] === 'HUG' && kids.some(c => c.type !== 'spacer' && c.sizing && c.sizing[axis] === 'FILL')) n.sizing[axis] = 'FIXED';
  }

  window.__mcWalk = function () {
    const sets = [];
    const warnings = [];
    for (const el of document.querySelectorAll('[data-figma-set]')) {
      const name = el.dataset.figmaSet;
      const variant = (el.dataset.figmaVariant || '').split(',').map(s => s.trim()).filter(Boolean).join(', ');
      let set = sets.find(s => s.name === name);
      if (!set) { set = { name, variants: [] }; sets.push(set); }
      if (set.variants.some(v => v.name === variant)) warnings.push(`${name}: variant "${variant}" appears twice`);
      const ctx = { inInstance: false, prop: null };
      const tree = walk(el, ctx, null);
      if (!tree) { warnings.push(`${name} ${variant}: nothing visible`); continue; }
      tree.sizing = rootSizing(el, tree);
      settle(tree);
      set.variants.push({ name: variant, tree });
    }
    // every variant of a set must name the same properties
    for (const s of sets) {
      const keys = s.variants.map(v => v.name.split(', ').map(p => p.split('=')[0]).sort().join(','));
      if (new Set(keys).size > 1) warnings.push(`${s.name}: variants name different properties (${[...new Set(keys)].join(' | ')})`);
    }
    const fallback = {};
    for (const t of [...usedTokens].sort()) fallback[t] = tokenHex[t];
    return { remPx: REM, sets, fallback, warnings };
  };
  // for the extractor: a sampled colour as a paint, matched to a token
  window.__mcPaint = str => paint(str);
})();
