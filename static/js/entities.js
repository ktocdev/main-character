// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, busy, esc, fmtDate, refreshStatus } from './core.js';
import { state, filters } from './state.js';
import { showTab } from './main.js';
import { loadGroups, groupSetDeep, groupPathLabel, rolledUpMemberSet, clearGroupSelection, clearGroupFilter, showGroup as showGroupByName } from './groups.js';
import * as popover from './popover.js';

// ---- entities ----
export async function loadEntities() {
  const [ents] = await Promise.all([fetch('/api/entities').then(r => r.json()), loadThingKinds()]);
  state.entities = ents;
  const dl = $('entity-names');
  dl.innerHTML = '';
  for (const name of Object.keys(state.entities).sort()) {
    const o = document.createElement('option');
    o.value = name;
    dl.appendChild(o);
  }
  await loadGroups();
  renderEntityList();
  refreshStatus();
}

const KINDS = ['person', 'project', 'place', 'thing', 'animal'];
const PLURAL = {person: 'people', project: 'projects', place: 'places', thing: 'things', animal: 'animals'};
// never a part or a parent (entities.NO_PARTS); a group holds them instead
export const NO_PARTS = new Set(['person', 'animal']);
// kinds whose attribute is a relationship, shown as "as pet cat"
export const HAS_RELATIONSHIP = new Set(['person', 'animal']);
// The kinds a thing can be: the built-ins, the author's own, then other.
// Kept to the built-ins when the list can't be had, so pickers never empty.
const BUILTIN_KINDS = ['music', 'podcast', 'game', 'show', 'movie', 'book', 'event', 'other'];
async function loadThingKinds() {
  try {
    const r = await (await fetch('/api/entities/thing-kinds')).json();
    if (!Array.isArray(r.kinds)) throw new Error(r.error);
    state.thingKinds = r.kinds;
    state.thingKindIcons = r.icons;
    state.thingKindMax = r.max_custom;
  } catch {
    if (!state.thingKinds.length) state.thingKinds = BUILTIN_KINDS.map(name => ({name, description: '', icon: '', custom: false}));
  }
  // a pick on a kind that's gone would hide every thing
  const have = new Set(state.thingKinds.map(k => k.name));
  for (const c of [...filters.thingCats]) if (!have.has(c)) filters.thingCats.delete(c);
  // an open thing's picker follows a kind added or removed meanwhile
  fillKindSelect($('thing-category'));
}
export const thingKindNames = () => state.thingKinds.map(k => k.name);

// "Dev · work": the name reads first, the qualifier muted. Plain names
// stay plain text.
export function setName(el, name, info) {
  el.textContent = '';
  const q = info?.variant_of ? name.slice(info.variant_of.length) : '';
  if (!q) { el.textContent = name; return; }
  const muted = document.createElement('span');
  muted.className = 'q';
  muted.textContent = q;
  el.append(info.variant_of, muted);
}
// the other halves of a split name, for "move to"
export function variantsOf(head) {
  return Object.entries(state.entities)
    .filter(([, i]) => i.variant_of === head && !i.unsorted)
    .sort((a, b) => b[1].mentions - a[1].mentions)
    .map(([, i]) => i.qualifier);
}

// a mix-up flag as words: "seen as coworker (9) and friend (4)"
export function mixupText(flag) {
  const parts = flag.map(([label, n]) => `${label} (${n})`);
  return 'seen as ' + (parts.length > 1 ? `${parts.slice(0, -1).join(', ')} and ${parts.at(-1)}` : parts[0]);
}
let sortAlpha = false;
let selectMode = false;         // checkboxes for batch add-to-group
const picked = new Set();       // entity names checked for the next batch
const openParts = new Set();    // parents whose parts are shown in the list
let splitFor = null;            // the person whose observations are being split
const splitPicked = new Set();  // "file|group|ent|obs" of the picked ones

function updateBatchCount() {
  $('batch-count').textContent = `${picked.size} selected`;
}

export function renderEntityList() {
  const filter = $('search').value.trim().toLowerCase();
  const activeGroupSet = filters.group ? groupSetDeep(filters.group) : null;
  // rolled-up groups collapse their members out of the flat list — but only in
  // the plain view; an active search, group filter, or select mode reveals them
  const hideSet = (!filter && !activeGroupSet && !selectMode) ? rolledUpMemberSet() : null;
  if (selectMode) for (const n of [...picked]) if (!state.entities[n]) picked.delete(n);
  const groups = Object.fromEntries(KINDS.map(k => [k, []]));
  // retired entities (a past chapter) live behind their own chip
  const retiredCount = Object.values(state.entities).filter(i => i.retired).length;
  $('flt-retired').hidden = !retiredCount && !filters.retired;
  $('flt-retired').textContent = `retired (${retiredCount})`;
  // names whose entries disagree about who they are
  const mixupCount = Object.values(state.entities).filter(i => i.mixup && !i.retired).length;
  $('flt-mixup').hidden = !mixupCount && !filters.mixup;
  $('flt-mixup').textContent = `maybe two (${mixupCount})`;
  renderThingCats();
  for (const [name, info] of Object.entries(state.entities)) {
    if (!!info.retired !== filters.retired) continue;
    const hay = (name + ' ' + (info.aliases || []).join(' ')).toLowerCase();
    if (filter && !hay.includes(filter)) continue;
    if (filters.unreviewed && info.reviewed) continue;
    if (filters.single && info.mentions !== 1) continue;
    if (filters.mixup && !info.mixup) continue;
    if (info.type === 'thing' && filters.thingCats.size && !filters.thingCats.has(info.category || 'other')) continue;
    if (activeGroupSet && !(info.groups || []).some(g => activeGroupSet.has(g.toLowerCase()))) continue;
    if (hideSet && hideSet.has(name.toLowerCase())) continue;
    groups[info.type].push([name, info.mentions, info.reviewed, !!info.mixup]);
  }
  // parts sit under their parent, collapsed, in the plain view; a search
  // or filter shows them flat with their path
  const nest = !filter && !selectMode;
  const shownNames = new Set(Object.values(groups).flat().map(([n]) => n));
  const nested = n => nest && state.entities[n].part_of && shownNames.has(state.entities[n].part_of)
    && state.entities[state.entities[n].part_of].type === state.entities[n].type;
  const wrap = $('entity-groups');
  wrap.innerHTML = '';
  const typeFilter = filters.types.size ? filters.types : null;
  let shown = 0;
  for (const kind of KINDS) {
    if (typeFilter && !typeFilter.has(kind)) continue;
    const all = groups[kind].sort(sortAlpha
      ? (a, b) => a[0].toLowerCase().localeCompare(b[0].toLowerCase())
      : (a, b) => b[1] - a[1]);
    if (!all.length) continue;
    shown += all.length;
    // each parent followed by its parts (when open), the parts marked
    const items = [];
    for (const it of all) {
      if (nested(it[0])) continue;
      items.push(it);
      const kids = all.filter(k => nested(k[0]) && state.entities[k[0]].part_of === it[0]);
      if (kids.length) items.push(['', 0, true, false, {toggle: it[0], count: kids.length}]);
      if (kids.length && openParts.has(it[0])) for (const k of kids) items.push([...k, {part: true}]);
    }
    const h = document.createElement('div');
    h.className = 'list-head';
    h.innerHTML = '<span class="eyebrow"></span>';
    h.firstChild.textContent = `${PLURAL[kind]} (${all.length})`;
    wrap.appendChild(h);
    for (const [name, mentions, reviewed, mixup, nestInfo] of items) {
      if (nestInfo?.toggle) {
        const t = document.createElement('button');
        t.className = 'parts-toggle';
        const open = openParts.has(nestInfo.toggle);
        t.innerHTML = '<span class="caret-icon"></span><span></span>';
        t.lastChild.textContent = `${nestInfo.count} part${nestInfo.count === 1 ? '' : 's'}`;
        t.setAttribute('aria-expanded', open);
        t.onclick = () => { if (open) openParts.delete(nestInfo.toggle); else openParts.add(nestInfo.toggle); renderEntityList(); };
        wrap.appendChild(t);
        continue;
      }
      const row = document.createElement('div');
      row.className = 'ent-row' + (nestInfo?.part ? ' part' : '');

      if (selectMode) {
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'ent-check';
        cb.checked = picked.has(name);
        cb.setAttribute('aria-label', `select ${name}`);
        cb.onclick = e => e.stopPropagation();
        cb.onchange = () => {
          if (cb.checked) picked.add(name); else picked.delete(name);
          updateBatchCount();
          renderFilterBar();
        };
        row.appendChild(cb);
      }

      const b = document.createElement('button');
      b.className = 'list-item' + (name === state.selected ? ' sel' : '');
      b.innerHTML = '<span class="li-title"></span><span class="li-meta"></span>';
      // nested under its parent, a part reads by its own name
      if (nestInfo?.part) b.querySelector('.li-title').textContent = state.entities[name].base || name;
      else setName(b.querySelector('.li-title'), name, state.entities[name]);
      b.querySelector('.li-meta').textContent = mentions;
      // a ring for maybe-two-people, then the unreviewed dot
      // (select mode's checkbox stands in for both)
      const dots = [];
      if (mixup && !selectMode) dots.push(['flag', 'maybe two people']);
      if (!reviewed && !selectMode) dots.push(['', 'unreviewed']);
      dots.forEach(([cls, label], i) => {
        const dot = document.createElement('span');
        dot.className = ['dot', cls, i ? '' : 'right'].filter(Boolean).join(' ');
        dot.setAttribute('aria-label', label);
        dot.title = label;
        b.appendChild(dot);
      });
      b.onclick = () => selectMode
        ? row.querySelector('.ent-check')?.click()
        : showEntity(name);
      row.appendChild(b);

      if (!selectMode) {
        const keep = document.createElement('button');
        keep.className = 'ent-keep' + (reviewed ? ' on' : '');
        keep.textContent = '✓';
        keep.title = reviewed ? 'reviewed (click to unmark)' : 'mark reviewed';
        keep.setAttribute('aria-label', `${reviewed ? 'unmark' : 'mark'} ${name} reviewed`);
        keep.dataset.tipPlace = 'above-end';
        keep.onclick = e => {
          e.stopPropagation();
          const next = !reviewed;
          busy(keep, async () => {
            if (await api('/api/entities/reviewed', {name, reviewed: next})) {
              state.entities[name].reviewed = next;
              renderEntityList();
            }
          });
        };
        row.appendChild(keep);
      }

      wrap.appendChild(row);
    }
  }
  if (!shown) {
    const e = document.createElement('p');
    e.className = 'empty';
    e.textContent = 'nothing matches these filters.';
    wrap.appendChild(e);
  }
  if (selectMode) updateBatchCount();
  updateExtra();
  renderFilterBar();
}

// Under "show", while things is on: a chip per kind of thing the journal
// has (a picked kind stays even once it empties, so it can come off).
// Picks combine; none picked shows every thing.
function renderThingCats() {
  const row = $('thing-cats');
  row.hidden = !filters.types.has('thing');
  if (row.hidden) setKindsOpen(false);
  if (row.hidden) return;
  const have = new Set(Object.values(state.entities)
    .filter(i => i.type === 'thing' && !!i.retired === filters.retired)
    .map(i => i.category || 'other'));
  // a kind of your own shows even before anything's in it
  for (const k of state.thingKinds) if (k.custom) have.add(k.name);
  row.innerHTML = '';
  for (const c of thingKindNames()) {
    if (!have.has(c) && !filters.thingCats.has(c)) continue;
    const b = document.createElement('button');
    const on = filters.thingCats.has(c);
    b.className = 'chip-toggle' + (on ? ' on' : '');
    b.dataset.cat = c;
    b.textContent = c;
    b.title = `only ${c} things; pick several to combine`;
    b.setAttribute('aria-pressed', String(on));
    b.onclick = () => {
      if (filters.thingCats.has(c)) filters.thingCats.delete(c); else filters.thingCats.add(c);
      renderEntityList();
    };
    row.appendChild(b);
  }
  const add = document.createElement('button');
  add.className = 'chip-toggle';
  add.id = 'thing-kinds-btn';
  add.textContent = '+ kind';
  add.title = 'kinds of thing of your own, like songs or food';
  add.setAttribute('aria-haspopup', 'true');
  add.setAttribute('aria-controls', 'thing-kinds-menu');
  add.setAttribute('aria-expanded', String(!$('thing-kinds-menu').hidden));
  add.onclick = () => setKindsOpen($('thing-kinds-menu').hidden);
  row.appendChild(add);
}

// a <select> of every kind of thing, built-ins first and other last
function fillKindSelect(sel) {
  const was = sel.value;
  sel.innerHTML = '';
  for (const k of state.thingKinds) {
    const o = document.createElement('option');
    o.value = k.name; o.textContent = k.name;
    sel.appendChild(o);
  }
  sel.value = was;
}

// ---- kinds of thing of the author's own ----
// Opened from "+ kind" at the end of the kind chips. Lists the ones added
// (each editable, each removable), then a form to add one: a name, what it
// covers (Claude reads it when sorting new entries) and an icon.
let editingKind = null;  // the name of the kind the form is editing, or null

function iconSpan(name) {
  const s = document.createElement('span');
  s.className = 'kind-icon';
  s.style.setProperty('--icon', `url("${new URL(`../icons/${name}-filled.svg`, import.meta.url).href}")`);
  return s;
}

function setKindsOpen(open) {
  if (open) popover.opened('thing-kinds');
  $('thing-kinds-menu').hidden = !open;
  $('thing-kinds-btn')?.setAttribute('aria-expanded', String(open));
  if (open) { kindForm(null); renderKinds(); }
}

function renderKinds() {
  const list = $('thing-kinds-list');
  list.innerHTML = '';
  const mine = state.thingKinds.filter(k => k.custom);
  $('thing-kinds-none').hidden = mine.length > 0;
  for (const k of mine) {
    const row = document.createElement('div');
    row.className = 'kind-row';
    row.appendChild(iconSpan(k.icon || 'bookmark-heart'));
    const text = document.createElement('span');
    text.className = 'kind-text';
    text.innerHTML = '<strong></strong><span class="note"></span>';
    text.firstChild.textContent = k.name;
    text.lastChild.textContent = k.description;
    row.appendChild(text);
    const edit = document.createElement('button');
    edit.className = 'text';
    edit.textContent = 'edit';
    edit.setAttribute('aria-label', `edit ${k.name}`);
    edit.onclick = () => kindForm(k);
    // two clicks: "remove", then "remove songs?" (a few seconds to decide)
    const rm = document.createElement('button');
    rm.className = 'text kind-remove';
    rm.textContent = 'remove';
    rm.title = `remove ${k.name}: its things show as other until it's back (undo brings it back)`;
    let armed = null;
    rm.onclick = () => {
      if (!armed) {
        rm.textContent = `remove ${k.name}?`;
        rm.classList.add('armed');
        armed = setTimeout(() => { armed = null; rm.textContent = 'remove'; rm.classList.remove('armed'); }, 4000);
        return;
      }
      clearTimeout(armed);
      busy(rm, async () => {
        if (await api('/api/entities/thing-kinds/delete', {name: k.name})) {
          notice(`removed ${k.name}. Its things show as other; undo puts it back.`);
          await loadEntities();
          renderKinds();
        }
      });
    };
    row.append(edit, rm);
    list.appendChild(row);
  }
  const full = mine.length >= state.thingKindMax;
  $('thing-kind-form').hidden = full && !editingKind;
  $('thing-kinds-full').hidden = !(full && !editingKind);
}

// the form: empty to add, or filled to edit one (its name then fixed)
function kindForm(k) {
  editingKind = k ? k.name : null;
  $('thing-kind-head').textContent = k ? `edit ${k.name}` : 'add a kind';
  $('thing-kind-name').value = k ? k.name : '';
  $('thing-kind-name').disabled = !!k;
  $('thing-kind-desc').value = k ? k.description : '';
  $('thing-kind-save').textContent = k ? 'save' : 'add';
  $('thing-kind-cancel').hidden = !k;
  $('thing-kind-form').hidden = false;
  $('thing-kinds-full').hidden = true;
  const icons = $('thing-kind-icons');
  icons.innerHTML = '';
  const chosen = k?.icon || 'bookmark-heart';
  for (const name of state.thingKindIcons) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'icon-pick';
    b.dataset.icon = name;
    b.setAttribute('role', 'radio');
    b.setAttribute('aria-checked', String(name === chosen));
    b.setAttribute('aria-label', name.replace(/-/g, ' '));
    b.title = name.replace(/-/g, ' ');
    b.appendChild(iconSpan(name));
    b.onclick = () => icons.querySelectorAll('.icon-pick').forEach(p => p.setAttribute('aria-checked', String(p === b)));
    icons.appendChild(b);
  }
}

async function saveKind() {
  const name = $('thing-kind-name').value.trim().toLowerCase();
  if (!name) { $('thing-kind-name').focus(); return; }
  const icon = $('thing-kind-icons').querySelector('[aria-checked="true"]')?.dataset.icon || '';
  const r = await api('/api/entities/thing-kinds', {
    name, description: $('thing-kind-desc').value.trim(), icon: icon === 'bookmark-heart' ? '' : icon,
  });
  if (!r) return;
  notice(editingKind ? `saved ${r.name}.`
    : `added ${r.name}. New entries are sorted into it from now on; to sort past ones, re-extract the entries that mention it (clean up → re-extract…).`);
  await loadEntities();
  kindForm(null);
  renderKinds();
}

// The foot of the sidebar: in select mode, a chip for each checked entity,
// then every filter that's on. It sticks to the bottom of the scrolling
// list, so a pick or a filter set at the top comes off from anywhere. A
// filter chip's × goes through the toggle it stands for, so the toggle,
// the folded count and the list all follow; a pick's × unchecks its box.
function renderFilterBar() {
  const bar = $('filter-bar');
  const sel = selectMode ? [...picked] : [];
  const on = [];
  const q = $('search').value.trim();
  if (q) on.push([`“${q.length > 14 ? q.slice(0, 13) + '…' : q}”`, 'clear the filter text',
    () => { $('search').value = ''; renderEntityList(); }]);
  for (const kind of filters.types)
    on.push([PLURAL[kind] || kind, `show all kinds again`, () => $('type-chips').querySelector(`[data-type="${kind}"]`).click()]);
  for (const c of filters.thingCats)
    on.push([`things · ${c}`, 'show every kind of thing', () => $('thing-cats').querySelector(`[data-cat="${c}"]`).click()]);
  if (filters.group) on.push([`in ${groupPathLabel(filters.group)}`, 'stop narrowing the list to this group', () => { clearGroupFilter(); renderEntityList(); }]);
  if (filters.unreviewed) on.push(['unreviewed', 'show reviewed ones too', () => $('flt-unreviewed').click()]);
  if (filters.single) on.push(['1 mention', 'show every mention count', () => $('flt-single').click()]);
  if (filters.mixup) on.push(['maybe two', 'show everyone', () => $('flt-mixup').click()]);
  if (filters.retired) on.push(['retired', 'back to everyday view', () => $('flt-retired').click()]);
  bar.hidden = !on.length && !sel.length;
  bar.innerHTML = '';
  const section = (label, cls) => {
    const g = document.createElement('div');
    g.className = 'foot-group ' + cls;
    const lbl = document.createElement('span');
    lbl.className = 'side-lbl';
    lbl.textContent = label;
    g.appendChild(lbl);
    bar.appendChild(g);
    return g;
  };
  const clearBtn = (g, text, run) => {
    const b = document.createElement('button');
    b.className = 'text';
    b.textContent = text;
    b.onclick = run;
    g.appendChild(b);
  };
  if (sel.length) {
    const g = section(`${sel.length} selected`, 'picks');
    if (sel.length > 1) clearBtn(g, 'clear', () => $('batch-clear').click());
    const chips = document.createElement('div');
    chips.className = 'foot-chips';
    sel.forEach((name, i) => chips.appendChild(tag(name, () => unpick(name, i), `unselect ${name}`)));
    g.appendChild(chips);
  }
  if (on.length) {
    const g = section('filters on', 'filters');
    for (const [text, title, off] of on) {
      g.appendChild(tag(text, off, `remove filter: ${text} (${title})`));
    }
    if (on.length > 1) clearBtn(g, 'clear all', () => { for (const [, , off] of on) off(); });
  }
}
// a pick's ×: uncheck it, then keep the keyboard in the chips (the next
// one along, or the last), not dropped to the top of the page
function unpick(name, i) {
  picked.delete(name);
  updateBatchCount();
  renderEntityList();
  const xs = $('filter-bar').querySelectorAll('.picks .x');
  (xs[Math.min(i, xs.length - 1)] || $('batch-group')).focus();
}

// "triage & recycling bin" folds its filters away; while folded, a count
// on the trigger says how many of them are on, so none is ever on unseen
function updateExtra() {
  const on = document.querySelectorAll('#extra-panel .chip-toggle[aria-pressed="true"]').length;
  const open = !$('extra-panel').hidden;
  $('extra-toggle').classList.toggle('active', on > 0);
  $('extra-count').hidden = open || !on;
  $('extra-count').textContent = on;
}
function setExtraOpen(open) {
  $('extra-panel').hidden = !open;
  $('extra-toggle').setAttribute('aria-expanded', String(open));
  updateExtra();
}

// ---- the clean up menu: every run-once tool ----
function setCleanupOpen(open) {
  if (open) popover.opened('cleanup');
  $('cleanup-menu').hidden = !open;
  $('cleanup-btn').setAttribute('aria-expanded', String(open));
  if (!open) setSuggestKindsOpen(false);
}
function setSuggestKindsOpen(open) {
  $('suggest-kinds').hidden = !open;
  $('suggest-toggle').setAttribute('aria-expanded', String(open));
}

// ---- batch add to group ----
async function batchAdd() {
  const group = $('batch-group').value.trim();
  if (!group) { $('batch-group').focus(); return; }
  if (!picked.size) return;
  const r = await busy($('batch-add'), () => api('/api/groups/members', {group, entities: [...picked]}), $('batch-group'), $('batch-clear'));
  if (!r) return;
  picked.clear();
  $('batch-group').value = '';
  await loadEntities();  // refreshes memberships + group counts, re-renders list
  const bits = [`added ${r.added.length} to ${r.group}${r.created ? ' (new group)' : ''}`];
  if (r.skipped.length) bits.push(`${r.skipped.length} already in`);
  if (r.unresolved.length) bits.push(`${r.unresolved.length} not found`);
  $('batch-count').textContent = bits.join(' · ');
}
// ---- local duplicate finder ----
// The suggestion panel sits above whatever the detail pane shows, and
// shows on its own when nothing is picked yet. Each open swaps in a fresh
// element: the last review's listeners go with the old one, and a review
// still waiting on Claude writes into that detached copy, not this one.
function suggestPanel(title) {
  const old = $('suggest-panel');
  const panel = old.cloneNode(false);
  old.replaceWith(panel);
  $('suggest-wrap').hidden = false;
  $('entities-pane').classList.add('drilled');
  pressed('show-deleted', title === 'deleted');
  panel.innerHTML = '<div class="head"><span class="eyebrow"></span>'
    + '<button class="text">dismiss</button></div>';
  panel.querySelector('.eyebrow').textContent = title;
  panel.querySelector('.text').onclick = closeSuggest;
  return panel;
}
export function hideSuggest() {
  $('suggest-wrap').hidden = true;
  pressed('show-deleted', false);
}
function closeSuggest() {
  hideSuggest();
  if (!$('entity-detail-col').hidden) return;
  $('entities-pane').classList.remove('drilled');
}
function panelSay(panel, text) {
  const p = document.createElement('div');
  p.className = 'working';
  p.textContent = text;
  panel.appendChild(p);
  return p;
}
// ---- paid calls (the clean up menu's "api call" half) ----
// One at a time: while one is out, the api-call items take no clicks and
// the clean up button carries a pulsing dot, so a second click can't buy
// the same answer twice (a review reopened mid-call used to keep billing
// the first). The wait line spins and counts seconds after five, so a
// long call doesn't read as frozen.
let paidRunning = null;
async function paid(what, work) {
  if (paidRunning) return undefined;
  paidRunning = what;
  $('cleanup-btn').classList.add('paid-busy');
  $('cleanup-menu').classList.add('paid-busy');
  const tips = [...document.querySelectorAll('#cleanup-menu .paid')].map(b => [b, b.title]);
  for (const [b] of tips) { b.setAttribute('aria-disabled', 'true'); b.title = `claude is already working on ${what}`; }
  try { return await work(); }
  finally {
    paidRunning = null;
    $('cleanup-btn').classList.remove('paid-busy');
    $('cleanup-menu').classList.remove('paid-busy');
    for (const [b, t] of tips) { b.removeAttribute('aria-disabled'); b.title = t; }
  }
}
function panelWait(panel, text) {
  const w = panelSay(panel, text);
  w.classList.add('waiting');
  const t0 = Date.now();
  const tick = setInterval(() => {
    const s = Math.round((Date.now() - t0) / 1000);
    if (!w.isConnected) clearInterval(tick);
    else if (s >= 5) w.textContent = `${text} ${s}s`;
  }, 1000);
  w.done = () => { clearInterval(tick); w.classList.remove('waiting'); };
  return w;
}

async function findDups() {
  const panel = suggestPanel('possible duplicates');
  const w = panelSay(panel, 'scanning for likely duplicates (local, free)…');
  const r = await (await fetch('/api/entities/duplicates')).json();
  w.remove();
  if (!r.pairs || !r.pairs.length) { panelSay(panel, 'no likely duplicates found.'); return; }
  for (const p of r.pairs) {
    const row = document.createElement('div');
    row.className = 'dup-row';
    row.innerHTML = `<span><strong>${esc(p.a)}</strong> (${p.a_mentions}) ↔ <strong>${esc(p.b)}</strong> (${p.b_mentions})</span>`;
    const mk = (label, fn, cls = 'quiet') => {
      const b = document.createElement('button');
      b.className = cls; b.textContent = label;
      b.onclick = () => busy(b, fn, ...row.querySelectorAll('button'));
      row.appendChild(b);
    };
    mk(`${p.a} → ${p.b}`, async () => {
      if (await api('/api/entities/merge', {source: p.a, target: p.b})) { row.remove(); loadEntities(); }
    });
    mk(`${p.b} → ${p.a}`, async () => {
      if (await api('/api/entities/merge', {source: p.b, target: p.a})) { row.remove(); loadEntities(); }
    });
    mk('not duplicates', async () => {
      if (await api('/api/entities/duplicates/dismiss', {kind: p.kind, a: p.a, b: p.b})) row.remove();
    }, 'quiet danger');
    const why = document.createElement('span');
    why.className = 'why';
    why.textContent = `${p.kind} · ${p.basis} ${p.score}`;
    row.appendChild(why);
    panel.appendChild(row);
  }
}

// A checkbox row for the review panels: name, muted note, optional line.
function checkRow(panel, {checked, label, note, line}) {
  const row = document.createElement('label');
  row.className = 'check-row';
  row.innerHTML = '<input type="checkbox"><span class="body"><span class="nm"></span> <span class="note"></span><span class="ln"></span></span>';
  row.querySelector('input').checked = checked;
  row.querySelector('.nm').textContent = label;
  row.querySelector('.note').textContent = note;
  row.querySelector('.ln').textContent = line || '';
  panel.appendChild(row);
  return row;
}
function panelHead(panel, text) {
  const h = document.createElement('div');
  h.className = 'sub eyebrow';
  h.textContent = text;
  panel.appendChild(h);
}
// the apply button under a checklist, its label kept to the checked count
function panelApply(panel, rows, label, fn) {
  const b = document.createElement('button');
  b.className = 'filled sm';
  const checked = () => rows.filter(([, row]) => row.isConnected && row.querySelector('input').checked).map(([x]) => x);
  b.onclick = async () => {
    const xs = checked();
    if (xs.length) { await busy(b, () => fn(xs)); relabel(); }
  };
  const relabel = () => { const n = checked().length; b.textContent = label(n); b.disabled = !n; };
  panel.addEventListener('change', relabel);
  panel.appendChild(b);
  relabel();
}

// ---- generic names ----
// The local detector's hits as a checklist, pre-checked. Uncheck a "the
// gym" that is your gym. Applying is one never-track change, one undo.
async function genericCleanup() {
  const panel = suggestPanel('generic names');
  const w = panelSay(panel, 'looking for category names (local, free)…');
  const r = await (await fetch('/api/entities/generic')).json();
  w.remove();
  if (!r.candidates || !r.candidates.length) { panelSay(panel, 'no generic names found.'); return; }
  panelSay(panel, "A category isn't an entity. Checked names stop being tracked; your entries keep every word, so search and the companion still find them. Uncheck one you mean as one particular place.");
  const rows = r.candidates.map(c => [c, checkRow(panel, {
    checked: true, label: c.name, line: c.first,
    note: `${c.kind} · ${c.mentions} mention${c.mentions === 1 ? '' : 's'}`,
  })]);
  panelApply(panel, rows, n => `stop tracking ${n}`, async picked => {
    const res = await api('/api/entities/delete-names', {names: picked.map(c => c.name)});
    if (res) { closeSuggest(); clearDetail(`stopped tracking ${res.deleted.length} generic names. Undo brings them back.`); }
  });
}

// ---- deleted, both kinds ----
// Never-tracked names (rules that block every future mention too) and
// mentions deleted entry by entry. A blocked name can be let back in: what
// it hid stays hidden, but new entries start it fresh. People and animals
// are pre-checked for that -- a deleted Allen shouldn't block every Allen.
async function showDeleted() {
  const panel = suggestPanel('deleted');
  const r = await (await fetch('/api/entities/deleted')).json();
  if (!r.names || (!r.names.length && !r.mentions.length)) { panelSay(panel, 'nothing deleted.'); return; }
  const restoreBtn = (row, key, mode) => {
    const b = document.createElement('button');
    b.className = 'quiet xs';
    b.textContent = 'restore';
    b.title = 'bring back everything this delete hid';
    b.onclick = e => {
      e.preventDefault();
      busy(b, async () => {
        if (await api('/api/entities/deleted/restore', {key, mode})) { row.remove(); panel.dispatchEvent(new Event('change')); loadEntities(); }
      });
    };
    row.appendChild(b);
  };
  if (r.names.length) {
    panelHead(panel, `never tracked (${r.names.length})`);
    panelSay(panel, 'Checked names are let back in: their old mentions stay deleted, and a new entry that mentions one starts it fresh.');
    const rows = r.names.map(n => {
      const row = checkRow(panel, {
        checked: HAS_RELATIONSHIP.has(n.kind) && !n.generic, label: n.name,
        note: `${n.kind} · ${n.mentions} mention${n.mentions === 1 ? '' : 's'}${n.generic ? ' · looks generic' : ''}`,
      });
      restoreBtn(row, n.key, 'name');
      return [n, row];
    });
    panelApply(panel, rows, n => `let ${n} name${n === 1 ? '' : 's'} back in`, async picked => {
      if (await api('/api/entities/deleted/free', {keys: picked.map(n => n.key)})) { showDeleted(); loadEntities(); }
    });
  }
  if (r.mentions.length) {
    panelHead(panel, `deleted mentions (${r.mentions.length})`);
    for (const m of r.mentions) {
      const row = document.createElement('div');
      row.className = 'dup-row';
      row.innerHTML = '<span><strong></strong> <span class="note"></span></span>';
      row.querySelector('strong').textContent = m.name;
      row.querySelector('.note').textContent = `${m.kind} · from ${m.entries} entr${m.entries === 1 ? 'y' : 'ies'}`;
      restoreBtn(row, m.key, 'mentions');
      panel.appendChild(row);
    }
  }
}

// ---- things review ----
// Before the thing kind, games and shows were filed as projects and a
// festival as a place. Claude proposes which are really things; each is
// pre-checked with its category, editable. One retype change, one undo.
async function thingsReview() {
  const panel = suggestPanel('things review');
  const w = panelWait(panel, 'asking claude which projects and places are really things…');
  const r = await paid('the things review', () => api('/api/entities/suggest-things', {}));
  w.done(); w.remove();
  if (!panel.isConnected) return;  // another review opened meanwhile
  if (!r) { closeSuggest(); return; }
  if (!r.things.length) { panelSay(panel, 'nothing looks like a thing. Looks clean.'); return; }
  panelSay(panel, 'A project is something you make or work on; a thing is something you enjoy or follow. Checked names become things, with the category shown. Observations come along.');
  const rows = r.things.map(t => {
    const row = checkRow(panel, {
      checked: true, label: t.name, line: t.reason,
      note: `${t.kind} · ${t.mentions} mention${t.mentions === 1 ? '' : 's'} →`,
    });
    const sel = document.createElement('select');
    sel.className = 'quiet-select sm';
    sel.setAttribute('aria-label', `category for ${t.name}`);
    fillKindSelect(sel);
    sel.value = t.category;
    row.querySelector('.note').after(' ', sel);
    return [{name: t.name, sel}, row];
  });
  panelApply(panel, rows, n => `make ${n} thing${n === 1 ? '' : 's'}`, async picked => {
    const res = await api('/api/entities/retype-things', {items: picked.map(p => ({name: p.name, category: p.sel.value}))});
    if (res) { closeSuggest(); clearDetail(`${res.retyped.length} now things. Undo puts them back.`); }
  });
}

// ---- animals review ----
// Before the animal kind, a pet was a person with relationship "pet".
// The labels say which (local, free): mostly animal labels is pre-checked;
// a stray one (a coworker once called "dog") is listed unchecked, since
// it's an observation to move, not a person to retype.
async function animalsReview() {
  const panel = suggestPanel('animals review');
  const r = await (await fetch('/api/entities/animals-review')).json();
  if (!panel.isConnected) return;  // another review opened meanwhile
  if (!r.animals?.length) { panelSay(panel, 'no people with an animal label. Looks clean.'); return; }
  panelSay(panel, 'Checked people become animals; observations, aliases and groups come along. An unchecked one has a stray animal label: open it and move that observation instead.');
  const rows = r.animals.map(a => {
    const labels = Object.entries(a.labels).map(([l, n]) => n > 1 ? `${l} ×${n}` : l).join(', ');
    const row = checkRow(panel, {
      checked: a.suggest, label: a.name, line: a.first,
      note: `${a.mentions} mention${a.mentions === 1 ? '' : 's'} · ${labels}`,
    });
    return [a.name, row];
  });
  panelApply(panel, rows, n => `make ${n} animal${n === 1 ? '' : 's'}`, async picked => {
    const res = await api('/api/entities/retype-animals', {names: picked});
    if (res) { closeSuggest(); clearDetail(`${res.retyped.length} now animals. Undo puts them back.`); }
  });
}

// ---- parts review ----
// One-time. Before part-of links, a piece of something was merged into it
// ("tabs" into a project) or given a slash name ("Harbor Town / Beach").
// A merge is a name rule, so every future "the beach" went to that town.
// Slash names whose parent exists are offered as parts, checked; each
// merged name gets keep as alias / part / never track, with generic ones
// pre-set to never track and Claude's guesses at parts pre-set to part.
// Applying is one curation change, one undo.
async function partsReview() {
  const panel = suggestPanel('parts review');
  const w = panelSay(panel, 'reading your merges (local, free)…');
  const r = await (await fetch('/api/entities/parts-review')).json();
  w.remove();
  if (!r.slash.length && !r.targets.length) { panelSay(panel, 'no merges or slash names to review.'); return; }
  panelSay(panel, 'A part is its own entity shown under its parent ("Coda / Tabs"), so it keeps its own timeline and an unrelated one with the same name stays separate. An alias is another name for the same thing. Nothing changes until you apply.');
  const slashRows = [];
  if (r.slash.length) {
    panelHead(panel, `slash names (${r.slash.length})`);
    for (const s of r.slash) {
      slashRows.push([s, checkRow(panel, {
        checked: true, label: s.name,
        note: `→ ${s.part}, part of ${s.parent}${s.joins ? ` · joins the existing ${s.joins}` : ''}`,
      })]);
    }
  }
  const sourceRows = [];
  if (r.targets.length) {
    panelHead(panel, `merged names, by what they merge into (${r.targets.length})`);
    for (const t of r.targets) {
      const d = document.createElement('details');
      d.innerHTML = '<summary><span class="nm"></span> <span class="note"></span></summary>';
      d.querySelector('.nm').textContent = t.target;
      d.querySelector('.note').textContent = `${t.kind} · ${t.sources.length} merged`;
      for (const s of t.sources) {
        const row = document.createElement('div');
        row.className = 'part-row';
        row.innerHTML = '<span class="nm"></span><span class="note"></span><select class="quiet-select sm"><option value="alias">keep as alias</option><option value="part">part</option><option value="never">never track</option></select><span class="ln"></span>';
        row.querySelector('.nm').textContent = s.name;
        row.querySelector('.note').textContent = `${s.mentions} mention${s.mentions === 1 ? '' : 's'}${s.generic ? ' · looks generic' : ''}`;
        row.querySelector('.ln').textContent = s.said;
        const sel = row.querySelector('select');
        sel.setAttribute('aria-label', `what ${s.name} is to ${t.target}`);
        if (s.generic) { sel.value = 'never'; d.open = true; }
        d.appendChild(row);
        sourceRows.push({s, t, sel, row, d});
      }
      panel.appendChild(d);
    }
  }
  const b = document.createElement('button');
  b.className = 'filled sm';
  const picked = () => ({
    convert: slashRows.filter(([, row]) => row.querySelector('input').checked).map(([s]) => ({name: s.name, parent: s.parent})),
    sources: sourceRows.filter(x => x.sel.value !== 'alias').map(x => ({key: x.s.key, target: x.t.target, action: x.sel.value})),
  });
  const relabel = () => {
    const p = picked(), n = p.convert.length + p.sources.length;
    b.textContent = `apply ${n} change${n === 1 ? '' : 's'}`;
    b.disabled = !n;
  };
  b.onclick = () => busy(b, async () => {
    const res = await api('/api/entities/parts-review', picked());
    if (res) { closeSuggest(); clearDetail(`parts review: ${res.changed} changes. Undo puts them all back.`); }
    else relabel();
  });
  panel.addEventListener('change', relabel);
  panel.appendChild(b);
  relabel();
  // Claude's guesses at parts, filled in when they arrive
  if (!sourceRows.some(x => !x.s.generic)) return;
  const ask = panelWait(panel, 'asking claude which merged names are really parts…');
  b.before(ask);
  const res = await paid('the parts review', () => api('/api/entities/suggest-parts', {}));
  ask.done();
  if (!res) { ask.textContent = "claude's suggestions didn't come back; choose by hand."; return; }
  for (const p of res.parts) {
    const x = sourceRows.find(x => x.s.key === p.key);
    if (!x || x.sel.value === 'never') continue;
    x.sel.value = 'part';
    x.row.querySelector('.ln').textContent = `claude: ${p.reason}`;
    x.d.open = true;
  }
  ask.textContent = res.parts.length
    ? `claude suggests ${res.parts.length} part${res.parts.length === 1 ? '' : 's'}, set below. Check each before applying.`
    : 'claude found no parts among them.';
  relabel();
}

// ---- targeted re-extract ----
// A prompt change only reaches entries extracted after it. This re-runs
// extraction for the entries that mention a word, after showing how many,
// roughly what it costs, and which of them have hand edits it would lose.
async function reextractPanel() {
  const panel = suggestPanel('re-extract');
  panelSay(panel, 'Re-run extraction for just the entries that mention a word or name, e.g. a show that was missed. Separate several with commas. Case and spaces don\'t matter: "live journal" finds LiveJournal.');
  const form = document.createElement('div');
  form.className = 'ent-actions';
  form.innerHTML = '<input type="text" class="input-xs" placeholder="90 day, live journal…"><button class="quiet sm">find entries</button>';
  panel.appendChild(form);
  const out = document.createElement('div');
  panel.appendChild(out);
  const input = form.querySelector('input');
  const findBtn = form.querySelector('button');
  const terms = () => input.value.split(',').map(t => t.trim()).filter(Boolean);
  const find = async () => {
    if (!terms().length) { input.focus(); return; }
    out.innerHTML = '';
    const r = await busy(findBtn, () => api('/api/entities/reextract/preview', {terms: terms()}), input);
    if (!r) return;
    if (!r.entries.length) { panelSay(out, 'no entries mention that.'); return; }
    const edited = r.entries.filter(e => e.edited);
    panelSay(out, `${r.entries.length} entr${r.entries.length === 1 ? 'y mentions' : 'ies mention'} ${r.terms.length > 1 ? 'one of them' : 'it'}. Re-extracting costs about $${r.estimate.toFixed(2)} on ${r.model}.`);
    if (edited.length) {
      panelHead(out, `hand edits that would be lost (${edited.length})`);
      panelSay(out, 'You edited, moved or deleted observations in the entries marked (edited) below. Re-extracting replaces them with fresh ones.');
    }
    panelHead(out, 'entries');
    for (const e of r.entries) panelSay(out, `${fmtDate(e.date)} · ${e.title}${e.edited ? ' (edited)' : ''}`);
    const go = document.createElement('button');
    go.className = 'filled sm';
    go.textContent = `re-extract ${r.entries.length} entr${r.entries.length === 1 ? 'y' : 'ies'} (~$${r.estimate.toFixed(2)})`;
    // paid: busy until the run is accepted, then the progress line takes over
    go.onclick = async () => {
      if (await busy(go, () => paid('a re-extract', () => api('/api/entities/reextract', {terms: r.terms})), findBtn, input)) watchReextract(out);
    };
    out.appendChild(go);
  };
  form.querySelector('button').onclick = find;
  input.onkeydown = e => { if (e.key === 'Enter') find(); };
  input.focus();
  // a run started earlier (this panel closed, the page reloaded) is still
  // going in the background: show it rather than offer a second one
  try {
    const s = await (await fetch('/api/entities/reextract/status')).json();
    if (s.running && panel.isConnected) watchReextract(out);
  } catch { }
}
async function watchReextract(out) {
  out.innerHTML = '';
  const line = panelSay(out, 'starting…');
  let misses = 0;
  for (;;) {
    let s;
    try { s = await (await fetch('/api/entities/reextract/status')).json(); }
    catch {
      // a blip, or the server restarting: keep trying for about half a
      // minute, then say so instead of freezing on the last count
      if (++misses > 20) {
        line.textContent = 'lost touch with the server. Open re-extract again to check on it.';
        return;
      }
      await new Promise(r => setTimeout(r, 1500));
      continue;
    }
    misses = 0;
    if (!s.running) {
      if (s.error) line.textContent = `stopped: ${s.error}`;
      else {
        const res = s.result || {done: [], failed: []};
        line.textContent = `re-extracted ${res.done.length} entr${res.done.length === 1 ? 'y' : 'ies'}`
          + (res.failed.length ? `; ${res.failed.length} failed and kept their old extraction` : '')
          + '. New names are waiting in triage.';
      }
      loadEntities();
      return;
    }
    line.textContent = s.total ? `${s.done + 1} of ${s.total} · ${s.current}` : 'starting…';
    await new Promise(r => setTimeout(r, 1500));
  }
}

// The detail pane shows one of: an entity, a group page, or the prompt to
// pick one. The edit box and its toggle only exist for an entity.
function showDetail(kind) {
  $('entity-detail-col').hidden = kind === 'none';
  $('entity-none').hidden = kind !== 'none';
  $('entity-edit-toggle').hidden = kind !== 'entity';
  $('entity-to-triage').hidden = kind !== 'entity' || !state.triageReturn;
  if (kind !== 'entity') { $('entity-edit').hidden = true; $('entity-chips').innerHTML = ''; }
  setEditOpen(kind === 'entity' && editOpen);
  $('entities-pane').classList.toggle('drilled', kind !== 'none' || !$('suggest-wrap').hidden);
  if (kind !== 'none') $('entity-detail').scrollTop = 0;
}
let editOpen = false;
function setEditOpen(open) {
  editOpen = open;
  $('entity-edit').hidden = !open;
  const b = $('entity-edit-toggle');
  b.textContent = open ? 'done' : 'edit ▾';
  b.setAttribute('aria-expanded', String(open));
  b.classList.toggle('on', open);
}
export function notice(text) {
  $('entity-notice').textContent = text || '';
}

// a removable tag: name + ×
function tag(label, onRemove, removeTitle) {
  const c = document.createElement('span');
  c.className = 'tag';
  const t = document.createElement('span');
  t.textContent = label;
  c.appendChild(t);
  const x = document.createElement('button');
  x.className = 'x';
  x.textContent = '×';
  x.setAttribute('aria-label', removeTitle);
  x.title = removeTitle;
  x.onclick = () => busy(x, onRemove);
  c.appendChild(x);
  return c;
}

export async function showEntity(name) {
  state.selected = name;
  clearGroupSelection();   // right pane now shows an entity, not a group
  renderEntityList();
  const r = await (await fetch('/api/entities/observations?name=' + encodeURIComponent(name))).json();
  if (r.error) { alert(r.error); return; }
  state.selected = r.name;
  const info = state.entities[r.name] || {};
  showDetail('entity');
  notice('');
  if (splitFor !== r.name) { splitFor = info.unsorted ? r.name : null; splitPicked.clear(); }
  setName($('entity-name'), r.name, info);
  $('entity-meta').textContent = `${r.type}${info.category ? ` · ${info.category}` : ''} · ${r.observations.length} observation${r.observations.length === 1 ? '' : 's'}`
    + (info.mentions ? ` · ${info.mentions} mention${info.mentions === 1 ? '' : 's'}` : '');
  $('merge-target').value = '';
  $('retype-kind').value = '';
  // a thing's category, changeable in place
  $('thing-category').hidden = r.type !== 'thing';
  fillKindSelect($('thing-category'));
  $('thing-category').value = info.category || 'other';

  // aka / in chips under the meta, and the editable rows in the edit box
  const chips = $('entity-chips');
  chips.innerHTML = '';
  const aliasEd = $('alias-chips');
  aliasEd.innerHTML = '';
  for (const a of (info.aliases || []).filter(a => a !== info.variant_of)) {
    const c = document.createElement('span');
    c.className = 'tag';
    c.innerHTML = '<span class="k">aka</span><span class="v"></span>';
    c.querySelector('.v').textContent = a;
    chips.appendChild(c);
    aliasEd.appendChild(tag(a, async () => {
      if (await api('/api/entities/alias', {name: r.name, remove: a})) await reloadEntity(r.name);
    }, `remove alias ${a}`));
  }
  if (info.retired) {
    const c = document.createElement('span');
    c.className = 'tag muted';
    c.innerHTML = '<span class="v">retired</span>';
    if (info.retired !== 'self') c.title = `retired with the group ${info.retired}`;
    chips.appendChild(c);
  }
  // maybe two people: confirm one, or move one person's mentions out
  // with "move" below (each date says what that entry called them)
  const mx = $('entity-mixup');
  mx.innerHTML = '';
  if (info.mixup) {
    mx.append(`possibly two people: ${mixupText(info.mixup)}. `);
    const sp = document.createElement('button');
    sp.className = 'link';
    sp.textContent = 'split them';
    sp.title = 'pick one person\'s observations and give each person a name';
    sp.onclick = () => { splitFor = r.name; splitPicked.clear(); showEntity(r.name); };
    mx.append(sp, ', or ');
    const one = document.createElement('button');
    one.className = 'link';
    one.textContent = "it's one person";
    one.title = 'stop flagging this name';
    one.onclick = () => busy(one, async () => {
      if (await api('/api/entities/not-mixed', {name: r.name, not_mixed: true})) {
        delete info.mixup;
        renderEntityList();
        mx.innerHTML = '';
        notice(`${r.name} won't be flagged again. Undo brings the flag back.`);
      }
    });
    mx.append(one);
  }
  // part of a parent, or the parent of parts: each opens the other
  if (info.part_of) {
    const c = document.createElement('button');
    c.className = 'tag';
    c.innerHTML = '<span class="k">part of</span><span class="v"></span>';
    c.querySelector('.v').textContent = info.part_of;
    c.title = 'open it';
    c.onclick = () => showEntity(info.part_of);
    chips.appendChild(c);
  }
  for (const p of (info.parts || [])) {
    const c = document.createElement('button');
    c.className = 'tag';
    c.innerHTML = '<span class="k">part</span><span class="v"></span>';
    c.querySelector('.v').textContent = state.entities[p]?.base || p;
    c.title = 'open it';
    c.onclick = () => showEntity(p);
    chips.appendChild(c);
  }
  $('part-btn').hidden = NO_PARTS.has(r.type);
  $('split-btn').hidden = r.type !== 'person';
  $('split-btn').textContent = info.unsorted ? 'sort…' : 'split…';
  $('part-row').hidden = !info.part_of;
  $('part-chip').innerHTML = '';
  if (info.part_of) {
    $('part-chip').appendChild(tag(info.part_of, async () => {
      const res = await api('/api/entities/part-of', {name: r.name, parent: ''});
      if (res) await reloadEntity(res.name);
    }, `no longer part of ${info.part_of}`));
    $('part-path').checked = r.name.includes(' / ');
  }
  // retired through a group can only be undone there
  const rb = $('retire-btn');
  rb.textContent = info.retired ? 'un-retire' : 'retire';
  rb.disabled = !!info.retired && info.retired !== 'self';
  rb.title = rb.disabled ? `retired with the group ${info.retired}; un-retire the group, or add them to an active one`
    : info.retired ? 'back into everyday view' : 'a past chapter: out of the list and triage, and the companion only brings them up when you do. Nothing is deleted';
  $('group-add-input').value = '';
  const gchips = $('group-chips');
  gchips.innerHTML = '';
  for (const g of (info.groups || [])) {
    const c = document.createElement('button');
    c.className = 'tag';
    c.innerHTML = '<span class="k">in</span><span class="v"></span>';
    c.querySelector('.v').textContent = groupPathLabel(g);
    c.title = 'open this group';
    c.onclick = () => showGroupByName(g);
    chips.appendChild(c);
    gchips.appendChild(tag(groupPathLabel(g), async () => {
      if (await api('/api/groups/member', {group: g, entity: r.name, remove: true})) await reloadEntity(r.name);
    }, `remove from group ${g}`));
  }

  // observations grouped by date, each editable
  const docEl = $('entity-doc');
  docEl.innerHTML = '<div class="rule eyebrow">observations</div>';
  // splitting: a bar on top, a checkbox per observation, "all" per entry
  const splitting = splitFor === r.name && r.type === 'person';
  const pickKey = o => `${o.file}|${o.group}|${o.ent_index}|${o.obs_index}`;
  if (splitting) docEl.appendChild(splitBar(r, info));
  // each entry's mention gets a date line saying what that entry called
  // it ("as coworker"), which is how two people under one name tell apart
  let lastKey = null;
  for (const o of r.observations) {
    const key = `${o.file}|${o.group}|${o.ent_index}`;
    if (key !== lastKey) {
      lastKey = key;
      const d = document.createElement('div');
      d.className = 'obs-date eyebrow';
      d.textContent = fmtDate(o.date) + (o.extracted_name !== (info.base || info.variant_of || r.name) ? ` · as "${o.extracted_name}"` : '')
        + (HAS_RELATIONSHIP.has(r.type) && o.attr ? ` · as ${o.attr}` : '');
      if (splitting) {
        const all = document.createElement('button');
        all.className = 'link pick-all';
        all.textContent = 'all from this entry';
        const mine = r.observations.filter(x => `${x.file}|${x.group}|${x.ent_index}` === key);
        all.onclick = () => {
          const on = !mine.every(x => splitPicked.has(pickKey(x)));
          for (const x of mine) on ? splitPicked.add(pickKey(x)) : splitPicked.delete(pickKey(x));
          showEntity(r.name);
        };
        d.append(' ', all);
      } else if (HAS_RELATIONSHIP.has(r.type)) {
        // only this entry's label; the others under the name keep theirs
        const rel = document.createElement('button');
        rel.className = 'link rel-edit';
        rel.textContent = o.attr ? 'change' : 'add relationship';
        rel.title = `what this entry calls ${o.extracted_name}`;
        rel.onclick = () => busy(rel, async () => {
          const text = prompt(`What was ${o.extracted_name} in this entry? (e.g. friend, pet cat)\nLeave it empty to clear it.`, o.attr);
          if (text === null || text.trim() === o.attr) return;
          if (await api('/api/observation', {...o, action: 'relationship', text: text.trim()})) await reloadEntity(r.name);
        });
        d.append(' ', rel);
      }
      docEl.appendChild(d);
    }
    const row = document.createElement('div');
    row.className = 'obs';
    if (splitting) {
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'pick';
      cb.checked = splitPicked.has(pickKey(o));
      cb.setAttribute('aria-label', 'pick this observation');
      cb.onchange = () => { cb.checked ? splitPicked.add(pickKey(o)) : splitPicked.delete(pickKey(o)); refreshSplitBar(); };
      row.appendChild(cb);
    }
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = o.text;
    const ops = document.createElement('span');
    ops.className = 'ops';
    const mk = (label, title, fn, cls) => {
      const b = document.createElement('button');
      b.textContent = label; b.title = title;
      b.onclick = () => busy(b, fn, ...ops.querySelectorAll('button'));
      if (cls) b.className = cls;
      ops.appendChild(b);
    };
    mk('edit', 'edit this observation', async () => {
      const text = prompt('Edit observation:', o.text);
      if (text === null || text.trim() === o.text) return;
      if (await api('/api/observation', {...o, action: 'edit', text: text.trim()})) await reloadEntity(r.name);
    });
    mk('move', 'move this observation to another entity', async () => {
      const target = prompt('Move this observation to which entity?\n(prefix with person:/project:/place:/thing:/animal: if it\'s new)', '');
      if (!target) return;
      let kind = r.type, tname = target.trim();
      const m = tname.match(/^(person|project|place|thing|animal):(.+)$/);
      if (m) { kind = m[1]; tname = m[2].trim(); }
      else if (state.entities[tname]) kind = state.entities[tname].type;
      if (await api('/api/observation', {...o, action: 'reassign', target_kind: kind, target_name: tname})) await reloadEntity(r.name);
    });
    mk('×', 'delete this observation', async () => {
      if (!confirm('Delete this observation?')) return;
      if (await api('/api/observation', {...o, action: 'delete'})) await reloadEntity(r.name);
    }, 'x');
    row.appendChild(t);
    row.appendChild(ops);
    docEl.appendChild(row);
  }
  if (!r.observations.length) {
    const e = document.createElement('p');
    e.className = 'gp-empty';
    e.textContent = 'nothing noted yet.';
    docEl.appendChild(e);
  }
}

// The split bar: where the picked observations go, and -- the first time
// a name is split -- what the rest are called, and which one keeps each
// group. Unsorted mentions are the same: pick, then say who.
let refreshSplitBar = () => {};
function splitBar(r, info) {
  const bar = document.createElement('div');
  bar.className = 'split-bar';
  const head = info.variant_of || info.base || r.name;
  // a retired person's later mentions: someone new is a first split, with
  // the retired one as the rest
  const first = !info.variant_of || info.closed;
  const others = info.closed ? [] : variantsOf(head).filter(q => q !== info.qualifier);
  bar.innerHTML = '<p class="lead"></p><div class="ent-actions to"><span class="lbl"></span></div>';
  bar.querySelector('.lead').textContent = info.closed
    ? `${head} is retired, and these later entries didn't say it was them. Pick ones about the retired ${head} to keep with them, or ones about someone new and name both.`
    : info.unsorted
    ? `These mentions of ${head} didn't say which ${head}. Pick the ones about the same person and say who.`
    : first
      ? `Pick one person's observations ("all from this entry" takes a whole entry), then name both people. Each name is ${head} plus a word that tells them apart, like ${head} · work.`
      : `Pick observations that belong to another ${head}.`;
  const to = bar.querySelector('.to');
  const sel = document.createElement('select');
  sel.className = 'quiet-select sm';
  sel.setAttribute('aria-label', 'move them to');
  for (const q of others) sel.add(new Option(`${head} · ${q}`, q));
  sel.add(new Option(`${head} · new…`, ''));
  const qIn = document.createElement('input');
  qIn.className = 'input-xs';
  qIn.placeholder = first ? 'e.g. work' : 'a new word…';
  qIn.setAttribute('aria-label', 'qualifier for the picked ones');
  const showNew = () => { qIn.hidden = !!sel.value; };
  sel.onchange = showNew;
  if (others.length) to.append(sel);
  to.append(qIn);
  showNew();
  let restIn = null;
  const groupSels = {};
  if (first) {
    const rest = document.createElement('div');
    rest.className = 'ent-actions';
    rest.innerHTML = '<span class="lbl"></span>';
    rest.querySelector('.lbl').textContent = info.closed ? `and the retired one is ${head} ·` : `and the rest are ${head} ·`;
    restIn = document.createElement('input');
    restIn.className = 'input-xs';
    restIn.placeholder = 'e.g. friend';
    if (info.closed) restIn.value = 'past';
    restIn.setAttribute('aria-label', 'qualifier for the rest');
    rest.append(restIn);
    bar.append(rest);
    for (const g of (info.groups || [])) {
      const row = document.createElement('div');
      row.className = 'ent-actions';
      row.innerHTML = '<span class="lbl"></span>';
      row.querySelector('.lbl').textContent = `${g} stays with`;
      const gs = document.createElement('select');
      gs.className = 'quiet-select sm';
      gs.add(new Option('the rest', 'rest'));
      gs.add(new Option('the picked ones', 'picked'));
      row.append(gs);
      bar.append(row);
      groupSels[g] = gs;
    }
  }
  const btns = document.createElement('div');
  btns.className = 'ent-actions';
  const go = document.createElement('button');
  go.className = 'filled sm';
  const cancel = document.createElement('button');
  cancel.className = 'text';
  cancel.textContent = info.unsorted ? 'close' : 'cancel';
  cancel.onclick = () => { splitFor = null; splitPicked.clear(); showEntity(r.name); };
  let keep = null;
  if (info.closed) {
    keep = document.createElement('button');
    keep.className = 'quiet sm';
    keep.onclick = () => busy(keep, async () => {
      const picks = r.observations.filter(o => splitPicked.has(`${o.file}|${o.group}|${o.ent_index}|${o.obs_index}`))
        .map(o => ({file: o.file, group: o.group, ent_index: o.ent_index, obs_index: o.obs_index}));
      const res = await api('/api/entities/keep-retired', {name: r.name, picks});
      if (!res) return;
      splitFor = null;
      splitPicked.clear();
      await loadEntities();
      const next = state.entities[r.name] ? r.name : res.to;
      if (state.entities[next]) await showEntity(next); else showNone();
      notice(`kept with ${res.to}, who stays retired. Undo puts it back.`);
    }, ...bar.querySelectorAll('button, input, select'));
    btns.append(keep);
  }
  btns.append(go, cancel);
  bar.append(btns);
  refreshSplitBar = () => {
    const n = splitPicked.size;
    to.querySelector('.lbl').textContent = `move ${n} picked to`;
    go.textContent = info.closed ? `someone new (${n})` : info.unsorted ? `sort ${n}` : first ? 'split' : `move ${n}`;
    go.disabled = !n;
    if (keep) {
      keep.textContent = `keep ${n} with the retired ${head}`;
      keep.disabled = !n;
    }
  };
  refreshSplitBar();
  go.onclick = () => busy(go, async () => {
    const qualifier = (sel.value && others.length ? sel.value : qIn.value).trim();
    if (!qualifier) { qIn.focus(); return; }
    if (restIn && !restIn.value.trim()) { restIn.focus(); return; }
    const picks = r.observations.filter(o => splitPicked.has(`${o.file}|${o.group}|${o.ent_index}|${o.obs_index}`))
      .map(o => ({file: o.file, group: o.group, ent_index: o.ent_index, obs_index: o.obs_index}));
    const groups = Object.fromEntries(Object.entries(groupSels).map(([g, s]) =>
      [g, s.value === 'picked' ? qualifier : restIn.value.trim()]));
    const res = await api('/api/entities/split', {name: r.name, picks, qualifier, rest: restIn ? restIn.value.trim() : '', groups});
    if (!res) return;
    splitFor = null;
    splitPicked.clear();
    await loadEntities();
    // stay with what's left, or go to where they went
    const next = state.entities[r.name] ? r.name : (res.rest || res.to);
    if (state.entities[next]) await showEntity(next); else showNone();
    notice(`${res.did}. Undo puts it back.`);
  }, ...bar.querySelectorAll('button, input, select'));
  return bar;
}

// An edit-box action: busy on its own control, with the rest of the box
// held, so one edit runs at a time (each waits on the server's write lock
// and an index rebuild).
export function editBusy(el, work) {
  return busy(el, work, ...$('entity-edit').querySelectorAll('button, input, select'));
}

export async function reloadEntity(name) {
  await loadEntities();
  if (state.entities[name]) await showEntity(name);
  else showNone();
}

// Back to the prompt. `msg`, when given, is what just happened to the entity
// that was open (merged, deleted, undone), said once on the list side.
function showNone() {
  state.selected = null;
  clearGroupSelection();
  renderEntityList();
  showDetail('none');
  $('entities-pane').classList.remove('drilled');
}
function clearDetail(msg) {
  showNone();
  loadEntities();
  if (msg) flash(msg);
}
// a notice line over the list, gone after a few seconds
let flashTimer = null;
function flash(msg) {
  let el = $('entity-flash');
  if (!el) {
    el = document.createElement('div');
    el.id = 'entity-flash';
    el.className = 'notice-line';
    el.style.padding = '0 .9rem .4rem';
    $('entity-groups').before(el);
  }
  el.textContent = msg;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.textContent = ''; }, 4000);
}
// groups.js switches the pane to a group page through this
export function showGroupPane() { showDetail('group'); }

async function histStep(url) {
  const btn = url.endsWith('undo') ? $('undo-btn') : $('redo-btn');
  const r = await busy(btn, async () => (await fetch(url, {method: 'POST'})).json(), $('undo-btn'), $('redo-btn'));
  if (!r) return;
  if (r.error) { alert(r.error); return; }
  await loadEntities();
  if (state.selected && state.entities[state.selected]) await showEntity(state.selected);
  else if (state.selected) clearDetail(r.undid ? `undid: ${r.undid}` : `redid: ${r.redid}`);
  refreshHistoryButtons();
}

async function refreshHistoryButtons() {
  const h = await (await fetch('/api/history')).json();
  $('undo-btn').title = h.undo ? `undo: ${h.undo}` : 'nothing to undo';
  $('redo-btn').title = h.redo ? `redo: ${h.redo}` : 'nothing to redo';
  $('undo-btn').classList.toggle('empty', !h.undo);
  $('redo-btn').classList.toggle('empty', !h.redo);
}

function pressed(id, on) {
  $(id).classList.toggle('on', on);
  $(id).setAttribute('aria-pressed', String(on));
  updateExtra();
}

export function init() {
  $('flt-unreviewed').onclick = () => {
    filters.unreviewed = !filters.unreviewed;
    pressed('flt-unreviewed', filters.unreviewed);
    renderEntityList();
  };
  $('flt-retired').onclick = () => {
    filters.retired = !filters.retired;
    pressed('flt-retired', filters.retired);
    loadGroups();  // the group browser shows retired groups only under this chip
    renderEntityList();
  };
  $('retire-btn').onclick = () => editBusy($('retire-btn'), async () => {
    if (!state.selected) return;
    const retire = !state.entities[state.selected]?.retired;
    const r = await api('/api/entities/retire', {name: state.selected, retired: retire});
    if (r) await reloadEntity(r.name);
    if (r && retire) flash(`${r.name} retired. Triage & recycling bin → retired shows them.`);
  });
  $('flt-mixup').onclick = () => {
    filters.mixup = !filters.mixup;
    pressed('flt-mixup', filters.mixup);
    renderEntityList();
  };
  $('flt-single').onclick = () => {
    filters.single = !filters.single;
    pressed('flt-single', filters.single);
    renderEntityList();
  };
  $('sort-az').onclick = () => {
    sortAlpha = !sortAlpha;
    pressed('sort-az', sortAlpha);
    renderEntityList();
  };
  $('flt-select').onclick = () => {
    selectMode = !selectMode;
    pressed('flt-select', selectMode);
    $('batch-bar').hidden = !selectMode;
    if (!selectMode) picked.clear();
    updateBatchCount();
    renderEntityList();
  };
  $('entity-edit-toggle').onclick = () => setEditOpen(!editOpen);
  $('entity-back').onclick = () => $('entities-pane').classList.remove('drilled');
  $('entity-close').onclick = showNone;
  // Esc closes the open entity or group, unless a field has focus or the
  // clean up menu is open (that Esc is the menu's; captured, so this runs
  // before popover.js closes it)
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || state.activeTab !== 'entities' || $('entity-detail-col').hidden) return;
    if (e.target.closest('input, textarea, select, dialog') || !$('cleanup-menu').hidden) return;
    showNone();
  }, true);
  $('entity-to-triage').onclick = () => showTab('triage');
  $('suggest-back').onclick = () => $('entities-pane').classList.remove('drilled');
  $('batch-add').onclick = batchAdd;
  $('batch-group').onkeydown = e => { if (e.key === 'Enter') batchAdd(); };
  $('batch-clear').onclick = () => { picked.clear(); updateBatchCount(); renderEntityList(); };
  $('search').oninput = renderEntityList;
  $('extra-toggle').onclick = () => setExtraOpen($('extra-panel').hidden);
  $('show-deleted').onclick = () => {
    if ($('show-deleted').getAttribute('aria-pressed') === 'true') closeSuggest();
    else showDeleted();
  };
  // the clean up menu: an item runs and closes it; find duplicates (api
  // call) opens its kinds in place instead
  $('cleanup-btn').onclick = () => setCleanupOpen($('cleanup-menu').hidden);
  popover.register('cleanup', () => setCleanupOpen(false),
    t => $('cleanup-menu').contains(t) || $('cleanup-btn').contains(t));
  const tools = {
    'find-dups': findDups, 'find-generic': genericCleanup, 'animals-review': animalsReview,
    'things-review': thingsReview, 'parts-review': partsReview, 'reextract-open': reextractPanel,
  };
  const held = b => paidRunning && b.classList.contains('paid');
  for (const [id, fn] of Object.entries(tools)) $(id).onclick = () => { if (held($(id))) return; setCleanupOpen(false); fn(); };
  $('suggest-toggle').onclick = () => setSuggestKindsOpen($('suggest-kinds').hidden);
  document.querySelectorAll('#suggest-kinds [data-kind]').forEach(b => b.onclick = () => {
    if (held(b)) return;
    setCleanupOpen(false);
    askSuggest(b.dataset.kind);
  });
  $('split-btn').onclick = () => {
    splitFor = splitFor === state.selected ? null : state.selected;
    splitPicked.clear();
    showEntity(state.selected);
  };
  $('part-btn').onclick = () => {
    const parent = $('merge-target').value.trim();
    if (!state.selected || !parent) { $('merge-target').focus(); return; }
    editBusy($('part-btn'), async () => {
      const r = await api('/api/entities/part-of', {name: state.selected, parent});
      if (r) await reloadEntity(r.name);
    });
  };
  $('part-path').onchange = () => editBusy($('part-path'), async () => {
    const r = await api('/api/entities/hide-path', {name: state.selected, hide: !$('part-path').checked});
    if (r) await reloadEntity(r.name);
  });

  $('merge-btn').onclick = () => editBusy($('merge-btn'), async () => {
    const target = $('merge-target').value.trim();
    if (!state.selected || !target) return;
    if (!confirm(`Merge "${state.selected}" into "${target}"?\n("${state.selected}" stays as an alias)`)) return;
    const r = await api('/api/entities/merge', {source: state.selected, target});
    if (r) clearDetail(`merged into ${r.into}`);
  });

  $('correct-btn').onclick = () => editBusy($('correct-btn'), async () => {
    const target = $('merge-target').value.trim();
    if (!state.selected || !target) return;
    if (!confirm(`Correct "${state.selected}" to "${target}"?\n(this fixes a typo, so "${state.selected}" is NOT kept as an alias)`)) return;
    const r = await api('/api/entities/correct', {source: state.selected, target});
    if (r) clearDetail(`corrected to ${r.into}`);
  });

  $('retype-kind').onchange = () => editBusy($('retype-kind'), async () => {
    const kind = $('retype-kind').value;
    if (!state.selected || !kind) return;
    const newName = prompt(`Move "${state.selected}" to ${PLURAL[kind]}. Rename it? (leave as-is to keep the name)`, state.selected);
    if (newName === null) { $('retype-kind').value = ''; return; }
    // a new thing starts as "other"; its category picker shows once it's open
    const r = await api('/api/entities/retype', {name: state.selected, new_type: kind, new_name: newName.trim()});
    if (r && kind === 'thing') await reloadEntity(newName.trim() || state.selected);
    else if (r) clearDetail(`moved to ${PLURAL[kind]}`);
  });

  $('thing-category').onchange = () => editBusy($('thing-category'), async () => {
    if (!state.selected) return;
    const r = await api('/api/entities/category', {name: state.selected, category: $('thing-category').value});
    if (r) await reloadEntity(r.name);
  });

  $('alias-add-btn').onclick = () => {
    const a = $('alias-new').value.trim();
    if (!state.selected || !a) return;
    editBusy($('alias-add-btn'), async () => {
      if (await api('/api/entities/alias', {name: state.selected, add: a})) {
        $('alias-new').value = '';
        await reloadEntity(state.selected);
      }
    });
  };

  $('rename-btn').onclick = () => editBusy($('rename-btn'), async () => {
    if (!state.selected) return;
    const own = state.entities[state.selected]?.base || state.selected;
    const newName = prompt(`Rename "${state.selected}" to:`, own);
    if (newName === null || !newName.trim() || newName.trim() === own) return;
    const r = await api('/api/entities/rename', {source: state.selected, target: newName.trim()});
    if (r) { state.selected = r.to; await reloadEntity(r.to); }
  });

  $('delete-btn').onclick = () => editBusy($('delete-btn'), async () => {
    if (!state.selected) return;
    if (!confirm(`Delete the mentions of "${state.selected}"?\n(your entries are untouched, and a new entry that mentions "${state.selected}" starts it fresh)`)) return;
    const r = await api('/api/entities/delete', {name: state.selected, mode: 'mentions'});
    if (r) clearDetail('deleted');
  });

  $('never-btn').onclick = () => editBusy($('never-btn'), async () => {
    if (!state.selected) return;
    if (!confirm(`Never track "${state.selected}"?\nEvery future "${state.selected}" will be ignored too. Meant for generic terms and junk.`)) return;
    const r = await api('/api/entities/delete', {name: state.selected, mode: 'name'});
    if (r) clearDetail(`no longer tracking ${r.deleted}`);
  });

  $('undo-btn').onclick = () => histStep('/api/undo');
  $('redo-btn').onclick = () => histStep('/api/redo');
  refreshHistoryButtons();
  setInterval(refreshHistoryButtons, 15000);

  // ---- type filter chips ----
  document.querySelectorAll('#type-chips [data-type]').forEach(b => b.onclick = () => {
    const kind = b.dataset.type;
    if (filters.types.has(kind)) filters.types.delete(kind); else filters.types.add(kind);
    // the kinds of thing go with things
    if (!filters.types.has('thing')) filters.thingCats.clear();
    b.classList.toggle('on', filters.types.has(kind));
    b.setAttribute('aria-pressed', String(filters.types.has(kind)));
    renderEntityList();
  });

  // ---- kinds of thing of your own ----
  popover.register('thing-kinds', () => setKindsOpen(false),
    t => $('thing-kinds-menu').contains(t) || !!$('thing-kinds-btn')?.contains(t));
  $('thing-kinds-close').onclick = () => setKindsOpen(false);
  $('thing-kind-save').onclick = () => busy($('thing-kind-save'), saveKind);
  $('thing-kind-cancel').onclick = () => { kindForm(null); renderKinds(); };
  for (const id of ['thing-kind-name', 'thing-kind-desc'])
    $(id).onkeydown = e => { if (e.key === 'Enter') $('thing-kind-save').click(); };
}

// ---- merge suggestions (find duplicates via claude, by type) ----
async function askSuggest(kind) {
  const panel = suggestPanel(`claude's suggestions · ${PLURAL[kind] || kind}`);
  const w = panelWait(panel, `asking claude for ${kind} merge suggestions…`);
  const r = await paid(`${PLURAL[kind] || kind} duplicates`, () => api('/api/entities/suggest', {kind}));
  w.done(); w.remove();
  if (!r) { closeSuggest(); return; }
  if (!r.groups.length) { panelSay(panel, 'no confident suggestions. Looks clean.'); return; }
  for (const g of r.groups) {
    const div = document.createElement('div');
    div.className = 'sg';
    div.innerHTML = `<span><strong>${g.members.map(esc).join(', ')}</strong> → ${esc(g.canonical)}</span>`;
    const ok = document.createElement('button');
    ok.className = 'quiet'; ok.textContent = 'apply';
    ok.onclick = () => busy(ok, async () => {
      for (const m of g.members) await api('/api/entities/merge', {source: m, target: g.canonical});
      div.remove();
      loadEntities();
    }, no);
    const no = document.createElement('button');
    no.className = 'quiet danger'; no.textContent = 'dismiss';
    no.onclick = () => div.remove();
    div.appendChild(ok);
    div.appendChild(no);
    const why = document.createElement('div');
    why.className = 'r';
    why.textContent = g.reason;
    div.appendChild(why);
    panel.appendChild(div);
  }
}
