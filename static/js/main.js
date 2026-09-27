// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, refreshStatus } from './core.js';
import { state } from './state.js';
import * as write from './write.js';
import * as seed from './seed.js';
import * as lookup from './lookup.js';
import * as search from './search.js';
import * as dreams from './dreams.js';
import * as history from './history.js';
import * as entities from './entities.js';
import * as groups from './groups.js';
import * as triage from './triage.js';
import * as categories from './categories.js';
import * as patterns from './patterns.js';
import * as settings from './settings.js';
import * as cost from './cost.js';
import * as help from './help.js';
import * as wizard from './wizard.js';
import * as nav from './nav.js';
import * as tooltip from './tooltip.js';
import * as popover from './popover.js';

// ---- tabs ----
// Categories came back with Phase 3 (entries split by date, 2026-07-11).
const CATEGORIES_ENABLED = true;
$('cat-paused').hidden = CATEGORIES_ENABLED;
$('categories').classList.toggle('paused', !CATEGORIES_ENABLED);

// The tooltip and popover layers go first: every screen's controls carry a
// `title`, and the nav's own menu is a popover.
popover.init();
tooltip.init();
tooltip.adopt();

// Show one tab's pane and run its loader. `lit` is which nav item to light
// when that differs from the pane (the seed editor is a sub-view of write).
export function showTab(name, lit = name) {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  nav.markActive(lit);
  state.activeTab = name;
  $('tab-' + name).classList.add('active');
  if (name === 'write' && !$('entry-send').disabled) write.loadWriteLog();
  if (name === 'chat') lookup.restoreLookupLog();
  if (name === 'search') { $('search-q').focus(); search.showTab(); }
  if (name === 'entities') entities.loadEntities();
  if (name === 'categories' && CATEGORIES_ENABLED) categories.loadCategories();
  if (name === 'patterns') patterns.loadPatterns();
  if (name === 'dreams') dreams.loadDreams();
  if (name === 'history') history.loadHistory();
  if (name === 'triage') { triage.startTriage(); $('triage').focus(); }
  if (name === 'settings') settings.loadSettings();
}
nav.init(showTab);
document.querySelectorAll('nav [data-tab="categories"]').forEach(b =>
  b.classList.toggle('paused', !CATEGORIES_ENABLED));

// feature wiring, in original document order
write.init();
seed.init();
lookup.init();
search.init();
dreams.init();
history.init();
entities.init();
groups.init();
triage.init();
categories.init();
patterns.init();
settings.init();
cost.init();
help.init();

// Status carries the date style the write log stamps its entries with, so it
// has to land before the log renders -- otherwise the first paint uses the
// default and the dates quietly disagree with Settings. A status hiccup must
// not cost the author their session view, hence the catch.
await refreshStatus().catch(() => {});

// A journal with no API key yet (a fresh clone, no .env) gets the first-run
// wizard over the top of the app instead of the write screen. `configured`
// defaults to true and a failed status leaves it that way, so the wizard can
// only open on a journal that actually said it needs setting up -- opening it
// over a working journal would be the worse failure of the two.
if (state.configured) {
  write.loadWriteLog();  // restore the open session on page load
} else {
  wizard.open();
}
