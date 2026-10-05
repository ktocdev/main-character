// SPDX-License-Identifier: AGPL-3.0-or-later
// ---- the demo script ----
// In a demo journal (the local one Settings opens, and the published web
// demo), whatever a visitor types, the next scripted message is what gets
// sent, and its reply was written for it (mock_fixtures/demo_script.json).
// This is the page's half: it swaps the box's text before the app's own
// send handler reads it, shows the next message in the box ahead of time,
// and opens with a notice saying so. The server's half (demo_script.py, or
// web-demo/backend.js in the published demo) knows the replies and where
// the visitor is; GET /api/demo/script tells the page the texts and the
// position, and the page asks again after every turn (the mc:turn and
// mc:closed events write.js and lookup.js dispatch).

let script = null;   // {write, lookup, chips, write_at, lookup_at, end_hint, notice}

async function load() {
  try {
    const r = await fetch('/api/demo/script');
    script = r.ok ? await r.json() : null;
  } catch { script = null; }
  return script;
}

// The first entry step at or after `i` (the script's length when none is left).
function nextEntry(i) {
  while (i < script.write.length && script.write[i].send !== 'entry') i++;
  return i;
}

// ---- the next message, shown before it's sent ----
// Each box holds the next scripted message, so what will be sent is never a
// surprise. It stays editable, and whatever is in it is swapped all the same.
// The box isn't grown to fit: a long entry put in as a reply lands would
// push that reply up out of view. Setting .value fires no input event, so
// the write tab's "Started …" stamp and the saved draft are left alone.
function prefill(id, step) {
  const box = document.getElementById(id);
  if (!box) return;
  if (step) { box.value = step.text; return; }
  box.value = '';
  box.placeholder = script.end_hint;
}
const prefillWrite = () => prefill('entry-text', script.write[script.write_at]);
const prefillLookup = () => {
  const q = script.lookup[script.lookup_at];
  prefill('chat-text', q === undefined ? null : {text: q});
};

// ---- the swap ----
// A capture-phase listener on the document runs before the buttons' own
// handlers, so the box already holds the scripted text when they read it.
// An empty box is left alone: the app does nothing with it, as before.
const RIGHT_BUTTON = {entry: 'entry-send', chat: 'chat-send'};

function swapWrite(e, button) {
  const box = document.getElementById('entry-text');
  if (!box || !box.value.trim()) return;
  const noReplyBox = document.getElementById('entry-noreply');
  const noReply = button.id === 'entry-send' && noReplyBox?.checked;
  const step = script.write[noReply ? nextEntry(script.write_at) : script.write_at];
  if (!step) {
    // The script is over: sent as typed. A no-reply save never streams,
    // so the end message would have nowhere to show; take the reply path.
    if (noReply) noReplyBox.checked = false;
    return;
  }
  box.value = step.text;
  if (!noReply && button.id !== RIGHT_BUTTON[step.send]) {
    // the next step is an entry and they pressed send, or the other way round
    e.preventDefault();
    e.stopImmediatePropagation();
    document.getElementById(RIGHT_BUTTON[step.send]).click();
  }
}

function swapLookup() {
  const box = document.getElementById('chat-text');
  if (!box || !box.value.trim()) return;
  if (script.chips.includes(box.value.trim())) return;
  const q = script.lookup[script.lookup_at];
  if (q !== undefined) box.value = q;
}

// ---- the notice ----
// The swap would surprise anyone who didn't know about it, so the demo opens
// by saying so, in the design system's Modal. Once per trip into a demo: the
// server names the trip (its instance id), and the page remembers the last
// one it showed. The web demo's backend forgets on every load.
const SEEN = 'rag_demo_notice';

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text) e.textContent = text;
  return e;
}

function notice() {
  let seen = null;
  try { seen = localStorage.getItem(SEEN); } catch { }
  if (seen === script.notice.key) return;
  try { localStorage.setItem(SEEN, script.notice.key); } catch { }
  const d = el('dialog', 'modal');
  d.id = 'demo-notice';
  d.setAttribute('aria-labelledby', 'demo-notice-title');
  const title = el('h2', 'modal-title', 'This demo follows a script');
  title.id = 'demo-notice-title';
  const form = el('form', 'modal-actions');
  form.method = 'dialog';
  const ok = el('button', 'send', 'start demo');
  ok.autofocus = true;
  form.append(ok);
  d.append(el('div', 'eyebrow', script.notice.eyebrow), title,
    el('p', 'modal-body', script.notice.body), form);
  d.addEventListener('close', () => {
    d.remove();
    document.getElementById('entry-text')?.focus();
  });
  document.body.append(d);
  d.showModal();
}

export async function init() {
  if (!await load()) return;
  document.addEventListener('click', e => {
    const button = e.target.closest && e.target.closest('#entry-send, #chat-send, #lookup-send');
    if (!button || button.disabled || !script) return;
    if (button.id === 'lookup-send') swapLookup();
    else swapWrite(e, button);
  }, true);
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.shiftKey || !script) return;
    if (e.target && e.target.id === 'chat-text') swapLookup();
  }, true);
  // after every turn and every close, where the visitor is now
  document.addEventListener('mc:turn', async e => {
    if (!await load()) return;
    if (e.detail?.tab === 'ask') prefillLookup();
    else prefillWrite();
  });
  document.addEventListener('mc:closed', async () => {
    if (await load()) prefillWrite();
  });
  prefillWrite();
  prefillLookup();
  notice();
}
