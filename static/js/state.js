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
};
export const filters = {unreviewed: false, single: false, group: null, types: new Set()};
