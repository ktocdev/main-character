export const $ = id => document.getElementById(id);
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));
// ---- status ----
import { state } from './state.js';

// ---- dates ----
// One renderer for every surface in the app. The Settings date format
// (MC_DATE_FORMAT, carried to the browser by /api/status) decides the style,
// so history, the write log, search, dreams, categories, patterns and the
// entity view can never drift into showing the same day three ways.
// Storage is untouched by any of this -- stamps are always ISO on disk.
export function fmtDay(y, mo, d) {
  return state.dateStyle === 'short'
    ? `${+mo}/${+d}/${y.slice(2)}`
    : new Date(+y, +mo - 1, +d).toLocaleDateString('en-US',
        {month: 'long', day: 'numeric', year: 'numeric'});
}

// 24-hour as the app stores it -> 12-hour as the journal reads it
export function fmtTime(hh, mi) {
  return `${(+hh % 12) || 12}:${mi}${+hh < 12 ? 'am' : 'pm'}`;
}

// Render a stored stamp for display: the ISO date becomes the configured
// style, and a time part following it becomes 12-hour. Anything else in the
// string is left alone, so this is safe on labels that only contain a date.
export function fmtDate(s) {
  return String(s ?? '')
    .replace(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/g,
      (_, y, mo, d, hh, mi) => `${fmtDay(y, mo, d)} · ${fmtTime(hh, mi)}`)
    .replace(/(\d{4})-(\d{2})-(\d{2})/g, (_, y, mo, d) => fmtDay(y, mo, d));
}

export async function refreshStatus() {
  const s = await (await fetch('/api/status')).json();
  $('status').textContent = `${s.entries} entries · ${s.entities} entities`;
  // Mock mode is indistinguishable from real once a reply is on screen, so
  // the banner stays up for the whole session rather than appearing per-call.
  document.body.classList.toggle('mock-mode', !!s.mock);
  // The seed instance is always mock mode; both states share one banner
  // (CSS shows it on mock-mode, the seed class only makes it louder). The
  // seed message subsumes the canned-replies fact, so it wins the text.
  document.body.classList.toggle('seed-instance', !!s.seed_instance);
  $('app-banner').textContent = s.seed_instance
    ? 'demo journal — sample entries, and the replies are canned. Restart to go back to yours.'
    : 'mock mode — replies are canned, not from Claude. No API calls are being made.';
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
  // An unhandled server exception (500, or anything else that never went
  // through a route's `{"error": ...}` response) won't carry an `error`
  // key -- treat any non-ok status as a failure too, not just one that says so.
  if (!res.ok) {
    let msg = `request failed (${res.status})`;
    try { const r = await res.json(); if (r && r.error) msg = r.error; } catch (e) {}
    alert(msg);
    return null;
  }
  const r = await res.json();
  if (r.error) { alert(r.error); return null; }
  return r;
}