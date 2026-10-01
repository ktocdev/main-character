// SPDX-License-Identifier: AGPL-3.0-or-later
// The design pages' theme toggle: the same client-only pin the app's
// Settings writes (localStorage `rag_theme`), so a choice made here carries
// into the app and back. The inline head script applies it before paint.
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
