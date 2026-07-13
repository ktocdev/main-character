export const $ = id => document.getElementById(id);
// ---- status ----
export async function refreshStatus() {
  const s = await (await fetch('/api/status')).json();
  $('status').textContent = `${s.entries} entries · ${s.entities} entities`;
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
