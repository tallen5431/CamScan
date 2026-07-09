// Circle detection and measurement tools for CamScan
window.CalibCircles = (function(){
  // Resolve dependencies lazily so module load order never matters.
  const U = () => window.CalibUnits;
  const D = () => window.CalibDraw;

  // Circle annotation object
  function createCircle(center, radius, mm_per_px){
    return {
      type: 'circle',
      center: center,         // [x, y]
      radius: radius,         // pixels
      mm_per_px: mm_per_px || 0,
      id: Date.now() + Math.random()
    };
  }

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

  // Detect circles automatically in the image
  async function detectCirclesAuto(imageData, options = {}){
    // This would call the Python backend via fetch
    // For now, return empty array (backend integration needed)
    console.log('[CircleDetect] Auto-detection requires backend integration');
    return [];
  }

  // Draw circle annotation
  function drawCircle(ctx, canvas, circle, data, unitsKey, isSelected = false){
    const unit = U().get(unitsKey);
    const mm_per_px = circle.mm_per_px || data?.mm_per_px || 0;

    const [cx, cy] = circle.center;
    const r = circle.radius;

    // Calculate measurements
    const diameter_mm = 2 * r * mm_per_px;
    const radius_mm = r * mm_per_px;
    const circumference_mm = 2 * Math.PI * r * mm_per_px;
    const area_mm2 = Math.PI * r * r * mm_per_px * mm_per_px;

    const diameter_display = unit.fromMM(diameter_mm);
    const area_display = unit.areaFromMM2(area_mm2);

    // Draw circle
    const lineWidth = isSelected ? D().px(canvas, 4) : D().px(canvas, 3);
    const color = isSelected ? 'rgba(255, 170, 0, 1)' : 'cyan';

    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();

    // Draw center point
    const dotR = D().px(canvas, 6);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(cx, cy, dotR, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#000';
    ctx.lineWidth = D().px(canvas, 2);
    ctx.stroke();

    // Draw diameter line
    ctx.strokeStyle = color;
    ctx.lineWidth = D().px(canvas, 2);
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(cx - r, cy);
    ctx.lineTo(cx + r, cy);
    ctx.stroke();
    ctx.setLineDash([]);

    // Draw labels
    const labelScale = 1.2;

    // Diameter label (above)
    D().boxLabel(
      ctx, canvas, cx, cy - r - 15,
      `⌀ ${diameter_display.toFixed(3)} ${unit.label}`,
      labelScale
    );

    // Area label (below)
    D().boxLabel(
      ctx, canvas, cx, cy + r + 15,
      `A ${area_display.toFixed(3)} ${unit.areaLabel}`,
      labelScale
    );

    // Radius indicators (left and right)
    const fontSize = Math.round(14 * labelScale);
    ctx.fillStyle = color;
    ctx.font = D().font(fontSize);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // R label on radius line
    ctx.fillText(`R ${radius_mm.toFixed(2)}`, cx - r/2, cy - 10);
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

  // Check if point is on circle perimeter
  function isPointOnCircle(px, py, circle, tolerance = 10){
    const [cx, cy] = circle.center;
    const dist = Math.hypot(px - cx, py - cy);
    return Math.abs(dist - circle.radius) < tolerance;
  }

  // Check if point is inside circle
  function isPointInCircle(px, py, circle){
    const [cx, cy] = circle.center;
    const dist = Math.hypot(px - cx, py - cy);
    return dist <= circle.radius;
  }

  return {
    createCircle,
    fitCircleFromPoints,
    detectCirclesAuto,
    drawCircle,
    circleToJSON,
    isPointOnCircle,
    isPointInCircle
  };
})();
