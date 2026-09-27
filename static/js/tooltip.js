// SPDX-License-Identifier: AGPL-3.0-or-later
// ---- the custom tooltip ----
// One card for every `title` in the app. Native tooltips are late, small
// and unstyled, and the redesign replaces them with a --bg-raised card in
// the UI face, full --text for contrast (docs/design_handoff_main_character,
// "Tooltip"). The rule of placement: above the control by default; below it
// when there is no room above (the first row under the header); above again
// for the sticky bottom bar. Measuring the room is what makes that one rule.
//
// Delegated on the document, so a control created later -- most of the app
// renders on the fly -- needs nothing more than a `title`. On first hover the
// title moves to `data-tip`, which keeps the browser's own tooltip from
// showing beside this one. Code that sets `.title` again later is fine: the
// next hover moves it again.

import { $ } from './core.js';

const GAP = 7.2;      // .45rem
const EDGE = 8;
let tip = null;
let anchor = null;

function ensure() {
  if (tip) return tip;
  tip = document.createElement('div');
  tip.id = 'tip';
  tip.setAttribute('role', 'tooltip');
  tip.hidden = true;
  document.body.appendChild(tip);
  return tip;
}

function textFor(el) {
  if (el.hasAttribute('title')) {
    const t = el.getAttribute('title');
    el.removeAttribute('title');
    if (t) el.dataset.tip = t; else delete el.dataset.tip;
  }
  return el.dataset.tip || '';
}

function place(el) {
  const t = ensure();
  const r = el.getBoundingClientRect();
  const w = t.offsetWidth, h = t.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  // above unless it would run into the header (or off the top)
  const header = document.querySelector('header');
  const top0 = header ? header.getBoundingClientRect().bottom : 0;
  const inHeader = header && header.contains(el);
  let top = r.top - GAP - h;
  const wantBelow = el.dataset.tipPlace === 'below';
  if (wantBelow || (!inHeader && top < top0 + 4) || top < EDGE) top = r.bottom + GAP;
  if (top + h > vh - EDGE) top = Math.max(EDGE, r.top - GAP - h);
  // left-aligned with the control, kept inside the viewport; right-aligned
  // when the control hugs the right edge (the cost meter)
  let left = r.left;
  if (left + w > vw - EDGE) left = r.right - w;
  left = Math.max(EDGE, Math.min(left, vw - w - EDGE));
  t.style.left = left + 'px';
  t.style.top = top + 'px';
}

export function show(el) {
  const text = textFor(el);
  if (!text) return;
  const t = ensure();
  anchor = el;
  t.textContent = text;
  t.hidden = false;
  place(el);
}

export function hide() {
  if (tip) tip.hidden = true;
  anchor = null;
}

function anchorFrom(target) {
  if (!(target instanceof Element)) return null;
  return target.closest('[title], [data-tip]');
}

export function init() {
  ensure();
  document.addEventListener('mouseover', e => {
    const el = anchorFrom(e.target);
    if (!el) return;
    if (el !== anchor) show(el);
  });
  document.addEventListener('mouseout', e => {
    if (!anchor) return;
    if (e.relatedTarget instanceof Node && anchor.contains(e.relatedTarget)) return;
    if (anchor.contains(e.target)) hide();
  });
  // keyboard: the focused control shows its tip, blur hides it
  document.addEventListener('focusin', e => {
    const el = anchorFrom(e.target);
    if (el && el.matches(':focus-visible')) show(el);
  });
  document.addEventListener('focusout', () => hide());
  // a click means the reader is acting, not reading
  document.addEventListener('mousedown', () => hide(), true);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') hide(); });
  document.addEventListener('scroll', () => hide(), true);
  window.addEventListener('resize', () => hide());
  // opening a popover closes every tooltip
  document.addEventListener('mc:popover', () => hide());
}

// Move any `title` already in the markup to `data-tip` right away, so a
// native tooltip never wins the race on a control hovered before the first
// mouseover reaches us (rare, but the two side by side look broken).
export function adopt(root = document) {
  root.querySelectorAll('[title]').forEach(el => {
    const t = el.getAttribute('title');
    el.removeAttribute('title');
    if (t) el.dataset.tip = t;
  });
}

// Keep the `title` idiom for code that reads a tooltip back
// (write.js reads the close action's) -- but through here, so it works
// after the attribute has moved.
export function tipOf(el) {
  return el ? (el.getAttribute('title') || el.dataset.tip || '') : '';
}
export function setTip(el, text) {
  if (!el) return;
  el.removeAttribute('title');
  if (text) el.dataset.tip = text; else delete el.dataset.tip;
  if (el === anchor) { if (text) show(el); else hide(); }
}
