// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, fmtDate } from './core.js';
import { state } from './state.js';
import { loadEntities, showEntity, mixupText, setName, variantsOf, NO_PARTS, HAS_RELATIONSHIP } from './entities.js';
import { showTab } from './main.js';

// ---- triage mode ----
// Entities come one at a time, junk first (1-mention entities lead). The
// keyboard does the work: k m c r a p t d D x s u e, then 1-4 in retype mode
// (and 1-6 for a thing's category), Enter and Esc in a field. The buttons
// mirror the keys. On a card flagged as maybe two people, k asks once.
// Unsorted mentions of a split name ("Dev · ?") come one entry at a time:
// 1-9 say which Dev, n names someone new.
let queue = [], qpos = 0, triageMode = null;
let skipped = [];        // names skipped in this pass, for the queue-empty offer
let toastTimer = null;
let editing = false;     // left for Entities with e; the next start is a return
let sortChoices = [], sortPicks = [];  // an unsorted card: the qualifiers, this entry's observations

export async function startTriage(only) {
  await loadEntities();
  // Coming back from the edit link keeps the pass going: what was skipped
  // stays skipped (and out of the way), so the queue, sorted the same way,
  // lands on the entity that was open -- or its successor if it was
  // reviewed or deleted over there.
  const returning = editing && !only;
  editing = false;
  queue = Object.entries(state.entities)
    // unsorted mentions wait here even under a reviewed name
    .filter(([n, i]) => (!i.reviewed || i.unsorted) && !i.retired && (!only || only.includes(n)) && !(returning && skipped.includes(n)))
    .sort((a, b) => a[1].mentions - b[1].mentions)  // junk (1-mention) first
    .map(([n]) => n);
  qpos = 0;
  if (!only && !returning) skipped = [];
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
  } catch { }
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
  $('triage-cat-row').hidden = true;
  $('triage-mixed-row').hidden = true;
  $('triage-never-row').hidden = true;
  $('triage-sort-row').hidden = true;
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
  setName($('triage-name'), name, info);
  const meta = `${info.type}${info.category ? ` · ${info.category}` : ''} · ${info.mentions} mention${info.mentions === 1 ? '' : 's'}`;
  $('triage-meta').textContent = meta;
  // the plain name a split person shares with the others isn't news
  const aka = (info.aliases || []).filter(a => a !== info.variant_of);
  $('triage-aliases').textContent = aka.length ? 'also ' + aka.join(' · ') : '';
  // a category name ("dive bar") gets d as never-track, said up front so
  // the key needs no confirming
  $('triage-generic').textContent = info.generic
    ? `looks generic: d stops tracking the name, so a future "${name}" is ignored too`
    : '';
  $('triage-btns').querySelector('[data-tkey="d"]').classList.toggle('suggested', !!info.generic);
  // entries that disagree about who this is: the dated eyebrows below
  // ("Mar 4 · as coworker") show which mentions are which
  $('triage-mixup').textContent = info.mixup ? `possibly two people: ${mixupText(info.mixup)}` : '';
  const obsEl = $('triage-obs');
  obsEl.innerHTML = '<span class="more">reading <span class="dots">···</span></span>';
  if (info.unsorted) {
    triageMode = 'sort';
    sortChoices = variantsOf(info.variant_of);
  }
  // on a retired person's later mentions, x is un-retire (in the sort row)
  $('triage-btns').querySelector('[data-tkey="x"]').disabled = !!info.closed;
  const r = await (await fetch('/api/entities/observations?name=' + encodeURIComponent(name))).json();
  if (queue[qpos] !== name) return;  // user already moved on
  let obs = r.observations || [];
  if (info.unsorted) {
    // one entry at a time: its observations, then which one it was
    const first = obs[0] ? `${obs[0].file}|${obs[0].ent_index}` : '';
    const left = info.mentions;
    obs = obs.filter(o => `${o.file}|${o.ent_index}` === first);
    sortPicks = obs.map(o => ({file: o.file, group: o.group, ent_index: o.ent_index, obs_index: o.obs_index}));
    $('triage-meta').textContent = `${left} mention${left === 1 ? '' : 's'} to sort`;
    const head = info.variant_of;
    $('triage-sort-q').textContent = info.closed
      ? `${head} is retired. Is this the same ${head}?`
      : `Which ${head} is this?`;
    const btns = $('triage-sort-btns');
    btns.innerHTML = '';
    if (info.closed) {
      // a past chapter's name in a later entry: them after all, someone
      // new, or not a past chapter any more
      const kb = document.createElement('button');
      kb.className = 'quiet';
      kb.innerHTML = '<span class="key">1</span>';
      kb.append(`the retired ${head}`);
      kb.onclick = keepRetired;
      const xb = document.createElement('button');
      xb.className = 'quiet';
      xb.innerHTML = '<span class="key">x</span>un-retire';
      xb.title = `${head} is back in the present: every later mention goes to them`;
      xb.onclick = unretireHead;
      btns.append(kb, xb);
    }
    sortChoices.slice(0, 9).forEach((q, i) => {
      const b = document.createElement('button');
      b.className = 'quiet';
      b.innerHTML = `<span class="key">${i + 1}</span>`;
      b.append(`${info.variant_of} · ${q}`);
      b.onclick = () => sortTo(q);
      btns.appendChild(b);
    });
    const nb = document.createElement('button');
    nb.className = 'quiet';
    nb.innerHTML = '<span class="key">n</span>someone new';
    nb.onclick = () => triagePrompt('qualify');
    btns.appendChild(nb);
    $('triage-sort-row').hidden = false;
  }
  if (!info.unsorted) $('triage-meta').textContent = `${meta} · ${obs.length} observation${obs.length === 1 ? '' : 's'}`;
  obsEl.innerHTML = '';
  obsEl.scrollTop = 0;
  // Every observation, grouped by date, each date tagged with what that
  // entry called it ("Mar 4 · as coworker"). The tail is where a second
  // person under the same name tends to turn up, so none of it is cut.
  let group = null, lastKey = null;
  for (const o of obs) {
    const key = `${o.date}|${o.attr || ''}`;
    if (key !== lastKey) {
      lastKey = key;
      group = document.createElement('div');
      const d = document.createElement('div');
      d.className = 'eyebrow';
      d.textContent = fmtDate(o.date) + (o.attr ? ` · ${HAS_RELATIONSHIP.has(info.type) ? 'as ' : ''}${o.attr}` : '');
      group.appendChild(d);
      obsEl.appendChild(group);
    }
    const p = document.createElement('div');
    p.className = 'line';
    p.innerHTML = '<span class="dash">–</span><span></span>';
    p.lastChild.textContent = o.text;
    group.appendChild(p);
  }
}

// e: open this entity in Entities for a deeper edit. Its detail pane offers
// the way back, and the pass (skips included) picks up where it was.
function editInEntities(name) {
  editing = true;
  state.triageReturn = true;
  showTab('entities');
  showEntity(name);
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
  part: 'part of', qualify: 'someone new:',
};
const TRIAGE_PLACEHOLDERS = {
  merge: 'existing entity…', correct: 'the right name…', rename: 'new name…', alias: 'another name…',
  part: 'what it belongs to…', qualify: 'a word that tells them apart…',
};

function triagePrompt(mode) {
  triageMode = mode;
  $('triage-mode-label').textContent = mode === 'qualify'
    ? `someone new: ${state.entities[queue[qpos]]?.variant_of} ·` : TRIAGE_LABELS[mode];
  $('triage-input-row').hidden = false;
  $('triage-kind-row').hidden = true;
  // a merge of a non-person may really be a part: offered beside apply
  $('triage-as-part').hidden = mode !== 'merge' || NO_PARTS.has(state.entities[queue[qpos]]?.type);
  const inp = $('triage-input');
  inp.placeholder = TRIAGE_PLACEHOLDERS[mode];
  inp.value = mode === 'rename' ? (state.entities[queue[qpos]]?.base || queue[qpos]) : '';
  inp.focus();
  if (mode === 'rename') inp.select();
  suggest();
}

// merge / correct: known entities as pills under the field, filtered by
// what has been typed (the prototype's autocomplete, not a <datalist>)
function suggest() {
  const box = $('triage-suggest');
  box.innerHTML = '';
  if (!['merge', 'correct', 'part'].includes(triageMode)) return;
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
  $('triage-cat-row').hidden = true;
  $('triage-mixed-row').hidden = true;
  $('triage-never-row').hidden = true;
  $('triage-suggest').innerHTML = '';
  // a sorting card goes back to asking which one
  if (state.entities[queue[qpos]]?.unsorted) triageMode = 'sort';
  $('triage').focus();
}

// k on a flagged card: one more press says it's one person, and the flag
// stays off for good; e goes to Entities to move one person's mentions out
function askMixed(name) {
  triageMode = 'mixed';
  $('triage-mixed-q').textContent = `Keep "${name}" as one person?`;
  $('triage-mixed-row').hidden = false;
}
async function keep(name, oneConfirmed = false) {
  triageMode = null;
  $('triage-mixed-row').hidden = true;
  if (oneConfirmed && !await api('/api/entities/not-mixed', {name, not_mixed: true})) return;
  if (await api('/api/entities/reviewed', {name, reviewed: true})) {
    state.entities[name].reviewed = true;
    if (oneConfirmed) delete state.entities[name].mixup;
    toast(oneConfirmed ? `${name} kept as one person ✓` : `${name} kept ✓`);
    refreshUndo();
    triageAdvance();
  }
}

// Two kinds of delete, neither touching the entries themselves. d drops the
// mentions there are now and leaves the name free (a new Allen starts
// fresh); for a generic name it stops tracking the name instead. D always
// stops tracking, after one confirming press.
function deleteMentions(name) {
  const generic = state.entities[name]?.generic;
  return triageAct(
    n => api('/api/entities/delete', {name: n, mode: generic ? 'name' : 'mentions'}),
    r => generic ? `no longer tracking ${r.deleted}. u brings it back.` : `deleted ${r.deleted}. The name stays free; u brings it back.`,
  );
}
function askNeverTrack(name) {
  triageMode = 'never';
  $('triage-never-q').textContent = `Never track "${name}"? Every future "${name}" will be ignored too.`;
  $('triage-never-row').hidden = false;
}
function neverTrack() {
  triageMode = null;
  $('triage-never-row').hidden = true;
  return triageAct(
    n => api('/api/entities/delete', {name: n, mode: 'name'}),
    r => `no longer tracking ${r.deleted}. u brings it back.`,
  );
}

async function triageApply(asPart = false) {
  const target = $('triage-input').value.trim();
  if (!target) { $('triage-input').focus(); return; }
  const name = queue[qpos], mode = asPart === true ? 'part' : triageMode;
  triageMode = null;
  $('triage-input-row').hidden = true;
  $('triage-suggest').innerHTML = '';
  if (mode === 'merge' || mode === 'correct') {
    await triageAct(
      n => api(`/api/entities/${mode}`, {source: n, target}),
      r => mode === 'merge' ? `${name} merged into ${r.into}, kept as an alias.` : `${name} corrected to ${r.into}. Old name not kept.`,
    );
  } else if (mode === 'qualify') {
    await sortTo(target);
  } else if (mode === 'part') {
    // its own entity under the parent: the card stays, renamed to its path
    const r = await api('/api/entities/part-of', {name, parent: target});
    if (r) {
      toast(`${state.entities[name]?.base || name} is now part of ${target}, kept separate from it.`);
      await loadEntities();
      queue[qpos] = r.name;
      refreshUndo();
      renderTriage();
    }
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

// an unsorted entry goes to one of the people with this name (or a new
// one); the card stays until the name has nothing left to sort
async function sortTo(qualifier) {
  const name = queue[qpos];
  const closed = state.entities[name]?.closed;
  const r = await api('/api/entities/split', {name, picks: sortPicks, qualifier});
  if (!r) return;
  // someone new beside a retired person: the retired one gets a word too
  toast(closed && r.rest
    ? `sorted to ${r.to}. The retired one is now ${r.rest}; rename it in entities.`
    : `sorted to ${r.to}.`);
  await sortedOn(name);
}

async function sortedOn(name) {
  await loadEntities();
  refreshUndo();
  if (state.entities[name]) renderTriage(); else triageAdvance();
}

// a retired person's later mention that was them after all
async function keepRetired() {
  const name = queue[qpos];
  const r = await api('/api/entities/keep-retired', {name, picks: sortPicks});
  if (!r) return;
  toast(`kept with ${r.to}, who stays retired.`);
  await sortedOn(name);
}

async function unretireHead() {
  const name = queue[qpos], head = state.entities[name]?.variant_of;
  const r = await api('/api/entities/retire', {name: head, retired: false});
  if (!r) return;
  toast(r.retired
    ? `${head} is retired through ${r.by}; un-retire the group instead.`
    : `${head} is back: later mentions go to them again.`);
  await sortedOn(name);
}

async function triageKey(key) {
  if (triageMode === 'sort' && state.entities[queue[qpos]]?.closed) {
    if (key === '1') { await keepRetired(); return; }
    if (key === 'x') { await unretireHead(); return; }
    if (key === 'n') { triagePrompt('qualify'); return; }
    if (key === 'k') { toast('say which one: 1 for the retired one, n for someone new, x to un-retire.'); return; }
    if (!['s', 'e', 'u', 'd', 'D'].includes(key)) return;
    triageMode = null;
  }
  if (triageMode === 'sort') {
    const i = Number(key);
    if (i >= 1 && i <= Math.min(9, sortChoices.length)) { await sortTo(sortChoices[i - 1]); return; }
    if (key === 'n') { triagePrompt('qualify'); return; }
    if (key === 'k') { toast(`say which one: ${sortChoices.length ? `1–${Math.min(9, sortChoices.length)}, or ` : ''}n for someone new.`); return; }
    if (!['s', 'e', 'u', 'd', 'D'].includes(key)) return;
    triageMode = null;  // the usual keys work on a sorting card too
  }
  if (triageMode === 'kind') {
    if (key === 'Escape') { triageCancel(); return; }
    const k = {1: 'person', 2: 'project', 3: 'place', 4: 'thing', 5: 'animal'}[key];
    if (k === 'thing') askCategory();
    else if (k) retype(k);
    return;
  }
  if (triageMode === 'category') {
    if (key === 'Escape') { triageCancel(); return; }
    const c = CATEGORIES[Number(key) - 1];
    if (c) retype('thing', c);
    return;
  }
  if (triageMode === 'mixed') {
    if (key === 'k' || key === 'Enter') keep(queue[qpos], true);
    else if (key === 'e') { triageCancel(); editInEntities(queue[qpos]); }
    else if (key === 'Escape') triageCancel();
    return;
  }
  if (triageMode === 'never') {
    if (key === 'D' || key === 'Enter') neverTrack();
    else if (key === 'Escape') triageCancel();
    return;
  }
  if (triageMode !== null) return;
  if (qpos >= queue.length && key !== 'u') return;
  const name = queue[qpos];
  switch (key) {
    case 'k':
      if (state.entities[name]?.mixup) askMixed(name);
      else await keep(name);
      break;
    case 's':
      skipped.push(name);
      toast('skipped. Back in the queue next time.');
      triageAdvance();
      break;
    case 'd': await deleteMentions(name); break;
    case 'D': askNeverTrack(name); break;
    case 'x':
      await triageAct(n => api('/api/entities/retire', {name: n, retired: true}),
        r => `${r.name} retired: out of everyday view. u brings them back.`);
      break;
    case 'e': editInEntities(name); break;
    case 'm': triagePrompt('merge'); break;
    case 'c': triagePrompt('correct'); break;
    case 'r': triagePrompt('rename'); break;
    case 'a': triagePrompt('alias'); break;
    case 'p':
      if (NO_PARTS.has(state.entities[name]?.type)) { toast(`${state.entities[name].type === 'person' ? 'people' : 'animals'} aren't parts of anything; a group holds them.`); break; }
      triagePrompt('part');
      break;
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

// a thing asks which kind of thing it is, one more key
const CATEGORIES = ['music', 'game', 'show', 'book', 'event', 'other'];
function askCategory() {
  triageMode = 'category';
  $('triage-kind-row').hidden = true;
  $('triage-cat-row').hidden = false;
}

async function retype(kind, category = '') {
  triageMode = null;
  $('triage-kind-row').hidden = true;
  $('triage-cat-row').hidden = true;
  await triageAct(
    n => api('/api/entities/retype', {name: n, new_type: kind, new_name: '', category}),
    r => `${r.retyped} is now a ${kind}${category ? ` (${category})` : ''}.`,
  );
}

export function init() {
  $('triage-apply').onclick = () => triageApply();
  $('triage-as-part').onclick = () => triageApply(true);
  $('triage-cancel').onclick = triageCancel;
  $('triage-kind-cancel').onclick = triageCancel;
  $('triage-cat-cancel').onclick = triageCancel;
  $('triage-never-yes').onclick = neverTrack;
  $('triage-mixed-yes').onclick = () => keep(queue[qpos], true);
  $('triage-mixed-edit').onclick = () => { triageCancel(); editInEntities(queue[qpos]); };
  $('triage-mixed-cancel').onclick = triageCancel;
  $('triage-never-cancel').onclick = triageCancel;
  $('triage-input').addEventListener('input', suggest);
  $('triage-input').addEventListener('keydown', e => {
    if (e.key === 'Escape') { e.preventDefault(); triageCancel(); }
    else if (e.key === 'Enter') { e.preventDefault(); triageApply(); }
    e.stopPropagation();
  });

  document.querySelectorAll('#triage-kind-row button[data-kind]').forEach(b => b.onclick = () =>
    b.dataset.kind === 'thing' ? askCategory() : retype(b.dataset.kind));
  document.querySelectorAll('#triage-cat-row button[data-cat]').forEach(b => b.onclick = () => retype('thing', b.dataset.cat));

  document.addEventListener('keydown', e => {
    if (state.activeTab !== 'triage') return;
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const keys = ['m', 'c', 'r', 'a', 'p', 'k', 'd', 'D', 'x', 's', 't', 'u', 'e', 'n', '1', '2', '3', '4', '5', '6', '7', '8', '9'];
    if (triageMode === 'never' || triageMode === 'mixed') keys.push('Enter', 'Escape');
    else if (['Enter', 'Escape'].includes(e.key) && triageMode !== 'kind' && triageMode !== 'category') return;
    if (keys.includes(e.key)) e.preventDefault();
    triageKey(e.key);
  });

  document.querySelectorAll('#triage-btns button').forEach(b => {
    b.onclick = () => triageKey(b.dataset.tkey);
  });
  $('triage-skipped').onclick = () => startTriage(skipped.slice());
  $('triage-open-entities').onclick = () => showTab('entities');
  $('triage-undo-last').onclick = () => triageKey('u');
}
