// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, esc, fmtDate, refreshStatus } from './core.js';
import { state, filters } from './state.js';
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
  for (const [name, info] of Object.entries(state.entities)) {
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

// The detail pane shows one of: an entity, a group page, or the prompt to
// pick one. The edit box and its toggle only exist for an entity.
function showDetail(kind) {
  $('entity-detail-col').hidden = kind === 'none';
  $('entity-none').hidden = kind !== 'none';
  $('entity-edit-toggle').hidden = kind !== 'entity';
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
  $('suggest-back').onclick = () => $('entities-pane').classList.remove('drilled');
  $('batch-add').onclick = batchAdd;
  $('batch-group').onkeydown = e => { if (e.key === 'Enter') batchAdd(); };
  $('batch-clear').onclick = () => { picked.clear(); updateBatchCount(); renderEntityList(); };
  $('search').oninput = renderEntityList;
  $('find-dups').onclick = findDups;

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
    if (!confirm(`Delete "${state.selected}" from the entity graph?`)) return;
    const r = await api('/api/entities/delete', {name: state.selected});
    if (r) clearDetail('deleted');
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
