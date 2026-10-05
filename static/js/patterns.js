// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, fmtDate } from './core.js';
import { busyLabel } from './categories.js';

// ---- patterns ----
// Recurring shapes the companion named across the weekly arcs and domain
// documents: cards, a kind filter over them, dismiss on each.
let library = {generated: null, patterns: []};
let kindFilter = null;
const open = new Set();      // which cards show their instances

export async function loadPatterns() {
  library = await (await fetch('/api/patterns')).json();
  renderPatterns();
}

function notice(text) { $('pat-notice').textContent = text || ''; }

function renderPatterns() {
  const all = library.patterns || [];
  $('pat-status').textContent = library.generated
    ? `detected ${fmtDate(library.generated.replace('T', ' '))} · ${all.length} pattern${all.length === 1 ? '' : 's'}`
    : 'not detected yet';

  // the kind filter: all N · kind N …
  const kinds = $('pat-kinds');
  kinds.innerHTML = '';
  kinds.hidden = !all.length;
  if (kindFilter && !all.some(p => p.kind === kindFilter)) kindFilter = null;
  const names = [...new Set(all.map(p => p.kind))];
  const chip = (label, count, key) => {
    const b = document.createElement('button');
    b.className = 'chip sm' + (kindFilter === key ? ' on' : '');
    b.innerHTML = '<span></span><span class="n"></span>';
    b.firstChild.textContent = label;
    b.lastChild.textContent = count;
    b.onclick = () => { kindFilter = key; renderPatterns(); };
    kinds.appendChild(b);
  };
  chip('all', all.length, null);
  for (const k of names) chip(k, all.filter(p => p.kind === k).length, k);

  const shown = all.filter(p => !kindFilter || p.kind === kindFilter);
  const el = $('pat-list');
  el.innerHTML = '';
  for (const p of shown) el.appendChild(card(p));

  // empty, all dismissed, or none of this kind
  const empty = $('pat-empty');
  if (shown.length) { empty.hidden = true; }
  else {
    empty.hidden = false;
    const eyebrow = !library.generated ? 'no patterns yet' : all.length ? 'none of this kind' : 'nothing detected';
    const text = !library.generated
      ? 'The companion needs a few weeks of entries and at least one domain document before recurring shapes are worth naming. Come back after that, or press detect patterns to check.'
      : all.length
        ? 'No pattern of this kind is left. Pick another kind above.'
        : 'Nothing recurring was found in the last pass, or every pattern has been dismissed. Dismissed patterns come back on their own only if new evidence shows up; detect patterns runs a fresh pass.';
    empty.innerHTML = '<div class="eyebrow"></div><p></p>';
    empty.querySelector('.eyebrow').textContent = eyebrow;
    empty.querySelector('p').textContent = text;
  }
  $('pat-foot').hidden = !all.length;
}

function card(p) {
  const d = document.createElement('article');
  d.className = 'card pattern';
  const head = document.createElement('div');
  head.className = 'head';
  const h = document.createElement('h3');
  h.textContent = p.name;
  const kind = document.createElement('span');
  kind.className = 'kind';
  kind.textContent = `${p.kind} · ${p.confidence} confidence`;
  const x = document.createElement('button');
  x.className = 'quiet danger dismiss';
  x.textContent = 'dismiss';
  x.title = 'not a real pattern. Hide it, and it stays hidden across re-detections unless new evidence shows up';
  x.onclick = async () => {
    if (await api('/api/patterns/dismiss', {name: p.name})) {
      library.patterns = library.patterns.filter(q => q !== p);
      renderPatterns();
      notice(`dismissed “${p.name}”. It only returns if new evidence shows up.`);
    }
  };
  head.append(h, kind, x);
  d.appendChild(head);
  const desc = document.createElement('p');
  desc.textContent = p.description;
  d.appendChild(desc);
  const trig = document.createElement('p');
  trig.className = 'pat-trigger';
  trig.innerHTML = '<span class="sc-label">trigger</span>';
  trig.appendChild(document.createTextNode(p.trigger));
  d.appendChild(trig);
  const n = (p.instances || []).length;
  if (n) {
    const isOpen = open.has(p.name);
    const t = document.createElement('button');
    t.className = 'pat-toggle';
    t.setAttribute('aria-expanded', String(isOpen));
    t.innerHTML = `<span>${n} instance${n === 1 ? '' : 's'}</span><span class="caret">${isOpen ? '▴' : '▾'}</span>`;
    const inst = document.createElement('div');
    inst.className = 'pat-instances';
    inst.hidden = !isOpen;
    for (const i of p.instances) {
      const dd = document.createElement('span');
      dd.className = 'd';
      dd.textContent = fmtDate(i.date);
      const nn = document.createElement('span');
      nn.className = 'n';
      nn.textContent = i.note;
      inst.append(dd, nn);
    }
    t.onclick = () => {
      const now = inst.hidden;
      inst.hidden = !now;
      t.setAttribute('aria-expanded', String(now));
      t.querySelector('.caret').textContent = now ? '▴' : '▾';
      if (now) open.add(p.name); else open.delete(p.name);
    };
    d.append(t, inst);
  }
  return d;
}

export function init() {
  $('pat-build').onclick = async () => {
    const b = $('pat-build');
    b.disabled = true;
    busyLabel(b, 'detecting');
    $('pat-status').innerHTML = 'reading arcs and domain documents <span class="dots">···</span>';
    notice('');
    try {
      const r = await api('/api/patterns/build', {});
      if (r) {
        const before = (library.patterns || []).length;
        library = r;
        renderPatterns();
        notice(r.skipped ? 'nothing has changed since the last pass; skipped.'
          : `${r.patterns.length} pattern${r.patterns.length === 1 ? '' : 's'} on record${r.patterns.length !== before ? ', updated' : ''}.`);
      } else renderPatterns();
    } finally {
      b.textContent = 'detect patterns';
      b.disabled = false;
    }
  };
}
