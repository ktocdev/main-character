// SPDX-License-Identifier: AGPL-3.0-or-later
// Cross-module mutable state. ES-module import bindings are read-only,
// so anything a module REASSIGNS from outside its own file lives on
// this object; `filters` is only ever property-mutated, so it exports
// directly and call sites stay unchanged.
export const state = {
  entities: {},          // entity index (entities.js owns; triage reads)
  selected: null,        // selected entity name (entities.js owns; groups reads)
  activeTab: 'write',    // main.js owns; write + triage read
  sessionSel: 'current', // history selection; write's closeSession resets it
  dateStyle: 'long',     // MC_DATE_FORMAT, via /api/status; write renders stamps with it
  clockSkewMs: 0,        // server clock − browser clock, via /api/status; write stamps against it
  tz: '',                // the server's zone name; shown on the entry stamp
  configured: true,      // /api/status: false on a fresh clone with no key.
                         // Optimistic default -- the wizard opening over a
                         // working journal because status hiccuped would be
                         // worse than it opening a beat late.
  demoBuilt: true,       // /api/status: false until the demo's index exists.
                         // Optimistic for the same shape of reason -- warning
                         // about a twenty-second build that then doesn't
                         // happen is worse than not warning.
  embedderCached: null,  // /api/status: whether the 90MB embedding model is
                         // already on this machine. null = couldn't tell, and
                         // the wait wording hedges rather than guessing.
};
export const filters = {unreviewed: false, single: false, group: null, types: new Set()};
