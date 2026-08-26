export const $ = id => document.getElementById(id);
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
// ---- status ----
export async function refreshStatus() {
  const s = await (await fetch('/api/status')).json();
  $('status').textContent = `${s.entries} entries · ${s.entities} entities`;
  // Mock mode is indistinguishable from real once a reply is on screen, so
  // the banner stays up for the whole session rather than appearing per-call.
  document.body.classList.toggle('mock-mode', !!s.mock);
}
export async function api(url, payload) {
  const res = await fetch(url, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const r = await res.json();
  if (r.error) { alert(r.error); return null; }
  return r;
}
