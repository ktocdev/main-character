import { $ } from './core.js';
import { state } from './state.js';
import { closeSession } from './write.js';

// ---- history (sessions) ----
// The landing view is the open chat: the conversation it continues, the
// journal entries, the companion's replies, and every follow-up — one
// braid. Older chats live in the sidebar, one open at a time.
let sessionIndex = null;

export async function loadHistory() {
  sessionIndex = await (await fetch('/api/sessions')).json();
  renderSessionList();
  await showSession(state.sessionSel);
}

// history dates display in the same M/D/YY style as the chat titles;
// replaces the ISO date inside a string, keeping any time part
function fmtDate(s) {
  return (s || '').replace(/(\d{4})-(\d{2})-(\d{2})/,
    (_, y, mo, d) => `${+mo}/${+d}/${y.slice(2)}`);
}

function renderSessionList() {
  const el = $('session-list-items');
  el.innerHTML = '';
  const mk = (key, title, dates, open) => {
    const b = document.createElement('button');
    b.className = 'session-item' + (state.sessionSel === key ? ' sel' : '') + (open ? ' open' : '');
    const t = document.createElement('span');
    t.textContent = title;
    const d = document.createElement('span');
    d.className = 'dates';
    d.textContent = dates;
    b.append(t, d);
    b.onclick = () => { state.sessionSel = key; renderSessionList(); showSession(key); };
    el.appendChild(b);
  };
  const cur = sessionIndex.current;
  mk('current',
     cur.title || 'new chat',
     `open · ${cur.message_count} message${cur.message_count === 1 ? '' : 's'}`,
     true);
  for (const s of sessionIndex.sessions) {
    const key = s.kind === 'archive'
      ? 'a:' + s.id
      : 'c:' + JSON.stringify([s.title, s.start, s.end]);
    mk(key, s.title || '(untitled)',
       s.start === s.end ? fmtDate(s.start) : `${fmtDate(s.start)} → ${fmtDate(s.end)}`, false);
  }
}

function addSessionPart(container, label, text, date) {
  const l = document.createElement('div');
  l.className = 'session-part-label';
  l.textContent = label;
  if (date) { l.dataset.tocDate = date; l.dataset.tocKind = 'entry'; }
  const t = document.createElement('div');
  t.className = 'session-part';
  t.textContent = text;
  container.append(l, t);
}

// a part with both sides (backfilled from the Claude export) renders as
// an interleaved chat; otherwise the flat user-side text
export function renderSessionPart(container, p) {
  const label = `${fmtDate(p.date)} — ${p.title}`;
  if (p.summary) addPartSummary(container, p);
  if (p.messages) {
    const l = document.createElement('div');
    l.className = 'session-part-label';
    l.textContent = label;
    l.dataset.tocDate = p.date;
    l.dataset.tocKind = 'entry';
    container.appendChild(l);
    addSessionBraid(container, p.messages);
  } else {
    addSessionPart(container, label, p.text, p.date);
  }
}

function addPartSummary(container, p) {
  const s = document.createElement('div');
  s.className = 'cat-summary';
  s.textContent = `${fmtDate(p.date)} — ${p.summary}`;
  s.dataset.tocDate = p.date;
  s.dataset.tocKind = 'summary';
  container.appendChild(s);
}

export function addSessionBraid(container, msgs, daySummaries) {
  let lastDay = null;
  for (const m of msgs) {
    const day = (m.ts || '').slice(0, 10);
    if (day && day !== lastDay && daySummaries && daySummaries[day]) {
      addPartSummary(container, daySummaries[day]);
      delete daySummaries[day];
    }
    const d = document.createElement('div');
    d.className = 'msg ' + (m.role === 'you' ? 'you' : 'companion');
    d.textContent = m.text;
    if (m.dream) d.title = 'dream entry — lives in the dream realm';
    if (day && day !== lastDay) {
      d.dataset.tocDate = day;
      d.dataset.tocKind = 'entry';
      lastDay = day;
    }
    container.appendChild(d);
  }
}

// right-hand jump nav: one link per day, with summary/entry sub-links
// when a day has both
function buildToc() {
  const toc = $('session-toc');
  toc.innerHTML = '';
  const els = $('session-body').querySelectorAll('[data-toc-date]');
  if (!els.length) return;
  const days = [], byDate = {};
  for (const el of els) {
    const d = el.dataset.tocDate;
    if (!byDate[d]) { byDate[d] = {date: d}; days.push(byDate[d]); }
    if (!byDate[d][el.dataset.tocKind]) byDate[d][el.dataset.tocKind] = el;
  }
  const head = document.createElement('div');
  head.className = 'toc-head';
  head.textContent = 'jump to';
  toc.appendChild(head);
  const go = el => el.scrollIntoView({behavior: 'smooth', block: 'start'});
  for (const d of days) {
    const b = document.createElement('button');
    b.className = 'toc-date';
    b.textContent = fmtDate(d.date);
    b.onclick = () => go(d.summary || d.entry);
    toc.appendChild(b);
    if (d.summary && d.entry) {
      const mk = (label, el) => {
        const s = document.createElement('button');
        s.className = 'toc-sub';
        s.textContent = label;
        s.onclick = () => go(el);
        toc.appendChild(s);
      };
      mk('summary', d.summary);
      mk('entry', d.entry);
    }
  }
}

async function showSession(key) {
  const body = $('session-body');
  body.textContent = 'loading…';
  $('session-toc').innerHTML = '';
  $('session-close-btn').style.display = 'none';

  if (key === 'current') {
    const r = await (await fetch('/api/sessions/current')).json();
    if (state.sessionSel !== 'current') return;
    $('session-title').textContent = r.parts.length ? r.parts[0].title : 'current chat';
    $('session-dates').textContent = 'open since ' + fmtDate(r.started);
    body.innerHTML = '';
    for (const p of r.parts) renderSessionPart(body, p);
    addSessionBraid(body, r.messages);
    if (!r.parts.length && !r.messages.length) {
      body.textContent = 'nothing here yet — chat or write to begin.';
    } else {
      $('session-close-btn').style.display = 'inline-block';
    }

  } else if (key.startsWith('a:')) {
    const r = await (await fetch('/api/sessions/archive?id=' + encodeURIComponent(key.slice(2)))).json();
    if (state.sessionSel !== key) return;
    if (r.error) { body.textContent = r.error; return; }
    $('session-title').textContent = r.title;
    $('session-dates').textContent = `${fmtDate(r.started)} → closed ${fmtDate(r.closed)}`;
    body.innerHTML = '';
    for (const p of r.parts) {
      if (p.text || p.messages) renderSessionPart(body, p);
    }
    // the closing entries' content lives in the braid below; each day's
    // summary slots in right above that day's messages
    const daySummaries = {};
    for (const p of r.parts) {
      if (!p.text && !p.messages && p.summary) daySummaries[p.date] = p;
    }
    addSessionBraid(body, r.messages, daySummaries);
    // any summary whose day never appears in the braid still shows
    for (const d of Object.keys(daySummaries).sort()) {
      addPartSummary(body, daySummaries[d]);
    }

  } else {
    const [title, start, end] = JSON.parse(key.slice(2));
    const r = await (await fetch(
      `/api/sessions/conversation?title=${encodeURIComponent(title)}` +
      `&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
    )).json();
    if (state.sessionSel !== key) return;
    if (r.error) { body.textContent = r.error; return; }
    $('session-title').textContent = r.title || '(untitled)';
    $('session-dates').textContent = r.start === r.end
      ? fmtDate(r.start) : `${fmtDate(r.start)} → ${fmtDate(r.end)}`;
    body.innerHTML = '';
    for (const p of r.parts) renderSessionPart(body, p);
  }
  buildToc();
}

export function init() {
  $('session-close-btn').onclick = closeSession;
}
