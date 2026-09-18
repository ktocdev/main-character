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

// ---- tabs ----
// Categories came back with Phase 3 (entries split by date, 2026-07-11).
const CATEGORIES_ENABLED = true;
$('cat-paused').style.display = CATEGORIES_ENABLED ? 'none' : 'block';
$('categories').classList.toggle('paused', !CATEGORIES_ENABLED);
document.querySelector('nav button[data-tab="categories"]')
  .classList.toggle('paused', !CATEGORIES_ENABLED);

document.querySelectorAll('nav button[data-tab]').forEach(b => b.onclick = () => {
  document.querySelectorAll('nav button[data-tab]').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  state.activeTab = b.dataset.tab;
  $('tab-' + state.activeTab).classList.add('active');
  if (state.activeTab === 'write' && !$('entry-send').disabled) write.loadWriteLog();
  if (state.activeTab === 'chat') lookup.restoreLookupLog();
  if (state.activeTab === 'search') $('search-q').focus();
  if (state.activeTab === 'entities') entities.loadEntities();
  if (state.activeTab === 'categories' && CATEGORIES_ENABLED) categories.loadCategories();
  if (state.activeTab === 'patterns') patterns.loadPatterns();
  if (state.activeTab === 'dreams') dreams.loadDreams();
  if (state.activeTab === 'history') history.loadHistory();
  if (state.activeTab === 'triage') triage.startTriage();
  if (state.activeTab === 'settings') settings.loadSettings();
});

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
