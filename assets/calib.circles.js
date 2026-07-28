// Circle detection and measurement tools for CamScan
window.CalibCircles = (function(){
  // Resolve dependencies lazily so module load order never matters.
  const U = () => window.CalibUnits;
  const D = () => window.CalibDraw;

  // Three-point circle (user clicks 3 points on the circumference).
  // Uses the circumcircle determinant formula — orientation-independent and robust for
  // horizontal/vertical chords (the old slope-based version mis-handled those). Returns
  // null only when the three points are (nearly) collinear.
  function fitCircleFromPoints(points){
    if(!points || points.length < 3) return null;
    const [p1, p2, p3] = points.slice(0, 3);
    const [ax, ay] = p1, [bx, by] = p2, [cx3, cy3] = p3;

    const d = 2 * (ax * (by - cy3) + bx * (cy3 - ay) + cx3 * (ay - by));
    if(Math.abs(d) < 1e-9) return null; // collinear — no unique circle

    const a2 = ax*ax + ay*ay, b2 = bx*bx + by*by, c2 = cx3*cx3 + cy3*cy3;
    const ux = (a2 * (by - cy3) + b2 * (cy3 - ay) + c2 * (ay - by)) / d;
    const uy = (a2 * (cx3 - bx) + b2 * (ax - cx3) + c2 * (bx - ax)) / d;
    const radius = Math.hypot(ux - ax, uy - ay);
    return { center: [ux, uy], radius };
  }

  // Draw circle annotation. Single implementation shared by the live canvas and
  // the PNG export so the two never drift. `opts`:
  //   { selected, labelScale, linePx, fallbackScale }
  // (a boolean is still accepted for backward compat and treated as `selected`).
  function drawCircle(ctx, canvas, circle, data, unitsKey, opts){
    if(typeof opts === 'boolean') opts = { selected: opts };
    opts = opts || {};
    const unit = U().get(unitsKey);
    const mm_per_px = circle.mm_per_px || opts.fallbackScale || data?.mm_per_px || 0;

    const [cx, cy] = circle.center;
    const r = circle.radius;

    // Perspective-aware diameter: a 3-point circle projects its rim points onto the plane
    // and refits, so a tilted hole reads true; a 2-point circle stays uniform. The circle
    // is still DRAWN as the image-space shape (below) — only the numbers are rectified.
    const M = window.CalibMeasure;
    const mctx = M ? M.context(data, circle.mm_per_px || opts.fallbackScale, opts.allowHomography !== false) : null;
    const cm = (M && mctx) ? M.circle(mctx, circle) : null;

    // When there's no scale yet, label in raw pixels rather than a fake "⌀ 0.000 mm"
    // that reads like a real zero-diameter result.
    const calibrated = cm ? (cm.diameterMM > 0) : (mm_per_px > 0);
    const diamMM = cm ? cm.diameterMM : (2 * r * mm_per_px);
    const areaMM2 = cm ? cm.areaMM2 : (Math.PI * r * r * mm_per_px * mm_per_px);
    const diamLabel = calibrated
      ? `⌀ ${unit.fromMM(diamMM).toFixed(3)} ${unit.label}`
      : `⌀ ${Math.round(2 * r)} px`;
    const areaLabel = calibrated
      ? `A ${unit.areaFromMM2(areaMM2).toFixed(3)} ${unit.areaLabel}`
      : `A ${Math.round(Math.PI * r * r)} px²`;

    const selected = !!opts.selected;
    const labelScale = opts.labelScale || 1.35;
    const linePx = (opts.linePx != null) ? opts.linePx : 3;
    const C = D().colors || {};
    const color = selected ? (C.selected || '#fff') : (C.circle || 'cyan');

    // Circle outline + solid diameter line (honors the Line-Thickness slider). A
    // selected circle draws bolder so the selection reads clearly.
    ctx.strokeStyle = color;
    ctx.lineWidth = D().px(canvas, selected ? linePx*1.7 : linePx);
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx - r, cy); ctx.lineTo(cx + r, cy); ctx.stroke();

    // Center point.
    const dotR = D().px(canvas, 8);
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.arc(cx, cy, dotR, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = '#000'; ctx.lineWidth = D().px(canvas, 2); ctx.stroke();

    // Labels stacked above the circle (honors the Text-Size slider). Offset the
    // second label by a full box height so they never overlap.
    const boxH = Math.round(22 * labelScale) + 20 * labelScale;
    D().boxLabel(ctx, canvas, cx, cy - r - 15, diamLabel, labelScale, C.circle);
    D().boxLabel(ctx, canvas, cx, cy - r - 15 - boxH - 8, areaLabel, labelScale, C.circle);
  }

  // Export circle to JSON
  function circleToJSON(circle, data, unitsKey){
    const unit = U().get(unitsKey);
    const mm_per_px = circle.mm_per_px || data?.mm_per_px || 0;

    const [cx, cy] = circle.center;
    const r = circle.radius;

    return {
      type: 'circle',
      center_x: cx,
      center_y: cy,
      radius_px: r,
      radius_mm: r * mm_per_px,
      diameter_px: 2 * r,
      diameter_mm: 2 * r * mm_per_px,
      circumference_px: 2 * Math.PI * r,
      circumference_mm: 2 * Math.PI * r * mm_per_px,
      area_px2: Math.PI * r * r,
      area_mm2: Math.PI * r * r * mm_per_px * mm_per_px,
      unit: unit.label
    };
  }

  return {
    fitCircleFromPoints,
    drawCircle,
    circleToJSON
  };
})();
