// SPDX-License-Identifier: AGPL-3.0-or-later
// card.html's own script, kept out of the page so it runs under a
// CSP with no inline scripts (the web demo publishes these pages).
import { itemCard, cardRow } from '../../js/card.js';

// The specimen content from the handoff (docs/design_handoff_card).
const cards = [
  ['summary', 'Week of September 22', 'Two late nights writing, a run with Mika at Greenwood Park, and the first week the recovery didn’t cost a sick day.', ['Sep 22–28', '7 entries']],
  ['category', 'Family', 'Calls with your mother, the move, and the question of whether to go home for the holidays.', ['31 entries', 'rewritten Sep 6']],
  ['dream', 'Surreal', 'Being late to something you can’t name; a hallway that keeps adding doors.', ['2 dreams', 'last Sep 19']],
  ['favorite', 'The lamp was the only light left on', 'Wrote until the lamp was the only light left on. That is the second night this week you stayed with it.', ['saved Sep 1', 'entry']],
  ['person', 'Mika', 'Running partner since spring. Shows up on the hard weeks, usually at Greenwood Park.', ['49 mentions', 'also: Mik', 'friends', 'running']],
  ['place', 'Greenwood Park', 'Where the Tuesday runs happen. Mentioned most on weeks you describe as tight.', ['12 mentions']],
  ['project', 'Groundwork', 'The side project you keep going to after meetings. Morale tracks how often it comes up.', ['18 mentions', 'since June']],
  ['summary', 'The long week before the move, when every evening ended at the kitchen table', 'Boxes, a lease that would not sign itself, three calls home and one you did not return. You wrote every night anyway, most of it about the table — who sat there, what got said, and what you decided to leave behind in the old apartment.', ['Aug 11–17', '9 entries', 'family', 'work', 'move']],
].map(([type, title, description, badges]) => ({type, title, description, badges, href: '#'}));

function spec(size, text) {
  const d = document.createElement('div');
  d.className = 'spec-line';
  d.innerHTML = '<b></b><span></span>';
  d.querySelector('b').textContent = size;
  d.querySelector('span').textContent = text;
  return d;
}

const sizes = document.getElementById('sizes-out');
for (const [size, n, text] of [
  ['sm', 4, 'min 9.5 × 12rem · 4 per 46rem row · no description · 2 badges'],
  ['md', 3, 'min 13 × 19rem · default · 3 per 46rem row · 3 badges'],
  ['lg', 2, 'min 18 × 24.5rem · 2 per 46rem row · 4 badges'],
]) sizes.append(spec(size, text), cardRow(cards.slice(0, n), size));

const horiz = document.getElementById('horizontal-out');
horiz.append(spec('md / lg', 'horizontal · 8.5rem · 7rem visual · one-line title · two-line description'));
for (const c of [cards[4], cards[1], cards[7]]) horiz.appendChild(itemCard({...c, orientation: 'horizontal'}));
horiz.append(spec('sm', 'horizontal · 5.75rem · 5rem visual · no description'));
for (const c of [cards[4], cards[5]]) horiz.appendChild(itemCard({...c, size: 'sm', orientation: 'horizontal'}));

const wrapping = document.getElementById('wrapping-out');
for (const [size, text] of [['sm', '6 per 72rem row'], ['md', '5 per 72rem row'], ['lg', '3 per 72rem row']])
  wrapping.append(spec(size, text), cardRow(cards, size));

const parts = document.getElementById('parts');
for (const [part, text] of [
  ['visual', 'Links to the item. One glyph per type (¶ § ☾ ★ ◎ ◇); people get their initials. Override it with icon. Hidden from screen readers and skipped by Tab, because the title is the real link.'],
  ['type', 'Tracked uppercase eyebrow in --text-accent-muted, the same label idiom every section in the app uses.'],
  ['title', 'Links to the item. Body serif 700, since bold is for entry titles and category names. Clamped to two lines; --text-accent on hover, an outline in the same colour on focus.'],
  ['description', 'Plain text, not a link, so it can be selected and copied. --text-muted serif, clamped to 3 lines on md and 4 on lg. Small has none.'],
  ['badges', 'Pinned to the bottom, one row, never wraps. A badge that doesn’t fit gets an ellipsis. Past the size’s limit the last slot becomes +N.'],
  ['row', '.card-row[.sm | .lg]: a grid whose track floor matches the card (9.5 / 13 / 18rem) with a 1rem gap, so every card in a row is the same width. One column below 670px.'],
]) {
  const a = document.createElement('div'), b = document.createElement('div');
  a.className = 'part'; a.textContent = part;
  b.className = 'rule-text'; b.textContent = text;
  parts.append(a, b);
}
