import { $, api, refreshStatus } from './core.js';
import { state } from './state.js';
import { addMsg, streamInto, composerBusy } from './conversation.js';
import { renderSessionPart, addSessionBraid, loadHistory } from './history.js';

// ---- draft persistence + growing textarea ----
// the write box survives an accidental refresh or tab close; it grows
// with its content (like Claude desktop) up to 40% of the window.
// Enter never submits here — Enter and Shift+Enter both make new lines.
function autosizeEntry() {
  const t = $('entry-text');
  t.style.height = 'auto';
  t.style.height = Math.min(t.scrollHeight + 2, window.innerHeight * 0.4) + 'px';
}
function clearComposer() {
  $('entry-text').value = '';
  localStorage.removeItem('rag_draft');
  autosizeEntry();
}
export async function closeSession() {
  if (!confirm('Close this chat?\n\nYour side of it becomes a journal entry — tagging, entities, summaries, and dream extraction run in the background. A fresh chat starts empty.')) return;
  const r = await api('/api/sessions/close', {});
  if (!r) return;
  $('write-log').innerHTML = '';
  $('entry-saved').textContent = `chat closed — saved as "${r.title}"`;
  state.sessionSel = 'current';
  if (state.activeTab === 'history') await loadHistory();
  refreshStatus();
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
    for (const p of r.parts) renderSessionPart(el, p);
    addSessionBraid(el, r.messages);
    el.scrollTop = el.scrollHeight;
  } catch (e) { }
}

export function init() {
  $('entry-text').value = localStorage.getItem('rag_draft') || '';
  autosizeEntry();
  $('entry-text').addEventListener('input', () => {
    localStorage.setItem('rag_draft', $('entry-text').value);
    autosizeEntry();
  });

  $('chat-send').onclick = async () => {
    const text = $('entry-text').value.trim();
    if (!text) return;
    clearComposer();
    composerBusy(true);
    addMsg('you', text);
    const el = addMsg('companion thinking', '');
    try { await streamInto(el, '/api/chat', {message: text}); }
    finally { composerBusy(false); $('entry-text').focus(); }
  };

  $('reflect-btn').onclick = async () => {
    composerBusy(true);
    const el = addMsg('companion thinking', '');
    try { await streamInto(el, '/api/reflect', {}); }
    finally { composerBusy(false); $('entry-text').focus(); }
  };

  $('reset').onclick = () => { $('write-actions').removeAttribute('open'); closeSession(); };

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
    composerBusy(true);
    $('entry-saved').textContent = 'saving…';
    addMsg('you', text);
    const el = addMsg('companion thinking', '');
    try {
      const res = await streamInto(el, '/api/entry', {text});
      if (res.ok) {
        $('entry-saved').textContent = 'entry saved — becomes journal memory when you close the chat';
        clearComposer();
        refreshStatus();
      } else {
        $('entry-saved').textContent = 'save failed — your draft is untouched';
      }
    } finally { composerBusy(false); $('entry-text').focus(); }
  };
}
