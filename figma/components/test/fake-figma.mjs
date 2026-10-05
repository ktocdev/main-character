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
    resize(w, h) {
      check(w >= 0.01 && h >= 0.01, `${this.name}: resize(${w}, ${h})`);
      this.width = w; this.height = h;
      if (this._sizingH !== undefined) { this._sizingH = 'FIXED'; this._sizingV = 'FIXED'; }
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
    remove() { if (this.parent) this.parent.children.splice(this.parent.children.indexOf(this), 1); this.parent = null; }
  }

  class Parent extends Node {
    constructor(type) { super(type); this.children = []; }
    appendChild(c) {
      check(c instanceof Node, 'appendChild needs a node');
      if (c.parent) c.remove();
      c.parent = this; this.children.push(c);
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

  class Component extends Frame {
    constructor() { super('COMPONENT'); }
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
    constructor() { super('COMPONENT_SET'); this.componentPropertyDefinitions = {}; this._seq = 0; }
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
    set lineHeight(v) { check(v.unit === 'AUTO' || (v.unit === 'PIXELS' && v.value > 0), 'line height'); this._lh = v; }
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
