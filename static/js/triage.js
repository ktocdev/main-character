// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, fmtDate } from './core.js';
import { state } from './state.js';
import { loadEntities } from './entities.js';

// ---- triage mode ----
let queue = [], qpos = 0, triageMode = null;

export async function startTriage() {
  await loadEntities();
  queue = Object.entries(state.entities)
    .filter(([, i]) => !i.reviewed)
    .sort((a, b) => a[1].mentions - b[1].mentions)  // junk (1-mention) first
    .map(([n]) => n);
  qpos = 0;
  renderTriage();
}

function toast(msg) {
  $('triage-toast').textContent = msg;
  setTimeout(() => { if ($('triage-toast').textContent === msg) $('triage-toast').textContent = ''; }, 3500);
}

async function renderTriage() {
  triageMode = null;
  $('triage-input-row').style.display = 'none';
  $('triage-kind-row').style.display = 'none';
  const total = Object.keys(state.entities).length;
  const reviewed = Object.values(state.entities).filter(i => i.reviewed).length;
  if (qpos >= queue.length) {
    $('triage-progress').textContent = `${reviewed} of ${total} reviewed`;
    $('triage-name').textContent = queue.length ? 'queue done 🎉' : 'everything is reviewed';
    $('triage-meta').textContent = '';
    $('triage-aliases').textContent = '';
    $('triage-obs').innerHTML = '';
    return;
  }
  const name = queue[qpos];
  const info = state.entities[name];
  if (!info) { qpos++; return renderTriage(); }
  $('triage-progress').textContent =
    `${reviewed} of ${total} reviewed · ${queue.length - qpos} left in this queue`;
  $('triage-name').textContent = name;
  $('triage-meta').textContent = `${info.type} · ${info.mentions} mention${info.mentions === 1 ? '' : 's'}`;
  $('triage-aliases').textContent = (info.aliases || []).length ? 'aka ' + info.aliases.join(', ') : '';
  const obsEl = $('triage-obs');
  obsEl.innerHTML = '<span class="thinking">loading…</span>';
  const r = await (await fetch('/api/entities/observations?name=' + encodeURIComponent(name))).json();
  if (queue[qpos] !== name) return;  // user already moved on
  obsEl.innerHTML = '';
  let lastDate = null;
  for (const o of (r.observations || []).slice(0, 12)) {
    if (o.date !== lastDate) {
      lastDate = o.date;
      const d = document.createElement('div');
      d.className = 'obs-date';
      d.textContent = fmtDate(o.date);
      obsEl.appendChild(d);
    }
    const p = document.createElement('div');
    p.textContent = '– ' + o.text;
    obsEl.appendChild(p);
  }
  if ((r.observations || []).length > 12) {
    const more = document.createElement('div');
    more.className = 'thinking';
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
    triageAdvance();
  }
}

const TRIAGE_PROMPTS = {
  merge:  n => `merge ${n} into (type a target entity):`,
  correct: n => `correct ${n} to (type the right name):`,
  rename: n => `rename ${n} to:`,
  alias:  n => `add an alias to ${n} (another name it goes by):`,
};

function triagePrompt(mode) {
  triageMode = mode;
  $('triage-mode-label').textContent = TRIAGE_PROMPTS[mode](queue[qpos]);
  $('triage-input-row').style.display = 'flex';
  $('triage-input').value = '';
  $('triage-input').focus();
}

function triageCancel() {
  triageMode = null;
  $('triage-input-row').style.display = 'none';
}

async function triageApply() {
  const target = $('triage-input').value.trim();
  if (!target) { $('triage-input').focus(); return; }
  const name = queue[qpos], mode = triageMode;
  triageMode = null;
  $('triage-input-row').style.display = 'none';
  if (mode === 'merge' || mode === 'correct') {
    await triageAct(
      n => api(`/api/entities/${mode}`, {source: n, target}),
      r => `${mode === 'merge' ? 'merged' : 'corrected'} ${name} → ${r.into}`,
    );
  } else if (mode === 'rename') {
    const r = await api('/api/entities/rename', {source: name, target});
    if (r) {
      toast(`renamed → ${r.to}`);
      await loadEntities();
      queue[qpos] = r.to;
      renderTriage();
    }
  } else if (mode === 'alias') {
    const r = await api('/api/entities/alias', {name, add: target});
    if (r) {
      toast(`“${target}” is now an alias of ${name}`);
      await loadEntities();
      renderTriage();  // stays on this entity; alias shown in its aka line
    }
  }
}

async function triageKey(key) {
  if (triageMode !== null) return;
  if (qpos >= queue.length && key !== 'u') return;
  const name = queue[qpos];
  switch (key) {
    case 'k':
      if (await api('/api/entities/reviewed', {name, reviewed: true})) {
        state.entities[name].reviewed = true;
        toast(`kept ${name} ✓`);
        triageAdvance();
      }
      break;
    case 's': triageAdvance(); break;
    case 'd':
      await triageAct(n => api('/api/entities/delete', {name: n}), r => `deleted ${r.deleted}`);
      break;
    case 'm': triagePrompt('merge'); break;
    case 'c': triagePrompt('correct'); break;
    case 'r': triagePrompt('rename'); break;
    case 'a': triagePrompt('alias'); break;
    case 't': $('triage-kind-row').style.display = 'flex'; break;
    case '1': case '2': case '3': {
      const row = $('triage-kind-row');
      if (getComputedStyle(row).display !== 'none') {
        row.querySelectorAll('button')[Number(key) - 1].click();
      }
      break;
    }
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

export function init() {
  $('triage-apply').onclick = triageApply;
  $('triage-cancel').onclick = triageCancel;
  $('triage-input').addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); triageCancel(); }
    else if (e.key === 'Enter') { e.preventDefault(); triageApply(); }
  });

  document.querySelectorAll('#triage-kind-row button').forEach(b => b.onclick = async () => {
    $('triage-kind-row').style.display = 'none';
    await triageAct(
      n => api('/api/entities/retype', {name: n, new_type: b.dataset.kind, new_name: ''}),
      r => `moved to ${r.to}s`,
    );
  });

  document.addEventListener('keydown', e => {
    if (state.activeTab !== 'triage' || triageMode !== null) return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (['m', 'c', 'r', 'a'].includes(e.key)) e.preventDefault();
    triageKey(e.key);
  });

  document.querySelectorAll('#triage-btns button').forEach(b => {
    b.onclick = () => triageKey(b.dataset.tkey);
  });
}
