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
  // (angle helpers removed — angle measurement lives in CalibMeasure.angle, the only path used.)

  // Solve a dense n×n linear system A·x = b by Gaussian elimination with partial
  // pivoting. Returns the solution vector, or null if the matrix is singular (which
  // is how a degenerate/collinear quad falls out gracefully). n is small (8), so this
  // is plenty fast and avoids pulling in a matrix library.
  function _solveLinear(A, b){
    const n = A.length;
    const M = A.map((row, i) => row.slice().concat(b[i])); // augmented [A|b]
    for(let col=0; col<n; col++){
      let piv=col;
      for(let r=col+1; r<n; r++) if(Math.abs(M[r][col]) > Math.abs(M[piv][col])) piv=r;
      if(Math.abs(M[piv][col]) < 1e-12) return null;       // singular → no unique solution
      if(piv!==col){ const t=M[piv]; M[piv]=M[col]; M[col]=t; }
      const d=M[col][col];
      for(let k=col; k<=n; k++) M[col][k]/=d;
      for(let r=0; r<n; r++){
        if(r===col) continue;
        const f=M[r][col]; if(f===0) continue;
        for(let k=col; k<=n; k++) M[r][k]-=f*M[col][k];
      }
    }
    return M.map(row => row[n]);
  }

  // Order 4 points into perimeter (convex) order by angle about their centroid, so the
  // caller need not make the user tap corners in a particular sequence. Returns a new
  // array of 4 [x,y], or null.
  function orderQuad(pts){
    if(!pts || pts.length<4) return null;
    const p = pts.slice(0,4).map(q => [q[0], q[1]]);
    const cx=(p[0][0]+p[1][0]+p[2][0]+p[3][0])/4, cy=(p[0][1]+p[1][1]+p[2][1]+p[3][1])/4;
    return p.sort((a,b) => Math.atan2(a[1]-cy, a[0]-cx) - Math.atan2(b[1]-cy, b[0]-cx));
  }

  // Signed-area (shoelace) magnitude of a quad given in perimeter order, in px².
  function quadAreaPx(pts){
    if(!pts || pts.length<4) return 0;
    let a=0;
    for(let i=0;i<4;i++){ const p=pts[i], q=pts[(i+1)%4]; a += p[0]*q[1] - q[0]*p[1]; }
    return Math.abs(a)/2;
  }

  // Homography (3×3) mapping the 4 source points to the 4 destination points under a
  // projective transform (h33 fixed to 1). With src = the tapped image-space corners of
  // a rectangle and dst = that rectangle's real-mm corners, the result maps image px →
  // plane mm, which is exactly what CalibMeasure needs to rectify a tilted photo.
  // Returns a row-major 3×3 array, or null on a degenerate correspondence.
  function solveHomography(src, dst){
    if(!src || !dst || src.length<4 || dst.length<4) return null;
    const A=[], b=[];
    for(let i=0;i<4;i++){
      const x=src[i][0], y=src[i][1], u=dst[i][0], v=dst[i][1];
      A.push([x, y, 1, 0, 0, 0, -x*u, -y*u]); b.push(u);
      A.push([0, 0, 0, x, y, 1, -x*v, -y*v]); b.push(v);
    }
    const h=_solveLinear(A, b);
    if(!h || h.some(v => !isFinite(v))) return null;
    return [[h[0],h[1],h[2]], [h[3],h[4],h[5]], [h[6],h[7],1]];
  }

  return { distPtLine, solveHomography, orderQuad, quadAreaPx };
})();