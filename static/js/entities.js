// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, esc, fmtDate, refreshStatus } from './core.js';
import { state, filters } from './state.js';
import { loadGroups, groupSetDeep, groupPathLabel, rolledUpMemberSet, clearGroupSelection } from './groups.js';

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
  for (const kind of ['person', 'project', 'place']) {
    if (typeFilter && !typeFilter.has(kind)) continue;
    const items = groups[kind].sort(sortAlpha
      ? (a, b) => a[0].toLowerCase().localeCompare(b[0].toLowerCase())
      : (a, b) => b[1] - a[1]);
    if (!items.length) continue;
    const h = document.createElement('div');
    h.className = 'kind';
    h.textContent = `${kind}s (${items.length})`;
    wrap.appendChild(h);
    for (const [name, mentions, reviewed] of items) {
      const row = document.createElement('div');
      row.className = 'ent-row' + (name === state.selected ? ' sel' : '');

      if (selectMode) {
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.className = 'ent-check';
        cb.checked = picked.has(name);
        cb.title = 'select for “add to group”';
        cb.onclick = e => e.stopPropagation();
        cb.onchange = () => {
          if (cb.checked) picked.add(name); else picked.delete(name);
          updateBatchCount();
        };
        row.appendChild(cb);
      }

      const b = document.createElement('button');
      b.className = 'ent';
      b.innerHTML = `${esc(name)} <span class="n">${mentions}</span>`;
      b.onclick = () => showEntity(name);
      row.appendChild(b);

      const keep = document.createElement('button');
      keep.className = 'ent-keep' + (reviewed ? ' on' : '');
      keep.textContent = reviewed ? '✓' : 'keep';
      keep.title = reviewed ? 'reviewed; click to unmark' : 'mark reviewed';
      keep.onclick = async e => {
        e.stopPropagation();
        const next = !reviewed;
        if (await api('/api/entities/reviewed', {name, reviewed: next})) {
          state.entities[name].reviewed = next;
          renderEntityList();
        }
      };
      row.appendChild(keep);

      wrap.appendChild(row);
    }
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
async function findDups() {
  const panel = $('suggest-panel');
  panel.style.display = 'block';
  panel.textContent = 'scanning for likely duplicates (local, free)…';
  const r = await (await fetch('/api/entities/duplicates')).json();
  panel.innerHTML = '';
  if (!r.pairs || !r.pairs.length) { panel.textContent = 'no likely duplicates found.'; return; }
  for (const p of r.pairs) {
    const row = document.createElement('div');
    row.className = 'dup-row';
    row.innerHTML = `<strong>${esc(p.a)}</strong> (${p.a_mentions}) ↔ <strong>${esc(p.b)}</strong> (${p.b_mentions})
      <span class="why">${esc(p.kind)} · ${esc(p.basis)} ${p.score}</span>`;
    const mk = (label, fn) => {
      const b = document.createElement('button');
      b.className = 'quiet'; b.textContent = label; b.onclick = fn;
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
    });
    panel.appendChild(row);
  }
}

export async function showEntity(name) {
  state.selected = name;
  clearGroupSelection();   // right pane now shows an entity, not a group
  renderEntityList();
  const r = await (await fetch('/api/entities/observations?name=' + encodeURIComponent(name))).json();
  if (r.error) { alert(r.error); return; }
  state.selected = r.name;
  const info = state.entities[r.name] || {};
  $('entity-name').textContent = r.name;
  $('entity-meta').textContent = `${r.type} · ${r.observations.length} observations`;
  $('entity-actions').style.display = 'flex';
  $('merge-target').value = '';
  $('retype-kind').value = '';

  // aliases
  $('alias-row').style.display = 'flex';
  const chips = $('alias-chips');
  chips.innerHTML = '';
  for (const a of (info.aliases || [])) {
    const c = document.createElement('span');
    c.className = 'chip';
    c.textContent = a + ' ';
    const x = document.createElement('button');
    x.textContent = '×';
    x.title = 'remove alias';
    x.onclick = async () => {
      if (await api('/api/entities/alias', {name: r.name, remove: a})) await reloadEntity(r.name);
    };
    c.appendChild(x);
    chips.appendChild(c);
  }

  // groups this entity belongs to
  $('group-row').style.display = 'flex';
  $('group-add-input').value = '';
  const gchips = $('group-chips');
  gchips.innerHTML = '';
  for (const g of (info.groups || [])) {
    const c = document.createElement('span');
    c.className = 'chip';
    c.textContent = groupPathLabel(g) + ' ';
    const x = document.createElement('button');
    x.textContent = '×';
    x.title = 'remove from this group';
    x.onclick = async () => {
      if (await api('/api/groups/member', {group: g, entity: r.name, remove: true})) await reloadEntity(r.name);
    };
    c.appendChild(x);
    gchips.appendChild(c);
  }

  // observations grouped by date, each editable
  const docEl = $('entity-doc');
  docEl.innerHTML = '';
  let lastDate = null;
  for (const o of r.observations) {
    if (o.date !== lastDate) {
      lastDate = o.date;
      const d = document.createElement('div');
      d.className = 'obs-date';
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
    const mk = (label, title, fn) => {
      const b = document.createElement('button');
      b.textContent = label; b.title = title; b.onclick = fn;
      ops.appendChild(b);
    };
    mk('edit', 'edit this observation', async () => {
      const text = prompt('Edit observation:', o.text);
      if (text === null || text.trim() === o.text) return;
      if (await api('/api/observation', {...o, action: 'edit', text: text.trim()})) await reloadEntity(r.name);
    });
    mk('move', 'reassign to a different entity', async () => {
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
    });
    row.appendChild(t);
    row.appendChild(ops);
    docEl.appendChild(row);
  }
}

export async function reloadEntity(name) {
  await loadEntities();
  if (state.entities[name]) await showEntity(name);
  else {
    $('entity-name').textContent = 'select an entity';
    $('entity-meta').textContent = '';
    $('entity-doc').innerHTML = '';
    $('entity-actions').style.display = 'none';
    $('alias-row').style.display = 'none';
    $('group-row').style.display = 'none';
  }
}

function clearDetail(msg) {
  state.selected = null;
  $('entity-name').textContent = msg;
  $('entity-meta').textContent = '';
  $('entity-doc').innerHTML = '';
  $('entity-actions').style.display = 'none';
  $('alias-row').style.display = 'none';
  $('group-row').style.display = 'none';
  loadEntities();
}

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
  $('undo-btn').style.opacity = h.undo ? 1 : .4;
  $('redo-btn').style.opacity = h.redo ? 1 : .4;
}

export function init() {
  $('flt-unreviewed').onclick = () => {
    filters.unreviewed = !filters.unreviewed;
    $('flt-unreviewed').classList.toggle('on', filters.unreviewed);
    renderEntityList();
  };
  $('flt-single').onclick = () => {
    filters.single = !filters.single;
    $('flt-single').classList.toggle('on', filters.single);
    renderEntityList();
  };
  $('sort-az').onclick = () => {
    sortAlpha = !sortAlpha;
    $('sort-az').classList.toggle('on', sortAlpha);
    renderEntityList();
  };
  $('flt-select').onclick = () => {
    selectMode = !selectMode;
    $('flt-select').classList.toggle('on', selectMode);
    $('batch-bar').style.display = selectMode ? 'flex' : 'none';
    if (!selectMode) picked.clear();
    updateBatchCount();
    renderEntityList();
  };
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
    if (!confirm(`Correct "${state.selected}" to "${target}"?\n(typo fix; "${state.selected}" is NOT kept as an alias)`)) return;
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
  const panel = $('suggest-panel');
  panel.style.display = 'block';
  panel.textContent = `asking claude for ${kind} merge suggestions…`;
  const r = await api('/api/entities/suggest', {kind});
  if (!r) { panel.style.display = 'none'; return; }
  panel.innerHTML = '';
  if (!r.groups.length) { panel.textContent = 'no confident suggestions; looks clean.'; return; }
  for (const g of r.groups) {
    const div = document.createElement('div');
    div.className = 'sg';
    div.innerHTML = `<strong>${g.members.map(esc).join(', ')}</strong> → ${esc(g.canonical)}<div class="r">${esc(g.reason)}</div>`;
    const ok = document.createElement('button');
    ok.className = 'quiet'; ok.textContent = 'apply';
    ok.onclick = async () => {
      for (const m of g.members) await api('/api/entities/merge', {source: m, target: g.canonical});
      div.remove();
      loadEntities();
    };
    const no = document.createElement('button');
    no.className = 'quiet'; no.textContent = 'dismiss';
    no.onclick = () => div.remove();
    div.appendChild(ok);
    div.appendChild(document.createTextNode(' '));
    div.appendChild(no);
    panel.appendChild(div);
  }
}
