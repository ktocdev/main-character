import { $, fmtDate } from './core.js';

// ---- search ----
// Exhaustive, local, free: every matching journal moment. "meaning" ranks
// by semantic similarity; "exact text" lists literal matches newest first.
// Dreams never appear here — they live in their own realm.
let searchMode = 'semantic', searchHits = [], searchSort = 'relevance';

export function init() {
  $('search-mode').onclick = () => {
    searchMode = searchMode === 'semantic' ? 'exact' : 'semantic';
    $('search-mode').textContent = searchMode === 'semantic' ? 'meaning' : 'exact text';
    $('search-mode').classList.toggle('on', searchMode === 'exact');
    if ($('search-q').value.trim() && searchHits.length) runSearch();
  };
  $('search-go').onclick = runSearch;
  $('search-q').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); runSearch(); }
  });
}

async function runSearch() {
  const q = $('search-q').value.trim();
  if (!q) return;
  $('search-status').textContent = 'searching…';
  $('search-results').innerHTML = '';
  try {
    const r = await (await fetch(
      `/api/search?q=${encodeURIComponent(q)}&mode=${searchMode}`
    )).json();
    if (r.error) { $('search-status').textContent = r.error; return; }
    searchHits = r.results;
    searchSort = 'relevance';
    renderSearchResults(q);
  } catch (e) {
    $('search-status').textContent = 'search failed — is the server up?';
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

function renderSearchResults(q) {
  const el = $('search-results');
  el.innerHTML = '';
  const status = $('search-status');
  status.innerHTML = '';
  if (!searchHits.length) {
    status.textContent = searchMode === 'exact'
      ? 'no exact matches — try meaning mode for related passages'
      : 'nothing found';
    return;
  }
  const label = document.createElement('span');
  label.textContent = `${searchHits.length} moment${searchHits.length === 1 ? '' : 's'}`
    + (searchMode === 'exact' ? ' · newest first' : '');
  status.appendChild(label);
  if (searchMode === 'semantic') {
    const sort = document.createElement('button');
    sort.className = 'quiet';
    sort.textContent = searchSort === 'relevance' ? 'sort: best match' : 'sort: newest';
    sort.title = 'toggle between best-match order and newest-first';
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
    const d = document.createElement('div');
    d.className = 'search-hit';
    const h = document.createElement('h3');
    h.textContent = `${fmtDate(hit.date)} — ${hit.title || '(untitled)'}`;
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
      rel.title = "close in meaning — doesn't contain the exact words";
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
