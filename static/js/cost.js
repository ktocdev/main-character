import { $ } from './core.js';
import { costLine } from './settings.js';

// ---- the nav cost icon ----
// Cost is available, not ambient. A running dollar figure sitting in the nav
// turns every session into something being watched, which is the wrong
// relationship to have with a journal -- so this is an icon that answers when
// asked and collapses again.
//
// It reads the same accumulator as Settings -> Models & Cost. Two entry
// points, one number: a nav figure that could disagree with the settings
// figure would make both untrustworthy.

let open = false;

async function show() {
  const panel = $('cost-panel');
  panel.textContent = 'reading…';
  try {
    const r = await fetch('/api/cost');
    if (!r.ok) throw new Error('no cost');
    const c = await r.json();
    panel.textContent = '';
    const line = document.createElement('div');
    line.className = 'cost-line';
    line.textContent = costLine(c);
    const note = document.createElement('div');
    note.className = 'cost-note';
    note.textContent = 'this session, estimated';
    panel.append(line, note);
  } catch {
    // Silent-zero would be a lie in the one direction that matters.
    panel.textContent = 'cost unavailable';
  }
}

function toggle(next) {
  open = next;
  $('cost-panel').hidden = !open;
  $('cost-toggle').setAttribute('aria-expanded', String(open));
  if (open) show();
}

export function init() {
  $('cost-toggle').onclick = e => { e.stopPropagation(); toggle(!open); };
  // Anywhere else dismisses it. A panel that only closes by pressing the same
  // small icon again is one people leave open by accident, which quietly
  // turns it back into the ambient figure this is meant not to be.
  document.addEventListener('click', () => { if (open) toggle(false); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && open) toggle(false);
  });
}
