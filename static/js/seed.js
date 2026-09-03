import { $, api } from './core.js';
import { state } from './state.js';
import { refreshSeedMenu } from './write.js';

// ---- seed summary editor (item 11) ----
// The seed summary is the rolling, co-edited life summary every chat opens
// with. Editing it used to mean leaving the app: close a chat, download the
// candidate, open it in a text editor, upload it back. That round trip is the
// reason a candidate could sit unreviewed for weeks. This brings the edit
// in-app as a sub-view of write -- not a nav tab (the bar is already full),
// reached from the #seed-banner or the ⋯ menu.
//
// Backend is done: GET /api/seed (state), GET /api/seed/download?which= (the
// document itself), POST /api/seed/upload (the one commit point, which backs
// up the prior seed and retires the superseded candidate). Nothing new here.

const DRAFT_KEY = 'mc_seed_draft';

// Prepended when handing the document to claude.ai. The URL ?q= route can't
// carry a 15 KB seed (encoding roughly doubles it, past what gateways allow),
// so this is copy-then-open, not a real prefill -- the button label says so.
const CLAUDE_PROMPT =
  'Below is my journal\'s "seed summary" — a rolling life summary my journaling '
  + 'companion reads at the start of every conversation. Help me revise it: keep '
  + 'the same first-person voice and roughly the same length, and return only the '
  + 'updated summary document, nothing else.\n\n---\n\n';

// Which document is loaded and the timestamp it carried when this edit began.
// srcWhich is 'candidate' (a post-close candidate waiting for review) or
// 'current' (the live seed). srcUpdated lets a draft notice the underlying
// document changed under it. baseline is the on-disk text, for dirty checks.
let editorState = { srcWhich: 'current', srcUpdated: null };
let baseline = '';
let draftTimer = null;

async function seedStatus() {
  try { return await (await fetch('/api/seed')).json(); } catch (e) { return null; }
}
async function fetchDoc(which) {
  try {
    const r = await fetch('/api/seed/download?which=' + which);
    return r.ok ? await r.text() : '';
  } catch (e) { return ''; }
}
// Load order is draft → candidate → live seed. The source under any draft is
// the candidate when one is pending, otherwise the live seed.
function pickSource(s) {
  if (s && s.candidate_exists) return { which: 'candidate', updated: s.candidate_updated };
  return { which: 'current', updated: s ? s.updated : null };
}

function readDraft() {
  try { const raw = localStorage.getItem(DRAFT_KEY); return raw ? JSON.parse(raw) : null; }
  catch (e) { return null; }
}
function writeDraft(obj) {
  try { localStorage.setItem(DRAFT_KEY, JSON.stringify(obj)); } catch (e) {}
}
function clearDraft() {
  try { localStorage.removeItem(DRAFT_KEY); } catch (e) {}
}

function setNote(msg) { $('seed-editor-note').textContent = msg || ''; }
function dirty() { return $('seed-editor-text').value !== baseline; }

function showSeedView() {
  document.querySelectorAll('nav button[data-tab]').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  // It is a sub-view of write, so keep write lit in the nav even though the
  // seed section is the one showing.
  const wb = document.querySelector('nav button[data-tab="write"]');
  if (wb) wb.classList.add('active');
  $('tab-seed').classList.add('active');
  state.activeTab = 'seed';
}

function renderMeta(s) {
  const meta = $('seed-editor-meta');
  if (editorState.srcWhich === 'candidate') {
    meta.textContent = `Editing the seed summary candidate — created ${s && s.candidate_updated || '—'}. `
      + 'Saving makes it your live seed.';
  } else if (s && s.exists) {
    meta.textContent = `Editing your live seed summary — last saved ${s.updated || '—'}.`;
  } else {
    meta.textContent = 'Editing your seed summary — none saved yet; saving creates it.';
  }
}

// A draft started against one document silently shadows a newer one a later
// chat close produces. Keep the draft, but say the source moved.
function renderDraftNote(s, hasDraft) {
  const n = $('seed-editor-draftnote');
  $('seed-discard-draft').hidden = !hasDraft;
  if (!hasDraft) { n.hidden = true; return; }
  const gone = editorState.srcWhich === 'candidate' && !(s && s.candidate_exists);
  const latest = editorState.srcWhich === 'candidate'
    ? (s && s.candidate_exists ? s.candidate_updated : null)
    : (s ? s.updated : null);
  const stale = gone || (latest && editorState.srcUpdated && latest !== editorState.srcUpdated);
  if (stale) {
    n.hidden = false;
    n.textContent = 'Heads up: the document this draft started from has changed since. '
      + 'Your draft is kept as-is — discard it to load the current version.';
  } else {
    n.hidden = true;
  }
}

export async function openSeedEditor() {
  showSeedView();
  const ta = $('seed-editor-text');
  ta.value = '';
  setNote('');
  const s = await seedStatus();
  const src = pickSource(s);
  const draft = readDraft();
  const hasDraft = !!(draft && typeof draft.text === 'string');
  editorState = hasDraft
    ? { srcWhich: draft.srcWhich || src.which, srcUpdated: draft.srcUpdated ?? src.updated }
    : { srcWhich: src.which, srcUpdated: src.updated };
  baseline = await fetchDoc(editorState.srcWhich);
  ta.value = hasDraft ? draft.text : baseline;
  renderMeta(s);
  renderDraftNote(s, hasDraft);
  ta.focus();
}

// Debounced, matching the composer's rag_draft persistence rather than adding
// a draft route. Stores the source binding so staleness can be detected later.
function scheduleDraftSave() {
  clearTimeout(draftTimer);
  draftTimer = setTimeout(() => {
    writeDraft({
      text: $('seed-editor-text').value,
      srcWhich: editorState.srcWhich,
      srcUpdated: editorState.srcUpdated,
    });
    $('seed-discard-draft').hidden = false;
    setNote('draft saved');
  }, 400);
}

async function save() {
  const text = $('seed-editor-text').value;
  // Mirror the server's floor so the friendly note fires before the 400 alert.
  if (text.trim().length < 200) {
    setNote('that looks too short to be a seed summary — nothing saved');
    return;
  }
  if (!confirm('Save this as your seed summary?\n\nEvery new chat will open with it. '
    + 'The current seed is backed up, and any pending candidate is retired.')) return;
  const r = await api('/api/seed/upload', { text });
  if (!r) return;                 // api() already alerted on the error
  clearDraft();
  await refreshSeedMenu();        // the candidate is gone; drop the banner
  await openSeedEditor();         // reload as the fresh live seed
  setNote('saved — every new chat now opens with it');
}

async function copy() {
  try {
    await navigator.clipboard.writeText($('seed-editor-text').value);
    setNote('copied to the clipboard');
  } catch (e) { setNote('could not copy — your browser blocked clipboard access'); }
}

// The buffer, not the file on disk -- a half-finished edit is what lands in an
// editor if someone still prefers one.
function download() {
  const blob = new Blob([$('seed-editor-text').value], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'seed_summary.md';
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  setNote('downloaded seed_summary.md (this box, not the file on disk)');
}

function triggerUpload() { $('seed-editor-file').click(); }
async function onUploadFile() {
  const f = $('seed-editor-file').files[0];
  if (!f) return;
  if (dirty() && !confirm(`Replace what's in the box with ${f.name}? Your current edits are not saved.`)) {
    $('seed-editor-file').value = '';
    return;
  }
  const text = await f.text();
  $('seed-editor-file').value = '';
  $('seed-editor-text').value = text;   // replace, don't commit -- Save is the one commit point
  scheduleDraftSave();
  setNote(`loaded from ${f.name} — not saved yet`);
}

async function openWithClaude() {
  const buf = CLAUDE_PROMPT + $('seed-editor-text').value;
  // Open first, synchronously: awaiting the clipboard before this spends the
  // click's user-activation and a popup blocker eats the tab.
  window.open('https://claude.ai/new', '_blank', 'noopener');
  try {
    await navigator.clipboard.writeText(buf);
    setNote('copied with a revise instruction — paste it into the new Claude tab');
  } catch (e) {
    setNote('opened Claude, but could not copy — copy the text here manually');
  }
}

async function discardDraft() {
  clearDraft();
  await openSeedEditor();   // recomputes the source from current state, no draft now
  setNote('draft discarded');
}

export function init() {
  // entry points: the ⋯ menu (live seed, any time) and the candidate banner
  $('seed-edit').onclick = () => { $('write-actions').removeAttribute('open'); openSeedEditor(); };
  $('seed-banner-edit').onclick = () => openSeedEditor();
  // back reuses the nav handler, which restores the write log
  $('seed-back').onclick = () => {
    const wb = document.querySelector('nav button[data-tab="write"]');
    if (wb) wb.click();
  };
  // Enter makes a newline; nothing here takes that away (no form, no submit-on-
  // Enter). Save is a button and only a button.
  $('seed-editor-text').addEventListener('input', () => { setNote(''); scheduleDraftSave(); });
  $('seed-save').onclick = save;
  $('seed-copy').onclick = copy;
  $('seed-dl').onclick = download;
  $('seed-upload2').onclick = triggerUpload;
  $('seed-editor-file').onchange = onUploadFile;
  $('seed-claude').onclick = openWithClaude;
  $('seed-discard-draft').onclick = discardDraft;
}
