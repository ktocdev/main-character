import { $ } from './core.js';

// ---- conversation helpers ----
export function addMsg(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  $('write-log').appendChild(d);
  $('write-log').scrollTop = $('write-log').scrollHeight;
  return d;
}

export async function streamInto(el, url, payload) {
  const res = await fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  if (!res.ok) { el.classList.remove('thinking'); el.textContent = 'error: ' + (await res.text()); return res; }
  // headers arrive before the model has produced anything, so keep the
  // thinking pulse until the first real token — that's the actual wait
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let started = false;
  while (true) {
    const {done, value} = await reader.read();
    if (done) break;
    const chunk = dec.decode(value, {stream: true});
    if (!chunk) continue;
    if (!started) { el.classList.remove('thinking'); el.textContent = ''; started = true; }
    el.textContent += chunk;
    const scroller = el.closest('#write-log, #chat-log');
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }
  if (!started) { el.classList.remove('thinking'); el.textContent = '[no response]'; }
  return res;
}

export function composerBusy(busy) {
  $('chat-send').disabled = busy;
  $('entry-send').disabled = busy;
  $('reflect-btn').disabled = busy;
}
