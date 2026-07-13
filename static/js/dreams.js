import { $, api } from './core.js';

// ---- dreams ----
export async function loadDreams() {
  renderDreams(await (await fetch('/api/dreams')).json());
}

function renderDreams(r) {
  $('dream-status').textContent = r.dreams.length
    ? `${r.dreams.length} dreams on record`
    : 'no dreams extracted yet';
  $('dream-weather').textContent = r.weather || '';
  const el = $('dream-list');
  el.innerHTML = '';
  for (const d of r.dreams) {
    const card = document.createElement('div');
    card.className = 'dream-card';
    const h = document.createElement('h3');
    h.textContent = d.date;
    for (const t of d.tones) {
      const chip = document.createElement('span');
      chip.className = 'dream-tone';
      chip.textContent = t;
      h.appendChild(chip);
    }
    card.appendChild(h);
    const n = document.createElement('p');
    n.textContent = d.narrative;
    card.appendChild(n);
    const cast = [...d.people, ...d.places];
    if (cast.length) {
      const c = document.createElement('div');
      c.className = 'dream-cast';
      c.textContent = cast.join(' · ');
      card.appendChild(c);
    }
    if (d.interpretation) {
      const i = document.createElement('p');
      i.className = 'dream-read';
      i.textContent = 'your read: ' + d.interpretation;
      card.appendChild(i);
    }
    el.appendChild(card);
  }
}

export function init() {
  $('dream-extract').onclick = async () => {
    const b = $('dream-extract');
    b.disabled = true;
    b.textContent = 'extracting… (~10s per unscanned entry)';
    try {
      const r = await api('/api/dreams/extract', {});
      if (r) { renderDreams(r); b.textContent = `${r.dreams.length} dreams on record`; }
    } finally {
      setTimeout(() => { b.textContent = 'extract dreams from history'; b.disabled = false; }, 4000);
    }
  };
}
