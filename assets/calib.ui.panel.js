// Bottom sheet: advanced controls (style, visibility, scale/units, actions)
window.CalibUIPanel = (function(){
  const STORE_KEY = 'calib.ui.prefs.v2';
  const load = () => { try { return JSON.parse(localStorage.getItem(STORE_KEY)||'{}'); } catch { return {}; } };
  const save = (p) => { try { localStorage.setItem(STORE_KEY, JSON.stringify(p)); } catch {} };

  function create(rootEl, overlay){
    // shell
    const wrap = document.createElement('div'); wrap.className = 'cal-sheet';
    const details = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = 'More tools ▾';
    const panel = document.createElement('div'); panel.className = 'cal-panel';
    details.append(summary, panel);
    wrap.append(details);
    rootEl.append(wrap);

    // cards
    const style = card('Style');
    const vis   = card('Visibility');
    const scale = card('Scale / Units');
    const ops   = card('Actions');
    panel.append(style, vis, scale, ops);

    const prefs = load();

    // ——— STYLE ———
    const labelScale = range(0.6,3, overlay.opts.labelScale, 0.1, v=>{
      overlay.opts.labelScale=v; overlay.redraw(); prefs.labelScale=v; save(prefs);
    }, true);
    const linePx = range(1,10, overlay.opts.linePx, 1, v=>{
      overlay.opts.linePx=v|0; overlay.redraw(); prefs.linePx=v|0; save(prefs);
    }, true);
    style.append( row(lbl('Label', labelScale.wrap), lbl('Line', linePx.wrap)) );

    // ——— VISIBILITY ———
    const showAnn   = checkbox(overlay.opts.showAnn, v=>{ overlay.opts.showAnn=v; overlay.redraw(); prefs.showAnn=v; save(prefs); });
    const showGrid  = checkbox(overlay.opts.showGrid, v=>{ overlay.opts.showGrid=v; overlay.redraw(); prefs.showGrid=v; save(prefs); });
    const showMarks = checkbox(overlay.opts.showMarkers, v=>{ overlay.opts.showMarkers=v; overlay.redraw(); prefs.showMarkers=v; save(prefs); });
    const visNames = { segment:'Seg', polyline:'Poly', rectangle:'Rect', angle:'Ang', note:'Note' };
    const visWrap = document.createElement('div'); visWrap.className='cal-row';
    overlay.opts.visible = overlay.opts.visible || {};
    Object.keys(visNames).forEach(t=>{
      const c = checkbox(overlay.opts.visible[t]!==false, v=>{
        overlay.opts.visible[t]=v; overlay.redraw();
        prefs.visible = prefs.visible||{}; prefs.visible[t]=v; save(prefs);
      });
      const l = document.createElement('label'); l.className='cal-inline'; l.append(c, document.createTextNode(visNames[t]));
      visWrap.appendChild(l);
    });
    vis.append( row(lbl('Annotations', showAnn), lbl('Grid', showGrid), lbl('Markers', showMarks)), visWrap );

    // ——— SCALE / UNITS ———
    const Units = window.CalibUnits;
    const unitSel = select(Object.keys(Units.defs), overlay.opts.units, v=>{
      overlay.opts.units=v; overlay.redraw(); prefs.units=v; save(prefs);
    }, k=>Units.defs[k]?.label || k);

    const snapChk = checkbox(overlay.opts.snap, v=>{
      overlay.opts.snap=v; snapRange.range.disabled=!v; overlay.updateKPI(); prefs.snap=v; save(prefs);
    });
    const snapRange = range(5,50, overlay.opts.snapPx, 1, v=>{
      overlay.opts.snapPx=v; prefs.snapPx=v; save(prefs);
    }, true);
    snapRange.range.className='cal-range';
    snapRange.range.disabled = !overlay.opts.snap;

    const lockSel = document.createElement('select');
    const optAuto = document.createElement('option'); optAuto.value=''; optAuto.textContent='Scale: Auto (nearest)';
    lockSel.appendChild(optAuto);
    (overlay.data?.markers||[]).forEach(m=>{
      const o=document.createElement('option');
      o.value=String(m.id); o.textContent=`Scale: Marker #${m.id}`;
      lockSel.appendChild(o);
    });
    if(overlay.opts.lockMarkerId!=null) lockSel.value=String(overlay.opts.lockMarkerId);
    lockSel.onchange=()=>{
      overlay.opts.lockMarkerId = lockSel.value || null;
      overlay.redraw();
      prefs.lockMarkerId = overlay.opts.lockMarkerId;
      save(prefs);
    };

    const zoomLive = text(()=> `${Math.round(overlay.vp.k*100)}%`);
    const scaleLive = text(()=>{
      const mm = overlay.getScale()||0, um = mm*1000;
      const u = Units.get(overlay.opts.units);
      return `${um.toFixed(0)}µm/px • ${u.fromMM(mm).toFixed(4)} ${u.label}/px`;
    });

    scale.append(
      row(lbl('Units', unitSel)),
      row(lbl('Snap', snapChk), lbl('Snap px', snapRange.wrap)),
      row(lockSel),
      row(lbl('Zoom', zoomLive), lbl('Scale', scaleLive)),
      small('Snap is OFF by default for precise clicks.')
    );

    // ——— ACTIONS ———
    const noteInput = document.createElement('input'); noteInput.type='text'; noteInput.placeholder='Note text…';
    noteInput.value = overlay.noteText||''; noteInput.oninput = ()=>{ overlay.noteText = noteInput.value; };

    const finishBtn = btn('Finish polyline', ()=>overlay.finishPolyline());
    const undoBtn   = btn('Undo', ()=>overlay.undo());
    const clearBtn  = btn('Clear all', ()=>overlay.clearAll());
    const expOnly   = checkbox(overlay.opts.exportVisibleOnly, v=>{ overlay.opts.exportVisibleOnly=v; prefs.exportVisibleOnly=v; save(prefs); });
    const savePNG   = btn('Save PNG', ()=>overlay.savePNG());
    const saveJSON  = btn('Save JSON', ()=>overlay.saveJSON());

    // reflect selection state into delete button
    const reflectSel = ()=>{ delBtn.disabled = !(overlay.ann && overlay.ann.selectedId != null); };
    const _origRedraw = overlay.redraw.bind(overlay);
    overlay.redraw = ()=>{ _origRedraw(); reflectSel(); };
    reflectSel();

    ops.append(
      row(noteInput, finishBtn, undoBtn, delBtn, clearBtn),
      row(lbl('Export visible only', expOnly), savePNG, saveJSON)
    );

    // public API
    function open(){ details.open = true; summary.scrollIntoView({ behavior:'smooth', block:'end' }); }
    function close(){ details.open = false; }
    function toggle(){ details.open = !details.open; if(details.open){ summary.scrollIntoView({ behavior:'smooth', block:'end' }); } }

    // keep readouts in sync
    let raf=0;
    function tick(){
      zoomLive.textContent = `${Math.round(overlay.vp.k*100)}%`;
      scaleLive.textContent = scaleLive._fn();
      raf = requestAnimationFrame(tick);
    }
    tick();

    // refit canvas when open/close (keeps things crisp)
    details.addEventListener('toggle', ()=>{
      if (overlay.vp && overlay.img) { overlay.vp.fit(overlay.canvas, overlay.img); (overlay.requestDraw||overlay.redraw).call(overlay); }
    }, {passive:true});

    return { root:wrap, details, open, close, toggle };
  }

  // —— helpers ——
  function card(title){ const c=document.createElement('div'); c.className='cal-card'; const h=document.createElement('h4'); h.textContent=title; c.append(h); return c; }
  function row(...els){ const r=document.createElement('div'); r.className='cal-row'; els.forEach(e=>r.appendChild(e)); return r; }
  function small(t){ const el=document.createElement('div'); el.className='cal-small'; el.textContent=t; return el; }
  function lbl(t, el){ const w=document.createElement('label'); w.className='cal-inline'; w.append(document.createTextNode(t+' '), el); return w; }
  function checkbox(cur, on){ const c=document.createElement('input'); c.type='checkbox'; c.checked=!!cur; c.onchange=()=>on(c.checked); return c; }
  function btn(txt, fn){ const b=document.createElement('button'); b.className='cal-btn'; b.textContent=txt; b.onclick=fn; return b; }
  function select(values, cur, onch, map){
    const s=document.createElement('select');
    values.forEach(v=>{ const o=document.createElement('option'); o.value=v; o.textContent=map?map(v):String(v); s.appendChild(o); });
    s.value=String(cur); s.onchange=()=>onch(s.value); return s;
  }
  function range(min,max,cur,step,oninput,show=false){
    const wrap=document.createElement('span'); wrap.className='cal-inline';
    const r=document.createElement('input'); r.type='range'; r.min=min; r.max=max; r.step=step; r.value=cur;
    const out=document.createElement('span'); out.className='cal-small'; if(show) out.textContent=String(cur);
    r.oninput=()=>{ const v=(String(step).includes('.'))?parseFloat(r.value):parseInt(r.value,10); oninput(v); if(show) out.textContent=String(v); };
    wrap.append(r); if(show) wrap.append(out);
    return { wrap, range:r, readout:out };
    }
  function text(fn){ const el=document.createElement('span'); el.className='cal-small'; el._fn=fn; el.textContent=fn(); return el; }

  return { create };
})();
