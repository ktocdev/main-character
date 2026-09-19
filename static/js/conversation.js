// SPDX-License-Identifier: AGPL-3.0-or-later
import { $ } from './core.js';

// ---- conversation helpers ----
export function addMsg(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  $('write-first-run')?.remove();   // see maybeFirstRun in write.js
  $('write-log').appendChild(d);
  $('write-log').scrollTop = $('write-log').scrollHeight;
  return d;
}

// Bring a message to the top of its scroller and leave it there — the
// Claude-desktop anchor (item 5). After a send, the user's own entry sits at
// the top and the companion reply grows below it instead of shoving the
// viewport down. This also leaves the log scrolled away from the bottom, so
// streamInto's follow-the-bottom rule below stays quiet for the whole reply
// unless the user scrolls back down to it deliberately.
export function anchorTop(el) {
  const scroller = el.closest('#write-log, #chat-log');
  if (scroller) scroller.scrollTop = el.offsetTop - scroller.offsetTop;
}

// Within this many px of the bottom counts as "reading the newest line", so
// the log keeps following the stream. Past it the user has scrolled up to
// read something and following would yank them back.
const STICK_PX = 48;

export async function streamInto(el, url, payload) {
  const res = await fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    el.classList.remove('thinking');
    // Every route answers with {"error": ...}. Printing the raw body was fine
    // while errors meant something had broken; a spend cap is the first
    // refusal an author meets in normal use, and reading it as JSON turns
    // "you are at your monthly ceiling" back into a crash.
    const body = await res.text();
    let message = body;
    try { message = JSON.parse(body).error || body; } catch (e) { /* not JSON */ }
    el.textContent = 'error: ' + message;
    return res;
  }
  // A route that had nothing to stream (a retried save that already landed)
  // answers with JSON. That is a status for the caller, not reply text.
  if ((res.headers.get('content-type') || '').includes('application/json')) {
    el.classList.remove('thinking');
    res.payload = await res.json().catch(() => null);
    return res;
  }
  // headers arrive before the model has produced anything, so keep the
  // thinking pulse until the first real token — that's the actual wait
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let started = false;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      const chunk = dec.decode(value, {stream: true});
      if (!chunk) continue;
      if (!started) { el.classList.remove('thinking'); el.textContent = ''; started = true; }
      // Decide whether to follow *before* appending — the append changes
      // scrollHeight, so measuring after would call every position "the bottom".
      const scroller = el.closest('#write-log, #chat-log');
      const pinned = scroller &&
        scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= STICK_PX;
      el.textContent += chunk;
      if (pinned) scroller.scrollTop = scroller.scrollHeight;
    }
  } catch (e) {
    // The request already succeeded -- whatever it stored is stored. Only
    // the reply broke off, so say that here and let the caller report the
    // saved state from the response head it already has.
    el.classList.remove('thinking');
    el.textContent += (started ? '\n\n' : '') + '[the reply was interrupted]';
    res.interrupted = true;
    return res;
  }
  if (!started) { el.classList.remove('thinking'); el.textContent = '[no response]'; }
  return res;
}

export function composerBusy(busy) {
  $('chat-send').disabled = busy;
  $('entry-send').disabled = busy;
  $('reflect-btn').disabled = busy;
}
