// SPDX-License-Identifier: AGPL-3.0-or-later
// ---- the nav: flat tabs above 1080px, a megamenu below ----
// Eleven tabs do not survive a phone-width bar, and a bar that wide is
// crowded on a laptop too. Above 1080px the eleven buttons stay in a row;
// below it one trigger (`write ▾`) opens a full-width panel with the same
// eleven in three groups, each with a one-line description. Both are built
// here from one table, so the flat bar and the menu can never disagree about
// what the app has in it. CSS (base.css) decides which one shows.

import { $ } from './core.js';
import * as popover from './popover.js';

export const TABS = [
  ['write', "today's entry and the companion's reply"],
  ['chat', 'look something up in your journal'],
  ['search', 'find an entry by word or phrase'],
  ['entities', 'people, places, projects and things'],
  ['categories', 'life domains, tagged over time'],
  ['patterns', 'recurring arcs the companion noticed'],
  ['dreams', 'dreams, kept apart from the day'],
  ['history', 'every past chapter, by date'],
  ['triage', 'confirm what the companion guessed'],
  ['help', 'shortcuts and how it works'],
  ['settings', 'theme, models, backups'],
];
// A tab is named by its id, except where the name changed and the id
// didn't: the lookup screen is "ask" to the reader and `chat` in the code
// (tab-chat, #chat-text, main.js), as the life summary is `seed`.
const LABELS = {chat: 'ask'};
export const label = id => LABELS[id] || id;
const GROUPS = [
  ['write', ['write', 'chat']],
  ['read back', ['search', 'entities', 'categories', 'patterns', 'dreams', 'history']],
  ['keep', ['triage', 'help', 'settings']],
];

let onSelect = () => {};
let active = 'write';

function build() {
  const nav = document.querySelector('header nav');
  const flat = document.createElement('div');
  flat.className = 'nav-flat';
  flat.setAttribute('role', 'tablist');
  for (const [id] of TABS) {
    const b = document.createElement('button');
    b.dataset.tab = id;
    b.textContent = label(id);
    b.onclick = () => select(id);
    flat.appendChild(b);
  }
  const menu = document.createElement('div');
  menu.className = 'nav-menu';
  const trigger = document.createElement('button');
  trigger.id = 'nav-trigger';
  trigger.setAttribute('aria-expanded', 'false');
  trigger.setAttribute('aria-controls', 'nav-panel');
  trigger.innerHTML = '<span class="lbl"></span><span class="caret">▾</span>';
  trigger.onclick = e => { e.stopPropagation(); toggleMenu(); };
  menu.appendChild(trigger);
  nav.prepend(flat, menu);

  const panel = document.createElement('div');
  panel.id = 'nav-panel';
  panel.hidden = true;
  for (const [name, ids] of GROUPS) {
    const g = document.createElement('div');
    g.className = 'nav-group';
    const h = document.createElement('div');
    h.className = 'eyebrow strong';
    h.textContent = name;
    g.appendChild(h);
    for (const id of ids) {
      const desc = TABS.find(t => t[0] === id)[1];
      const b = document.createElement('button');
      b.className = 'list-item';
      b.dataset.tab = id;
      b.innerHTML = `<span class="li-title"></span><span class="li-desc"></span>`;
      b.querySelector('.li-title').textContent = label(id);
      b.querySelector('.li-desc').textContent = desc;
      b.onclick = () => { closeMenu(); select(id); };
      g.appendChild(b);
    }
    panel.appendChild(g);
  }
  // the panel hangs from the header, not the nav, so it can span the width
  nav.closest('header').appendChild(panel);
  popover.register('nav', closeMenu, t => panel.contains(t) || trigger.contains(t));
}

function toggleMenu() {
  const panel = $('nav-panel');
  if (panel.hidden) {
    popover.opened('nav');
    panel.hidden = false;
    $('nav-trigger').setAttribute('aria-expanded', 'true');
    $('nav-trigger').querySelector('.caret').textContent = '▴';
  } else closeMenu();
}
export function closeMenu() {
  const panel = $('nav-panel');
  if (!panel || panel.hidden) return;
  panel.hidden = true;
  $('nav-trigger').setAttribute('aria-expanded', 'false');
  $('nav-trigger').querySelector('.caret').textContent = '▾';
}

// Light one tab in both renderings. `shown` is which tab's pane is visible
// when that differs (the seed editor keeps `write` lit).
export function markActive(id) {
  active = id;
  document.querySelectorAll('nav [data-tab]').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === id));
  const lbl = document.querySelector('#nav-trigger .lbl');
  if (lbl) lbl.textContent = label(id);
}

export function current() { return active; }

function select(id) {
  markActive(id);
  onSelect(id);
}

export function init(handler) {
  onSelect = handler;
  build();
  markActive(active);
}
