// Geometry + math helpers
window.CalibGeom = (function(){
  function distPtLine(px,py, ax,ay, bx,by){
    const vx=bx-ax, vy=by-ay, wx=px-ax, wy=py-ay;
    const c1 = vx*wx + vy*wy;
    if(c1<=0) return Math.hypot(px-ax, py-ay);
    const c2 = vx*vx + vy*vy;
    if(c2<=c1) return Math.hypot(px-bx, py-by);
    const t = c1/c2;
    const hx = ax + t*vx, hy = ay + t*vy;
    return Math.hypot(px-hx, py-hy);
  }
  function angleDeg(ax,ay, bx,by){ return Math.atan2(by-ay,bx-ax)*180/Math.PI; }
  function angleABC(a,v,b){
    const ang = Math.abs((Math.atan2(a[1]-v[1], a[0]-v[0]) - Math.atan2(b[1]-v[1], b[0]-v[0]))*180/Math.PI);
    return (ang>180)? 360-ang : ang;
  }
  return { distPtLine, angleDeg, angleABC };
})();
