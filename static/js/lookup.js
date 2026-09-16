// SPDX-License-Identifier: AGPL-3.0-or-later
import { $ } from './core.js';
import { streamInto } from './conversation.js';

// ---- chat (lookup) ----
// An information tool over the journal — its own conversation, never
// part of the open chat, never journal memory. Display kept locally.
function lookupMsg(role, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  d.textContent = text;
  $('chat-log').appendChild(d);
  $('chat-log').scrollTop = $('chat-log').scrollHeight;
  return d;
}
function saveLookupLog() {
  const msgs = [...$('chat-log').querySelectorAll('.msg')].map(el => ({
    role: el.classList.contains('you') ? 'you' : 'companion',
    text: el.textContent,
  }));
  localStorage.setItem('rag_lookup', JSON.stringify(msgs));
}
export function restoreLookupLog() {
  if ($('chat-log').childElementCount) return;
  try {
    for (const m of JSON.parse(localStorage.getItem('rag_lookup') || '[]')) {
      lookupMsg(m.role, m.text);
    }
  } catch (e) { }
}
async function sendLookup() {
  const text = $('chat-text').value.trim();
  if (!text) return;
  $('chat-text').value = '';
  $('lookup-send').disabled = true;
  lookupMsg('you', text);
  const el = lookupMsg('companion thinking', '');
  try { await streamInto(el, '/api/lookup', {message: text}); }
  finally {
    saveLookupLog();
    $('lookup-send').disabled = false;
    $('chat-text').focus();
  }
}
export function init() {
  $('lookup-send').onclick = sendLookup;
  $('chat-text').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendLookup(); }
  });
  $('lookup-clear').onclick = async () => {
    $('chat-log').innerHTML = '';
    localStorage.removeItem('rag_lookup');
    try { await fetch('/api/lookup/reset', {method: 'POST'}); } catch (e) { }
  };
}

