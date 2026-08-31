export const $ = id => document.getElementById(id);
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
// ---- status ----
import { state } from './state.js';

export async function refreshStatus() {
  const s = await (await fetch('/api/status')).json();
  $('status').textContent = `${s.entries} entries · ${s.entities} entities`;
  // Mock mode is indistinguishable from real once a reply is on screen, so
  // the banner stays up for the whole session rather than appearing per-call.
  document.body.classList.toggle('mock-mode', !!s.mock);
  if (s.date_style) state.dateStyle = s.date_style;
  // The server owns the clock and reads stamps back in *its* zone
  // (config.parse_stamp), so a browser in another zone would write a wall
  // clock the server then misreads. Carry the difference and stamp
  // against it. Under five minutes it's ignored: `now` carries no
  // seconds, so a small value is that truncation, not a real difference.
  if (s.now) {
    const skew = Date.parse(s.now.replace(' ', 'T')) - Date.now();
    state.clockSkewMs = Math.abs(skew) >= 5 * 60 * 1000 ? skew : 0;
  }
  if (s.tz) state.tz = s.tz;
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