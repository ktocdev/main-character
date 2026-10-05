// SPDX-License-Identifier: AGPL-3.0-or-later
// tokens.html's own script, kept out of the page so it runs under a
// CSP with no inline scripts (the web demo publishes these pages).
// ---- ramps, read live from tokens.css ----
const rootCs = getComputedStyle(document.documentElement);
const stepHex = name => rootCs.getPropertyValue('--' + name).trim();
const RAMPS = { umber: [0, 50, 100, 200, 300, 400, 500, 600, 700, 800, 850, 900, 950], amber: [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950], rust: [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950] };
const rampEl = document.getElementById('ramp-list');
for (const [hue, steps] of Object.entries(RAMPS)) {
  const r = document.createElement('div');
  r.className = 'ramp';
  r.innerHTML = `<div class="name" data-style="font:600 .95rem var(--font-ui); color:var(--text-default)">${hue}</div><div class="steps"></div>`;
  for (const s of steps) {
    const st = document.createElement('div');
    st.className = 'step';
    st.innerHTML = `<div class="sq" data-style="background:var(--${hue}-${s})"></div><b>${s}</b><span>${stepHex(`${hue}-${s}`)}</span>`;
    r.querySelector('.steps').appendChild(st);
  }
  rampEl.appendChild(r);
}
// WCAG contrast between two hex colours
const lum = h => { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255].map(v => { v /= 255; return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }).reduce((a, v, i) => a + v * [.2126, .7152, .0722][i], 0); };
const ratio = (a, b) => { const x = lum(a), y = lum(b); return ((Math.max(x, y) + .05) / (Math.min(x, y) + .05)).toFixed(1) + ':1'; };
// [role, job, kind]: kind says what the contrast is against. The steps
// each role points at come from tokens.css itself: the :root rule for
// dark, the data-theme="light" rule for light.
const JOBS = [
  ['--bg-page', 'page background', 'surface'],
  ['--bg-raised', 'cards, panels, menus', 'surface'],
  ['--bg-muted', 'inputs, textareas, chips, pressed nav, the soft toggle on', 'surface'],
  ['--text-default', 'primary text', 'text'],
  ['--text-muted', 'meta, labels, help copy', 'text'],
  ['--text-you', 'your own words in a transcript', 'text'],
  ['--text-accent', 'italic headings, active labels', 'text'],
  ['--text-accent-muted', 'eyebrows, date labels', 'text'],
  ['--text-danger', 'error notes, delete on hover', 'text'],
  ['--text-on-accent', 'the label on an amber fill', 'on'],
  ['--fill-accent', 'the send button, the solid toggle on', 'fill'],
  ['--fill-accent-hover', 'the send button on hover', 'fill'],
  ['--fill-accent-muted', 'the filled button', 'fill'],
  ['--fill-danger', 'danger fills', 'fill'],
  ['--border-default', 'every hairline', 'line'],
  ['--border-strong', 'a hairline lifted on hover', 'line'],
  ['--border-accent', 'dashed borders', 'line'],
  ['--border-danger', 'a danger outline: the danger button on hover, an error note', 'line'],
  ['--focus-ring', 'the keyboard focus outline', 'line'],
];
const tokenRules = [...document.styleSheets].filter(sh => /tokens\.css$/.test(sh.href || '')).flatMap(sh => [...sh.cssRules]);
const ruleFor = sel => tokenRules.find(r => r.selectorText === sel && r.style.getPropertyValue('--bg-page'));
const darkRule = ruleFor(':root'), lightRule = ruleFor(':root[data-theme="light"]');
const stepOf = (rule, role) => (/var\(--([a-z]+-\d+)\)/.exec(rule.style.getPropertyValue(role)) || [])[1];
const ROLES = JOBS.map(([role, job, kind]) => [role, stepOf(darkRule, role), stepOf(lightRule, role), job, kind]);
// pin every role to its dark step inside .dk and its light step inside .lt
// (a constructed sheet, not a <style> element: the page runs under a CSP
// with no inline styles)
const pin = new CSSStyleSheet();
pin.replaceSync(['dk', 'lt'].map((t, i) => `.${t} { ${ROLES.map(r => `${r[0]}: var(--${r[1 + i]});`).join(' ')} }`).join('\n'));
document.adoptedStyleSheets = [...document.adoptedStyleSheets, pin];
// the platform colours, from design.css's :root and light rules
const designRules = [...document.styleSheets].filter(sh => /design\.css$/.test(sh.href || '')).flatMap(sh => [...sh.cssRules]);
const platDark = designRules.find(r => r.selectorText === ':root' && r.style.getPropertyValue('--platform-well'));
const platLight = designRules.find(r => r.selectorText === ':root[data-theme="light"]');
const platEl = document.getElementById('platform');
for (const [name, what] of [['--platform-highlight', 'the open list\'s hovered row'], ['--platform-highlight-text', 'its text'], ['--platform-rule', 'the open list\'s rule, the unchecked box\'s rule'], ['--platform-well', 'the unchecked box\'s fill, and the tick']]) {
  const d = platDark.style.getPropertyValue(name).trim(), l = platLight.style.getPropertyValue(name).trim();
  const el = document.createElement('span');
  el.innerHTML = `<span class="plat"><i data-style="background:${d}"></i><i data-style="background:${l}"></i></span><span class="mono"></span> · ${d} / ${l} · `;
  el.querySelector('.mono').textContent = name;
  el.append(what);
  platEl.appendChild(el);
}
const step = (roles, theme, role) => roles.find(r => r[0] === role)[theme === 'dk' ? 1 : 2];
function cell(theme, s, kind, roles) {
  const hex = stepHex(s), page = stepHex(step(roles, theme, '--bg-page')), on = stepHex(step(roles, theme, '--text-on-accent'));
  const fillHex = stepHex(step(roles, theme, '--fill-accent'));
  let chip = `<div class="chip-sq" data-style="background:${hex}"></div>`, note = '';
  if (kind === 'text') { chip += `<span class="aa" data-style="color:${hex}">Aa</span>`; note = `${ratio(hex, page)} on page`; }
  else if (kind === 'fill') { chip = `<div class="chip-sq" data-style="background:${hex}; color:${on}; display:grid; place-items:center; font:600 .95rem var(--font-ui)">Aa</div>`; note = `${ratio(on, hex)} under its label`; }
  else if (kind === 'on') { chip = `<div class="chip-sq" data-style="background:${fillHex}; color:${hex}; display:grid; place-items:center; font:600 .95rem var(--font-ui)">Aa</div>`; note = `${ratio(hex, fillHex)} on --fill-accent`; }
  else if (kind === 'line') note = `${ratio(hex, page)} on page`;
  return `<div class="swatch ${theme}">${chip}<span class="pair"><span>${s}</span><span class="hex">${hex}${note ? ' · ' + note : ''}</span></span></div>`;
}
function drawRoles(roles) {
  const el = document.getElementById('role-map');
  el.textContent = '';
  for (const [name, dk, lt, job, kind] of roles) {
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `<div class="id"><div class="name"></div><div class="role"></div></div>${cell('dk', dk, kind, roles)}${cell('lt', lt, kind, roles)}`;
    row.querySelector('.name').textContent = name;
    row.querySelector('.role').textContent = job;
    el.appendChild(row);
  }
}
drawRoles(ROLES);

const fmt = rem => `${rem}rem · ${Math.round(rem * 16 * 10) / 10}px`;
const type = [
  ['--font-3xs', .7, 'eyebrows, tracked uppercase labels (600); message labels use .75rem', 'YOU · SEPTEMBER 1, 2026'],
  ['--font-2xs', .75, 'small buttons, tight meta', 'medium confidence · 8 weeks'],
  ['--font-xs', .8, 'default UI text: buttons, status, meta. The workhorse.', 'restart the journal · 31 entries'],
  ['--font-sm', .85, 'chips, help copy, secondary UI', 'emotional 30 · family 3'],
  ['--font-base', .95, 'primary UI text, form labels, send button', 'save entry · send · reflect'],
  ['--font-md', 1.05, 'sub-headings', 'Settings'],
  ['--font-lg', 1.15, 'the masthead h1', 'main character'],
  ['--font-xl', 1.4, 'the one focal heading', 'Is this the same Mika?'],
];
const typeEl = document.getElementById('type-scale');
for (const [name, rem, covers, ui] of type) {
  const row = document.createElement('div');
  row.className = 'typerow';
  row.innerHTML = `<div class="id"><div data-style="display:flex; gap:.6rem; align-items:baseline; flex-wrap:wrap"><span class="name" data-style="font:600 .95rem var(--font-ui); color:var(--text-default)"></span><span class="val"></span></div><div class="role"></div></div>
    <div class="sample" data-style="font-family:var(--font-ui); font-size:${rem}rem"></div>
    <div class="sample" data-style="font-family:var(--font-body); font-size:${rem}rem">The lamp was still on.</div>`;
  row.querySelector('.name').textContent = name;
  row.querySelector('.val').textContent = fmt(rem);
  row.querySelector('.role').textContent = covers;
  row.querySelectorAll('.sample')[0].textContent = ui;
  typeEl.appendChild(row);
}
const space = [
  ['--space-1', .25, 'icon and inline nudges'], ['--space-2', .4, 'tight inline gaps, the most common value'],
  ['--space-3', .5, 'compact padding, chip padding'], ['--space-4', .6, 'composer gaps, button rows'],
  ['--space-5', .8, 'standard component padding'], ['--space-6', 1, 'section spacing, pane padding'],
  ['--space-7', 1.2, 'header gaps'], ['--space-8', 1.5, 'pane outer padding, major section margins'],
];
const spaceEl = document.getElementById('space-scale');
for (const [name, rem, covers] of space) {
  const row = document.createElement('div');
  row.className = 'sprow';
  row.innerHTML = `<div class="id"><span class="name" data-style="font:600 .95rem var(--font-ui); color:var(--text-default)"></span><span class="val"></span></div>
    <div data-style="display:flex; align-items:center; gap:.8rem; min-width:0"><div class="bar" data-style="width:${rem}rem"></div><span class="role"></span></div>`;
  row.querySelector('.name').textContent = name;
  row.querySelector('.val').textContent = fmt(rem);
  row.querySelector('.role').textContent = covers;
  spaceEl.appendChild(row);
}
const radii = [
  ['--radius-sm', '6px', 'flat-nav active, segmented control, list hover, text buttons', '6rem', 'quiet'],
  ['--radius-base', '8px', 'quiet buttons, tooltip, list items, the menu trigger', '6rem', 'quiet'],
  ['--radius-lg', '10px', 'send button, textareas, popovers, the cost panel', '6rem', 'send'],
  ['--radius-pill', '999px', 'chips and pill buttons', '6rem', 'uneasy'],
  ['--radius-circle', '50%', 'the cost meter ◌ and the ? button', '2.2rem', '?'],
];
const radEl = document.getElementById('radii');
for (const [name, val, covers, w, label] of radii) {
  const c = document.createElement('div');
  c.className = 'card2';
  c.innerHTML = `<div data-style="display:flex; gap:.6rem; align-items:baseline; flex-wrap:wrap"><span class="name" data-style="font:600 .95rem var(--font-ui); color:var(--text-default)"></span><span class="val"></span></div>
    <div class="rad"><div class="box" data-style="width:${w}; border-radius:${val}"></div><div class="fill" data-style="border-radius:${val}"></div></div><div class="role"></div>`;
  c.querySelector('.name').textContent = name;
  c.querySelector('.val').textContent = val;
  c.querySelector('.box').textContent = label;
  c.querySelector('.role').textContent = covers;
  radEl.appendChild(c);
}

// ---- styles, applied through the CSSOM ----
// Specimens on this page carry one-off styles, some of them computed above.
// A style attribute is an inline style, which the published demo's CSP
// refuses, so they are written as data-style and set here instead, where
// the CSP allows it.
for (const el of document.querySelectorAll('[data-style]')) {
  el.style.cssText = el.dataset.style;
  el.removeAttribute('data-style');
}
