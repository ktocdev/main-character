// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, fmtDate } from './core.js';
import { state } from './state.js';
import { closeSession, hasNewMaterial } from './write.js';
import { showTab } from './main.js';

// ---- history (chapters) ----
// The landing view is the open chapter: the conversation it continues, the
// journal entries, the companion's replies, and every follow-up, one braid.
// Older chapters live in the list, one open at a time. Each chapter reads as
// one section per day, with a jump-to rail (or a sticky pill row on narrower
// screens) when it spans more than one day.
let sessionIndex = null;

export async function loadHistory() {
  sessionIndex = await (await fetch('/api/sessions')).json();
  renderSessionList();
  await showSession(state.sessionSel);
}

function renderSessionList() {
  const el = $('session-list-items');
  el.innerHTML = '';
  const group = name => {
    const h = document.createElement('div');
    h.className = 'list-head';
    h.innerHTML = '<span class="eyebrow"></span>';
    h.firstChild.textContent = name;
    el.appendChild(h);
  };
  const mk = (key, title, meta, open) => {
    const b = document.createElement('button');
    b.className = 'list-item' + (state.sessionSel === key ? ' sel' : '') + (open ? ' open' : '');
    b.innerHTML = '<span class="li-title"></span><span class="li-meta"></span>';
    b.querySelector('.li-title').textContent = title;
    const m = b.querySelector('.li-meta');
    if (open) { const dot = document.createElement('span'); dot.className = 'dot open'; m.appendChild(dot); }
    m.appendChild(document.createTextNode(meta));
    b.onclick = () => { state.sessionSel = key; renderSessionList(); showSession(key); drill(); };
    el.appendChild(b);
  };
  const cur = sessionIndex.current;
  group('open');
  mk('current',
     cur.title || 'new chapter',
     `open · ${cur.message_count} message${cur.message_count === 1 ? '' : 's'}`,
     true);
  if (sessionIndex.sessions.length) group('closed');
  for (const s of sessionIndex.sessions) {
    const key = s.kind === 'archive'
      ? 'a:' + s.id
      : 'c:' + JSON.stringify([s.title, s.start, s.end]);
    mk(key, s.title || '(untitled)',
       s.start === s.end ? fmtDate(s.start) : `${fmtDate(s.start)} → ${fmtDate(s.end)}`, false);
  }
}

function drill() { $('history-pane').classList.add('drilled'); $('session-view').scrollTop = 0; }

// A day's section rule: the date, and `closed · carried forward` on the
// right when the day belongs to the chapter this one continues.
function dayRule(container, date, label, carried) {
  const l = document.createElement('div');
  l.className = 'rule eyebrow day-rule' + (carried ? ' carried' : '');
  const d = document.createElement('span');
  d.textContent = label || fmtDate(date);
  l.appendChild(d);
  if (carried) {
    const m = document.createElement('span');
    m.className = 'meta';
    m.textContent = 'closed · carried forward';
    l.appendChild(m);
  }
  if (date) { l.dataset.tocDate = date; l.dataset.tocKind = 'entry'; }
  container.appendChild(l);
  return l;
}

function addSessionPart(container, label, text, date, carried) {
  dayRule(container, date, label, carried);
  const t = document.createElement('div');
  t.className = carried ? 'session-part carried' : 'session-part';
  t.textContent = text;
  container.appendChild(t);
}

// a part with both sides (backfilled from the Claude export) renders as
// an interleaved chat; otherwise the flat user-side text
// `carried` marks a part the open chapter merely continues: the closed
// chapter it opened with, not something written into it. Only the current-
// chapter views pass it; an archive renders its own parts as itself.
export function renderSessionPart(container, p, carried) {
  const label = `${fmtDate(p.date)} · ${p.title}`;
  if (p.messages) {
    dayRule(container, p.date, label, carried);
    if (p.summary) addPartSummary(container, p, true);
    const box = document.createElement('div');
    if (carried) box.className = 'carried-block';
    addSessionBraid(box, p.messages);
    container.appendChild(box);
  } else {
    if (p.summary) addPartSummary(container, p);
    addSessionPart(container, label, p.text, p.date, carried);
  }
}

function addPartSummary(container, p, afterRule) {
  const s = document.createElement('div');
  s.className = 'part-summary';
  s.textContent = afterRule ? p.summary : `${fmtDate(p.date)} · ${p.summary}`;
  s.dataset.tocDate = p.date;
  s.dataset.tocKind = 'summary';
  container.appendChild(s);
}

// `withStamps` dates the author's own messages. Only the write screen asks
// for it: the archive views carry a date rule per day and a jump-to, so a
// stamp on every entry there would be the third telling of the same thing.
export function addSessionBraid(container, msgs, daySummaries, withStamps) {
  let lastDay = null;
  for (const m of msgs) {
    const day = (m.ts || '').slice(0, 10);
    if (day && day !== lastDay && !withStamps) {
      dayRule(container, day);
      if (daySummaries && daySummaries[day]) {
        addPartSummary(container, daySummaries[day], true);
        delete daySummaries[day];
      }
    } else if (day && day !== lastDay && daySummaries && daySummaries[day]) {
      addPartSummary(container, daySummaries[day]);
      delete daySummaries[day];
    }
    const d = document.createElement('div');
    d.className = 'msg ' + (m.role === 'you' ? 'you' : 'companion');
    d.textContent = m.text;
    // read by .msg.you[data-stamp]::before, so the date sits on the existing
    // label row rather than adding one of its own
    if (withStamps && m.role === 'you') {
      const stamp = fmtDate(m.ts);
      if (stamp) d.dataset.stamp = stamp;
    }
    if (m.dream) d.title = 'dream entry, which lives in the dream realm';
    if (day && day !== lastDay) {
      if (withStamps) { d.dataset.tocDate = day; d.dataset.tocKind = 'entry'; }
      lastDay = day;
    }
    container.appendChild(d);
  }
}

// jump-to: one link per day, as a right rail (>= 1100px) or a sticky pill
// row under the actions. Both only when the chapter spans more than one day.
function buildToc() {
  const rail = $('session-toc'), inline = $('session-jumps');
  rail.innerHTML = '';
  inline.innerHTML = '';
  const els = $('session-body').querySelectorAll('[data-toc-date]');
  const days = [], byDate = {};
  for (const el of els) {
    const d = el.dataset.tocDate;
    if (!byDate[d]) { byDate[d] = {date: d}; days.push(byDate[d]); }
    if (!byDate[d][el.dataset.tocKind]) byDate[d][el.dataset.tocKind] = el;
  }
  if (days.length < 2) return;
  const view = $('session-view');
  const go = el => {
    // scroll the pane, not the window, leaving room for the sticky row
    const top = el.getBoundingClientRect().top - view.getBoundingClientRect().top + view.scrollTop;
    view.scrollTo({top: Math.max(0, top - 56), behavior: 'smooth'});
  };
  const head = document.createElement('span');
  head.className = 'eyebrow';
  head.textContent = 'jump to';
  rail.appendChild(head);
  const ihead = document.createElement('span');
  ihead.className = 'eyebrow';
  ihead.textContent = 'jump to';
  inline.appendChild(ihead);
  for (const d of days) {
    const target = d.entry || d.summary;
    const b = document.createElement('button');
    b.className = 'toc-date';
    const sub = [d.summary && 'summary', d.entry && 'entry'].filter(Boolean).join(' · ');
    b.innerHTML = '<span></span><span class="sub"></span>';
    b.firstChild.textContent = fmtDate(d.date);
    b.lastChild.textContent = sub;
    b.onclick = () => go(target);
    rail.appendChild(b);
    const c = document.createElement('button');
    c.className = 'chip sm';
    c.textContent = shortDate(d.date);
    c.onclick = () => go(target);
    inline.appendChild(c);
  }
}
function shortDate(iso) {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d).toLocaleDateString('en-US', {month: 'short', day: 'numeric'});
}

function setNotice(text) { $('session-notice').textContent = text || ''; }

async function showSession(key) {
  const body = $('session-body');
  body.innerHTML = '';
  $('session-loading').hidden = false;
  $('session-empty').hidden = true;
  $('session-toc').innerHTML = '';
  $('session-jumps').innerHTML = '';
  $('session-close-btn').hidden = true;
  $('session-continue').hidden = true;
  setNotice('');

  if (key === 'current') {
    const r = await (await fetch('/api/sessions/current')).json();
    if (state.sessionSel !== 'current') return;
    $('session-title').textContent = r.parts.length ? r.parts[0].title : 'current chapter';
    $('session-dates').textContent = 'open since ' + fmtDate(r.started);
    $('session-loading').hidden = true;
    $('session-continue').hidden = false;
    for (const p of r.parts) renderSessionPart(body, p, true);
    addSessionBraid(body, r.messages);
    if (!r.parts.length && !r.messages.length) {
      $('session-empty').hidden = false;
    } else if (hasNewMaterial(r.messages)) {
      // the server refuses a close with nothing new in it, so only offer one
      // when there is; a carried-forward part is not new material
      $('session-close-btn').hidden = false;
    }

  } else if (key.startsWith('a:')) {
    const r = await (await fetch('/api/sessions/archive?id=' + encodeURIComponent(key.slice(2)))).json();
    if (state.sessionSel !== key) return;
    $('session-loading').hidden = true;
    if (r.error) { body.textContent = r.error; return; }
    $('session-title').textContent = r.title;
    $('session-dates').textContent = `${fmtDate(r.started)} → closed ${fmtDate(r.closed)}`;
    for (const p of r.parts) {
      if (p.text || p.messages) renderSessionPart(body, p);
    }
    // the closing entries' content lives in the braid below; each day's
    // summary slots in right under that day's rule
    const daySummaries = {};
    for (const p of r.parts) {
      if (!p.text && !p.messages && p.summary) daySummaries[p.date] = p;
    }
    addSessionBraid(body, r.messages, daySummaries);
    // any summary whose day never appears in the braid still shows
    for (const d of Object.keys(daySummaries).sort()) {
      dayRule(body, d);
      addPartSummary(body, daySummaries[d], true);
    }

  } else {
    const [title, start, end] = JSON.parse(key.slice(2));
    const r = await (await fetch(
      `/api/sessions/conversation?title=${encodeURIComponent(title)}` +
      `&start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`
    )).json();
    if (state.sessionSel !== key) return;
    $('session-loading').hidden = true;
    if (r.error) { body.textContent = r.error; return; }
    $('session-title').textContent = r.title || '(untitled)';
    $('session-dates').textContent = r.start === r.end
      ? fmtDate(r.start) : `${fmtDate(r.start)} → ${fmtDate(r.end)}`;
    for (const p of r.parts) renderSessionPart(body, p);
  }
  buildToc();
}

export function init() {
  $('session-close-btn').onclick = async () => {
    if (await closeSession()) setNotice('your side became an entry; tagging, entities and the week’s arc are updating. A new chapter is open in write.');
  };
  $('session-continue').onclick = () => showTab('write');
  $('session-back').onclick = () => $('history-pane').classList.remove('drilled');
}
