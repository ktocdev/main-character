// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, esc, fmtDate, refreshStatus } from './core.js';
import { state, filters } from './state.js';
import { showTab } from './main.js';
import { loadGroups, groupSetDeep, groupPathLabel, rolledUpMemberSet, clearGroupSelection, showGroup as showGroupByName } from './groups.js';

// ---- entities ----
export async function loadEntities() {
  state.entities = await (await fetch('/api/entities')).json();
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

const PLURAL = {person: 'people', project: 'projects', place: 'places'};
let sortAlpha = false;
let selectMode = false;         // checkboxes for batch add-to-group
const picked = new Set();       // entity names checked for the next batch

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
  const groups = {person: [], project: [], place: []};
  // retired entities (a past chapter) live behind their own chip
  const retiredCount = Object.values(state.entities).filter(i => i.retired).length;
  $('flt-retired').hidden = !retiredCount && !filters.retired;
  $('flt-retired').textContent = `retired (${retiredCount})`;
  for (const [name, info] of Object.entries(state.entities)) {
    if (!!info.retired !== filters.retired) continue;
    const hay = (name + ' ' + (info.aliases || []).join(' ')).toLowerCase();
    if (filter && !hay.includes(filter)) continue;
    if (filters.unreviewed && info.reviewed) continue;
    if (filters.single && info.mentions !== 1) continue;
    if (activeGroupSet && !(info.groups || []).some(g => activeGroupSet.has(g.toLowerCase()))) continue;
    if (hideSet && hideSet.has(name.toLowerCase())) continue;
    groups[info.type].push([name, info.mentions, info.reviewed]);
  }
  const wrap = $('entity-groups');
  wrap.innerHTML = '';
  const typeFilter = filters.types.size ? filters.types : null;
  let shown = 0;
  for (const kind of ['person', 'project', 'place']) {
    if (typeFilter && !typeFilter.has(kind)) continue;
    const items = groups[kind].sort(sortAlpha
      ? (a, b) => a[0].toLowerCase().localeCompare(b[0].toLowerCase())
      : (a, b) => b[1] - a[1]);
    if (!items.length) continue;
    shown += items.length;
    const h = document.createElement('div');
    h.className = 'list-head';
    h.innerHTML = '<span class="eyebrow"></span>';
    h.firstChild.textContent = `${PLURAL[kind]} (${items.length})`;
    wrap.appendChild(h);
    for (const [name, mentions, reviewed] of items) {
      const row = document.createElement('div');
      row.className = 'ent-row';

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
        };
        row.appendChild(cb);
      }

      const b = document.createElement('button');
      b.className = 'list-item' + (name === state.selected ? ' sel' : '');
      b.innerHTML = '<span class="li-title"></span><span class="li-meta"></span>';
      b.querySelector('.li-title').textContent = name;
      b.querySelector('.li-meta').textContent = mentions;
      if (!reviewed) {
        const dot = document.createElement('span');
        dot.className = 'dot right';
        dot.setAttribute('aria-label', 'unreviewed');
        b.appendChild(dot);
      }
      b.onclick = () => selectMode
        ? row.querySelector('.ent-check')?.click()
        : showEntity(name);
      row.appendChild(b);

      if (!selectMode) {
        const keep = document.createElement('button');
        keep.className = 'ent-keep' + (reviewed ? ' on' : '');
        keep.textContent = reviewed ? '✓' : 'keep';
        keep.title = reviewed ? 'reviewed (click to unmark)' : 'mark reviewed';
        keep.onclick = async e => {
          e.stopPropagation();
          const next = !reviewed;
          if (await api('/api/entities/reviewed', {name, reviewed: next})) {
            state.entities[name].reviewed = next;
            renderEntityList();
          }
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
}

// ---- batch add to group ----
async function batchAdd() {
  const group = $('batch-group').value.trim();
  if (!group) { $('batch-group').focus(); return; }
  if (!picked.size) return;
  const r = await api('/api/groups/members', {group, entities: [...picked]});
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
// shows on its own when nothing is picked yet.
function suggestPanel(title) {
  const panel = $('suggest-panel');
  $('suggest-wrap').hidden = false;
  $('entities-pane').classList.add('drilled');
  panel.innerHTML = '<div class="head"><span class="eyebrow"></span>'
    + '<button class="text">dismiss</button></div>';
  panel.querySelector('.eyebrow').textContent = title;
  panel.querySelector('.text').onclick = closeSuggest;
  return panel;
}
function closeSuggest() {
  $('suggest-wrap').hidden = true;
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
      b.className = cls; b.textContent = label; b.onclick = fn;
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
  b.onclick = () => { const xs = checked(); if (xs.length) fn(xs); };
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
// it hid stays hidden, but new entries start it fresh. People are
// pre-checked for that -- a deleted Allen shouldn't block every Allen.
async function showDeleted() {
  const panel = suggestPanel('deleted');
  const r = await (await fetch('/api/entities/deleted')).json();
  if (!r.names || (!r.names.length && !r.mentions.length)) { panelSay(panel, 'nothing deleted.'); return; }
  const restoreBtn = (row, key, mode) => {
    const b = document.createElement('button');
    b.className = 'quiet xs';
    b.textContent = 'restore';
    b.title = 'bring back everything this delete hid';
    b.onclick = async e => {
      e.preventDefault();
      if (await api('/api/entities/deleted/restore', {key, mode})) { row.remove(); panel.dispatchEvent(new Event('change')); loadEntities(); }
    };
    row.appendChild(b);
  };
  if (r.names.length) {
    panelHead(panel, `never tracked (${r.names.length})`);
    panelSay(panel, 'Checked names are let back in: their old mentions stay deleted, and a new entry that mentions one starts it fresh.');
    const rows = r.names.map(n => {
      const row = checkRow(panel, {
        checked: n.kind === 'person' && !n.generic, label: n.name,
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
  x.onclick = onRemove;
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
  $('entity-name').textContent = r.name;
  $('entity-meta').textContent = `${r.type} · ${r.observations.length} observation${r.observations.length === 1 ? '' : 's'}`
    + (info.mentions ? ` · ${info.mentions} mention${info.mentions === 1 ? '' : 's'}` : '');
  $('merge-target').value = '';
  $('retype-kind').value = '';

  // aka / in chips under the meta, and the editable rows in the edit box
  const chips = $('entity-chips');
  chips.innerHTML = '';
  const aliasEd = $('alias-chips');
  aliasEd.innerHTML = '';
  for (const a of (info.aliases || [])) {
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
  let lastDate = null;
  for (const o of r.observations) {
    if (o.date !== lastDate) {
      lastDate = o.date;
      const d = document.createElement('div');
      d.className = 'obs-date eyebrow';
      d.textContent = fmtDate(o.date) + (o.extracted_name !== r.name ? ` · as "${o.extracted_name}"` : '');
      docEl.appendChild(d);
    }
    const row = document.createElement('div');
    row.className = 'obs';
    const t = document.createElement('span');
    t.className = 't';
    t.textContent = o.text;
    const ops = document.createElement('span');
    ops.className = 'ops';
    const mk = (label, title, fn, cls) => {
      const b = document.createElement('button');
      b.textContent = label; b.title = title; b.onclick = fn;
      if (cls) b.className = cls;
      ops.appendChild(b);
    };
    mk('edit', 'edit this observation', async () => {
      const text = prompt('Edit observation:', o.text);
      if (text === null || text.trim() === o.text) return;
      if (await api('/api/observation', {...o, action: 'edit', text: text.trim()})) await reloadEntity(r.name);
    });
    mk('move', 'move this observation to another entity', async () => {
      const target = prompt('Move this observation to which entity?\n(prefix with person:/project:/place: if it\'s new)', '');
      if (!target) return;
      let kind = r.type, tname = target.trim();
      const m = tname.match(/^(person|project|place):(.+)$/);
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

export async function reloadEntity(name) {
  await loadEntities();
  if (state.entities[name]) await showEntity(name);
  else showNone();
}

// Back to the prompt. `msg`, when given, is what just happened to the entity
// that was open (merged, deleted, undone), said once on the list side.
function showNone() {
  state.selected = null;
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
  const res = await fetch(url, {method: 'POST'});
  const r = await res.json();
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

const pressed = (id, on) => {
  $(id).classList.toggle('on', on);
  $(id).setAttribute('aria-pressed', String(on));
};

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
  $('retire-btn').onclick = async () => {
    if (!state.selected) return;
    const retire = !state.entities[state.selected]?.retired;
    const r = await api('/api/entities/retire', {name: state.selected, retired: retire});
    if (r) await reloadEntity(r.name);
    if (r && retire) flash(`${r.name} retired. The retired chip shows them.`);
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
  $('entity-to-triage').onclick = () => showTab('triage');
  $('suggest-back').onclick = () => $('entities-pane').classList.remove('drilled');
  $('batch-add').onclick = batchAdd;
  $('batch-group').onkeydown = e => { if (e.key === 'Enter') batchAdd(); };
  $('batch-clear').onclick = () => { picked.clear(); updateBatchCount(); renderEntityList(); };
  $('search').oninput = renderEntityList;
  $('find-dups').onclick = findDups;
  $('find-generic').onclick = genericCleanup;
  $('show-deleted').onclick = showDeleted;

  $('merge-btn').onclick = async () => {
    const target = $('merge-target').value.trim();
    if (!state.selected || !target) return;
    if (!confirm(`Merge "${state.selected}" into "${target}"?\n("${state.selected}" stays as an alias)`)) return;
    const r = await api('/api/entities/merge', {source: state.selected, target});
    if (r) clearDetail(`merged into ${r.into}`);
  };

  $('correct-btn').onclick = async () => {
    const target = $('merge-target').value.trim();
    if (!state.selected || !target) return;
    if (!confirm(`Correct "${state.selected}" to "${target}"?\n(this fixes a typo, so "${state.selected}" is NOT kept as an alias)`)) return;
    const r = await api('/api/entities/correct', {source: state.selected, target});
    if (r) clearDetail(`corrected to ${r.into}`);
  };

  $('retype-kind').onchange = async () => {
    const kind = $('retype-kind').value;
    if (!state.selected || !kind) return;
    const newName = prompt(`Move "${state.selected}" to ${kind}s. Rename it? (leave as-is to keep the name)`, state.selected);
    if (newName === null) { $('retype-kind').value = ''; return; }
    const r = await api('/api/entities/retype', {name: state.selected, new_type: kind, new_name: newName.trim()});
    if (r) clearDetail(`moved to ${kind}s`);
  };

  $('alias-add-btn').onclick = async () => {
    const a = $('alias-new').value.trim();
    if (!state.selected || !a) return;
    if (await api('/api/entities/alias', {name: state.selected, add: a})) {
      $('alias-new').value = '';
      await reloadEntity(state.selected);
    }
  };

  $('rename-btn').onclick = async () => {
    if (!state.selected) return;
    const newName = prompt(`Rename "${state.selected}" to:`, state.selected);
    if (newName === null || !newName.trim() || newName.trim() === state.selected) return;
    const r = await api('/api/entities/rename', {source: state.selected, target: newName.trim()});
    if (r) { state.selected = r.to; await reloadEntity(r.to); }
  };

  $('delete-btn').onclick = async () => {
    if (!state.selected) return;
    if (!confirm(`Delete the mentions of "${state.selected}"?\n(your entries are untouched, and a new entry that mentions "${state.selected}" starts it fresh)`)) return;
    const r = await api('/api/entities/delete', {name: state.selected, mode: 'mentions'});
    if (r) clearDetail('deleted');
  };

  $('never-btn').onclick = async () => {
    if (!state.selected) return;
    if (!confirm(`Never track "${state.selected}"?\nEvery future "${state.selected}" will be ignored too. Meant for generic terms and junk.`)) return;
    const r = await api('/api/entities/delete', {name: state.selected, mode: 'name'});
    if (r) clearDetail(`no longer tracking ${r.deleted}`);
  };

  $('undo-btn').onclick = () => histStep('/api/undo');
  $('redo-btn').onclick = () => histStep('/api/redo');
  refreshHistoryButtons();
  setInterval(refreshHistoryButtons, 15000);

  // ---- type filter chips ----
  document.querySelectorAll('#type-chips [data-type]').forEach(b => b.onclick = () => {
    const kind = b.dataset.type;
    if (filters.types.has(kind)) filters.types.delete(kind); else filters.types.add(kind);
    b.classList.toggle('on', filters.types.has(kind));
    b.setAttribute('aria-pressed', String(filters.types.has(kind)));
    renderEntityList();
  });

  // ---- merge suggestions (ask claude, by type) ----
  $('suggest-kind').onchange = e => {
    const kind = e.target.value;
    e.target.value = '';               // reset to the placeholder for next time
    if (kind) askSuggest(kind);
  };
}

async function askSuggest(kind) {
  const panel = suggestPanel(`claude's suggestions · ${PLURAL[kind] || kind}`);
  const w = panelSay(panel, `asking claude for ${kind} merge suggestions…`);
  const r = await api('/api/entities/suggest', {kind});
  w.remove();
  if (!r) { closeSuggest(); return; }
  if (!r.groups.length) { panelSay(panel, 'no confident suggestions. Looks clean.'); return; }
  for (const g of r.groups) {
    const div = document.createElement('div');
    div.className = 'sg';
    div.innerHTML = `<span><strong>${g.members.map(esc).join(', ')}</strong> → ${esc(g.canonical)}</span>`;
    const ok = document.createElement('button');
    ok.className = 'quiet'; ok.textContent = 'apply';
    ok.onclick = async () => {
      for (const m of g.members) await api('/api/entities/merge', {source: m, target: g.canonical});
      div.remove();
      loadEntities();
    };
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
