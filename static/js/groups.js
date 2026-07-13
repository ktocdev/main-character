import { $, api } from './core.js';
import { state, filters } from './state.js';
import { loadEntities, renderEntityList, showEntity, reloadEntity } from './entities.js';

// ---- entity groups (viewing & associating; nestable via parent) ----
let groupsData = [];
let expandedGroup = null;

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
  renderGroupBrowser();
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
    const row = document.createElement('div');
    row.className = 'grp-row' + (filters.group === g.name ? ' on' : '');
    row.style.paddingLeft = (depth * 0.9) + 'rem';

    const deep = groupSetDeep(g.name);
    const memberSet = new Set();
    for (const gg of groupsData) {
      if (deep.has(gg.name.toLowerCase())) {
        for (const m of gg.members) memberSet.add(m.toLowerCase());
      }
    }

    const btn = document.createElement('button');
    btn.className = 'grp-btn';
    btn.innerHTML = `${g.name} <span class="n">${memberSet.size}</span>`;
    btn.title = 'show only entities in this group';
    btn.onclick = () => {
      filters.group = filters.group === g.name ? null : g.name;
      renderGroupBrowser();
      renderEntityList();
    };
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
    for (const child of sortG(childrenOf(g.name))) renderOne(child, depth + 1);
  };
  for (const g of sortG(roots)) renderOne(g, 0);
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
    const opening = f.style.display === 'none';
    f.style.display = opening ? 'flex' : 'none';
    if (opening) $('group-new-name').focus();
  };
  $('group-new-save').onclick = createGroup;
  $('group-new-name').onkeydown = e => { if (e.key === 'Enter') createGroup(); };
  $('group-add-btn').onclick = addSelectedToGroup;
  $('group-add-input').onkeydown = e => { if (e.key === 'Enter') addSelectedToGroup(); };
}
