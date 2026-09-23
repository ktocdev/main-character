// SPDX-License-Identifier: AGPL-3.0-or-later
import { $, esc, installDemo, demoBuildWait } from './core.js';
import { restartServer } from './settings.js';
import { state } from './state.js';

// ---- the first-run wizard (Phase 3 item 5) ----
// What a fresh clone lands on when there is no .env: a key, a time zone, the
// category set, and a way in. It replaces hand-editing .env blind, which was
// the only documented route into a working journal.
//
// Everything it saves goes through endpoints that already existed --
// /api/settings for the three .env values, /api/organic/custom for a
// hand-added category, /api/restart to come back on the result. The wizard
// owns no storage and no writer of its own: close the tab halfway through and
// nothing was written.
//
// Deliberately NOT here: date format and model choice. Both have sensible
// defaults, both are cosmetic or cheap to change later, and both belong in
// Settings for the people who go looking. Categories are the exception --
// getting them right before the first entry has a real payoff, because a
// category added later does not retroactively tag what came before it.

// The collected answers, for the life of the overlay and no longer. The key
// exists here and in the POST that commits it, and nowhere else: no
// localStorage, because a draft API key outliving the tab it was typed into
// is not a convenience worth having.
let draft = {key: '', tz: '', disabled: [], custom: []};
let options = null;   // GET /api/settings -- the zone list and the built-ins
let step = 'key';

const STEPS = [
  ['key', 'your key'],
  ['zone', 'time zone'],
  ['categories', 'categories'],
  ['finish', 'start'],
];

const body = () => $('wizard-body');
const note = () => $('wizard-note');

// Said next to both demo doors, and only while it is true -- the build happens
// once per clone, and someone who looked around first and came back to finish
// has already paid for it. An unexplained twenty-second pause on the first
// screen a stranger sees would read as a program that had hung.
const buildNote = () => state.demoBuilt ? '' :
  ' The demo builds its search index on the first visit: ' + demoBuildWait()
  + '.';

function say(text, kind) {
  const n = note();
  n.textContent = text || '';
  n.className = kind ? 'wiz-note ' + kind : 'wiz-note';
}

// The step rail. Purely orientation -- the steps are not clickable, because
// going back to a validated key field to retype it is not a thing anyone
// wants, and forward is what the buttons are for.
function rail() {
  const at = STEPS.findIndex(s => s[0] === step);
  $('wizard-steps').innerHTML = STEPS.map(([id, label], i) =>
    '<span class="wiz-step' + (i === at ? ' at' : '') + (i < at ? ' done' : '')
    + '">' + esc(label) + '</span>').join('');
}

function render() {
  rail();
  say('');
  if (step === 'key') return renderKey();
  if (step === 'zone') return renderZone();
  if (step === 'categories') return renderCategories();
  if (step === 'import') return renderImport();
  return renderFinish();
}

// ---- step 1: the key ----

function renderKey() {
  body().innerHTML = [
    '<h2>Welcome to Main Character</h2>',
    '<p class="wiz-lede">A journal that remembers everything you have ever',
    '  written in it. It runs on this computer, against your own Anthropic',
    '  API key.</p>',
    '<label class="wiz-label" for="wiz-key">Anthropic API key</label>',
    '<input type="password" id="wiz-key" autocomplete="off" spellcheck="false"',
    '       placeholder="sk-ant-...">',
    '<p class="wiz-help">Get one from the',
    '  <a href="https://console.anthropic.com/settings/keys" target="_blank"',
    '     rel="noopener">Anthropic Console</a>. It is written to a',
    '  <code>.env</code> file on this computer and goes nowhere except to',
    '  Anthropic when you write. Checking it here makes one very small real',
    '  call, costing a fraction of a cent, because that is the only way',
    '  to find out whether a key actually works.</p>',
    '<div class="wiz-actions">',
    '  <button class="send" id="wiz-key-go">check this key</button>',
    '</div>',
    '<p class="wiz-aside">No key yet?',
    '  <a href="#" id="wiz-skip">look around the demo journal first</a>',
    '  (32 entries and 1 dream entry). The newest 3 are still in the open chat,',
    '  so close it to add them to journal memory. The life is fictional and the replies are recorded.',
    '  Nothing is spent. Demo writing is stored locally until the next demo visit, when all demo data is reset.' + buildNote() + '</p>',
  ].join('\n');

  const input = $('wiz-key');
  input.focus();
  input.onkeydown = e => { if (e.key === 'Enter') checkKey(); };
  $('wiz-key-go').onclick = checkKey;
  $('wiz-skip').onclick = e => { e.preventDefault(); lookAround(); };
}

async function checkKey() {
  const input = $('wiz-key'), go = $('wiz-key-go');
  const key = input.value.trim();
  if (!key) { say('Paste a key first.', 'error'); input.focus(); return; }

  go.disabled = true;
  say('checking…');
  let r;
  try {
    // Not core.js's api(): a key that does not work is an ordinary answer
    // here, and it belongs under the field rather than in an alert box.
    const res = await fetch('/api/setup/validate-key', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key}),
    });
    r = await res.json();
  } catch (e) {
    r = {ok: false, error: 'could not reach the server.'};
  }
  go.disabled = false;

  if (!r.ok) {
    say(r.error || 'that key did not work.', 'error');
    input.focus();
    input.select();
    return;
  }
  draft.key = key;
  step = 'zone';
  render();
}

// ---- step 2: the time zone ----

function renderZone() {
  // The browser is the only thing here that knows where the person actually
  // is. MC_TIMEZONE's default is "the server's local zone", which is right
  // exactly until the journal runs somewhere other than the machine its
  // author is sitting at.
  let detected = '';
  try { detected = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (e) {}
  const zones = (options && options.options && options.options.timezones) || [];
  const usable = zones.includes(detected);

  body().innerHTML = [
    '<h2>Where are you?</h2>',
    '<p class="wiz-lede">Your entries are stamped in this zone, and it is the',
    '  companion&rsquo;s sense of what &ldquo;today&rdquo; means.</p>',
    '<label class="wiz-label" for="wiz-tz">Time zone</label>',
    '<div id="wiz-tz-slot"></div>',
    '<p class="wiz-help">Changeable later in Settings. Stored dates never',
    '  change with it. Only the way they are read does.</p>',
    '<div class="wiz-actions">',
    '  <button class="send" id="wiz-zone-go">continue</button>',
    '</div>',
  ].join('\n');

  const slot = $('wiz-tz-slot');
  if (!zones.length) {
    // config._zone() swallows a failed lookup, so without saying this the
    // picker would simply be missing and never explain itself.
    const warn = document.createElement('p');
    warn.className = 'wiz-warn';
    warn.textContent = 'No time zone database is installed, so this cannot be '
      + 'set here. The journal will use this computer’s own zone, which '
      + 'is almost certainly right. Installing tzdata (already in '
      + 'requirements.txt) turns the picker on.';
    slot.appendChild(warn);
    draft.tz = '';
  } else {
    const sel = document.createElement('select');
    sel.id = 'wiz-tz';
    sel.add(new Option('use this computer’s zone', ''));
    for (const z of zones) sel.add(new Option(z, z));
    sel.value = usable ? detected : '';
    slot.appendChild(sel);
    if (detected && !usable) {
      const warn = document.createElement('p');
      warn.className = 'wiz-warn';
      warn.textContent = 'Your browser reports ' + detected + ', which this '
        + 'machine cannot resolve. Pick the closest one, or leave it on this '
        + 'computer’s own zone.';
      slot.appendChild(warn);
    }
  }
  $('wiz-zone-go').onclick = () => {
    const sel = $('wiz-tz');
    draft.tz = sel ? sel.value : '';
    step = 'categories';
    render();
  };
}

// ---- step 3: categories ----

function renderCategories() {
  const cats = (options && options.options && options.options.categories) || [];
  body().innerHTML = [
    '<h2>What does your life have in it?</h2>',
    '<p class="wiz-lede">These are the tags the journal suggests on a new',
    '  entry. They are all on by default. Turn off any that do not apply, so they',
    '  never show up as noise.</p>',
    '<div id="wiz-cats"></div>',
    '<h3 class="wiz-sub">Add your own</h3>',
    '<p class="wiz-help">Worth doing now if you already know you want one. A',
    '  category added later does not go back and tag what you wrote before it',
    '  existed, so a <code>gaming</code> or <code>finances</code> added here',
    '  earns more than the same one added after a dozen entries.</p>',
    '<div class="wiz-newcat">',
    '  <input type="text" id="wiz-cat-name" placeholder="category name…">',
    '  <input type="text" id="wiz-cat-keys" placeholder="keywords, comma-separated (optional)">',
    '  <button class="quiet" id="wiz-cat-add">add</button>',
    '</div>',
    '<div id="wiz-custom"></div>',
    '<div class="wiz-actions">',
    '  <button class="send" id="wiz-cats-go">continue</button>',
    '</div>',
  ].join('\n');

  const box = $('wiz-cats');
  const off = new Set(draft.disabled);
  for (const c of cats) {
    const row = document.createElement('label');
    row.className = 'wiz-check';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.value = c.name;
    cb.checked = !off.has(c.name);
    const text = document.createElement('span');
    text.innerHTML = '<strong>' + esc(c.name) + '</strong> · '
      + esc(c.description || '');
    row.append(cb, text);
    box.appendChild(row);
  }

  drawCustom();
  $('wiz-cat-add').onclick = addCustom;
  $('wiz-cat-name').onkeydown = e => { if (e.key === 'Enter') addCustom(); };
  $('wiz-cat-keys').onkeydown = e => { if (e.key === 'Enter') addCustom(); };

  $('wiz-cats-go').onclick = () => {
    const boxes = [...document.querySelectorAll('#wiz-cats input[type=checkbox]')];
    const disabled = boxes.filter(b => !b.checked).map(b => b.value);
    // The server refuses an empty tagger, and finding that out at the commit
    // -- two screens later, with a validated key in hand -- would be a poor
    // place to learn it.
    if (boxes.length && disabled.length === boxes.length) {
      say('Leave at least one category on. A journal that can tag '
          + 'nothing is worse off than one with a category it never uses.',
          'error');
      return;
    }
    draft.disabled = disabled;
    step = 'finish';
    render();
  };
}

function drawCustom() {
  const box = $('wiz-custom');
  if (!box) return;
  box.textContent = '';
  for (const [i, c] of draft.custom.entries()) {
    const row = document.createElement('div');
    row.className = 'wiz-customrow';
    const label = document.createElement('span');
    label.textContent = c.keywords ? c.name + ' · ' + c.keywords : c.name;
    const drop = document.createElement('button');
    drop.className = 'quiet';
    drop.textContent = '×';
    drop.title = 'remove';
    drop.onclick = () => { draft.custom.splice(i, 1); drawCustom(); };
    row.append(label, drop);
    box.appendChild(row);
  }
}

function addCustom() {
  const name = $('wiz-cat-name').value.trim();
  if (!name) return;
  if (draft.custom.some(c => c.name.toLowerCase() === name.toLowerCase())) {
    say('You already added that one.', 'error');
    return;
  }
  draft.custom.push({name, keywords: $('wiz-cat-keys').value.trim()});
  $('wiz-cat-name').value = '';
  $('wiz-cat-keys').value = '';
  $('wiz-cat-name').focus();
  say('');
  drawCustom();
}

// ---- step 4: the way in ----
// Three doors, in this order on purpose: starting fresh is the ordinary path,
// importing is for the minority who happen to have a Claude export, and the
// demo is a look around rather than a way to use the thing.

function renderFinish() {
  body().innerHTML = [
    '<h2>You are set up</h2>',
    '<p class="wiz-lede">Your key and settings are ready to write. Pick where',
    '  to land. The journal restarts either way and takes a',
    '  moment.</p>',
    '<div class="wiz-doors">',
    '  <button class="send" id="wiz-write">start writing</button>',
    '  <button class="quiet" id="wiz-import">import a Claude export</button>',
    '  <button class="quiet" id="wiz-demo">just look around the demo first</button>',
    '</div>',
    '<p class="wiz-help">The journal builds itself from your entries, starting',
    '  with the first one. Nothing needs setting up before you write.',
    buildNote() ? '<br>' + buildNote() : '',
    '</p>',
  ].join('\n');
  $('wiz-write').onclick = () => finish('journal');
  $('wiz-import').onclick = () => { step = 'import'; render(); };
  $('wiz-demo').onclick = () => finish('seed');
}

function renderImport() {
  body().innerHTML = [
    '<h2>Import a Claude export</h2>',
    '<p class="wiz-lede">Optional, and only useful if you already have one.',
    '  Your messages become journal entries, so the journal has a history to',
    '  work with from day one. Claude&rsquo;s replies are never stored as',
    '  journal memory.</p>',
    '<ol class="wiz-list">',
    '  <li>Request your data from claude.ai &rarr; Settings &rarr; Privacy.',
    '    It arrives by email as a zip.</li>',
    '  <li>Unzip it and find <code>conversations.json</code>.</li>',
    '  <li>Save your settings below, then run this in a terminal, in the',
    '    folder you cloned:</li>',
    '</ol>',
    '<pre class="wiz-cmd"><code>python bulk_import.py path/to/conversations.json</code></pre>',
    '<p class="wiz-help">It reads the export, splits it into one entry per',
    '  day, embeds it on this machine and writes markdown backups. The',
    '  embedding is free and makes no Claude calls.</p>',
    '<div class="wiz-actions">',
    '  <button class="send" id="wiz-import-go">save and start the journal</button>',
    '  <button class="quiet" id="wiz-import-back">back</button>',
    '</div>',
  ].join('\n');
  $('wiz-import-go').onclick = () => finish('journal');
  $('wiz-import-back').onclick = () => { step = 'finish'; render(); };
}

// ---- committing ----

// The three .env values go through the settings route, which already owns the
// whitelist, the atomic write and the 0600. Custom categories go through the
// route the Categories tab uses. Neither is reimplemented here.
async function commit() {
  const values = {ANTHROPIC_API_KEY: draft.key};
  // Only when there is one to write. An empty MC_TIMEZONE would be refused by
  // the validator on a machine with no tz database, and "" means "this
  // computer's zone" anyway -- which is what leaving the line out already does.
  if (draft.tz) values.MC_TIMEZONE = draft.tz;
  if (draft.disabled.length) {
    values.MC_DISABLED_CATEGORIES = draft.disabled.join(',');
  }

  const res = await fetch('/api/settings', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({values}),
  });
  const saved = await res.json();
  if (!res.ok || saved.error) {
    throw new Error(saved.error || 'the settings could not be saved');
  }

  // After the key, never before: these are the optional half, and a custom
  // category that fails is a line in the Categories tab the author can add
  // again in ten seconds. The key is the part that must not be lost, so it is
  // already on disk by the time anything here can go wrong.
  const failed = [];
  for (const c of draft.custom) {
    try {
      const r = await fetch('/api/organic/custom', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: c.name, keywords: c.keywords || ''}),
      });
      const rb = await r.json();
      if (!r.ok || rb.error) failed.push(c.name);
    } catch (e) {
      failed.push(c.name);
    }
  }
  return failed;
}

async function finish(into) {
  const buttons = [...document.querySelectorAll(
    '.wiz-doors button, .wiz-actions button')];
  buttons.forEach(b => { b.disabled = true; });
  say('saving…');
  let failed;
  try {
    failed = await commit();
  } catch (e) {
    say(e.message || String(e), 'error');
    buttons.forEach(b => { b.disabled = false; });
    return;
  }
  if (failed.length) {
    // Said, not swallowed -- but not a reason to stop, since the key landed.
    say('Saved, but could not add: ' + failed.join(', ')
        + '. You can add them again in the categories tab.', 'warn');
  }
  // The demo has to exist before there is anything to restart into. Built on
  // demand rather than assumed: it is gitignored, so a fresh clone has none,
  // and the restart route would otherwise refuse with an instruction to go
  // and run a command -- in a terminal, which is the one place a first-run
  // flow cannot follow someone.
  if (into === 'seed' && !await installDemo(say)) {
    buttons.forEach(b => { b.disabled = false; });
    return;
  }
  // restartServer reloads the page itself once a *different* process answers,
  // and the reloaded page finds the journal configured -- so the wizard does
  // not open again.
  try {
    await restartServer(note(), true, into);
  } finally {
    // A refusal or timeout leaves this page open and must allow a retry.
    buttons.forEach(b => { b.disabled = false; });
  }
}

// The demo, from the key step, with nothing collected and nothing written.
// A key is not needed to read a journal whose replies were recorded months
// ago -- and requiring one to look would invert the reason the demo exists.
async function lookAround() {
  const go = $('wiz-key-go'), skip = $('wiz-skip');
  if (go) go.disabled = true;
  if (skip) skip.style.pointerEvents = 'none';
  if (await installDemo(say)) {
    // `saved: false` -- there is genuinely nothing saved to reassure anyone
    // about, and a refusal that claimed otherwise would be inventing one.
    await restartServer(note(), false, 'seed');
  }
  if (go) go.disabled = false;
  if (skip) skip.style.pointerEvents = '';
}

// ---- opening ----

export async function open() {
  const overlay = $('setup-wizard');
  if (!overlay) return;
  overlay.hidden = false;
  document.body.classList.add('setting-up');
  step = 'key';
  body().textContent = 'loading…';
  try {
    const res = await fetch('/api/settings');
    options = res.ok ? await res.json() : null;
  } catch (e) {
    options = null;
  }
  // A missing payload is survivable: the zone step says the picker is
  // unavailable and the category step renders empty, while the key -- the one
  // thing that actually has to be collected -- is unaffected.
  render();
}
