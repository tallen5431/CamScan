// Units conversion helpers (global namespace)
window.CalibUnits = (function(){
  // Each unit knows how to convert a LENGTH from mm (fromMM) and back (toMM).
  // areaFromMM2 converts an AREA (mm²) to this unit squared — the factor is squared,
  // which is why area must never be scaled with the linear fromMM().
  const defs = {
    mm : { label:"mm",  areaLabel:"mm²", fromMM: v=>v,      toMM:v=>v,      areaFromMM2: v=>v },
    cm : { label:"cm",  areaLabel:"cm²", fromMM: v=>v/10,   toMM:v=>v*10,   areaFromMM2: v=>v/100 },
    in : { label:"in",  areaLabel:"in²", fromMM: v=>v/25.4, toMM:v=>v*25.4, areaFromMM2: v=>v/645.16 }
  };
  const get = k => defs[k] || defs.mm;
  return { get, defs };
})();