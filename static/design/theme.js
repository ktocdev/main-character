// SPDX-License-Identifier: AGPL-3.0-or-later
// Every design page's theme pre-paint: the same client-only pin the app's
// Settings writes, applied before first paint. A file rather than an inline
// script, so the pages run under a CSP with no inline scripts.
try {
  var _t = localStorage.getItem('rag_theme');
  if (_t === 'light' || _t === 'dark') document.documentElement.dataset.theme = _t;
} catch (e) {}
