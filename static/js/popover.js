// SPDX-License-Identifier: AGPL-3.0-or-later
// ---- popovers, menus and the one rule between them ----
// Popovers, menus and tooltips are mutually exclusive: opening one closes the
// rest, Escape closes, a click outside closes. Each owner registers a closer
// and calls `opened(name)` when it opens; that is the whole protocol.

const closers = new Map();

export function register(name, close, contains) {
  closers.set(name, { close, contains });
}

export function closeAll(except) {
  for (const [name, c] of closers) if (name !== except) c.close();
}

// Announce an opening. Tooltips listen for the event and hide.
export function opened(name) {
  closeAll(name);
  document.dispatchEvent(new CustomEvent('mc:popover', { detail: name }));
}

export function init() {
  document.addEventListener('click', e => {
    for (const c of closers.values()) {
      if (c.contains && c.contains(e.target)) continue;
      c.close();
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeAll();
  });
}
