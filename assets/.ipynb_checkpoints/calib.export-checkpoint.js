// calib.export.js — download helpers (PNG + JSON)
window.CalibExport = (function () {
  const Units = window.CalibUnits, Draw = window.CalibDraw, Geom = window.CalibGeom;

  function downloadBlob(blob, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }

  function exportJSON(payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const name = (payload?.image ? (payload.image.split('/').pop() || 'calibration') : 'calibration') + '.annotations.json';
    downloadBlob(blob, name);
  }

  // Minimal, faithful PNG render (image + optional grid/markers + annotations)
  function exportPNG(img, calib, ann, showGrid, showMarkers, unitsKey) {
    if (!img || !img.width || !img.height) return;
    const c = document.createElement('canvas');
    c.width = img.width; c.height = img.height;
    const ctx = c.getContext('2d', { alpha: false });

    // helpers (match overlay sizing)
    const px = v => Math.max(1, Math.max(c.width, c.height) / 1000 * v);
    const font = p => `bold ${Math.round(p)}px Segoe UI, sans-serif`;

    // background
    ctx.fillStyle = '#000'; ctx.fillRect(0, 0, c.width, c.height);
    ctx.drawImage(img, 0, 0);

    if (showGrid) Draw.drawGrid(ctx, c.width, c.height);
    if (showMarkers && calib) Draw.drawMarkers(ctx, c, calib, 3);

    const unit = Units.get(unitsKey || 'mm');
    const dotR = px(8), linePx = px(3);

    // Label box (same look as overlay)
    function boxLabel(cx, cy, text, scale = 1.4) {
      const f = Math.round(22 * scale), pad = 10 * scale;
      const boxW = Math.max(200, text.length * 9) * (scale * 0.9);
      const boxH = (f + 2 * pad);
      const lx = cx - boxW / 2, ly = cy - boxH - 12;
      ctx.fillStyle = "rgba(0,0,0,.72)"; ctx.fillRect(lx, ly, boxW, boxH);
      ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = px(1.5); ctx.strokeRect(lx, ly, boxW, boxH);
      ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.font = font(f);
      ctx.fillText(text, lx + boxW / 2, ly + pad + f * 0.75);
    }

    // best-available mm/px near a point
    function mmPerPxAt(pt) {
      const markers = calib?.markers || [];
      if (!markers.length) return calib?.mm_per_px || 0;
      let best = null, bestD = 1e18;
      for (const m of markers) {
        if (!m.mm_per_px || !m.corners || m.corners.length < 4) continue;
        const pts = m.corners.map(p => [p.x, p.y]);
        const c0 = pts.reduce((a, p) => [a[0] + p[0] / pts.length, a[1] + p[1] / pts.length], [0, 0]);
        const d = Math.hypot((pt?.[0]||0) - c0[0], (pt?.[1]||0) - c0[1]);
        if (d < bestD) { bestD = d; best = m.mm_per_px; }
      }
      return best || calib?.mm_per_px || 0;
    }

    // Draw annotations
    for (const a of (ann?.items || [])) {
      const selColor = "rgba(255,170,0,1)";
      if (a.type === 'segment') {
        const mmpx = a.mm_per_px || mmPerPxAt([(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2]);
        const val = unit.fromMM(Math.hypot(a.b[0]-a.a[0], a.b[1]-a.a[1]) * mmpx);
        ctx.lineWidth = linePx; ctx.strokeStyle = "lime";
        ctx.beginPath(); ctx.moveTo(a.a[0], a.a[1]); ctx.lineTo(a.b[0], a.b[1]); ctx.stroke();
        ctx.fillStyle = "lime";
        for (const [x, y] of [a.a, a.b]) { ctx.beginPath(); ctx.arc(x, y, dotR, 0, Math.PI * 2); ctx.fill(); ctx.strokeStyle = "#000"; ctx.lineWidth = px(2); ctx.stroke(); }
        const mid = [(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2]; boxLabel(mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`);
      } else if (a.type === 'note') {
        const [tx, ty] = a.p;
        ctx.fillStyle = "deepskyblue"; ctx.beginPath(); ctx.arc(tx, ty, px(9), 0, Math.PI * 2); ctx.fill();
        if (a.text) {
          const scale = 1.4, pad = 8 * scale, f = Math.round(18 * scale);
          const boxW = Math.max(140, a.text.length * 10) * (scale * 0.8), boxH = f + 2 * pad;
          const lx = tx + 14, ly = ty - boxH / 2;
          ctx.fillStyle = "rgba(0,0,0,.7)"; ctx.fillRect(lx, ly, boxW, boxH);
          ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = px(1.5); ctx.strokeRect(lx, ly, boxW, boxH);
          ctx.fillStyle = "#fff"; ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.font = font(f);
          ctx.fillText(a.text, lx + pad, ly + boxH / 2);
        }
      } else if (a.type === 'polyline') {
        const pts = a.pts || []; if (pts.length < 2) continue;
        let pxSum = 0; for (let i = 1; i < pts.length; i++) pxSum += Math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]);
        const mmpx = a.mm_per_px || mmPerPxAt(pts[Math.floor(pts.length/2)]);
        const val = unit.fromMM(pxSum * mmpx);
        ctx.lineWidth = linePx; ctx.strokeStyle = "orange"; ctx.beginPath(); ctx.moveTo(pts[0][0], pts[0][1]);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]); ctx.stroke();
        ctx.fillStyle = "orange"; for (const [x,y] of pts){ ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=px(2); ctx.stroke(); }
        const mid = pts[Math.floor(pts.length/2)]; boxLabel(mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`);
      } else if (a.type === 'rectangle') {
        const [x1,y1,x2,y2] = a.rect; const mmpx = a.mm_per_px || mmPerPxAt([(x1+x2)/2,(y1+y2)/2]);
        const wmm = (x2-x1)*mmpx, hmm = (y2-y1)*mmpx, amm = wmm*hmm;
        ctx.lineWidth = linePx; ctx.strokeStyle = "orange"; ctx.strokeRect(x1,y1,x2-x1,y2-y1);
        boxLabel((x1+x2)/2, y1-10, `${unit.fromMM(wmm).toFixed(3)}×${unit.fromMM(hmm).toFixed(3)} ${unit.label} • A ${amm.toFixed(1)} mm²`);
      } else if (a.type === 'angle') {
        ctx.lineWidth = linePx; ctx.strokeStyle = "orange";
        ctx.beginPath(); ctx.moveTo(a.v[0], a.v[1]); ctx.lineTo(a.a[0], a.a[1]); ctx.moveTo(a.v[0], a.v[1]); ctx.lineTo(a.b[0], a.b[1]); ctx.stroke();
        const ang = Geom.angleABC(a.a, a.v, a.b); boxLabel(a.v[0], a.v[1] - 20, `θ ${ang.toFixed(2)}°`);
      }
    }

    downloadBlob(dataURLtoBlob(c.toDataURL('image/png')), 'calibration.png');

    function dataURLtoBlob(dataURL) {
      const parts = dataURL.split(','), bstr = atob(parts[1]); let n = bstr.length;
      const u8 = new Uint8Array(n); while (n--) u8[n] = bstr.charCodeAt(n);
      return new Blob([u8], { type: 'image/png' });
    }
  }

  return { exportJSON, exportPNG };
})();
