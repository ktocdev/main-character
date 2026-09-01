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
      <details id="set-models">
        <summary>Which models this journal uses, and what they cost</summary>
        <div class="set-disclosure-body">
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
          <div class="set-row" id="set-caps-control"></div>
          <div class="set-row">
            <label>Session cost</label>
            <div class="set-control"><p class="set-warn">Not built yet. This is
              where the running total for the open session will show, split
              between the companion and background work.</p></div>
          </div>
        </div>
      </details>
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
  const caps = $('set-caps-control');
  if (!s.spend_caps_enforced) {
    const note = document.createElement('p');
    note.className = 'set-help';
    note.textContent = 'Nothing limits what this journal can spend. Until '
      + 'spend caps are built, the backstop that does not depend on this app '
      + 'being correct is a spend limit on your Anthropic Console account.';
    caps.appendChild(note);

    const set = [['MC_MAX_SESSION_TOKENS', 'tokens per session'],
                 ['MC_MAX_MONTHLY_SPEND', 'dollars per month']]
      .filter(([key]) => v[key]);
    if (set.length) {
      const warn = document.createElement('p');
      warn.className = 'set-warn';
      warn.textContent = 'Your .env already sets '
        + set.map(([key, label]) => v[key] + ' ' + label).join(' and ')
        + ' \u2014 stored, but not read by anything yet. It will start '
        + 'applying when caps are enforced; it is not protecting you now.';
      caps.appendChild(warn);
    }
  }

  $('settings-save').disabled = false;
  $('settings-note').textContent = '';
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

async function restartServer(note) {
  note.className = 'set-note';
  note.textContent = 'restarting to apply…';
  const before = await instanceId();
  let r;
  try {
    r = await (await fetch('/api/restart', {method: 'POST'})).json();
  } catch (e) {
    r = {error: 'could not reach the server.'};
  }
  if (r.error) {
    // the save itself still happened -- say so, or this reads as a lost edit
    note.className = 'set-note error';
    note.textContent = r.error
      + ' Your change is saved in .env; restart the journal to apply it.';
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
  note.textContent = 'saved, but the journal did not come back — '
    + 'start it again the way you normally do.';
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

export function init() {
  $('settings-save').onclick = save;
}
