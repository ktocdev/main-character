// SPDX-License-Identifier: AGPL-3.0-or-later
// The web demo's backend, running in the page.
//
// Only the published web demo loads this (scripts/build_web_demo.py adds it
// to index.html); the local app never does. It replaces window.fetch for
// every /api/ URL and answers from data/before.json and data/after.json --
// what the real server said, captured from a throwaway install of the demo
// corpus -- plus what the visitor has done since the page loaded. The views
// cannot tell the difference, which is the point: none of them changed.
//
// Everything lives in memory. A reload starts the demo over, and nothing is
// written to storage.
(function () {
  'use strict';

  const realFetch = window.fetch.bind(window);
  const READ_ONLY = 'Not available in the web demo — this is a read-only preview.';
  const REPLY_DELAY_MS = 1200;  // mock_client.DELAYS['companion._stream_turn']
  const CHUNK_MS = 35;          // mock_client.STREAM_CHUNK_DELAY
  const STEP_MS = 1600;         // one close-pipeline stage

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const clone = v => JSON.parse(JSON.stringify(v));
  const pad = n => String(n).padStart(2, '0');

  // The browser's clock stands in for the server's, so status reports no skew.
  function nowStamp() {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} `
      + `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  // ---- state ----
  let before = null, after = null, rec = null, searchAfter = null;
  let phase = 'before';        // which capture the reads come from
  let current = null;          // the open session: {started, parts, messages}
  let visitorSaved = 0;        // entries the visitor saved, for the status count
  const savedIds = new Set();  // save ids already stored: a retry is a duplicate
  let closes = 0;
  let firstArchive = null;     // the messages the recorded close archived
  const extraArchives = [];    // closes after the first, newest first
  let closing = null;          // {running, done: Set, finished}
  let seed = null;             // {current, updated, candidate, candidateUpdated}

  async function load(path) {
    const res = await realFetch(path);
    if (!res.ok) throw new Error(`web demo: could not load ${path} (${res.status})`);
    return res.json();
  }

  const ready = Promise.all([load('data/before.json'), load('data/after.json')])
    .then(([b, a]) => {
      rec = b;
      before = b.reads;
      after = a.reads;
      searchAfter = a.search;
      const cur = before['/api/sessions/current'].json;
      current = {started: cur.started, parts: cur.parts, messages: cur.messages};
      for (const m of current.messages) if (m.entry_id) savedIds.add(m.entry_id);
      const s = before['/api/seed'].json;
      seed = {
        current: s.exists ? read('/api/seed/download?which=current').text : null,
        updated: s.updated,
        candidate: null,
        candidateUpdated: null,
      };
    });

  function read(key) {
    if (phase === 'after' && key in after) return after[key];
    return before[key];
  }

  // The server writes the archive during the close itself, so History moves
  // to the after-close sessions as soon as the first close returns -- not
  // when the pipeline behind it finishes.
  function readSessions(key) {
    if (closes > 0 && key in after) return after[key];
    return before[key];
  }

  // ---- responses ----
  function json(body, status = 200, headers = {}) {
    return new Response(JSON.stringify(body), {
      status, headers: {'Content-Type': 'application/json', ...headers},
    });
  }

  // Streams the way mock_client does: a wait for the "model", then a few
  // words at a time. The response resolves at once, as a real one does when
  // its headers arrive, so the page's thinking state covers the wait.
  function stream(text, headers = {}, onDone) {
    const enc = new TextEncoder();
    const words = text.split(' ');
    const body = new ReadableStream({
      async start(controller) {
        await sleep(REPLY_DELAY_MS);
        let i = 0;
        while (i < words.length) {
          const n = 1 + Math.floor(Math.random() * 4);
          let chunk = words.slice(i, i + n).join(' ');
          if (i + n < words.length) chunk += ' ';
          i += n;
          await sleep(CHUNK_MS);
          controller.enqueue(enc.encode(chunk));
        }
        if (onDone) onDone();
        controller.close();
      },
    });
    return new Response(body, {
      status: 200, headers: {'Content-Type': 'text/plain; charset=utf-8', ...headers},
    });
  }

  // mock_client._pick: the prompt's SHA-256, first four bytes big-endian,
  // modulo the bucket. Same message, same reply.
  async function pickReply(prompt) {
    const replies = rec.replies;
    let n = 0;
    if (crypto.subtle) {
      const d = new Uint8Array(await crypto.subtle.digest(
        'SHA-256', new TextEncoder().encode(prompt)));
      n = ((d[0] << 24) >>> 0) + (d[1] << 16) + (d[2] << 8) + d[3];
    } else {
      // crypto.subtle needs a secure context; plain http off localhost has none
      for (const c of prompt) n = (n * 31 + c.charCodeAt(0)) >>> 0;
    }
    return replies[n % replies.length];
  }

  // ---- status ----
  function status() {
    const s = clone(read('/api/status').json);
    const open = current.messages.filter(m => m.role === 'you' && m.kind === 'entry' && !m.dream).length;
    const entries = before['/api/status'].json.entries + visitorSaved;
    return Object.assign(s, {
      entries, open_entries: open, indexed_entries: entries - open,
      conversation_turns: current.messages.filter(m => m.role === 'companion').length,
      // what skips the wizard (main.js) and shows the demo banner (core.js)
      configured: true, mock: true, seed_instance: true, web_demo: true,
      demo_built: true, embedder_cached: true,
      now: nowStamp(),
      tz: (Intl.DateTimeFormat().resolvedOptions().timeZone) || s.tz,
    });
  }

  // ---- sessions ----
  function sessionList() {
    const s = clone(readSessions('/api/sessions').json);
    s.current = {
      started: current.started,
      title: current.parts.length ? current.parts[0].title : null,
      message_count: current.messages.length,
    };
    const extra = extraArchives.map(a => ({
      kind: 'archive', id: a.id, title: a.title,
      start: a.parts[0].date, end: a.parts[a.parts.length - 1].date,
    }));
    s.sessions = [...extra, ...s.sessions].sort((x, y) => y.end.localeCompare(x.end));
    return s;
  }

  function archive(id) {
    const extra = extraArchives.find(a => a.id === id);
    if (extra) return json(extra);
    const r = readSessions(`/api/sessions/archive?id=${id}`);
    if (!r) return json({error: 'not found'}, 404);
    const body = clone(r.json);
    // The recorded close archived the demo's own three days. The visitor's
    // entries were in that chat too, so the archive carries them.
    if (firstArchive && rec.close.response.key === id) body.messages = firstArchive;
    return json(body, r.status);
  }

  // ---- writing ----
  function validStamp(ts) { return typeof ts === 'string' && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(ts); }
  function newId() {
    return Array.from(crypto.getRandomValues(new Uint8Array(16)),
      b => b.toString(16).padStart(2, '0')).join('');
  }

  // server.write_entry
  async function writeEntry(body) {
    const text = String(body.text || '').trim();
    if (!text) return json({error: 'empty entry'}, 400);
    const id = body.save_id || newId();
    const ts = validStamp(body.ts) ? body.ts : nowStamp();
    const saved = {'X-Entry-Id': id, 'X-Entry-Saved': '1'};
    if (savedIds.has(id)) {
      return body.no_reply
        ? json({ok: true, entry_id: id, no_reply: true, duplicate: true})
        : json({ok: true, entry_id: id, duplicate: true}, 200, saved);
    }
    savedIds.add(id);
    visitorSaved++;
    current.messages.push({role: 'you', kind: 'entry', entry_id: id, text, ts});
    if (body.no_reply) return json({ok: true, entry_id: id, no_reply: true, duplicate: false});
    const reply = await pickReply(text);
    return stream(reply, saved, () => current.messages.push({role: 'companion', text: reply, ts}));
  }

  async function chat(body) {
    const text = String(body.message || '');
    current.messages.push({role: 'you', kind: 'chat', text, ts: nowStamp()});
    const reply = await pickReply(text);
    return stream(reply, {}, () => current.messages.push({role: 'companion', text: reply, ts: nowStamp()}));
  }

  async function reflect() {
    const reply = await pickReply('reflect\n' + current.messages.length);
    return stream(reply, {}, () => current.messages.push({role: 'companion', text: reply, ts: nowStamp()}));
  }

  // ---- close ----
  // The first close replays the recorded one: its response, then the saved
  // after-close state once the progress steps have run. Later closes archive
  // what was written since and run the same steps, but nothing derived moves.
  function close() {
    if (closing && !closing.finished) {
      return json({error: 'the memory pipeline from a previous close is still '
        + 'running -- wait for it to finish before closing again.'}, 409);
    }
    const mine = current.messages.filter(m => m.role === 'you' && !m.dream);
    if (!mine.length) return json({error: 'nothing new in this chat yet — write or chat first'}, 400);

    const first = closes === 0;
    let result;
    if (first) {
      result = clone(rec.close.response);
      firstArchive = current.messages;
    } else {
      const stamps = mine.map(m => m.ts).sort();
      const date = stamps[stamps.length - 1].slice(0, 10);
      const title = `Journal chat ${date}`;
      const days = [...new Set(stamps.map(t => t.slice(0, 10)))];
      const id = `web-demo-${closes}`;
      extraArchives.unshift({
        id, entry_schema: 2, title, started: current.started,
        closed: stamps[stamps.length - 1],
        parts: days.map(d => ({date: d, title, entry_ids: [], legacy: false})),
        messages: current.messages,
      });
      result = {ok: true, key: id, title, entry_title: title, date};
    }
    closes++;
    current = {started: nowStamp(), parts: [], messages: []};
    runPipeline(first);
    return json(result);
  }

  async function runPipeline(first) {
    closing = {running: null, done: new Set(), finished: false};
    for (const step of rec.close.steps) {
      closing.running = step.key;
      await sleep(STEP_MS);
      closing.done.add(step.key);
      if (step.key === 'seed' && first) {
        seed.candidate = after['/api/seed/download?which=candidate'].text;
        seed.candidateUpdated = after['/api/seed'].json.candidate_updated;
      }
    }
    closing.running = null;
    closing.finished = true;
    if (first) phase = 'after';
  }

  function progress() {
    const c = closing || {running: null, done: new Set(), finished: true};
    const steps = rec.close.steps.map(s => ({
      key: s.key, label: s.label,
      status: c.done.has(s.key) ? 'done' : c.running === s.key ? 'running' : 'pending',
    }));
    return json({active: !c.finished, steps, done: c.finished});
  }

  // ---- seed ----
  function seedStatus() {
    return json({
      exists: seed.current !== null, updated: seed.updated,
      candidate_exists: seed.candidate !== null, candidate_updated: seed.candidateUpdated,
    });
  }

  function seedDownload(which) {
    const text = which === 'candidate' ? seed.candidate : seed.current;
    if (text === null) return json({error: `no ${which} seed yet`}, 404);
    const name = which === 'candidate' ? 'seed_summary.candidate.md' : 'seed_summary.md';
    return new Response(text, {headers: {
      'Content-Type': 'text/markdown; charset=utf-8',
      'Content-Disposition': `attachment; filename="${name}"`,
    }});
  }

  // seed.save_seed
  function seedUpload(body) {
    const text = String(body.text || '').trim();
    if (text.length < 200) {
      return json({error: 'that file looks empty — not replacing the seed with it'}, 400);
    }
    seed.current = text + '\n';
    seed.updated = nowStamp();
    seed.candidate = null;
    seed.candidateUpdated = null;
    return json({ok: true, chars: text.length});
  }

  // ---- search: plain word matching in place of chroma ----
  // server._fold: lowercase and strip accents a character at a time, so
  // indexes still line up with the original text.
  const fold = s => Array.from(s, c => c.toLowerCase().normalize('NFKD')[0] || c).join('');

  // server._snippet_around
  function snippetAround(doc, q, pre = 150, post = 300) {
    const hay = fold(doc);
    const fq = fold(q);
    const terms = [fq, ...fq.split(/\s+/).filter(w => w.length > 2)];
    let idx = -1;
    for (const term of terms) {
      const found = hay.indexOf(term);
      if (found !== -1 && (idx === -1 || found < idx)) idx = found;
      if (term === fq && found !== -1) break;
    }
    if (idx === -1) return doc.slice(0, 450) + (doc.length > 450 ? '…' : '');
    const start = Math.max(0, idx - pre);
    const end = Math.min(doc.length, idx + post);
    return (start ? '…' : '') + doc.slice(start, end) + (end < doc.length ? '…' : '');
  }

  function count(hay, needle) {
    let n = 0;
    for (let i = hay.indexOf(needle); i !== -1; i = hay.indexOf(needle, i + needle.length)) n++;
    return n;
  }

  // Exact mode is the server's own: literal matches, newest first. Meaning
  // mode can't rank by meaning without the embedding model, so it ranks by
  // the query's words instead -- the whole phrase first, then entries holding
  // the most of its words.
  function search(q, mode) {
    q = q.trim();
    if (!q) return json({error: 'empty query'}, 400);
    const corpus = phase === 'after' ? searchAfter : rec.search;
    const needle = fold(q);
    let results;
    if (mode === 'exact') {
      results = corpus.map(e => ({e, hits: count(fold(e.text), needle)}))
        .filter(x => x.hits)
        .map(({e, hits}) => ({date: e.date, title: e.title, snippet: snippetAround(e.text, q), hits}))
        .sort((a, b) => b.date.localeCompare(a.date));
    } else {
      const words = [...new Set(needle.split(/\s+/).filter(w => w.length > 2))];
      const scored = corpus.map(e => {
        const hay = fold(e.text);
        return {
          e, literal: hay.includes(needle),
          words: words.filter(w => hay.includes(w)).length,
          hits: words.reduce((n, w) => n + count(hay, w), 0),
        };
      }).filter(x => x.literal || x.words);
      scored.sort((a, b) => (b.literal - a.literal) || (b.words - a.words) || (b.hits - a.hits));
      const exact = scored.filter(x => x.literal);
      const related = scored.filter(x => !x.literal).slice(0, 12);
      results = [...exact, ...related].map(x => ({
        date: x.e.date, title: x.e.title, snippet: snippetAround(x.e.text, q),
        match: x.literal ? 'exact' : 'related',
      }));
    }
    return json({query: q, mode, results});
  }
  // ---- routing ----
  function keyOf(u) {
    const params = [...u.searchParams].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return params.length
      ? u.pathname + '?' + params.map(([k, v]) => `${k}=${v}`).join('&')
      : u.pathname;
  }

  function fromCapture(key) {
    const r = key.startsWith('/api/sessions/') ? readSessions(key) : read(key);
    if (!r) return json({error: 'not found'}, 404);
    if ('json' in r) return json(r.json, r.status);
    const headers = {'Content-Type': r.type || 'text/plain'};
    if (r.filename) headers['Content-Disposition'] = `attachment; filename="${r.filename}"`;
    return new Response(r.text, {status: r.status, headers});
  }

  async function get(u) {
    const p = u.pathname, q = u.searchParams;
    switch (p) {
      case '/api/status': return json(status());
      case '/api/sessions/current':
        return json({started: current.started, parts: current.parts, messages: current.messages});
      case '/api/sessions': return json(sessionList());
      case '/api/sessions/archive': return archive(q.get('id') || '');
      case '/api/sessions/close/progress': return progress();
      case '/api/seed': return seedStatus();
      case '/api/seed/download': return seedDownload(q.get('which') === 'candidate' ? 'candidate' : 'current');
      case '/api/search': return search(q.get('q') || '', q.get('mode') || 'semantic');
      default: return fromCapture(keyOf(u));
    }
  }

  async function post(u, body) {
    switch (u.pathname) {
      case '/api/entry': return writeEntry(body);
      case '/api/chat': return chat(body);
      case '/api/reflect': return reflect();
      case '/api/lookup': return stream(await pickReply(String(body.message || '')));
      case '/api/lookup/reset':
      case '/api/reset': return json({ok: true});
      case '/api/sessions/seed': return json({ok: true, seeded: 0});
      case '/api/sessions/close': return close();
      case '/api/seed/upload': return seedUpload(body);
      default: return json({error: READ_ONLY}, 403);
    }
  }

  window.fetch = async function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || String(input);
    const u = new URL(url, location.href);
    if (u.origin !== location.origin || !u.pathname.startsWith('/api/')) {
      return realFetch(input, init);
    }
    await ready;
    const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    if (method === 'GET') return get(u);
    let body = {};
    try { body = init && init.body ? JSON.parse(init.body) : {}; } catch (e) { body = {}; }
    return post(u, body);
  };
})();
