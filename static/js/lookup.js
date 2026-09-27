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
// Smart replies, per question: on, Claude can search again on its own before
// answering, at the cost of several calls. Off by default, and remembered in
// this browser only. The demo hides it (see core.js): canned replies can't
// search, and the server ignores it there anyway.
const SMART_KEY = 'rag_lookup_smart';
function smartOn() {
  return $('lookup-smart').classList.contains('on');
}
function setSmart(on) {
  $('lookup-smart').classList.toggle('on', on);
  $('lookup-smart').setAttribute('aria-pressed', String(on));
  try { localStorage.setItem(SMART_KEY, on ? '1' : ''); } catch (e) { }
}
async function sendLookup() {
  const text = $('chat-text').value.trim();
  if (!text) return;
  $('chat-text').value = '';
  $('lookup-send').disabled = true;
  lookupMsg('you', text);
  const el = lookupMsg('companion thinking', '');
  const smart = smartOn() && !$('lookup-smart').hidden;
  try { await streamInto(el, '/api/lookup', {message: text, smart}); }
  finally {
    saveLookupLog();
    $('lookup-send').disabled = false;
    $('chat-text').focus();
  }
}
export function init() {
  $('lookup-send').onclick = sendLookup;
  let saved = '';
  try { saved = localStorage.getItem(SMART_KEY) || ''; } catch (e) { }
  setSmart(saved === '1');
  $('lookup-smart').onclick = () => setSmart(!smartOn());
  $('chat-text').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendLookup(); }
  });
  $('lookup-clear').onclick = async () => {
    $('chat-log').innerHTML = '';
    localStorage.removeItem('rag_lookup');
    try { await fetch('/api/lookup/reset', {method: 'POST'}); } catch (e) { }
  };
}

