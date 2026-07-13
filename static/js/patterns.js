import { $, api } from './core.js';

// ---- patterns ----
export async function loadPatterns() {
  const r = await (await fetch('/api/patterns')).json();
  renderPatterns(r);
}

function renderPatterns(r) {
  $('pat-status').textContent = r.generated
    ? `detected ${r.generated.replace('T', ' ')} · ${r.patterns.length} pattern${r.patterns.length === 1 ? '' : 's'}`
    : 'nothing detected yet — hit detect patterns';
  const el = $('pat-list');
  el.innerHTML = '';
  for (const p of r.patterns) {
    const d = document.createElement('div');
    d.className = 'pattern';
    const h = document.createElement('h3');
    h.textContent = p.name;
    const kind = document.createElement('span');
    kind.className = 'pat-kind';
    kind.textContent = `${p.kind} · ${p.confidence} confidence`;
    h.appendChild(kind);
    const x = document.createElement('button');
    x.className = 'quiet pat-dismiss';
    x.textContent = 'dismiss';
    x.title = 'not a real pattern — hide it, stays hidden across re-detections';
    x.onclick = async () => {
      if (await api('/api/patterns/dismiss', {name: p.name})) d.remove();
    };
    h.appendChild(x);
    d.appendChild(h);
    const desc = document.createElement('p');
    desc.textContent = p.description;
    d.appendChild(desc);
    const trig = document.createElement('p');
    trig.className = 'pat-trigger';
    trig.textContent = 'trigger: ' + p.trigger;
    d.appendChild(trig);
    const inst = document.createElement('div');
    inst.className = 'pat-instances';
    for (const i of p.instances) {
      const row = document.createElement('div');
      row.textContent = `${i.date} — ${i.note}`;
      inst.appendChild(row);
    }
    d.appendChild(inst);
    el.appendChild(d);
  }
}

export function init() {
  $('pat-build').onclick = async () => {
    const b = $('pat-build');
    b.disabled = true;
    b.textContent = 'detecting… (~1 min)';
    try {
      const r = await api('/api/patterns/build', {});
      if (r) renderPatterns(r);
    } finally {
      b.textContent = 'detect patterns';
      b.disabled = false;
    }
  };
}
