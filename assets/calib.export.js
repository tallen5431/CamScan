// PNG + JSON exporters (native-resolution rendering)
window.CalibExport = (function(){
  const Units = window.CalibUnits;
  const Draw  = window.CalibDraw;
  const Geom  = window.CalibGeom;

  function _drawAnnotations(ctx, canvas, data, store, unitsKey, labelScale=1.4, linePx=3){
    const unit = Units.get(unitsKey);
    const dotR = Draw.px(canvas, 8), line = Draw.px(canvas, linePx);

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

  return { exportPNG, exportJSON };
})();