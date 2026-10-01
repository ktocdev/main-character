// SPDX-License-Identifier: AGPL-3.0-or-later
// toggle.html's own script, kept out of the page so it runs under a
// CSP with no inline scripts (the web demo publishes these pages).
document.getElementById('live-row').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  b.setAttribute('aria-pressed', String(b.getAttribute('aria-pressed') !== 'true'));
});
