// Orchestrator: wires viewport + gestures + annotations + UI
(function(){
  function waitForDeps(maxMs, step, ready){
    const t0=performance.now();
    (function tick(){
      if(window.CalibUnits && window.CalibGeom && window.CalibDraw && window.CalibAnn && window.CalibExport
         && window.CalibViewport && window.CalibGestures && window.CalibUI && window.CalibMeasure) return ready();
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
    const Units=window.CalibUnits, Draw=window.CalibDraw, Ann=window.CalibAnn, Measure=window.CalibMeasure;
    const { CalibViewport:VP, CalibGestures:G, CalibUI:UI, CalibExport:Xport } = window;

    class CalibrationOverlay{
      constructor(canvas, imgSrc, jsonSrc){
        this.canvas=canvas; this.ctx=canvas.getContext('2d',{alpha:false});
        this.imgSrc=imgSrc; this.jsonSrc=jsonSrc; this.img=null; this.data=null;
        this.vp = VP.create(canvas, null);

        this.ann = Ann.createStore();        // image-space items
        this.selectedPoints=[]; this.hover=null; this.noteText='';
        this.drag=null; this._rAF=0; this._spacePan=false;

        // Undo/redo history: snapshots of ann.items taken BEFORE each mutating op.
        this._undoStack=[]; this._redoStack=[]; this._preDragSnapshot=null; this._dragPushed=false;
        // True when the active marker size came from a remembered (localStorage) value
        // rather than this photo's own calibration — surfaced in the KPI so it is never silent.
        this._markerSizeFromMemory=false;

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
            if(ms>0 && this.data && this.data.markers && this.data.markers.length){ this.setMarkerSizeMM(ms, true); }
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
      requestDraw(){ if(this._destroyed || this._rAF) return; this._rAF=requestAnimationFrame(()=>{ this._rAF=0; if(!this._destroyed) this.redraw(); }); }
      setMode(m){ this.opts.mode=m; this.selectedPoints=[]; this.hover=null; this.requestDraw(); }
      zoomStep(f){ this.vp.setZoomAround(this.vp.k*f, this.vp.centerAnchor(this.canvas)); this.requestDraw(); }
      setZoom(k){ this.vp.setZoomAround(k, this.vp.centerAnchor(this.canvas)); this.requestDraw(); }
      fitToContainer(){ this.vp.fit(this.canvas, this.img); this.requestDraw(); }
      fitToHeight(){ this.vp.fitHeight(this.canvas, this.img); this.requestDraw(); }
      resetView(){ this.vp.reset(); this.vp.fit(this.canvas, this.img); this.requestDraw(); }
      // --- undo/redo history ---------------------------------------------------
      // A deep clone of the current annotation list. Cheap for the dozens of items
      // a user realistically draws, and makes every op trivially reversible.
      _snapshot(){ return this.ann.items.map(a=>JSON.parse(JSON.stringify(a))); }
      // Call BEFORE any mutation of ann.items. Pushes the pre-change state and
      // invalidates the redo stack (a new edit forks history).
      _pushHistory(){
        this._undoStack.push(this._snapshot());
        if(this._undoStack.length>100) this._undoStack.shift();
        this._redoStack.length=0;
      }
      canUndo(){ return this._undoStack.length>0 || this.selectedPoints.length>0; }
      canRedo(){ return this._redoStack.length>0; }
      undo(){
        // First peel back any in-progress (uncommitted) points.
        if(this.selectedPoints.length){ this.selectedPoints.pop(); this.hover=null; return this.requestDraw(); }
        if(this._undoStack.length){
          this._redoStack.push(this._snapshot());
          this.ann.items=this._undoStack.pop();
          this.ann.selectedId=null; this.hover=null; this.requestDraw();
        }
      }
      redo(){
        if(this._redoStack.length){
          this._undoStack.push(this._snapshot());
          this.ann.items=this._redoStack.pop();
          this.ann.selectedId=null; this.hover=null; this.requestDraw();
        }
      }
      clearAll(){ this._pushHistory(); this.selectedPoints=[]; this.hover=null; this.ann.selectedId=null; this.ann.items=[]; this.requestDraw(); }

      deleteSelected(){
        if(this.ann && this.ann.selectedId!=null){
          this._pushHistory();
          this.ann.items = this.ann.items.filter(a => a.id !== this.ann.selectedId);
          this.ann.selectedId = null;
          this.requestDraw();
        }
      }

      _wire(){
        // Only hit-test annotations that are actually shown. (The previous
        // `... || true` collapsed this to a constant, so hidden annotations
        // stayed selectable/draggable.)
        const hitVisible = () => !!this.opts.showAnn;

        const canPanStart = (ev) => (
          this.opts.mode==='pan' || this._spacePan || ev.shiftKey || ev.button===1 || ev.button===2
        );

        this._gestureDetach = G.attach(this.canvas, this.vp, {
          canPanStart,
          onTransform: ()=>this.requestDraw(),
          onHover: (pt)=>{ this.hover = this.opts.snap ? this._maybeSnap(pt) : pt; if(this.selectedPoints.length) this.requestDraw(); },
          onDown: (pt, ev)=>{
            this.ann.hitTolPx = this.vp.pxToImg(18);
            const hit = Ann.hitTest(this.ann, pt[0], pt[1], hitVisible);
            this.ann.selectedId = hit ? hit.id : this.ann.selectedId;
            this.drag = hit ? this._makeDragHandle(hit, pt) : null;
            // Remember the pre-drag state; it is committed to history only if the
            // drag actually moves the geometry (see _updateDrag), so a plain
            // select-click doesn't pollute the undo stack.
            if(this.drag){ this._preDragSnapshot=this._snapshot(); this._dragPushed=false; }
            this.requestDraw();
          },
          onDrag: (pt)=>{
            if(!this.drag) return;
            if(!this._dragPushed && this._preDragSnapshot){
              this._undoStack.push(this._preDragSnapshot);
              if(this._undoStack.length>100) this._undoStack.shift();
              this._redoStack.length=0;
              this._dragPushed=true;
            }
            this._updateDrag(this.opts.snap ? this._maybeSnap(pt, this.drag && this.drag.item) : pt);
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
              this._pushHistory();
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
              const mid=[(a[0]+b[0])/2,(a[1]+b[1])/2];
              // Keep the two points drawn while the user types the real length so the
              // line stays visible behind the inline input (the old prompt() covered it).
              this._promptScale(px, mid);
              return this.requestDraw();
            }
            if(this.opts.mode==='segment' && this.selectedPoints.length===2){
              const [a,b]=this.selectedPoints; this._pushHistory(); Ann.addSegment(this.ann, a,b, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            if(this.opts.mode==='rectangle' && this.selectedPoints.length===2){
              const [a,b]=this.selectedPoints; this._pushHistory(); Ann.addRectangle(this.ann, a,b, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            if(this.opts.mode==='angle' && this.selectedPoints.length===3){
              const [a,v,b]=this.selectedPoints; this._pushHistory(); Ann.addAngle(this.ann, a,v,b, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            if(this.opts.mode==='circle' && this.selectedPoints.length===2){
              const [center,edge]=this.selectedPoints; this._pushHistory(); Ann.addCircle(this.ann, center, edge, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            if(this.opts.mode==='circle3pt' && this.selectedPoints.length===3){
              this._pushHistory(); Ann.addCircle3pt(this.ann, this.selectedPoints, mm, this.opts.units, this.opts.lockMarkerId);
              this.selectedPoints=[]; return this.requestDraw();
            }
            this.requestDraw();
          }
        });

        // Double-click / double-tap finishes an in-progress polyline.
        this._onDblClick = (e)=>{
          e.preventDefault();
          if(this.opts.mode==='polyline') this.finishPolyline();
        };
        this.canvas.addEventListener('dblclick', this._onDblClick);

        // keyboard: add Space-to-pan. Handlers are stored on `this` so destroy() can
        // remove them — otherwise every replaced overlay leaks 4 live window listeners.
        this._onKeyDown = (e)=>{
          if (e.target && /input|textarea|select/i.test(e.target.tagName)) return;
          const k=e.key.toLowerCase();
          // Undo/redo chords (handled before the modifier guard below).
          if((e.ctrlKey||e.metaKey) && k==='z'){ e.preventDefault(); if(e.shiftKey) this.redo(); else this.undo(); return; }
          if((e.ctrlKey||e.metaKey) && k==='y'){ e.preventDefault(); this.redo(); return; }
          if((e.ctrlKey||e.metaKey) && k==='c'){ if(this.ann && this.ann.selectedId!=null){ e.preventDefault(); this._copySelected(); return; } }
          // Don't hijack native browser/OS shortcuts (Ctrl+0 reset-zoom, Cmd+1 tab
          // switch, Ctrl+± page zoom, …). Only plain / Shift keys drive the tools.
          if(e.ctrlKey||e.metaKey||e.altKey) return;
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
          if(k==='delete' || k==='backspace'){ e.preventDefault(); this.deleteSelected(); }
          if(k==='c') this._copySelected();
          if(k==='+' || k==='=') this.zoomStep(1.2);
          if(k==='-' || k==='_') this.zoomStep(1/1.2);
        };
        window.addEventListener('keydown', this._onKeyDown);

        this._onKeyUp = (e)=>{ if(e.key===' ') this._spacePan=false; };
        window.addEventListener('keyup', this._onKeyUp);

        // Orientation change → re-fit (the aspect changed). Plain resize → keep the
        // user's zoom/pan, just refresh the backing store so drawing stays crisp.
        this._onResize = ()=>{
          this.vp._updateBackingStore();
          this.requestDraw();
        };
        window.addEventListener('resize', this._onResize, {passive:true});
        this._onOrient = ()=>{ this._layout(); };
        window.addEventListener('orientationchange', this._onOrient, {passive:true});
      }

      // Tear down all global listeners + the gesture handlers and stop any pending
      // redraw. The app's core loop replaces the #viewer subtree on every new upload,
      // so without this each prior overlay (its decoded image, undo stacks and 4 window
      // listeners) would leak permanently and keep reacting to keystrokes/resize.
      destroy(){
        if(this._destroyed) return;
        this._destroyed=true;
        try{ this._gestureDetach && this._gestureDetach(); }catch(e){}
        window.removeEventListener('keydown', this._onKeyDown);
        window.removeEventListener('keyup', this._onKeyUp);
        window.removeEventListener('resize', this._onResize);
        window.removeEventListener('orientationchange', this._onOrient);
        if(this._onDblClick && this.canvas) this.canvas.removeEventListener('dblclick', this._onDblClick);
        if(this._rAF){ cancelAnimationFrame(this._rAF); this._rAF=0; }
        const box=document.getElementById('cal-scale-input'); if(box) box.remove();
      }

      // --- helpers -------------------------------------------------------------
      // Snap the point to the nearest marker corner OR existing annotation vertex
      // within snapPx. Snapping to annotation endpoints is what makes chained /
      // continuous dimensioning possible (start a new measurement exactly where a
      // previous one ended). Returns the snapped point, or the original if none near.
      // `excludeItem` (optional): the annotation currently being dragged. Its vertices are
      // skipped so a grabbed endpoint never snaps back onto its own stale pre-drag
      // coordinates — which otherwise froze fine (<snapPx) vertex adjustments while snap
      // was on, the exact mode snapping is meant to serve.
      _maybeSnap(pt, excludeItem){
        const tol = this.vp.pxToImg(this.opts.snapPx);
        let best=null, bestD=tol;
        const consider=(x,y)=>{ const d=Math.hypot(pt[0]-x, pt[1]-y); if(d<bestD){ bestD=d; best=[x,y]; } };
        for(const m of (this.data?.markers||[])) for(const p of (m.corners||[])) consider(p.x, p.y);
        for(const a of this.ann.items){
          if(a===excludeItem) continue;
          if(a.type==='segment'){ consider(a.a[0],a.a[1]); consider(a.b[0],a.b[1]); }
          else if(a.type==='polyline'){ for(const q of (a.pts||[])) consider(q[0],q[1]); }
          else if(a.type==='rectangle'){ const [x1,y1,x2,y2]=a.rect; consider(x1,y1); consider(x2,y1); consider(x2,y2); consider(x1,y2); }
          else if(a.type==='angle'){ consider(a.a[0],a.a[1]); consider(a.v[0],a.v[1]); consider(a.b[0],a.b[1]); }
          else if(a.type==='circle'){ consider(a.center[0],a.center[1]); }
          else if(a.type==='note'){ consider(a.p[0],a.p[1]); }
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
          // Anchor the diagonally-opposite corner so the rectangle can be resized
          // in any direction (including shrinking) from the grabbed corner.
          for(let i=0;i<4;i++) if(near(corners[i])) return {item, kind:'rect-corner', idx:i, anchor:corners[(i+2)%4]};
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
          // Resize from the grabbed corner toward `p`, keeping the opposite corner
          // fixed. Math.min/max re-normalises so the rect stays valid even when the
          // grabbed corner is dragged past the anchor.
          const a=d.anchor||[d.item.rect[0],d.item.rect[1]];
          d.item.rect=[Math.min(p[0],a[0]),Math.min(p[1],a[1]),Math.max(p[0],a[0]),Math.max(p[1],a[1])];
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
        if(Draw.resetLabels) Draw.resetLabels();   // start a fresh label-placement frame
        this._drawAnnotations();
        this._drawPreview();

        this.updateKPI();
      }

      _drawAnnotations(){
        if(!this.opts.showAnn) return;
        const c=this.ctx, unit=Units.get(this.opts.units);
        const C=Draw.colors;
        const dotR = Draw.px(this.canvas,8), linePx=Draw.px(this.canvas,this.opts.linePx);
        // Selected shapes render in a high-contrast white and a bolder line so the
        // selection is unmistakable regardless of the base tool color.
        const selW = linePx*1.7;
        for(const a of this.ann.items){
          const sel=(a.id===this.ann.selectedId);
          if(a.type==='segment'){
            const mm=Measure.length(this._measureCtx(a), a.a[0],a.a[1], a.b[0],a.b[1]);
            const pxLen=Math.hypot(a.b[0]-a.a[0], a.b[1]-a.a[1]);
            c.lineWidth=sel?selW:linePx; c.strokeStyle=sel?C.selected:C.segment;
            c.beginPath(); c.moveTo(a.a[0],a.a[1]); c.lineTo(a.b[0],a.b[1]); c.stroke();
            c.fillStyle=sel?C.selected:C.segment;
            for(const [x,y] of [a.a,a.b]){ c.beginPath(); c.arc(x,y,dotR,0,Math.PI*2); c.fill(); c.strokeStyle="#000"; c.lineWidth=Draw.px(this.canvas,2); c.stroke(); }
            const mid=[(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2];
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], this._fmtLen(mm, pxLen), this.opts.labelScale, C.segment);
          } else if(a.type==='note'){
            const [tx,ty]=a.p; c.fillStyle=sel?C.selected:C.note;
            c.beginPath(); c.arc(tx,ty,Draw.px(this.canvas,9),0,Math.PI*2); c.fill();
            c.strokeStyle="#000"; c.lineWidth=Draw.px(this.canvas,2); c.stroke();
            if(a.text){ const f=Math.round(18*this.opts.labelScale); const pad=8*this.opts.labelScale; const lx=tx+14;
              c.font=Draw.font(f); c.textAlign="left"; c.textBaseline="middle";
              const boxW=c.measureText(a.text).width + 2*pad, boxH=f+2*pad, ly=ty-boxH/2;
              c.fillStyle="rgba(0,0,0,.72)"; c.fillRect(lx,ly,boxW,boxH);
              c.strokeStyle="rgba(255,255,255,.35)"; c.lineWidth=Draw.px(this.canvas,1.5); c.strokeRect(lx,ly,boxW,boxH);
              c.fillStyle="#fff"; c.fillText(a.text, lx+pad, ty);
            }
          } else if(a.type==='polyline'){
            const pts=a.pts||[]; if(pts.length<2) continue;
            const mm=Measure.polyline(this._measureCtx(a), pts);
            let pxLen=0; for(let i=1;i<pts.length;i++) pxLen+=Math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]);
            c.lineWidth=sel?selW:linePx; c.strokeStyle=sel?C.selected:C.polyline;
            c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); for(let i=1;i<pts.length;i++) c.lineTo(pts[i][0], pts[i][1]); c.stroke();
            c.fillStyle=sel?C.selected:C.polyline;
            for(const [x,y] of pts){ c.beginPath(); c.arc(x,y,Draw.px(this.canvas,8),0,Math.PI*2); c.fill(); c.strokeStyle="#000"; c.lineWidth=Draw.px(this.canvas,2); c.stroke(); }
            const mid=pts[Math.floor(pts.length/2)]; Draw.boxLabel(c, this.canvas, mid[0], mid[1], this._fmtLen(mm, pxLen), this.opts.labelScale, C.polyline);
          } else if(a.type==='rectangle'){
            const [x1,y1,x2,y2]=a.rect;
            const rm=Measure.rect(this._measureCtx(a), x1,y1,x2,y2);
            c.lineWidth=sel?selW:linePx; c.strokeStyle=sel?C.selected:C.rectangle; c.strokeRect(x1,y1,x2-x1,y2-y1);
            let rlabel;
            if(this.isCalibrated()){
              rlabel = `${unit.fromMM(rm.w).toFixed(3)}×${unit.fromMM(rm.h).toFixed(3)} ${unit.label} • A ${unit.areaFromMM2(rm.area).toFixed(3)} ${unit.areaLabel}`;
            }else{
              const pw=Math.abs(x2-x1), ph=Math.abs(y2-y1);
              rlabel = `${Math.round(pw)}×${Math.round(ph)} px • A ${Math.round(pw*ph)} px²`;
            }
            Draw.boxLabel(c, this.canvas, (x1+x2)/2, y1-10, rlabel, this.opts.labelScale, C.rectangle);
          } else if(a.type==='angle'){
            c.lineWidth=sel?selW:linePx; c.strokeStyle=sel?C.selected:C.angle;
            c.beginPath(); c.moveTo(a.v[0],a.v[1]); c.lineTo(a.a[0],a.a[1]); c.moveTo(a.v[0],a.v[1]); c.lineTo(a.b[0],a.b[1]); c.stroke();
            const ang=Measure.angle(this._measureCtx(a), a.a, a.v, a.b); Draw.boxLabel(c, this.canvas, a.v[0], a.v[1]-20, `θ ${ang.toFixed(2)}°`, this.opts.labelScale, C.angle);
          } else if(a.type==='circle'){
            // Shared with the PNG exporter so the live canvas and the export never
            // drift (line thickness, label size/position all come from opts here).
            if(window.CalibCircles){
              window.CalibCircles.drawCircle(c, this.canvas, a, this.data, this.opts.units,
                { selected: sel, labelScale: this.opts.labelScale, linePx: this.opts.linePx, fallbackScale: this.getScale() });
            }
          }
        }
      }

      _drawPreview(){
        if(this.selectedPoints.length===0 && !this.hover) return;
        const c=this.ctx, unit=Units.get(this.opts.units);
        const linePx=Draw.px(this.canvas,this.opts.linePx), dotR=Draw.px(this.canvas,8);
        // Snap cursor: when snapping is on, ring the point the next click will land on
        // (marker corner or an existing measurement's endpoint) so chaining is visible.
        if(this.opts.snap && this.hover && this.opts.mode!=='pan' && this.opts.mode!=='select'){
          c.save(); c.strokeStyle='rgba(0,212,255,.95)'; c.lineWidth=Draw.px(this.canvas,2);
          c.beginPath(); c.arc(this.hover[0], this.hover[1], Draw.px(this.canvas,10), 0, Math.PI*2); c.stroke();
          c.restore();
        }
        c.fillStyle='orange'; c.strokeStyle='rgba(255,200,0,.9)'; c.lineWidth=linePx;
        for(const [x,y] of this.selectedPoints){ c.beginPath(); c.arc(x,y,dotR,0,Math.PI*2); c.fill(); c.strokeStyle='#000'; c.lineWidth=Draw.px(this.canvas,2); c.stroke(); }
        const H=this.hover;
        if((this.opts.mode==='segment'||this.opts.mode==='setscale') && this.selectedPoints.length===1 && H){ const a=this.selectedPoints[0], b=H;
          c.save(); c.setLineDash([10,8]); c.beginPath(); c.moveTo(a[0],a[1]); c.lineTo(b[0],b[1]); c.stroke(); c.restore();
          const mid=[(a[0]+b[0])/2,(a[1]+b[1])/2];
          if(this.opts.mode==='setscale'){
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `set scale — click 2nd point`, this.opts.labelScale);
          } else {
            const mm=Measure.length(this._measureCtx(null), a[0],a[1], b[0],b[1]);
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `~${this._fmtLen(mm, Math.hypot(b[0]-a[0], b[1]-a[1]))}`, this.opts.labelScale);
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
          const sMM=this.getScale()||0;
          c.save(); c.setLineDash([10,8]); c.strokeStyle='cyan'; c.lineWidth=linePx;
          c.beginPath(); c.arc(center[0],center[1],radius,0,Math.PI*2); c.stroke();
          c.setLineDash([]); c.beginPath(); c.moveTo(center[0]-radius,center[1]); c.lineTo(center[0]+radius,center[1]); c.stroke();
          c.restore();
          Draw.boxLabel(c, this.canvas, center[0], center[1]-radius-15, `⌀ ~${this._fmtLen(2*radius*sMM, 2*radius)}`, this.opts.labelScale);
        }
        else if(this.opts.mode==='circle3pt' && this.selectedPoints.length>=2 && H){
          const pts=(this.selectedPoints.length===2)?[...this.selectedPoints,H]:this.selectedPoints;
          if(pts.length===3 && window.CalibCircles){
            const fitted=window.CalibCircles.fitCircleFromPoints(pts);
            if(fitted){
              const [cx,cy]=fitted.center, r=fitted.radius;
              const sMM=this.getScale()||0;
              c.save(); c.setLineDash([10,8]); c.strokeStyle='cyan'; c.lineWidth=linePx;
              c.beginPath(); c.arc(cx,cy,r,0,Math.PI*2); c.stroke();
              c.setLineDash([]); c.beginPath(); c.moveTo(cx-r,cy); c.lineTo(cx+r,cy); c.stroke();
              c.restore();
              Draw.boxLabel(c, this.canvas, cx, cy-r-15, `⌀ ~${this._fmtLen(2*r*sMM, 2*r)}`, this.opts.labelScale);
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

      // Measurement context for an annotation: uses the calibration homography to
      // rectify perspective when present, unless a manual scale is active (which is
      // inherently uniform). Falls back to the annotation's own frozen mm/px.
      _measureCtx(a){
        const fallback = (a && a.mm_per_px) || this.getScale() || 0;
        return Measure.context(this.data, fallback, !this.opts.manualMmPerPx);
      }

      _annCentroid(a){
        if(a.type==='segment') return [(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2];
        if(a.type==='rectangle'){ const [x1,y1,x2,y2]=a.rect; return [(x1+x2)/2,(y1+y2)/2]; }
        if(a.type==='angle') return a.v;
        if(a.type==='circle') return a.center;
        if(a.type==='polyline' && a.pts && a.pts.length) return a.pts[Math.floor(a.pts.length/2)];
        if(a.type==='note') return a.p;
        return [0,0];
      }

      // Precise value(s) of the currently-selected annotation, for the status readout
      // and copy-to-clipboard. Reuses the same perspective-aware measurement path as
      // the on-canvas labels so the readout matches exactly. Returns {label,text,copy}.
      _selectedReadout(){
        const a=this.ann.items.find(it=>it.id===this.ann.selectedId); if(!a) return null;
        const unit=Units.get(this.opts.units), ctx=this._measureCtx(a);
        const cal=this.isCalibrated();
        if(a.type==='segment'){ const px=Math.hypot(a.b[0]-a.a[0],a.b[1]-a.a[1]); const t=this._fmtLen(Measure.length(ctx,a.a[0],a.a[1],a.b[0],a.b[1]),px); return {label:'Length', text:t, copy:cal?unit.fromMM(Measure.length(ctx,a.a[0],a.a[1],a.b[0],a.b[1])).toFixed(3):String(Math.round(px))}; }
        if(a.type==='polyline'){ const pts=a.pts||[]; let px=0; for(let i=1;i<pts.length;i++) px+=Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]); const mm=Measure.polyline(ctx,pts); return {label:'Path', text:this._fmtLen(mm,px), copy:cal?unit.fromMM(mm).toFixed(3):String(Math.round(px))}; }
        if(a.type==='rectangle'){ const [x1,y1,x2,y2]=a.rect; const rm=Measure.rect(ctx,x1,y1,x2,y2);
          if(cal){ const w=unit.fromMM(rm.w),h=unit.fromMM(rm.h),ar=unit.areaFromMM2(rm.area); return {label:'Rect', text:`${w.toFixed(3)}×${h.toFixed(3)} ${unit.label} · A ${ar.toFixed(3)} ${unit.areaLabel}`, copy:`${w.toFixed(3)}x${h.toFixed(3)}`}; }
          const pw=Math.abs(x2-x1),ph=Math.abs(y2-y1); return {label:'Rect', text:`${Math.round(pw)}×${Math.round(ph)} px · A ${Math.round(pw*ph)} px²`, copy:`${Math.round(pw)}x${Math.round(ph)}`}; }
        if(a.type==='angle'){ const ang=Measure.angle(ctx,a.a,a.v,a.b); return {label:'Angle', text:`${ang.toFixed(2)}°`, copy:ang.toFixed(2)}; }
        if(a.type==='circle'){ const scale=(a.mm_per_px||this.getScale()||0); const t=this._fmtLen(2*a.radius*scale, 2*a.radius); return {label:'⌀', text:t, copy:cal?unit.fromMM(2*a.radius*scale).toFixed(3):String(Math.round(2*a.radius))}; }
        if(a.type==='note'){ return {label:'Note', text:(a.text||''), copy:(a.text||'')}; }
        return null;
      }
      _copySelected(){
        const rd=this._selectedReadout(); if(!rd) return;
        try{ if(navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(rd.copy); }catch(e){}
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

      // Format a length/area for a label: real units when calibrated, raw PIXELS
      // otherwise — so an uncalibrated measurement reads as "128 px", never a fake
      // "0.000 mm" that looks like a genuine zero-length result.
      _fmtLen(mm, pxDist){
        if(this.isCalibrated()){ const u=Units.get(this.opts.units); return `${u.fromMM(mm).toFixed(3)} ${u.label}`; }
        return `${Math.round(pxDist)} px`;
      }
      _fmtArea(mm2, pxArea){
        if(this.isCalibrated()){ const u=Units.get(this.opts.units); return `${u.areaFromMM2(mm2).toFixed(3)} ${u.areaLabel}`; }
        return `${Math.round(pxArea)} px²`;
      }

      // Re-scale every detected marker to a new real cube edge length (mm). Because
      // detection is purely pixel-based, mm_per_px = size_mm / edge_px is exact and
      // instant — no server round-trip needed.
      setMarkerSizeMM(mm, fromMemory){
        mm = parseFloat(mm);
        if(!(mm>0)) return;
        // Distinguish a size the user typed for THIS photo from one restored across
        // photos, so the KPI can flag the remembered case instead of silently
        // rescaling an unrelated image (see updateKPI).
        this._markerSizeFromMemory = !!fromMemory;
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
        this._markerSizeFromMemory = false;
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
          this._pushHistory();
          Ann.addPolyline(this.ann, this.selectedPoints, mm, this.opts.units, this.opts.lockMarkerId);
        }
        this.selectedPoints=[]; this.hover=null; this.requestDraw();
      }

      // Ask for the real length of a just-drawn line and set the working scale from it.
      // The UI may provide onNeedScaleInput(pixelLen, apply) for an inline field; otherwise
      // we fall back to a prompt(). The entered value is in the current display unit.
      _promptScale(pixelLen, mid){
        const unit=Units.get(this.opts.units);
        const done=(valStr)=>{
          this.selectedPoints=[]; this.hover=null;
          if(valStr!=null){ const v=parseFloat(valStr); if(v>0){ this.setManualScaleFromPixels(pixelLen, unit.toMM(v)); this.setMode('select'); } }
          this.requestDraw();
        };
        if(typeof this.onNeedScaleInput==='function'){ this.onNeedScaleInput(pixelLen, done, unit, mid); return; }
        this._inlineScaleInput(pixelLen, done, unit, mid);
      }

      // Floating inline field near the drawn line's midpoint for entering its real
      // length. Replaces the native prompt(), which covered the line and was clumsy on
      // mobile. done(valueStringOrNull) applies the value or cancels.
      _inlineScaleInput(pixelLen, done, unit, mid){
        const prev=document.getElementById('cal-scale-input'); if(prev) prev.remove();
        const box=document.createElement('div'); box.id='cal-scale-input';
        box.style.cssText='position:fixed;z-index:40;background:#0e0e0e;border:2px solid #00d4ff;border-radius:8px;padding:8px;box-shadow:0 8px 24px rgba(0,0,0,.6);display:flex;gap:6px;align-items:center;font:14px Segoe UI,system-ui,sans-serif;color:#eee;';
        const lab=document.createElement('span'); lab.textContent='Line length:';
        const inp=document.createElement('input'); inp.type='number'; inp.step='0.01'; inp.min='0'; inp.inputMode='decimal'; inp.placeholder='e.g. 25.4';
        inp.style.cssText='width:96px;padding:6px;background:#181818;border:1px solid #2a2a2a;border-radius:6px;color:#eee;font:14px inherit;';
        const us=document.createElement('span'); us.textContent=unit.label;
        const ok=document.createElement('button'); ok.type='button'; ok.textContent='Set'; ok.style.cssText='padding:6px 12px;background:#00d4ff;color:#000;border:none;border-radius:6px;font-weight:600;cursor:pointer;';
        const cancel=document.createElement('button'); cancel.type='button'; cancel.textContent='✕'; cancel.style.cssText='padding:6px 9px;background:#181818;color:#eee;border:1px solid #2a2a2a;border-radius:6px;cursor:pointer;';
        box.append(lab,inp,us,ok,cancel); document.body.appendChild(box);
        // Position near the line midpoint (fall back to the viewport centre).
        try{
          const r=this.canvas.getBoundingClientRect();
          const [dx,dy]=this.vp.imageToCanvas(mid[0],mid[1]);
          const cssX=r.left + dx*(r.width/Math.max(1,this.canvas.width));
          const cssY=r.top  + dy*(r.height/Math.max(1,this.canvas.height));
          box.style.left=Math.max(8, Math.min(window.innerWidth-box.offsetWidth-8, cssX-box.offsetWidth/2))+'px';
          box.style.top =Math.max(8, Math.min(window.innerHeight-box.offsetHeight-8, cssY+16))+'px';
        }catch(e){ box.style.left='50%'; box.style.top='40%'; box.style.transform='translate(-50%,-50%)'; }
        const finish=(val)=>{ box.remove(); done(val); };
        ok.onclick=()=>finish(inp.value);
        cancel.onclick=()=>finish(null);
        inp.addEventListener('keydown',(e)=>{ e.stopPropagation(); if(e.key==='Enter'){ e.preventDefault(); finish(inp.value); } else if(e.key==='Escape'){ e.preventDefault(); finish(null); } });
        setTimeout(()=>{ inp.focus(); if(inp.select) inp.select(); },0);
      }

      // Live guidance for the active tool, e.g. "Angle — click point 2 of 3", so a
      // user (especially on mobile, where hover tooltips don't exist) always knows
      // how many clicks the current tool needs and where they are in the sequence.
      _toolHint(){
        const m=this.opts.mode;
        const defs={ segment:['Measure',2], setscale:['Set scale',2], rectangle:['Area',2],
                     circle:['Circle',2], angle:['Angle',3], circle3pt:['3-pt circle',3],
                     note:['Note',1], polyline:['Path',0], pan:['Pan',0], select:['Select',0] };
        const d=defs[m]; if(!d) return '';
        const [name,total]=d, n=this.selectedPoints.length;
        if(m==='pan') return '';
        if(m==='select') return this.ann.selectedId!=null ? '✏️ Select — item selected (Delete removes it)' : '✏️ Select — tap a measurement';
        if(m==='note') return '✏️ Note — tap to place';
        if(m==='polyline') return n ? `✏️ Path — ${n} point(s) · double-tap or Enter to finish` : '✏️ Path — tap to start';
        return `✏️ ${name} — click point ${Math.min(n+1,total)} of ${total}`;
      }

      updateKPI(){
        const el=document.getElementById('cal-kpi'); if(!el) return;
        const s=this.getScale()||0; const unit=Units.get(this.opts.units);
        const zoom=`Zoom: ${Math.round(this.vp.k*100)}%`;
        const anns=`Annotations: ${this.ann.items.length}`;
        // When an item is selected, lead with its precise value (+ copy hint) instead
        // of the generic tool step-hint.
        const rd=this._selectedReadout();
        const lead = rd ? `🔎 ${rd.label}: ${rd.text} · C to copy` : this._toolHint();
        const hintPart = lead ? `${lead} | ` : '';
        if(s<=0){
          el.innerHTML = `${hintPart}⚠️ <b>Not calibrated</b> — no reference square found. <a href="#" id="cal-cal-link" style="color:#00d4ff;font-weight:600">Set a scale</a> by drawing a line of known length. &nbsp;|&nbsp; ${zoom} &nbsp;|&nbsp; ${anns}`;
          const lnk=el.querySelector('#cal-cal-link'); if(lnk) lnk.onclick=(e)=>{ e.preventDefault(); this.setMode('setscale'); };
          return;
        }
        // Flag a marker size restored from a previous photo so a stale remembered
        // value can't silently rescale an unrelated image without the user noticing.
        const remembered = (!this.opts.manualMmPerPx && this._markerSizeFromMemory) ? ' (remembered)' : '';
        const src = this.opts.manualMmPerPx ? 'manual' : `${this.currentMarkerSizeMM()??'—'} mm square${remembered}`;
        const unitPerPx=unit.fromMM(s);
        // A manual scale is user-defined (trusted); only the auto-detected marker
        // scale carries a confidence. Warn when it came from a rough fallback.
        const lowConf = !this.opts.manualMmPerPx && this.data && this.data.calibration_confidence === 'low';
        const warn = lowConf ? '⚠️ Approximate auto-cal — verify with “Set scale” • ' : '';
        // Show when measurements are being rectified for camera tilt.
        const persp = (!this.opts.manualMmPerPx && this.data && this.data.homography) ? ' • perspective-corrected' : '';
        el.textContent = `${hintPart}${warn}Scale: ${unitPerPx.toFixed(6)} ${unit.label}/px (${(s*1000).toFixed(1)} µm/px) • ref: ${src}${persp} | ${zoom} | Snap: ${this.opts.snap?'on':'off'} | ${anns}`;
      }

      _exportStore(){ return { items: this.ann.items.filter(Boolean) }; }
      _imgH(){ return this.img ? (this.img.naturalHeight || this.img.height) : 0; }
      savePNG(){ Xport.exportPNG(this.img, this.data, this._exportStore(), this.opts.showGrid, this.opts.showMarkers, this.opts.units, this.opts.labelScale, this.opts.linePx, !this.opts.manualMmPerPx); }
      saveJSON(){
        const payload=Ann.toExportJSON(this.imgSrc, {
          marker_size_mm:this.data?.marker_size_mm??null,
          mm_per_px:this.getScale()||(this.data?.mm_per_px??null),
          pixels_per_mm:this.data?.pixels_per_mm??null,
          manual_scale:this.opts.manualMmPerPx||null,
          // Include the rectifying homography (unless a manual scale overrides it) so a
          // JSON consumer can reproduce the SAME perspective-corrected mm the app showed,
          // instead of getting foreshortened values from mm_per_px alone on tilted shots.
          homography:(!this.opts.manualMmPerPx && this.data?.homography) ? this.data.homography : null,
          markers:this.data?.markers??[]
        }, this._exportStore(), this.opts.units);
        Xport.exportJSON(payload);
      }
      _confirmUncalibrated(){
        return this.isCalibrated() || confirm('Not calibrated — exported values will be in pixels, not millimetres. Set a scale first for real measurements. Export anyway?');
      }
      saveCSV(){ if(!this._confirmUncalibrated()) return; Xport.exportCSV(this.data, this._exportStore(), this.opts.units, this.getScale(), !this.opts.manualMmPerPx); }
      saveDXF(){ if(!this._confirmUncalibrated()) return; return Xport.exportDXF(this.data, this._exportStore(), { image_height:this._imgH(), mm_per_px:this.getScale()||this.data?.mm_per_px||0, allowHomography:!this.opts.manualMmPerPx }); }
      saveSVG(){ if(!this._confirmUncalibrated()) return; return Xport.exportSVG(this.data, this._exportStore(), { mm_per_px:this.getScale()||this.data?.mm_per_px||0, allowHomography:!this.opts.manualMmPerPx }); }
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
    // Destroy overlays whose .cal-view was removed from the DOM (the upload callback
    // swaps out #viewer's subtree on every new photo), then init any new ones. Without
    // the destroy pass, each replaced overlay leaks its window listeners forever.
    function reapRemoved(nodes){
      for(const n of nodes){
        if(!n || n.nodeType!==1) continue;
        const views=[];
        if(n.matches && n.matches('.cal-view')) views.push(n);
        if(n.querySelectorAll) n.querySelectorAll('.cal-view').forEach(v=>views.push(v));
        for(const v of views){ if(v.__overlay && v.__overlay.destroy){ try{ v.__overlay.destroy(); }catch(e){} v.__overlay=null; } }
      }
    }
    if(document.readyState!=='loading') initScan(); else document.addEventListener('DOMContentLoaded', initScan);
    new MutationObserver((mutations)=>{
      for(const m of mutations) if(m.removedNodes && m.removedNodes.length) reapRemoved(m.removedNodes);
      initScan();
    }).observe(document.documentElement,{childList:true,subtree:true});
  });
})();
