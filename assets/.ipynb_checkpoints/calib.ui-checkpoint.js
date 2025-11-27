// Collapsible toolbar builder; wires to overlay public API
window.CalibUI = (function(){
  function build(rootEl, overlay){
    let box = rootEl.querySelector('.cal-tools');
    if(!box){ box = document.createElement('details'); box.className='cal-tools'; rootEl.insertBefore(box, rootEl.firstChild); }
    box.innerHTML = `
      <summary>Tools ▾ (tap to expand)</summary>
      <div class="body"></div>
      <div class="cal-kpi" id="cal-kpi"></div>
    `;
    if(window.innerWidth > 900) box.setAttribute('open','');

    const body = box.querySelector('.body');
    const add  = el => body.appendChild(el);

    const Units = window.CalibUnits;

    // Mode (now includes 'select' and is default)
    const modes = ['select','segment','polyline','rectangle','angle','note'];
    const modeSel = sel(modes, overlay.opts.mode, v=>overlay.setMode(v));
    add(label('Mode', modeSel));

    // Units
    const unitSel = sel(Object.keys(Units.defs), overlay.opts.units, v=>{ overlay.opts.units=v; overlay.redraw(); });
    add(label('Units', unitSel, k=>Units.defs[k]?.label||k));

    // Snap
    const snapChk = checkbox(overlay.opts.snap, v=>{ overlay.opts.snap=v; overlay.updateKPI(); });
    add(label('Snap', snapChk));
    const snapRange = range(5, 50, overlay.opts.snapPx, 1, v=>{ overlay.opts.snapPx=v; }, true);
    add(label('Snap px', snapRange.wrap));

    // Scale lock
    const lockSel = document.createElement('select');
    const optAuto = document.createElement('option'); optAuto.value=''; optAuto.textContent='Scale: Auto (nearest)'; lockSel.appendChild(optAuto);
    (overlay.data?.markers||[]).forEach(m=>{ const o=document.createElement('option'); o.value=String(m.id); o.textContent=`Scale: Marker #${m.id}`; lockSel.appendChild(o); });
    lockSel.onchange=()=>{ overlay.opts.lockMarkerId = lockSel.value || null; overlay.redraw(); };
    add(lockSel);

    // Label size / line thickness
    const size = range(0.6, 3, overlay.opts.labelScale, 0.1, v=>{ overlay.opts.labelScale=v; overlay.redraw(); });
    add(label('Label', size.wrap));
    const thick = range(1, 10, overlay.opts.linePx, 1, v=>{ overlay.opts.linePx=v|0; overlay.redraw(); });
    add(label('Line', thick.wrap));

    // Visibility
    const annChk = checkbox(overlay.opts.showAnn, v=>{ overlay.opts.showAnn=v; overlay.redraw(); });
    add(label('Annotations', annChk));
    const visNames = {segment:'Seg', polyline:'Poly', rectangle:'Rect', angle:'Ang', note:'Note'};
    const visWrap = document.createElement('span'); visWrap.className='cal-inline';
    for(const t of Object.keys(visNames)){
      const c = checkbox(overlay.opts.visible[t]!==false, v=>{ overlay.opts.visible[t]=v; overlay.redraw(); });
      const l = document.createElement('label'); l.className='cal-inline';
      l.appendChild(c); l.appendChild(document.createTextNode(visNames[t]));
      visWrap.appendChild(l);
    }
    add(visWrap);

    // Markers / Auto-add
    add(label('Markers', checkbox(overlay.opts.showMarkers, v=>{ overlay.opts.showMarkers=v; overlay.redraw(); })));
    add(label('Auto-add', checkbox(overlay.opts.autoAdd, v=>{ overlay.opts.autoAdd=v; })));

    // Zoom/Fit/Reset
    add(button('➖', ()=>overlay.zoomStep(1/1.2)));
    add(button('➕', ()=>overlay.zoomStep(1.2)));
    add(button('Fit', ()=>overlay.fitToContainer()));
    add(button('Reset', ()=>overlay.resetView()));
    const zoomRange = range(0.25, 6, overlay.vp.k, 0.05, v=>{ overlay.setZoom(v); overlay.updateKPI(); });
    add(label('Zoom', zoomRange.wrap));

    // Notes / Polyline
    const noteInput = document.createElement('input'); noteInput.type='text'; noteInput.placeholder='Note text…';
    noteInput.value = overlay.noteText; noteInput.oninput=()=>{ overlay.noteText=noteInput.value; };
    add(noteInput);
    add(button('Finish', ()=>overlay.finishPolyline()));

    // Edit ops
    add(button('Undo', ()=>overlay.undo()));
    add(button('Clear', ()=>overlay.clearAll()));
    add(button('Grid', ()=>{ overlay.opts.showGrid = !overlay.opts.showGrid; overlay.redraw(); }));

    // Export
    const expVis = label('Export visible only', checkbox(overlay.opts.exportVisibleOnly, v=>{ overlay.opts.exportVisibleOnly=v; }));
    add(expVis);
    add(button('Save PNG', ()=>overlay.savePNG()));
    add(button('Save JSON', ()=>overlay.saveJSON()));

    overlay.updateKPI();
  }

  // small helpers
  function label(txt, el, mapText){
    const w = document.createElement('label'); w.className='cal-inline';
    w.appendChild(document.createTextNode(txt+' '));
    if(el.tagName==='SELECT' && mapText){
      [...el.options].forEach(o=>{ o.textContent = mapText(o.value); });
    }
    w.appendChild(el);
    return w;
  }
  function sel(values, cur, onch){
    const s=document.createElement('select');
    values.forEach(v=>{ const o=document.createElement('option'); o.value=String(v); o.textContent=String(v); s.appendChild(o); });
    s.value=String(cur); s.onchange=()=>onch(s.value);
    return s;
  }
  function checkbox(cur, onch){ const c=document.createElement('input'); c.type='checkbox'; c.checked=!!cur; c.onchange=()=>onch(c.checked); return c; }
  function button(txt, fn){ const b=document.createElement('button'); b.textContent=txt; b.onclick=fn; return b; }
  function range(min,max,cur,step,oninput, withReadout=false){
    const w=document.createElement('span'); w.className='cal-inline';
    const r=document.createElement('input'); r.type='range'; r.min=String(min); r.max=String(max); r.step=String(step); r.value=String(cur);
    let s=null; if(withReadout){ s=document.createElement('span'); s.className='cal-mini'; s.textContent=String(cur); }
    r.oninput=()=>{ const v=(r.step.indexOf('.')>=0)? parseFloat(r.value) : parseInt(r.value,10); oninput(v); if(s){ s.textContent=String(v); } };
    w.appendChild(r); if(s) w.appendChild(s);
    return { wrap:w, range:r, readout:s };
  }

  return { build };
})();
