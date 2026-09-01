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

export async function loadSettings() {
  const body = $('settings-body');
  body.textContent = 'loading…';
  let s;
  try {
    s = await (await fetch('/api/settings')).json();
  } catch (e) {
    body.textContent = 'could not read settings.';
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

  $('settings-save').disabled = false;
  $('settings-note').textContent = '';
}

// A save is only half a change: the process read .env at import. Rather than
// send the author to a terminal, ask the server to come back on its own and
// reload the page once it answers again. The fetches below are *expected* to
// fail for a few seconds -- the socket closes while the process is restarting.
async function restartServer(note) {
  note.className = 'set-note';
  note.textContent = 'restarting to apply…';
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
    try {
      if ((await fetch('/api/status', {cache: 'no-store'})).ok) {
        location.reload();
        return;
      }
    } catch (e) { }   // still down, keep waiting
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
  // didn't touch — and an untouched key field sends nothing at all
  const values = {};
  const pairs = [
    ['MC_DATE_FORMAT', $('set-date').value],
    ['MC_TIMEZONE', $('set-tz').value],
    ['MC_LANGUAGE', $('set-lang').value],
  ];
  for (const [key, val] of pairs) {
    if (val !== (loaded.values[key] || '')) values[key] = val;
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
