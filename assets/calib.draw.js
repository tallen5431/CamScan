// Drawing helpers (DPR-aware sizing, labels, grid, markers)
window.CalibDraw = (function(){
  function px(canvas, v){
    const base = Math.max(canvas.width, canvas.height) / 1000;
    return Math.max(1, v * base);
  }
  const font = px => `bold ${px}px Segoe UI, system-ui, sans-serif`;

  // Single source of truth for annotation colors, shared by the live canvas and the
  // PNG export so the two never drift. Hues are an Okabe-Ito-derived, colorblind-safe
  // set — each tool gets a distinct hue, the calibration marker gets a reserved
  // "chrome" magenta, and selection is a high-contrast white that stands out over any
  // base color instead of the old amber that was nearly identical to 'orange'.
  const colors = {
    segment:   '#009e73', // green
    polyline:  '#e69f00', // orange (path)
    rectangle: '#0072b2', // blue (area)
    angle:     '#cc79a7', // reddish purple
    circle:    '#56b4e9', // sky blue
    note:      '#f0e442', // yellow
    marker:    '#f000ff', // magenta — calibration reference (not a measurement tool)
    selected:  '#ffffff'  // selection highlight
  };

  // Per-frame label-placement registry: boxLabel consults it to avoid stacking labels
  // on top of each other (the crowded-PCB problem). resetLabels() must be called once
  // at the start of every frame (live redraw and PNG export) before any boxLabel call.
  let _placed = [];
  function resetLabels(){ _placed = []; }
  function _overlaps(a, b){
    return !(a.x + a.w < b.x || b.x + b.w < a.x || a.y + a.h < b.y || b.y + b.h < a.y);
  }

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
      ctx.strokeStyle=colors.marker;
      ctx.beginPath(); ctx.moveTo(pts[0][0],pts[0][1]);
      for(let i=1;i<pts.length;i++) ctx.lineTo(pts[i][0],pts[i][1]);
      ctx.closePath(); ctx.stroke();
      ctx.fillStyle=colors.marker;
      for(const [x,y] of pts){
        ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill();
        ctx.strokeStyle="#000"; ctx.lineWidth=px(canvas,2); ctx.stroke();
      }
    }
  }

  // accent (optional): a tool color used for a thin colored strip on the label's left
  // edge, so a label can be traced back to its annotation in a cluster.
  function boxLabel(ctx, canvas, cx, cy, text, scale=1.2, accent){
    const f = Math.round(22*scale), pad = 10*scale, gap = 6*scale;
    // Size the box to the actual text instead of a 200px floor, so labels are snug
    // and far less likely to overlap in the first place.
    ctx.font = font(f);
    ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
    const boxW = ctx.measureText(text).width + 2*pad;
    const boxH = f + 2*pad;

    // Desired position: centered above the anchor. Then nudge to avoid earlier labels.
    let box = { x: cx - boxW/2, y: cy - boxH - 12, w: boxW, h: boxH };
    let moved = false, guard = 0;
    while(_placed.some(p => _overlaps(box, p)) && guard++ < 60){ box.y -= (boxH + gap); moved = true; }
    // If stacking pushed it off the top, drop below the anchor and walk downward.
    if(box.y < 2){ box.y = cy + 12; moved = true; guard = 0;
      while(_placed.some(p => _overlaps(box, p)) && guard++ < 60){ box.y += (boxH + gap); }
    }
    _placed.push({ x: box.x, y: box.y, w: boxW, h: boxH });

    // Leader line back to the anchor when the label had to move, so it stays tied to
    // its measurement.
    if(moved){
      ctx.save();
      ctx.strokeStyle = "rgba(255,255,255,.55)"; ctx.lineWidth = px(canvas, 1);
      ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(box.x + boxW/2, box.y + boxH/2); ctx.stroke();
      ctx.restore();
    }

    ctx.fillStyle = "rgba(0,0,0,.72)"; ctx.fillRect(box.x, box.y, boxW, boxH);
    if(accent){ ctx.fillStyle = accent; ctx.fillRect(box.x, box.y, Math.max(px(canvas,3), 3*scale), boxH); }
    ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = px(canvas, 1.5); ctx.strokeRect(box.x, box.y, boxW, boxH);
    ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "alphabetic"; ctx.font = font(f);
    ctx.fillText(text, box.x + boxW/2, box.y + pad + f*0.75);
  }

  return { px, font, drawGrid, drawMarkers, boxLabel, colors, resetLabels };
})();