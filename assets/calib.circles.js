// Circle detection and measurement tools for CamScan
window.CalibCircles = (function(){
  const Units = window.CalibUnits;
  const Draw  = window.CalibDraw;
  const Geom  = window.CalibGeom;

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

  // Three-point circle (user clicks 3 points on circumference)
  function fitCircleFromPoints(points){
    if(points.length < 3) return null;

    // Use first 3 points for circle fit
    const [p1, p2, p3] = points.slice(0, 3);

    // Calculate circle center using perpendicular bisectors
    const mid12 = [(p1[0] + p2[0])/2, (p1[1] + p2[1])/2];
    const mid23 = [(p2[0] + p3[0])/2, (p2[1] + p3[1])/2];

    const slope12 = (p2[1] - p1[1]) / (p2[0] - p1[0]);
    const slope23 = (p3[1] - p2[1]) / (p3[0] - p2[0]);

    // Handle vertical/horizontal cases
    if(Math.abs(slope12) < 0.001 || Math.abs(slope23) < 0.001 ||
       Math.abs(slope12 - slope23) < 0.001){
      // Fallback to simple center calculation
      const cx = (p1[0] + p2[0] + p3[0]) / 3;
      const cy = (p1[1] + p2[1] + p3[1]) / 3;
      const r1 = Math.hypot(cx - p1[0], cy - p1[1]);
      const r2 = Math.hypot(cx - p2[0], cy - p2[1]);
      const r3 = Math.hypot(cx - p3[0], cy - p3[1]);
      const radius = (r1 + r2 + r3) / 3;
      return {center: [cx, cy], radius: radius};
    }

    // Perpendicular slopes
    const perpSlope12 = -1 / slope12;
    const perpSlope23 = -1 / slope23;

    // y = mx + b for each perpendicular bisector
    const b1 = mid12[1] - perpSlope12 * mid12[0];
    const b2 = mid23[1] - perpSlope23 * mid23[0];

    // Intersection of perpendicular bisectors = circle center
    const cx = (b2 - b1) / (perpSlope12 - perpSlope23);
    const cy = perpSlope12 * cx + b1;

    // Radius = distance from center to any point
    const radius = Math.hypot(cx - p1[0], cy - p1[1]);

    return {center: [cx, cy], radius: radius};
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
    const unit = Units.get(unitsKey);
    const mm_per_px = circle.mm_per_px || data?.mm_per_px || 0;

    const [cx, cy] = circle.center;
    const r = circle.radius;

    // Calculate measurements
    const diameter_mm = 2 * r * mm_per_px;
    const radius_mm = r * mm_per_px;
    const circumference_mm = 2 * Math.PI * r * mm_per_px;
    const area_mm2 = Math.PI * r * r * mm_per_px * mm_per_px;

    const diameter_display = unit.fromMM(diameter_mm);
    const area_display = area_mm2; // Always show area in mm²

    // Draw circle
    const lineWidth = isSelected ? Draw.px(canvas, 4) : Draw.px(canvas, 3);
    const color = isSelected ? 'rgba(255, 170, 0, 1)' : 'cyan';

    ctx.strokeStyle = color;
    ctx.lineWidth = lineWidth;
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();

    // Draw center point
    const dotR = Draw.px(canvas, 6);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(cx, cy, dotR, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#000';
    ctx.lineWidth = Draw.px(canvas, 2);
    ctx.stroke();

    // Draw diameter line
    ctx.strokeStyle = color;
    ctx.lineWidth = Draw.px(canvas, 2);
    ctx.setLineDash([5, 5]);
    ctx.beginPath();
    ctx.moveTo(cx - r, cy);
    ctx.lineTo(cx + r, cy);
    ctx.stroke();
    ctx.setLineDash([]);

    // Draw labels
    const labelScale = 1.2;

    // Diameter label (above)
    Draw.boxLabel(
      ctx, canvas, cx, cy - r - 15,
      `⌀ ${diameter_display.toFixed(3)} ${unit.label}`,
      labelScale
    );

    // Area label (below)
    Draw.boxLabel(
      ctx, canvas, cx, cy + r + 15,
      `A ${area_display.toFixed(1)} mm²`,
      labelScale
    );

    // Radius indicators (left and right)
    const fontSize = Math.round(14 * labelScale);
    ctx.fillStyle = color;
    ctx.font = Draw.font(fontSize);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    // R label on radius line
    ctx.fillText(`R ${radius_mm.toFixed(2)}`, cx - r/2, cy - 10);
  }

  // Export circle to JSON
  function circleToJSON(circle, data, unitsKey){
    const unit = Units.get(unitsKey);
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
