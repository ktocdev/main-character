// SPDX-License-Identifier: AGPL-3.0-or-later
// icons.html's own script, kept out of the page so it runs under a
// CSP with no inline scripts (the web demo publishes these pages).
import { cardRow, iconFile, ONE_VERSION } from '../../js/card.js';

// The assignments, mirrored from card.js (TYPE_ICONS, SUBTYPE_ICONS) with a
// note on what each means; a row that disagrees with card.js says so.
const GROUPS = [
  ['Card types', 'The visual on each kind of card. A category, tone or thing without an icon of its own uses its type’s.', [
    {key: 'summary', icon: 'root-list'},
    {key: 'category', icon: 'category', note: 'Also organic and custom categories.'},
    {key: 'dream', label: 'dream category', icon: 'moon', note: 'Any tone without its own icon.'},
    {key: 'favorite', icon: 'star'},
    {key: 'person', initials: true, note: 'Initials, not an icon: the first letter of the first two words of the name.'},
    {key: 'place', icon: 'location-1'},
    {key: 'project', icon: 'rocket', note: 'Something you’re building or launching.'},
    {key: 'thing', icon: 'bookmark-heart', note: 'Something you enjoy or follow. Any thing category without its own icon.'},
    {key: 'animal', icon: 'cat', note: 'A pet, or someone else’s animal. Shares the pets category icon.'},
  ]],
  ['Categories', 'The ten built-in life categories (categories.py).', [
    {key: 'work', icon: 'work'},
    {key: 'relationships', icon: 'heart'},
    {key: 'health', icon: 'hospital-1'},
    {key: 'creative', icon: 'palette'},
    {key: 'social', icon: 'chat-bubble-smile'},
    {key: 'family', icon: 'usergroup'},
    {key: 'pets', icon: 'cat'},
    {key: 'emotional', icon: 'emo-emotional'},
    {key: 'ai_reflection', label: 'AI reflection', icon: 'ai-book-open'},
    {key: 'home', icon: 'houses-2'},
  ]],
  ['Dream tones', 'The eight tones (dreams.py TONES), which are what the dream category cards show.', [
    {key: 'nightmare', icon: 'thunderstorm-night'},
    {key: 'anxiety', icon: 'no-expression'},
    {key: 'processing', icon: 'component-steps'},
    {key: 'peaceful', icon: 'peace-bold', note: 'One version, the same in both sets.'},
    {key: 'surreal', icon: 'alien'},
    {key: 'lucid', icon: 'hand', note: 'Looking at your hands is the usual reality check in a lucid dream.'},
    {key: 'nostalgic', icon: 'ice-cream'},
    {key: 'joyful', icon: 'cake'},
  ]],
  ['Thing categories', 'Things you enjoy or follow, as opposed to projects you make (the thing kind in the entity plan).', [
    {key: 'music', icon: 'music', note: 'Bands, artists, albums.'},
    {key: 'game', icon: 'gamepad', note: 'Cities Skylines, Tomb Raider.'},
    {key: 'show', icon: 'tv', note: 'TV and film: 90 Day Fiance.'},
    {key: 'book', icon: 'book-open'},
    {key: 'event', icon: 'ticket', note: 'Recurring: Rib Fest, Squared Off Fest.'},
    {key: 'other', icon: 'bookmark-heart', note: 'Shares the thing icon.'},
  ]],
];
// the card type each group's rows feed: the type itself, or its subtypes
const GROUP_TYPE = [null, 'category', 'dream', 'thing'];

// Where each icon comes from; everything else is TDesign. Shown in the full
// set, since the licences differ (all MIT).
const SOURCE = {
  alien: 'Phosphor', 'peace-bold': 'Phosphor',
  hand: 'Boxicons', rocket: 'Boxicons', category: 'Boxicons', 'bookmark-heart': 'Bootstrap',
};

// ---- loading ----
// Inlined rather than <img>, so currentColor picks up the text colour.
const cache = new Map();
function load(file) {
  if (!cache.has(file)) cache.set(file, fetch(`../icons/${file}.svg`)
    .then(r => r.text())
    .then(t => new DOMParser().parseFromString(t, 'image/svg+xml').documentElement));
  return cache.get(file);
}
const fileFor = (name, style) => style === 'filled' && !ONE_VERSION.has(name) ? `${name}-filled` : name;
function icon(name, style = 'outline') {
  const holder = document.createElement('span');
  holder.className = 'ico';
  load(fileFor(name, style)).then(svg => {
    const el = document.importNode(svg, true);
    el.setAttribute('aria-hidden', 'true');
    el.setAttribute('focusable', 'false');
    holder.appendChild(el);
  });
  return holder;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function tile(name, style, initials) {
  const t = el('div', 'tile');
  if (initials) { t.classList.add('initials'); t.textContent = 'WW'; }
  else t.appendChild(icon(name, style));
  return t;
}

// ---- assignments ----
const usedBy = new Map();   // icon -> the rows that use it, for the full set
const map = document.getElementById('map-out');
for (const [gi, [title, blurb, rows]] of GROUPS.entries()) {
  const group = el('div', 'map-group');
  group.append(el('div', 'spec-line'), el('p', 'note', blurb));
  group.firstChild.append(el('b', null, title));
  const head = el('div', 'map-row head');
  for (const h of ['', 'filled', 'outline', 'file']) head.appendChild(el('div', null, h));
  group.appendChild(head);
  for (const r of rows) {
    const label = r.label ?? r.key;
    if (r.icon) usedBy.set(r.icon, [...usedBy.get(r.icon) ?? [], label]);
    const row = el('div', 'map-row');
    const name = el('div', 'map-key');
    name.append(el('span', 'mono', label));
    if (r.note) name.append(el('span', 'why', r.note));
    const file = el('div', 'file mono', r.initials ? 'text' : r.icon);
    // card.js is the source of truth; flag a row that has drifted from it
    const drawn = r.initials ? null : iconFile(GROUP_TYPE[gi] ?? r.key, r.key, 'outline')?.split('/').pop().replace(/\.svg$/, '');
    if (!r.initials && drawn !== r.icon) file.append(el('span', 'why drift', `card.js draws ${drawn ?? 'nothing'}`));
    row.append(name, tile(r.icon, 'filled', r.initials), tile(r.icon, 'outline', r.initials), file);
    group.appendChild(row);
  }
  map.appendChild(group);
}

// ---- in a card ----
// Small cards drawn by card.js itself, which picks the icon from the type
// and subtype, so these rows show what the app draws.
const cap = s => s.replace(/^./, c => c.toUpperCase());
const CARD_ROWS = [
  ['card types', [
    ['summary', 'Week of September 22', ['Sep 22–28']],
    ['category', 'Family', ['31 entries']],
    ['dream', 'Surreal', ['2 dreams']],
    ['favorite', 'The lamp was the only light left on', ['saved Sep 1']],
    ['person', 'Wren Whitaker', ['49 mentions']],
    ['place', 'Greenwood Park', ['12 mentions']],
    ['project', 'Groundwork', ['18 mentions']],
    ['thing', 'The Tin Orchard', ['music']],
  ].map(([type, title, badges]) => ({type, title, badges}))],
  ['categories', GROUPS[1][2].map(r => ({type: 'category', subtype: r.key, title: cap(r.label ?? r.key), badges: ['12 entries']}))],
  ['dream tones', GROUPS[2][2].map(r => ({type: 'dream', subtype: r.key, title: cap(r.key), badges: ['3 dreams']}))],
  ['thing categories', GROUPS[3][2].map(r => ({type: 'thing', subtype: r.key, title: cap(r.key), badges: ['thing']}))],
];

const cardsOut = document.getElementById('cards-out');
function drawCards(style) {
  cardsOut.replaceChildren();
  for (const [label, cards] of CARD_ROWS)
    cardsOut.append(el('div', 'spec-line', label), cardRow(cards.map(c => ({...c, href: '#cards', iconStyle: style})), 'sm'));
}
const seg = document.getElementById('style');
seg.addEventListener('click', e => {
  const b = e.target.closest('button');
  if (!b) return;
  seg.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
  drawCards(b.dataset.s);
});
drawCards('filled');

// ---- the full set ----
// Every icon in use, alphabetical, with what uses it and where it is from.
const set = document.getElementById('set-out');
for (const name of [...usedBy.keys()].sort()) {
  const cell = el('div', 'set-cell');
  const pair = el('div', 'pair');
  pair.append(icon(name, 'filled'));
  if (!ONE_VERSION.has(name)) pair.append(icon(name, 'outline'));
  cell.append(pair, el('span', 'mono', name),
    el('span', 'why', `${usedBy.get(name).join(', ')} · ${SOURCE[name] ?? 'TDesign'}`));
  set.appendChild(cell);
}
