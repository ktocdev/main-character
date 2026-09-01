import { $, esc } from './core.js';

// ---- settings ----
// A UI over .env. Nothing here changes the running process: the app reads
// its config once at import, so every save is followed by "restart to apply"
// rather than by the setting quietly taking effect. Saying so is the whole
// difference between a settings screen people trust and one they don't.

let loaded = null;      // the last GET, so save can send only what changed

function fieldRow(id, label, help) {
  return `<div class="set-row">
    <label for="${id}">${esc(label)}</label>
    <div class="set-control" id="${id}-control"></div>
    <p class="set-help">${help}</p>
  </div>`;
}

// Between a save and the next restart the file and the running process
// disagree. The control shows the file (so a save is visibly persisted), and
// this marks the row with what the journal is *still* doing meanwhile --
// without it, a saved setting is indistinguishable from a live one.
function markPending(controlId, key, describe) {
  const stored = loaded.values[key] || '', active = (loaded.active || {})[key] || '';
  if (stored === active) return;
  const p = document.createElement('p');
  p.className = 'set-warn';
  p.textContent = 'Saved. This journal is still running as '
    + describe(active) + ' until you restart it.';
  $(controlId).appendChild(p);
}

// A picker can only send back what it can show. When .env holds a value this
// list has no option for -- a hand-set date format, a zone this machine can't
// resolve -- the select displays some *other* option, and saving from it would
// overwrite the author's line with a value they never chose. Lock the control
// out of the save instead, and say why it looks the way it does.
function lockUnshowable(sel, controlId, stored, describe) {
  if (!stored || [...sel.options].some(o => o.value === stored)) return;
  sel.dataset.locked = '1';
  const p = document.createElement('p');
  p.className = 'set-warn';
  p.textContent = '.env sets this to ' + describe + ', which this list can\'t '
    + 'show, so saving here leaves it alone. Edit .env to change it.';
  $(controlId).appendChild(p);
}

// What an empty box will actually do. Three different answers, and saying the
// wrong one is how someone ends up believing they have a ceiling they don't:
//
//   - no `caps` block at all -- this journal predates the enforcement and is
//     running code that reads none of these. Not "no limit": that would
//     describe a setting, when what is true is that nothing is checking.
//   - a default of 0 -- blank really does mean no ceiling, and "0 turns it
//     off" is then a distinction without a difference, so it isn't offered.
//   - a real default -- name the figure. "Blank uses the default" without it
//     sends the reader to config.py to find out what they just agreed to.
function capHelp(live) {
  if (!live || live.limit === undefined) {
    return 'This journal has not said what it is enforcing — it is '
      + 'running a build from before the spend caps. Restart it.';
  }
  if (!live.default) return 'Leave it blank for no ceiling at all.';
  return 'Blank uses the default, ' + money(live.default) + '. 0 turns it off.';
}

// A cap input. Blank is meaningful here and means "use the default", so the
// placeholder has to name that default -- an empty box next to the words
// "stop after" otherwise reads as no ceiling at all, which is the one thing
// this pane must never imply while a ceiling is in force.
function capField(id, key, values, live, fmt) {
  const box = $(id + '-control');
  if (!box) return;
  const input = document.createElement('input');
  input.type = 'text';
  input.id = id;
  input.value = values[key] || '';
  input.autocomplete = 'off';
  // The *default*, not the limit in force: this is what the box would mean if
  // left empty, and the limit in force is already spelled out below. Blank
  // when the journal hasn't said -- an invented "no limit" there would be the
  // page answering a question it was never told the answer to.
  input.placeholder = !live || live.limit === undefined ? ''
    : live.default ? 'default: ' + fmt(live.default) : 'no limit';
  box.appendChild(input);

  // The process read its ceilings at import, same as every other setting.
  // Comparing the file against what is actually in force is the only way to
  // tell a saved cap from a live one -- and a saved-but-not-live cap is
  // precisely the state someone believes they are protected in.
  const stored = (values[key] || '').trim();
  if (stored && live && Number(stored) !== Number(live.limit)) {
    const p = document.createElement('p');
    p.className = 'set-warn';
    p.textContent = 'Saved. This journal is still stopping at '
      + (live.limit ? fmt(live.limit) : 'nothing') + ' until you restart it.';
    box.parentElement.appendChild(p);
  }
}

// "used of limit" for one ceiling, or "used, no limit set".
const money = n => '$' + Number(n).toFixed(2);

function usedLine(label, cap, fmt) {
  const p = document.createElement('p');
  p.className = 'set-help';
  p.textContent = label + ': ' + fmt(cap.used)
    + (cap.limit ? ' of ' + fmt(cap.limit) : ' \u2014 no limit set');
  return p;
}

export async function loadSettings() {
  const body = $('settings-body');
  body.textContent = 'loading…';
  $('settings-save').disabled = true;
  let s;
  try {
    const res = await fetch('/api/settings');
    s = await res.json();
    // an error body is valid JSON and would otherwise walk straight into the
    // rendering below, throw on a missing field, and leave this pane sitting
    // on "loading…" with no idea what went wrong
    if (!res.ok || !s || !s.values || !s.options) {
      throw new Error((s && s.error) || 'the server did not return settings');
    }
  } catch (e) {
    body.textContent = 'could not read settings — ' + (e.message || e);
    return;
  }
  loaded = s;
  const v = s.values, o = s.options;

  body.innerHTML = `
    <section class="set-group">
      <h3>Journal</h3>
      ${fieldRow('set-date', 'Date display',
        'How dates are shown. What gets stored never changes — entries are '
        + 'always kept in ISO form, so switching back and forth is safe.')}
      ${fieldRow('set-tz', 'Time zone',
        'The zone your entries are stamped in, and the companion\'s sense of '
        + '"now". Leave it as your computer\'s zone unless the journal runs '
        + 'somewhere else.')}
      ${fieldRow('set-lang', 'Language',
        'English is the only option today. The setting exists so adding '
        + 'another later is a configuration change, not a rebuild.')}
    </section>
    <section class="set-group">
      <h3>Models &amp; cost</h3>
      ${fieldRow('set-companion-model', 'Companion model',
        'The voice you write to. This is the one place model quality is '
        + 'felt directly, so it is worth spending more here than anywhere '
        + 'else.')}
      ${fieldRow('set-companion-effort', 'Companion effort',
        'How hard the companion thinks before answering. Higher is slower '
        + 'and costs more. The choices come from the model above &mdash; '
        + 'not every model offers the same ones.')}
      ${fieldRow('set-processing-model', 'Processing model',
        'Everything that happens in the background: tagging, entities, '
        + 'summaries, arcs, patterns, dreams. It runs in bulk and is where '
        + 'most of the spend goes, so it is the useful place to trade down.')}
      ${fieldRow('set-max-session', 'Stop after (dollars per session)',
        'A ceiling on one session, counted from when the journal last '
        + 'started and cleared when you close a chat. '
        + capHelp((s.caps || {}).session))}
      ${fieldRow('set-max-spend', 'Stop after (dollars per month)',
        'A ceiling on the calendar month, kept in a small file so it '
        + 'survives restarts. ' + capHelp((s.caps || {}).monthly))}
      <div class="set-row" id="set-caps-control"></div>
      <div class="set-row">
        <label>Session cost</label>
        <div class="set-control" id="set-cost-control"></div>
      </div>
    </section>
    <section class="set-group">
      <h3>API key</h3>
      <div class="set-row">
        <label for="set-key">Anthropic API key</label>
        <div class="set-control">
          <input type="password" id="set-key" autocomplete="off"
                 placeholder="${s.api_key_set ? 'a key is set — type a new one to replace it' : 'no key set'}">
        </div>
        <p class="set-help">Write-only: the key is never sent back to this
          page, not even partially. Leave it blank to keep the one you have.</p>
      </div>
    </section>`;

  // date format
  const date = document.createElement('select');
  date.id = 'set-date';
  for (const f of o.date_formats) {
    const opt = new Option(`${f.example}  (${f.style})`, f.value);
    opt.selected = f.value === v.MC_DATE_FORMAT;
    date.add(opt);
  }
  $('set-date-control').appendChild(date);
  lockUnshowable(date, 'set-date-control', v.MC_DATE_FORMAT || '',
    'the format ' + (v.MC_DATE_FORMAT || ''));
  markPending('set-date-control', 'MC_DATE_FORMAT',
    a => (o.date_formats.find(f => f.value === a) || {}).example || a);

  // time zone — the list is what this machine can actually resolve
  const tz = document.createElement('select');
  tz.id = 'set-tz';
  tz.add(new Option(`use this computer's zone (${s.resolved_timezone})`, ''));
  for (const z of o.timezones) {
    const opt = new Option(z, z);
    opt.selected = z === v.MC_TIMEZONE;
    tz.add(opt);
  }
  if (!v.MC_TIMEZONE) tz.value = '';
  tz.disabled = !s.tz_database;
  $('set-tz-control').appendChild(tz);
  lockUnshowable(tz, 'set-tz-control', v.MC_TIMEZONE || '',
    'the zone ' + (v.MC_TIMEZONE || ''));
  if (!s.tz_database) {
    // config._zone() swallows the lookup failure, so without this the picker
    // would offer nothing and never say why
    const warn = document.createElement('p');
    warn.className = 'set-warn';
    warn.textContent = 'No time zone database is installed, so zones can\'t '
      + 'be chosen here. Installing tzdata (already in requirements.txt) '
      + 'enables this.';
    $('set-tz-control').appendChild(warn);
  }
  markPending('set-tz-control', 'MC_TIMEZONE',
    a => a || ("this computer's zone (" + s.resolved_timezone + ')'));

  // language
  const lang = document.createElement('select');
  lang.id = 'set-lang';
  for (const l of o.languages) {
    const opt = new Option(l.label, l.value);
    opt.selected = l.value === v.MC_LANGUAGE;
    lang.add(opt);
  }
  lang.disabled = o.languages.length < 2;
  $('set-lang-control').appendChild(lang);
  markPending('set-lang-control', 'MC_LANGUAGE',
    a => (o.languages.find(l => l.value === a) || {}).label || a);

  // ---- models ----
  // Both pickers offer the same lineup: nothing is restricted by bucket. The
  // difference between the two is the guidance beside them, not the options
  // inside them.
  //
  // A server running an older build serves a payload with no `models` in it.
  // Unguarded that is a TypeError on the first `o.models.find`, which takes
  // out every control built after it -- both pickers and the spend note --
  // and leaves the section looking empty rather than broken. An empty
  // settings pane that throws in the console is a bug report nobody can
  // write, so name the cause instead.
  if (!Array.isArray(o.models) || !o.models.length) {
    const stale = document.createElement('p');
    stale.className = 'set-warn';
    stale.textContent = 'The server has not been restarted since these '
      + 'settings were added, so it cannot say which models it offers. '
      + 'Restart it and reload this page.';
    $('set-companion-model-control').appendChild(stale);
  } else {
    const modelName = m => (o.models.find(x => x.value === m) || {}).label || m;
    const priceOf = m => {
      const pr = (o.models.find(x => x.value === m) || {}).price;
      return pr ? '$' + pr.in + ' / $' + pr.out + ' per Mtok' : 'price unlisted';
    };
    function modelSelect(id, current) {
      const sel = document.createElement('select');
      sel.id = id;
      for (const m of o.models) {
        const opt = new Option(m.label + ' \u2014 ' + priceOf(m.value), m.value);
        opt.selected = m.value === current;
        sel.add(opt);
      }
      $(id + '-control').appendChild(sel);
      lockUnshowable(sel, id + '-control', current || '',
        'the model ' + (current || ''));
      return sel;
    }

    const cModel = modelSelect('set-companion-model', v.MC_COMPANION_MODEL);
    markPending('set-companion-model-control', 'MC_COMPANION_MODEL', modelName);

    // The companion is the one surface where dropping a tier is felt in the
    // writing itself, so say so at the moment it is chosen. The processing
    // picker gets no equivalent: trading down there is the intended lever.
    const voiceWarn = document.createElement('p');
    voiceWarn.className = 'set-warn';
    $('set-companion-model-control').appendChild(voiceWarn);
    const showVoiceWarning = () => {
      voiceWarn.textContent = /opus/.test(cModel.value) ? ''
        : modelName(cModel.value) + ' costs less, but the companion\'s replies '
          + 'are what you actually read. Opus reads best as the voice.';
    };

    // Effort levels belong to the model, so this list is rebuilt whenever the
    // model changes rather than filtered at save time. A model with no levels
    // at all (Haiku 4.5 takes no effort parameter) disables the control, which
    // also drops it from the save -- see save()'s `disabled` check.
    const effort = document.createElement('select');
    effort.id = 'set-companion-effort';
    const effortNote = document.createElement('p');
    effortNote.className = 'set-warn';
    $('set-companion-effort-control').append(effort, effortNote);

    function fillEfforts(keep) {
      // A model .env names but this list can't show locks the model picker
      // (see lockUnshowable), and its effort levels are just as unknowable:
      // filling the list from whichever option happened to be selected first
      // would offer levels belonging to a different model, silently rewrite
      // the author's stored effort to one of them, and get the whole save
      // 400'd -- date format and all -- when the server validated it against
      // the model actually in .env. So the effort is locked with the model.
      if (cModel.dataset.locked) {
        effort.textContent = '';
        effort.disabled = true;
        effortNote.textContent = '.env sets the companion model to one this '
          + "list can't show, so its effort levels aren't known here either. "
          + 'Edit .env to change either.';
        return;
      }
      const levels = (o.models.find(m => m.value === cModel.value) || {}).efforts || [];
      effort.textContent = '';
      for (const level of levels) effort.add(new Option(level, level));
      effort.disabled = !levels.length;
      effortNote.textContent = levels.length ? ''
        : modelName(cModel.value) + ' takes no effort setting, so it is left off '
          + 'the call entirely rather than sent and rejected.';
      // Keep the author's level across a model change when the new model still
      // offers it; otherwise fall back to that model's top level, which is
      // nearer the original intent than the list's first entry.
      if (levels.includes(keep)) effort.value = keep;
      else if (levels.length) effort.value = levels[levels.length - 1];
    }
    fillEfforts(v.MC_COMPANION_EFFORT);
    showVoiceWarning();
    cModel.onchange = () => { fillEfforts(effort.value); showVoiceWarning(); };
    markPending('set-companion-effort-control', 'MC_COMPANION_EFFORT', a => a);

    modelSelect('set-processing-model', v.MC_PROCESSING_MODEL);
    markPending('set-processing-model-control', 'MC_PROCESSING_MODEL', modelName);
  }

  // ---- spend caps ----
  // There is no cap control here, because there is no cap: nothing reads
  // these values before a call yet. An input would let someone set a ceiling
  // and believe it protected them, and even a read-only "no limit" row is
  // just a setting that does nothing taking up space.
  //
  // The exception is a value that is already in .env -- hand-added, or saved
  // through the API, which whitelists both keys. That author has every reason
  // to think their cap is working, and this is the only place that can tell
  // them otherwise. So: say what is true always, and name the stale values
  // only when they exist.
  // Rendered with the rest of the section rather than on a disclosure's
  // first open. The figure is a snapshot of the moment the pane was loaded --
  // there is nothing to subscribe to, since it only moves when a call the
  // author just triggered comes back, and a number that ticks on its own in a
  // settings pane invites watching it. Reopening Settings re-reads it.
  renderCost();

  capField('set-max-session', 'MC_MAX_SESSION_SPEND', v,
           (s.caps || {}).session, money);
  capField('set-max-spend', 'MC_MAX_MONTHLY_SPEND', v,
           (s.caps || {}).monthly, money);

  const caps = $('set-caps-control');
  if (!s.spend_caps_enforced) {
    // A server older than the enforcement stores these and reads none of
    // them. Saying so is the whole point of the flag: a pane that showed the
    // fields without this would be claiming a ceiling that does not exist.
    const note = document.createElement('p');
    note.className = 'set-warn';
    note.textContent = 'This journal stores these but nothing checks them '
      + '\u2014 it is running a build from before the caps were enforced. '
      + 'Restart it to pick up the new one.';
    caps.appendChild(note);
  } else {
    caps.appendChild(usedLine('This session', s.caps.session, money));
    caps.appendChild(usedLine('This month (' + s.caps.monthly.month + ')',
                              s.caps.monthly, money));
    if (v.MC_MAX_SESSION_TOKENS) {
      // It was a token count, briefly, and nothing reads it now. Silence here
      // would leave a line in .env that looks exactly like a cap.
      const dead = document.createElement('p');
      dead.className = 'set-warn';
      dead.textContent = 'Your .env still sets MC_MAX_SESSION_TOKENS. That '
        + 'setting was replaced by the dollar figure above and is ignored \u2014 '
        + 'delete the line so it stops looking like a ceiling.';
      caps.appendChild(dead);
    }
    if (!s.caps.monthly.recording) {
      const mock = document.createElement('p');
      mock.className = 'set-warn';
      mock.textContent = 'Mock mode: the month\u2019s figure is whatever real '
        + 'use last recorded. Nothing spent here reaches it, because none of '
        + 'it is real.';
      caps.appendChild(mock);
    }
  }

  const backstop = document.createElement('p');
  backstop.className = 'set-help';
  backstop.textContent = 'These are estimates from list prices, checked '
    + 'before each call \u2014 so the call that crosses a line finishes, and '
    + 'the next one is refused. The backstop that does not depend on this app '
    + 'being right is a spend limit on your Anthropic Console account.';
  caps.appendChild(backstop);

  $('settings-save').disabled = false;
  $('settings-note').textContent = '';
  syncSeedButton();
}

// A save is only half a change: the process read .env at import. Rather than
// send the author to a terminal, ask the server to come back on its own and
// reload the page once it answers again. The fetches below are *expected* to
// fail for a few seconds -- the socket closes while the process is restarting.
// Which process is answering right now, or null if none is. /api/status
// returning 200 is not evidence the restart happened: uvicorn keeps serving
// while it drains, so the poll below has to watch for the id to *change*.
async function instanceId() {
  try {
    const res = await fetch('/api/status', {cache: 'no-store'});
    return res.ok ? ((await res.json()).instance || null) : null;
  } catch (e) {
    return null;      // still down
  }
}

async function restartServer(note, saved = true, into = 'journal') {
  note.className = 'set-note';
  note.textContent = 'restarting to apply…';
  const before = await instanceId();
  let r;
  try {
    r = await (await fetch('/api/restart', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({into}),
    })).json();
  } catch (e) {
    r = {error: 'could not reach the server.'};
  }
  if (r.error) {
    note.className = 'set-note error';
    // After a save the refusal has to say the save survived it, or a restart
    // that didn't happen reads as an edit that didn't happen. Pressed on its
    // own there is nothing to reassure anyone about, and saying "your change
    // is saved" when nothing was changed is just confusing.
    note.textContent = saved
      ? r.error + ' Your change is saved in .env; restart the journal to apply it.'
      : r.error;
    return;
  }
  for (let i = 0; i < 90; i++) {
    await new Promise(done => setTimeout(done, 700));
    const now = await instanceId();
    if (now && now !== before) {    // a different process: it really is back
      location.reload();
      return;
    }
  }
  note.className = 'set-note error';
  note.textContent = (saved ? 'saved, but ' : '')
    + 'the journal did not come back — start it again the way you normally do.';
}

// The restart the save flow already performs, reachable on its own. Item 7
// made the app restart itself, but only as the tail of a successful save --
// which left every other path telling the author to open a terminal: the 409
// while the pipeline runs, the 501, a .env edited by hand, and the
// stale-payload banner above that says "restart it and reload this page" with
// no way to comply. Same function on purpose: a second restart path would
// drift from the refusals and from the instance-id poll that is the only
// evidence the process actually changed.
async function restartNow() {
  const btn = $('settings-restart');
  btn.disabled = true;
  await restartServer($('settings-note'), false);
  // Only reached when the restart was refused or never landed -- a successful
  // one reloads the page out from under this line.
  btn.disabled = false;
}

// ---- session cost ----
// Fetched when Models & Cost is opened, not polled. The figure only moves
// when a call the author just triggered comes back, so there is nothing to
// subscribe to -- and a number that ticks on its own in a settings pane
// invites watching it, which is the opposite of the point.
export function costLine(c) {
  const dollars = c.total.dollars;
  // Below a cent, a rounded figure reads as free. Say "under $0.01" instead:
  // the honest statement is that it is small, not that it is nothing.
  const money = dollars === 0 ? '$0.00'
    : dollars < 0.01 ? 'under $0.01'
    : '$' + dollars.toFixed(2);
  return money + ' \u00b7 ' + c.total.tokens.toLocaleString() + ' tokens \u00b7 '
    + c.total.calls + (c.total.calls === 1 ? ' call' : ' calls');
}

async function renderCost() {
  const box = $('set-cost-control');
  if (!box) return;
  box.textContent = 'reading…';
  let c;
  try {
    const r = await fetch('/api/cost');
    if (!r.ok) throw new Error('the server did not return a cost');
    c = await r.json();
  } catch (e) {
    // A cost view that fails should say so. Showing $0.00 on a failed fetch
    // would be a lie in the one direction that matters.
    box.textContent = '';
    const err = document.createElement('p');
    err.className = 'set-warn';
    err.textContent = 'Could not read the session cost \u2014 ' + (e.message || e);
    box.appendChild(err);
    return;
  }

  box.textContent = '';
  const total = document.createElement('p');
  total.className = 'set-cost-total';
  total.textContent = costLine(c);
  box.appendChild(total);

  const split = document.createElement('p');
  split.className = 'set-help';
  const part = (name, b) => c.labels[c.models[name]] + ' \u2014 $'
    + b.dollars.toFixed(2) + ' over ' + b.calls
    + (b.calls === 1 ? ' call' : ' calls');
  split.textContent = 'Companion: ' + part('companion', c.companion)
    + '. Background: ' + part('processing', c.processing) + '.';
  box.appendChild(split);

  const note = document.createElement('p');
  note.className = 'set-help';
  note.textContent = 'Since this journal last started, cleared when you close '
    + 'a session. Estimated from list prices for the models above, not from '
    + 'your account \u2014 treat it as a comparison between choices, not a bill.';
  box.appendChild(note);
}

async function save() {
  if (!loaded) return;
  const btn = $('settings-save');
  const note = $('settings-note');

  // only what actually changed, so a save never rewrites a line the user
  // didn't touch — and an untouched key field sends nothing at all. A control
  // that is disabled or locked is skipped outright: its value is not an
  // answer the author gave, so sending it would be this page inventing one.
  const values = {};
  const pairs = [
    ['MC_DATE_FORMAT', 'set-date'],
    ['MC_TIMEZONE', 'set-tz'],
    ['MC_LANGUAGE', 'set-lang'],
    ['MC_COMPANION_MODEL', 'set-companion-model'],
    ['MC_COMPANION_EFFORT', 'set-companion-effort'],
    ['MC_PROCESSING_MODEL', 'set-processing-model'],
    ['MC_MAX_SESSION_SPEND', 'set-max-session'],
    ['MC_MAX_MONTHLY_SPEND', 'set-max-spend'],
  ];
  for (const [key, id] of pairs) {
    const el = $(id);
    // A control the render declined to build (see the models guard above) has
    // no value to save -- and reading .disabled off null would take save() down
    // with it, so a stale payload would cost you the date format too.
    if (!el || el.disabled || el.dataset.locked) continue;
    if (el.value !== (loaded.values[key] || '')) values[key] = el.value;
  }
  const key = $('set-key').value.trim();
  if (key) values.ANTHROPIC_API_KEY = key;

  if (!Object.keys(values).length) {
    note.className = 'set-note';
    note.textContent = 'nothing changed.';
    return;
  }

  btn.disabled = true;
  note.className = 'set-note';
  note.textContent = 'saving…';
  let r;
  try {
    const res = await fetch('/api/settings', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({values}),
    });
    r = await res.json();
  } catch (e) {
    r = {error: 'could not reach the server'};
  }
  btn.disabled = false;

  if (r.error) {
    note.className = 'set-note error';
    note.textContent = r.error;
    return;
  }
  $('set-key').value = '';
  // reload first: loadSettings() ends by clearing the note, so setting it
  // beforehand would wipe the only confirmation the author ever sees. It
  // also leaves the pending markers on screen if the restart is refused.
  await loadSettings();
  await restartServer(note);
}

// The popover lives in the top layer, so it can't be positioned by a
// containing box -- it is placed against the `?` button each time it opens.
// Clamped to the viewport rather than trusting the button to be far enough
// from the right edge: Settings is a centred column and the window is not.
const HELP_GAP = 8, HELP_EDGE = 16;

function placeHelp() {
  const help = $('settings-restart-help');
  const at = $('settings-restart-info').getBoundingClientRect();
  const w = help.offsetWidth, h = help.offsetHeight;

  const left = Math.min(at.left, window.innerWidth - w - HELP_EDGE);
  help.style.left = Math.max(HELP_EDGE, left) + 'px';

  // Above when there isn't room below. These buttons are the last thing in a
  // scrolling pane, so "below" is off the bottom of the window more often
  // than not -- which is how this first shipped, and the text was unreadable.
  const below = at.bottom + HELP_GAP;
  const fits = below + h + HELP_EDGE <= window.innerHeight;
  const top = fits ? below : at.top - HELP_GAP - h;
  help.style.top = Math.max(HELP_EDGE, top) + 'px';
}

// Which journal is loaded is a fact about the running process, and
// refreshStatus() has already put it on the body. Asking again through a
// second payload would give this pane a way to disagree with the banner.
const inSeed = () => document.body.classList.contains('seed-instance');

// The one button whose *label* is the state. Called on load and after every
// settings render, because the status that decides it arrives asynchronously.
function syncSeedButton() {
  const btn = $('settings-seed');
  if (btn) btn.textContent = inSeed() ? 'return to my journal' : 'load seed corpus';
}

// The demo corpus is a separate journal with its own data dirs, not a mode of
// this one -- so getting there is a restart, same as any other setting read at
// import. Nothing is written to .env, which is what makes the next restart the
// way back: the destination lives in the child process's environment and dies
// with it.
async function loadSeed() {
  const btn = $('settings-seed');
  const note = $('settings-note');
  if (inSeed()) {
    btn.disabled = true;
    await restartServer(note, false, 'journal');
    btn.disabled = false;
    return;
  }
  if (!confirm('Restart on the seed corpus?\n\nIt is a different journal '
      + 'with its own entries, entities and summaries \u2014 nothing you do '
      + 'there touches yours. Any restart brings you back.')) return;
  btn.disabled = true;
  await restartServer(note, false, 'seed');
  btn.disabled = false;
}

export function init() {
  $('settings-save').onclick = save;
  $('settings-restart').onclick = restartNow;
  $('settings-seed').onclick = loadSeed;
  syncSeedButton();
  // `toggle` fires after the popover is in the layer, so it has a width to
  // measure by then; `beforetoggle` would measure zero.
  const help = $('settings-restart-help');
  help.addEventListener('toggle', e => {
    if (e.newState === 'open') {
      placeHelp();
      // The panel is in the top layer, so it does not travel with the pane it
      // was opened from: without this it hangs in place while the settings
      // list scrolls away beneath it. Capture, because the scroll happens on
      // #settings rather than on the window.
      addEventListener('scroll', placeHelp, true);
      addEventListener('resize', placeHelp);
    } else {
      removeEventListener('scroll', placeHelp, true);
      removeEventListener('resize', placeHelp);
    }
  });
}
