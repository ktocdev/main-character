// SPDX-License-Identifier: AGPL-3.0-or-later
// A stand-in for the Figma plugin API, enough of it to run code.js outside
// Figma. It keeps the rules the real API enforces and that are easy to get
// wrong: auto-layout sizing needs an auto-layout frame or parent, text needs
// its fonts loaded before its characters change, variant names must agree,
// component property references must name a property the set has. Where
// the real API would throw, this throws.

const ENUM = {
  layoutMode: ['NONE', 'HORIZONTAL', 'VERTICAL'],
  primaryAxisAlignItems: ['MIN', 'MAX', 'CENTER', 'SPACE_BETWEEN'],
  counterAxisAlignItems: ['MIN', 'MAX', 'CENTER', 'BASELINE'],
  layoutSizing: ['FIXED', 'HUG', 'FILL'],
  strokeAlign: ['INSIDE', 'OUTSIDE', 'CENTER'],
  textAutoResize: ['NONE', 'WIDTH_AND_HEIGHT', 'HEIGHT', 'TRUNCATE'],
  textTruncation: ['DISABLED', 'ENDING'],
  textAlignHorizontal: ['LEFT', 'CENTER', 'RIGHT', 'JUSTIFIED'],
  textCase: ['ORIGINAL', 'UPPER', 'LOWER', 'TITLE'],
  textDecoration: ['NONE', 'UNDERLINE', 'STRIKETHROUGH'],
  layoutWrap: ['NO_WRAP', 'WRAP'],
  layoutPositioning: ['AUTO', 'ABSOLUTE'],
};
const BINDABLE = new Set(['paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'itemSpacing', 'counterAxisSpacing',
  'topLeftRadius', 'topRightRadius', 'bottomLeftRadius', 'bottomRightRadius', 'width', 'height', 'minWidth', 'minHeight',
  'strokeWeight', 'opacity', 'fontSize']);

function check(cond, msg) { if (!cond) throw new Error('figma: ' + msg); }
function oneOf(kind, v) { check(ENUM[kind].includes(v), `${kind} cannot be ${JSON.stringify(v)}`); }

let ids = 0;
export function makeFigma({ fonts, variables }) {
  const loaded = new Set();
  const fontId = f => f.family + '|' + f.style;
  const logs = [];

  class Node {
    constructor(type) {
      this.id = String(++ids); this.type = type; this.name = type.toLowerCase();
      this.parent = null; this.x = 0; this.y = 0; this.width = 100; this.height = 100;
      this.visible = true; this.opacity = 1; this.fills = []; this.strokes = []; this.effects = [];
      this.boundVariables = {}; this._refs = null; this._layoutPositioning = 'AUTO';
    }
    get layoutPositioning() { return this._layoutPositioning; }
    set layoutPositioning(v) {
      oneOf('layoutPositioning', v);
      check(v === 'AUTO' || (this.parent && this.parent.layoutMode && this.parent.layoutMode !== 'NONE'),
        `${this.name}: ABSOLUTE needs an auto-layout parent`);
      this._layoutPositioning = v;
    }
    // like Figma: a layer inside an instance is placed from its main, not
    // by a plugin
    get x() { return this._x; }
    set x(v) { check(!this._inInstance, 'in set_x: This property cannot be overridden in an instance: relative-transform'); this._x = v; }
    get y() { return this._y; }
    set y(v) { check(!this._inInstance, 'in set_y: This property cannot be overridden in an instance: relative-transform'); this._y = v; }
    resize(w, h) {
      check(w >= 0.01 && h >= 0.01, `${this.name}: resize(${w}, ${h})`);
      this.width = w; this.height = h;
      if (this._sizingH !== undefined) { this._sizingH = 'FIXED'; this._sizingV = 'FIXED'; }
      // like Figma: resizing an instance lays out its SCALE-constrained
      // layers again from its main
      const m = this.type === 'INSTANCE' && this.mainComponent;
      if (m) this.children.forEach((c, i) => {
        const mc = m.children[i], k = c.constraints;
        if (!mc || !k || k.horizontal !== 'SCALE' || k.vertical !== 'SCALE') return;
        const sx = w / m.width, sy = h / m.height;
        c._x = mc.x * sx; c._y = mc.y * sy; c.width = mc.width * sx; c.height = mc.height * sy;
      });
    }
    resizeWithoutConstraints(w, h) { this.resize(w, h); }
    setBoundVariable(field, v) {
      check(BINDABLE.has(field), `cannot bind ${field}`);
      check(v && v.id, 'binding needs a variable');
      if (field === 'fontSize') check(this.type !== 'TEXT', 'bind fontSize on a text node with setRangeBoundVariable');
      this.boundVariables[field] = v.id;
    }
    get componentPropertyReferences() { return this._refs; }
    set componentPropertyReferences(r) {
      let set = this.parent;
      while (set && set.type !== 'COMPONENT_SET') {
        check(set.type !== 'INSTANCE', `${this.name}: can't reference a property from inside an instance`);
        // like Figma: a slot's content can't be bound to properties
        check(set.type !== 'SLOT', `${this.name}: can't reference a property from inside a slot`);
        set = set.parent;
      }
      check(set, `${this.name}: not inside a component set`);
      for (const [k, key] of Object.entries(r)) {
        check(['characters', 'visible', 'mainComponent'].includes(k), `bad reference ${k}`);
        const def = set.componentPropertyDefinitions[key];
        check(def, `${this.name}: no property ${key} on ${set.name}`);
        if (k === 'characters') check(this.type === 'TEXT' && def.type === 'TEXT', `${this.name}: characters needs a TEXT property on a text layer`);
        if (k === 'visible') check(def.type === 'BOOLEAN', `${this.name}: visible needs a BOOLEAN property`);
      }
      this._refs = r;
    }
    remove() {
      const p = this.parent;
      if (p) p.children.splice(p.children.indexOf(this), 1);
      this.parent = null;
      // like Figma, a group that loses its last child goes too
      if (p && p.type === 'GROUP' && !p.children.length) p.remove();
    }
    get constraints() { return this._constraints; }
    set constraints(c) {
      const ok = ['MIN', 'CENTER', 'MAX', 'STRETCH', 'SCALE'];
      check(c && ok.includes(c.horizontal) && ok.includes(c.vertical), `${this.name}: constraints ${JSON.stringify(c)}`);
      this._constraints = c;
    }
    get isExposedInstance() { return !!this._exposed; }
    set isExposedInstance(v) {
      check(this.type === 'INSTANCE', `${this.name}: only an instance can be exposed`);
      let p = this.parent;
      while (p && p.type !== 'COMPONENT') { check(p.type !== 'INSTANCE', `${this.name}: exposed from inside another instance`); p = p.parent; }
      check(p, `${this.name}: exposed instances must sit in a component`);
      this._exposed = v;
    }
    // Absolute bounds: x and y add up through the ancestors, except a group,
    // which (as in Figma) has no coordinate space of its own. A centred
    // stroke draws half its weight outside the geometry.
    get absoluteBoundingBox() {
      let x = this.x, y = this.y;
      for (let p = this.parent; p && p.type !== 'PAGE'; p = p.parent) if (p.type !== 'GROUP') { x += p.x; y += p.y; }
      return { x, y, width: this.width, height: this.height };
    }
    get absoluteRenderBounds() {
      const b = this.absoluteBoundingBox;
      const h = this.strokes.length ? (this.strokeWeight || 1) / 2 : 0;
      // _ink: paths drawn off from the node's box ({dx, dy}), the way a
      // card's switched outline icon shows in Figma with its box in place
      const o = this._ink || { dx: 0, dy: 0 };
      return { x: b.x - h + o.dx, y: b.y - h + o.dy, width: b.width + 2 * h, height: b.height + 2 * h };
    }
    // like Figma: the outline is a new vector beside the node, painted with
    // its stroke and as big as the stroke draws; null when there is no
    // stroke. Where Figma puts it isn't documented, and real icons came out
    // shifted, so the stand-in puts it at the geometry's corner (half a
    // stroke off) to make the plugin's re-alignment do its job.
    outlineStroke() {
      check(this.parent, `${this.name}: outlineStroke on a removed node`);
      if (!this.strokes.length) return null;
      const v = new Node('VECTOR');
      v.fills = this.strokes;
      const r = this.absoluteRenderBounds;
      v.x = this.x; v.y = this.y; v.width = r.width; v.height = r.height;
      v._drew = r;
      this.parent.insertChild(this.parent.children.indexOf(this) + 1, v);
      return v;
    }
  }

  class Parent extends Node {
    constructor(type) { super(type); this.children = []; }
    appendChild(c) {
      check(c instanceof Node, 'appendChild needs a node');
      if (c.parent) c.remove();
      c.parent = this; this.children.push(c);
    }
    insertChild(i, c) {
      check(c instanceof Node, 'insertChild needs a node');
      if (c.parent) c.remove();
      check(i >= 0 && i <= this.children.length, `insertChild index ${i}`);
      c.parent = this; this.children.splice(i, 0, c);
    }
    findAll(fn) {
      const out = [];
      const go = n => { for (const c of n.children || []) { if (!fn || fn(c)) out.push(c); go(c); } };
      go(this);
      return out;
    }
    findOne(fn) { return this.findAll(fn)[0] || null; }
  }

  class Frame extends Parent {
    constructor(type = 'FRAME') {
      super(type);
      this._layoutMode = 'NONE'; this._sizingH = 'FIXED'; this._sizingV = 'FIXED';
      this.paddingTop = this.paddingRight = this.paddingBottom = this.paddingLeft = 0;
      this.itemSpacing = 0; this.counterAxisSpacing = 0; this._layoutWrap = 'NO_WRAP';
      this._primary = 'MIN'; this._counter = 'MIN'; this.clipsContent = true; this.layoutGrow = 0;
      this.strokeWeight = 0; this.cornerRadius = 0;
    }
    get layoutMode() { return this._layoutMode; }
    set layoutMode(v) { oneOf('layoutMode', v); this._layoutMode = v; if (v !== 'NONE') { this._sizingH = 'HUG'; this._sizingV = 'HUG'; } }
    get layoutWrap() { return this._layoutWrap; }
    set layoutWrap(v) { oneOf('layoutWrap', v); check(v === 'NO_WRAP' || this._layoutMode === 'HORIZONTAL', `${this.name}: WRAP needs HORIZONTAL`); this._layoutWrap = v; }
    get primaryAxisAlignItems() { return this._primary; }
    set primaryAxisAlignItems(v) { oneOf('primaryAxisAlignItems', v); this._primary = v; }
    get counterAxisAlignItems() { return this._counter; }
    set counterAxisAlignItems(v) {
      oneOf('counterAxisAlignItems', v);
      check(v !== 'BASELINE' || this._layoutMode === 'HORIZONTAL', `${this.name}: BASELINE needs HORIZONTAL`);
      this._counter = v;
    }
    get strokeAlign() { return this._strokeAlign; }
    set strokeAlign(v) { oneOf('strokeAlign', v); this._strokeAlign = v; }
    get minWidth() { return this._minW; }
    set minWidth(v) { check(this._layoutMode !== 'NONE' || autoParent(this), `${this.name}: minWidth needs auto layout`); this._minW = v; }
    get minHeight() { return this._minH; }
    set minHeight(v) { check(this._layoutMode !== 'NONE' || autoParent(this), `${this.name}: minHeight needs auto layout`); this._minH = v; }
    get layoutSizingHorizontal() { return this._sizingH; }
    set layoutSizingHorizontal(v) { sizing(this, 'h', v); }
    get layoutSizingVertical() { return this._sizingV; }
    set layoutSizingVertical(v) { sizing(this, 'v', v); }
  }
  function autoParent(n) { return n.parent && n.parent.layoutMode && n.parent.layoutMode !== 'NONE'; }
  function sizing(n, axis, v) {
    oneOf('layoutSizing', v);
    if (v === 'FILL') {
      check(autoParent(n), `${n.name}: FILL needs an auto-layout parent`);
      const p = n.parent;
      const pAxis = (p.layoutMode === 'HORIZONTAL') === (axis === 'h') ? 'primary' : 'counter';
      const pSize = axis === 'h' ? p._sizingH : p._sizingV;
      check(!(pAxis === 'primary' && pSize === 'HUG'), `${n.name}: FILL on ${axis} inside a parent that hugs that axis`);
    }
    if (v === 'HUG') check(n.type === 'TEXT' || n.type === 'INSTANCE' || (n.layoutMode && n.layoutMode !== 'NONE'), `${n.name}: HUG needs auto layout or text`);
    if (axis === 'h') n._sizingH = v; else n._sizingV = v;
  }

  class Rect extends Node {
    constructor() { super('RECTANGLE'); this._sizingH = 'FIXED'; this._sizingV = 'FIXED'; }
    get layoutSizingHorizontal() { return this._sizingH; }
    set layoutSizingHorizontal(v) { sizing(this, 'h', v); }
    get layoutSizingVertical() { return this._sizingV; }
    set layoutSizingVertical(v) { sizing(this, 'v', v); }
  }

  // A slot: a frame whose content an instance can replace freely. Like
  // Figma, createSlot() adds it as a direct child of the component, with a
  // SLOT property named after the slot (renaming the slot renames it);
  // variants' slots of one name share one property once combined.
  class Slot extends Frame {
    constructor() { super('SLOT'); this.name = 'Slot'; }
    get layoutMode() { return this._layoutMode; }
    set layoutMode(v) {
      check(v !== 'GRID', 'GRID layout is not allowed on a slot');
      oneOf('layoutMode', v); this._layoutMode = v;
      if (v !== 'NONE') { this._sizingH = 'HUG'; this._sizingV = 'HUG'; }
    }
  }

  class Component extends Frame {
    constructor() { super('COMPONENT'); this.description = ''; this.key = 'key-' + this.id; }
    // like Figma: a variant's properties, from its name; null outside a set
    get variantProperties() {
      if (!this.parent || this.parent.type !== 'COMPONENT_SET') return null;
      return Object.fromEntries(this.name.split(', ').map(p => p.split('=')));
    }
    createSlot() {
      const sl = new Slot();
      this.appendChild(sl);
      return sl;
    }
    createInstance() {
      const inst = cloneAs(this, 'INSTANCE');
      inst.mainComponent = this;
      return inst;
    }
  }
  function cloneAs(src, type) {
    const n = src.type === 'TEXT' ? new Text() : src.children ? new Frame(type || src.type) : new Node(type || src.type);
    for (const k of Object.keys(src)) if (!['id', 'parent', 'children', '_refs'].includes(k)) n[k] = src[k];
    n.type = type || src.type;
    if (src._paths) n.vectorPaths = src._paths;
    if (src.children) for (const c of src.children) { const cc = cloneAs(c); cc.parent = n; n.children.push(cc); cc._inInstance = true; }
    return n;
  }

  class ComponentSet extends Frame {
    constructor() { super('COMPONENT_SET'); this.componentPropertyDefinitions = {}; this._seq = 0; this.key = 'key-' + this.id; }
    editComponentProperty(key, opts) {
      const def = this.componentPropertyDefinitions[key];
      check(def, `${this.name}: no property ${key}`);
      if (opts.preferredValues) {
        check(def.type === 'SLOT' || def.type === 'INSTANCE_SWAP', `${key}: preferredValues on a ${def.type} property`);
        check(opts.preferredValues.every(p => ['COMPONENT', 'COMPONENT_SET'].includes(p.type) && typeof p.key === 'string'),
          'preferredValues entries are {type, key}');
        def.preferredValues = opts.preferredValues;
      }
      return key;
    }
    addComponentProperty(name, type, def) {
      check(['TEXT', 'BOOLEAN'].includes(type), `property type ${type}`);
      check(type !== 'TEXT' || typeof def === 'string', `${name}: TEXT default must be a string`);
      check(type !== 'BOOLEAN' || typeof def === 'boolean', `${name}: BOOLEAN default must be a boolean`);
      for (const k of Object.keys(this.componentPropertyDefinitions)) check(k.split('#')[0] !== name, `${this.name}: property "${name}" twice`);
      const key = `${name}#${++this._seq}:0`;
      this.componentPropertyDefinitions[key] = { type, defaultValue: def };
      return key;
    }
  }

  class Text extends Node {
    constructor() {
      super('TEXT'); this._chars = ''; this._font = null; this.ranges = [];
      this._sizingH = 'HUG'; this._sizingV = 'HUG'; this._autoResize = 'WIDTH_AND_HEIGHT';
    }
    get fontName() { return this._font; }
    set fontName(f) { check(loaded.has(fontId(f)), `font ${fontId(f)} not loaded`); this._font = f; }
    get characters() { return this._chars; }
    set characters(s) {
      check(this._font, `${this.name}: set fontName before characters`);
      for (const f of this.allFonts()) check(loaded.has(fontId(f)), `${this.name}: font ${fontId(f)} not loaded before setting characters`);
      check(typeof s === 'string', 'characters must be a string');
      // like Figma: new characters take the style of the first character
      const first = this.ranges.filter(r => r.s === 0).map(r => ({ ...r, e: s.length }));
      this._chars = s; this.ranges = s.length ? first : [];
    }
    allFonts() { return [this._font, ...this.ranges.filter(r => r.font).map(r => r.font)].filter(Boolean); }
    getRangeAllFontNames(s, e) { this.range(s, e); return this.allFonts(); }
    range(s, e) { check(s >= 0 && e <= this._chars.length && s < e, `${this.name}: range ${s}-${e} of ${this._chars.length}`); }
    setRangeFontName(s, e, f) { this.range(s, e); check(loaded.has(fontId(f)), `font ${fontId(f)} not loaded`); this.ranges.push({ s, e, font: f }); }
    setRangeFontSize(s, e, v) { this.range(s, e); check(v > 0, 'font size'); this.ranges.push({ s, e, size: v }); }
    setRangeFills(s, e, f) { this.range(s, e); check(Array.isArray(f), 'fills'); this.ranges.push({ s, e, fills: f }); }
    setRangeLetterSpacing(s, e, v) { this.range(s, e); check(v.unit === 'PIXELS' || v.unit === 'PERCENT', 'letter spacing unit'); }
    setRangeTextCase(s, e, v) { this.range(s, e); oneOf('textCase', v); }
    setRangeTextDecoration(s, e, v) { this.range(s, e); oneOf('textDecoration', v); }
    setRangeBoundVariable(s, e, field, v) { this.range(s, e); check(field === 'fontSize', `range bind ${field}`); check(v && v.id, 'variable'); }
    get lineHeight() { return this._lh; }
    set lineHeight(v) { check(v.unit === 'AUTO' || ((v.unit === 'PIXELS' || v.unit === 'PERCENT') && v.value > 0), 'line height'); this._lh = v; }
    get textAlignHorizontal() { return this._ta; }
    set textAlignHorizontal(v) { oneOf('textAlignHorizontal', v); this._ta = v; }
    get textAutoResize() { return this._autoResize; }
    set textAutoResize(v) { oneOf('textAutoResize', v); this._autoResize = v; this._sizingH = v === 'WIDTH_AND_HEIGHT' ? 'HUG' : 'FIXED'; }
    get textTruncation() { return this._trunc; }
    set textTruncation(v) { oneOf('textTruncation', v); this._trunc = v; }
    get layoutSizingHorizontal() { return this._sizingH; }
    set layoutSizingHorizontal(v) { sizing(this, 'h', v); }
    get layoutSizingVertical() { return this._sizingV; }
    set layoutSizingVertical(v) { sizing(this, 'v', v); }
  }

  const page = new Parent('PAGE');
  page.name = 'Page 1';
  page.selection = [];

  // variables: the file's own (from figma-variables), plus whatever the
  // plugin creates. Modes, values and aliases are checked as Figma does.
  let nextId = 0;
  class Collection {
    constructor(name) {
      this.id = 'VariableCollectionId:' + name; this.name = name;
      this.modes = [{ modeId: this.id + '/0', name: 'Mode 1' }];
    }
    renameMode(id, name) { const m = this.modes.find(x => x.modeId === id); check(m, `renameMode: no mode ${id}`); m.name = name; }
    addMode(name) { const id = this.id + '/' + this.modes.length; this.modes.push({ modeId: id, name }); return id; }
  }
  class Variable {
    constructor(name, collection, type) {
      this.id = 'VariableID:' + nextId++; this.name = name; this.resolvedType = type;
      this.variableCollectionId = collection.id; this.valuesByMode = {}; this.scopes = ['ALL_SCOPES'];
    }
    setValueForMode(modeId, value) {
      const c = collections.find(x => x.id === this.variableCollectionId);
      check(c && c.modes.some(m => m.modeId === modeId), `${this.name}: no mode ${modeId} in its collection`);
      if (value && value.type === 'VARIABLE_ALIAS') {
        const target = vars.find(v => v.id === value.id);
        check(target && target.resolvedType === this.resolvedType, `${this.name}: alias to a missing or mistyped variable`);
      } else if (this.resolvedType === 'COLOR') {
        check(value && ['r', 'g', 'b'].every(k => value[k] >= 0 && value[k] <= 1), `${this.name}: not a colour`);
      }
      this.valuesByMode[modeId] = value;
    }
  }
  const collections = [...new Set(variables.map(v => v.collection))].map(c => new Collection(c));
  const vars = variables.map(v => new Variable(v.name, collections.find(c => c.name === v.collection), v.type));

  const figma = {
    logs,
    currentPage: page,
    viewport: { center: { x: 0, y: 0 }, scrollAndZoomIntoView() {} },
    showUI() {},
    notify(m) { logs.push({ kind: 'notify', text: m }); },
    ui: { postMessage(m) { logs.push(m); }, onmessage: null },
    async listAvailableFontsAsync() { return fonts.map(f => ({ fontName: f })); },
    async loadFontAsync(f) {
      check(fonts.some(x => fontId(x) === fontId(f)), `font ${fontId(f)} is not available`);
      loaded.add(fontId(f));
    },
    createFrame() { const f = new Frame(); page.appendChild(f); return f; },
    createComponent() { const c = new Component(); page.appendChild(c); return c; },
    createText() { const t = new Text(); page.appendChild(t); return t; },
    createVector() {
      const v = new Node('VECTOR');
      Object.defineProperty(v, 'vectorPaths', {
        set(p) { check(Array.isArray(p) && p.every(x => /^M /.test(x.data) && !/NaN/.test(x.data)), 'vector paths'); this._paths = p; },
        get() { return this._paths; },
      });
      page.appendChild(v);
      return v;
    },
    createRectangle() { const r = new Rect(); page.appendChild(r); return r; },
    createImage(bytes) {
      check(bytes instanceof Uint8Array && bytes.length > 0, 'createImage takes the bytes as a Uint8Array');
      return { hash: 'image-' + bytes.length };
    },
    // A frame the SVG's size, a group per <g>, one vector per <path>, painted
    // as the SVG paints it: fill, stroke and stroke-width inherited from
    // enclosing <g>s, fill black unless "none". Each vector gets made-up
    // bounds (paths aren't parsed), remembered as where it drew. Enough to
    // check what the plugin does with the result.
    createNodeFromSvg(svg) {
      check(typeof svg === 'string' && /^\s*<svg\b/.test(svg), 'createNodeFromSvg needs SVG markup');
      check(!/currentColor/.test(svg), 'currentColor left in the SVG (Figma draws it black)');
      const attr = (tag, k) => (tag.match(new RegExp(`\\s${k}="([^"]*)"`)) || [])[1];
      const root = svg.match(/<svg\b[^>]*>/)[0];
      const f = new Frame();
      f.width = Number(attr(root, 'width')) || 100; f.height = Number(attr(root, 'height')) || 100;
      f.x = 400; f.y = 300;
      page.appendChild(f);
      const stack = [{ into: f }];
      const vectors = [];
      for (const [tag] of svg.matchAll(/<\/?(g|path)\b[^>]*>/g)) {
        if (tag.startsWith('</')) { stack.pop(); continue; }
        const top = stack[stack.length - 1];
        const inh = { ...top };
        for (const k of ['fill', 'stroke', 'stroke-width']) if (attr(tag, k)) inh[k] = attr(tag, k);
        if (tag.startsWith('<g')) {
          if (tag.endsWith('/>')) continue;
          const g = new Parent('GROUP');
          top.into.appendChild(g);
          stack.push({ ...inh, into: g });
          continue;
        }
        const v = new Node('VECTOR');
        const solid = { type: 'SOLID', color: { r: 0, g: 0, b: 0 } };
        v.fills = inh.fill === 'none' ? [] : [solid];
        v.strokes = inh.stroke && inh.stroke !== 'none' ? [solid] : [];
        v.strokeWeight = Number(inh['stroke-width']) || 1;
        const i = vectors.length;
        v.x = 2 + i; v.y = 3 + i; v.width = 18 - i; v.height = 16 - i;
        top.into.appendChild(v);
        vectors.push(v);
      }
      for (const v of vectors) v._drew = v.absoluteBoundingBox;
      return f;
    },
    flatten(nodes, parent) {
      check(nodes.length > 0, 'flatten needs nodes');
      for (const n of nodes) check(n instanceof Node && n.parent, 'flatten: a node that is not in the document');
      const into = parent || nodes[0].parent;
      const v = new Node('VECTOR');
      v.fills = nodes[0].fills; v._flattened = nodes.length;
      // how many went in somewhere other than where the SVG drew them
      const near = (a, b) => Math.abs(a.x - b.x) < 0.01 && Math.abs(a.y - b.y) < 0.01;
      v._misplaced = nodes.filter(n => n._drew && !near(n.absoluteBoundingBox, n._drew)).length;
      for (const n of nodes) n.remove();
      into.appendChild(v);
      return v;
    },
    combineAsVariants(nodes, parent) {
      check(nodes.length > 0, 'combineAsVariants needs nodes');
      const keys = nodes.map(n => {
        check(n.type === 'COMPONENT', 'only components combine');
        const props = n.name.split(', ').map(p => p.split('='));
        check(props.every(p => p.length === 2 && p[0] && p[1]), `variant name "${n.name}"`);
        return props.map(p => p[0]).sort().join(',');
      });
      check(new Set(keys).size === 1, `variants name different properties: ${[...new Set(keys)].join(' | ')}`);
      check(new Set(nodes.map(n => n.name.split(', ').sort().join(', '))).size === nodes.length, 'two variants with one name');
      const set = new ComponentSet();
      for (const n of nodes) set.appendChild(n);
      for (const n of nodes) for (const sl of n.children.filter(c => c.type === 'SLOT')) {
        let key = Object.keys(set.componentPropertyDefinitions).find(k => k.split('#')[0] === sl.name);
        if (key) check(set.componentPropertyDefinitions[key].type === 'SLOT', `${sl.name}: a slot and another property share a name`);
        else { key = `${sl.name}#${++set._seq}:0`; set.componentPropertyDefinitions[key] = { type: 'SLOT' }; }
        sl.slotProperty = key;
      }
      parent.appendChild(set);
      return set;
    },
    variables: {
      async getLocalVariableCollectionsAsync() { return collections; },
      async getLocalVariablesAsync(type) { return type ? vars.filter(v => v.resolvedType === type) : vars; },
      createVariableCollection(name) {
        check(!collections.some(c => c.name === name), `a second collection named ${name}`);
        const c = new Collection(name); collections.push(c); return c;
      },
      createVariable(name, collection, type) {
        check(collection instanceof Collection, 'createVariable takes the collection itself, not its id');
        check(!vars.some(v => v.variableCollectionId === collection.id && v.name === name), `a second variable named ${name}`);
        const v = new Variable(name, collection, type); vars.push(v); return v;
      },
      createVariableAlias(v) { check(v instanceof Variable, 'alias of a non-variable'); return { type: 'VARIABLE_ALIAS', id: v.id }; },
      async importVariableByKeyAsync() { throw new Error('no libraries'); },
      setBoundVariableForPaint(p, field, v) {
        check(field === 'color' && v.resolvedType === 'COLOR', 'paint binding');
        return { ...p, boundVariables: { color: { type: 'VARIABLE_ALIAS', id: v.id } } };
      },
    },
    teamLibrary: { async getAvailableLibraryVariableCollectionsAsync() { return []; } },
  };
  return figma;
}
