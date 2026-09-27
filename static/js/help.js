// SPDX-License-Identifier: AGPL-3.0-or-later
import { $ } from './core.js';
import { showTab } from './main.js';

// ---- help ----
// The sections are an accordion of <details>. A jump pill (or any in-page
// #hash) points at a section or at something nested inside one (the triage
// step lives inside "entities"), and the browser will not reveal a target
// sitting inside a collapsed <details> on its own. So open the enclosing
// section first, then scroll the pane, leaving room for the sticky row.
const JUMPS = ['write', 'chat', 'search', 'history', 'entities', 'triage', 'categories', 'patterns', 'dreams', 'safety'];

function reveal(id) {
  const target = document.getElementById(id);
  if (!target) return;
  const sec = target.closest('details.help-sec');
  if (sec) sec.open = true;
  const pane = $('help');
  requestAnimationFrame(() => {
    const top = target.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop;
    pane.scrollTo({top: Math.max(0, top - 64), behavior: 'smooth'});
  });
}

function markActive(id) {
  document.querySelectorAll('#help-jumps .chip').forEach(b =>
    b.classList.toggle('on', b.dataset.jump === id));
}

function updateAllLabel() {
  const secs = [...document.querySelectorAll('#help details.help-sec')];
  $('help-all').textContent = secs.every(s => s.open) ? 'collapse all' : 'expand all';
}

export function init() {
  const pills = $('help-jumps');
  if (!pills) return;
  for (const id of JUMPS) {
    const b = document.createElement('button');
    b.className = 'chip sm';
    b.dataset.jump = id;
    b.textContent = id;
    b.onclick = () => { markActive(id); reveal('h-' + id); };
    pills.appendChild(b);
  }
  $('help-all').onclick = () => {
    const secs = [...document.querySelectorAll('#help details.help-sec')];
    const all = secs.every(s => s.open);
    secs.forEach(s => { s.open = !all; });
    updateAllLabel();
  };
  document.querySelectorAll('#help details.help-sec').forEach(s =>
    s.addEventListener('toggle', updateAllLabel));
  updateAllLabel();
  document.querySelectorAll('#help [data-tab-link]').forEach(a =>
    a.onclick = e => { e.preventDefault(); showTab(a.dataset.tabLink); });
}
