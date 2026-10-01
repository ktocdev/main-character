// SPDX-License-Identifier: AGPL-3.0-or-later
// badge.html's own script, kept out of the page so it runs under a
// CSP with no inline scripts (the web demo publishes these pages).
import { itemCard } from '../../js/card.js';
document.getElementById('card-out').appendChild(itemCard({
  type: 'summary', title: 'Week of September 22', href: '#', size: 'sm',
  badges: ['Sep 22–28', '7 entries'],
}));
