import { $, refreshStatus } from './core.js';
import { state } from './state.js';
import * as write from './write.js';
import * as lookup from './lookup.js';
import * as search from './search.js';
import * as dreams from './dreams.js';
import * as history from './history.js';
import * as entities from './entities.js';
import * as groups from './groups.js';
import * as triage from './triage.js';
import * as categories from './categories.js';
import * as patterns from './patterns.js';

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
});

// feature wiring, in original document order
write.init();
lookup.init();
search.init();
dreams.init();
history.init();
entities.init();
groups.init();
triage.init();
categories.init();
patterns.init();

write.loadWriteLog();  // restore the open session on page load
refreshStatus();
