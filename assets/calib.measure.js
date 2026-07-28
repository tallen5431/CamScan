// Perspective-aware measurement helpers.
//
// When the calibration carries a homography (image px -> unit square, built from
// the marker's true corners), linear measurements are rectified onto the marker's
// plane, so tilted/angled photos measure TRUE millimetres instead of foreshortened
// values. Without a homography (or when a manual scale is set) it falls back to the
// uniform mm/px scale, so behaviour is unchanged for straight-on shots.
//
// NOTE: this only helps for geometry COPLANAR with the marker. A 3-point circle is
// tilt-corrected by projecting its rim points onto the plane and refitting (see circle());
// a 2-point circle has no rim points, so it stays on the uniform scale (approximate on tilt).
window.CalibMeasure = (function(){
  // Build a measurement context.
  //   cal             : calibration JSON (.homography, .marker_size_mm, .mm_per_px)
  //   mmPerPxFallback : per-annotation frozen scale, used when no homography
  //   allowHomography : pass false to force the uniform scale (e.g. manual scale set)
  function context(cal, mmPerPxFallback, allowHomography){
    const H = (allowHomography !== false && cal && cal.homography) ? cal.homography : null;
    const edgeMM = (cal && cal.marker_size_mm) || 0;
    const usable = !!(H && edgeMM > 0);
    return {
      H: usable ? H : null,
      edgeMM: edgeMM,
      mmPerPx: mmPerPxFallback || (cal && cal.mm_per_px) || 0,
      corrected: usable
    };
  }

  // Project an image point through the homography into plane mm coordinates
  // (X right, Y down — the marker's frame, following image orientation).
  function _mm(ctx, x, y){
    const H = ctx.H;
    const w = H[2][0]*x + H[2][1]*y + H[2][2];
    return [ (H[0][0]*x + H[0][1]*y + H[0][2]) / w * ctx.edgeMM,
             (H[1][0]*x + H[1][1]*y + H[1][2]) / w * ctx.edgeMM ];
  }

  // Public projection to plane mm; returns null when there's no usable homography
  // so callers can fall back to the uniform scale.
  function project(ctx, x, y){ return ctx.H ? _mm(ctx, x, y) : null; }

  // Length (mm) of the image-space segment (ax,ay)-(bx,by).
  function length(ctx, ax, ay, bx, by){
    if(ctx.H){ const A=_mm(ctx,ax,ay), B=_mm(ctx,bx,by); return Math.hypot(B[0]-A[0], B[1]-A[1]); }
    return Math.hypot(bx-ax, by-ay) * ctx.mmPerPx;
  }

  // Total length (mm) of a polyline given as [[x,y],...] image points.
  function polyline(ctx, pts){
    let sum = 0;
    for(let i=1;i<pts.length;i++) sum += length(ctx, pts[i-1][0], pts[i-1][1], pts[i][0], pts[i][1]);
    return sum;
  }

  // A rectangle drawn axis-aligned in image space maps to a quadrilateral on the
  // plane under perspective; width/height are its top/left edge lengths and area is
  // the true quad area (shoelace). Returns { w, h, area } in mm / mm².
  function rect(ctx, x1, y1, x2, y2){
    if(ctx.H){
      const c = [[x1,y1],[x2,y1],[x2,y2],[x1,y2]].map(p => _mm(ctx, p[0], p[1]));
      const w = Math.hypot(c[1][0]-c[0][0], c[1][1]-c[0][1]);
      const h = Math.hypot(c[3][0]-c[0][0], c[3][1]-c[0][1]);
      let area = 0;
      for(let i=0;i<4;i++){ const a=c[i], b=c[(i+1)%4]; area += a[0]*b[1] - b[0]*a[1]; }
      return { w: w, h: h, area: Math.abs(area)/2 };
    }
    const w = (x2-x1)*ctx.mmPerPx, h = (y2-y1)*ctx.mmPerPx;
    return { w: w, h: h, area: w*h };
  }

  // Circle / hole measurement. When a homography is active AND the circle carries its
  // three rim points (the 3-point tool keeps them), we project those points onto the
  // plane — where the hole is a true circle again — and fit the circle THERE, so a hole
  // photographed at an angle (an ellipse in the image) reads its real diameter. A 2-point
  // circle has no rim points, so it falls back to the uniform scale (approximate on tilt).
  // Returns { radiusMM, diameterMM, areaMM2, centerMM, corrected }.
  function circle(ctx, c){
    if(ctx && ctx.H && c && Array.isArray(c.pts) && c.pts.length>=3){
      const P = c.pts.slice(0,3).map(p => _mm(ctx, p[0], p[1]));  // rim points -> plane mm
      const Circ = window.CalibCircles;
      const fit = Circ && Circ.fitCircleFromPoints(P);
      if(fit && fit.radius>0){
        const r = fit.radius;
        return { radiusMM:r, diameterMM:2*r, areaMM2:Math.PI*r*r, centerMM:fit.center, corrected:true };
      }
    }
    const s = (c && c.mm_per_px) || (ctx && ctx.mmPerPx) || 0;
    const r = ((c && c.radius) || 0) * s;
    return { radiusMM:r, diameterMM:2*r, areaMM2:Math.PI*r*r, centerMM:null, corrected:false };
  }

  // True planar angle (degrees) at vertex v, with a,v,b as image points [x,y].
  function angle(ctx, a, v, b){
    let A=a, V=v, B=b;
    if(ctx.H){ A=_mm(ctx,a[0],a[1]); V=_mm(ctx,v[0],v[1]); B=_mm(ctx,b[0],b[1]); }
    const d = Math.abs((Math.atan2(A[1]-V[1], A[0]-V[0]) - Math.atan2(B[1]-V[1], B[0]-V[0])) * 180/Math.PI);
    return d > 180 ? 360 - d : d;
  }

  return { context, project, length, polyline, rect, angle, circle };
})();
