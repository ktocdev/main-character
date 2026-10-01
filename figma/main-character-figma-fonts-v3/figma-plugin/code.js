// Creates the 11 Main Character text styles and binds size/family to the Scale variables.
(async () => {
  const log = [];
  const fonts = await figma.listAvailableFontsAsync();
  const find = (test) => fonts.filter(f => test(f.fontName.family));
  const serifFonts = find(f => /old standard/i.test(f));
  const sansFonts = find(f => /cmu/i.test(f) && /demi/i.test(f) && /cond/i.test(f));
  if (!serifFonts.length) { figma.closePlugin('Old Standard TT not found.'); return; }
  if (!sansFonts.length) { figma.closePlugin('CMU Sans Demi Condensed not found — install fonts/cmu-sans-demicondensed.ttf and restart Figma.'); return; }

  const serifFamily = serifFonts[0].fontName.family;
  const sansFamily = sansFonts[0].fontName.family;
  const pick = (list, want) => {
    const s = list.map(f => f.fontName);
    return s.find(f => want.test(f.style)) || s.find(f => /regular|book|roman|normal/i.test(f.style)) || s[0];
  };
  const F = {
    serif: pick(serifFonts, /^regular$/i),
    serifItalic: pick(serifFonts, /^italic$/i),
    serifBold: pick(serifFonts, /^bold$/i),
    sans: pick(sansFonts, /regular|demi|condensed/i),
    sansStrong: pick(sansFonts, /semi ?bold|demi ?bold|bold/i),
  };
  for (const k in F) await figma.loadFontAsync(F[k]);

  // Variables
  const vars = await figma.variables.getLocalVariablesAsync();
  const v = (name) => vars.find(x => x.name === 'font-size/' + name) || vars.find(x => x.name === name || x.name.endsWith('/' + name));
  // Force correct px values (rem × 16) onto the font-size variables.
  const SIZES = { 'font-3xs': 11.2, 'font-2xs': 12, 'font-xs': 12.8, 'font-sm': 13.6, 'font-base': 15.2, 'font-md': 16.8, 'font-lg': 18.4, 'font-xl': 22.4 };
  for (const [n, px] of Object.entries(SIZES)) {
    const fv = v(n);
    if (fv && fv.resolvedType === 'FLOAT') for (const m of Object.keys(fv.valuesByMode)) fv.setValueForMode(m, px);
    else log.push('missing variable ' + n);
  }
  // Make family variables match the real installed family names so bindings resolve.
  for (const [name, fam] of [['body', serifFamily], ['ui', sansFamily]]) {
    const fv = vars.find(x => x.resolvedType === 'STRING' && x.name.endsWith('/' + name));
    if (fv) { for (const m of Object.keys(fv.valuesByMode)) fv.setValueForMode(m, fam); }
  }

  const P = (n) => ({ unit: 'PERCENT', value: n });
  const styles = [
    ['Prose/Body',         F.serif,       17,   null,        { unit: 'PIXELS', value: 28.05 }, 0, 'body', 'prose'],
    ['Prose/Bold title',   F.serifBold,   15.2, 'font-base', P(165), 0, 'body'],
    ['Wordmark',           F.serifItalic, 18.4, 'font-lg',   P(130), 0, 'body'],
    ['Heading/Section',    F.serifItalic, 16.8, 'font-md',   P(130), 0, 'body'],
    ['Heading/Focal',      F.serif,       22.4, 'font-xl',   P(130), 0, 'body'],
    ['UI/Base',            F.sans,        15.2, 'font-base', P(130), 0, 'ui'],
    ['UI/Base strong',     F.sansStrong,  15.2, 'font-base', P(130), 0, 'ui'],
    ['UI/Small',           F.sans,        13.6, 'font-sm',   P(145), 0, 'ui'],
    ['UI/XS',              F.sans,        12.8, 'font-xs',   P(130), 0, 'ui'],
    ['UI/2XS',             F.sans,        12,   'font-2xs',  P(130), 0, 'ui'],
    ['UI/Eyebrow',         F.sansStrong,  11.2, 'font-3xs',  P(130), 10, 'ui', null, 'UPPER'],
  ];

  const existing = await figma.getLocalTextStylesAsync();
  for (const [name, font, size, sizeVar, lh, ls, famVar, lhVar, textCase] of styles) {
    const s = existing.find(x => x.name === name) || figma.createTextStyle();
    s.name = name;
    s.fontName = font;
    s.fontSize = size;
    s.lineHeight = lh;
    s.letterSpacing = { unit: 'PERCENT', value: ls };
    if (textCase) s.textCase = textCase;
    try {
      const sv = sizeVar && v(sizeVar); if (sv) { s.setBoundVariable('fontSize', null); s.setBoundVariable('fontSize', sv); }
      const fv = v(famVar);             if (fv) s.setBoundVariable('fontFamily', fv);
      const lv = lhVar && v(lhVar);     if (lv) s.setBoundVariable('lineHeight', lv);
    } catch (e) { log.push(name + ': ' + e.message); }
  }

  figma.closePlugin(`Wordmark → ${v('font-lg') ? v('font-lg').name : 'unbound'}. Created 11 text styles (${serifFamily} / ${sansFamily}).` + (log.length ? ' Binding issues: ' + log.join('; ') : ''));
})().catch(e => figma.closePlugin('Error: ' + e.message));
