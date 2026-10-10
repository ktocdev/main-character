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
  thing: 'thing', animal: 'animal',
};
// ---- icons (static/icons/, chosen on static/design/icons.html) ----
// One per type, and one per subtype where it has its own: a built-in
// category, a dream tone, a thing category. Anything else (an organic
// category, an unknown tone) falls back to its type's icon. People have no
// icon: they show their initials.
const TYPE_ICONS = {
  summary: 'root-list', category: 'category', dream: 'moon', favorite: 'star',
  place: 'location-1', project: 'rocket', thing: 'bookmark-heart', animal: 'cat',
};
const SUBTYPE_ICONS = {
  category: {
    work: 'work', relationships: 'heart', health: 'hospital-1', creative: 'palette',
    social: 'chat-bubble-smile', family: 'usergroup', pets: 'cat',
    emotional: 'emo-emotional', ai_reflection: 'ai-book-open', home: 'houses-2',
  },
  dream: {
    nightmare: 'thunderstorm-night', anxiety: 'no-expression', processing: 'component-steps',
    peaceful: 'peace-bold', surreal: 'alien', lucid: 'hand', nostalgic: 'ice-cream', joyful: 'cake',
  },
  thing: {music: 'music', game: 'gamepad', show: 'tv', book: 'book-open', event: 'ticket'},
};
// read by figma/icons/icons.mjs, which bundles the icons for Figma
export { TYPE_ICONS, SUBTYPE_ICONS };
// these have one version, used for both styles
export const ONE_VERSION = new Set(['peace-bold']);
const ICON_DIR = new URL('../icons/', import.meta.url);
const MAX_BADGES = {sm: 2, md: 3, lg: 4};

// The icon's file for a type and subtype, or null for a person.
export function iconFile(type, subtype, style = 'filled') {
  const name = SUBTYPE_ICONS[type]?.[subtype] ?? TYPE_ICONS[type];
  if (!name) return null;
  return new URL(`${style === 'filled' && !ONE_VERSION.has(name) ? `${name}-filled` : name}.svg`, ICON_DIR).href;
}

// A person shows their initials: the first letter of the first two words.
function initials(title) {
  return title.split(/\s+/).filter(Boolean).slice(0, 2)
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
    s.className = 'badge card-badge';
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
// subtype: the category key, dream tone or thing category, for its own
// icon. iconStyle: filled (the default) | outline. icon: text that
// replaces the icon or initials. image: a URL whose picture fills the
// visual in place of either (experimental: no plan yet for where images
// come from).
export function itemCard({
  type, title, href = '#', description = '', badges = [], icon, image,
  subtype, iconStyle = 'filled', size = 'md', orientation = 'auto',
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
  // The icon is a mask filled with currentColor, so it takes the visual's
  // accent and hover colours. Set through the CSSOM, which the CSP allows.
  const file = icon || image ? null : iconFile(type, subtype, iconStyle);
  if (image) {
    // A photo fills the visual instead (experimental). Its alt is empty
    // because the visual is hidden from screen readers already.
    const img = document.createElement('img');
    img.className = 'card-image';
    img.src = image;
    img.alt = '';
    img.loading = 'lazy';
    visual.classList.add('image');
    visual.appendChild(img);
  } else if (file) {
    const glyph = document.createElement('span');
    glyph.className = 'card-icon';
    glyph.style.setProperty('--icon', `url("${file}")`);
    visual.appendChild(glyph);
  } else {
    visual.classList.add('text');
    visual.textContent = icon || initials(title);
  }

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
