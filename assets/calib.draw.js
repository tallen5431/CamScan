// Drawing helpers (DPR-aware sizing, labels, grid, markers)
window.CalibDraw = (function(){
  function px(canvas, v){
    const base = Math.max(canvas.width, canvas.height) / 1000;
    return Math.max(1, v * base);
  }
  const font = px => `bold ${px}px Segoe UI, system-ui, sans-serif`;

  function drawGrid(ctx, w, h){
    ctx.save(); ctx.lineWidth=1; ctx.strokeStyle="rgba(255,255,255,0.12)";
    for(let x=0;x<=w;x+=50){ ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,h); ctx.stroke(); }
    for(let y=0;y<=h;y+=50){ ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(w,y); ctx.stroke(); }
    ctx.restore();
  }

  function drawMarkers(ctx, canvas, data, linePx){
    if(!data || !Array.isArray(data.markers)) return;
    const dotR = px(canvas, 10);
    ctx.lineWidth = px(canvas, linePx);
    for(const m of data.markers){
      if(!m.corners || m.corners.length<4) continue;
      const pts = m.corners.map(p=>[p.x,p.y]);
      ctx.strokeStyle="rgba(255,255,0,.95)";
      ctx.beginPath(); ctx.moveTo(pts[0][0],pts[0][1]);
      for(let i=1;i<pts.length;i++) ctx.lineTo(pts[i][0],pts[i][1]);
      ctx.closePath(); ctx.stroke();
      ctx.fillStyle="rgba(0,255,255,1)";
      for(const [x,y] of pts){
        ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill();
        ctx.strokeStyle="#000"; ctx.lineWidth=px(canvas,2); ctx.stroke();
      }
    }
  }

  function boxLabel(ctx, canvas, cx, cy, text, scale=1.2){
    const f = Math.round(22*scale), pad = 10*scale;
    const boxW = Math.max(200, text.length*9) * (scale*0.9);
    const boxH = (f + 2*pad);
    const lx = cx - boxW/2, ly = cy - boxH - 12;
    ctx.fillStyle="rgba(0,0,0,.72)"; ctx.fillRect(lx,ly,boxW,boxH);
    ctx.strokeStyle="rgba(255,255,255,.35)"; ctx.lineWidth=px(ctx.canvas,1.5); ctx.strokeRect(lx,ly,boxW,boxH);
    ctx.fillStyle="#fff"; ctx.textAlign="center"; ctx.font=font(f);
    ctx.fillText(text, lx+boxW/2, ly + pad + f*0.75);
  }

  return { px, font, drawGrid, drawMarkers, boxLabel };
})();