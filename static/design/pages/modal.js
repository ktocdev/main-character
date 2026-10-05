// SPDX-License-Identifier: AGPL-3.0-or-later
// modal.html's own script, kept out of the page so it runs under a
// CSP with no inline scripts (the web demo publishes these pages).
const dialog = document.getElementById('try-modal');
document.getElementById('open-modal').onclick = () => dialog.showModal();
// a click on the backdrop lands on the dialog itself, outside the card's box
dialog.addEventListener('click', e => {
  const r = dialog.getBoundingClientRect();
  if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close();
});
