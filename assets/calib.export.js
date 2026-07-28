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

  // Neutralize spreadsheet formula injection: a cell beginning with = + - @ (or a
  // leading tab/CR) is executed as a formula by Excel/Sheets/LibreOffice on open, so
  // prefix a single quote. Also RFC-4180 double-quote escaping for the quoted field.
  function _csvSafe(s){
    s = String(s == null ? '' : s);
    if(/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return s.replace(/"/g, '""');
  }

  // Resolve the app's request path prefix (from Dash config) so API calls work whether
  // the app is served at "/" or behind a reverse proxy at "/somepath/".
  function _apiUrl(path){
    let base='/';
    try{ const cfg=JSON.parse(document.getElementById('_dash-config').textContent); base=cfg.requests_pathname_prefix||'/'; }catch(e){}
    if(!base.endsWith('/')) base+='/';
    return base + path.replace(/^\//,'');
  }

  function _drawAnnotations(ctx, canvas, data, store, unitsKey, labelScale=1.4, linePx=3, fallbackScale=0, allowHomography=true){
    const unit = U().get(unitsKey);
    const Draw = D();
    const C = Draw.colors || {};
    const M = window.CalibMeasure;
    const dotR = Draw.px(canvas, 8), line = Draw.px(canvas, linePx);
    const Circles = window.CalibCircles;
    // Same perspective-aware measurement as the live canvas, so exports match.
    const mctx = a => M.context(data, _scaleFor(a, data, fallbackScale), allowHomography);
    if(Draw.resetLabels) Draw.resetLabels();   // fresh label-placement frame for the export

    for(const a of store.items){
      if(a.type==='segment'){
        const val = unit.fromMM(M.length(mctx(a), a.a[0],a.a[1], a.b[0],a.b[1]));
        ctx.lineWidth=line; ctx.strokeStyle=C.segment;
        ctx.beginPath(); ctx.moveTo(a.a[0],a.a[1]); ctx.lineTo(a.b[0],a.b[1]); ctx.stroke();
        ctx.fillStyle=C.segment; for(const [x,y] of [a.a,a.b]){ ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke(); }
        const mid=[(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2];
        Draw.boxLabel(ctx, canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, labelScale, C.segment);
      }else if(a.type==='circle'){
        if(Circles){
          // Forward allowHomography (drawCircle defaults it to true when the key is absent),
          // so a PNG / submission JPEG stamps the SAME diameter/area as the live canvas, the
          // CSV, the DXF and the SVG — e.g. after a manual uniform scale disables tilt-correction.
          Circles.drawCircle(ctx, canvas, a, data, unitsKey,
            { selected:false, labelScale, linePx, fallbackScale, allowHomography });
        }
      }else if(a.type==='note'){
        const tx=a.p[0], ty=a.p[1];
        ctx.fillStyle=C.note; ctx.beginPath(); ctx.arc(tx,ty,Draw.px(canvas,9),0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke();
        if(a.text){ const pad=8*labelScale, f=Math.round(18*labelScale); ctx.font=Draw.font(f); ctx.textAlign="left"; ctx.textBaseline="middle"; const boxW=ctx.measureText(a.text).width + 2*pad, boxH=f+2*pad; const lx=tx+14, ly=ty-boxH/2; ctx.fillStyle="rgba(0,0,0,.72)"; ctx.fillRect(lx,ly,boxW,boxH); ctx.strokeStyle="rgba(255,255,255,.35)"; ctx.lineWidth=Draw.px(canvas,1.5); ctx.strokeRect(lx,ly,boxW,boxH); ctx.fillStyle="#fff"; ctx.fillText(a.text, lx+pad, ly+boxH/2); }
      }else if(a.type==='polyline'){
        const pts=a.pts||[]; if(pts.length<2) continue;
        const m2=mctx(a);
        const closed=!!a.closed && pts.length>=3;
        const val=unit.fromMM(M.polyline(m2, pts, closed));
        ctx.lineWidth=line; ctx.strokeStyle=C.polyline; ctx.beginPath(); ctx.moveTo(pts[0][0],pts[0][1]); for(let i=1;i<pts.length;i++) ctx.lineTo(pts[i][0],pts[i][1]);
        if(closed){ ctx.closePath(); ctx.save(); ctx.globalAlpha=0.12; ctx.fillStyle=C.polyline; ctx.fill(); ctx.restore(); }
        ctx.stroke();
        ctx.fillStyle=C.polyline; for(const [x,y] of pts){ ctx.beginPath(); ctx.arc(x,y,dotR,0,Math.PI*2); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=Draw.px(canvas,2); ctx.stroke(); }
        const mid = pts[Math.floor(pts.length/2)];
        let lab = `${val.toFixed(3)} ${unit.label}`;
        if(closed){ const ar=unit.areaFromMM2(M.polygonArea(m2, pts)); lab = `⬡ ${val.toFixed(2)} ${unit.label} · A ${ar.toFixed(1)} ${unit.areaLabel}`; }
        Draw.boxLabel(ctx, canvas, mid[0], mid[1], lab, labelScale, C.polyline);
      }else if(a.type==='rectangle'){
        const [x1,y1,x2,y2]=a.rect; const rm=M.rect(mctx(a), x1,y1,x2,y2); const wmm=rm.w, hmm=rm.h, amm=rm.area;
        ctx.lineWidth=line; ctx.strokeStyle=C.rectangle; ctx.strokeRect(x1,y1,x2-x1,y2-y1);
        Draw.boxLabel(ctx, canvas, (x1+x2)/2, y1-10, `${unit.fromMM(wmm).toFixed(3)}×${unit.fromMM(hmm).toFixed(3)} ${unit.label} • A ${unit.areaFromMM2(amm).toFixed(3)} ${unit.areaLabel}`, labelScale, C.rectangle);
      }else if(a.type==='angle'){
        ctx.lineWidth=line; ctx.strokeStyle=C.angle; ctx.beginPath(); ctx.moveTo(a.v[0],a.v[1]); ctx.lineTo(a.a[0],a.a[1]); ctx.moveTo(a.v[0],a.v[1]); ctx.lineTo(a.b[0],a.b[1]); ctx.stroke();
        const ang=M.angle(mctx(a), a.a, a.v, a.b); Draw.boxLabel(ctx, canvas, a.v[0], a.v[1]-20, `θ ${ang.toFixed(2)}°`, labelScale, C.angle);
      }
    }
  }

  // Render the image + annotations onto an offscreen canvas, optionally downscaled so its
  // long side is <= maxDim. Shared by the PNG download and the submission-packet capture
  // so what the customer submits matches exactly what they saw.
  function _renderAnnotated(img, data, store, opts){
    opts = opts || {};
    const iw = img.naturalWidth || img.width, ih = img.naturalHeight || img.height;
    // Render at full resolution first (so the annotation line/label sizing — which is
    // canvas-size-relative — is identical to the PNG download), then downscale cleanly.
    const full = document.createElement('canvas'); full.width = iw; full.height = ih;
    const ctx = full.getContext('2d');
    ctx.drawImage(img, 0, 0);
    const fallback = (data && data.mm_per_px) || 0;
    if(opts.showGrid) D().drawGrid(ctx, iw, ih);
    if(opts.showMarkers) D().drawMarkers(ctx, full, data, opts.linePx != null ? opts.linePx : 3);
    _drawAnnotations(ctx, full, data, store, opts.units, opts.labelScale != null ? opts.labelScale : 1.4,
                     opts.linePx != null ? opts.linePx : 3, fallback, opts.allowHomography !== false);
    const maxDim = opts.maxDim || 0;
    if(maxDim && Math.max(iw, ih) > maxDim){
      const s = maxDim / Math.max(iw, ih);
      const small = document.createElement('canvas');
      small.width = Math.max(1, Math.round(iw * s)); small.height = Math.max(1, Math.round(ih * s));
      small.getContext('2d').drawImage(full, 0, 0, small.width, small.height);
      return small;
    }
    return full;
  }

  function exportPNG(img, data, store, showGrid, showMarkers, unitsKey, labelScale=1.4, linePx=3, allowHomography=true){
    const off = _renderAnnotated(img, data, store, { showGrid, showMarkers, units:unitsKey, labelScale, linePx, allowHomography });
    off.toBlob((blob)=>{ const a=document.createElement('a'); a.download='annotated.png'; a.href=URL.createObjectURL(blob); a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000); }, 'image/png');
  }

  // Annotated view as a downscaled JPEG data URL (for the submission packet).
  function renderAnnotatedDataURL(img, data, store, opts){
    const off = _renderAnnotated(img, data, store, opts || {});
    return off.toDataURL('image/jpeg', 0.82);
  }

  function exportJSON(payload){
    const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
    const a=document.createElement('a'); a.download='annotations.json'; a.href=URL.createObjectURL(blob); a.click(); setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
  }

  // Build the measurements CSV as a string (dimension table). Exposed so the submission
  // packet can carry the same table the "Download CSV" button produces, without a download.
  // fallbackScale (mm/px) is used for annotations that did not capture their own scale.
  function buildCSV(data, store, unitsKey, fallbackScale=0, allowHomography=true){
    const unit = U().get(unitsKey);
    const M = window.CalibMeasure;

    let csv = 'Type,Label,Value,Unit,Value_mm,Area_mm2,Notes\n';

    for(let i = 0; i < store.items.length; i++){
      const a = store.items[i];
      const label = `Item_${i+1}`;
      const mm_per_px = _scaleFor(a, data, fallbackScale);
      const mctx = M.context(data, mm_per_px, allowHomography);

      if(a.type === 'segment'){
        const mm = M.length(mctx, a.a[0],a.a[1], a.b[0],a.b[1]);
        const val = unit.fromMM(mm);
        csv += `Line,${label},${val.toFixed(3)},${unit.label},${mm.toFixed(3)},,\n`;

      }else if(a.type === 'circle'){
        // 3-point circles are tilt-corrected (rim points refit on the plane); 2-point
        // circles stay on the uniform scale. M.circle() handles both.
        const cm = M.circle(mctx, a);
        const r_mm = cm.radiusMM, d_mm = cm.diameterMM;
        const area_mm2 = cm.areaMM2;
        const circum_mm = 2 * Math.PI * r_mm;
        const d_val = unit.fromMM(d_mm);
        const note = cm.corrected ? '; tilt-corrected' : '';
        csv += `Circle,${label},${d_val.toFixed(3)},${unit.label},${d_mm.toFixed(3)},${area_mm2.toFixed(3)},Circumference: ${circum_mm.toFixed(2)}mm${note}\n`;

      }else if(a.type === 'rectangle'){
        const [x1,y1,x2,y2] = a.rect;
        const rm = M.rect(mctx, x1,y1,x2,y2);
        const w_mm = rm.w, h_mm = rm.h, area_mm2 = rm.area;
        const w_val = unit.fromMM(w_mm);
        const h_val = unit.fromMM(h_mm);
        // Keep the human "WxH" in the display Value column, but leave Value_mm strictly
        // numeric (the width); the height rides in Notes so a spreadsheet can parse the column.
        csv += `Rectangle,${label},${w_val.toFixed(3)}x${h_val.toFixed(3)},${unit.label},${w_mm.toFixed(3)},${area_mm2.toFixed(3)},H ${h_mm.toFixed(3)} mm\n`;

      }else if(a.type === 'polyline'){
        const pts = a.pts || [];
        const closed = !!a.closed && pts.length >= 3;
        const mm = M.polyline(mctx, pts, closed);
        const val = unit.fromMM(mm);
        if(closed){
          const area_mm2 = M.polygonArea(mctx, pts);
          csv += `Outline,${label},${val.toFixed(3)},${unit.label},${mm.toFixed(3)},${area_mm2.toFixed(3)},closed profile · ${pts.length} points\n`;
        }else{
          csv += `Polyline,${label},${val.toFixed(3)},${unit.label},${mm.toFixed(3)},,${pts.length} points\n`;
        }

      }else if(a.type === 'angle'){
        const ang = M.angle(mctx, a.a, a.v, a.b);
        csv += `Angle,${label},${ang.toFixed(2)},degrees,,,\n`;

      }else if(a.type === 'note'){
        const text = _csvSafe(a.text);  // escape quotes + block formula injection
        csv += `Note,${label},,,,,"${text}"\n`;
      }
    }
    return csv;
  }

  // Export measurements to CSV for spreadsheet analysis (downloads the file).
  function exportCSV(data, store, unitsKey, fallbackScale=0, allowHomography=true){
    const csv = buildCSV(data, store, unitsKey, fallbackScale, allowHomography);
    const blob = new Blob([csv], {type:'text/csv'});
    const link = document.createElement('a');
    link.download = 'measurements.csv';
    link.href = URL.createObjectURL(blob);
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  }

  // Export to DXF for CAD software (requires the /api/export/dxf backend endpoint).
  // opts: { image_height (px, for Y-flip), mm_per_px (default/fallback scale),
  //         allowHomography (default true) }
  // Build the DXF request body. Exposed for testing. When a homography is active
  // (and allowed), every point is projected into plane mm (CAD Y-up) so the DXF is
  // perspective-rectified and consistent with the on-screen/PNG/CSV measurements;
  // the backend then writes the coordinates verbatim (mm_per_px=1, no Y-flip).
  // Otherwise geometry is sent in pixels with the uniform scale as before.
  function _buildDXFRequest(data, store, opts){
    opts = opts || {};
    const fallback = opts.mm_per_px || (data && data.mm_per_px) || 1.0;
    const Circles = window.CalibCircles;
    const M = window.CalibMeasure;
    const ctx = M.context(data, fallback, opts.allowHomography !== false);
    const plane = ctx.corrected;
    const geometry = [];
    // Image point -> DXF coord in plane mm, CAD Y-up (negate the image-down Y).
    const P = (x, y) => { const m = M.project(ctx, x, y); return [m[0], -m[1]]; };

    const fmt = v => String(Math.round(v*1000)/1000);
    // Emit a DIMTEXT value label at an image-space anchor (projected in plane mode).
    // Height is authored so it lands ~3 mm tall after the backend applies mm_per_px.
    const emitText = (ix, iy, text, s, layer) => {
      if(plane){ const m = M.project(ctx, ix, iy); geometry.push({ type:'text', x:m[0], y:-m[1], text, height:3, mm_per_px:1, layer: layer||'DIMTEXT' }); }
      else { const sc = s || fallback || 1; geometry.push({ type:'text', x:ix, y:iy, text, height: 3/sc, mm_per_px:sc, layer: layer||'DIMTEXT' }); }
    };

    for(const a of store.items){
      const s = _scaleFor(a, data, fallback) || fallback;
      if(a.type === 'segment'){
        if(plane){ const A=P(a.a[0],a.a[1]), B=P(a.b[0],a.b[1]); geometry.push({ type:'line', x1:A[0], y1:A[1], x2:B[0], y2:B[1], mm_per_px:1 }); }
        else geometry.push({ type:'line', x1:a.a[0], y1:a.a[1], x2:a.b[0], y2:a.b[1], mm_per_px:s });
        emitText((a.a[0]+a.b[0])/2, (a.a[1]+a.b[1])/2, `${fmt(M.length(ctx, a.a[0],a.a[1], a.b[0],a.b[1]))} mm`, s);
      }else if(a.type === 'circle'){
        const cm = M.circle(ctx, a);
        if(cm.corrected && cm.centerMM){
          // Tilt-corrected hole: centre and radius fitted on the plane (CAD Y-up), so the
          // DXF circle is a true circle at true size — what the shop actually machines.
          geometry.push({ type:'circle', center_x:cm.centerMM[0], center_y:-cm.centerMM[1], radius_px:cm.radiusMM, mm_per_px:1 });
        }else if(plane){
          // 2-point circle under a homography: centre on the plane, radius on the uniform
          // scale (a 2-point circle can't recover the ellipse) — matches the shown number.
          const c=P(a.center[0], a.center[1]);
          geometry.push({ type:'circle', center_x:c[0], center_y:c[1], radius_px:a.radius*s, mm_per_px:1 });
        }else if(Circles){ const c = Circles.circleToJSON(a, data, 'mm'); c.mm_per_px = s; geometry.push(c); }
        emitText(a.center[0], a.center[1], `⌀ ${fmt(cm.diameterMM)} mm`, s);
      }else if(a.type === 'rectangle'){
        const [x1, y1, x2, y2] = a.rect;
        // Closed profile so CAD can extrude it directly.
        if(plane){ const q=[[x1,y1],[x2,y1],[x2,y2],[x1,y2]].map(p=>P(p[0],p[1])); geometry.push({ type:'polyline', points:q, closed:true, mm_per_px:1 }); }
        else geometry.push({ type:'rectangle', x1, y1, x2, y2, mm_per_px:s });
        const rm=M.rect(ctx, x1,y1,x2,y2);
        emitText((x1+x2)/2, y1, `${fmt(rm.w)}×${fmt(rm.h)} mm`, s);
      }else if(a.type === 'polyline'){
        const pts = a.pts || [];
        const closed = !!a.closed && pts.length >= 3;   // a traced part outline → closed, extrudable profile
        if(plane){ geometry.push({ type:'polyline', points:pts.map(p=>P(p[0],p[1])), closed:closed, mm_per_px:1 }); }
        else geometry.push({ type:'polyline', points:pts, closed:closed, mm_per_px:s });
        if(pts.length){ const mid=pts[Math.floor(pts.length/2)]; emitText(mid[0], mid[1], `${fmt(M.polyline(ctx, pts, closed))} mm`, s); }
      }else if(a.type === 'angle'){
        // Two legs as lines on the ANGLES layer + the angle value as text at the vertex.
        const leg=(p,q)=>{ if(plane){ const A=P(p[0],p[1]), B=P(q[0],q[1]); geometry.push({ type:'line', x1:A[0],y1:A[1],x2:B[0],y2:B[1], mm_per_px:1, layer:'ANGLES' }); }
                           else geometry.push({ type:'line', x1:p[0],y1:p[1],x2:q[0],y2:q[1], mm_per_px:s, layer:'ANGLES' }); };
        leg(a.v, a.a); leg(a.v, a.b);
        emitText(a.v[0], a.v[1], `${Math.round(M.angle(ctx, a.a, a.v, a.b)*100)/100}°`, s, 'ANGLES');
      }else if(a.type === 'note'){
        emitText(a.p[0], a.p[1], String(a.text||'Note'), s, 'NOTES');
      }
    }
    return { geometry, mm_per_px: plane ? 1 : fallback, image_height: plane ? 0 : (opts.image_height || 0), plane };
  }

  async function exportDXF(data, store, opts){
    const req = _buildDXFRequest(data, store, opts);
    const geometry = req.geometry;

    if(!geometry.length){ alert('Nothing to export to DXF — draw a line, rectangle, circle or polyline first.'); return false; }

    try {
      const response = await fetch(_apiUrl('api/export/dxf'), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ geometry, mm_per_px: req.mm_per_px, image_height: req.image_height })
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
      exportJSON({geometry, mm_per_px: req.mm_per_px, image_height: req.image_height, format: 'dxf_compatible'});
      return false;
    }
  }

  // Serialize a _buildDXFRequest() geometry list into a DXF string, entirely client-side —
  // no backend. Follows the Python export_to_dxf() conventions so a CamScan DXF carries
  // equivalent geometry whichever path produced it: units = mm, and Y is flipped by
  // image_height when coordinates are still in image pixels (plane-mode geometry already
  // arrives in CAD mm, Y-up, with mm_per_px = 1 and no flip). POSITIONS use ONE uniform scale
  // (the request default) so multi-marker geometry stays spatially coherent; per-item
  // mm_per_px is used only for a shape's intrinsic SIZE (radius, text height). AC1015 (R2000)
  // so LWPOLYLINE profiles import as single closed, extrudable entities. (Bytes are not
  // identical to the Python/ezdxf output — that writer emits a LAYER table — but geometry matches.)
  function dxfFromGeometry(geometry, mm_per_px, image_height){
    const items = Array.isArray(geometry) ? geometry : [];
    const base = mm_per_px || 1.0;
    const H = image_height || 0;
    const out = [];
    const g = (code, val) => { out.push(String(code)); out.push(String(val)); };
    const num = v => { const r = Math.round(v * 1e6) / 1e6; return Object.is(r, -0) ? '0.0' : String(r); };
    const flipY = y => (H ? (H - y) : y);
    const layerOf = (it, def) => it.layer || def;

    g(0,'SECTION'); g(2,'HEADER'); g(9,'$ACADVER'); g(1,'AC1015'); g(9,'$INSUNITS'); g(70,4); g(0,'ENDSEC');
    g(0,'SECTION'); g(2,'ENTITIES');

    for(const it of items){
      let s = parseFloat(it && it.mm_per_px);      // per-item scale — intrinsic SIZE only
      if(!isFinite(s) || s === 0) s = base;
      const pos = base;                            // uniform scale for POSITIONS (spatial coherence)
      try{
        const t = it && it.type;
        if(t === 'line'){
          g(0,'LINE'); g(100,'AcDbEntity'); g(8, layerOf(it,'LINES')); g(100,'AcDbLine');
          g(10, num(it.x1*pos)); g(20, num(flipY(it.y1)*pos)); g(30,'0.0');
          g(11, num(it.x2*pos)); g(21, num(flipY(it.y2)*pos)); g(31,'0.0');
        }else if(t === 'circle'){
          g(0,'CIRCLE'); g(100,'AcDbEntity'); g(8, layerOf(it,'CIRCLES')); g(100,'AcDbCircle');
          g(10, num(it.center_x*pos)); g(20, num(flipY(it.center_y)*pos)); g(30,'0.0'); g(40, num(it.radius_px*s));
        }else if(t === 'rectangle'){
          const pts = [[it.x1,it.y1],[it.x2,it.y1],[it.x2,it.y2],[it.x1,it.y2]];
          _dxfPoly(g, num, pts.map(p=>[p[0]*pos, flipY(p[1])*pos]), true, layerOf(it,'RECTANGLES'));
        }else if(t === 'polyline'){
          const pts = (it.points||[]).map(p=>[p[0]*pos, flipY(p[1])*pos]);
          if(pts.length) _dxfPoly(g, num, pts, !!it.closed, layerOf(it,'POLYLINES'));
        }else if(t === 'text'){
          let h = parseFloat(it.height); if(!isFinite(h) || h<=0) h = 3; h *= s;
          g(0,'TEXT'); g(100,'AcDbEntity'); g(8, layerOf(it,'DIMTEXT')); g(100,'AcDbText');
          g(10, num(it.x*pos)); g(20, num(flipY(it.y)*pos)); g(30,'0.0'); g(40, num(h));
          g(1, String(it.text==null?'':it.text).replace(/[\r\n]+/g,' ')); g(100,'AcDbText');
        }
      }catch(e){ /* skip one malformed item, keep the rest — matches the Python writer */ }
    }

    g(0,'ENDSEC'); g(0,'EOF');
    // DXF is CRLF-delimited pairs of (group code, value).
    return out.join('\r\n') + '\r\n';
  }
  function _dxfPoly(g, num, pts, closed, layer){
    g(0,'LWPOLYLINE'); g(100,'AcDbEntity'); g(8, layer); g(100,'AcDbPolyline');
    g(90, pts.length); g(70, closed ? 1 : 0);
    for(const p of pts){ g(10, num(p[0])); g(20, num(p[1])); }
  }

  // Client-side SVG export in real millimetres. Reuses the same perspective-aware
  // projection as the DXF path (plane-mm when a homography is active), needs no backend,
  // and imports directly into Illustrator/Inkscape/Fusion and most laser cutters.
  // Build the SVG string. Exposed for testing.
  function _buildSVG(data, store, opts){
    opts = opts || {};
    const fallback = opts.mm_per_px || (data && data.mm_per_px) || 1.0;
    const M = window.CalibMeasure;
    const ctx = M.context(data, fallback, opts.allowHomography !== false);
    const plane = ctx.corrected;
    const C = (window.CalibDraw && window.CalibDraw.colors) || {};
    // Point -> mm, SVG orientation (Y down). Plane mode uses the homography (which is
    // already X-right/Y-down); pixel mode is px*scale. No CAD Y-flip here.
    const P = (x, y, s) => { if(plane){ const m = M.project(ctx, x, y); return [m[0], m[1]]; } return [x*s, y*s]; };
    const els = []; let minX=1e18, minY=1e18, maxX=-1e18, maxY=-1e18;
    const bump = (x,y) => { if(x<minX)minX=x; if(y<minY)minY=y; if(x>maxX)maxX=x; if(y>maxY)maxY=y; };
    const n = v => (Math.round(v*1000)/1000);

    for(const a of store.items){
      const s = _scaleFor(a, data, fallback) || fallback;
      if(a.type === 'segment'){
        const A=P(a.a[0],a.a[1],s), B=P(a.b[0],a.b[1],s); bump(A[0],A[1]); bump(B[0],B[1]);
        els.push(`<line x1="${n(A[0])}" y1="${n(A[1])}" x2="${n(B[0])}" y2="${n(B[1])}" stroke="${C.segment||'#009e73'}" stroke-width="0.3"/>`);
      }else if(a.type === 'rectangle'){
        const [x1,y1,x2,y2]=a.rect; const q=[[x1,y1],[x2,y1],[x2,y2],[x1,y2]].map(p=>P(p[0],p[1],s)); q.forEach(p=>bump(p[0],p[1]));
        els.push(`<polygon points="${q.map(p=>n(p[0])+','+n(p[1])).join(' ')}" fill="none" stroke="${C.rectangle||'#0072b2'}" stroke-width="0.3"/>`);
      }else if(a.type === 'polyline'){
        const q=(a.pts||[]).map(p=>P(p[0],p[1],s)); if(!q.length) continue; q.forEach(p=>bump(p[0],p[1]));
        const tag = (a.closed && q.length >= 3) ? 'polygon' : 'polyline';   // closed outline → <polygon>
        els.push(`<${tag} points="${q.map(p=>n(p[0])+','+n(p[1])).join(' ')}" fill="none" stroke="${C.polyline||'#e69f00'}" stroke-width="0.3"/>`);
      }else if(a.type === 'angle'){
        const V=P(a.v[0],a.v[1],s), A=P(a.a[0],a.a[1],s), B=P(a.b[0],a.b[1],s); [V,A,B].forEach(p=>bump(p[0],p[1]));
        els.push(`<polyline points="${n(A[0])+','+n(A[1])} ${n(V[0])+','+n(V[1])} ${n(B[0])+','+n(B[1])}" fill="none" stroke="${C.angle||'#cc79a7'}" stroke-width="0.3"/>`);
      }else if(a.type === 'circle'){
        const cm = M.circle(ctx, a);
        let c, r;
        if(cm.corrected && cm.centerMM){ c=[cm.centerMM[0], cm.centerMM[1]]; r=cm.radiusMM; }  // true plane circle
        else { c=P(a.center[0],a.center[1],s); r=a.radius*s; }
        bump(c[0]-r,c[1]-r); bump(c[0]+r,c[1]+r);
        els.push(`<circle cx="${n(c[0])}" cy="${n(c[1])}" r="${n(r)}" fill="none" stroke="${C.circle||'#56b4e9'}" stroke-width="0.3"/>`);
      }
    }
    if(!els.length) return null;
    const pad=5; minX-=pad; minY-=pad; maxX+=pad; maxY+=pad;
    const w=n(maxX-minX), h=n(maxY-minY);
    const svg = `<?xml version="1.0" encoding="UTF-8"?>\n`+
      `<svg xmlns="http://www.w3.org/2000/svg" width="${w}mm" height="${h}mm" viewBox="${n(minX)} ${n(minY)} ${w} ${h}">\n`+
      els.join('\n') + `\n</svg>\n`;
    return svg;
  }

  function exportSVG(data, store, opts){
    const svg = _buildSVG(data, store, opts);
    if(!svg){ alert('Nothing to export to SVG — draw a line, rectangle, circle or polyline first.'); return false; }
    const blob = new Blob([svg], {type:'image/svg+xml'});
    const link = document.createElement('a');
    link.download = 'geometry.svg';
    link.href = URL.createObjectURL(blob);
    link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    return true;
  }

  return { exportPNG, renderAnnotatedDataURL, exportJSON, exportCSV, buildCSV, exportDXF, exportSVG,
           dxfFromGeometry, _buildDXFRequest, _buildSVG };
})();
