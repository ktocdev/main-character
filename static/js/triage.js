// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, fmtDate } from './core.js';
import { state } from './state.js';
import { loadEntities } from './entities.js';
import { showTab } from './main.js';

// ---- triage mode ----
// Entities come one at a time, junk first (1-mention entities lead). The
// keyboard does the work: k m c r a t d s u, then 1/2/3 in retype mode,
// Enter and Esc in a field. The buttons mirror the keys.
let queue = [], qpos = 0, triageMode = null;
let skipped = [];        // names skipped in this pass, for the queue-empty offer
let toastTimer = null;

export async function startTriage(only) {
  await loadEntities();
  queue = Object.entries(state.entities)
    .filter(([n, i]) => !i.reviewed && (!only || only.includes(n)))
    .sort((a, b) => a[1].mentions - b[1].mentions)  // junk (1-mention) first
    .map(([n]) => n);
  qpos = 0;
  if (!only) skipped = [];
  refreshUndo();
  renderTriage();
}

function toast(msg) {
  $('triage-toast').textContent = msg;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('triage-toast').textContent = ''; }, 2600);
}

// undo dims until there is something to undo (the stack lives server-side)
async function refreshUndo() {
  try {
    const h = await (await fetch('/api/history')).json();
    $('triage-undo').classList.toggle('empty', !h.undo);
    $('triage-undo-last').hidden = !h.undo;
  } catch (e) { }
}

function progress(reviewed, total, left) {
  $('triage-progress').innerHTML = '<div class="line"><span class="count"></span><span class="note"></span></div>'
    + '<div class="hairline"><i></i></div>';
  $('triage-progress').querySelector('.count').textContent =
    `${reviewed} of ${total} reviewed` + (left ? ` · ${left} left in this queue` : '');
  $('triage-progress').querySelector('.note').textContent = left ? 'junk first — 1-mention entities lead' : '';
  $('triage-progress').querySelector('i').style.width = total ? `${Math.round(reviewed / total * 100)}%` : '0%';
}

async function renderTriage() {
  triageMode = null;
  $('triage-input-row').hidden = true;
  $('triage-kind-row').hidden = true;
  $('triage-suggest').innerHTML = '';
  const total = Object.keys(state.entities).length;
  const reviewed = Object.values(state.entities).filter(i => i.reviewed).length;
  if (qpos >= queue.length) {
    progress(reviewed, total, 0);
    $('triage-card').hidden = true;
    $('triage-btns').hidden = true;
    $('triage-foot').hidden = true;
    const done = $('triage-done');
    done.hidden = false;
    done.querySelector('p').textContent = !total
      ? 'Nothing waiting. New entities arrive unreviewed each time a chapter is closed; an occasional minute here keeps the graph clean.'
      : queue.length
        ? `Every entity in this pass is reviewed${skipped.length ? `, except the ${skipped.length} you skipped` : ''}. The next closed chapter refills the queue.`
        : 'Everything is reviewed. New entities arrive unreviewed each time a chapter is closed; an occasional minute here keeps the graph clean.';
    const sk = $('triage-skipped');
    sk.hidden = !skipped.length;
    sk.textContent = `go through the ${skipped.length} skipped`;
    return;
  }
  $('triage-card').hidden = false;
  $('triage-btns').hidden = false;
  $('triage-foot').hidden = false;
  $('triage-done').hidden = true;
  const name = queue[qpos];
  const info = state.entities[name];
  if (!info) { qpos++; return renderTriage(); }
  progress(reviewed, total, queue.length - qpos);
  $('triage-name').textContent = name;
  $('triage-meta').textContent = `${info.type} · ${info.mentions} mention${info.mentions === 1 ? '' : 's'}`;
  $('triage-aliases').textContent = (info.aliases || []).length ? 'also ' + info.aliases.join(' · ') : '';
  const obsEl = $('triage-obs');
  obsEl.innerHTML = '<span class="more">reading <span class="dots">···</span></span>';
  const r = await (await fetch('/api/entities/observations?name=' + encodeURIComponent(name))).json();
  if (queue[qpos] !== name) return;  // user already moved on
  obsEl.innerHTML = '';
  let group = null, lastDate = null;
  for (const o of (r.observations || []).slice(0, 12)) {
    if (o.date !== lastDate) {
      lastDate = o.date;
      group = document.createElement('div');
      const d = document.createElement('div');
      d.className = 'eyebrow';
      d.textContent = fmtDate(o.date);
      group.appendChild(d);
      obsEl.appendChild(group);
    }
    const p = document.createElement('div');
    p.className = 'line';
    p.innerHTML = '<span class="dash">–</span><span></span>';
    p.lastChild.textContent = o.text;
    group.appendChild(p);
  }
  if ((r.observations || []).length > 12) {
    const more = document.createElement('div');
    more.className = 'more';
    more.textContent = `… and ${r.observations.length - 12} more`;
    obsEl.appendChild(more);
  }
}

function triageAdvance() { qpos++; renderTriage(); }

async function triageAct(fn, successMsg) {
  const name = queue[qpos];
  const r = await fn(name);
  if (r) {
    toast(successMsg(r));
    await loadEntities();
    queue = queue.filter((n, i) => i <= qpos || state.entities[n]);  // drop vanished
    refreshUndo();
    triageAdvance();
  }
}

const TRIAGE_LABELS = {
  merge: 'merge into', correct: 'correct to', rename: 'rename to', alias: 'also known as',
};
const TRIAGE_PLACEHOLDERS = {
  merge: 'existing entity…', correct: 'the right name…', rename: 'new name…', alias: 'another name…',
};

function triagePrompt(mode) {
  triageMode = mode;
  $('triage-mode-label').textContent = TRIAGE_LABELS[mode];
  $('triage-input-row').hidden = false;
  $('triage-kind-row').hidden = true;
  const inp = $('triage-input');
  inp.placeholder = TRIAGE_PLACEHOLDERS[mode];
  inp.value = mode === 'rename' ? queue[qpos] : '';
  inp.focus();
  if (mode === 'rename') inp.select();
  suggest();
}

// merge / correct: known entities as pills under the field, filtered by
// what has been typed (the prototype's autocomplete, not a <datalist>)
function suggest() {
  const box = $('triage-suggest');
  box.innerHTML = '';
  if (triageMode !== 'merge' && triageMode !== 'correct') return;
  const q = $('triage-input').value.trim().toLowerCase();
  if (!q) return;
  const cur = queue[qpos];
  const names = Object.keys(state.entities)
    .filter(n => n !== cur && n.toLowerCase().includes(q))
    .sort((a, b) => a.toLowerCase().indexOf(q) - b.toLowerCase().indexOf(q) || a.localeCompare(b))
    .slice(0, 6);
  for (const n of names) {
    const b = document.createElement('button');
    b.className = 'chip sm';
    b.textContent = n;
    b.onclick = () => { $('triage-input').value = n; $('triage-input').focus(); suggest(); };
    box.appendChild(b);
  }
}

function triageCancel() {
  triageMode = null;
  $('triage-input-row').hidden = true;
  $('triage-kind-row').hidden = true;
  $('triage-suggest').innerHTML = '';
  $('triage').focus();
}

async function triageApply() {
  const target = $('triage-input').value.trim();
  if (!target) { $('triage-input').focus(); return; }
  const name = queue[qpos], mode = triageMode;
  triageMode = null;
  $('triage-input-row').hidden = true;
  $('triage-suggest').innerHTML = '';
  if (mode === 'merge' || mode === 'correct') {
    await triageAct(
      n => api(`/api/entities/${mode}`, {source: n, target}),
      r => mode === 'merge' ? `${name} merged into ${r.into}, kept as an alias.` : `${name} corrected to ${r.into}. Old name not kept.`,
    );
  } else if (mode === 'rename') {
    const r = await api('/api/entities/rename', {source: name, target});
    if (r) {
      toast(`renamed to ${r.to}.`);
      await loadEntities();
      queue[qpos] = r.to;
      refreshUndo();
      renderTriage();
    }
  } else if (mode === 'alias') {
    const r = await api('/api/entities/alias', {name, add: target});
    if (r) {
      toast(`${target} added as an alias.`);
      await loadEntities();
      refreshUndo();
      renderTriage();  // stays on this entity; alias shown in its also line
    }
  }
  $('triage').focus();
}

async function triageKey(key) {
  if (triageMode === 'kind') {
    if (key === 'Escape') { triageCancel(); return; }
    const k = {1: 'person', 2: 'project', 3: 'place'}[key];
    if (k) retype(k);
    return;
  }
  if (triageMode !== null) return;
  if (qpos >= queue.length && key !== 'u') return;
  const name = queue[qpos];
  switch (key) {
    case 'k':
      if (await api('/api/entities/reviewed', {name, reviewed: true})) {
        state.entities[name].reviewed = true;
        toast(`${name} kept ✓`);
        refreshUndo();
        triageAdvance();
      }
      break;
    case 's':
      skipped.push(name);
      toast('skipped. Back in the queue next time.');
      triageAdvance();
      break;
    case 'd':
      await triageAct(n => api('/api/entities/delete', {name: n}), r => `deleted ${r.deleted}. u brings it back.`);
      break;
    case 'm': triagePrompt('merge'); break;
    case 'c': triagePrompt('correct'); break;
    case 'r': triagePrompt('rename'); break;
    case 'a': triagePrompt('alias'); break;
    case 't':
      triageMode = 'kind';
      $('triage-kind-row').hidden = false;
      break;
    case 'u': {
      const res = await fetch('/api/undo', {method: 'POST'});
      const r = await res.json();
      if (r.error) { toast(r.error); break; }
      toast(`undid: ${r.undid}`);
      await startTriage();
      break;
    }
  }
}

async function retype(kind) {
  triageMode = null;
  $('triage-kind-row').hidden = true;
  await triageAct(
    n => api('/api/entities/retype', {name: n, new_type: kind, new_name: ''}),
    r => `${r.to || queue[qpos]} is now a ${kind}.`,
  );
}

export function init() {
  $('triage-apply').onclick = triageApply;
  $('triage-cancel').onclick = triageCancel;
  $('triage-kind-cancel').onclick = triageCancel;
  $('triage-input').addEventListener('input', suggest);
  $('triage-input').addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); triageCancel(); }
    else if (e.key === 'Enter') { e.preventDefault(); triageApply(); }
    e.stopPropagation();
  });

  document.querySelectorAll('#triage-kind-row button[data-kind]').forEach(b => b.onclick = () => retype(b.dataset.kind));

  document.addEventListener('keydown', e => {
    if (state.activeTab !== 'triage') return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (['m', 'c', 'r', 'a', 'k', 'd', 's', 't', 'u', '1', '2', '3'].includes(e.key)) e.preventDefault();
    triageKey(e.key);
  });

  document.querySelectorAll('#triage-btns button').forEach(b => {
    b.onclick = () => triageKey(b.dataset.tkey);
  });
  $('triage-skipped').onclick = () => startTriage(skipped.slice());
  $('triage-open-entities').onclick = () => showTab('entities');
  $('triage-undo-last').onclick = () => triageKey('u');
}
