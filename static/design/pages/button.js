// SPDX-License-Identifier: AGPL-3.0-or-later
// button.html's own script, kept out of the page so it runs under a
// CSP with no inline scripts (the web demo publishes these pages).
import { busy } from '../../js/core.js';

// the busy row is live: a click holds the button for two seconds
for (const b of document.querySelectorAll('#busy-live button'))
  b.addEventListener('click', () => busy(b, () => new Promise(r => setTimeout(r, 2000))));
