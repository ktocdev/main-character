// SPDX-License-Identifier: AGPL-3.0-or-later
// tooltip.html's own script, kept out of the page so it runs under a
// CSP with no inline scripts (the web demo publishes these pages).
import * as tooltip from '../../js/tooltip.js';
tooltip.init();
tooltip.adopt(document.getElementById('live'));
