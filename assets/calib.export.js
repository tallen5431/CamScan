// PNG + JSON + DXF + CSV exporters (native-resolution rendering)
window.CalibExport = (function(){
  const Units = window.CalibUnits;
  const Draw  = window.CalibDraw;
  const Geom  = window.CalibGeom;

  function _drawAnnotations(ctx, canvas, data, store, unitsKey, labelScale=1.4, linePx=3){
    const unit = Units.get(unitsKey);
    const dotR = Draw.px(canvas, 8), line = Draw.px(canvas, linePx);
    const Circles = window.CalibCircles;  // Circle module

    for(const a of store.items){
      const selColor = "rgba(255,170,0,1)"; // ignored for export but keeps parity
      if(a.type==='segment'){
        const mm_per_px = a.mm_per_px || data?.mm_per_px || 0;
        const val = unit.fromMM(Math.hypot(a.b[0]-a.a[0], a.b[1]-a.a[1]) * mm_per_px);
        ctx.lineWidth=line; ctx.strokeStyle="lime";
        ctx.beginPath(); ctx.moveTo(a.a[0],a.a[1]); ctx.lineTo(a.b[0],a.b[1]); ctx.stroke();
        ctx.fillStyle="lime"; for(const [x,y] of [a.a,a.b]){ ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke(); }
        const mid=[(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2];
        Draw.boxLabel(ctx, canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, labelScale);
      }else if(a.type==='circle'){
        // Draw circle using CalibCircles module
        if(Circles){
          Circles.drawCircle(ctx, canvas, a, data, unitsKey, false);
        }
      }else if(a.type==='note'){
        const tx=a.p[0], ty=a.p[1];
        ctx.fillStyle="deepskyblue"; ctx.beginPath(); ctx.arc(tx,ty,Draw.px(canvas,9),0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke();
        if(a.text){ const pad=8*labelScale, f=Math.round(18*labelScale); const boxW=Math.max(140, a.text.length*10)*(labelScale*0.8), boxH=f+2*pad; const lx=tx+14, ly=ty-boxH/2; ctx.fillStyle="rgba(0,0,0,.7)"; ctx.fillRect(lx,ly,boxW,boxH); ctx.strokeStyle="rgba(255,255,255,.35)"; ctx.lineWidth=Draw.px(canvas,1.5); ctx.strokeRect(lx,ly,boxW,boxH); ctx.fillStyle="#fff"; ctx.textAlign="left"; ctx.textBaseline="middle"; ctx.font=Draw.font(f); ctx.fillText(a.text, lx+pad, ly+boxH/2); }
      }else if(a.type==='polyline'){
        const pts=a.pts||[]; if(pts.length<2) continue;
        const mm_per_px=a.mm_per_px||data?.mm_per_px||0; let pxSum=0; for(let i=1;i<pts.length;i++) pxSum+=Math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]);
        const val=unit.fromMM(pxSum*mm_per_px);
        ctx.lineWidth=line; ctx.strokeStyle="orange"; ctx.beginPath(); ctx.moveTo(pts[0][0],pts[0][1]); for(let i=1;i<pts.length;i++) ctx.lineTo(pts[i][0],pts[i][1]); ctx.stroke();
        ctx.fillStyle="orange"; for(const [x,y] of pts){ ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke(); }
        const mid = pts[Math.floor(pts.length/2)]; Draw.boxLabel(ctx, canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, labelScale);
      }else if(a.type==='rectangle'){
        const [x1,y1,x2,y2]=a.rect; const mm_per_px=a.mm_per_px||data?.mm_per_px||0; const wmm=(x2-x1)*mm_per_px, hmm=(y2-y1)*mm_per_px, amm=wmm*hmm;
        ctx.lineWidth=line; ctx.strokeStyle="orange"; ctx.strokeRect(x1,y1,x2-x1,y2-y1);
        Draw.boxLabel(ctx, canvas, (x1+x2)/2, y1-10, `${unit.fromMM(wmm).toFixed(3)}×${unit.fromMM(hmm).toFixed(3)} ${unit.label} • A ${(amm).toFixed(1)} mm²`, labelScale);
      }else if(a.type==='angle'){
        ctx.lineWidth=line; ctx.strokeStyle="orange"; ctx.beginPath(); ctx.moveTo(a.v[0],a.v[1]); ctx.lineTo(a.a[0],a.a[1]); ctx.moveTo(a.v[0],a.v[1]); ctx.lineTo(a.b[0],a.b[1]); ctx.stroke();
        const ang=Geom.angleABC(a.a,a.v,a.b); Draw.boxLabel(ctx, canvas, a.v[0], a.v[1]-20, `θ ${ang.toFixed(2)}°`, labelScale);
      }
    }
  }

  function exportPNG(img, data, store, showGrid, showMarkers, unitsKey, labelScale=1.4, linePx=3){
    const w = img.naturalWidth || img.width; const h = img.naturalHeight || img.height;
    const off = document.createElement('canvas'); off.width = w; off.height = h; const ctx = off.getContext('2d');
    ctx.drawImage(img, 0, 0);
    if(showGrid) Draw.drawGrid(ctx, w, h);
    if(showMarkers) Draw.drawMarkers(ctx, off, data, linePx);
    _drawAnnotations(ctx, off, data, store, unitsKey, labelScale, linePx);
    off.toBlob((blob)=>{ const a=document.createElement('a'); a.download='annotated.png'; a.href=URL.createObjectURL(blob); a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000); }, 'image/png');
  }

  function exportJSON(payload){
    const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
    const a=document.createElement('a'); a.download='annotations.json'; a.href=URL.createObjectURL(blob); a.click(); setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
  }

  // Export measurements to CSV for spreadsheet analysis
  function exportCSV(data, store, unitsKey){
    const unit = Units.get(unitsKey);
    const mm_per_px = data?.mm_per_px || 1.0;
    const Circles = window.CalibCircles;

    let csv = 'Type,Label,Value,Unit,Value_mm,Area_mm2,Notes\n';

    for(let i = 0; i < store.items.length; i++){
      const a = store.items[i];
      const label = `Item_${i+1}`;

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
        csv += `Circle,${label},${d_val.toFixed(3)},${unit.label},${d_mm.toFixed(3)},${area_mm2.toFixed(2)},Circumference: ${circum_mm.toFixed(2)}mm\n`;

      }else if(a.type === 'rectangle'){
        const [x1,y1,x2,y2] = a.rect;
        const w_mm = (x2-x1) * mm_per_px;
        const h_mm = (y2-y1) * mm_per_px;
        const area_mm2 = w_mm * h_mm;
        const w_val = unit.fromMM(w_mm);
        const h_val = unit.fromMM(h_mm);
        csv += `Rectangle,${label},${w_val.toFixed(3)}x${h_val.toFixed(3)},${unit.label},${w_mm.toFixed(3)}x${h_mm.toFixed(3)},${area_mm2.toFixed(2)},\n`;

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
        csv += `Note,${label},,,,,\"${text}\"\n`;
      }
    }

    const blob = new Blob([csv], {type:'text/csv'});
    const link = document.createElement('a');
    link.download = 'measurements.csv';
    link.href = URL.createObjectURL(blob);
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  // Export to DXF format for CAD software (requires backend endpoint)
  async function exportDXF(data, store){
    const mm_per_px = data?.mm_per_px || 1.0;
    const Circles = window.CalibCircles;

    // Prepare geometry data
    const geometry = [];

    for(const a of store.items){
      if(a.type === 'segment'){
        geometry.push({
          type: 'line',
          x1: a.a[0],
          y1: a.a[1],
          x2: a.b[0],
          y2: a.b[1]
        });
      }else if(a.type === 'circle'){
        if(Circles){
          geometry.push(Circles.circleToJSON(a, data, 'mm'));
        }
      }else if(a.type === 'rectangle'){
        const [x1, y1, x2, y2] = a.rect;
        geometry.push({
          type: 'rectangle',
          x1: x1, y1: y1,
          x2: x2, y2: y2
        });
      }else if(a.type === 'polyline'){
        geometry.push({
          type: 'polyline',
          points: a.pts || []
        });
      }
    }

    // Send to backend for DXF generation
    try {
      const response = await fetch('/api/export/dxf', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({geometry, mm_per_px})
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
      exportJSON({geometry, mm_per_px, format: 'dxf_compatible'});
      return false;
    }
  }

  return { exportPNG, exportJSON, exportCSV, exportDXF };
})();