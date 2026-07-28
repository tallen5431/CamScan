// Annotation store + hit testing (+ optional visibility filter) + export JSON
window.CalibAnn = (function(){
  // Look the dependency up lazily so module load order never matters.
  const distPtLine = (...a)=>window.CalibGeom.distPtLine(...a);
  let _id=1; const next=()=>_id++;

  function createStore(){ return { items:[], selectedId:null, hitTolPx:24 }; }
  function addSegment(s,a,b,mm_per_px,units,markerId){ const id=next(); s.items.push({id,type:'segment',a:[...a],b:[...b],mm_per_px,units,markerId}); s.selectedId=id; }
  function addNote(s,p,text){ const id=next(); s.items.push({id,type:'note',p:[...p],text:String(text||'')}); s.selectedId=id; }
  function addPolyline(s,pts,mm_per_px,units,markerId){ const id=next(); s.items.push({id,type:'polyline',pts:pts.map(p=>[...p]),mm_per_px,units,markerId}); s.selectedId=id; }
  function addRectangle(s,a,b,mm_per_px,units,markerId){ const x1=Math.min(a[0],b[0]),y1=Math.min(a[1],b[1]),x2=Math.max(a[0],b[0]),y2=Math.max(a[1],b[1]); const id=next(); s.items.push({id,type:'rectangle',rect:[x1,y1,x2,y2],mm_per_px,units,markerId}); s.selectedId=id; }
  function addAngle(s,a,v,b,mm_per_px,units,markerId){ const id=next(); s.items.push({id,type:'angle',a:[...a],v:[...v],b:[...b],mm_per_px,units,markerId}); s.selectedId=id; }

  // Circle annotation: center + radius (manual circle - 2 points)
  function addCircle(s,center,edge,mm_per_px,units,markerId){
    const radius=Math.hypot(edge[0]-center[0],edge[1]-center[1]);
    const id=next();
    s.items.push({id,type:'circle',center:[...center],radius,mm_per_px,units,markerId});
    s.selectedId=id;
  }

  // Circle from 3 points (using CalibCircles.fitCircleFromPoints)
  function addCircle3pt(s,pts,mm_per_px,units,markerId){
    if(pts.length<3) return;
    const Circles=window.CalibCircles;
    if(!Circles) return;
    const fitted=Circles.fitCircleFromPoints(pts);
    if(!fitted) return;
    const id=next();
    // Keep the 3 rim points: they let the measurement project them onto the plane and
    // refit the circle there, so a hole shot at an angle reads its TRUE diameter.
    s.items.push({id,type:'circle',center:[...fitted.center],radius:fitted.radius,
                  pts:pts.slice(0,3).map(p=>[p[0],p[1]]),mm_per_px,units,markerId});
    s.selectedId=id;
  }

  // allowFn(item) optional; hitTolPx is in IMAGE units (overlay scales it with zoom)
  function hitTest(s,x,y,allowFn){
    let best=null, bestD=s.hitTolPx||18;
    for(const it of s.items){
      if(typeof allowFn==='function' && !allowFn(it)) continue;
      if(it.type==='segment'){
        const d=distPtLine(x,y,it.a[0],it.a[1],it.b[0],it.b[1]); if(d<bestD){bestD=d;best=it;}
      }else if(it.type==='polyline'){
        const pts=it.pts||[]; for(let i=1;i<pts.length;i++){ const d=distPtLine(x,y,pts[i-1][0],pts[i-1][1],pts[i][0],pts[i][1]); if(d<bestD){bestD=d;best=it;} }
      }else if(it.type==='rectangle'){
        const [x1,y1,x2,y2]=it.rect; const es=[[x1,y1,x2,y1],[x2,y1,x2,y2],[x2,y2,x1,y2],[x1,y2,x1,y1]];
        for(const [ax,ay,bx,by] of es){ const d=distPtLine(x,y,ax,ay,bx,by); if(d<bestD){bestD=d;best=it;} }
      }else if(it.type==='angle'){
        const ln=[[it.v,it.a],[it.v,it.b]]; for(const [p,q] of ln){ const d=distPtLine(x,y,p[0],p[1],q[0],q[1]); if(d<bestD){bestD=d;best=it;} }
      }else if(it.type==='circle'){
        // Perimeter → select/resize. Also register a hit at the CENTER dot so a large
        // circle (whose interior never comes within tolerance of the ring) can still be
        // grabbed and moved — previously only the ring hit, so big circles could be
        // resized but never repositioned.
        const [cx,cy]=it.center;
        const distFromCenter=Math.hypot(x-cx,y-cy);
        const distFromPerimeter=Math.abs(distFromCenter-it.radius);
        if(distFromPerimeter<bestD){bestD=distFromPerimeter;best=it;}
        if(distFromCenter<bestD){bestD=distFromCenter;best=it;}
      }else if(it.type==='note'){
        const d=Math.hypot(x-it.p[0],y-it.p[1]); if(d<bestD){bestD=d;best=it;}
      }
    }
    return best;
  }

  function toExportJSON(imgSrc, calib, store, units){
    return { image: imgSrc, calibration: calib, annotations: store.items.map(a=>JSON.parse(JSON.stringify(a))), units };
  }

  return { createStore, addSegment, addNote, addPolyline, addRectangle, addAngle, addCircle, addCircle3pt, hitTest, toExportJSON };
})();
