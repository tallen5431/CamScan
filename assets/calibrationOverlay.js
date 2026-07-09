// Orchestrator: wires viewport + gestures + annotations + UI
(function(){
  function waitForDeps(maxMs, step, ready){
    const t0=performance.now();
    (function tick(){
      if(window.CalibUnits && window.CalibGeom && window.CalibDraw && window.CalibAnn && window.CalibExport
         && window.CalibViewport && window.CalibGestures && window.CalibUI) return ready();
      if(performance.now()-t0>maxMs){ console.error('[CalibrationOverlay] modules missing'); return; }
      setTimeout(tick, step);
    })();
  }

  (function cssOnce(){
    const ID="cal-ovr-css";
    if(document.getElementById(ID)) return;
    const st=document.createElement('style'); st.id=ID;
    st.textContent = `
      .cal-view{max-width:1000px;margin:0 auto}
      .cal-view canvas{display:block; width:100%; touch-action:none}
    `;
    document.head.appendChild(st);
  })();

  waitForDeps(6000, 80, function boot(){
    const Units=window.CalibUnits, Draw=window.CalibDraw, Ann=window.CalibAnn;
    const { CalibViewport:VP, CalibGestures:G, CalibUI:UI, CalibExport:Xport } = window;

    class CalibrationOverlay{
      constructor(canvas, imgSrc, jsonSrc){
        this.canvas=canvas; this.ctx=canvas.getContext('2d',{alpha:false});
        this.imgSrc=imgSrc; this.jsonSrc=jsonSrc; this.img=null; this.data=null;
        this.vp = VP.create(canvas, null);

        this.ann = Ann.createStore();        // image-space items
        this.selectedPoints=[]; this.hover=null; this.noteText='';
        this.drag=null; this._rAF=0; this._spacePan=false;

        this.opts = {
          mode:'select',          // 'pan','select','segment','polyline','rectangle','angle','circle','circle3pt','note','setscale'
          units:'mm',
          snap:false, snapPx:15,  // DEFAULT: off (precise clicks)
          labelScale:1.35, linePx:3,
          showGrid:false, showAnn:true, showMarkers:true,
          exportVisibleOnly:true, lockMarkerId:null,
          manualMmPerPx:null      // user-set scale (from a known length) — wins over markers
        };

        this._init();
      }

      async _init(){
        const wrap=this.canvas.closest('.cal-view');
        this.img = new Image(); this.img.decoding='async'; this.img.loading='eager';
        this.img.onload = async()=>{
          await this._loadJSON();
          (this.data.markers||[]).forEach((m,i)=>{ if(m.id==null) m.id=i+1; });

          // If the user picked a real cube size earlier this session, apply it now.
          try{
            const ms=parseFloat(localStorage.getItem('calib.markerSizeMM'));
            if(ms>0 && this.data && this.data.markers && this.data.markers.length){ this.setMarkerSizeMM(ms); }
          }catch(e){}

          this._wire();
          UI.build(wrap, this);           // builds the toolbar (its height defines the canvas top)
          this._layout();                  // size the canvas to the space under the toolbar + fit
          this.redraw();
        };
        this.img.src = this.imgSrc;
      }

      // Flexbox (CSS) makes the canvas fill the space beneath the sticky toolbar; here we
      // just refresh the backing store and fit the whole image inside that box (contain),
      // so nothing is hidden behind the toolbar or cut off at the bottom.
      _layout(){
        this.vp.fit(this.canvas, this.img);
        this.requestDraw();
      }

      async _loadJSON(){
        try{
          const r = await fetch(this.jsonSrc,{cache:'no-store'}); if(!r.ok) throw new Error(r.status);
          const raw = await r.json();
          this.data = (raw && raw.markers) ? raw : (()=>{
            const base=(String(this.imgSrc).split('/').pop());
            if(raw && raw[base]) return raw[base];
            const k=raw && Object.keys(raw)[0];
            return k ? raw[k] : { markers:[], image_size:{width:this.img.width,height:this.img.height}, marker_size_mm:null };
          })();
        }catch{
          // Fallback if JSON fails (e.g., HTTPS / prefix / proxy issues)
          this.data = { markers:[], image_size:{width:this.img.width,height:this.img.height}, marker_size_mm:null };
        }
      }

      // --- wiring --------------------------------------------------------------
      requestDraw(){ if(this._rAF) return; this._rAF=requestAnimationFrame(()=>{ this._rAF=0; this.redraw(); }); }
      setMode(m){ this.opts.mode=m; this.selectedPoints=[]; this.hover=null; this.requestDraw(); }
      zoomStep(f){ this.vp.setZoomAround(this.vp.k*f, this.vp.centerAnchor(this.canvas)); this.requestDraw(); }
      setZoom(k){ this.vp.setZoomAround(k, this.vp.centerAnchor(this.canvas)); this.requestDraw(); }
      fitToContainer(){ this.vp.fit(this.canvas, this.img); this.requestDraw(); }
      fitToHeight(){ this.vp.fitHeight(this.canvas, this.img); this.requestDraw(); }
      resetView(){ this.vp.reset(); this.vp.fit(this.canvas, this.img); this.requestDraw(); }
      undo(){
        if(this.selectedPoints.length){ this.selectedPoints.pop(); }
        else if(this.ann.selectedId!=null){
          this.ann.items=this.ann.items.filter(a=>a.id!==this.ann.selectedId);
          this.ann.selectedId=null;   // clear so the next Undo isn't a no-op
        }
        this.hover=null; this.requestDraw();
      }
      clearAll(){ this.selectedPoints=[]; this.hover=null; this.ann.selectedId=null; this.ann.items=[]; this.requestDraw(); }

      deleteSelected(){
        if(this.ann && this.ann.selectedId!=null){
          this.ann.items = this.ann.items.filter(a => a.id !== this.ann.selectedId);
          this.ann.selectedId = null;
          this.requestDraw();
        }
      }

      _wire(){
        const hitVisible = it => this.opts.showAnn && this.ann.items.includes(it) || true;

        const canPanStart = (ev) => (
          this.opts.mode==='pan' || this._spacePan || ev.shiftKey || ev.button===1 || ev.button===2
        );

        G.attach(this.canvas, this.vp, {
          canPanStart,
          onTransform: ()=>this.requestDraw(),
          onHover: (pt)=>{ this.hover = this.opts.snap ? this._maybeSnap(pt) : pt; if(this.selectedPoints.length) this.requestDraw(); },
          onDown: (pt, ev)=>{
            this.ann.hitTolPx = this.vp.pxToImg(18);
            const hit = Ann.hitTest(this.ann, pt[0], pt[1], hitVisible);
            this.ann.selectedId = hit ? hit.id : this.ann.selectedId;
            this.drag = hit ? this._makeDragHandle(hit, pt) : null;
            this.requestDraw();
          },
          onDrag: (pt)=>{
            if(!this.drag) return;
            this._updateDrag(this.opts.snap ? this._maybeSnap(pt) : pt);
            this.requestDraw();
          },
          onUp: ()=>{ this.drag=null; },
          onClick: (pt)=>{
            if (this.opts.mode==='pan') return; // clicks do nothing in Pan mode
            const p = this.opts.snap ? this._maybeSnap(pt) : pt;

            if (this.opts.mode==='select'){
              this.ann.hitTolPx = this.vp.pxToImg(18);
              const hit = Ann.hitTest(this.ann, p[0], p[1], hitVisible);
              this.ann.selectedId = hit ? hit.id : null;
              return this.requestDraw();
            }
            if (this.opts.mode==='note'){
              Ann.addNote(this.ann, p, this.noteText||'Note');
              return this.requestDraw();
            }

            // Polyline: a tap near the previous point (or double-tap) finishes the path.
            if (this.opts.mode==='polyline' && this.selectedPoints.length>=2){
              const last=this.selectedPoints[this.selectedPoints.length-1];
              if(Math.hypot(p[0]-last[0], p[1]-last[1]) < this.vp.pxToImg(12)){
                return this.finishPolyline();
              }
            }

            this.selectedPoints.push(p);
            const mm=this.getScale() || (this.data?.mm_per_px ?? 0);

            // Set-scale-from-line: two clicks define a line of known real length.
            if(this.opts.mode==='setscale' && this.selectedPoints.length===2){
              const [a,b]=this.selectedPoints;
              const px=Math.hypot(b[0]-a[0], b[1]-a[1]);
              this.selectedPoints=[];
              this._promptScale(px);
              return this.requestDraw();
            }
            if(this.opts.mode==='segment' && this.selectedPoints.length===2){
              const [a,b]=this.selectedPoints; Ann.addSegment(this.ann, a,b, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            if(this.opts.mode==='rectangle' && this.selectedPoints.length===2){
              const [a,b]=this.selectedPoints; Ann.addRectangle(this.ann, a,b, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            if(this.opts.mode==='angle' && this.selectedPoints.length===3){
              const [a,v,b]=this.selectedPoints; Ann.addAngle(this.ann, a,v,b, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            if(this.opts.mode==='circle' && this.selectedPoints.length===2){
              const [center,edge]=this.selectedPoints; Ann.addCircle(this.ann, center, edge, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            if(this.opts.mode==='circle3pt' && this.selectedPoints.length===3){
              Ann.addCircle3pt(this.ann, this.selectedPoints, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            this.requestDraw();
          }
        });

        // Double-click / double-tap finishes an in-progress polyline.
        this.canvas.addEventListener('dblclick', (e)=>{
          e.preventDefault();
          if(this.opts.mode==='polyline') this.finishPolyline();
        });

        // keyboard: add Space-to-pan
        window.addEventListener('keydown', (e)=>{
          if (e.target && /input|textarea|select/i.test(e.target.tagName)) return;
          const k=e.key.toLowerCase();
          if(k===' ') { this._spacePan = true; e.preventDefault(); }
          if(k==='0') this.setMode('pan');
          if(k==='1') this.setMode('select');
          if(k==='2') this.setMode('segment');
          if(k==='3') this.setMode('polyline');
          if(k==='4') this.setMode('rectangle');
          if(k==='5') this.setMode('angle');
          if(k==='6') this.setMode('circle');
          if(k==='7') this.setMode('circle3pt');
          if(k==='8') this.setMode('note');
          if(k==='enter') this.finishPolyline();
          if(k==='escape'){ this.selectedPoints=[]; this.hover=null; this.requestDraw(); }
          if(k==='+' || k==='=') this.zoomStep(1.2);
          if(k==='-' || k==='_') this.zoomStep(1/1.2);
        });
        window.addEventListener('keyup', (e)=>{ if(e.key===' ') this._spacePan=false; });

        // Orientation change → re-fit (the aspect changed). Plain resize → keep the
        // user's zoom/pan, just refresh the backing store so drawing stays crisp.
        window.addEventListener('resize', ()=>{
          this.vp._updateBackingStore();
          this.requestDraw();
        }, {passive:true});
        window.addEventListener('orientationchange', ()=>{ this._layout(); }, {passive:true});
      }

      // --- helpers -------------------------------------------------------------
      _maybeSnap(pt){
        // Keep in case you want to re-enable later; default is precision (no snap)
        const tol = this.vp.pxToImg(this.opts.snapPx);
        let best=null, bestD=tol;
        for(const m of (this.data?.markers||[])){
          for(const p of (m.corners||[])){
            const d=Math.hypot(pt[0]-p.x, pt[1]-p.y);
            if(d<bestD){ bestD=d; best=[p.x,p.y]; }
          }
        }
        return best || pt;
      }

      _makeDragHandle(item, p){
        const tol = this.ann.hitTolPx || 10;
        const near = (q)=>Math.hypot(q[0]-p[0], q[1]-p[1])<tol;
        if(item.type==='segment'){
          if(near(item.a)) return {item, kind:'seg-a'};
          if(near(item.b)) return {item, kind:'seg-b'};
          return {item, kind:'move-seg', start:p};
        }
        if(item.type==='note') return {item, kind:'move-note', start:p};
        if(item.type==='rectangle'){
          const [x1,y1,x2,y2]=item.rect;
          const corners=[[x1,y1],[x2,y1],[x2,y2],[x1,y2]];
          for(let i=0;i<4;i++) if(near(corners[i])) return {item, kind:'rect-corner', idx:i};
          return {item, kind:'move-rect', start:p};
        }
        if(item.type==='polyline'){
          const pts=item.pts||[];
          for(let i=0;i<pts.length;i++) if(near(pts[i])) return {item, kind:'poly-vertex', idx:i};
          return {item, kind:'move-poly', start:p};
        }
        if(item.type==='angle'){
          if(near(item.a)) return {item, kind:'ang-a'};
          if(near(item.v)) return {item, kind:'ang-v'};
          if(near(item.b)) return {item, kind:'ang-b'};
          return {item, kind:'move-ang', start:p};
        }
        if(item.type==='circle'){
          const [cx,cy]=item.center;
          // Near the center → move the whole circle.
          if(near([cx,cy])) return {item, kind:'move-circle', start:p};
          // Anywhere on the ring → resize (grab the perimeter, not just one point).
          const distFromCenter=Math.hypot(p[0]-cx, p[1]-cy);
          if(Math.abs(distFromCenter-item.radius)<tol) return {item, kind:'circle-resize'};
          // Otherwise move.
          return {item, kind:'move-circle', start:p};
        }
        return null;
      }
      _updateDrag(p){
        const d=this.drag; if(!d) return;
        if(d.kind==='seg-a') d.item.a = [p[0],p[1]];
        else if(d.kind==='seg-b') d.item.b = [p[0],p[1]];
        else if(d.kind==='move-seg'){ const dx=p[0]-d.start[0], dy=p[1]-d.start[1]; d.item.a=[d.item.a[0]+dx,d.item.a[1]+dy]; d.item.b=[d.item.b[0]+dx,d.item.b[1]+dy]; d.start=[p[0],p[1]]; }
        else if(d.kind==='move-note'){ const dx=p[0]-d.start[0], dy=p[1]-d.start[1]; d.item.p=[d.item.p[0]+dx,d.item.p[1]+dy]; d.start=[p[0],p[1]]; }
        else if(d.kind==='rect-corner'){
          const [x1,y1,x2,y2]=d.item.rect; const cs=[[x1,y1],[x2,y1],[x2,y2],[x1,y2]];
          cs[d.idx]=[p[0],p[1]];
          const xs=[cs[0][0],cs[1][0],cs[2][0],cs[3][0]].sort((a,b)=>a-b);
          const ys=[cs[0][1],cs[1][1],cs[2][1],cs[3][1]].sort((a,b)=>a-b);
          d.item.rect=[xs[0],ys[0],xs[3],ys[3]];
        }else if(d.kind==='move-rect'){ const dx=p[0]-d.start[0], dy=p[1]-d.start[1]; const r=d.item.rect; d.item.rect=[r[0]+dx,r[1]+dy,r[2]+dx,r[3]+dy]; d.start=[p[0],p[1]]; }
        else if(d.kind==='poly-vertex'){ d.item.pts[d.idx]=[p[0],p[1]]; }
        else if(d.kind==='move-poly'){ const dx=p[0]-d.start[0], dy=p[1]-d.start[1]; d.item.pts=d.item.pts.map(q=>[q[0]+dx,q[1]+dy]); d.start=[p[0],p[1]]; }
        else if(d.kind==='ang-a'){ d.item.a=[p[0],p[1]]; } else if(d.kind==='ang-v'){ d.item.v=[p[0],p[1]]; } else if(d.kind==='ang-b'){ d.item.b=[p[0],p[1]]; }
        else if(d.kind==='move-ang'){ const dx=p[0]-d.start[0], dy=p[1]-d.start[1]; d.item.a=[d.item.a[0]+dx,d.item.a[1]+dy]; d.item.b=[d.item.b[0]+dx,d.item.b[1]+dy]; d.item.v=[d.item.v[0]+dx,d.item.v[1]+dy]; d.start=[p[0],p[1]]; }
        else if(d.kind==='move-circle'){ const dx=p[0]-d.start[0], dy=p[1]-d.start[1]; d.item.center=[d.item.center[0]+dx,d.item.center[1]+dy]; d.start=[p[0],p[1]]; }
        else if(d.kind==='circle-resize'){ const [cx,cy]=d.item.center; d.item.radius=Math.hypot(p[0]-cx,p[1]-cy); }
      }

      // --- draw ----------------------------------------------------------------
      redraw(){
        this.ann.hitTolPx = this.vp.pxToImg(18);
        const c=this.ctx;
        // clear & set transform
        c.setTransform(1,0,0,1,0,0);
        c.clearRect(0,0,this.canvas.width,this.canvas.height);
        this.vp.applyToContext(c);

        c.drawImage(this.img,0,0);
        if(this.opts.showGrid) Draw.drawGrid(c, this.img.width, this.img.height);
        if(this.opts.showMarkers) Draw.drawMarkers(c, this.canvas, this.data, this.opts.linePx);
        this._drawAnnotations();
        this._drawPreview();

        this.updateKPI();
      }

      _drawAnnotations(){
        if(!this.opts.showAnn) return;
        const c=this.ctx, unit=Units.get(this.opts.units);
        const dotR = Draw.px(this.canvas,8), linePx=Draw.px(this.canvas,this.opts.linePx);
        for(const a of this.ann.items){
          const sel=(a.id===this.ann.selectedId);
          if(a.type==='segment'){
            const mm=a.mm_per_px||this.getScale()||0;
            const dx=a.b[0]-a.a[0], dy=a.b[1]-a.a[1];
            const val=unit.fromMM(Math.hypot(dx,dy)*mm);
            c.lineWidth=linePx; c.strokeStyle=sel?"rgba(255,170,0,1)":"lime";
            c.beginPath(); c.moveTo(a.a[0],a.a[1]); c.lineTo(a.b[0],a.b[1]); c.stroke();
            c.fillStyle=sel?"rgba(255,170,0,1)":"lime";
            for(const [x,y] of [a.a,a.b]){ c.beginPath(); c.arc(x,y,dotR,0,Math.PI*2); c.fill(); c.strokeStyle="#000"; c.lineWidth=Draw.px(this.canvas,2); c.stroke(); }
            const mid=[(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2];
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
          } else if(a.type==='note'){
            const [tx,ty]=a.p; c.fillStyle=sel?"rgba(255,170,0,1)":"deepskyblue";
            c.beginPath(); c.arc(tx,ty,Draw.px(this.canvas,9),0,Math.PI*2); c.fill();
            c.strokeStyle="#000"; c.lineWidth=Draw.px(this.canvas,2); c.stroke();
            if(a.text){ const f=Math.round(18*this.opts.labelScale); const pad=8*this.opts.labelScale; const lx=tx+14;
              c.font=Draw.font(f); c.textAlign="left"; c.textBaseline="middle";
              const boxW=c.measureText(a.text).width + 2*pad, boxH=f+2*pad, ly=ty-boxH/2;
              c.fillStyle="rgba(0,0,0,.7)"; c.fillRect(lx,ly,boxW,boxH);
              c.fillStyle="#fff"; c.fillText(a.text, lx+pad, ty);
            }
          } else if(a.type==='polyline'){
            const pts=a.pts||[]; if(pts.length<2) continue;
            const mm=a.mm_per_px||this.getScale()||0; let px=0; for(let i=1;i<pts.length;i++) px+=Math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]);
            const val=unit.fromMM(px*mm);
            c.lineWidth=linePx; c.strokeStyle=sel?"rgba(255,170,0,1)":"orange";
            c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); for(let i=1;i<pts.length;i++) c.lineTo(pts[i][0], pts[i][1]); c.stroke();
            c.fillStyle=sel?"rgba(255,170,0,1)":"orange";
            for(const [x,y] of pts){ c.beginPath(); c.arc(x,y,Draw.px(this.canvas,8),0,Math.PI*2); c.fill(); c.strokeStyle="#000"; c.lineWidth=Draw.px(this.canvas,2); c.stroke(); }
            const mid=pts[Math.floor(pts.length/2)]; Draw.boxLabel(c, this.canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
          } else if(a.type==='rectangle'){
            const [x1,y1,x2,y2]=a.rect;
            const mm=a.mm_per_px||this.getScale()||0; const wmm=(x2-x1)*mm, hmm=(y2-y1)*mm, amm=wmm*hmm;
            c.lineWidth=linePx; c.strokeStyle=sel?"rgba(255,170,0,1)":"orange"; c.strokeRect(x1,y1,x2-x1,y2-y1);
            Draw.boxLabel(c, this.canvas, (x1+x2)/2, y1-10, `${unit.fromMM(wmm).toFixed(3)}×${unit.fromMM(hmm).toFixed(3)} ${unit.label} • A ${unit.areaFromMM2(amm).toFixed(3)} ${unit.areaLabel}`, this.opts.labelScale);
          } else if(a.type==='angle'){
            c.lineWidth=linePx; c.strokeStyle=sel?"rgba(255,170,0,1)":"orange";
            c.beginPath(); c.moveTo(a.v[0],a.v[1]); c.lineTo(a.a[0],a.a[1]); c.moveTo(a.v[0],a.v[1]); c.lineTo(a.b[0],a.b[1]); c.stroke();
            const ang=window.CalibGeom.angleABC(a.a,a.v,a.b); Draw.boxLabel(c, this.canvas, a.v[0], a.v[1]-20, `θ ${ang.toFixed(2)}°`, this.opts.labelScale);
          } else if(a.type==='circle'){
            const [cx,cy]=a.center, r=a.radius;
            const mm=a.mm_per_px||this.getScale()||0;
            const diam_mm=2*r*mm, area_mm2=Math.PI*r*r*mm*mm;
            const diam_val=unit.fromMM(diam_mm);
            c.lineWidth=linePx; c.strokeStyle=sel?"rgba(255,170,0,1)":"cyan";
            c.beginPath(); c.arc(cx,cy,r,0,Math.PI*2); c.stroke();
            // Draw diameter line
            c.beginPath(); c.moveTo(cx-r,cy); c.lineTo(cx+r,cy); c.stroke();
            // Draw center dot
            c.fillStyle=sel?"rgba(255,170,0,1)":"cyan";
            c.beginPath(); c.arc(cx,cy,dotR,0,Math.PI*2); c.fill();
            c.strokeStyle="#000"; c.lineWidth=Draw.px(this.canvas,2); c.stroke();
            // Labels
            Draw.boxLabel(c, this.canvas, cx, cy-r-15, `⌀ ${diam_val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
            Draw.boxLabel(c, this.canvas, cx, cy-r-35, `A ${unit.areaFromMM2(area_mm2).toFixed(3)} ${unit.areaLabel}`, this.opts.labelScale);
          }
        }
      }

      _drawPreview(){
        if(this.selectedPoints.length===0 && !this.hover) return;
        const c=this.ctx, unit=Units.get(this.opts.units);
        const linePx=Draw.px(this.canvas,this.opts.linePx), dotR=Draw.px(this.canvas,8);
        c.fillStyle='orange'; c.strokeStyle='rgba(255,200,0,.9)'; c.lineWidth=linePx;
        for(const [x,y] of this.selectedPoints){ c.beginPath(); c.arc(x,y,dotR,0,Math.PI*2); c.fill(); c.strokeStyle='#000'; c.lineWidth=Draw.px(this.canvas,2); c.stroke(); }
        const H=this.hover;
        if((this.opts.mode==='segment'||this.opts.mode==='setscale') && this.selectedPoints.length===1 && H){ const a=this.selectedPoints[0], b=H;
          c.save(); c.setLineDash([10,8]); c.beginPath(); c.moveTo(a[0],a[1]); c.lineTo(b[0],b[1]); c.stroke(); c.restore();
          const mid=[(a[0]+b[0])/2,(a[1]+b[1])/2];
          if(this.opts.mode==='setscale'){
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `set scale — click 2nd point`, this.opts.labelScale);
          } else {
            const sMM=this.getScale()||0, val=unit.fromMM(Math.hypot(b[0]-a[0], b[1]-a[1]) * sMM);
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `~${val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
          }
        }
        else if(this.opts.mode==='polyline' && this.selectedPoints.length>=1){ const pts=H?[...this.selectedPoints,H]:[...this.selectedPoints];
          c.save(); c.setLineDash([10,8]); c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); for(let i=1;i<pts.length;i++) c.lineTo(pts[i][0], pts[i][1]); c.stroke(); c.restore();
        }
        else if(this.opts.mode==='rectangle' && this.selectedPoints.length===1 && H){
          const a=this.selectedPoints[0], b=H;
          const x1=Math.min(a[0],b[0]), y1=Math.min(a[1],b[1]); const x2=Math.max(a[0],b[0]), y2=Math.max(a[1],b[1]);
          c.save(); c.setLineDash([10,8]); c.strokeRect(x1,y1,x2-x1,y2-y1); c.restore();
        }
        else if(this.opts.mode==='angle' && this.selectedPoints.length>=1){
          const a=(this.selectedPoints.length>=1)?this.selectedPoints[0]:null;
          const v=(this.selectedPoints.length>=2)?this.selectedPoints[1]:H;
          const b=(this.selectedPoints.length>=3)?this.selectedPoints[2]:H;
          if(a&&v&&b){ c.save(); c.setLineDash([10,8]); c.beginPath(); c.moveTo(v[0],v[1]); c.lineTo(a[0],a[1]); c.moveTo(v[0],v[1]); c.lineTo(b[0],b[1]); c.stroke(); c.restore(); }
        }
        else if(this.opts.mode==='circle' && this.selectedPoints.length===1 && H){
          const center=this.selectedPoints[0], edge=H;
          const radius=Math.hypot(edge[0]-center[0], edge[1]-center[1]);
          const sMM=this.getScale()||0, diam_mm=2*radius*sMM, diam_val=unit.fromMM(diam_mm);
          c.save(); c.setLineDash([10,8]); c.strokeStyle='cyan'; c.lineWidth=linePx;
          c.beginPath(); c.arc(center[0],center[1],radius,0,Math.PI*2); c.stroke();
          c.setLineDash([]); c.beginPath(); c.moveTo(center[0]-radius,center[1]); c.lineTo(center[0]+radius,center[1]); c.stroke();
          c.restore();
          Draw.boxLabel(c, this.canvas, center[0], center[1]-radius-15, `⌀ ~${diam_val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
        }
        else if(this.opts.mode==='circle3pt' && this.selectedPoints.length>=2 && H){
          const pts=(this.selectedPoints.length===2)?[...this.selectedPoints,H]:this.selectedPoints;
          if(pts.length===3 && window.CalibCircles){
            const fitted=window.CalibCircles.fitCircleFromPoints(pts);
            if(fitted){
              const [cx,cy]=fitted.center, r=fitted.radius;
              const sMM=this.getScale()||0, diam_mm=2*r*sMM, diam_val=unit.fromMM(diam_mm);
              c.save(); c.setLineDash([10,8]); c.strokeStyle='cyan'; c.lineWidth=linePx;
              c.beginPath(); c.arc(cx,cy,r,0,Math.PI*2); c.stroke();
              c.setLineDash([]); c.beginPath(); c.moveTo(cx-r,cy); c.lineTo(cx+r,cy); c.stroke();
              c.restore();
              Draw.boxLabel(c, this.canvas, cx, cy-r-15, `⌀ ~${diam_val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
            }
          }
        }
      }

      // Pixel edge length of a marker, computed from its 4 corners. This lets us
      // recompute mm_per_px for any real cube size without re-running detection.
      _markerEdgePx(m){
        const pts=(m.corners||[]).map(p=>[p.x,p.y]);
        if(pts.length<4) return 0;
        let d=0; for(let i=0;i<4;i++){ const a=pts[i], b=pts[(i+1)%4]; d+=Math.hypot(a[0]-b[0], a[1]-b[1]); }
        return d/4;
      }

      // Scale (mm/px) that applies at image point `ref`.
      _scaleForPoint(ref){
        // 1) A manually entered scale (from a known length) always wins.
        if(this.opts.manualMmPerPx && this.opts.manualMmPerPx>0) return this.opts.manualMmPerPx;
        if(!this.data || !Array.isArray(this.data.markers) || !this.data.markers.length) return 0;
        // 2) A specific locked marker, if it resolves.
        if(this.opts.lockMarkerId){
          const m=this.data.markers.find(m=>String(m.id)===String(this.opts.lockMarkerId));
          if(m && m.mm_per_px) return m.mm_per_px;
          // fall through to auto if the locked marker is missing/invalid
        }
        // 3) Nearest marker (to `ref`) with a valid scale.
        ref = ref || [0,0];
        let best=null, bestD=1e18;
        for(const m of this.data.markers){
          const pts=(m.corners||[]).map(p=>[p.x,p.y]); if(pts.length<4||!m.mm_per_px) continue;
          const c=pts.reduce((a,p)=>[a[0]+p[0]/pts.length,a[1]+p[1]/pts.length],[0,0]);
          const d=Math.hypot(ref[0]-c[0], ref[1]-c[1]); if(d<bestD){ bestD=d; best=m; }
        }
        if(best) return best.mm_per_px;
        // 4) Any marker with a scale.
        const any=this.data.markers.find(m=>m.mm_per_px>0);
        return any ? any.mm_per_px : 0;
      }

      getScale(){ return this._scaleForPoint(this.hover || this.selectedPoints[0] || [0,0]); }

      _annCentroid(a){
        if(a.type==='segment') return [(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2];
        if(a.type==='rectangle'){ const [x1,y1,x2,y2]=a.rect; return [(x1+x2)/2,(y1+y2)/2]; }
        if(a.type==='angle') return a.v;
        if(a.type==='circle') return a.center;
        if(a.type==='polyline' && a.pts && a.pts.length) return a.pts[Math.floor(a.pts.length/2)];
        if(a.type==='note') return a.p;
        return [0,0];
      }

      // After the calibration changes (cube size or manual scale), refresh the frozen
      // per-annotation scale so existing measurements update to the new reference.
      _rescaleAnnotations(){
        for(const a of this.ann.items){
          if(a.type==='note') continue;
          const s=this._scaleForPoint(this._annCentroid(a));
          if(s>0) a.mm_per_px=s;
        }
      }

      isCalibrated(){ return this.getScale() > 0; }

      // Re-scale every detected marker to a new real cube edge length (mm). Because
      // detection is purely pixel-based, mm_per_px = size_mm / edge_px is exact and
      // instant — no server round-trip needed.
      setMarkerSizeMM(mm){
        mm = parseFloat(mm);
        if(!(mm>0)) return;
        this.opts.manualMmPerPx = null; // markers become the source of truth again
        const vals=[];
        for(const m of (this.data?.markers||[])){
          const px = m.edge_px || this._markerEdgePx(m);
          if(px>0){ m.edge_mm = mm; m.mm_per_px = mm/px; vals.push(m.mm_per_px); }
        }
        if(this.data){
          this.data.marker_size_mm = mm;
          if(vals.length){ this.data.mm_per_px = vals.reduce((a,b)=>a+b,0)/vals.length; this.data.pixels_per_mm = 1/this.data.mm_per_px; }
        }
        try{ localStorage.setItem('calib.markerSizeMM', String(mm)); }catch(e){}
        this._rescaleAnnotations();
        this.requestDraw();
      }

      // Set the scale directly from a known length: the user draws a line of known real
      // length; mm_per_px = knownMM / pixelLength. Works even with zero detected markers.
      setManualScaleFromPixels(pixelLength, knownMM){
        knownMM = parseFloat(knownMM);
        if(!(pixelLength>0) || !(knownMM>0)) return;
        this.opts.manualMmPerPx = knownMM/pixelLength;
        this._rescaleAnnotations();
        this.requestDraw();
      }
      clearManualScale(){ this.opts.manualMmPerPx = null; this._rescaleAnnotations(); this.requestDraw(); }
      currentMarkerSizeMM(){ return (this.data && this.data.marker_size_mm) || null; }

      // Commit the in-progress polyline (needs >=2 points). Clears the working points.
      finishPolyline(){
        if(this.opts.mode==='polyline' && this.selectedPoints.length>=2){
          const mm=this.getScale() || (this.data?.mm_per_px ?? 0);
          Ann.addPolyline(this.ann, this.selectedPoints, mm, this.opts.units, this.opts.lockMarkerId);
        }
        this.selectedPoints=[]; this.hover=null; this.requestDraw();
      }

      // Ask for the real length of a just-drawn line and set the working scale from it.
      // The UI may provide onNeedScaleInput(pixelLen, apply) for an inline field; otherwise
      // we fall back to a prompt(). The entered value is in the current display unit.
      _promptScale(pixelLen){
        const unit=Units.get(this.opts.units);
        const apply=(valStr)=>{
          if(valStr==null) return;
          const v=parseFloat(valStr);
          if(v>0){ this.setManualScaleFromPixels(pixelLen, unit.toMM(v)); this.setMode('select'); }
        };
        if(typeof this.onNeedScaleInput==='function') this.onNeedScaleInput(pixelLen, apply, unit);
        else apply(prompt(`Enter the real length of this line in ${unit.label}:`, ''));
      }

      updateKPI(){
        const el=document.getElementById('cal-kpi'); if(!el) return;
        const s=this.getScale()||0; const unit=Units.get(this.opts.units);
        const zoom=`Zoom: ${Math.round(this.vp.k*100)}%`;
        const anns=`Annotations: ${this.ann.items.length}`;
        if(s<=0){
          el.innerHTML = `⚠️ <b>Not calibrated</b> — no reference square found. Use “Set scale” to draw a line of known length. &nbsp;|&nbsp; ${zoom} &nbsp;|&nbsp; ${anns}`;
          return;
        }
        const src = this.opts.manualMmPerPx ? 'manual' : `${this.currentMarkerSizeMM()??'—'} mm square`;
        const unitPerPx=unit.fromMM(s);
        el.textContent = `Scale: ${unitPerPx.toFixed(6)} ${unit.label}/px (${(s*1000).toFixed(1)} µm/px) • ref: ${src} | ${zoom} | Snap: ${this.opts.snap?'on':'off'} | ${anns}`;
      }

      _exportStore(){ return { items: this.ann.items.filter(Boolean) }; }
      _imgH(){ return this.img ? (this.img.naturalHeight || this.img.height) : 0; }
      savePNG(){ Xport.exportPNG(this.img, this.data, this._exportStore(), this.opts.showGrid, this.opts.showMarkers, this.opts.units, this.opts.labelScale, this.opts.linePx); }
      saveJSON(){
        const payload=Ann.toExportJSON(this.imgSrc, {
          marker_size_mm:this.data?.marker_size_mm??null,
          mm_per_px:this.getScale()||(this.data?.mm_per_px??null),
          pixels_per_mm:this.data?.pixels_per_mm??null,
          manual_scale:this.opts.manualMmPerPx||null,
          markers:this.data?.markers??[]
        }, this._exportStore(), this.opts.units);
        Xport.exportJSON(payload);
      }
      _confirmUncalibrated(){
        return this.isCalibrated() || confirm('Not calibrated — exported values will be in pixels, not millimetres. Set a scale first for real measurements. Export anyway?');
      }
      saveCSV(){ if(!this._confirmUncalibrated()) return; Xport.exportCSV(this.data, this._exportStore(), this.opts.units, this.getScale()); }
      saveDXF(){ if(!this._confirmUncalibrated()) return; return Xport.exportDXF(this.data, this._exportStore(), { image_height:this._imgH(), mm_per_px:this.getScale()||this.data?.mm_per_px||0 }); }
    }

    window.CalibrationOverlay = window.CalibrationOverlay || CalibrationOverlay;

    function initScan(){
      document.querySelectorAll('.cal-view:not([data-initialized])').forEach(el=>{
        // Prefer data-img; fall back to data-img-fallback if needed (important for HTTPS/proxy)
        const primary = el.getAttribute('data-img');
        const fallback = el.getAttribute('data-img-fallback');
        const img = primary || fallback;

        const json=el.getAttribute('data-json');
        const canvas=el.querySelector('canvas');
        if(!img||!json||!canvas) return;
        el.setAttribute('data-initialized','1');
        el.__overlay = new CalibrationOverlay(canvas, img, json);
      });
    }
    if(document.readyState!=='loading') initScan(); else document.addEventListener('DOMContentLoaded', initScan);
    new MutationObserver(initScan).observe(document.documentElement,{childList:true,subtree:true});
  });
})();
