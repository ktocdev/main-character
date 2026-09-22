// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, esc } from './core.js';
import { state, filters } from './state.js';
import { loadEntities, renderEntityList, showEntity, reloadEntity } from './entities.js';

// ---- entity groups (viewing & associating; nestable via parent) ----
let groupsData = [];
let expandedGroup = null;   // which group's editor is open
let openRollup = null;      // which rolled-up group is expanded to show its members
let openGroup = null;       // which group's detail page is shown in the right pane

export async function loadGroups() {
  groupsData = (await (await fetch('/api/groups')).json()).groups || [];
  const dl = $('group-names');
  dl.innerHTML = '';
  const sorted = [...groupsData].sort((a, b) =>
    a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  for (const g of sorted) {
    const o = document.createElement('option');
    o.value = g.name;
    dl.appendChild(o);
  }
  if (filters.group && !groupsData.some(g => g.name === filters.group)) filters.group = null;
  if (expandedGroup && !groupsData.some(g => g.name === expandedGroup)) expandedGroup = null;
  if (openRollup && !groupsData.some(g => g.name === openRollup)) openRollup = null;
  if (openGroup && !groupsData.some(g => g.name === openGroup)) openGroup = null;
  renderGroupBrowser();
}

// canonical (lowercased) names of every entity that sits in a rolled-up
// group — entities.js hides these from the flat list until the group is opened
export function rolledUpMemberSet() {
  const hidden = new Set();
  for (const g of groupsData) {
    if (!g.rollup) continue;
    const deep = groupSetDeep(g.name);
    for (const gg of groupsData) {
      if (deep.has(gg.name.toLowerCase())) {
        for (const m of gg.members) hidden.add(m.toLowerCase());
      }
    }
  }
  return hidden;
}

// deep member display names of a group (its own + nested children's),
// split into resolved (clickable) and unresolved (dormant, dimmed)
function deepMembers(name) {
  const deep = groupSetDeep(name);
  const resolved = new Map(), unresolved = new Map();
  for (const gg of groupsData) {
    if (!deep.has(gg.name.toLowerCase())) continue;
    for (const m of gg.members) resolved.set(m.toLowerCase(), m);
    for (const m of (gg.unresolved || [])) unresolved.set(m.toLowerCase(), m);
  }
  for (const k of resolved.keys()) unresolved.delete(k);
  const byName = (a, b) => a.toLowerCase().localeCompare(b.toLowerCase());
  return {
    resolved: [...resolved.values()].sort(byName),
    unresolved: [...unresolved.values()].sort(byName),
  };
}

export function groupSetDeep(name) {
  // lowercase names of the group + all nested child groups
  const active = new Set([name.toLowerCase()]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const g of groupsData) {
      if (g.parent && active.has(g.parent.toLowerCase()) && !active.has(g.name.toLowerCase())) {
        active.add(g.name.toLowerCase());
        changed = true;
      }
    }
  }
  return active;
}

export function groupPathLabel(name) {
  const chain = [], seen = new Set();
  let cur = groupsData.find(g => g.name.toLowerCase() === name.toLowerCase());
  while (cur && !seen.has(cur.name.toLowerCase())) {
    seen.add(cur.name.toLowerCase());
    chain.unshift(cur.name);
    const parent = cur.parent;
    cur = parent ? groupsData.find(g => g.name.toLowerCase() === parent.toLowerCase()) : null;
  }
  return chain.join(' › ') || name;
}

function renderGroupBrowser() {
  const list = $('group-list');
  list.innerHTML = '';
  const names = new Set(groupsData.map(g => g.name.toLowerCase()));
  const sortG = arr => arr.sort((a, b) =>
    a.name.toLowerCase().localeCompare(b.name.toLowerCase()));
  // orphaned parents render as roots so nothing disappears
  const roots = groupsData.filter(g => !g.parent || !names.has(g.parent.toLowerCase()));
  const childrenOf = name =>
    groupsData.filter(g => g.parent.toLowerCase() === name.toLowerCase());

  const renderOne = (g, depth) => {
    const rolled = !!g.rollup;
    const isOpen = openRollup === g.name;
    const row = document.createElement('div');
    row.className = 'grp-row'
      + ((openGroup === g.name || filters.group === g.name) ? ' on' : '')
      + (rolled ? ' rolled' : '');
    row.style.paddingLeft = (depth * 0.9) + 'rem';

    const deep = groupSetDeep(g.name);
    const memberSet = new Set();
    for (const gg of groupsData) {
      if (deep.has(gg.name.toLowerCase())) {
        for (const m of gg.members) memberSet.add(m.toLowerCase());
      }
    }

    if (rolled) {
      // the caret is a quick inline peek; the title opens the full page
      const caret = document.createElement('span');
      caret.className = 'grp-caret';
      caret.textContent = isOpen ? '▾' : '▸';
      caret.title = isOpen ? 'hide members here' : 'peek at members here';
      caret.onclick = () => {
        openRollup = isOpen ? null : g.name;
        renderGroupBrowser();
      };
      row.appendChild(caret);
    }

    const btn = document.createElement('button');
    btn.className = 'grp-btn';
    btn.innerHTML = `${esc(g.name)} <span class="n">${memberSet.size}</span>`;
    btn.title = 'open this group; list its entities on the right';
    btn.onclick = () => showGroup(g.name);
    row.appendChild(btn);

    const edit = document.createElement('button');
    edit.className = 'grp-edit' + (expandedGroup === g.name ? ' on' : '');
    edit.textContent = '✎';
    edit.title = 'edit this group';
    edit.onclick = () => {
      expandedGroup = expandedGroup === g.name ? null : g.name;
      renderGroupBrowser();
    };
    row.appendChild(edit);
    list.appendChild(row);

    if (expandedGroup === g.name) list.appendChild(groupEditor(g));
    if (rolled && isOpen) list.appendChild(rolledMembers(g));
    for (const child of sortG(childrenOf(g.name))) renderOne(child, depth + 1);
  };
  for (const g of sortG(roots)) renderOne(g, 0);
}

// clicking a group in the sidebar opens its page in the right pane: the entities
// it holds (clickable), any subgroups (drill down), and dormant members (dimmed)
export function showGroup(name) {
  const g = groupsData.find(x => x.name.toLowerCase() === name.toLowerCase());
  if (!g) return;
  openGroup = g.name;
  state.selected = null;
  renderGroupBrowser();   // move the row highlight to this group
  renderEntityList();     // drop any entity-row highlight

  // this pane is shared with the entity view — hide its entity-only controls
  $('suggest-panel').style.display = 'none';
  $('entity-actions').style.display = 'none';
  $('alias-row').style.display = 'none';
  $('group-row').style.display = 'none';

  const byName = (a, b) => a.toLowerCase().localeCompare(b.toLowerCase());
  const kids = groupsData
    .filter(x => x.parent && x.parent.toLowerCase() === g.name.toLowerCase())
    .sort((a, b) => byName(a.name, b.name));
  const members = [...g.members].sort(byName);
  const dormant = [...(g.unresolved || [])].sort(byName);

  $('entity-name').textContent = groupPathLabel(g.name);
  const bits = [`${members.length} entit${members.length === 1 ? 'y' : 'ies'}`];
  if (kids.length) bits.push(`${kids.length} subgroup${kids.length === 1 ? '' : 's'}`);
  $('entity-meta').textContent = 'group · ' + bits.join(' · ');

  const doc = $('entity-doc');
  doc.innerHTML = '';
  const page = document.createElement('div');
  page.className = 'group-page';

  // preserve the old behavior — narrowing the flat left list to this group
  const ops = document.createElement('div');
  ops.className = 'gp-ops';
  const filtering = filters.group === g.name;
  const flt = document.createElement('button');
  flt.className = 'quiet' + (filtering ? ' on chip-toggle' : '');
  flt.textContent = filtering ? 'clear list filter' : 'show only these in the list';
  flt.title = 'narrow the left-hand entity list to this group’s members';
  flt.onclick = () => {
    filters.group = filtering ? null : g.name;
    renderEntityList();
    showGroup(g.name);    // re-render to flip the button + row highlight
  };
  ops.appendChild(flt);
  page.appendChild(ops);

  const section = label => {
    const s = document.createElement('div');
    s.className = 'gp-section';
    s.textContent = label;
    page.appendChild(s);
  };
  const grid = () => {
    const gd = document.createElement('div');
    gd.className = 'gp-grid';
    page.appendChild(gd);
    return gd;
  };

  if (kids.length) {
    section('subgroups');
    const gd = grid();
    for (const k of kids) {
      const b = document.createElement('button');
      b.className = 'gp-sub';
      b.innerHTML = `${esc(k.name)} <span class="n">${k.members.length}</span>`;
      b.title = 'open this subgroup';
      b.onclick = () => showGroup(k.name);
      gd.appendChild(b);
    }
  }

  section(`entities (${members.length})`);
  if (members.length) {
    const gd = grid();
    for (const m of members) {
      const b = document.createElement('button');
      b.className = 'gp-ent';
      b.textContent = m;
      b.title = 'open entity';
      b.onclick = () => showEntity(m);
      gd.appendChild(b);
    }
  } else {
    const e = document.createElement('div');
    e.className = 'gp-empty';
    e.textContent = 'no entities in this group yet';
    page.appendChild(e);
  }

  if (dormant.length) {
    section('dormant');
    const gd = grid();
    for (const m of dormant) {
      const s = document.createElement('span');
      s.className = 'gp-ent dim';
      s.textContent = m;
      s.title = 'this name no longer matches an entity';
      gd.appendChild(s);
    }
  }

  doc.appendChild(page);
}

// entities.js calls this when an entity is opened, so the group page's row
// highlight clears (the right pane now shows the entity, not the group)
export function clearGroupSelection() {
  if (openGroup !== null) {
    openGroup = null;
    renderGroupBrowser();
  }
}

// inline member list shown under a rolled-up group when it's expanded —
// its entities live here instead of cluttering the flat person/project list
function rolledMembers(g) {
  const box = document.createElement('div');
  box.className = 'grp-members';
  const { resolved, unresolved } = deepMembers(g.name);
  if (!resolved.length && !unresolved.length) {
    const e = document.createElement('div');
    e.className = 'empty';
    e.textContent = 'no members yet';
    box.appendChild(e);
    return box;
  }
  for (const m of resolved) {
    const b = document.createElement('button');
    b.className = 'gm';
    b.textContent = m;
    b.title = 'open entity';
    b.onclick = () => showEntity(m);
    box.appendChild(b);
  }
  for (const m of unresolved) {
    const b = document.createElement('span');
    b.className = 'gm dim';
    b.textContent = m;
    b.title = 'no longer matches an entity';
    box.appendChild(b);
  }
  return box;
}

function groupEditor(g) {
  const ed = document.createElement('div');
  ed.className = 'grp-editor';

  const chips = document.createElement('div');
  chips.className = 'chips';
  const mkChip = (m, dim) => {
    const c = document.createElement('span');
    c.className = 'kw-chip member' + (dim ? ' dim' : '');
    const t = document.createElement('span');
    t.textContent = m;
    if (dim) t.title = 'no longer matches an entity';
    else {
      t.style.cursor = 'pointer';
      t.title = 'open entity';
      t.onclick = () => showEntity(m);
    }
    c.appendChild(t);
    const x = document.createElement('button');
    x.textContent = '×';
    x.title = 'remove from group';
    x.onclick = async () => {
      if (await api('/api/groups/member', {group: g.name, entity: m, remove: true})) await loadEntities();
    };
    c.appendChild(x);
    chips.appendChild(c);
  };
  for (const m of g.members) mkChip(m, false);
  for (const m of g.unresolved) mkChip(m, true);
  if (!g.members.length && !g.unresolved.length) chips.textContent = 'no members yet';
  ed.appendChild(chips);

  const addRow = document.createElement('div');
  addRow.className = 'grp-ops';
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.setAttribute('list', 'entity-names');
  inp.placeholder = 'add entity…';
  const addB = document.createElement('button');
  addB.className = 'quiet';
  addB.textContent = 'add';
  const doAdd = async () => {
    const v = inp.value.trim();
    if (!v) return;
    if (await api('/api/groups/member', {group: g.name, entity: v})) await loadEntities();
  };
  addB.onclick = doAdd;
  inp.onkeydown = e => { if (e.key === 'Enter') doAdd(); };
  addRow.appendChild(inp);
  addRow.appendChild(addB);
  ed.appendChild(addRow);

  const ops = document.createElement('div');
  ops.className = 'grp-ops';

  const roll = document.createElement('button');
  roll.className = 'quiet' + (g.rollup ? ' on chip-toggle' : '');
  roll.textContent = g.rollup ? 'unroll' : 'roll up';
  roll.title = g.rollup
    ? 'show this group’s members in the main list again'
    : 'hide this group’s members from the main list; click the group title to reveal them';
  roll.onclick = async () => {
    if (await api('/api/groups/edit', {name: g.name, rollup: !g.rollup})) {
      if (!g.rollup) openRollup = null;  // will re-collapse; nothing to keep open
      await loadGroups();
      renderEntityList();
    }
  };
  ops.appendChild(roll);

  const ren = document.createElement('button');
  ren.className = 'quiet';
  ren.textContent = 'rename';
  ren.onclick = async () => {
    const v = prompt('Rename group:', g.name);
    if (!v || v.trim() === g.name) return;
    if (await api('/api/groups/edit', {name: g.name, rename: v.trim()})) {
      if (filters.group === g.name) filters.group = v.trim();
      if (expandedGroup === g.name) expandedGroup = v.trim();
      await loadEntities();
    }
  };
  ops.appendChild(ren);

  const sel = document.createElement('select');
  sel.title = 'nest this group under another';
  const own = groupSetDeep(g.name);
  const optRoot = document.createElement('option');
  optRoot.value = '';
  optRoot.textContent = '(no parent)';
  sel.appendChild(optRoot);
  for (const other of groupsData) {
    if (own.has(other.name.toLowerCase())) continue;
    const o = document.createElement('option');
    o.value = other.name;
    o.textContent = 'under ' + other.name;
    if (g.parent.toLowerCase() === other.name.toLowerCase()) o.selected = true;
    sel.appendChild(o);
  }
  sel.onchange = async () => {
    if (await api('/api/groups/edit', {name: g.name, parent: sel.value})) await loadEntities();
  };
  ops.appendChild(sel);

  const del = document.createElement('button');
  del.className = 'quiet';
  del.textContent = 'delete';
  del.onclick = async () => {
    if (!confirm(`Delete group "${g.name}"?\n(entities are not affected; nested groups move up a level)`)) return;
    if (await api('/api/groups/edit', {name: g.name, delete: true})) {
      if (filters.group === g.name) filters.group = null;
      expandedGroup = null;
      await loadEntities();
    }
  };
  ops.appendChild(del);
  ed.appendChild(ops);
  return ed;
}

async function createGroup() {
  const v = $('group-new-name').value.trim();
  if (!v) return;
  if (await api('/api/groups', {name: v})) {
    $('group-new-name').value = '';
    $('group-newform').style.display = 'none';
    await loadEntities();
  }
}

async function addSelectedToGroup() {
  const v = $('group-add-input').value.trim();
  if (!v || !state.selected) return;
  if (await api('/api/groups/member', {group: v, entity: state.selected})) {
    $('group-add-input').value = '';
    await reloadEntity(state.selected);
  }
}

export function init() {
  $('group-new-btn').onclick = () => {
    const f = $('group-newform');
    // computed, not f.style: it starts hidden by entities.css, not inline
    const opening = getComputedStyle(f).display === 'none';
    f.style.display = opening ? 'flex' : 'none';
    if (opening) $('group-new-name').focus();
  };
  $('group-new-save').onclick = createGroup;
  $('group-new-name').onkeydown = e => { if (e.key === 'Enter') createGroup(); };
  $('group-add-btn').onclick = addSelectedToGroup;
  $('group-add-input').onkeydown = e => { if (e.key === 'Enter') addSelectedToGroup(); };
}
