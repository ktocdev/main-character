// SPDX-License-Identifier: AGPL-3.0-or-later
// What every design page shares: the theme toggle, the side nav, frozen
// interaction states and specimen captions.

// ---- the pages, in nav order ----
// One list, so a new page is one line here and one card on the home page.
export const PAGES = [
  ['tokens', 'tokens.html', 'foundation'],
  ['card', 'card.html'],
  ['button', 'button.html'],
  ['input', 'input.html'],
  ['textarea', 'textarea.html'],
  ['select', 'select.html'],
  ['checkbox', 'checkbox.html'],
  ['toggle', 'toggle.html'],
  ['badge', 'badge.html'],
  ['button group', 'button-group.html'],
  ['tooltip', 'tooltip.html'],
  ['action menu', 'action-menu.html'],
  ['disclosure', 'disclosure.html'],
  ['search bar', 'search-bar.html'],
  ['chat bar', 'chat-bar.html'],
  ['modal', 'modal.html'],
];

// ---- theme ----
// The same client-only pin the app's Settings writes (localStorage
// `rag_theme`), so a choice made here carries into the app and back. The
// inline head script applies it before paint.
const seg = document.getElementById('theme');
function mark() {
  let t = 'auto';
  try { t = localStorage.getItem('rag_theme') || 'auto'; } catch (e) {}
  if (t !== 'light' && t !== 'dark') t = 'auto';
  seg.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.t === t)));
}
seg.addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  const t = b.dataset.t;
  try { if (t === 'auto') localStorage.removeItem('rag_theme'); else localStorage.setItem('rag_theme', t); } catch (e) {}
  if (t === 'auto') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  mark();
});
mark();

// ---- side nav ----
// The site links go above whatever "on this page" links the page wrote.
const nav = document.querySelector('nav.side');
if (nav) {
  const here = location.pathname.split('/').pop();
  const top = document.createDocumentFragment();
  const add = (label, href, current) => {
    const a = document.createElement('a');
    a.href = href; a.textContent = label;
    if (current) a.setAttribute('aria-current', 'page');
    top.appendChild(a);
  };
  const eyebrow = text => {
    const d = document.createElement('div');
    d.className = 'eyebrow'; d.textContent = text;
    top.appendChild(d);
  };
  eyebrow('design system');
  add('home', '/design', location.pathname === '/design');
  let components = false;
  for (const [label, file, group] of PAGES) {
    if (!group && !components) { eyebrow('components'); components = true; }
    add(label, '/static/design/' + file, here === file);
  }
  nav.prepend(top);
}

// ---- frozen states ----
// Every :hover, :focus, :focus-visible and :focus-within rule in the app's
// stylesheets gets a twin keyed on a class (.is-hover, .is-focus, ...), so
// a specimen can hold a state still. The twin is made from the rule itself,
// so a state shown here can't drift from the one the app draws.
const STATES = [
  [/:focus-visible(?![-\w])/g, '.is-focus-visible'],
  [/:focus-within(?![-\w])/g, '.is-focus-within'],
  [/:focus(?![-\w])/g, '.is-focus'],
  [/:hover(?![-\w])/g, '.is-hover'],
];
const STATE_TEST = /:(hover|focus|focus-visible|focus-within)(?![-\w])/;

// split a selector list on its top-level commas, not those inside :is()
function splitList(sel) {
  const out = [];
  let depth = 0, start = 0;
  for (let i = 0; i < sel.length; i++) {
    const c = sel[i];
    if (c === '(') depth++;
    else if (c === ')') depth--;
    else if (c === ',' && depth === 0) { out.push(sel.slice(start, i).trim()); start = i + 1; }
  }
  out.push(sel.slice(start).trim());
  return out;
}

function freeze(list) {
  for (let i = 0; i < list.cssRules.length; i++) {
    const r = list.cssRules[i];
    if (r instanceof CSSStyleRule) {
      const parts = splitList(r.selectorText).filter(s => STATE_TEST.test(s));
      if (!parts.length) continue;
      const sel = parts.map(s => STATES.reduce((a, [re, cls]) => a.replace(re, cls), s)).join(', ');
      // a twin the parser refuses (a state on a pseudo-element, like a
      // scrollbar thumb's :hover) is skipped, not fatal to the sheet
      try { list.insertRule(`${sel} { ${r.style.cssText} }`, i + 1); i++; } catch (e) {}
    } else if (r.cssRules && (r instanceof CSSMediaRule || r instanceof CSSSupportsRule)) {
      freeze(r);
    }
  }
}
for (const sheet of document.styleSheets) {
  try { freeze(sheet); } catch (e) { /* a sheet from another origin: none here */ }
}

// ---- specimen captions ----
// A specimen is any element with data-figma-variant (the Figma exporter
// reads the same attribute). In a .spec-row each gets a caption under it:
// its variant values, so the page and the Figma variant names match. A
// specimen that needs a parent for its CSS (.set-control) sits in a .host,
// and the host is what goes in the cell.
for (const el of document.querySelectorAll('.spec-row > [data-figma-variant], .spec-row > .host > [data-figma-variant]')) {
  const box = el.parentElement.classList.contains('host') ? el.parentElement : el;
  const cell = document.createElement('div');
  cell.className = 'spec-cell';
  box.replaceWith(cell);
  cell.appendChild(box);
  const cap = document.createElement('div');
  cap.className = 'cap';
  cap.textContent = el.dataset.figmaVariant.split(',').map(s => s.split('=')[1].trim()).join(' · ');
  cell.appendChild(cap);
}
