// SPDX-License-Identifier: AGPL-3.0-or-later
import { $ } from './core.js';

// ---- help ----
// The help sections are collapsible <details> (item 2). A table-of-contents
// link — or any in-page #hash — points at a summary or at something nested
// inside a section (the triage step lives inside "entities"), and the browser
// will not reveal a target sitting inside a collapsed <details> on its own.
// So open the enclosing section first, then scroll the target into view.
function reveal(hash) {
  if (!hash || hash === '#') return;
  const target = document.getElementById(decodeURIComponent(hash.slice(1)));
  if (!target) return;
  const sec = target.closest('details.help-sec');
  if (sec) sec.open = true;
  target.scrollIntoView({ block: 'start' });
}

export function init() {
  const toc = $('help-toc');
  if (!toc) return;
  toc.addEventListener('click', e => {
    const a = e.target.closest('a[href^="#"]');
    if (!a) return;
    e.preventDefault();
    reveal(a.getAttribute('href'));
  });
}
