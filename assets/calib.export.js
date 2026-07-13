// PNG + JSON + DXF + CSV exporters (native-resolution rendering)
window.CalibExport = (function(){
  // Resolve dependencies lazily so module load order never matters.
  const U = () => window.CalibUnits;
  const D = () => window.CalibDraw;
  const G = () => window.CalibGeom;

  // Scale (mm/px) for an annotation: its own captured value, else the fallback, else
  // the calibration average. Keeps exports consistent with what is shown on screen.
  function _scaleFor(a, data, fallback){
    return a.mm_per_px || fallback || (data && data.mm_per_px) || 0;
  }

  // Resolve the app's request path prefix (from Dash config) so API calls work whether
  // the app is served at "/" or behind a reverse proxy at "/somepath/".
  function _apiUrl(path){
    let base='/';
    try{ const cfg=JSON.parse(document.getElementById('_dash-config').textContent); base=cfg.requests_pathname_prefix||'/'; }catch(e){}
    if(!base.endsWith('/')) base+='/';
    return base + path.replace(/^\//,'');
  }

  function _drawAnnotations(ctx, canvas, data, store, unitsKey, labelScale=1.4, linePx=3, fallbackScale=0){
    const unit = U().get(unitsKey);
    const Draw = D();
    const dotR = Draw.px(canvas, 8), line = Draw.px(canvas, linePx);
    const Circles = window.CalibCircles;

    for(const a of store.items){
      if(a.type==='segment'){
        const mm_per_px = _scaleFor(a, data, fallbackScale);
        const val = unit.fromMM(Math.hypot(a.b[0]-a.a[0], a.b[1]-a.a[1]) * mm_per_px);
        ctx.lineWidth=line; ctx.strokeStyle="lime";
        ctx.beginPath(); ctx.moveTo(a.a[0],a.a[1]); ctx.lineTo(a.b[0],a.b[1]); ctx.stroke();
        ctx.fillStyle="lime"; for(const [x,y] of [a.a,a.b]){ ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke(); }
        const mid=[(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2];
        Draw.boxLabel(ctx, canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, labelScale);
      }else if(a.type==='circle'){
        if(Circles){
          Circles.drawCircle(ctx, canvas, a, data, unitsKey,
            { selected:false, labelScale, linePx, fallbackScale });
        }
      }else if(a.type==='note'){
        const tx=a.p[0], ty=a.p[1];
        ctx.fillStyle="deepskyblue"; ctx.beginPath(); ctx.arc(tx,ty,Draw.px(canvas,9),0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke();
        if(a.text){ const pad=8*labelScale, f=Math.round(18*labelScale); ctx.font=Draw.font(f); ctx.textAlign="left"; ctx.textBaseline="middle"; const boxW=ctx.measureText(a.text).width + 2*pad, boxH=f+2*pad; const lx=tx+14, ly=ty-boxH/2; ctx.fillStyle="rgba(0,0,0,.7)"; ctx.fillRect(lx,ly,boxW,boxH); ctx.strokeStyle="rgba(255,255,255,.35)"; ctx.lineWidth=Draw.px(canvas,1.5); ctx.strokeRect(lx,ly,boxW,boxH); ctx.fillStyle="#fff"; ctx.fillText(a.text, lx+pad, ly+boxH/2); }
      }else if(a.type==='polyline'){
        const pts=a.pts||[]; if(pts.length<2) continue;
        const mm_per_px=_scaleFor(a, data, fallbackScale); let pxSum=0; for(let i=1;i<pts.length;i++) pxSum+=Math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]);
        const val=unit.fromMM(pxSum*mm_per_px);
        ctx.lineWidth=line; ctx.strokeStyle="orange"; ctx.beginPath(); ctx.moveTo(pts[0][0],pts[0][1]); for(let i=1;i<pts.length;i++) ctx.lineTo(pts[i][0],pts[i][1]); ctx.stroke();
        ctx.fillStyle="orange"; for(const [x,y] of pts){ ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke(); }
        const mid = pts[Math.floor(pts.length/2)]; Draw.boxLabel(ctx, canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, labelScale);
      }else if(a.type==='rectangle'){
        const [x1,y1,x2,y2]=a.rect; const mm_per_px=_scaleFor(a, data, fallbackScale); const wmm=(x2-x1)*mm_per_px, hmm=(y2-y1)*mm_per_px, amm=wmm*hmm;
        ctx.lineWidth=line; ctx.strokeStyle="orange"; ctx.strokeRect(x1,y1,x2-x1,y2-y1);
        Draw.boxLabel(ctx, canvas, (x1+x2)/2, y1-10, `${unit.fromMM(wmm).toFixed(3)}×${unit.fromMM(hmm).toFixed(3)} ${unit.label} • A ${unit.areaFromMM2(amm).toFixed(3)} ${unit.areaLabel}`, labelScale);
      }else if(a.type==='angle'){
        ctx.lineWidth=line; ctx.strokeStyle="orange"; ctx.beginPath(); ctx.moveTo(a.v[0],a.v[1]); ctx.lineTo(a.a[0],a.a[1]); ctx.moveTo(a.v[0],a.v[1]); ctx.lineTo(a.b[0],a.b[1]); ctx.stroke();
        const ang=G().angleABC(a.a,a.v,a.b); Draw.boxLabel(ctx, canvas, a.v[0], a.v[1]-20, `θ ${ang.toFixed(2)}°`, labelScale);
      }
    }
  }

  function exportPNG(img, data, store, showGrid, showMarkers, unitsKey, labelScale=1.4, linePx=3){
    const w = img.naturalWidth || img.width; const h = img.naturalHeight || img.height;
    const off = document.createElement('canvas'); off.width = w; off.height = h; const ctx = off.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const fallback = (data && data.mm_per_px) || 0;
    if(showGrid) D().drawGrid(ctx, w, h);
    if(showMarkers) D().drawMarkers(ctx, off, data, linePx);
    _drawAnnotations(ctx, off, data, store, unitsKey, labelScale, linePx, fallback);
    off.toBlob((blob)=>{ const a=document.createElement('a'); a.download='annotated.png'; a.href=URL.createObjectURL(blob); a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000); }, 'image/png');
  }

  function exportJSON(payload){
    const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
    const a=document.createElement('a'); a.download='annotations.json'; a.href=URL.createObjectURL(blob); a.click(); setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
  }

  // Export measurements to CSV for spreadsheet analysis. fallbackScale (mm/px) is used
  // for annotations that did not capture their own scale.
  function exportCSV(data, store, unitsKey, fallbackScale=0){
    const unit = U().get(unitsKey);
    const Geom = G();

    let csv = 'Type,Label,Value,Unit,Value_mm,Area_mm2,Notes\n';

    for(let i = 0; i < store.items.length; i++){
      const a = store.items[i];
      const label = `Item_${i+1}`;
      const mm_per_px = _scaleFor(a, data, fallbackScale);

      if(a.type === 'segment'){
        const px = Math.hypot(a.b[0]-a.a[0], a.b[1]-a.a[1]);
        const mm = px * mm_per_px;
        const val = unit.fromMM(mm);
        csv += `Line,${label},${val.toFixed(3)},${unit.label},${mm.toFixed(3)},,\n`;

      }else if(a.type === 'circle'){
        const r_mm = a.radius * mm_per_px;
        const d_mm = 2 * r_mm;
        const area_mm2 = Math.PI * r_mm * r_mm;
        const circum_mm = 2 * Math.PI * r_mm;
        const d_val = unit.fromMM(d_mm);
        csv += `Circle,${label},${d_val.toFixed(3)},${unit.label},${d_mm.toFixed(3)},${area_mm2.toFixed(3)},Circumference: ${circum_mm.toFixed(2)}mm\n`;

      }else if(a.type === 'rectangle'){
        const [x1,y1,x2,y2] = a.rect;
        const w_mm = (x2-x1) * mm_per_px;
        const h_mm = (y2-y1) * mm_per_px;
        const area_mm2 = w_mm * h_mm;
        const w_val = unit.fromMM(w_mm);
        const h_val = unit.fromMM(h_mm);
        csv += `Rectangle,${label},${w_val.toFixed(3)}x${h_val.toFixed(3)},${unit.label},${w_mm.toFixed(3)}x${h_mm.toFixed(3)},${area_mm2.toFixed(3)},\n`;

      }else if(a.type === 'polyline'){
        const pts = a.pts || [];
        let px_sum = 0;
        for(let j = 1; j < pts.length; j++){
          px_sum += Math.hypot(pts[j][0]-pts[j-1][0], pts[j][1]-pts[j-1][1]);
        }
        const mm = px_sum * mm_per_px;
        const val = unit.fromMM(mm);
        csv += `Polyline,${label},${val.toFixed(3)},${unit.label},${mm.toFixed(3)},,${pts.length} points\n`;

      }else if(a.type === 'angle'){
        const ang = Geom.angleABC(a.a, a.v, a.b);
        csv += `Angle,${label},${ang.toFixed(2)},degrees,,,\n`;

      }else if(a.type === 'note'){
        const text = (a.text || '').replace(/"/g, '""');  // Escape quotes
        csv += `Note,${label},,,,,"${text}"\n`;
      }
    }

    const blob = new Blob([csv], {type:'text/csv'});
    const link = document.createElement('a');
    link.download = 'measurements.csv';
    link.href = URL.createObjectURL(blob);
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  // Export to DXF for CAD software (requires the /api/export/dxf backend endpoint).
  // opts: { image_height (px, for Y-flip), mm_per_px (default/fallback scale) }
  async function exportDXF(data, store, opts){
    opts = opts || {};
    const fallback = opts.mm_per_px || (data && data.mm_per_px) || 1.0;
    const Circles = window.CalibCircles;
    const geometry = [];

    for(const a of store.items){
      const s = _scaleFor(a, data, fallback) || fallback;
      if(a.type === 'segment'){
        geometry.push({ type:'line', x1:a.a[0], y1:a.a[1], x2:a.b[0], y2:a.b[1], mm_per_px:s });
      }else if(a.type === 'circle'){
        if(Circles){ const c = Circles.circleToJSON(a, data, 'mm'); c.mm_per_px = s; geometry.push(c); }
      }else if(a.type === 'rectangle'){
        const [x1, y1, x2, y2] = a.rect;
        geometry.push({ type:'rectangle', x1, y1, x2, y2, mm_per_px:s });
      }else if(a.type === 'polyline'){
        geometry.push({ type:'polyline', points:a.pts || [], mm_per_px:s });
      }
    }

    if(!geometry.length){ alert('Nothing to export to DXF — draw a line, rectangle, circle or polyline first.'); return false; }

    try {
      const response = await fetch(_apiUrl('api/export/dxf'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ geometry, mm_per_px: fallback, image_height: opts.image_height || 0 })
      });

      if(response.ok){
        const blob = await response.blob();
        const link = document.createElement('a');
        link.download = 'geometry.dxf';
        link.href = URL.createObjectURL(blob);
        link.click();
        setTimeout(() => URL.revokeObjectURL(link.href), 1000);
        return true;
      }else{
        console.error('[DXF Export] Server error:', await response.text());
        alert('DXF export failed. Backend endpoint may not be available.');
        return false;
      }
    }catch(err){
      console.error('[DXF Export] Error:', err);
      alert('DXF export requires backend support. Downloading JSON instead.');
      exportJSON({geometry, mm_per_px: fallback, image_height: opts.image_height || 0, format: 'dxf_compatible'});
      return false;
    }
  }

  return { exportPNG, exportJSON, exportCSV, exportDXF };
})();
