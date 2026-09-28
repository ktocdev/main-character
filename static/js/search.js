// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, fmtDate } from './core.js';
import { toggleEntryText } from './categories.js';

// ---- search ----
// Exhaustive, local, free: every matching journal moment. "meaning" ranks
// by semantic similarity; "exact text" lists literal matches newest first.
// Dreams never appear here — they live in their own realm.
let searchMode = 'semantic', searchHits = [], searchSort = 'relevance';
let searched = false;      // whether a query has run since the tab opened

// Three example queries that fill the box. Generic on purpose: the journal
// is anyone's, so these name the kind of thing search is for rather than a
// person in it.
const PROMPTS = ['times I felt proud of my work', 'the last time I slept badly', 'a walk that cleared my head'];

function setMode(exact) {
  searchMode = exact ? 'exact' : 'semantic';
  const b = $('search-mode');
  b.textContent = exact ? 'exact text' : 'meaning';
  b.classList.toggle('on', exact);
  b.setAttribute('aria-pressed', String(exact));
}

// main.js calls this when the tab opens: nothing searched yet means the
// empty state, which says what the two modes do.
export function showTab() {
  if (!searched) renderEmpty();
}

export function init() {
  setMode(false);
  $('search-mode').onclick = () => {
    setMode(searchMode === 'semantic');
    if ($('search-q').value.trim() && searched) runSearch();
  };
  $('search-bar').onsubmit = e => { e.preventDefault(); runSearch(); };
}

async function runSearch() {
  const q = $('search-q').value.trim();
  if (!q) return;
  searched = true;
  const status = $('search-status');
  status.innerHTML = '<span>reading</span><span class="dots">···</span>';
  $('search-results').innerHTML = '';
  try {
    const r = await (await fetch(
      `/api/search?q=${encodeURIComponent(q)}&mode=${searchMode}`
    )).json();
    if (r.error) { status.textContent = r.error; return; }
    searchHits = r.results;
    searchSort = 'relevance';
    renderSearchResults(q);
  } catch {
    status.textContent = 'search failed. Is the server up?';
  }
}

// safe highlighting: build text nodes, wrap query words in <mark>
function highlightInto(el, text, q) {
  const terms = q.toLowerCase().split(/\s+/).filter(w => w.length > 2);
  el.textContent = '';
  if (!terms.length) { el.textContent = text; return; }
  const esc = terms.map(t => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const splitter = new RegExp('(' + esc.join('|') + ')', 'ig');
  const isTerm = new RegExp('^(?:' + esc.join('|') + ')$', 'i');
  for (const part of text.split(splitter)) {
    if (!part) continue;
    if (isTerm.test(part)) {
      const m = document.createElement('mark');
      m.textContent = part;
      el.appendChild(m);
    } else {
      el.appendChild(document.createTextNode(part));
    }
  }
}

// An eyebrow, one dim sentence and a row of pills: the shape every empty
// state in the app shares.
function suggestBlock(eyebrow, html, pills) {
  const box = document.createElement('div');
  box.className = 'suggest';
  box.innerHTML = `<div class="eyebrow">${eyebrow}</div><p>${html}</p>`;
  if (pills.length) {
    const row = document.createElement('div');
    row.className = 'pills';
    for (const [label, go] of pills) {
      const b = document.createElement('button');
      b.className = 'chip';
      b.textContent = label;
      b.onclick = go;
      row.appendChild(b);
    }
    box.appendChild(row);
  }
  return box;
}

function renderEmpty() {
  $('search-status').innerHTML = '';
  const el = $('search-results');
  el.innerHTML = '';
  el.appendChild(suggestBlock('find a moment',
    'Search every entry you’ve written. It runs on this computer, so it is free and instant and never calls Claude. '
    + '<em>meaning</em> finds passages like the one you describe; <em>exact text</em> finds the literal words, newest first. '
    + 'To ask a question and get an answer, use chat.',
    PROMPTS.map(p => [p, () => { $('search-q').value = p; runSearch(); }])));
}

function renderNone(q) {
  const el = $('search-results');
  el.innerHTML = '';
  const quoted = document.createElement('span');
  quoted.textContent = q;
  if (searchMode === 'exact') {
    el.appendChild(suggestBlock('nothing yet',
      `No entry mentions “${quoted.innerHTML}” in so many words. Try <em>meaning</em> to find passages that are about it without saying it.`,
      [['search by meaning instead', () => { setMode(false); runSearch(); }]]));
  } else {
    el.appendChild(suggestBlock('nothing yet',
      `Nothing in the journal reads like “${quoted.innerHTML}”. Try other words, or <em>exact text</em> for a name or phrase you know you wrote.`,
      [['search the exact words instead', () => { setMode(true); runSearch(); }]]));
  }
}

function renderSearchResults(q) {
  const el = $('search-results');
  el.innerHTML = '';
  const status = $('search-status');
  status.innerHTML = '';
  if (!searchHits.length) { renderNone(q); return; }
  const label = document.createElement('span');
  label.textContent = `${searchHits.length} moment${searchHits.length === 1 ? '' : 's'}`
    + (searchMode === 'exact' ? ' · newest first' : '');
  status.appendChild(label);
  if (searchMode === 'semantic') {
    const sort = document.createElement('button');
    sort.className = 'quiet';
    sort.textContent = searchSort === 'relevance' ? 'sort: best match' : 'sort: newest';
    sort.title = 'switch between best match and newest first';
    sort.onclick = () => {
      searchSort = searchSort === 'relevance' ? 'newest' : 'relevance';
      renderSearchResults(q);
    };
    status.appendChild(sort);
  }

  const hits = [...searchHits];
  if (searchMode === 'semantic' && searchSort === 'newest') {
    hits.sort((a, b) => b.date.localeCompare(a.date));
  }
  for (const hit of hits) {
    const d = document.createElement('article');
    d.className = 'search-hit';
    const h = document.createElement('h3');
    const t = document.createElement('span');
    t.textContent = `${fmtDate(hit.date)} — ${hit.title || '(untitled)'}`;
    h.appendChild(t);
    h.title = 'show the full entry';
    if (hit.hits > 1) {
      const n = document.createElement('span');
      n.className = 'hits-n';
      n.textContent = `×${hit.hits}`;
      h.appendChild(n);
    }
    if (hit.match === 'related') {
      const rel = document.createElement('span');
      rel.className = 'related-tag';
      rel.textContent = 'related';
      rel.title = "close in meaning, though it doesn't contain the exact words";
      h.appendChild(rel);
    }
    h.onclick = () => toggleEntryText(d, hit);
    d.appendChild(h);
    const s = document.createElement('div');
    s.className = 'search-snip';
    highlightInto(s, hit.snippet, q);
    d.appendChild(s);
    el.appendChild(d);
  }
}
