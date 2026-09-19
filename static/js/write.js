// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, esc, refreshStatus } from './core.js';
import { state } from './state.js';
import { addMsg, streamInto, composerBusy, anchorTop } from './conversation.js';
import { renderSessionPart, addSessionBraid, loadHistory } from './history.js';

// ---- draft persistence + growing textarea ----
// the write box survives an accidental refresh or tab close; it grows with
// its content (like Claude desktop) to fill the viewport, then scrolls
// internally. Item 4: the earlier `innerHeight * 0.4` cap was the interim
// stop; full height is the spec, and item 11's seed editor inherits it.
// Enter never submits here — Enter and Shift+Enter both make new lines.
function autosizeEntry() {
  const t = $('entry-text');
  t.style.height = 'auto';
  // The composer is pinned to the bottom of the write panel and grows UPWARD,
  // squeezing the log (flex:1) above it — so the room to grow is NOT the gap
  // below the box (that is ~zero), it is the panel height minus the composer's
  // own fixed chrome (stamp row, controls, padding) minus a small peek of the
  // log we always keep visible. Measured after height:auto so both offset
  // heights reflect the same layout pass.
  const panel = t.closest('.tab');
  const composer = t.closest('.composer');
  const chrome = composer ? composer.offsetHeight - t.offsetHeight : 68;
  const panelH = panel ? panel.clientHeight : window.innerHeight;
  const LOG_PEEK = 80;  // never let the box eat the whole log
  const max = Math.max(120, panelH - chrome - LOG_PEEK);
  t.style.height = Math.min(t.scrollHeight + 2, max) + 'px';
}
function clearComposer() {
  $('entry-text').value = '';
  localStorage.removeItem('rag_draft');
  autosizeEntry();
}
function restoreDraft(text) {
  $('entry-text').value = text;
  localStorage.setItem('rag_draft', text);
  autosizeEntry();
}

// ---- save identity ----
// Every deliberate save carries an id the server stores as the entry's own.
// A save whose outcome is unknown (the connection dropped before an answer)
// keeps its id with the draft, so saving that same text again is recognised
// as a retry of it -- not a second entry. Once a save is confirmed the id is
// spent: writing the same words again later is a new entry, and counts.
const PENDING_SAVE = 'rag_pending_save';
function newSaveId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return Array.from(crypto.getRandomValues(new Uint8Array(16)),
    b => b.toString(16).padStart(2, '0')).join('');
}
function saveIdFor(text) {
  let pending = null;
  try { pending = JSON.parse(localStorage.getItem(PENDING_SAVE)); } catch (e) { }
  if (pending && pending.text === text && pending.id) return pending.id;
  const id = newSaveId();
  try { localStorage.setItem(PENDING_SAVE, JSON.stringify({text, id})); } catch (e) { }
  return id;
}
function settleSave() {
  try { localStorage.removeItem(PENDING_SAVE); } catch (e) { }
}
const SAVED_NOTE = 'becomes journal memory when you close the chat';
const UNREACHED = 'could not reach the journal — your draft is back, and '
  + 'saving it again will not make a second copy';
// The one rule that decides whether a close is possible, mirrored from the
// server (sessions.close_session: "nothing new in this chat yet"). A
// carried-forward base part is context, not new material — only messages
// count, and dreams are excluded here exactly as close_session excludes them.
export function hasNewMaterial(messages) {
  return (messages || []).some(m => m.role === 'you' && !m.dream);
}

export async function closeSession() {
  // A pending candidate is retired, not carried: generate_candidate folds the
  // new archive into the *live* seed, so an unuploaded candidate's integration
  // is backed up and then skipped in the seed's lineage. Say so before the
  // close — the only moment the author can still act on it.
  const s = await seedState();
  const pending = (s && s.candidate_exists)
    ? '\n\nA seed summary candidate from your last close is still pending. '
      + 'Closing now retires it to summaries/seed_backups and builds the next '
      + 'one from the live seed instead — what it integrated is kept as a file '
      + 'but drops out of the seed. Download and upload it first to keep it.\n'
    : '';
  if (!confirm('Close this chat?' + pending + '\n\nYour side of it becomes a journal entry — tagging, entities, summaries, dream extraction, and the seed summary candidate run in the background. A fresh chat starts empty.')) return;
  const r = await api('/api/sessions/close', {});
  if (!r) return;
  $('write-log').innerHTML = '';
  $('entry-saved').textContent = `chat closed — saved as "${r.title}".`;
  trackCloseProgress();
  state.sessionSel = 'current';
  if (state.activeTab === 'history') await loadHistory();
  refreshStatus();
}

// After a close, the memory pipeline runs as background tasks — so the close
// response returns before any of it has happened (item 1). Poll the server's
// progress record and show which stage is running instead of one static line.
// The per-call delays mock mode adds are what make each stage visible without
// a real key; against a live key the stages are genuinely long. When the seed
// stage lands, its candidate banner appears the way watching /api/seed used to.
let closePoll = null;
const CP_GLYPH = {done: '✓', running: '…', failed: '✕', pending: '·'};
function renderCloseProgress(steps, done) {
  const box = $('close-progress');
  if (!box) return;
  box.hidden = false;
  box.innerHTML = '<b>updating memory</b>'
    + steps.map(s => `<div class="cp-step cp-${s.status}">`
        + `<span class="cp-mark">${CP_GLYPH[s.status] || '·'}</span>`
        + `<span>${esc(s.label)}</span></div>`).join('')
    + (done ? '<div class="cp-done">memory updated — a fresh chat is open</div>' : '');
}
function trackCloseProgress() {
  clearInterval(closePoll);
  let seedShown = false;
  let tries = 0;
  const stop = () => {
    clearInterval(closePoll);
    refreshSeedMenu();
    refreshStatus();  // entity counts change after the background refresh
    setTimeout(() => { const b = $('close-progress'); if (b) b.hidden = true; }, 8000);
  };
  const tick = async () => {
    let p;
    try { p = await (await fetch('/api/sessions/close/progress')).json(); }
    catch (e) {
      // transient — the next tick tries again, but a run of failures still
      // has to hit the same ~120 s ceiling normal polling does, or a
      // persistently erroring endpoint polls forever.
      if (++tries > 120) stop();
      return;
    }
    renderCloseProgress(p.steps, p.done);
    if (!seedShown && p.steps.some(s => s.key === 'seed' && s.status === 'done')) {
      seedShown = true;
      refreshSeedMenu();
    }
    // ~120 s ceiling so a stuck pipeline can't poll forever; the pipeline is
    // seconds in mock mode and well under this against a real key.
    if (p.done || ++tries > 120) stop();
  };
  closePoll = setInterval(tick, 1000);
  tick();
}

// ---- the seed ritual (close → download candidate → edit in VS Code → upload) ----
export async function seedState() {
  try { return await (await fetch('/api/seed')).json(); } catch (e) { return null; }
}
export async function refreshSeedMenu() {
  const s = await seedState();
  if (!s) return;
  $('seed-download').hidden = !s.exists;
  $('seed-banner').hidden = !s.candidate_exists;
}
// ---- write (the one conversation surface) ----
// A chat over the current journal cluster: everything the open session
// contains (the chat it continues + entries + replies) scrolls above,
// the composer stays at the bottom, newest message right above it.
export async function loadWriteLog() {
  const el = $('write-log');
  el.innerHTML = '';
  try {
    // one-time migration: the chat an older version of this page kept
    // in localStorage joins the open session on the server
    const old = localStorage.getItem('rag_chat');
    if (old) {
      try {
        await fetch('/api/sessions/seed', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({messages: JSON.parse(old)}),
        });
      } catch (e) { }
      localStorage.removeItem('rag_chat');
    }
    const r = await (await fetch('/api/sessions/current')).json();
    for (const p of r.parts) renderSessionPart(el, p, true);
    addSessionBraid(el, r.messages, null, true);
    if (!r.parts.length && !r.messages.length) await maybeFirstRun(el);
    el.scrollTop = el.scrollHeight;
  } catch (e) { }
}

// A brand-new journal's write screen is otherwise a blank log. One message,
// shown only while there is nothing here and nothing has ever been closed,
// so it goes away on its own: addMsg removes it at the first entry, and a
// journal with archives never shows it again. No dismissal, nothing stored.
// Worded with the help tab's own vocabulary so the two agree.
async function maybeFirstRun(el) {
  let s;
  try { s = await (await fetch('/api/sessions')).json(); } catch (e) { return; }
  if ((s.sessions || []).some(i => i.kind === 'archive')) return;
  const d = document.createElement('div');
  d.id = 'write-first-run';
  d.innerHTML = '<p>This is one open chat, and everything you write here joins it. '
    + '<b>save entry</b> keeps a journal entry; <b>send</b> talks it over with the companion.</p>'
    + '<p>Write for about a week, then open the <b>⋯</b> menu and choose '
    + '<b>summarize &amp; close chat</b> — that is when it all becomes journal memory.</p>';
  el.appendChild(d);
}

// The entry's own timestamp. Stamped when the user starts writing, not
// when they hit save — a long entry belongs to the moment it was begun.
// Editable, so a missed day can be written up later and still land on the
// day it happened; close_session groups by this.
let entryStamp = null;

function pad(n) { return String(n).padStart(2, '0'); }

// The server's wall clock, not the browser's — the two differ when
// MC_TIMEZONE names a zone this machine isn't in. See refreshStatus.
function serverNow() { return new Date(Date.now() + (state.clockSkewMs || 0)); }

function stampString(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
       + `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function renderStamp() {
  if (!entryStamp) { $('entry-stamp').hidden = true; return; }
  $('entry-stamp').hidden = false;
  const long = state.dateStyle !== 'short';
  const when = entryStamp.toLocaleString(undefined, {
    month: long ? 'long' : 'numeric', day: 'numeric',
    year: long ? 'numeric' : '2-digit',
    hour: 'numeric', minute: '2-digit',
  });
  $('entry-stamp-text').textContent = `Started ${when}`;
  $('entry-stamp-text').title = state.tz
    ? `when this entry was written — ${state.tz}`
    : 'when this entry was written';
}

function startStamp() {
  if (entryStamp) return;          // the first keystroke wins, not the last
  entryStamp = serverNow();
  renderStamp();
}

export function clearStamp() { entryStamp = null; renderStamp(); }

function initStamp() {
  // 'input', not 'focus': the send handler refocuses the box in its
  // finally block, and on 'focus' that fired startStamp again the instant
  // after clearStamp() — so the stamp bar never hid, and an entry begun
  // hours later carried the previous save's time instead of its own.
  $('entry-text').addEventListener('input', startStamp);
  $('entry-stamp-edit').onclick = () => {
    const d = entryStamp || serverNow();
    $('entry-date').value = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    $('entry-time').value = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    $('entry-stamp-text').hidden = true;
    $('entry-stamp-edit').hidden = true;
    $('entry-stamp-fields').hidden = false;
  };
  $('entry-stamp-done').onclick = () => {
    const [y, m, day] = ($('entry-date').value || '').split('-').map(Number);
    const [hh, mm] = ($('entry-time').value || '').split(':').map(Number);
    if (y && m && day) entryStamp = new Date(y, m - 1, day, hh || 0, mm || 0);
    $('entry-stamp-fields').hidden = true;
    $('entry-stamp-text').hidden = false;
    $('entry-stamp-edit').hidden = false;
    renderStamp();
  };
}

export function init() {
  $('entry-text').value = localStorage.getItem('rag_draft') || '';
  autosizeEntry();
  initStamp();
  if ($('entry-text').value) startStamp();   // a restored draft is already begun
  $('entry-text').addEventListener('input', () => {
    localStorage.setItem('rag_draft', $('entry-text').value);
    autosizeEntry();
  });

  $('chat-send').onclick = async () => {
    const text = $('entry-text').value.trim();
    if (!text) return;
    clearComposer();
    composerBusy(true);
    const you = addMsg('you', text);
    const el = addMsg('companion thinking', '');
    anchorTop(you);   // stay on your own message while the reply streams in
    try { await streamInto(el, '/api/chat', {message: text}); }
    finally { composerBusy(false); $('entry-text').focus(); }
  };

  $('reflect-btn').onclick = async () => {
    composerBusy(true);
    const el = addMsg('companion thinking', '');
    anchorTop(el);   // no message of your own here — hold on the reply itself
    try { await streamInto(el, '/api/reflect', {}); }
    finally { composerBusy(false); $('entry-text').focus(); }
  };

  $('reset').onclick = () => { $('write-actions').removeAttribute('open'); closeSession(); };

  // Mock-only reset (see /api/sessions/discard): throw the scratch chat away
  // without committing it as an entry or running the pipeline. Visibility is
  // gated in CSS by body.mock-mode; the server refuses it off mock regardless.
  $('discard').onclick = async () => {
    $('write-actions').removeAttribute('open');
    if (!confirm('Discard this chat?\n\nIt is thrown away — no journal entry, '
      + 'no memory update. This is a testing affordance for the mock journal.')) return;
    const r = await api('/api/sessions/discard', {});
    if (!r) return;
    $('write-log').innerHTML = '';
    $('entry-saved').textContent = 'chat discarded — a fresh one is open.';
    state.sessionSel = 'current';
    if (state.activeTab === 'history') await loadHistory();
    refreshStatus();
  };

  const resetTitle = $('reset').title;
  $('write-actions').addEventListener('toggle', async () => {
    if (!$('write-actions').open) return;
    refreshSeedMenu();
    // don't offer a close the server will refuse — see hasNewMaterial
    let fresh = true;
    try {
      const r = await (await fetch('/api/sessions/current')).json();
      fresh = hasNewMaterial(r.messages);
    } catch (e) { }   // can't tell: leave the action available
    $('reset').disabled = !fresh;
    $('reset').title = fresh ? resetTitle
      : 'nothing new in this chat yet — write or chat first';
  });
  $('seed-download').onclick = () => { window.location = '/api/seed/download?which=current'; };
  $('seed-banner-download').onclick = () => { window.location = '/api/seed/download?which=candidate'; };
  $('seed-upload-btn').onclick = () => $('seed-upload-file').click();
  $('seed-banner-upload').onclick = () => $('seed-upload-file').click();
  $('seed-upload-file').onchange = async () => {
    const f = $('seed-upload-file').files[0];
    if (!f) return;
    const text = await f.text();
    const r = await api('/api/seed/upload', {text});
    if (r) $('entry-saved').textContent = `seed updated from ${f.name} — every new turn opens with it`;
    $('seed-upload-file').value = '';
    refreshSeedMenu();
  };
  refreshSeedMenu();

  $('refresh-summaries').onclick = async () => {
    const b = $('refresh-summaries');
    b.disabled = true;
    b.textContent = 'refreshing…';
    try {
      const r = await api('/api/summaries/refresh', {});
      if (r) b.textContent = `memory current (${r.arcs} weeks)`;
    } finally {
      setTimeout(() => { b.textContent = 'refresh memory'; b.disabled = false; }, 4000);
    }
  };

  $('entry-send').onclick = async () => {
    const text = $('entry-text').value.trim();
    if (!text) return;
    const ts = entryStamp ? stampString(entryStamp) : null;

    // No-reply mode (item 3): save the entry and skip the companion call
    // entirely — no cost, sometimes you just want to write. The entry still
    // becomes journal memory at close, exactly like a replied-to one.
    const save_id = saveIdFor(text);

    if ($('entry-noreply').checked) {
      clearComposer();
      composerBusy(true);
      $('entry-saved').textContent = 'saving…';
      const you = addMsg('you', text);
      anchorTop(you);
      try {
        let r = null, unreached = false;
        try { r = await api('/api/entry', {text, ts, no_reply: true, save_id}); }
        catch (e) { unreached = true; }
        if (r && r.ok) {
          settleSave();
          clearStamp();
          if (r.duplicate) {
            you.remove();   // the copy that landed earlier is already in the log
            await loadWriteLog();
          }
          $('entry-saved').textContent = r.duplicate
            ? 'that entry was already saved — nothing was added twice'
            : `entry saved, no reply — ${SAVED_NOTE}`;
          refreshStatus();
        } else {
          you.remove();
          restoreDraft(text);
          $('entry-saved').textContent = unreached ? UNREACHED
            : 'save failed — your draft is untouched';
        }
      } finally { composerBusy(false); $('entry-text').focus(); }
      return;
    }

    clearComposer();            // shrink the box back to default size right away
    composerBusy(true);
    $('entry-saved').textContent = 'saving…';
    const you = addMsg('you', text);
    const el = addMsg('companion thinking', '');
    anchorTop(you);             // stay on your entry while the reply streams
    try {
      let res = null;
      try { res = await streamInto(el, '/api/entry', {text, ts, save_id}); }
      catch (e) {
        el.classList.remove('thinking');
        el.textContent = 'error: could not reach the journal';
      }
      // The response head says whether the entry is stored, and it arrives
      // before the reply -- so a reply that fails afterwards cannot make a
      // saved entry look unsaved, or tempt a resave that would double it.
      if (res && res.headers.get('X-Entry-Saved') === '1') {
        settleSave();
        clearStamp();   // the next entry gets its own
        if (res.payload && res.payload.duplicate) {
          await loadWriteLog();
          $('entry-saved').textContent = 'that entry was already saved — nothing was added twice';
        } else {
          $('entry-saved').textContent = res.interrupted
            ? `entry saved, but the reply was interrupted — the entry ${SAVED_NOTE}`
            : `entry saved — ${SAVED_NOTE}`;
        }
        refreshStatus();
      } else {
        // not saved (or unknown) — put the draft back so nothing is lost
        restoreDraft(text);
        $('entry-saved').textContent = res ? 'save failed — your draft is untouched'
          : UNREACHED;
      }
    } finally { composerBusy(false); $('entry-text').focus(); }
  };
}
