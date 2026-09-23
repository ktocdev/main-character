// SPDX-License-Identifier: AGPL-3.0-or-later
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
  // `entries` counts saved (and imported) entries: a save adds one at once,
  // a close only moves it into journal memory. The ones still in the open
  // chat are counted -- and saved -- already; the tooltip says where they are.
  $('status').textContent = `${s.entries} entries · ${s.entities} entities`;
  const open = s.open_entries || 0;
  $('status').title = open
    ? `${open} ${open === 1 ? 'entry' : 'entries'} in this chat, added to journal memory when you close it`
    : '';
  // Canned replies only ever run against the demo, so the seed instance is
  // the whole banner condition now -- there is no mode where the replies are
  // canned and the journal is yours. `mock` is still reported by /api/status
  // and still drives the cost panel's note, but it no longer shows a bar of
  // its own. The bar stays up for the whole session rather than appearing
  // per-call, because a reply on screen is indistinguishable from a real one.
  document.body.classList.toggle('seed-instance', !!s.seed_instance);
  // Only the in-browser backend of the published web demo sets this: there
  // is no server, so base.css hides what only a server could do.
  document.body.classList.toggle('web-demo', !!s.web_demo);
  $('app-banner').textContent = s.web_demo
    ? 'web demo... sample journal, canned replies.'
    : 'demo journal... sample journal, canned replies.';
  // A server too old to report this sends nothing; `!== false` reads that
  // as configured rather than as a fresh clone, so the wizard cannot open
  // over a journal that has been working for months.
  state.configured = s.configured !== false;
  state.demoBuilt = s.demo_built !== false;
  state.embedderCached = s.embedder_cached === undefined
    ? null : s.embedder_cached;
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
// Build the demo journal if it is not there yet (Phase 3 item 5).
//
// Shared by the wizard's demo door and Settings' "load demo journal", which
// both used to dead-end on a 409 telling the reader to go and run a command
// in a terminal -- the one place a first-run flow cannot follow them. The
// server does the building; this is the waiting and the saying so.
//
// `report(text, kind)` renders progress wherever the caller shows messages,
// because the two callers write into different elements with different class
// vocabularies. Returns whether the demo is now ready to restart into.
// How long building the demo will take, as a fragment the three places that
// mention it can drop into their own sentence. One source, because they used
// to disagree, and because the estimate is the part most likely to change.
//
// The reason there is more than one answer: chroma fetches its embedding
// model (about 90MB) at the first embed on the machine, not at install and
// not per journal. From the wizard that is almost always this build, since a
// fresh clone has embedded nothing yet. From Settings it usually is not,
// because writing a single entry already paid for it. Saying which one the
// reader is in beats listing both and leaving them to work it out.
export function demoBuildWait() {
  if (state.embedderCached === false) {
    return 'a few minutes, while the 90MB embedding model downloads '
      + '(once per machine, not once per journal)';
  }
  if (state.embedderCached === true) return 'around twenty seconds';
  return 'around twenty seconds, or a few minutes if the embedding model '
    + 'still has to download';
}

export async function installDemo(report) {
  const started = Date.now();
  let timer = null;
  // Nothing on screen for the first beat. An already-built demo answers in
  // about a millisecond, and a "building…" line flashing past would be a
  // claim about work that never happened. Past that, the elapsed count is
  // the only honest progress available: the build is a child process with
  // no channel back, so a bar would be inventing a fraction it cannot know.
  const hold = setTimeout(() => {
    const tick = () => report('building the demo journal of 32 entries and 1 dream entry. '
      + 'The newest 3 will open in the chat (' + Math.round((Date.now() - started) / 1000)
      + 's). Expect ' + demoBuildWait() + '.');
    tick();
    timer = setInterval(tick, 1000);
  }, 400);
  try {
    const res = await fetch('/api/setup/install-demo', {method: 'POST'});
    const body = await res.json();
    if (!res.ok || body.error) throw new Error(body.error || 'it did not finish');
    state.demoBuilt = true;
    // Only when something was actually built: on the trips after the first
    // there is nothing to announce, and the restart says its own piece next.
    if (body.built) report('demo journal built. Opening it…');
    return true;
  } catch (e) {
    report('Could not build the demo. ' + (e.message || e), 'error');
    return false;
  } finally {
    clearTimeout(hold);
    if (timer) clearInterval(timer);
  }
}

// Save a file the server hands back. Through fetch rather than by pointing
// window.location at the route, so it works the same whether the answer
// comes from the server or from the web demo's in-page backend. The name the
// response gives wins over `filename`.
export async function download(url, filename) {
  const res = await fetch(url, {cache: 'no-store'});
  if (!res.ok) {
    let msg = `download failed (${res.status})`;
    try { const r = await res.json(); if (r && r.error) msg = r.error; } catch (e) {}
    alert(msg);
    return;
  }
  const named = /filename="?([^";]+)"?/i.exec(res.headers.get('content-disposition') || '');
  const href = URL.createObjectURL(await res.blob());
  const a = document.createElement('a');
  a.href = href;
  a.download = named ? named[1] : filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
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
