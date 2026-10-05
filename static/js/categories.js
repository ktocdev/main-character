// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, esc, fmtDate } from './core.js';

// ---- categories ----
// A list of categories (built-in and yours) on the left; on the right, the
// one you picked: its domain document, then every entry tagged with it and
// that entry's tag editor. Proposals from the organic scan sit at the top of
// the list and open to a confirm / not now / dismiss card.
let catIndex = null, catSelected = null, propSelected = null;
let organicState = {proposals: [], custom: []};

export async function loadCategories() {
  const [ci, os] = await Promise.all([
    fetch('/api/categories').then(r => r.json()),
    fetch('/api/organic').then(r => r.json()),
  ]);
  catIndex = ci;
  organicState = os;
  renderCategories();
}

const label = n => n === '__untagged' ? 'untagged' : n.replace('_', ' ');

function renderCategories() {
  const status = $('cat-status');
  status.textContent = catIndex.tagged < catIndex.total
    ? `${catIndex.tagged} of ${catIndex.total} tagged`
    : `all ${catIndex.total} tagged`;
  renderProposals();
  renderList();
  renderDetail();
}

// The row: name, a dashed `yours` pill on hand-made categories, the count.
function catRow(name, count, custom) {
  const b = document.createElement('button');
  b.className = 'list-item' + (catSelected === name && !propSelected ? ' sel' : '');
  b.innerHTML = '<span class="li-title"></span>' + (custom ? '<span class="yours">yours</span>' : '')
    + '<span class="li-meta right"></span>';
  b.querySelector('.li-title').textContent = label(name);
  b.querySelector('.li-meta').textContent = count;
  if (custom) b.title = 'a category of yours';
  b.onclick = () => { catSelected = name; propSelected = null; notice(''); renderCategories(); drill(); };
  return b;
}

function renderList() {
  const list = $('cat-rows');
  list.innerHTML = '';
  const customNames = new Set(catIndex.custom || []);
  const untagged = Object.values(catIndex.conversations)
    .filter(c => !Object.keys(c.categories).length).length;
  const head = document.createElement('div');
  head.className = 'list-head';
  head.innerHTML = '<span class="eyebrow">categories</span><span class="meta">by entries</span>';
  list.appendChild(head);
  for (const [name, count] of Object.entries(catIndex.counts).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))) {
    list.appendChild(catRow(name, count, customNames.has(name)));
  }
  if (untagged) list.appendChild(catRow('__untagged', untagged, false));
}

function renderProposals() {
  const el = $('cat-proposed');
  el.innerHTML = '';
  const props = organicState.proposals || [];
  if (!props.length) return;
  const head = document.createElement('div');
  head.className = 'list-head';
  head.innerHTML = '<span class="eyebrow">proposed</span><span class="meta"></span>';
  head.querySelector('.meta').textContent = props.length;
  el.appendChild(head);
  for (const p of props) {
    const b = document.createElement('button');
    b.className = 'list-item' + (propSelected === p.id ? ' sel' : '');
    b.innerHTML = '<span class="dot dashed"></span><span class="li-title"></span><span class="li-meta right"></span>';
    b.querySelector('.li-title').textContent = p.name;
    b.querySelector('.li-meta').textContent = `${p.confidence} · ${p.weeks}w`;
    b.onclick = () => { propSelected = p.id; notice(''); renderCategories(); drill(); };
    el.appendChild(b);
  }
}

// ---- the detail pane ----
// The notice line lives inside the detail column, which is rebuilt on every
// render, so the text is kept here and written back after each render.
let noticeText = '';
function notice(text) {
  noticeText = text || '';
  const n = $('cat-notice');
  if (n) n.textContent = noticeText;
}
function drill() { $('cat-split').classList.add('drilled'); $('cat-detail').scrollTop = 0; }
function undrill() { $('cat-split').classList.remove('drilled'); }

function renderDetail() {
  const prop = propSelected && (organicState.proposals || []).find(p => p.id === propSelected);
  const col = $('cat-detail-col'), none = $('cat-none');
  if (prop) { renderProposal(prop); col.hidden = false; none.hidden = true; notice(noticeText); return; }
  if (!catSelected || !(catSelected in catIndex.counts || catSelected === '__untagged')) {
    catSelected = null;
    col.hidden = true; none.hidden = false; undrill();
    return;
  }
  col.hidden = false; none.hidden = true;
  renderCategory(catSelected);
  notice(noticeText);
}

function renderProposal(p) {
  const col = $('cat-detail-col');
  col.innerHTML = '<button class="text back-link">‹ all categories</button>'
    + '<div class="eyebrow">proposed category</div>'
    + '<div class="title-row prop-title"><h2 class="title"></h2><span class="title-meta"></span></div>'
    + '<div class="prop-members"></div><p class="prop-reason"></p>'
    + '<div class="prop-actions"></div>'
    + '<div id="cat-notice" class="notice-line"></div>'
    + '<p class="footnote prop-foot">confirm makes this a category of yours, seeded with the names above as keywords. not now keeps it in the list; dismiss forgets it unless new members appear.</p>';
  col.querySelector('.back-link').onclick = undrill;
  col.querySelector('h2').textContent = p.name;
  col.querySelector('.title-meta').textContent = `${p.confidence} confidence · ${p.weeks} weeks`;
  col.querySelector('.prop-reason').textContent = p.reason;
  const mem = col.querySelector('.prop-members');
  for (const m of p.members) {
    const c = document.createElement('span');
    c.className = 'chip sm';
    c.textContent = m;
    mem.appendChild(c);
  }
  const row = col.querySelector('.prop-actions');
  const act = (text, action, cls, title) => {
    const b = document.createElement('button');
    b.className = cls;
    b.textContent = text;
    b.title = title;
    b.onclick = async () => {
      if (await api('/api/organic/respond', {id: p.id, action})) {
        propSelected = null;
        if (action === 'confirm') catSelected = p.name.toLowerCase().replace(/\s+/g, '_');
        await loadCategories();
        if (action === 'confirm' && !(catSelected in catIndex.counts)) catSelected = null;
      }
    };
    row.appendChild(b);
  };
  act('confirm', 'confirm', 'filled', 'create this category and tag the entries that mention these members');
  act('not now', 'not_now', 'quiet', 'defer until the cluster gains a new member');
  act('dismiss', 'dismiss', 'quiet danger', 'not a real category. It stays hidden unless new members appear');
}

function renderCategory(name) {
  const col = $('cat-detail-col');
  const customDef = (organicState.custom || []).find(c => c.name === name);
  const rows = Object.entries(catIndex.conversations)
    .filter(([, c]) => name === '__untagged'
      ? !Object.keys(c.categories).length
      : name in c.categories)
    .sort((a, b) => b[1].date.localeCompare(a[1].date));
  col.innerHTML = '<button class="text back-link">‹ all categories</button>'
    + '<div class="title-row"><h2 class="title"></h2></div>'
    + '<div class="title-meta"></div>'
    + '<div id="cat-notice" class="notice-line"></div>';
  col.querySelector('.back-link').onclick = undrill;
  col.querySelector('h2').textContent = label(name);
  const through = rows.length ? ` · through ${fmtDate(rows[0][1].date)}` : '';
  col.querySelector('.title-meta').textContent = `${rows.length} entr${rows.length === 1 ? 'y' : 'ies'}${through}`;

  // a category of yours: remove it, edit its keywords and members
  if (customDef) {
    const rm = document.createElement('button');
    rm.className = 'quiet sm danger';
    rm.textContent = 'remove category';
    rm.title = 'delete this category. Its tags disappear; entries are untouched';
    rm.onclick = async () => {
      if (!confirm(`delete category "${customDef.name}"?`)) return;
      if (await api('/api/organic/custom/delete', {name: customDef.name})) { catSelected = null; loadCategories(); }
    };
    col.querySelector('.title-row').appendChild(rm);
    col.insertBefore(tagRow('keywords', customDef.keywords, 'add keyword…',
      kw => ({remove_keyword: kw}), kw => ({add_keyword: kw}), customDef.name), $('cat-notice'));
    col.insertBefore(tagRow('members', customDef.members, 'add member…',
      m => ({remove_member: m}), m => ({add_member: m}), customDef.name), $('cat-notice'));
  }

  // the domain document
  if (name !== '__untagged') {
    const rule = document.createElement('div');
    rule.className = 'rule eyebrow';
    rule.style.marginTop = '1.6rem';
    rule.innerHTML = '<span>domain document</span><span class="meta"></span>';
    col.appendChild(rule);
    const doc = document.createElement('div');
    doc.className = 'cat-doc';
    doc.innerHTML = '<div class="working">reading <span class="dots">···</span></div>';
    col.appendChild(doc);
    fetch('/api/summaries/domain?name=' + encodeURIComponent(name))
      .then(r => r.json())
      .then(r => {
        if (r.error) throw new Error(r.error);
        renderDoc(doc, rule.querySelector('.meta'), r.doc);
      })
      .catch(() => {
        doc.innerHTML = '';
        const p = document.createElement('p');
        p.className = 'cat-nodoc';
        p.textContent = 'Not written yet. The companion drafts a domain document once a category has a few tagged entries, and rewrites it as more arrive.';
        doc.appendChild(p);
      });
  }

  // the entries
  const entries = document.createElement('div');
  entries.id = 'cat-entries';
  const erule = document.createElement('div');
  erule.className = 'rule eyebrow';
  erule.textContent = 'entries';
  entries.appendChild(erule);
  if (!rows.length) {
    const p = document.createElement('p');
    p.className = 'cat-none';
    p.textContent = name === '__untagged'
      ? 'Every entry carries at least one tag.'
      : 'No entries carry this tag yet. Add it from any entry’s + tag… control, or wait for the next tagging pass.';
    entries.appendChild(p);
  }
  for (const [key, c] of rows) entries.appendChild(entryRow(key, c, name));
  col.appendChild(entries);
}

// The document is markdown from the summarizer: a comment, a `# name (N
// entries, through DATE)` line, a title, then prose with **bold** leads.
function renderDoc(box, meta, text) {
  const lines = text.split('\n').filter(l => !l.startsWith('<!--'));
  const head = lines.find(l => /^# .*through \d{4}-\d{2}-\d{2}\)/.test(l));
  if (head) {
    const m = head.match(/through (\d{4}-\d{2}-\d{2})/);
    if (m) meta.textContent = `through ${fmtDate(m[1])}`;
  }
  const body = lines.filter(l => !l.startsWith('# ')).join('\n').trim();
  box.innerHTML = '';
  for (const para of body.split(/\n\s*\n/)) {
    const p = document.createElement('p');
    // **lead:** at the start of a paragraph becomes a small heading run-in
    const mm = para.match(/^\*\*([^*]+)\*\*\s*(.*)$/s);
    if (mm) {
      const b = document.createElement('b');
      b.textContent = mm[1] + ' ';
      p.appendChild(b);
      p.appendChild(document.createTextNode(mm[2]));
    } else {
      p.textContent = para;
    }
    box.appendChild(p);
  }
}

// keywords / members of a category of yours: removable tags + an add field
function tagRow(title, items, placeholder, removePayload, addPayload, catName) {
  const row = document.createElement('div');
  row.className = 'cat-kw tag-editor';
  const l = document.createElement('span');
  l.className = 'tag-label';
  l.textContent = title;
  row.appendChild(l);
  for (const it of items) {
    const t = document.createElement('span');
    t.className = 'tag';
    t.innerHTML = '<span></span><button class="x" aria-label="remove">×</button>';
    t.firstChild.textContent = it;
    t.querySelector('.x').title = `remove this ${title.slice(0, -1)}`;
    t.querySelector('.x').onclick = async () => {
      if (await api('/api/organic/custom/edit', {name: catName, ...removePayload(it)})) loadCategories();
    };
    row.appendChild(t);
  }
  const inp = document.createElement('input');
  inp.type = 'text';
  inp.className = 'input-2xs';
  inp.placeholder = placeholder;
  inp.onkeydown = async e => {
    if (e.key !== 'Enter') return;
    const v = inp.value.trim();
    if (!v) return;
    if (await api('/api/organic/custom/edit', {name: catName, ...addPayload(v)})) loadCategories();
  };
  row.appendChild(inp);
  return row;
}

function entryRow(key, c, name) {
  const row = document.createElement('div');
  row.className = 'cat-entry';
  const h = document.createElement('h3');
  h.innerHTML = '<span></span><span class="caret">▾</span>';
  h.firstChild.textContent = `${fmtDate(c.date)} — ${c.title}`;
  h.title = 'show the full entry';
  h.setAttribute('role', 'button');
  h.onclick = async () => {
    const open = row.querySelector('.entry-full');
    h.querySelector('.caret').textContent = open ? '▾' : '▴';
    await toggleEntryText(row, c, name);
  };
  row.appendChild(h);
  if (c.categories[name]) {
    const ev = document.createElement('div');
    ev.className = 'cat-ev';
    ev.textContent = c.categories[name];
    row.appendChild(ev);
  }
  const tags = document.createElement('div');
  tags.className = 'tag-editor';
  for (const [n, evid] of Object.entries(c.categories).sort()) {
    const chip = document.createElement('span');
    chip.className = 'tag' + (n === name ? ' on' : '');
    chip.title = evid;
    chip.innerHTML = '<span></span><button class="x">×</button>';
    chip.firstChild.textContent = label(n);
    const x = chip.querySelector('.x');
    x.title = 'remove this tag (kept as your correction)';
    x.setAttribute('aria-label', `remove tag ${label(n)} from this entry`);
    x.onclick = async () => {
      const r = await api('/api/categories/tag', {key, name: n, present: false});
      if (r) { catIndex = r; renderCategories(); notice(`removed “${label(n)}” from ${fmtDate(c.date)}. Fixes stick across future tagging passes.`); }
    };
    tags.appendChild(chip);
  }
  const add = document.createElement('select');
  add.className = 'tag-add';
  add.setAttribute('aria-label', 'add a tag to this entry');
  add.innerHTML = '<option value="">+ tag…</option>' +
    Object.keys(catIndex.counts)
      .filter(n => !(n in c.categories))
      .map(n => `<option value="${esc(n)}">${esc(label(n))}</option>`).join('');
  add.onchange = async () => {
    if (!add.value) return;
    const r = await api('/api/categories/tag', {key, name: add.value, present: true});
    if (r) { catIndex = r; renderCategories(); notice(`tagged ${fmtDate(c.date)} with “${label(add.value)}”.`); }
  };
  tags.appendChild(add);
  row.appendChild(tags);
  return row;
}

// also search.js's: a hit expands into its full entry the same way
export async function toggleEntryText(row, c) {
  const existing = row.querySelector('.entry-full');
  if (existing) { existing.remove(); return; }
  const d = document.createElement('div');
  d.className = 'entry-full';
  d.innerHTML = '<span>reading</span> <span class="dots">···</span>';
  row.querySelector('h3').after(d);
  const r = await (await fetch(
    `/api/entry?date=${encodeURIComponent(c.date)}&title=${encodeURIComponent(c.title)}`
  )).json();
  if (r.error) { d.textContent = r.error; return; }
  d.textContent = '';
  if (r.summary) {
    const s = document.createElement('div');
    s.className = 'summary';
    s.textContent = r.summary;
    d.appendChild(s);
  }
  const t = document.createElement('div');
  t.textContent = r.text;
  d.appendChild(t);
}

// an in-button progress label: `verb ···` with the dots pulsing
export function busyLabel(btn, verb) {
  btn.innerHTML = '';
  btn.appendChild(document.createTextNode(verb + ' '));
  const dots = document.createElement('span');
  dots.className = 'dots';
  dots.textContent = '···';
  btn.appendChild(dots);
}

export function init() {
  $('cat-scan').onclick = async () => {
    const b = $('cat-scan');
    b.disabled = true;
    busyLabel(b, 'scanning');
    try {
      const r = await api('/api/organic/scan', {});
      if (r) {
        organicState = r;
        await loadCategories();
        notice(r.proposals.length
          ? `${r.proposals.length} proposal${r.proposals.length === 1 ? '' : 's'} waiting under “proposed”.`
          : 'nothing new found.');
      }
    } finally {
      b.textContent = 'scan for new categories'; b.disabled = false;
    }
  };

  $('cat-build').onclick = async () => {
    const b = $('cat-build');
    b.disabled = true;
    $('cat-status').innerHTML = 'tagging <span class="dots">···</span>';
    busyLabel(b, 'tagging');
    try {
      const r = await api('/api/categories/build', {});
      if (r) {
        await loadCategories();
        notice(r.new ? `tagged ${r.new} new entr${r.new === 1 ? 'y' : 'ies'}.` : 'nothing new to tag.');
      }
    } finally {
      b.textContent = 'tag new entries'; b.disabled = false;
    }
  };

  // + new category: an inline form at the foot of the list; Enter submits
  const form = $('cat-newform'), toggle = $('cat-new-toggle');
  const showForm = on => {
    form.hidden = !on; toggle.hidden = on;
    if (on) $('newcat-name').focus();
  };
  toggle.onclick = () => showForm(true);
  $('newcat-cancel').onclick = () => showForm(false);
  const create = async () => {
    const name = $('newcat-name').value.trim();
    if (!name) return;
    const r = await api('/api/organic/custom', {name, keywords: $('newcat-keywords').value});
    if (r) {
      $('newcat-name').value = '';
      $('newcat-keywords').value = '';
      showForm(false);
      catSelected = name.toLowerCase();
      propSelected = null;
      await loadCategories();
    }
  };
  $('newcat-add').onclick = create;
  for (const id of ['newcat-name', 'newcat-keywords']) {
    $(id).onkeydown = e => {
      if (e.key === 'Enter') { e.preventDefault(); create(); }
      if (e.key === 'Escape') showForm(false);
    };
  }
  $('cat-browse').onclick = () => {
    $('cat-paused').hidden = true;
    $('categories').classList.remove('paused');
    loadCategories();
  };
}
