// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, api, esc, fmtDate } from './core.js';

// ---- categories ----
let catIndex = null, catSelected = null, organicState = {proposals: [], custom: []};

export async function loadCategories() {
  const [ci, os] = await Promise.all([
    fetch('/api/categories').then(r => r.json()),
    fetch('/api/organic').then(r => r.json()),
  ]);
  catIndex = ci;
  organicState = os;
  renderCategories();
}

function renderCategories() {
  $('cat-status').textContent = catIndex.tagged < catIndex.total
    ? `${catIndex.tagged} of ${catIndex.total} entries tagged`
    : `all ${catIndex.total} entries tagged`;
  const chips = $('cat-chips');
  chips.innerHTML = '';
  const untagged = Object.values(catIndex.conversations)
    .filter(c => !Object.keys(c.categories).length).length;
  const customNames = new Set(catIndex.custom || []);
  const mkChip = (name, count, label) => {
    const b = document.createElement('button');
    b.className = 'cat-chip' + (catSelected === name ? ' active' : '')
      + (customNames.has(name) ? ' custom' : '');
    b.textContent = `${label || name.replace('_', ' ')} ${count}`;
    if (customNames.has(name)) b.title = 'organic category (yours)';
    b.onclick = () => { catSelected = catSelected === name ? null : name; renderCategories(); };
    chips.appendChild(b);
  };
  for (const [name, count] of Object.entries(catIndex.counts).sort((a, b) => b[1] - a[1])) {
    mkChip(name, count);
  }
  if (untagged) mkChip('__untagged', untagged, 'untagged');
  renderProposals();
  renderCustomRow();
  renderCatEntries();
}

function renderProposals() {
  const el = $('cat-proposals');
  el.innerHTML = '';
  for (const p of organicState.proposals) {
    const d = document.createElement('div');
    d.className = 'cat-proposal';
    const head = document.createElement('div');
    head.innerHTML = `<b>${esc(p.name)}</b> <span class="prop-meta">${esc(p.confidence)} confidence · ${p.weeks} weeks</span>`;
    d.appendChild(head);
    const mem = document.createElement('div');
    mem.className = 'prop-members';
    mem.textContent = p.members.join(' · ');
    d.appendChild(mem);
    const why = document.createElement('div');
    why.className = 'prop-reason';
    why.textContent = p.reason;
    d.appendChild(why);
    const row = document.createElement('div');
    row.className = 'prop-actions';
    const act = (label, action, title) => {
      const b = document.createElement('button');
      b.className = 'quiet';
      b.textContent = label;
      b.title = title;
      b.onclick = async () => {
        if (await api('/api/organic/respond', {id: p.id, action})) loadCategories();
      };
      row.appendChild(b);
    };
    act('confirm', 'confirm', 'create this category — entries mentioning these members get the tag');
    act('not now', 'not_now', 'defer — re-surfaces when the cluster gains a new member');
    act('dismiss', 'dismiss', 'not a real category — stays hidden unless new members appear');
    d.appendChild(row);
    el.appendChild(d);
  }
}

function renderCustomRow() {
  const el = $('cat-custom-row');
  el.innerHTML = '';
  if (!organicState.custom.length) return;
  const label = document.createElement('div');
  label.className = 'custom-label';
  label.textContent = 'your categories:';
  el.appendChild(label);
  for (const c of organicState.custom) {
    const row = document.createElement('div');
    row.className = 'custom-cat';

    const name = document.createElement('span');
    name.className = 'cc-name';
    name.textContent = c.name;
    row.appendChild(name);

    const removable = (chip, payload, label) => {
      const x = document.createElement('button');
      x.textContent = '×';
      x.title = label;
      x.onclick = async () => {
        if (await api('/api/organic/custom/edit', {name: c.name, ...payload})) loadCategories();
      };
      chip.appendChild(document.createTextNode(' '));
      chip.appendChild(x);
    };
    if (c.keywords.length) {
      const l = document.createElement('span');
      l.className = 'cc-meta';
      l.textContent = 'keywords:';
      row.appendChild(l);
      for (const kw of c.keywords) {
        const k = document.createElement('span');
        k.className = 'kw-chip';
        k.textContent = kw;
        removable(k, {remove_keyword: kw}, 'remove this keyword');
        row.appendChild(k);
      }
    }
    if (c.members.length) {
      const l = document.createElement('span');
      l.className = 'cc-meta';
      l.textContent = 'members:';
      row.appendChild(l);
      for (const mem of c.members) {
        const m = document.createElement('span');
        m.className = 'kw-chip member';
        m.textContent = mem;
        removable(m, {remove_member: mem}, 'remove this member');
        row.appendChild(m);
      }
    }
    if (!c.keywords.length && !c.members.length) {
      const e = document.createElement('span');
      e.className = 'cc-meta';
      e.textContent = 'no keywords or members yet';
      row.appendChild(e);
    }

    const x = document.createElement('button');
    x.className = 'quiet';
    x.textContent = '× delete';
    x.title = 'delete this category (its tags disappear; entries are untouched)';
    x.onclick = async () => {
      if (!confirm(`delete category "${c.name}"?`)) return;
      if (await api('/api/organic/custom/delete', {name: c.name})) loadCategories();
    };
    row.appendChild(x);
    el.appendChild(row);
  }
}

function renderCatEntries() {
  const el = $('cat-entries');
  el.innerHTML = '';
  if (!catSelected) {
    el.innerHTML = '<p class="cat-hint">pick a category to browse its entries — click a title for the full text, × a wrong tag to remove it (your fixes stick)</p>';
    return;
  }
  // a custom category shows its definition (keywords + members)
  const customDef = (organicState.custom || []).find(c => c.name === catSelected);
  if (customDef) {
    const def = document.createElement('div');
    def.className = 'cat-def';
    if (customDef.keywords.length) {
      const l = document.createElement('span');
      l.className = 'lbl';
      l.textContent = 'keywords';
      def.appendChild(l);
      for (const kw of customDef.keywords) {
        const k = document.createElement('span');
        k.className = 'kw-chip';
        k.textContent = kw;
        def.appendChild(k);
      }
    }
    if (customDef.members.length) {
      const l = document.createElement('span');
      l.className = 'lbl';
      l.textContent = 'members';
      def.appendChild(l);
      for (const mem of customDef.members) {
        const m = document.createElement('span');
        m.className = 'kw-chip member';
        m.textContent = mem;
        def.appendChild(m);
      }
    }
    if (!customDef.keywords.length && !customDef.members.length) {
      def.textContent = 'no keywords or members defined yet';
    }
    el.appendChild(def);
  }

  if (catSelected !== '__untagged') {
    const box = document.createElement('div');
    box.className = 'cat-domain';
    box.textContent = 'loading domain summary…';
    el.appendChild(box);
    fetch('/api/summaries/domain?name=' + encodeURIComponent(catSelected))
      .then(r => r.json())
      .then(r => {
        if (r.error) { box.remove(); return; }
        const lines = r.doc.split('\n').filter(l => !l.startsWith('<!--'));
        const title = (lines.find(l => l.startsWith('# ')) || '').replace(/^# /, '');
        const body = lines.filter(l => !l.startsWith('# ')).join('\n').trim();
        box.innerHTML = '';
        const h = document.createElement('div');
        h.className = 'cat-domain-title';
        h.textContent = title;
        const p = document.createElement('div');
        p.className = 'cat-domain-body';
        p.textContent = body;
        box.appendChild(h);
        box.appendChild(p);
      })
      .catch(() => box.remove());
  }
  const rows = Object.entries(catIndex.conversations)
    .filter(([, c]) => catSelected === '__untagged'
      ? !Object.keys(c.categories).length
      : catSelected in c.categories)
    .sort((a, b) => b[1].date.localeCompare(a[1].date));
  for (const [key, c] of rows) {
    const row = document.createElement('div');
    row.className = 'cat-entry';
    const h = document.createElement('h3');
    h.textContent = `${fmtDate(c.date)} — ${c.title}`;
    h.title = 'show full entry';
    h.onclick = () => toggleEntryText(row, c);
    row.appendChild(h);
    if (c.categories[catSelected]) {
      const ev = document.createElement('div');
      ev.className = 'cat-ev';
      ev.textContent = c.categories[catSelected];
      row.appendChild(ev);
    }
    const tags = document.createElement('div');
    tags.className = 'cat-tags';
    for (const [n, evid] of Object.entries(c.categories).sort()) {
      const chip = document.createElement('span');
      chip.className = 'chip';
      chip.title = evid;
      chip.textContent = n.replace('_', ' ') + ' ';
      const x = document.createElement('button');
      x.textContent = '×';
      x.title = 'remove this tag (kept as your correction)';
      x.onclick = async () => {
        const r = await api('/api/categories/tag', {key, name: n, present: false});
        if (r) { catIndex = r; renderCategories(); }
      };
      chip.appendChild(x);
      tags.appendChild(chip);
    }
    const add = document.createElement('select');
    add.className = 'cat-add';
    add.innerHTML = '<option value="">+ tag…</option>' +
      Object.keys(catIndex.counts)
        .filter(n => !(n in c.categories))
        .map(n => `<option value="${n}">${n.replace('_', ' ')}</option>`).join('');
    add.onchange = async () => {
      if (!add.value) return;
      const r = await api('/api/categories/tag', {key, name: add.value, present: true});
      if (r) { catIndex = r; renderCategories(); }
    };
    tags.appendChild(add);
    row.appendChild(tags);
    el.appendChild(row);
  }
}

async function toggleEntryText(row, c) {
  const existing = row.querySelector('.cat-full');
  if (existing) { existing.remove(); return; }
  const d = document.createElement('div');
  d.className = 'cat-full';
  d.textContent = 'loading…';
  row.appendChild(d);
  const r = await (await fetch(
    `/api/entry?date=${encodeURIComponent(c.date)}&title=${encodeURIComponent(c.title)}`
  )).json();
  if (r.error) { d.textContent = r.error; return; }
  d.textContent = '';
  if (r.summary) {
    const s = document.createElement('div');
    s.className = 'cat-summary';
    s.textContent = r.summary;
    d.appendChild(s);
  }
  const t = document.createElement('div');
  t.textContent = r.text;
  d.appendChild(t);
}
export function init() {
  $('cat-scan').onclick = async () => {
    const b = $('cat-scan');
    b.disabled = true;
    b.textContent = 'scanning…';
    try {
      const r = await api('/api/organic/scan', {});
      if (r) {
        organicState = r;
        await loadCategories();
        b.textContent = r.proposals.length
          ? `${r.proposals.length} proposal${r.proposals.length === 1 ? '' : 's'}`
          : 'nothing new found';
      }
    } finally {
      setTimeout(() => { b.textContent = 'scan for new categories'; b.disabled = false; }, 4000);
    }
  };

  $('newcat-add').onclick = async () => {
    const name = $('newcat-name').value.trim();
    if (!name) return;
    const r = await api('/api/organic/custom', {
      name,
      keywords: $('newcat-keywords').value,
    });
    if (r) {
      $('newcat-name').value = '';
      $('newcat-keywords').value = '';
      loadCategories();
    }
  };

  $('cat-build').onclick = async () => {
    const b = $('cat-build');
    b.disabled = true;
    b.textContent = 'tagging… (~5s per new entry)';
    try {
      const r = await api('/api/categories/build', {});
      if (r) {
        await loadCategories();
        b.textContent = r.new ? `tagged ${r.new} new` : 'nothing new to tag';
      }
    } finally {
      setTimeout(() => { b.textContent = 'tag new entries'; b.disabled = false; }, 4000);
    }
  };
}
