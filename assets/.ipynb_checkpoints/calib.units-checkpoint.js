// Units conversion helpers (global namespace)
window.CalibUnits = (function(){
  const defs = {
    mm : { label:"mm",  fromMM: v=>v,            toMM:v=>v },
    cm : { label:"cm",  fromMM: v=>v/10,         toMM:v=>v*10 },
    in : { label:"in",  fromMM: v=>v/25.4,       toMM:v=>v*25.4 }
  };
  const get = k => defs[k] || defs.mm;
  return { get, defs };
})();
