// SPDX-License-Identifier: AGPL-3.0-or-later
// ---- the item card (static/css/card.css) ----
// One card for anything the journal can point at. The visual and the title
// both link to `href`; the visual is hidden from screen readers and skipped
// by Tab, so each card is one focus stop. The description stays outside any
// link so it can be selected and copied. Everything is set with textContent:
// titles and descriptions come from the journal and are never markup.

const LABELS = {
  summary: 'summary', category: 'category', dream: 'dream category',
  favorite: 'favorite', person: 'person', place: 'place', project: 'project',
};
// ☾ ◎ ◇ ★ are not in Old Standard TT and fall back to a system face.
// Swap them for real icons if the app ever gets an icon set.
const GLYPHS = {
  summary: '¶', category: '§', dream: '☾', favorite: '★', place: '◎', project: '◇',
};
const MAX_BADGES = {sm: 2, md: 3, lg: 4};

// A person shows their initials: the first letter of the first two words.
function glyphFor(type, title) {
  return GLYPHS[type] ?? title.split(/\s+/).filter(Boolean).slice(0, 2)
    .map(w => w[0].toUpperCase()).join('');
}

// Past the size's limit the last slot becomes a plain "+N".
function badgeRow(badges, size) {
  const row = document.createElement('div');
  row.className = 'card-badges';
  const labels = badges.map(b => typeof b === 'string' ? b : b.label);
  const max = MAX_BADGES[size];
  const shown = labels.length > max ? labels.slice(0, max - 1) : labels;
  for (const label of shown) {
    const s = document.createElement('span');
    s.className = 'card-badge';
    s.textContent = label;
    row.appendChild(s);
  }
  if (shown.length < labels.length) {
    const more = document.createElement('span');
    more.className = 'card-more';
    more.textContent = `+${labels.length - shown.length}`;
    row.appendChild(more);
  }
  return row;
}

// size: sm | md | lg. orientation: auto (horizontal below 670px, from CSS)
// | vertical | horizontal. Small cards never show a description.
export function itemCard({
  type, title, href = '#', description = '', badges = [], icon,
  size = 'md', orientation = 'auto',
}) {
  const card = document.createElement('article');
  card.className = 'item-card';
  if (size !== 'md') card.classList.add(size);
  if (orientation !== 'auto') card.classList.add(orientation);

  const visual = document.createElement('a');
  visual.className = 'card-visual';
  visual.href = href;
  visual.tabIndex = -1;
  visual.setAttribute('aria-hidden', 'true');
  visual.textContent = icon || glyphFor(type, title);

  const body = document.createElement('div');
  body.className = 'card-body';
  const eyebrow = document.createElement('div');
  eyebrow.className = 'card-type';
  eyebrow.textContent = LABELS[type] ?? type;
  const link = document.createElement('a');
  link.className = 'card-title';
  link.href = href;
  link.textContent = title;
  body.append(eyebrow, link);
  if (description && size !== 'sm') {
    const p = document.createElement('p');
    p.className = 'card-desc';
    p.textContent = description;
    body.appendChild(p);
  }
  if (badges.length) body.appendChild(badgeRow(badges, size));

  card.append(visual, body);
  return card;
}

// The grid the cards sit in: its track floor matches the card size.
export function cardRow(cards, size = 'md') {
  const row = document.createElement('div');
  row.className = 'card-row';
  if (size !== 'md') row.classList.add(size);
  for (const c of cards) row.appendChild(itemCard({...c, size}));
  return row;
}
