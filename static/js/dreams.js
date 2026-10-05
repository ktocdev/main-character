// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, fmtDate } from './core.js';
import { busyLabel } from './categories.js';
import { showTab } from './main.js';
import { showEntity } from './entities.js';

// ---- dreams ----
// Their own realm: a separate collection waking questions never surface.
// Cards with a tone filter over them; the cast links into entities.
let data = {dreams: [], weather: ''};
let toneFilter = null;

export async function loadDreams() {
  data = await (await fetch('/api/dreams')).json();
  renderDreams();
}

function notice(text) { $('dream-notice').textContent = text || ''; }

function renderDreams() {
  const all = data.dreams || [];
  $('dream-status').textContent = all.length
    ? `${all.length} dream${all.length === 1 ? '' : 's'} on record`
    : 'no dreams extracted yet';

  // weather: the companion's line, plus how many dreams the last 14 days hold
  const w = $('dream-weather');
  w.hidden = !all.length;
  if (all.length) {
    const cutoff = new Date(Date.now() - 14 * 86400000).toISOString().slice(0, 10);
    const recent = all.filter(d => (d.date || '') >= cutoff).length;
    w.innerHTML = '<span class="w"></span><span class="m"></span>';
    // The server's line already says the window and the count; the meta
    // beside it says both, so strip them from the italic part.
    const weather = (data.weather || 'Dream weather: clear')
      .replace(/\s*\(last 14 days\)/i, '').replace(/\s*\(\d+ dreams?\)\s*$/i, '');
    w.querySelector('.w').textContent = weather;
    w.querySelector('.m').textContent = `last 14 days · ${recent ? recent + (recent === 1 ? ' dream' : ' dreams') : 'no dreams recorded'}`;
  }

  // the tone filter
  const tones = $('dream-tones');
  tones.innerHTML = '';
  tones.hidden = !all.length;
  const names = [...new Set(all.flatMap(d => d.tones || []))];
  if (toneFilter && !names.includes(toneFilter)) toneFilter = null;
  const chip = (label, count, key) => {
    const b = document.createElement('button');
    b.className = 'chip sm' + (toneFilter === key ? ' on' : '');
    b.innerHTML = '<span></span><span class="n"></span>';
    b.firstChild.textContent = label;
    b.lastChild.textContent = count;
    b.onclick = () => { toneFilter = key; renderDreams(); };
    tones.appendChild(b);
  };
  chip('all', all.length, null);
  for (const t of names) chip(t, all.filter(d => (d.tones || []).includes(t)).length, t);

  const shown = all.filter(d => !toneFilter || (d.tones || []).includes(toneFilter));
  const el = $('dream-list');
  el.innerHTML = '';
  for (const d of shown) el.appendChild(card(d));

  const empty = $('dream-empty');
  if (shown.length) empty.hidden = true;
  else {
    empty.hidden = false;
    empty.innerHTML = '<div class="eyebrow"></div><p></p>';
    empty.querySelector('.eyebrow').textContent = all.length ? 'none with this tone' : 'no dreams yet';
    empty.querySelector('p').textContent = all.length
      ? 'No dream carries this tone. Pick another above.'
      : 'Nothing here until an entry mentions a dream. Write one the way you always have ("last night I dreamt…") and it arrives when that chapter is closed, or press extract dreams from history to scan what is already written.';
  }
}

function card(d) {
  const c = document.createElement('article');
  c.className = 'card dream-card';
  const head = document.createElement('div');
  head.className = 'head';
  const h = document.createElement('h3');
  h.textContent = fmtDate(d.date);
  head.appendChild(h);
  for (const t of d.tones || []) {
    const b = document.createElement('button');
    b.className = 'badge lg accent';
    b.textContent = t;
    b.title = `show only ${t} dreams`;
    b.onclick = () => { toneFilter = t; renderDreams(); };
    head.appendChild(b);
  }
  c.appendChild(head);
  const n = document.createElement('p');
  n.textContent = d.narrative;
  c.appendChild(n);
  const cast = [...(d.people || []), ...(d.places || [])];
  if (cast.length) {
    const row = document.createElement('div');
    row.className = 'dream-cast';
    row.innerHTML = '<span class="sc-label">cast</span>';
    for (const name of cast) {
      const b = document.createElement('button');
      b.textContent = name;
      b.title = `open ${name} in entities`;
      b.onclick = () => { showTab('entities'); showEntity(name); };
      row.appendChild(b);
    }
    c.appendChild(row);
  }
  if (d.interpretation) {
    const i = document.createElement('p');
    i.className = 'dream-read';
    i.innerHTML = '<span class="sc-label">your read</span>';
    i.appendChild(document.createTextNode(d.interpretation));
    c.appendChild(i);
  }
  return c;
}

export function init() {
  $('dream-extract').onclick = async () => {
    const b = $('dream-extract');
    b.disabled = true;
    busyLabel(b, 'extracting');
    $('dream-status').innerHTML = 'reading entries that mention a dream <span class="dots">···</span>';
    notice('');
    try {
      const before = (data.dreams || []).length;
      const r = await api('/api/dreams/extract', {});
      if (r) {
        data = r;
        renderDreams();
        const added = (r.dreams || []).length - before;
        notice(added > 0 ? `${added} new dream${added === 1 ? '' : 's'} on record.`
          : r.dreams.length ? 'nothing new. Every entry that mentions a dream is already scanned.'
          : 'no entry mentions a dream yet.');
      } else renderDreams();
    } finally {
      b.textContent = 'extract dreams from history';
      b.disabled = false;
    }
  };
}
