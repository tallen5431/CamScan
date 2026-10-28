// Orchestrator: wires viewport + gestures + annotations + UI
(function(){
  function waitForDeps(maxMs, step, ready){
    const t0=performance.now();
    (function tick(){
      if(window.CalibUnits && window.CalibGeom && window.CalibDraw && window.CalibAnn && window.CalibExport
         && window.CalibViewport && window.CalibGestures && window.CalibUI && window.CalibMeasure
         && window.CalibCircles) return ready();
      if(performance.now()-t0>maxMs){ console.error('[CalibrationOverlay] modules missing'); return; }
      setTimeout(tick, step);
    })();
  }

  (function cssOnce(){
    const ID="cal-ovr-css";
    if(document.getElementById(ID)) return;
    const st=document.createElement('style'); st.id=ID;
    st.textContent = `
      .cal-view{max-width:1400px;margin:0 auto}
      .cal-view canvas{display:block; width:100%; touch-action:none}
    `;
    document.head.appendChild(st);
  })();

  waitForDeps(6000, 80, function boot(){
    const Units=window.CalibUnits, Draw=window.CalibDraw, Ann=window.CalibAnn, Measure=window.CalibMeasure, Geom=window.CalibGeom;
    const { CalibViewport:VP, CalibGestures:G, CalibUI:UI, CalibExport:Xport } = window;

    class CalibrationOverlay{
      constructor(canvas, imgSrc, jsonSrc){
        this.canvas=canvas; this.ctx=canvas.getContext('2d',{alpha:false});
        this.imgSrc=imgSrc; this.jsonSrc=jsonSrc; this.img=null; this.data=null;
        this.vp = VP.create(canvas, null);

        this.ann = Ann.createStore();        // image-space items
        this.selectedPoints=[]; this.hover=null; this.noteText='';
        this.drag=null; this._rAF=0; this._spacePan=false;
        this._selDrag=null; this._suppressClick=false;   // precision-mode endpoint dragging

        // Undo/redo history: snapshots of ann.items taken BEFORE each mutating op.
        this._undoStack=[]; this._redoStack=[]; this._preDragSnapshot=null; this._dragPushed=false;
        // True when the active marker size came from a remembered (localStorage) value
        // rather than this photo's own calibration — surfaced in the KPI so it is never silent.
        this._markerSizeFromMemory=false;
        // Scale (vp.k) captured right after each fit. The ResizeObserver uses it to tell
        // "user is still at fit" (re-fit when the canvas box changes so reclaimed toolbar
        // space grows the IMAGE, not the letterbox) from "user has manually zoomed" (keep
        // their zoom, just refresh the backing store). See _onBoxResize.
        this._fitK=1;
        // Last measured KPI height written into --cal-kpi-h, so we only touch the CSS var
        // (and trigger a reflow) when the status strip's wrapped height actually changes.
        this._kpiH=0;
        // Signature of the last-rendered status strip; lets updateKPI skip rebuilding the
        // chips (and the offsetHeight reflow) on frames where nothing it shows changed.
        this._kpiSig=null;
        // Set right before a --cal-kpi-h write so the ResizeObserver ignores the box change
        // that write causes (see the observer in _wire) instead of re-fitting from it.
        this._suppressRefit=false;

        this.opts = {
          mode:'select',          // 'pan','select','segment','polyline','rectangle','angle','circle','circle3pt','note','setscale','setscalerect'
          units:'mm',
          snap:false, snapPx:15,  // DEFAULT: off (precise clicks)
          labelScale:1.35, linePx:3,
          showGrid:false, showAnn:true, showMarkers:true,
          showLabels:true,        // measurement labels on the canvas (turn off to edit a dense outline)
          handlePx:5,             // vertex-handle radius in SCREEN css px — constant regardless of zoom,
                                  // so zooming in to fix a point gives a small, precise dot over the edge
          exportVisibleOnly:true, lockMarkerId:null,
          manualMmPerPx:null,     // user-set scale (from a known length) — wins over markers
          manualHomography:null   // user-set px->mm homography (from a paper rectangle's 4
                                  // corners) — like manualMmPerPx but ALSO rectifies tilt
        };

        this._init();
      }

      async _init(){
        const wrap=this.canvas.closest('.cal-view');
        this.img = new Image(); this.img.decoding='async'; this.img.loading='eager';
        this.img.onload = async()=>{
          if(this._destroyed) return;   // a late decode must not re-wire a torn-down overlay
          // Clear any error card from a PREVIOUS failed load on this same wrap. It is an
          // opaque, full-bleed z-index:20 overlay and nothing else removed it, so a retry
          // that succeeded still showed the "could not load the image" screen on top.
          if(wrap){ const old=wrap.querySelector('.cal-load-error'); if(old) old.remove(); }
          await this._loadJSON();
          if(this._destroyed) return;   // destroyed DURING the async JSON load
          (this.data.markers||[]).forEach((m,i)=>{ if(m.id==null) m.id=i+1; });

          // If the user picked a real cube size earlier this session, apply it now — but NOT
          // when reopening a saved view: that view already carries its own marker_size_mm in
          // the restored calibration, and overwriting it from a stale localStorage value would
          // silently rescale the reopened side (breaking the mm round-trip for the marker path).
          try{
            const ms=parseFloat(localStorage.getItem('calib.markerSizeMM'));
            if(!(wrap && wrap.__restore) && ms>0 && this.data && this.data.markers && this.data.markers.length){
              this.setMarkerSizeMM(ms, true);
            }
          }catch(e){}

          this._wire();
          UI.build(wrap, this);           // builds the toolbar (its height defines the canvas top)
          this._layout();                  // size the canvas to the space under the toolbar + fit
          this.redraw();
          // Reopening a saved view? Restore its annotations + scale overrides and skip the
          // first-shot nudges (it already has a scale, and the marks are the customer's work).
          if(wrap && wrap.__restore){
            const st=wrap.__restore; wrap.__restore=null;
            this._applyRestore(st);
          }else{
            // Nudge on a soft/dark photo first, then (if no square calibrated this photo)
            // offer the auto-detected sheet as a one-tap, tilt-corrected scale.
            this._showQualityHint();
            this._offerDetectedPaper();
          }
        };
        // On decode/load failure, try the server file URL (data-img-fallback) once — it
        // survives HTTPS/proxy cases the base64 data-URI can trip on — then, if that
        // also fails, show a visible error+retry card instead of a blank dead-end (the
        // whole toolbar is built inside onload, so a silent failure left nothing).
        this.img.onerror = ()=>{
          if(this._destroyed) return;   // a late decode failure must not touch a torn-down overlay
          const fb = wrap && wrap.getAttribute('data-img-fallback');
          if(fb && !this._triedFallback && fb !== this.imgSrc){
            this._triedFallback = true;
            this.img.src = fb;
            return;
          }
          this._renderLoadError(wrap);
        };
        this.img.src = this.imgSrc;
      }

      _renderLoadError(wrap){
        if(!wrap || wrap.querySelector('.cal-load-error')) return;
        const box=document.createElement('div'); box.className='cal-load-error';
        box.style.cssText='position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:24px;text-align:center;background:#0e0e0e;color:#eee;font:14px Segoe UI,system-ui,sans-serif;z-index:20;';
        const h=document.createElement('div'); h.textContent='⚠️ Could not load the image';
        h.style.cssText='font-size:16px;font-weight:600;';
        const p=document.createElement('div'); p.textContent='The uploaded image failed to load or decode. Please try uploading it again.';
        p.style.cssText='color:#9aa;max-width:420px;';
        const btn=document.createElement('button'); btn.type='button'; btn.textContent='↻ Upload a new image';
        btn.style.cssText='padding:10px 16px;background:#00d4ff;color:#000;border:none;border-radius:8px;font-weight:600;cursor:pointer;';
        btn.onclick=()=>{ try{ (window.CalibJob && window.CalibJob.beforeReload ? window.CalibJob.beforeReload() : Promise.resolve()).then(() => window.location.reload()); }catch(e){ window.location.reload(); } };
        box.append(h,p,btn); wrap.appendChild(box);
      }

      // Flexbox (CSS) makes the canvas fill the space beneath the sticky toolbar; here we
      // just refresh the backing store and fit the whole image inside that box (contain),
      // so nothing is hidden behind the toolbar or cut off at the bottom.
      _layout(){
        this.vp.fit(this.canvas, this.img);
        this._fitK = this.vp.k;   // remember "fitted" scale so resize can detect manual zoom
        this.requestDraw();
      }

      // The canvas CSS box changed (toolbar wrapped/shrank, window resized, focus mode
      // toggled, status strip grew a line). Always refresh the backing store so the image
      // stays crisp and taps stay aligned. Then, if the user hasn't manually zoomed away
      // from the last fit, re-fit so the freed space enlarges the IMAGE instead of just
      // growing the letterbox bars — otherwise preserve their zoom/pan untouched.
      _onBoxResize(){
        if(this._destroyed || !this.img) return;
        // The box changed, so the strip may rewrap at the same content — force updateKPI
        // to re-measure its height (the signature gate would otherwise skip it).
        this._kpiSig=null;
        const atFit = Math.abs(this.vp.k - this._fitK) < 1e-4;
        if(atFit){
          this.vp.fit(this.canvas, this.img);
          this._fitK = this.vp.k;
        }else{
          this.vp._updateBackingStore();
        }
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
      fitToContainer(){ this.vp.fit(this.canvas, this.img); this._fitK=this.vp.k; this.requestDraw(); }
      fitToHeight(){ this.vp.fitHeight(this.canvas, this.img); this._fitK=this.vp.k; this.requestDraw(); }
      resetView(){ this.vp.reset(); this.vp.fit(this.canvas, this.img); this._fitK=this.vp.k; this.requestDraw(); }
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
          // The snapshot froze each item's mm/px as it was then; the scale may have changed
          // since (undo history holds the items, not the scale), so read the current one.
          this._rescaleAnnotations();
          this.ann.selectedId=null; this.hover=null; this._invalidateTraceAnchor(); this.requestDraw();
        }
      }
      redo(){
        if(this._redoStack.length){
          this._undoStack.push(this._snapshot());
          this.ann.items=this._redoStack.pop();
          this._rescaleAnnotations();
          this.ann.selectedId=null; this.hover=null; this._invalidateTraceAnchor(); this.requestDraw();
        }
      }
      // History moved the items out from under the Detail slider's retrace anchor: retire the
      // slider and drop the anchor so a later drag can't delete the wrong items or stack a
      // duplicate outline. (See _runAutoTrace, which removes prior geometry by _autoAddedIds.)
      _invalidateTraceAnchor(){ this._dismissTraceDetail(); this._lastAutoSeed=null; this._autoAddedIds=[]; }
      clearAll(){ this._pushHistory(); this.selectedPoints=[]; this.hover=null; this.ann.selectedId=null; this.ann.items=[]; this._autoAddedIds=[]; this._dismissTraceDetail(); this.requestDraw(); }

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
          onHover: (pt)=>{ this.hover = this.opts.snap ? this._maybeSnap(pt) : pt; if(this.selectedPoints.length || this._precisionMode()) this.requestDraw(); },
          onDown: (pt, ev)=>{
            this._suppressClick=false;
            // In a precision scale mode, pressing ON a placed point grabs it to fine-tune
            // (drag) instead of hit-testing an annotation or placing a new point. The
            // gesture layer already turns a moved press into a drag (and no click), so we
            // just need to suppress the click for a no-move grab so it doesn't dup a point.
            if(this._precisionMode() && this.selectedPoints.length){
              const tol=this.vp.pxToImg(22*this.vp.dpr); let bi=-1, bd=tol;
              for(let i=0;i<this.selectedPoints.length;i++){
                const d=Math.hypot(pt[0]-this.selectedPoints[i][0], pt[1]-this.selectedPoints[i][1]);
                if(d<=bd){ bd=d; bi=i; }
              }
              if(bi>=0){ this._selDrag=bi; this._suppressClick=true; this.drag=null; return this.requestDraw(); }
            }
            this.ann.hitTolPx = this.vp.pxToImg(18*this.vp.dpr);
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
            // Dragging a placed scale/verify/paper point: move it live so the user can
            // land it exactly on an edge (the loupe shows the magnified target).
            if(this._selDrag!=null){
              this.selectedPoints[this._selDrag] = this.opts.snap ? this._maybeSnap(pt) : pt;
              return this.requestDraw();
            }
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
          onUp: ()=>{ this.drag=null; this._selDrag=null; },
          onClick: (pt)=>{
            // A press that only grabbed a placed point (no move) must not also place a
            // new one. _suppressClick is reset every onDown, so it can't leak.
            if(this._suppressClick){ this._suppressClick=false; return this.requestDraw(); }
            // "Not the part": once armed, the next tap marks background and re-traces, in ANY
            // mode. It must come before the mode branches: a finished trace leaves the overlay
            // in Select, and checked after them the tap was eaten as a selection click — it
            // deselected the outline and never re-traced (caught by tests/test_newtools_e2e.py
            // driving the real button and a real canvas tap). The raw point is used: snapping
            // could pull a background hint onto the outline's own edge.
            if(this._bgTapArm){
              return this.addBackgroundTap(pt);
            }
            if (this.opts.mode==='pan') return; // clicks do nothing in Pan mode
            const p = this.opts.snap ? this._maybeSnap(pt) : pt;

            if (this.opts.mode==='select'){
              this.ann.hitTolPx = this.vp.pxToImg(18*this.vp.dpr);
              const hit = Ann.hitTest(this.ann, p[0], p[1], hitVisible);
              this.ann.selectedId = hit ? hit.id : null;
              return this.requestDraw();
            }
            if (this.opts.mode==='note'){
              this._pushHistory();
              Ann.addNote(this.ann, p, this.noteText||'Note');
              return this.requestDraw();
            }

            // Outline auto-trace: when armed, a tap seeds server-side segmentation of the
            // part under it (instead of adding a vertex) → a full editable outline in one tap.
            if(this.opts.mode==='outline' && this._autoOutlineArm){
              this._autoOutlineArm=false;
              return this.autoOutline(p);
            }

            // Path / outline: a tap near the previous point (or double-tap) finishes. For a
            // closed outline, a tap near the FIRST point instead closes the loop.
            if ((this.opts.mode==='polyline' || this.opts.mode==='outline') && this.selectedPoints.length>=2){
              const tol=this.vp.pxToImg(14*this.vp.dpr);
              const last=this.selectedPoints[this.selectedPoints.length-1];
              const first=this.selectedPoints[0];
              if(this.opts.mode==='outline' && this.selectedPoints.length>=3 &&
                 Math.hypot(p[0]-first[0], p[1]-first[1]) < tol){
                return this.finishPolyline();
              }
              if(Math.hypot(p[0]-last[0], p[1]-last[1]) < tol){
                return this.finishPolyline();
              }
            }

            // Paper/rectangle scale: ignore taps past the 4th while the size dialog is up.
            if(this.opts.mode==='setscalerect' && this.selectedPoints.length>=4) return this.requestDraw();

            this.selectedPoints.push(p);
            const mm=this.getScale() || (this.data?.mm_per_px ?? 0);

            // Set-scale-from-paper: four taps outline a rectangle of known real size; its
            // corners give a perspective homography (tilt correction), not just a scale.
            if(this.opts.mode==='setscalerect' && this.selectedPoints.length===4){
              this._promptRectScale(this.selectedPoints.slice());
              return this.requestDraw();
            }

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
            // Verify-scale: two clicks across a SECOND known feature; compare the measured
            // length (at the current scale) with its real size to expose a wrong scale.
            if(this.opts.mode==='verifyscale' && this.selectedPoints.length===2){
              const [a,b]=this.selectedPoints;
              this._promptVerify(a,b);
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
          if(this.opts.mode==='polyline' || this.opts.mode==='outline') this.finishPolyline();
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
          if(k==='9') this.setMode('outline');
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

        // Orientation change → re-fit (the aspect changed). Plain resize / any CSS-driven
        // change to the canvas box → _onBoxResize (keeps the user's zoom unless they're
        // still at fit, in which case it re-fits so reclaimed space grows the image).
        this._onResize = ()=>{ this._onBoxResize(); };
        window.addEventListener('resize', this._onResize, {passive:true});
        this._onOrient = ()=>{ this._layout(); };
        window.addEventListener('orientationchange', this._onOrient, {passive:true});

        // A ResizeObserver catches box changes that 'resize' never fires for: the toolbar
        // wrapping to a different number of rows, focus mode hiding it, or the status strip
        // growing a line. Debounced through rAF so a burst of layout ticks coalesces into
        // one re-fit. This is what makes the compact toolbar actually enlarge the image
        // instead of just widening the letterbox bars.
        if(typeof ResizeObserver!=='undefined'){
          this._ro = new ResizeObserver(()=>{
            if(this._roRAF) return;
            this._roRAF = requestAnimationFrame(()=>{
              this._roRAF=0;
              // updateKPI writes --cal-kpi-h, which changes .cal-view padding-bottom and
              // thus this very canvas box — firing us back. Re-fitting on that self-caused
              // tick creates a feedback loop (and, mid-drag, a visible zoom/pan jump), so
              // absorb it: refresh the backing store to stay crisp, but do NOT re-fit.
              if(this._suppressRefit){ this._suppressRefit=false; this.vp._updateBackingStore(); this.requestDraw(); return; }
              this._onBoxResize();
            });
          });
          try{ this._ro.observe(this.canvas); }catch(e){}
        }
      }

      // Tear down all global listeners + the gesture handlers and stop any pending
      // redraw. The app's core loop replaces the #viewer subtree on every new upload,
      // so without this each prior overlay (its decoded image, undo stacks and 4 window
      // listeners) would leak permanently and keep reacting to keystrokes/resize.
      destroy(){
        if(this._destroyed) return;
        this._destroyed=true;
        // G.attach() returns { detach: fn } — call .detach(), not the object itself.
        try{ this._gestureDetach && this._gestureDetach.detach && this._gestureDetach.detach(); }catch(e){}
        window.removeEventListener('keydown', this._onKeyDown);
        window.removeEventListener('keyup', this._onKeyUp);
        window.removeEventListener('resize', this._onResize);
        window.removeEventListener('orientationchange', this._onOrient);
        if(this._onDblClick && this.canvas) this.canvas.removeEventListener('dblclick', this._onDblClick);
        if(this._ro){ try{ this._ro.disconnect(); }catch(e){} this._ro=null; }
        if(this._roRAF){ cancelAnimationFrame(this._roRAF); this._roRAF=0; }
        if(this._rAF){ cancelAnimationFrame(this._rAF); this._rAF=0; }
        // Tear down the document/window listeners UI.build() attached (save-menu
        // outside-click, toolbar scroll-cue resize) so they don't accumulate per upload.
        try{ this._uiCleanup && this._uiCleanup(); }catch(e){}
        // Remove the floating, body-appended UI this overlay spawned. These live OUTSIDE the
        // .cal-view subtree, so node-reuse/reapRemoved never clean them; the Detail slider is
        // the worst — it carries a live listener bound to THIS (now dead) overlay, so a stray
        // drag would fire retraceDetail() on a discarded store. Retire them all here.
        try{ this._dismissTraceDetail(); }catch(e){}
        if(this._armToast){ try{ this._armToast.dismiss(); }catch(e){} this._armToast=null; }
        ['cal-scale-input', 'cal-rect-input', 'cal-verify', 'cal-paper-offer', 'cal-quality']
          .forEach(id=>{ const n=document.getElementById(id); if(n) n.remove(); });
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
        const tol = this.vp.pxToImg(this.opts.snapPx*this.vp.dpr);
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
        this.ann.hitTolPx = this.vp.pxToImg(18*this.vp.dpr);
        const c=this.ctx;
        // Paint the whole backing store with the base surface tone before drawing the
        // image, so the letterbox bars match the app's chrome instead of the hard pure
        // black an {alpha:false} clearRect leaves.
        c.setTransform(1,0,0,1,0,0);
        c.fillStyle='#0d0d0f';
        c.fillRect(0,0,this.canvas.width,this.canvas.height);
        this.vp.applyToContext(c);

        c.drawImage(this.img,0,0);
        if(this.opts.showGrid) Draw.drawGrid(c, this.img.width, this.img.height);
        // An unconfirmed "square-ish shape" stops being worth drawing once the user has set a
        // scale of their own: it was only ever a candidate, and outlined in the reference
        // colour it reads as if it were still the scale.
        const ownScale = this.opts.manualMmPerPx || this.opts.manualHomography;
        const unconfirmed = this.data && this.data.calibration_usable===false && !this._markerConfirmed;
        if(this.opts.showMarkers && !(ownScale && unconfirmed)) Draw.drawMarkers(c, this.canvas, this.data, this.opts.linePx);
        if(Draw.resetLabels) Draw.resetLabels();   // start a fresh label-placement frame
        this._drawAnnotations();
        this._drawPreview();
        this._drawDetectedRect();
        this._drawLoupe();

        this.updateKPI();
      }

      // Vertex-handle radius in IMAGE units that renders at a CONSTANT screen size
      // (opts.handlePx css px) regardless of zoom. Fixed image-space dots grew on screen as you
      // zoomed in — covering the very edge you're aligning to; this keeps them small and precise.
      _handleR(mult){
        return this.vp.pxToImg((this.opts.handlePx || 5) * (mult || 1) * this.vp.dpr);
      }
      // Labels likewise: a constant on-screen size (about 15 px text). Sized in image pixels
      // they grew with the zoom until, zoomed in to check an outline's dots, one label covered
      // the very dots being checked. (The PNG export keeps its own fixed labelScale.)
      // Lines too: sized in image pixels, a selected outline was ~35 px wide on screen when
      // zoomed in, hiding the edge its dots were being lined up against.
      _lineW(mult){
        return this.vp.pxToImg((this.opts.linePx || 3) * 0.8 * (mult || 1) * (this.vp.dpr || 1));
      }
      _lblScale(){
        return this.vp.pxToImg((this.opts.labelScale || 1.35) * 0.5 * (this.vp.dpr || 1));
      }

      _drawAnnotations(){
        if(!this.opts.showAnn) return;
        const c=this.ctx, unit=Units.get(this.opts.units);
        const C=Draw.colors;
        const dotR = this._handleR(), linePx=this._lineW();
        const showLabels = this.opts.showLabels !== false;
        // While a dot is being dragged, labels step back so nothing hides what is being lined up.
        if(Draw.setLabelAlpha) Draw.setLabelAlpha((this.drag || this._selDrag!=null) ? 0.18 : 1);
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
            for(const [x,y] of [a.a,a.b]){ c.beginPath(); c.arc(x,y,dotR,0,Math.PI*2); c.fill(); c.strokeStyle="#000"; c.lineWidth=this._lineW(0.6); c.stroke(); }
            const mid=[(a.a[0]+a.b[0])/2,(a.a[1]+a.b[1])/2];
            if(showLabels) Draw.boxLabel(c, this.canvas, mid[0], mid[1], this._fmtLen(mm, pxLen), this._lblScale(), C.segment);
          } else if(a.type==='note'){
            const [tx,ty]=a.p; c.fillStyle=sel?C.selected:C.note;
            c.beginPath(); c.arc(tx,ty,this._handleR(1.6),0,Math.PI*2); c.fill();
            c.strokeStyle="#000"; c.lineWidth=this._lineW(0.6); c.stroke();
            if(a.text && showLabels){ const f=Math.round(18*this._lblScale()); const pad=8*this._lblScale(); const lx=tx+14;
              c.font=Draw.font(f); c.textAlign="left"; c.textBaseline="middle";
              const boxW=c.measureText(a.text).width + 2*pad, boxH=f+2*pad, ly=ty-boxH/2;
              c.fillStyle="rgba(0,0,0,.72)"; c.fillRect(lx,ly,boxW,boxH);
              c.strokeStyle="rgba(255,255,255,.35)"; c.lineWidth=this._lineW(0.5); c.strokeRect(lx,ly,boxW,boxH);
              c.fillStyle="#fff"; c.fillText(a.text, lx+pad, ty);
            }
          } else if(a.type==='polyline'){
            const pts=a.pts||[]; if(pts.length<2) continue;
            const mctx=this._measureCtx(a);
            const closed=!!a.closed && pts.length>=3;
            const mm=Measure.polyline(mctx, pts, closed);
            let pxLen=0; for(let i=1;i<pts.length;i++) pxLen+=Math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]);
            if(closed) pxLen+=Math.hypot(pts[0][0]-pts[pts.length-1][0], pts[0][1]-pts[pts.length-1][1]);
            c.lineWidth=sel?selW:linePx; c.strokeStyle=sel?C.selected:C.polyline;
            c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); for(let i=1;i<pts.length;i++) c.lineTo(pts[i][0], pts[i][1]);
            if(closed){ c.closePath(); c.save(); c.globalAlpha=0.12; c.fillStyle=sel?C.selected:C.polyline; c.fill(); c.restore(); }
            c.stroke();
            c.fillStyle=sel?C.selected:C.polyline;
            // Handles: constant small screen size; the SELECTED outline's are a touch larger so
            // the active one is easy to grab. The generous hit tolerance (hitTolPx) makes even
            // small dots easy to catch, so they can stay unobtrusive over a dense outline.
            const hR = sel ? this._handleR(1.5) : dotR, ringW = this._lineW(0.5);
            for(const [x,y] of pts){ c.beginPath(); c.arc(x,y,hR,0,Math.PI*2); c.fill(); c.strokeStyle="#000"; c.lineWidth=ringW; c.stroke(); }
            if(showLabels){
              let label=this._fmtLen(mm, pxLen);
              if(closed){
                const unit=Units.get(this.opts.units);
                label = this.isCalibrated()
                  ? `⬡ peri ${this._fmtLen(mm, pxLen)} · A ${unit.areaFromMM2(Measure.polygonArea(mctx, pts)).toFixed(1)} ${unit.areaLabel}`
                  : `⬡ outline`;
              }
              // Anchor the label above the shape's bounding box (top-centre), NOT on a middle
              // vertex — so it floats clear above the part instead of covering the points you edit.
              let ax, ay;
              if(closed){
                let minX=Infinity,maxX=-Infinity,minY=Infinity;
                for(const [x,y] of pts){ if(x<minX)minX=x; if(x>maxX)maxX=x; if(y<minY)minY=y; }
                ax=(minX+maxX)/2; ay=minY;
              }else{ const m=pts[Math.floor(pts.length/2)]; ax=m[0]; ay=m[1]; }
              Draw.boxLabel(c, this.canvas, ax, ay, label, this._lblScale(), C.polyline);
            }
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
            if(showLabels) Draw.boxLabel(c, this.canvas, (x1+x2)/2, y1-10, rlabel, this._lblScale(), C.rectangle);
          } else if(a.type==='angle'){
            c.lineWidth=sel?selW:linePx; c.strokeStyle=sel?C.selected:C.angle;
            c.beginPath(); c.moveTo(a.v[0],a.v[1]); c.lineTo(a.a[0],a.a[1]); c.moveTo(a.v[0],a.v[1]); c.lineTo(a.b[0],a.b[1]); c.stroke();
            const ang=Measure.angle(this._measureCtx(a), a.a, a.v, a.b); if(showLabels) Draw.boxLabel(c, this.canvas, a.v[0], a.v[1]-20, `θ ${ang.toFixed(2)}°`, this._lblScale(), C.angle);
          } else if(a.type==='circle'){
            // Shared with the PNG exporter so the live canvas and the export never
            // drift (line thickness, label size/position all come from opts here).
            if(window.CalibCircles){
              window.CalibCircles.drawCircle(c, this.canvas, a, this._dataForMeasure(), this.opts.units,
                { selected: sel, labelScale: this._lblScale(), linePx: this.opts.linePx, lineW: this._lineW(),
                  fallbackScale: this.getScale(), allowHomography: this._allowHomography(),
                  showLabel: showLabels, handleR: this._handleR(1.2) });
            }
          }
        }
      }

      _drawPreview(){
        if(this.selectedPoints.length===0 && !this.hover) return;
        const c=this.ctx, unit=Units.get(this.opts.units);
        const linePx=this._lineW(), dotR=this._handleR();
        // Snap cursor: when snapping is on, ring the point the next click will land on
        // (marker corner or an existing measurement's endpoint) so chaining is visible.
        if(this.opts.snap && this.hover && this.opts.mode!=='pan' && this.opts.mode!=='select'){
          c.save(); c.strokeStyle='rgba(0,212,255,.95)'; c.lineWidth=this._lineW(0.6);
          c.beginPath(); c.arc(this.hover[0], this.hover[1], this._handleR(1.6), 0, Math.PI*2); c.stroke();
          c.restore();
        }
        c.fillStyle='orange'; c.strokeStyle='rgba(255,200,0,.9)'; c.lineWidth=linePx;
        for(const [x,y] of this.selectedPoints){ c.beginPath(); c.arc(x,y,dotR,0,Math.PI*2); c.fill(); c.strokeStyle='#000'; c.lineWidth=this._lineW(0.6); c.stroke(); }
        const H=this.hover;
        if((this.opts.mode==='segment'||this.opts.mode==='setscale'||this.opts.mode==='verifyscale') && this.selectedPoints.length===1 && H){ const a=this.selectedPoints[0], b=H;
          c.save(); c.setLineDash([10,8]); c.beginPath(); c.moveTo(a[0],a[1]); c.lineTo(b[0],b[1]); c.stroke(); c.restore();
          const mid=[(a[0]+b[0])/2,(a[1]+b[1])/2];
          if(this.opts.mode==='setscale'){
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `set scale — click 2nd point`, this._lblScale());
          } else if(this.opts.mode==='verifyscale'){
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `verify — click 2nd point`, this._lblScale());
          } else {
            const mm=Measure.length(this._measureCtx(null), a[0],a[1], b[0],b[1]);
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `~${this._fmtLen(mm, Math.hypot(b[0]-a[0], b[1]-a[1]))}`, this._lblScale());
          }
        }
        else if((this.opts.mode==='polyline' || this.opts.mode==='outline') && this.selectedPoints.length>=1){ const pts=H?[...this.selectedPoints,H]:[...this.selectedPoints];
          c.save(); c.setLineDash([10,8]); c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); for(let i=1;i<pts.length;i++) c.lineTo(pts[i][0], pts[i][1]);
          if(this.opts.mode==='outline' && this.selectedPoints.length>=3) c.closePath();   // preview the closing edge
          c.stroke(); c.restore();
        }
        else if(this.opts.mode==='setscalerect' && this.selectedPoints.length>=1){ const pts=H?[...this.selectedPoints,H]:[...this.selectedPoints];
          c.save(); c.setLineDash([10,8]); c.strokeStyle='rgba(0,212,255,.95)';
          c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); for(let i=1;i<pts.length;i++) c.lineTo(pts[i][0], pts[i][1]);
          if(this.selectedPoints.length>=3) c.closePath();   // show the closing edge once the shape is readable
          c.stroke(); c.restore();
          const need=4-this.selectedPoints.length;
          const mid=this.selectedPoints[0];
          Draw.boxLabel(c, this.canvas, mid[0], mid[1], need>0?`tap ${need} more corner${need===1?'':'s'}`:`pick paper size`, this._lblScale());
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
          Draw.boxLabel(c, this.canvas, center[0], center[1]-radius-15, `⌀ ~${this._fmtLen(2*radius*sMM, 2*radius)}`, this._lblScale());
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
              Draw.boxLabel(c, this.canvas, cx, cy-r-15, `⌀ ~${this._fmtLen(2*r*sMM, 2*r)}`, this._lblScale());
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
        // An UNVERIFIED detection is "a dark square-ish thing", not "the calibration target".
        // Applying it as a scale is how a photo containing no reference at all ends up
        // quoting millimetres: across six real customer photos of one part, with no marker
        // in any of them, this path produced scales spanning 6.6x. Measure in pixels and ask
        // for a scale instead — unless the user has confirmed the object themselves.
        if(!this._markerConfirmed && this.data.calibration_usable === false) return 0;
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
        return Measure.context(this._dataForMeasure(), fallback, this._allowHomography());
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
      // Perspective-aware readout for ONE annotation: {label,text,copy}. Shared by the
      // status strip, copy-to-clipboard, and the submission packet so all three agree.
      _readoutFor(a){
        if(!a) return null;
        const unit=Units.get(this.opts.units), ctx=this._measureCtx(a);
        const cal=this.isCalibrated();
        if(a.type==='segment'){ const px=Math.hypot(a.b[0]-a.a[0],a.b[1]-a.a[1]);
          const lmm=Measure.length(ctx,a.a[0],a.a[1],a.b[0],a.b[1]);
          const t=this._fmtLen(lmm,px);
          // `mm` is the raw millimetre value, kept separate from the formatted text so
          // another VIEW of the same part can reuse this dimension as its scale reference.
          return {label:'Length', text:t, mm:(cal?lmm:null),
                  copy:cal?unit.fromMM(lmm).toFixed(3):String(Math.round(px))}; }
        if(a.type==='polyline'){ const pts=a.pts||[]; const closed=!!a.closed && pts.length>=3;
          let px=0; for(let i=1;i<pts.length;i++) px+=Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]);
          if(closed) px+=Math.hypot(pts[0][0]-pts[pts.length-1][0], pts[0][1]-pts[pts.length-1][1]);
          const mm=Measure.polyline(ctx,pts,closed);
          if(closed){
            if(cal){ const ar=unit.areaFromMM2(Measure.polygonArea(ctx,pts));
              // A closed outline with a depth is no longer a flat profile — it is a solid.
              // Say so, because that is the whole description a modeller needs for an
              // extruded part (a running board, trim, moulding, a bracket).
              if(a.depth_mm>0){
                const d=unit.fromMM(a.depth_mm), vol=unit.areaFromMM2(Measure.polygonArea(ctx,pts))*d;
                return {label:'Extrusion',
                        text:`profile ${this._fmtLen(mm,px)} · A ${ar.toFixed(3)} ${unit.areaLabel} · extruded ${d.toFixed(2)} ${unit.label} · vol ${vol.toFixed(1)} ${unit.areaLabel}·${unit.label}`,
                        copy:d.toFixed(3)};
              }
              return {label:'Outline', text:`peri ${this._fmtLen(mm,px)} · A ${ar.toFixed(3)} ${unit.areaLabel}`, copy:unit.fromMM(mm).toFixed(3)}; }
            let pa=0; for(let i=0;i<pts.length;i++){ const q0=pts[i], q1=pts[(i+1)%pts.length]; pa+=q0[0]*q1[1]-q1[0]*q0[1]; } pa=Math.abs(pa)/2;
            return {label:'Outline', text:`peri ${Math.round(px)} px · A ${Math.round(pa)} px²`, copy:String(Math.round(px))};
          }
          return {label:'Path', text:this._fmtLen(mm,px), copy:cal?unit.fromMM(mm).toFixed(3):String(Math.round(px))}; }
        if(a.type==='rectangle'){ const [x1,y1,x2,y2]=a.rect; const rm=Measure.rect(ctx,x1,y1,x2,y2);
          if(cal){ const w=unit.fromMM(rm.w),h=unit.fromMM(rm.h),ar=unit.areaFromMM2(rm.area); return {label:'Rect', text:`${w.toFixed(3)}×${h.toFixed(3)} ${unit.label} · A ${ar.toFixed(3)} ${unit.areaLabel}`, copy:`${w.toFixed(3)}x${h.toFixed(3)}`}; }
          const pw=Math.abs(x2-x1),ph=Math.abs(y2-y1); return {label:'Rect', text:`${Math.round(pw)}×${Math.round(ph)} px · A ${Math.round(pw*ph)} px²`, copy:`${Math.round(pw)}x${Math.round(ph)}`}; }
        if(a.type==='angle'){ const ang=Measure.angle(ctx,a.a,a.v,a.b); return {label:'Angle', text:`${ang.toFixed(2)}°`, copy:ang.toFixed(2)}; }
        if(a.type==='circle'){ const cm=Measure.circle(ctx, a); let t=this._fmtLen(cm.diameterMM, 2*a.radius);
          // A 3-point circle is tilt-corrected (rim points refit on the plane); a 2-point
          // circle can't be, so flag it as approximate on a tilted shot and point at 3-pt.
          if(cal && cm.corrected) t += ' • ⟂ tilt-corrected';
          else if(cal && this._perspectiveActive()) t += ' • ⌀ approx on tilt — use 3-pt';
          return {label:'⌀', text:t, copy:cal?unit.fromMM(cm.diameterMM).toFixed(3):String(Math.round(2*a.radius))}; }
        if(a.type==='note'){ return {label:'Note', text:(a.text||''), copy:(a.text||'')}; }
        return null;
      }
      _selectedReadout(){ return this._readoutFor(this.ann.items.find(it=>it.id===this.ann.selectedId)); }
      _copySelected(){
        const rd=this._selectedReadout(); if(!rd) return;
        try{ if(navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(rd.copy); }catch(e){}
      }

      // ---- Submission (multi-view job) capture -------------------------------------
      // Every measurement on this view, as [{label,text}], for the review + email packet.
      getMeasurements(){ return this.ann.items.map(a=>this._readoutFor(a)).filter(Boolean)
        .map(r=>{ const o={label:r.label, text:r.text}; if(r.mm>0) o.mm=r.mm; return o; }); }
      // --- Is the scale reference big enough for what is being measured? ----------------
      // A homography is only trustworthy near the points that fixed it. Extrapolate far
      // outside the reference and the error explodes: measured on synthetic renders at 30 deg
      // tilt with 1 px corner noise, a true 300 mm feature came back with an RMS error of
      //   reference 1/5 of the measured span -> ~6 mm        (fine)
      //   reference 1/13 of the span         -> ~23 mm       (marginal)
      //   reference 1/25 of the span         -> metres        (nonsense)
      // So a 40 mm calibration card against a 1.5 m running board — 1/38 — produces numbers
      // that look plausible and are meaningless. Nothing warned about that before.
      // The reference's extent in PIXELS, whatever supplied the scale.
      _referenceExtentPx(){
        if(this._refExtentPx > 0) return this._refExtentPx;
        const m = (this.data && this.data.markers || [])[0];
        if(m){
          if(m.edge_px > 0) return m.edge_px;
          const px = this._markerEdgePx && this._markerEdgePx(m);
          if(px > 0) return px;
        }
        return 0;
      }

      // null when there is nothing to say; otherwise {level:'warn'|'bad', ratio, text}.
      // The arithmetic lives in CalibGeom so it can be unit-tested without a DOM.
      referenceSpanWarning(){
        const v = Geom.referenceSpanVerdict(this._referenceExtentPx(),
                                            Geom.measuredExtentPx(this.ann && this.ann.items));
        if(!v) return null;
        const ratio = v.ratio, bad = v.level === 'bad';
        return {
          level: bad ? 'bad' : 'warn',
          ratio: ratio,
          text: `You are measuring about ${Math.round(ratio)}x the size of your scale reference. `
              + (bad ? 'These numbers are not trustworthy at this range. '
                     : 'Accuracy falls off this far from the reference. ')
              + 'Use a bigger reference that covers more of the part — with a ruler, lay out a '
              + 'rectangle (e.g. 300 x 300 mm) and use Set scale > a rectangle you measured.'
        };
      }

      // --- Extruded profiles -------------------------------------------------------------
      // A traced closed outline plus one depth IS the part, for anything made by extrusion —
      // which is most replacement trim: running boards, mouldings, sills, brackets. Without
      // this the app can only ever describe a flat silhouette, and the modeller has to carry
      // the depth across from a separate measurement by hand and hope it was the right one.
      // Returns true when the depth was applied.
      setExtrusionDepth(mm){
        const a = this.ann.items.find(it => it.id === this.ann.selectedId);
        if(!a || a.type !== 'polyline' || !a.closed) return false;
        const d = Number(mm);
        this._pushHistory();
        if(Number.isFinite(d) && d > 0) a.depth_mm = d; else { delete a.depth_mm; delete a.wall_mm; }
        this.requestDraw();
        return true;
      }
      // Hollow the selected profile into a cover: a plate this thick with a wall this thick
      // round its edge, open underneath. 0 / blank goes back to a solid extrusion. The server
      // enforces the printable minimum and says why when it refuses, so this only stores.
      setWallThickness(mm){
        const a = this.ann.items.find(it => it.id === this.ann.selectedId);
        if(!a || a.type !== 'polyline' || !a.closed) return false;
        const w = Number(mm);
        this._pushHistory();
        if(Number.isFinite(w) && w > 0) a.wall_mm = w; else delete a.wall_mm;
        this.requestDraw();
        return true;
      }
      // The selected closed outline as a 3D-part view: exactly the geometry the DXF/STL
      // exports send (tilt-corrected millimetres when a perspective calibration exists), so a
      // part built from several photos uses the same numbers every other export does.
      // Returns {points, mm_per_px, image_height, size_mm:[w,h]} or null.
      // The selected closed outline as a profile in mm — or, given `id`, that outline (a 3D
      // view follows the outline it was assigned from; see calib.part3d.js sync).
      partProfile(id){
        const a = (id != null) ? this.ann.items.find(it => it.id === id && it.type === 'polyline' && it.closed)
                               : this.selectedProfile();
        if(!a) return null;
        const req = Xport._buildDXFRequest(this._dataForMeasure(), { items:[a] },
                      { image_height:this._imgH(), mm_per_px:this.getScale()||this.data?.mm_per_px||0,
                        allowHomography:this._allowHomography() });
        const g = (req.geometry||[]).find(x => x && x.type==='polyline' && x.closed);
        if(!g || !Array.isArray(g.points) || g.points.length < 3) return null;
        const s = Number(g.mm_per_px) || 1;
        const xs = g.points.map(p => p[0]*s), ys = g.points.map(p => p[1]*s);
        return { points: g.points, mm_per_px: s, image_height: req.image_height || 0, ann_id: a.id,
                 size_mm: [Math.max(...xs)-Math.min(...xs), Math.max(...ys)-Math.min(...ys)] };
      }
      // The selected item when it is a closed profile that can take a depth, else null.
      selectedProfile(){
        const a = this.ann.items.find(it => it.id === this.ann.selectedId);
        return (a && a.type === 'polyline' && a.closed) ? a : null;
      }

      // The user telling us the detected object really IS their calibration square. Keeps a
      // genuine marker in poor light from being a dead end, while still refusing to assume it.
      confirmDetectedMarker(sizeMM){
        const mm = parseFloat(sizeMM);
        if(!(mm > 0)) return false;
        const m = (this.data && this.data.markers || [])[0];
        if(!m) return false;
        this._markerConfirmed = true;
        this.setMarkerSizeMM(mm);          // rescales from the detected edge in pixels
        this.updateKPI(); this.requestDraw();
        return true;
      }
      // True when the server found something square but could not confirm it is the target.
      hasUnconfirmedDetection(){
        return !this._markerConfirmed
            && !!(this.data && this.data.calibration_usable === false
                  && (this.data.markers || []).length);
      }

      // A plain-language description of how THIS view is scaled, so the shop can trust it.
      getScaleInfo(){
        const s=this.getScale()||0; const unit=Units.get(this.opts.units);
        let source='none';
        if(this.opts.manualHomography) source=(this._manualRef && this._manualRef.kind==='squared')
          ? 'ruler, squared up from parallel sides' : 'paper/rectangle';   // perspective flag conveys tilt-correction
        else if(this.opts.manualMmPerPx) source='manual line';
        else if(this.currentMarkerSizeMM()) source=`printed square ${this.currentMarkerSizeMM()} mm`;
        const span=this.referenceSpanWarning();
        return { calibrated:s>0, mm_per_px:s, unit:unit.label,
                 perspective:this._perspectiveActive(), source,
                 confidence:(this.data&&this.data.calibration_confidence)||null,
                 referenceSpanRatio: span ? Math.round(span.ratio*10)/10 : null,
                 referenceSpanLevel: span ? span.level : null };
      }
      // The annotated image as a downscaled JPEG data URL (long side <= maxDim) — small
      // enough to POST several per submission, clear enough to show the measurements.
      annotatedDataURL(maxDim){
        try{ return Xport.renderAnnotatedDataURL(this.img, this._dataForMeasure(), this._exportStore(),
                 { showGrid:this.opts.showGrid, showMarkers:this.opts.showMarkers, units:this.opts.units,
                   labelScale:this.opts.labelScale, linePx:this.opts.linePx,
                   allowHomography:this._allowHomography(), maxDim:maxDim||1600 }); }
        catch(e){ return null; }
      }
      // One view's complete record for the submission packet.
      getViewRecord(label){
        const rec = { label:label||'', image:this.annotatedDataURL(1600),
                      scale:this.getScaleInfo(), measurements:this.getMeasurements(),
                      units:this.opts.units, capturedAt:null };
        // Machine-readable geometry (mm, CAD Y-up) + a dimension-table CSV, captured now
        // while the annotations are live. This is what makes the submission CAD-ready: the
        // geometry is exactly the DXF/solid input, so we don't re-measure from a photo later.
        try{
          const scale = this.getScale() || (this.data && this.data.mm_per_px) || 0;
          rec.geometry = Xport._buildDXFRequest(this._dataForMeasure(), this._exportStore(),
                           { image_height:this._imgH(), mm_per_px:scale, allowHomography:this._allowHomography() });
          rec.csv = Xport.buildCSV(this._dataForMeasure(), this._exportStore(), this.opts.units, scale, this._allowHomography());
        }catch(e){ /* geometry is best-effort; the image + measurements still submit */ }
        // Full reloadable snapshot (raw image + calibration + editable annotations) so this
        // exact view can be re-OPENED and modelled later, not just re-measured from a photo.
        try{ rec.restore = this.getRestoreState(1600); }catch(e){ /* best-effort */ }
        return rec;
      }

      // ---- Reloadable per-view state (the Save / Load job round-trip) ------------------
      // The raw (un-annotated) image as a downscaled JPEG data URL — a clean surface to
      // re-edit on (the annotated image would bake the marks in).
      rawDataURL(maxDim){
        try{
          const W=this.img.naturalWidth||this.img.width, H=this.img.naturalHeight||this.img.height;
          const s=Math.min(1,(maxDim||1600)/Math.max(W,H));
          const cw=Math.max(1,Math.round(W*s)), ch=Math.max(1,Math.round(H*s));
          const cnv=document.createElement('canvas'); cnv.width=cw; cnv.height=ch;
          cnv.getContext('2d').drawImage(this.img,0,0,cw,ch);
          return cnv.toDataURL('image/jpeg',0.85);
        }catch(e){ return null; }
      }

      // A complete, reloadable snapshot of THIS view in ONE pixel space: the raw image plus
      // the calibration, the editable annotation layer, and any manual scale overrides.
      // Downscaled to keep the bundle small (localStorage / e-mail / postMessage-friendly);
      // calibration and annotations are scaled by the SAME factor, so a reloaded view
      // measures IDENTICALLY — real millimetres are preserved, not just pixel positions.
      getRestoreState(maxDim){
        if(!this.img) return null;
        const W=this.img.naturalWidth||this.img.width, H=this.img.naturalHeight||this.img.height;
        if(!W||!H) return null;
        const md=maxDim||1600, s=Math.min(1,md/Math.max(W,H));
        const raw=this.rawDataURL(md);
        if(!raw) return null;
        return {
          v:1, w:Math.round(W*s), h:Math.round(H*s),
          raw:raw,
          calib:this._scaleCalib(JSON.parse(JSON.stringify(this.data||{})), s),
          ann:(this.ann.items||[]).map(a=>this._scaleAnnItem(JSON.parse(JSON.stringify(a)), s)),
          units:this.opts.units,
          manual:{
            mmPerPx:(this.opts.manualMmPerPx>0 ? this.opts.manualMmPerPx/s : null),
            homography:(this.opts.manualHomography ? this._scaleHomography(this.opts.manualHomography, s) : null),
            ref:this._scaleRef(this._manualRef, s),
            line:(this._scaleLine ? { a:[this._scaleLine.a[0]*s, this._scaleLine.a[1]*s],
                                      b:[this._scaleLine.b[0]*s, this._scaleLine.b[1]*s], mm:this._scaleLine.mm } : null),
            // A marker the user confirmed by hand, and the reference's size for the
            // too-small-reference warning: neither is in the calibration JSON.
            markerConfirmed:!!this._markerConfirmed,
            extentPx:(this._refExtentPx>0 ? this._refExtentPx*s : null)
          }
        };
      }

      // Apply a snapshot onto THIS freshly-booted overlay. The image + calibration already
      // arrived via the .cal-view data-img/data-json the loader read; here we restore the
      // annotation layer and the manual scale overrides (which don't live in the calibration
      // JSON), then redraw. Everything is in the snapshot's downscaled space already, so
      // there is no scaling to do at load time.
      _applyRestore(state){
        try{
          if(!state) return;
          if(state.units) this.opts.units=state.units;
          const m=state.manual||{};
          this.opts.manualMmPerPx=(m.mmPerPx>0?m.mmPerPx:null);
          this.opts.manualHomography=(m.homography||null);
          this._manualRef=(m.ref||null);
          this._scaleLine=(m.line && Array.isArray(m.line.a) && Array.isArray(m.line.b) && m.line.mm>0) ? m.line : null;
          this._markerConfirmed=!!m.markerConfirmed;
          this._refExtentPx=(m.extentPx>0 ? m.extentPx : null);
          if(Array.isArray(state.ann)){
            this.ann.items=state.ann.map(a=>JSON.parse(JSON.stringify(a)));
            Ann.reserveIds(this.ann.items);   // advance the id counter past restored ids (no collisions)
          }
          this.ann.selectedId=null; this.selectedPoints=[]; this.hover=null;
          this._undoStack=[]; this._redoStack=[];
          this.requestDraw();
        }catch(e){ /* a partial restore still shows the image + calibration */ }
      }

      // H maps image px -> (unit square | plane mm). For downscaled px' = s*px we need
      // H'(px') == H(px); since [x,y,1] = diag(1/s,1/s,1)*[x',y',1], H' = H*diag(1/s,1/s,1)
      // — i.e. scale the first two COLUMNS by 1/s. (s === 1 leaves H untouched.)
      _scaleHomography(Hm, s){
        if(s===1 || !Array.isArray(Hm)) return Hm;
        try{ return Hm.map(row=>{ const r=row.slice(); r[0]=r[0]/s; r[1]=r[1]/s; return r; }); }
        catch(e){ return Hm; }
      }
      _scalePts(pts, s){ return (s===1||!Array.isArray(pts))?pts:pts.map(p=>Array.isArray(p)?[p[0]*s,p[1]*s]:p); }
      _scaleRef(ref, s){
        if(!ref || s===1) return ref||null;
        try{
          const r=JSON.parse(JSON.stringify(ref));
          ['corners','pts','points','quad'].forEach(k=>{ if(Array.isArray(r[k])) r[k]=this._scalePts(r[k], s); });
          return r;
        }catch(e){ return ref; }
      }
      _scaleCalib(c, s){
        if(!c || s===1) return c;
        try{
          if(c.image_size){ if(c.image_size.width) c.image_size.width*=s; if(c.image_size.height) c.image_size.height*=s; }
          if(typeof c.mm_per_px==='number' && c.mm_per_px>0) c.mm_per_px=c.mm_per_px/s;
          // pixels_per_mm is the reciprocal of mm_per_px; scale it to match (px shrink by s ->
          // fewer px per mm) so the exported calibration JSON stays internally consistent.
          if(typeof c.pixels_per_mm==='number' && c.pixels_per_mm>0) c.pixels_per_mm=c.pixels_per_mm*s;
          if(Array.isArray(c.homography)) c.homography=this._scaleHomography(c.homography, s);
          (c.markers||[]).forEach(m=>{
            if(Array.isArray(m.corners)) m.corners=m.corners.map(pt=>('x'in pt)?Object.assign({},pt,{x:pt.x*s,y:pt.y*s}):[pt[0]*s,pt[1]*s]);
            if(m.center){ if('x'in m.center){ m.center.x*=s; m.center.y*=s; } else if(Array.isArray(m.center)){ m.center=[m.center[0]*s,m.center[1]*s]; } }
            if(typeof m.edge_px==='number') m.edge_px*=s;
            if(typeof m.mm_per_px==='number' && m.mm_per_px>0) m.mm_per_px=m.mm_per_px/s;
          });
          // The focal length is in pixels too. Left unscaled while the marker's edge_px was
          // scaled, a reopened side's camera distance came out 1/s too far.
          if(c.camera && typeof c.camera.focal_px==='number' && c.camera.focal_px>0) c.camera.focal_px*=s;
          return c;
        }catch(e){ return c; }
      }
      _scaleAnnItem(a, s){
        if(!a || s===1) return a;
        try{
          ['a','b','v','p','center'].forEach(k=>{ if(Array.isArray(a[k])) a[k]=[a[k][0]*s,a[k][1]*s]; });
          if(Array.isArray(a.pts)) a.pts=a.pts.map(p=>[p[0]*s,p[1]*s]);
          if(Array.isArray(a.rect)) a.rect=a.rect.map(v=>v*s);
          if(typeof a.radius==='number') a.radius=a.radius*s;
          if(typeof a.mm_per_px==='number' && a.mm_per_px>0) a.mm_per_px=a.mm_per_px/s;
          return a;
        }catch(e){ return a; }
      }

      // After the calibration changes (cube size or manual scale), refresh the frozen
      // per-annotation scale so existing measurements update to the new reference.
      _rescaleAnnotations(){
        // When the scale is CLEARED (clearManualScale, or a marker size set to nothing) every
        // _scaleForPoint returns 0, and only writing on s>0 left each annotation holding the
        // scale that was just deleted. Circle and export labels read that frozen value first
        // (`a.mm_per_px || fallback || ...`), so they kept quoting millimetres derived from a
        // scale the app no longer has, while isCalibrated() reported false. Zero them — but
        // only when there is genuinely no scale anywhere, so an annotation that merely sits
        // away from any marker keeps the value it captured.
        const anyScale = this._scaleForPoint([0,0]) > 0 || (this.data && this.data.mm_per_px > 0);
        for(const a of this.ann.items){
          if(a.type==='note') continue;
          const s=this._scaleForPoint(this._annCentroid(a));
          if(s>0) a.mm_per_px=s;
          else if(!anyScale) a.mm_per_px=0;
        }
      }

      isCalibrated(){ return this.getScale() > 0; }

      // True when linear measurements are being rectified by a homography — either the
      // user's paper-rectangle scale, or the auto marker homography when no manual scale
      // overrides it. A manual LINE scale (manualMmPerPx without manualHomography) is
      // uniform, so it is NOT perspective-corrected. Circles are never rectified.
      _perspectiveActive(){
        return !!this.opts.manualHomography || (!this.opts.manualMmPerPx && !!(this.data && this.data.homography));
      }

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
        this.opts.manualHomography = null; this._manualRef = null; this._scaleLine = null;
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
      setManualScaleFromPixels(pixelLength, knownMM, ends){
        knownMM = parseFloat(knownMM);
        if(!(pixelLength>0) || !(knownMM>0)) return;
        // The line itself, not just its scale: Square up re-reads it on the corrected plane.
        this._scaleLine = (Array.isArray(ends) && ends.length===2) ? { a:ends[0].slice(), b:ends[1].slice(), mm:knownMM } : null;
        this._markerSizeFromMemory = false;
        // A line scale is uniform — drop any paper-rectangle homography so we don't claim
        // tilt correction the single line can't provide.
        this.opts.manualHomography = null; this._manualRef = null;
        this.opts.manualMmPerPx = knownMM/pixelLength;
        this._refExtentPx = pixelLength;        // for the too-small-reference check
        this._rescaleAnnotations();
        this.requestDraw();
      }
      clearManualScale(){
        this.opts.manualMmPerPx = null; this.opts.manualHomography = null; this._manualRef = null; this._scaleLine = null;
        // The raised-plane homography was derived from the calibration that just went away.
        this._planeH = null; this._featureHeightMM = 0;
        this._rescaleAnnotations(); this.requestDraw();
      }
      currentMarkerSizeMM(){ return (this.data && this.data.marker_size_mm) || null; }

      // Commit the in-progress path/outline. An open path needs >=2 points; a closed
      // outline needs >=3 and stores a `closed` flag (→ a closed, extrudable DXF profile).
      finishPolyline(){
        const m=this.opts.mode, closed=(m==='outline');
        if((m==='polyline' || m==='outline') && this.selectedPoints.length>=(closed?3:2)){
          const mm=this.getScale() || (this.data?.mm_per_px ?? 0);
          this._pushHistory();
          Ann.addPolyline(this.ann, this.selectedPoints, mm, this.opts.units, this.opts.lockMarkerId, closed);
        }
        this.selectedPoints=[]; this.hover=null; this.requestDraw();
      }

      // Resolve an API path against the app's request-path prefix (works behind a proxy).
      _apiUrl(path){
        let base='/';
        try{ const cfg=JSON.parse(document.getElementById('_dash-config').textContent); base=cfg.requests_pathname_prefix||'/'; }catch(e){}
        if(base.charAt(base.length-1)!=='/') base+='/';
        return base + String(path).replace(/^\//,'');
      }

      // A small transient status pill (self-contained — the overlay has no toast system).
      // Returns { update(msg, kind), dismiss() }.
      _toast(msg, opts){
        opts=opts||{};
        const el=document.createElement('div');
        el.style.cssText='position:fixed;left:50%;bottom:88px;transform:translateX(-50%);z-index:70;'+
          'background:#12171c;color:#e8eef2;border:1px solid #2a3a44;border-radius:10px;padding:10px 14px;'+
          'font:600 13px Segoe UI,system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.5);max-width:90vw;text-align:center;'+
          // It can sit over the photo: taps go through it to the photo, except on its buttons.
          'pointer-events:none;';
        const text=document.createElement('span'); text.textContent=msg; el.appendChild(text);
        let timer=null;
        // Optional action button ({label, fn}): a message that says "now do X" should let
        // you do X from where you are reading it, not send you hunting through menus.
        // opts.actions: several, the first one highlighted.
        (opts.actions || (opts.action ? [opts.action] : [])).forEach(function(act, i){
          const b=document.createElement('button'); b.type='button'; b.className='cal-toast-action';
          b.textContent=act.label;
          b.style.cssText='margin-left:10px;min-height:34px;padding:5px 12px;border-radius:8px;'+
            (i ? 'border:1px solid #2a3a44;background:#1b2229;color:#e8eef2;' : 'border:none;background:#00d4ff;color:#04222b;')+
            'font:700 13px Segoe UI,system-ui,sans-serif;cursor:pointer;pointer-events:auto;';
          b.onclick=()=>{ api.dismiss(); act.fn(); };
          el.appendChild(b);
        });
        // One message at a time: they all sit in the same spot, so a newer one replaces the
        // last rather than printing over it — page-wide, since each photo has its own overlay.
        if(window.__calLastToast && window.__calLastToast!==el && window.__calLastToast.parentNode) window.__calLastToast.parentNode.removeChild(window.__calLastToast);
        window.__calLastToast=el;
        document.body.appendChild(el);
        // Above whatever sits along the bottom right now — the selected outline's buttons,
        // the trace's Detail bar — so the message never covers the buttons it talks about.
        // Never higher than the middle of the screen.
        const place=()=>{
          let topY=window.innerHeight-88;
          document.querySelectorAll('.cal-context button.show, #cal-trace-detail').forEach(function(n){
            const r=n.getBoundingClientRect();
            if(r.height>0 && r.bottom>window.innerHeight*0.45 && r.top<topY) topY=r.top;
          });
          el.style.bottom=Math.round(Math.min(window.innerHeight*0.55, window.innerHeight-topY+10))+'px';
        };
        place();
        // The buttons it must clear can appear a moment later (a trace finishing selects
        // the new outline): look again.
        let placer=null;
        const follow=()=>{ clearInterval(placer); placer=setInterval(()=>{ if(!el.parentNode) clearInterval(placer); else place(); }, 400); };
        follow();
        const self=this;
        let gone=false;
        const api={
          // A replaced message that still has news (a trace finishing) takes the spot back.
          update(m, kind){
            if(!gone && !el.parentNode){
              if(window.__calLastToast && window.__calLastToast.parentNode) window.__calLastToast.parentNode.removeChild(window.__calLastToast);
              document.body.appendChild(el); window.__calLastToast=el;
              follow();
            }
            place();
            text.textContent=m; el.style.borderColor = kind==='err' ? '#e06a4a' : (kind==='ok' ? '#3fae74' : '#2a3a44'); },
          dismiss(){ gone=true; clearInterval(placer); if(timer) clearTimeout(timer); if(el.parentNode) el.parentNode.removeChild(el); }
        };
        if(opts.autoMs) timer=setTimeout(api.dismiss, opts.autoMs);
        return api;
      }

      // Arm the next tap (in Outline mode) to auto-trace the part it lands on.
      // "That bit isn't the part." One tap on something wrongly included — almost always a
      // shadow — and the trace runs again as GrabCut with that as background. This is the
      // only thing that fixes a dark part lying next to its own shadow: the two differ by
      // about 15 grey levels, so no threshold can separate them, and the plain trace swallows
      // the shadow whole (measured: 100% of the frame on a real customer photo, 37% once a
      // background tap was supplied).
      armBackgroundTap(){
        if(!Array.isArray(this._lastAutoSeed)){
          this._toast('Trace the part first, then mark what it got wrong.', {autoMs:3000});
          return;
        }
        this._bgTapArm = true;
        if(this._armToast) this._armToast.dismiss();
        this._armToast = this._toast('Tap something that is NOT the part (its shadow, the ground).',
                                     {autoMs:6000});
        this.requestDraw();
      }
      // Record a background tap and immediately re-trace with it.
      addBackgroundTap(pt){
        if(!Array.isArray(pt)) return;
        this._bgTapArm = false;
        if(this._armToast){ this._armToast.dismiss(); this._armToast=null; }
        this._bgTaps = (this._bgTaps||[]).concat([[pt[0], pt[1]]]);
        // The seed itself is the strongest "this IS the part" evidence we have.
        this._fgTaps = Array.isArray(this._lastAutoSeed) ? [this._lastAutoSeed.slice()] : [];
        this._runAutoTrace(this._lastAutoSeed, this._autoSimplify, true);
      }
      backgroundTapCount(){ return (this._bgTaps||[]).length; }
      clearBackgroundTaps(){ this._bgTaps=[]; this._fgTaps=[]; }

      armAutoOutline(){
        if(this.opts.mode!=='outline') this.setMode('outline');
        this.selectedPoints=[]; this.hover=null;
        this._autoOutlineArm=true;
        this._dismissTraceDetail();          // a fresh trace starts — retire the old slider
        if(this._armToast) this._armToast.dismiss();
        this._armToast=this._toast('Tap the part to trace it automatically.', {autoMs:5000});
        this.requestDraw();
      }

      // Auto-trace the part under `seed` (image coords): POST the photo to the server, which
      // segments the tapped object and returns its outer boundary + interior holes; we drop
      // them in as editable closed profiles and switch to Select so any point can be dragged.
      // A Detail slider then lets the user re-trace the SAME spot smoother or simpler.
      autoOutline(seed){
        if(this._armToast){ this._armToast.dismiss(); this._armToast=null; }
        if(!this.img){ return; }
        // A new seed means a new object: the old "not the part" taps described the old one.
        this.clearBackgroundTaps();
        this._lastAutoSeed = Array.isArray(seed) ? seed.slice() : null;
        // An Area box around the part becomes the trace's region-of-interest: the server builds
        // a clean LOCAL background model inside it and ignores everything outside, so distant
        // clutter, a large cast shadow, or a nearby object can't derail the trace.
        this._lastAutoRoi = this._roiForSeed(this._lastAutoSeed);
        if(!(this._autoSimplify > 0)) this._autoSimplify = 0.0006;   // default detail level
        this._runAutoTrace(this._lastAutoSeed, this._autoSimplify, false);
      }

      // Pick the Area (rectangle) annotation to use as the trace ROI: the smallest box that
      // contains the seed, or — if the seed is off any box / absent — the smallest box overall.
      // Returns [x, y, w, h] in image coords, or null when no usable box is drawn.
      _roiForSeed(seed){
        try{
          const norm=r=>{ const x1=Math.min(r[0],r[2]), y1=Math.min(r[1],r[3]),
                                x2=Math.max(r[0],r[2]), y2=Math.max(r[1],r[3]);
                          return [x1, y1, x2-x1, y2-y1]; };
          let cands=((this.ann && this.ann.items) || [])
            .filter(a=>a && a.type==='rectangle' && Array.isArray(a.rect) && a.rect.length===4)
            .map(a=>norm(a.rect)).filter(b=>b[2]>4 && b[3]>4);
          if(!cands.length) return null;
          if(Array.isArray(seed)){
            const inside=cands.filter(b=>seed[0]>=b[0] && seed[0]<=b[0]+b[2] &&
                                         seed[1]>=b[1] && seed[1]<=b[1]+b[3]);
            if(inside.length) cands=inside;
          }
          cands.sort((a,b)=>a[2]*a[3]-b[2]*b[3]);   // most specific (smallest) box wins
          return cands[0];
        }catch(e){ return null; }
      }

      // Re-run the last auto-trace at a new detail level (from the Detail slider), REPLACING
      // the previously auto-added outline + holes. One undo step.
      retraceDetail(simplify){
        if(!Array.isArray(this._lastAutoSeed)){ return; }
        this._autoSimplify = Math.min(0.05, Math.max(0.0003, simplify));
        this._runAutoTrace(this._lastAutoSeed, this._autoSimplify, true);
      }

      // The /api/trace request for `seed` at detail `simplify`, with the Area box and the
      // taps, all in the posted image's coords; sentScale maps results back. null if the
      // photo cannot be read. Shared by the trace and by Refine's full-detail re-trace.
      _traceRequest(seed, simplify){
        const natW=this.img.naturalWidth||this.img.width, natH=this.img.naturalHeight||this.img.height;
        const sentScale=Math.min(1, 1600/Math.max(natW, natH));   // cap the POST size; map results back
        const cw=Math.max(1,Math.round(natW*sentScale)), ch=Math.max(1,Math.round(natH*sentScale));
        const cnv=document.createElement('canvas'); cnv.width=cw; cnv.height=ch;
        cnv.getContext('2d').drawImage(this.img, 0, 0, cw, ch);
        let durl; try{ durl=cnv.toDataURL('image/jpeg', 0.85); }catch(e){ return null; }

        const exclude=[];
        try{
          ((this.data && this.data.markers) || []).forEach(function(m){
            const cs=m.corners||[]; if(cs.length<4) return;
            const xs=cs.map(c=>c.x), ys=cs.map(c=>c.y);
            const x=Math.min.apply(null,xs), y=Math.min.apply(null,ys);
            exclude.push([x*sentScale, y*sentScale, (Math.max.apply(null,xs)-x)*sentScale, (Math.max.apply(null,ys)-y)*sentScale]);
          });
        }catch(e){}
        const seedSent = seed ? [seed[0]*sentScale, seed[1]*sentScale] : null;
        // Hints travel in the SAME sent-image coords as the seed and the ROI; a tap left in
        // full-resolution coords would land somewhere else entirely on the posted image.
        const sent = p => [p[0]*sentScale, p[1]*sentScale];
        // Carry the Area box (if any) as the ROI, in the SAME sent-image coords as the seed.
        let roiSent=null;
        if(Array.isArray(this._lastAutoRoi) && this._lastAutoRoi.length===4){
          const r=this._lastAutoRoi;
          roiSent=[r[0]*sentScale, r[1]*sentScale, r[2]*sentScale, r[3]*sentScale];
        }
        return { sentScale:sentScale, body:{ image:durl, seed:seedSent, exclude:exclude, simplify:simplify, roi:roiSent,
                                             fg:(this._fgTaps||[]).map(sent), bg:(this._bgTaps||[]).map(sent) } };
      }

      _runAutoTrace(seed, simplify, isRetrace){
        if(!this.img){ return; }
        const self=this;
        const req=this._traceRequest(seed, simplify);
        if(!req) return;
        const sentScale=req.sentScale;
        const toast=this._toast(isRetrace ? 'Re-tracing…' : 'Finding the outline…');
        // A photo whose colours mislead the quick trace falls back to a careful one that takes
        // several seconds (6-10 s measured on real customer photos). Say so, so it never
        // looks hung.
        const slowNote=setTimeout(()=>{ try{ toast.update('Still tracing — this photo needs the slower, careful method (a few seconds)…'); }catch(e){} }, 3500);
        fetch(this._apiUrl('api/trace'), { method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify(req.body) })
          .then(r=>{ clearTimeout(slowNote); return r.json(); })
          .then(function(j){
            if(j && j.ok && Array.isArray(j.points) && j.points.length>=3){
              const mm=self.getScale() || (self.data && self.data.mm_per_px) || 0;
              self._pushHistory();
              // On a re-trace, remove what the previous auto-trace added so a detail change
              // REPLACES the outline + holes instead of stacking new geometry on top.
              if(isRetrace && Array.isArray(self._autoAddedIds) && self._autoAddedIds.length){
                const ids=new Set(self._autoAddedIds);
                self.ann.items = self.ann.items.filter(function(a){ return !ids.has(a.id); });
              }
              self._autoAddedIds = [];
              const pts=j.points.map(p=>[p[0]/sentScale, p[1]/sentScale]);   // back to image coords
              Ann.addPolyline(self.ann, pts, mm, self.opts.units, self.opts.lockMarkerId, true);
              self._autoAddedIds.push(self.ann.selectedId);
              self._tracedOuter={ id:self.ann.selectedId, json:JSON.stringify(self.ann.items[self.ann.items.length-1].pts) };
              let nPts=pts.length, nHoles=0;
              // Interior holes come back typed: a round bore as a TRUE circle (perfect in the
              // DXF), a hex / 12-point socket or slot as a corner-kept polygon. Each is added
              // as its own closed profile, so the DXF carries the holes, not just the silhouette.
              (Array.isArray(j.holes)?j.holes:[]).forEach(function(h){
                if(h && h.shape==='circle' && h.r>0){
                  const c=[h.cx/sentScale, h.cy/sentScale], edge=[c[0]+h.r/sentScale, c[1]];
                  Ann.addCircle(self.ann, c, edge, mm, self.opts.units, self.opts.lockMarkerId);
                  self._autoAddedIds.push(self.ann.selectedId); nHoles++;
                }else{
                  const raw=(h && Array.isArray(h.points)) ? h.points : (Array.isArray(h) ? h : null);
                  if(raw && raw.length>=3){
                    const hp=raw.map(p=>[p[0]/sentScale, p[1]/sentScale]);
                    Ann.addPolyline(self.ann, hp, mm, self.opts.units, self.opts.lockMarkerId, true);
                    self._autoAddedIds.push(self.ann.selectedId); nPts+=hp.length; nHoles++;
                  }
                }
              });
              self.setMode('select'); self.requestDraw();
              const tail = nHoles ? (' + '+nHoles+' hole'+(nHoles>1?'s':'')) : '';
              // A shadow across the part is what the plain trace misses most, and the fix is one
              // the user would not guess: an Area box round the part makes everything outside it
              // "not the part", and the shadowed end comes back (see auto_outline._segment).
              const boxed = !!self._lastAutoRoi;
              toast.update('Outline'+tail+' traced'+(boxed ? ' inside your Area box' : '')+' — drag any dot to fix it'+
                (boxed || isRetrace ? ', or use the Detail slider.'
                                    : '. Missed a part in shadow? Draw an Area box round the part, then tap it again.'), 'ok');
              setTimeout(toast.dismiss, isRetrace ? 1400 : (boxed ? 2800 : 6000));
              self._showTraceDetail(nPts, nHoles);
            }else{
              // The server says WHY when it can be specific (a trace covering the whole frame
              // means the tap had no edge to find); fall back to the generic advice otherwise.
              const why = (j && typeof j.hint === 'string' && j.hint) ? j.hint
                        : (isRetrace ? 'Re-trace found nothing — try a different detail level.'
                                     : 'No part found there — tap right on the part, or trace it by hand.');
              toast.update(why, 'err');
              setTimeout(toast.dismiss, (j && j.hint) ? 6000 : 3400);
            }
          })
          .catch(function(){ clearTimeout(slowNote); toast.update('Auto-trace unavailable — trace by hand instead.', 'err'); setTimeout(toast.dismiss, 3000); });
      }

      // A floating "Detail" slider shown after an auto-trace: drag to re-trace the SAME spot
      // finer (smoother curves, more points) or simpler. Built once; later calls only refresh
      // the live vertex count. The slider drives the re-trace on release (change), not on every
      // pixel of the drag, so the server isn't hammered.
      _showTraceDetail(nPts, nHoles){
        const self=this, COARSE=0.003, FINE=0.0003;
        let el=document.getElementById('cal-trace-detail');
        if(!el){
          el=document.createElement('div'); el.id='cal-trace-detail';
          el.style.cssText='position:fixed;left:50%;bottom:132px;transform:translateX(-50%);z-index:60;'+
            'display:flex;gap:9px;align-items:center;background:#12171c;color:#e8eef2;border:1px solid #2a3a44;'+
            'border-radius:12px;padding:9px 13px;box-shadow:0 8px 28px rgba(0,0,0,.5);font:600 13px Segoe UI,system-ui,sans-serif;max-width:94vw;';
          const lab=document.createElement('span'); lab.textContent='Detail'; lab.style.color='#9fb2bb';
          const s1=document.createElement('span'); s1.textContent='simpler'; s1.style.cssText='color:#76767e;font-size:11px;';
          const rng=document.createElement('input'); rng.type='range'; rng.id='cal-trace-detail-rng';
          rng.min='0'; rng.max='100'; rng.step='1'; rng.style.cssText='width:150px;accent-color:#00d4ff;cursor:pointer;';
          rng.setAttribute('aria-label','Trace detail');
          const s2=document.createElement('span'); s2.textContent='finer'; s2.style.cssText='color:#76767e;font-size:11px;';
          const cnt=document.createElement('span'); cnt.id='cal-trace-detail-cnt'; cnt.style.cssText='color:#9fb2bb;font-size:11px;min-width:60px;text-align:right;';
          const done=document.createElement('button'); done.type='button'; done.textContent='✓ Done';
          done.style.cssText='min-height:32px;padding:5px 12px;border-radius:8px;border:none;background:#00d4ff;color:#04222b;font-weight:600;cursor:pointer;';
          done.onclick=function(){ self._dismissTraceDetail(); };
          rng.addEventListener('change', function(){
            const t=parseInt(rng.value,10)/100;
            self.retraceDetail(COARSE*Math.pow(FINE/COARSE, t));
          });
          el.appendChild(lab); el.appendChild(s1); el.appendChild(rng); el.appendChild(s2); el.appendChild(cnt); el.appendChild(done);
          document.body.appendChild(el);
        }
        const rng=document.getElementById('cal-trace-detail-rng');
        if(rng && document.activeElement!==rng){
          const cur=this._autoSimplify||0.0006;
          const v=Math.round(100*Math.log(cur/COARSE)/Math.log(FINE/COARSE));
          rng.value=String(Math.max(0, Math.min(100, v)));
        }
        const cnt=document.getElementById('cal-trace-detail-cnt');
        if(cnt) cnt.textContent=(nPts||0)+' pts'+(nHoles?(' · '+nHoles+'h'):'');
      }
      // Straighten the selected outline onto the part's edges (outline_cleanup.py): each long
      // side becomes one straight line fitted to the photo's edge beside it, sides meet at a
      // clean corner, and a rounded corner keeps its traced dots. One undo step.
      // mode 'refine' also gives each corner the radius the photo shows. It works best from
      // the dots as traced, so if the outline is exactly what Straighten made of them, those
      // dots go instead (re-fitting already straight sides from two dots each nudged them off).
      straightenSelected(mode){
        const refine = mode==='refine';
        const a=this.ann && this.ann.items.find(x=>x.id===this.ann.selectedId);
        if(!a || a.type!=='polyline' || !a.closed || !Array.isArray(a.pts) || a.pts.length<3){
          this._toast('Select a closed outline first, then '+(refine?'Refine':'Straighten')+'.', {autoMs:2600});
          return Promise.resolve(false);
        }
        this._straightened = this._straightened || {};
        const prior = this._straightened[a.id];
        let src = (refine && prior && prior.to===JSON.stringify(a.pts)) ? prior.from : a.pts;
        if(!this.img) return Promise.resolve(false);
        // Detail the first trace simplified away (a gentle rounded corner traced as one straight
        // piece) cannot be fitted afterwards. So if the outline is still exactly the trace's own —
        // or Straighten's version of it — Refine first re-traces the same spot at full detail,
        // with the same Area box and taps. Anything edited by hand is refined as it stands.
        const t=this._tracedOuter, curJ=JSON.stringify(a.pts);
        const fromTrace = refine && t && t.id===a.id && Array.isArray(this._lastAutoSeed)
          && (curJ===t.json || (prior && prior.to===curJ && JSON.stringify(prior.from)===t.json));
        const natW=this.img.naturalWidth||this.img.width, natH=this.img.naturalHeight||this.img.height;
        const k=Math.min(1, 1600/Math.max(natW, natH));      // the same posted size the trace uses
        const cnv=document.createElement('canvas');
        cnv.width=Math.max(1,Math.round(natW*k)); cnv.height=Math.max(1,Math.round(natH*k));
        cnv.getContext('2d').drawImage(this.img, 0, 0, cnv.width, cnv.height);
        let durl=null; try{ durl=cnv.toDataURL('image/jpeg', 0.85); }catch(e){}   // none: straighten by the dots alone
        const self=this, id=a.id;
        if(refine && !durl){ this._toast('Refine needs the photo, which could not be read here.', {autoMs:3000}); return Promise.resolve(false); }
        const toast=this._toast(refine ? (fromTrace ? 'Refining — re-tracing at full detail (a few seconds)…' : 'Refining…') : 'Straightening…');
        const fine = fromTrace ? (function(){
          const req=self._traceRequest(self._lastAutoSeed, 0.0003);
          if(!req) return Promise.resolve(src);
          return fetch(self._apiUrl('api/trace'), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(req.body) })
            .then(r=>r.json())
            .then(j=>(j && j.ok && Array.isArray(j.points) && j.points.length>=3) ? j.points.map(p=>[p[0]/req.sentScale, p[1]/req.sentScale]) : src)
            .catch(()=>src);
        })() : Promise.resolve(src);
        let usedPts=src;
        return fine.then(srcPts => { usedPts=srcPts; return fetch(this._apiUrl('api/outline/straighten'), { method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ image:durl, mode:refine?'refine':'straighten', points:srcPts.map(p=>[p[0]*k, p[1]*k]) }) }); })
          .then(r=>r.json())
          .then(function(j){
            const cur=self.ann.items.find(x=>x.id===id);
            if(cur && refine && fromTrace && usedPts!==src && (!j || !j.ok)){
              // No straight sides to fit (a round part), but the full-detail re-trace is
              // itself the more detailed outline that was asked for.
              self._pushHistory();
              cur.pts=usedPts.map(p=>p.slice());
              self._dismissTraceDetail(); self.requestDraw();
              toast.update('Refined: re-traced at full detail ('+cur.pts.length+' points) — no straight sides to fit. Undo to go back.', 'ok');
              setTimeout(toast.dismiss, 3200);
              return true;
            }
            if(!j || !j.ok || !cur || !Array.isArray(j.points)){
              toast.update('Nothing to '+(refine?'refine':'straighten')+' — this outline has no long straight sides.', 'err');
              setTimeout(toast.dismiss, 3000);
              return false;
            }
            self._pushHistory();
            const before=cur.pts.map(p=>p.slice());
            cur.pts=j.points.map(p=>[p[0]/k, p[1]/k]);
            if(!refine) self._straightened[id]={ from: before, to: JSON.stringify(cur.pts) };
            self._dismissTraceDetail();   // its slider re-traces, which would undo this
            self.requestDraw();
            const n=j.straight|0, sn=j.snapped|0, rd=j.rounded|0;
            toast.update(refine
              ? ('Refined: '+n+' straight side'+(n===1?'':'s')+(rd ? ', '+rd+' rounded corner'+(rd===1?'':'s')+' fitted to the photo' : ', corners kept as they are')+'. Undo to go back.')
              : ('Straightened '+n+' side'+(n===1?'':'s')+(sn ? ' — '+(sn===n?'all':sn)+' snapped to the edge in the photo' : '')+'. Undo to go back.'), 'ok');
            setTimeout(toast.dismiss, 3200);
            return true;
          })
          .catch(function(){ toast.update((refine?'Refine':'Straighten')+' needs the server — try again.', 'err'); setTimeout(toast.dismiss, 3000); return false; });
      }

      // Square up (square_up.py): two edges that are parallel on the part converge in a photo
      // taken at an angle, and where they meet says how the camera was tilted; with the scale
      // line, every measurement and export is then corrected for that tilt. The ruler under
      // the scale line is the first choice — it is a rectangle for certain, which the part may
      // not be (ruler_check.py finds its edges in a crop of the photo round the line) — and the
      // part's own long sides are used when no ruler edges are found. The message reports how
      // the other one reads once squared up, which is the check on the result, and offers it
      // instead. Undo puts the plain line scale back.
      squareUp(reference){
        const a=this.ann && this.ann.items.find(x=>x.id===this.ann.selectedId);
        if(!a || a.type!=='polyline' || !a.closed || !Array.isArray(a.pts) || a.pts.length<4){
          this._toast('Select the part\'s traced outline first, then Square up (it uses the ruler under your scale line).', {autoMs:3600});
          return Promise.resolve(false);
        }
        const line=this._scaleLine;
        if(!line){
          this._toast('Set the scale with a line along your ruler first (📐 → Measure something of known size), then Square up.', {autoMs:5000});
          return Promise.resolve(false);
        }
        const W=this.img.naturalWidth||this.img.width, H=this.img.naturalHeight||this.img.height;
        const cam=this.data && this.data.camera;
        const focal=(cam && cam.focal_source==='exif' && cam.focal_px>0) ? cam.focal_px : null;
        const self=this;
        // The photo round the scale line, at full resolution (capped), for the ruler's edges.
        let rulerImage=null, rulerBox=null;
        if(reference!=='part'){
          try{
            const L=Math.hypot(line.b[0]-line.a[0], line.b[1]-line.a[1]), m=0.3*L+20;
            const x0=Math.max(0, Math.floor(Math.min(line.a[0], line.b[0])-m)), y0=Math.max(0, Math.floor(Math.min(line.a[1], line.b[1])-m));
            const x1=Math.min(W, Math.ceil(Math.max(line.a[0], line.b[0])+m)), y1=Math.min(H, Math.ceil(Math.max(line.a[1], line.b[1])+m));
            const k=Math.min(1, 2400/Math.max(1, x1-x0, y1-y0));
            if(x1-x0>8 && y1-y0>8){
              const cnv=document.createElement('canvas');
              cnv.width=Math.max(1, Math.round((x1-x0)*k)); cnv.height=Math.max(1, Math.round((y1-y0)*k));
              cnv.getContext('2d').drawImage(this.img, x0, y0, x1-x0, y1-y0, 0, 0, cnv.width, cnv.height);
              rulerImage=cnv.toDataURL('image/jpeg', 0.92); rulerBox=[x0, y0, k];
            }
          }catch(e){ rulerImage=null; rulerBox=null; }
        }
        const u=Units.get(this.opts.units), dp=(u.label==='mm'?1:(u.label==='cm'?2:3));
        const len=mm=>u.fromMM(mm).toFixed(dp)+' '+u.label;
        const r1=v=>Math.round(v*10)/10;
        const toast=this._toast('Squaring up…');
        // The scale as it was asked about. A new scale set while the request is out (it can
        // take a second or two) must not be overwritten by a result built from the old one.
        const sent={ line:line, mm:this.opts.manualMmPerPx, H:this.opts.manualHomography };
        const unchanged=()=>self._scaleLine===sent.line && self.opts.manualMmPerPx===sent.mm && self.opts.manualHomography===sent.H;
        return fetch(this._apiUrl('api/square_up'), { method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ outline:a.pts, ruler:[line.a, line.b], ruler_mm:line.mm, image_w:W, image_h:H, focal_px:focal,
                                  ruler_image:rulerImage, ruler_box:rulerBox, reference:reference||'auto' }) })
          .then(r=>r.json())
          .then(function(j){
            // Redraw the scale line down the ruler's middle: what finds its edges. `before`
            // puts the plain line scale back first, when a result was applied.
            const redraw=function(before){
              return { label:'Redraw scale line', fn:function(){
                if(before && before()===false) return;
                self.setMode('setscale');
                self._toast('Tap one end of the ruler, then the other — down its middle, end to end.', {autoMs:6000});
              } };
            };
            if(!j || !j.ok){
              toast.dismiss();
              self._toast((j && j.error) || 'Could not square up this photo.',
                (j && j.ruler_reason && reference!=='part') ? { autoMs:9000, actions:[redraw(null)] } : { autoMs:5000 });
              return false;
            }
            if(!unchanged()){ toast.update('The scale changed while squaring up — press Square up again to use the new one.', 'err'); setTimeout(toast.dismiss, 5000); return false; }
            const prev={ mm:self.opts.manualMmPerPx, H:self.opts.manualHomography, ref:self._manualRef, ext:self._refExtentPx };
            // Undo and the switch button act only while this result is still the scale: once
            // another scale is set, "undo" would put back a scale from before that one.
            const stillMine=function(){
              if(self.opts.manualHomography===j.H) return true;
              self._toast('The scale has been set again since — this Square up can no longer be undone from here.', {autoMs:3500});
              return false;
            };
            const undo=function(){
              if(!stillMine()) return;
              if(prev.H) self.setManualRectScale(prev.H, prev.mm, prev.ref, prev.ext);
              else { self.opts.manualHomography=null; self._manualRef=prev.ref; self.opts.manualMmPerPx=prev.mm; self._rescaleAnnotations(); self.requestDraw(); }
            };
            if(self._manualRef && self._manualRef.kind==='squared' && self._squarePrev) Object.assign(prev, self._squarePrev);   // switching: undo goes all the way back
            // The server's H is the ruler's plane: a plane offset on the line scale does not
            // carry over. Square up from the line's own scale and say so; Undo puts the
            // offset back with the mm/px it lives in.
            const plane=self._lineScalePlane(), dropped=plane ? plane.h : 0;
            self.setManualRectScale(j.H, dropped ? plane.base : self.opts.manualMmPerPx, { kind:'squared', tilt_deg:j.tilt_deg, reference:j.reference,
                                     focal_source:j.focal_source, sides:j.sides, ruler_edges:j.ruler_edges }, self._refExtentPx);
            self._squarePrev=prev;
            toast.dismiss();
            const tilt=r1(j.tilt_deg)+'°';
            let msg, noRuler=null;
            if(j.reference==='ruler' && j.partial){
              // One edge clear, the other assumed parallel to it: the photo is lined up on the
              // ruler, but two parallel lines cannot show a tilt along it, so the part's taper
              // read through it may be that tilt and is not called one.
              msg='Squared up from the ruler, but only one of its edges was clear'+(j.ruler_reason ? ' ('+j.ruler_reason+')' : '')+
                ', so the other was assumed parallel — this lines the photo up but cannot see a camera tilt along the ruler.';
              const p=j.part;
              if(p && p.width_mm) msg+=' The part reads '+len(p.width_mm[0])+' and '+len(p.width_mm[1])+' wide at its two ends (not checked against the ruler).';
              msg+=' For a full check, redraw the scale line down the ruler\'s middle, end to end.';
            } else if(j.reference==='ruler'){
              msg='Squared up from the ruler\'s edges: the camera was tilted '+tilt+'.';
              const rw=j.ruler_check && j.ruler_check.width_mm;
              if(rw) msg+=' The ruler reads '+len((rw[0]+rw[1])/2)+' wide — if yours is, the correction is right.';
              const p=j.part;
              if(p && p.width_mm){
                // A ruler is short next to the part: the taper is good to about ± 2 standard
                // errors (taper_sigma_mm), so only a taper well beyond that is called one.
                const d=Math.abs(p.width_mm[1]-p.width_mm[0]), pm=2*(p.taper_sigma_mm||0);
                msg+= d>Math.max(1, pm)
                  ? ' The part\'s long sides are NOT parallel: '+len(p.width_mm[0])+' wide at one end, '+len(p.width_mm[1])+' at the other ('+len(d)+(pm?' ± '+len(pm):'')+
                    ') — it tapers, or its side walls show at one end. The outline keeps that taper.'
                  : ' The part\'s long sides agree with the ruler: parallel to within '+len(Math.max(d, pm))+'.';
              }
            } else {
              msg='Squared up from the part\'s long sides: the camera was tilted '+tilt+' along the part (its sides narrowed '+
                r1(j.width_change_pct)+'% end to end).';
              const c=j.ruler_check;
              if(c && c.width_mm && !j.partial) msg+=' Check: the ruler now reads '+len(c.width_mm[0])+' and '+len(c.width_mm[1])+' wide at its two ends'+
                (c.width_change_pct>0.6 ? ' ('+r1(c.width_change_pct)+'% apart — a true rectangle reads the same; the part may not be one).' : ' — square.');
              else if(reference!=='part' && j.other!=='ruler'){
                // The ruler was wanted and not usable: say why, what that costs, and how to
                // get it used — the scale line is where its edges are looked for.
                noRuler=j.ruler_reason || 'no clear edges';
                msg=(j.ruler_found ? 'Found the ruler\'s edges along your scale line but couldn\'t square up from them ('
                                   : 'Couldn\'t find the ruler\'s edges along your scale line (')+noRuler+'), so this used the part\'s long sides, which assumes the part is a true rectangle'+
                  ' (camera tilted '+tilt+' along it). To use the ruler, redraw the scale line down its middle, end to end.';
              }
            }
            if(dropped) msg+=' Your '+dropped+' mm plane offset is off: a squared-up photo measures in the ruler\'s plane (Undo brings it back).';
            // A photo with no lens data (chat apps strip it) got a typical phone lens instead.
            if(j.focal_source==='assumed' && j.tilt_deg>1) msg+=' Tip: this file has no camera data, so a typical phone lens was assumed — the phone\'s original photo is more exact.';
            const actions=[];
            if(j.other) actions.push({ label: j.other==='ruler' ? (j.partial ? 'Use the ruler (one edge)' : 'Use the ruler instead') : 'Use the part\'s sides instead',
                                       fn:function(){ if(!stillMine()) return; self.ann.selectedId=a.id; self.squareUp(j.other); } });
            if(noRuler || j.partial) actions.push(redraw(function(){ if(!stillMine()) return false; undo(); }));
            const why='Use the ruler: unavailable — '+(noRuler || '');
            if(noRuler) actions.push({ label:'Use the ruler', disabled:true, title:why,
                                       fn:function(){ self._toast(why+'. Redraw the scale line down the ruler\'s middle, end to end.', {autoMs:6000}); } });
            actions.push({ label:'Undo', fn:undo });
            self._toast(msg, { autoMs:noRuler ? 20000 : 16000, actions:actions });
            // The toast's buttons know nothing of "disabled": grey that one out in place, with
            // the reason on hover, rather than hide it — it says the ruler was looked for.
            const shown=window.__calLastToast;
            if(noRuler && shown && shown.querySelectorAll){
              shown.querySelectorAll('button').forEach(function(btn){
                if(btn.textContent!=='Use the ruler') return;
                btn.disabled=true; btn.title=why; btn.setAttribute('aria-disabled', 'true');
                btn.style.opacity='0.5'; btn.style.cursor='not-allowed';
              });
            }
            return true;
          })
          .catch(function(){ toast.update('Square up needs the server — try again.', 'err'); setTimeout(toast.dismiss, 3000); return false; });
      }

      // Make the selected outline symmetric (outline_cleanup.symmetrize_outline): averaged with
      // its own mirror image about whichever axis its halves already agree on more. The message
      // offers the other axis, applied to the outline as it was. One undo step each.
      symmetrizeSelected(axis, fromPts){
        const a=this.ann && this.ann.items.find(x=>x.id===this.ann.selectedId);
        if(!a || a.type!=='polyline' || !a.closed || !Array.isArray(a.pts) || a.pts.length<3){
          this._toast('Select a closed outline first, then Symmetric.', {autoMs:2600});
          return Promise.resolve(false);
        }
        const src=(fromPts || a.pts).map(p=>p.slice()), self=this, id=a.id;
        const toast=this._toast('Making it symmetric…');
        return fetch(this._apiUrl('api/outline/symmetric'), { method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ points:src, axis:axis||'auto' }) })
          .then(r=>r.json())
          .then(function(j){
            const cur=self.ann.items.find(x=>x.id===id);
            if(!j || !j.ok || !cur){ toast.update((j && j.error) || 'Could not make this outline symmetric.', 'err'); setTimeout(toast.dismiss, 4500); return false; }
            self._pushHistory();
            cur.pts=j.points.map(p=>p.slice());
            self._dismissTraceDetail(); self.requestDraw();
            toast.dismiss();
            const name={long:'long', short:'short'};
            self._toast('Made symmetric about its '+name[j.axis]+' axis (its halves differed by '+j.mismatch_pct+'%). Undo to go back.',
              { autoMs:8000, action:{ label:'Use the '+name[j.other.axis]+' axis ('+j.other.mismatch_pct+'%)', fn:function(){
                const c2=self.ann.items.find(x=>x.id===id); if(!c2) return;
                self.ann.selectedId=id;
                self.symmetrizeSelected(j.other.axis, src);
              } } });
            return true;
          })
          .catch(function(){ toast.update('Symmetric needs the server — try again.', 'err'); setTimeout(toast.dismiss, 3000); return false; });
      }

      _dismissTraceDetail(){ const el=document.getElementById('cal-trace-detail'); if(el) el.remove(); }

      // Ask for the real length of a just-drawn line and set the working scale from it.
      // The UI may provide onNeedScaleInput(pixelLen, apply) for an inline field; otherwise
      // we fall back to a prompt(). The entered value is in the current display unit.
      _promptScale(pixelLen, mid){
        const unit=Units.get(this.opts.units);
        const ends=this.selectedPoints.slice(0,2).map(p=>p.slice());   // kept for Square up
        const done=(valStr)=>{
          this.selectedPoints=[]; this.hover=null;
          if(valStr!=null){ const v=parseFloat(valStr); if(v>0){ this.setManualScaleFromPixels(pixelLen, unit.toMM(v), ends); this.setMode('select'); } }
          this.requestDraw();
        };
        if(typeof this.onNeedScaleInput==='function'){ this.onNeedScaleInput(pixelLen, done, unit, mid); return; }
        this._inlineScaleInput(pixelLen, done, unit, mid, ends);
      }

      // Floating inline field near the drawn line's midpoint for entering its real
      // length. Replaces the native prompt(), which covered the line and was clumsy on
      // mobile. done(valueStringOrNull) applies the value or cancels.
      _inlineScaleInput(pixelLen, done, unit, mid, ends){
        const prev=document.getElementById('cal-scale-input'); if(prev) prev.remove();
        const box=document.createElement('div'); box.id='cal-scale-input';
        box.style.cssText='position:fixed;z-index:40;background:#0e0e0e;border:2px solid #00d4ff;border-radius:10px;padding:10px;box-shadow:0 8px 24px rgba(0,0,0,.6);display:flex;flex-wrap:wrap;gap:8px;align-items:center;max-width:calc(100vw - 16px);font:15px Segoe UI,system-ui,sans-serif;color:#eee;';
        const lab=document.createElement('span'); lab.textContent='Line length:'; lab.style.cssText='font-weight:600;';
        // 16px font stops iOS from auto-zooming on focus; 44px min-height meets the touch
        // target the rest of the UI enforces (this input is the ONLY calibration path when
        // no marker is detected, so it must be comfortable on a phone).
        const inp=document.createElement('input'); inp.type='number'; inp.step='0.01'; inp.min='0'; inp.inputMode='decimal'; inp.placeholder='e.g. 25.4';
        inp.setAttribute('enterkeyhint','done');
        inp.style.cssText='flex:1 1 96px;min-width:96px;min-height:44px;padding:8px 10px;background:#181818;border:1px solid #2a2a2a;border-radius:8px;color:#eee;font:16px Segoe UI,system-ui,sans-serif;';
        const us=document.createElement('span'); us.textContent=unit.label;
        const ok=document.createElement('button'); ok.type='button'; ok.textContent='Set'; ok.style.cssText='min-height:44px;padding:8px 18px;background:#00d4ff;color:#000;border:none;border-radius:8px;font-weight:600;font-size:15px;cursor:pointer;';
        const cancel=document.createElement('button'); cancel.type='button'; cancel.textContent='✕'; cancel.setAttribute('aria-label','Cancel'); cancel.style.cssText='min-height:44px;min-width:44px;padding:8px 12px;background:#181818;color:#eee;border:1px solid #2a2a2a;border-radius:8px;font-size:15px;cursor:pointer;';
        // Apply an EXACT millimetre length (used by the preset chips below). Unlike the
        // typed field — which is in the active display unit — presets are already mm, so
        // they bypass unit.toMM and can't be corrupted by inch/cm mode (the unit trap).
        const finishMM=(mm)=>{
          box.remove(); this.selectedPoints=[]; this.hover=null;
          if(mm>0){ this.setManualScaleFromPixels(pixelLen, mm, ends); this.setMode('select'); }
          this.requestDraw();
        };
        // One-tap reference presets so a customer without a ruler (or who doesn't know a
        // quarter is 24.26 mm) can still set scale from something in the shot. Card is
        // ISO ID-1 — identical worldwide; the coin/paper sizes are region-specific but
        // cover the usual cases. Each is the length you'd draw a line ACROSS.
        const presets=document.createElement('div');
        presets.style.cssText='flex:1 1 100%;display:flex;flex-wrap:wrap;gap:6px;align-items:center;border-top:1px solid #242424;padding-top:8px;margin-top:2px;';
        const plab=document.createElement('span'); plab.textContent='…or tap what you measured across:';
        plab.style.cssText='flex:1 1 100%;font-size:12px;color:#9aa;';
        presets.appendChild(plab);
        for(const p of [
          {t:'💳 Card',    mm:85.6,   h:'ID / credit-card long edge (85.6 mm, ISO standard)'},
          {t:'🪙 Quarter', mm:24.26,  h:'US quarter diameter (24.26 mm)'},
          {t:'📄 A4',      mm:297,    h:'A4 sheet long edge (297 mm)'},
          {t:'📄 Letter',  mm:279.4,  h:'US Letter long edge (279.4 mm)'},
        ]){
          const b=document.createElement('button'); b.type='button';
          b.textContent=`${p.t} · ${p.mm} mm`; b.title=p.h; b.setAttribute('aria-label',p.h);
          b.style.cssText='min-height:40px;padding:6px 11px;background:#151515;color:#cde;border:1px solid #2a3540;border-radius:8px;font-size:13px;cursor:pointer;';
          b.onclick=()=>finishMM(p.mm);
          presets.appendChild(b);
        }
        // THE SAME PART appears in every photo of a job, so a length measured in a view
        // that HAD a usable reference is a valid reference in a view that did not. That is
        // what rescues a set like "ruler in one shot, none in the others": measure the part's
        // width where the ruler lies in its plane, then reuse that width to scale every other
        // angle. Without this, a photo with no reference simply cannot be measured at all.
        try{
          const J=window.CalibJob;
          const dims=(J && J.referenceDimensions) ? J.referenceDimensions() : [];
          if(dims.length){
            const head=document.createElement('span');
            head.textContent='…or a dimension you already measured on this part:';
            head.style.cssText='flex:1 1 100%;font-size:12px;color:#9aa;margin-top:6px;';
            presets.appendChild(head);
            for(const dmn of dims.slice(0,6)){
              const b=document.createElement('button'); b.type='button';
              b.textContent=`📏 ${dmn.view}: ${dmn.label} · ${dmn.mm.toFixed(1)} mm`;
              b.title=`Reuse ${dmn.mm.toFixed(2)} mm, measured on "${dmn.view}"`;
              b.style.cssText='min-height:40px;padding:6px 11px;background:#151a1f;color:#cfe6ee;'
                + 'border:1px solid #29414c;border-radius:8px;font-size:13px;cursor:pointer;';
              b.onclick=()=>finishMM(dmn.mm);
              presets.appendChild(b);
            }
          }
        }catch(e){ /* the job layer is optional; presets above still work */ }

        box.append(lab,inp,us,ok,cancel,presets); document.body.appendChild(box);
        // Anchor ABOVE the drawn line (never below): the soft keyboard opens from the
        // bottom, so a box placed under the finger/midpoint gets covered on phones. Fall
        // back below only if there's no room above, and keep it out of the lower ~45%.
        try{
          const r=this.canvas.getBoundingClientRect();
          const [dx,dy]=this.vp.imageToCanvas(mid[0],mid[1]);
          const cssX=r.left + dx*(r.width/Math.max(1,this.canvas.width));
          const cssY=r.top  + dy*(r.height/Math.max(1,this.canvas.height));
          const bw=box.offsetWidth, bh=box.offsetHeight;
          let top = cssY - bh - 16;
          if(top < 8) top = cssY + 16;                       // no room above → drop below
          top = Math.max(8, Math.min(top, window.innerHeight*0.45)); // stay clear of the keyboard band
          box.style.left=Math.max(8, Math.min(window.innerWidth-bw-8, cssX-bw/2))+'px';
          box.style.top =top+'px';
        }catch(e){ box.style.left='50%'; box.style.top='24%'; box.style.transform='translateX(-50%)'; }
        const finish=(val)=>{ box.remove(); done(val); };
        ok.onclick=()=>finish(inp.value);
        cancel.onclick=()=>finish(null);
        inp.addEventListener('keydown',(e)=>{ e.stopPropagation(); if(e.key==='Enter'){ e.preventDefault(); finish(inp.value); } else if(e.key==='Escape'){ e.preventDefault(); finish(null); } });
        setTimeout(()=>{ inp.focus(); if(inp.select) inp.select(); },0);
      }

      // Measurement `data` to feed CalibMeasure. When a manual paper-rectangle scale is
      // active, substitute a synthetic calibration: a px->mm homography with
      // marker_size_mm=1, so CalibMeasure returns real millimetres directly (it multiplies
      // unit-square coords by edgeMM, and edgeMM=1 makes it a pass-through). Otherwise the
      // real detection data (auto square homography + marker size) is used unchanged.
      _dataForMeasure(){
        if(this.opts.manualHomography)
          return Object.assign({}, this.data, { homography:this.opts.manualHomography, marker_size_mm:1 });
        // Measuring a face that is NOT in the card's plane? Swap in the homography for that
        // raised plane. Everything downstream (lengths, areas, rectangles, angles, 3-point
        // circles) already reads its geometry from here, so one substitution corrects them
        // all. Without it a face h mm above the card reads d/(d-h) too large — +5.3% for a
        // part only 20 mm thick at arm's length, and that error is there just as much in a
        // perfectly square-on shot as in a tilted one.
        if(this._planeH) return Object.assign({}, this.data, { homography:this._planeH });
        return this.data;
      }

      // --- Measuring off the card's plane -------------------------------------------------
      // Height in mm of the face being measured ABOVE the card (a part's top face is
      // positive; a recess is negative). 0 means measure in the card's own plane.
      // Under a line scale the offset is read back from the scale itself (_lineScalePlane).
      featureHeightMM(){ const p = this._lineScalePlane(); return p ? p.h : (this._featureHeightMM || 0); }

      // How wrong an UNCORRECTED measurement of a face `h` mm off the card's plane would be,
      // as a percentage, or null when the working distance is unknown. Taken from the
      // server's camera block so this readout and the correction cannot disagree.
      parallaxPctFor(h){
        const cam = this.data && this.data.camera;
        if(!cam || !cam.distance_mm || !h) return null;
        // 100h/(d-h), NOT parallax_pct_per_mm * h. The error is a hyperbola, not a line, so
        // scaling the per-millimetre figure understates it — by 1.1 points at h=40 mm and
        // 400 mm, and 2.8 points at h=40 mm and 257 mm (a tilted, close-in shot), always in
        // the direction of looking better than it is. camera.distance_mm is already the
        // perpendicular distance to the card's plane, which is the d this needs.
        const d = cam.distance_mm;
        if(!(d > 0) || h >= d / 3) return null;   // past a third of the way to the lens, meaningless
        return 100 * h / (d - h);
      }

      // A plain sentence for the customer, or null when there is nothing worth saying.
      parallaxAdvice(h){
        const pct = this.parallaxPctFor(h);
        if(pct == null || Math.abs(pct) < 0.5) return null;
        const cam = this.data.camera || {};
        const hedge = cam.focal_source === 'exif' ? '' : ' (estimated \u2014 this photo carries no lens data)';
        return 'A face ' + h + ' mm off the card reads about ' + pct.toFixed(0) + '% ' +
               (pct > 0 ? 'large' : 'small') + hedge +
               '. Resting the card on that face and re-shooting is exact; correcting here is an estimate.';
      }

      // A line scale's plane offset, read back from the scale rather than stored beside it.
      // The line's own mm/px is its length over its drawn ends, and the ends are saved with
      // the view, so the offset in force is whatever the current mm/px implies: it survives
      // a reload, a job swap, an autosave or an Undo with nothing else saved, and a newly
      // drawn line reads 0 by construction. Z = f * mm/px does not change when a save
      // downscales the photo (f and mm/px scale oppositely). null unless a plain line scale.
      _lineScalePlane(){
        const cur = this.opts.manualMmPerPx;
        if(!(cur > 0) || this.opts.manualHomography) return null;
        const L = this._scaleLine;
        const px = (L && Array.isArray(L.a) && Array.isArray(L.b)) ? Math.hypot(L.b[0] - L.a[0], L.b[1] - L.a[1]) : 0;
        // No ends kept (a save from before they were): what this session remembers, else this scale.
        const prev = this._lineScaleBase;
        const base = (px > 0 && L.mm > 0) ? L.mm / px : ((prev && cur === prev.scaled) ? prev.mm : cur);
        const cam = (this.data && this.data.camera) || {};
        const W = (this.img && (this.img.naturalWidth || this.img.width)) || 0, H = (this.img && (this.img.naturalHeight || this.img.height)) || 0;
        const exif = cam.focal_source === 'exif' && cam.focal_px > 0;
        // camera_geometry.default_focal_px: a 68° field of view across the long side.
        const f = exif ? cam.focal_px : Math.max(W, H) / (2 * Math.tan(34 * Math.PI / 180));
        const Z = f * base, factor = cur / base;
        // A save's downscale leaves ~1e-15 of float noise; 0.1 mm at 600 mm is 1.7e-4.
        const h = (Z > 0 && Math.abs(factor - 1) > 1e-6) ? Math.round(10 * Z * (1 - factor)) / 10 : 0;
        return { base:base, Z:Z, exif:exif, h:h };
      }

      // Ask the server for the homography of the plane `h` mm above the card, and measure
      // there instead. Resolves true when the correction is active.
      async setFeatureHeight(h){
        const mm = Number(h) || 0;
        // A manual paper rectangle (or a Square up) carries no camera geometry to lift.
        if(this.opts.manualHomography){
          this._featureHeightMM = 0; this._planeH = null; this.updateKPI(); this.requestDraw(); return !mm;
        }
        // A line scale holds in the ruler's plane only. A plane d mm farther from the camera
        // is seen at Z+d instead of Z, so each px there spans (Z+d)/Z as many mm, with
        // Z = f * mm/px the working distance. On a customer's top photo the outline was the
        // cover's bottom edge, 45 mm below the ruler on its top face: 0.516 mm/px, f = 1186 px
        // (no lens data), Z = 612 mm, so every top-view size read 7.4% small. `h` keeps the
        // marker path's sense: mm ABOVE the reference, so that edge is h = -45.
        const plane = this._lineScalePlane();
        if(plane){
          // Always from the line's own scale, so applying twice does not compound and 0
          // puts the line's scale back, in this session or after a reload.
          const base = plane.base, Z = plane.Z;
          const done = () => { this._planeH = null; this._rescaleAnnotations(); this.updateKPI(); this.requestDraw(); };
          this._featureHeightMM = 0;   // the offset lives in the mm/px; featureHeightMM() reads it back
          if(!mm){ this.opts.manualMmPerPx = base; this._lineScaleBase = null; done(); return true; }
          // Past a third of the way to the lens the estimate means nothing.
          if(!(Z > 0) || Math.abs(mm) >= Z / 3){ this.opts.manualMmPerPx = base; this._lineScaleBase = null; done(); return false; }
          const factor = (Z - mm) / Z, pct = 100 * (factor - 1);
          this.opts.manualMmPerPx = base * factor;
          this._lineScaleBase = { mm:base, scaled:this.opts.manualMmPerPx };
          done();
          this._toast('Scale moved to a face ' + Math.abs(mm) + ' mm ' + (mm < 0 ? 'below' : 'above') + ' the ruler: sizes ' +
                      (pct >= 0 ? '+' : '') + pct.toFixed(1) + '%' + (plane.exif ? '' : ', estimated (no lens data)') +
                      '. Laying the ruler on that face and re-shooting is exact. Set 0 to go back.', { autoMs:9000 });
          return true;
        }
        this._featureHeightMM = mm;
        if(!mm){ this._planeH = null; this.updateKPI(); this.requestDraw(); return true; }
        // Otherwise only the auto marker homography can be lifted.
        if(!this.data || !this.data.homography){
          this._featureHeightMM = 0; this._planeH = null; this.updateKPI(); this.requestDraw(); return false;
        }
        const cam = this.data.camera || {};
        const sz = this.data.image_size || {};
        const m0 = (this.data.markers || [])[0] || {};
        try{
          const r = await fetch(Xport._apiUrl('api/plane'), {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ homography:this.data.homography, marker_size_mm:this.data.marker_size_mm,
                                   focal_px:cam.focal_px, edge_px:m0.edge_px,
                                   image_size:{ width:sz.width||this.img.naturalWidth||this.img.width,
                                                height:sz.height||this.img.naturalHeight||this.img.height },
                                   height_mm:mm })
          });
          const j = await r.json();
          if(!r.ok || !j.ok || !j.homography){ this._featureHeightMM = 0; this._planeH = null; return false; }
          this._planeH = j.homography;
          // Deliberately NOT calling _rescaleAnnotations(): the per-annotation frozen
          // mm_per_px comes from the marker and is unchanged by which plane we measure in.
          // It is only the fallback for paths with no homography (a 2-point circle), and
          // those stay in the card's plane — as they already do under tilt.
          this.updateKPI(); this.requestDraw();
          return true;
        }catch(e){
          this._featureHeightMM = 0; this._planeH = null; this.updateKPI(); this.requestDraw();
          return false;
        }
      }
      // Whether the perspective homography should rectify measurements right now. A manual
      // paper rectangle carries its own homography (use it); a manual LINE scale is uniform
      // by nature so it disables the auto homography; otherwise the auto homography applies.
      _allowHomography(){ return this.opts.manualHomography ? true : !this.opts.manualMmPerPx; }

      // Set a perspective-corrected scale from a paper rectangle's 4 corners. H maps image
      // px -> plane mm; mmPerPx is a representative uniform scale kept for circles (which
      // aren't rectified), the KPI readout, and the fallback path. ref describes the sheet.
      setManualRectScale(H, mmPerPx, ref, extentPx){
        if(!H) return;
        this._refExtentPx = extentPx || null;   // for the too-small-reference check
        // A manual paper rectangle carries no camera geometry, so a raised plane derived
        // from the marker's homography no longer applies.
        this._planeH = null; this._featureHeightMM = 0;
        this._markerSizeFromMemory=false;
        this.opts.manualHomography=H;
        if(mmPerPx>0) this.opts.manualMmPerPx=mmPerPx;   // wins over any detected marker
        this._manualRef=ref||null;
        // A rectangle replaces the ruler line as the scale; keeping the line let a later
        // Square up silently rebuild the scale from it. Square up's own result keeps it.
        if(!(ref && ref.kind==='squared')) this._scaleLine=null;
        this._rescaleAnnotations();
        this.requestDraw();
      }

      // Build and apply a perspective scale from a rectangle's 4 image corners (from taps
      // OR from auto-detection) given its real size. The longer real edge is assigned to
      // whichever image edge is longer in pixels, so orientation (portrait/landscape) is
      // handled automatically. Returns true on success. Shared by the manual Paper dialog
      // and the auto-detected-sheet offer so both go through the exact same tested math.
      _applyRectScale(corners, wmm, hmm){
        const ordered=Geom.orderQuad(corners);
        if(!ordered || !(wmm>0) || !(hmm>0)) return false;
        const e0=Math.hypot(ordered[1][0]-ordered[0][0], ordered[1][1]-ordered[0][1]);
        const e1=Math.hypot(ordered[2][0]-ordered[1][0], ordered[2][1]-ordered[1][1]);
        const long=Math.max(wmm,hmm), short=Math.min(wmm,hmm);
        const dst = (e0>=e1) ? [[0,0],[long,0],[long,short],[0,short]]
                             : [[0,0],[short,0],[short,long],[0,long]];
        const Hm=Geom.solveHomography(ordered, dst); if(!Hm) return false;
        const areaPx=Geom.quadAreaPx(ordered);
        const mmPerPx = areaPx>0 ? Math.sqrt((wmm*hmm)/areaPx) : 0;
        // Longest diagonal: how far the 4 corners actually brace the fit, which is what
        // decides how far it can be trusted away from them.
        let extentPx=0;
        for(let i=0;i<4;i++) for(let j=i+1;j<4;j++)
          extentPx=Math.max(extentPx, Math.hypot(ordered[j][0]-ordered[i][0], ordered[j][1]-ordered[i][1]));
        this.setManualRectScale(Hm, mmPerPx, { w:long, h:short }, extentPx);
        return true;
      }

      // After the 4th corner tap, ask which rectangle was outlined (A4 / Letter / custom)
      // and build the homography. The tapped corners stay drawn behind the dialog so the
      // user can see what they outlined; the dialog only offers real, known sizes.
      _promptRectScale(imgPts){
        const cleanup=()=>{ const b=document.getElementById('cal-rect-input'); if(b) b.remove();
                            this.selectedPoints=[]; this.hover=null; this.requestDraw(); };
        const applyWH=(wmm,hmm)=>{
          if(!this._applyRectScale(imgPts, wmm, hmm)) return false;
          this.selectedPoints=[]; this.hover=null; this.setMode('select');
          return true;
        };

        const prev=document.getElementById('cal-rect-input'); if(prev) prev.remove();
        const box=document.createElement('div'); box.id='cal-rect-input';
        box.style.cssText='position:fixed;z-index:41;left:50%;top:14%;transform:translateX(-50%);background:#0e0e0e;border:2px solid #00d4ff;border-radius:10px;padding:12px 14px;box-shadow:0 8px 24px rgba(0,0,0,.6);max-width:calc(100vw - 16px);font:15px Segoe UI,system-ui,sans-serif;color:#eee;';
        const title=document.createElement('div'); title.textContent='What did you outline?'; title.style.cssText='font-weight:700;margin-bottom:4px;';
        const sub=document.createElement('div');
        sub.textContent='Any rectangle whose size you know — including one you laid out with a ruler. '
          + 'Tilt is corrected from the 4 corners, so an angled photo still measures true.';
        sub.style.cssText='font-size:12px;color:#9aa;margin-bottom:10px;max-width:36ch;';
        const row=document.createElement('div'); row.style.cssText='display:flex;flex-wrap:wrap;gap:8px;align-items:center;';
        const mkBtn=(label,cls,fn)=>{ const b=document.createElement('button'); b.type='button'; b.textContent=label;
          b.style.cssText='min-height:44px;padding:8px 14px;border-radius:8px;font-size:14px;cursor:pointer;'+(cls==='primary'
            ?'background:#00d4ff;color:#000;border:none;font-weight:600;'
            :'background:#151515;color:#cde;border:1px solid #2a3540;'); b.onclick=fn; return b; };
        row.appendChild(mkBtn('A4 · 210×297', '', ()=>{ if(applyWH(210,297)) box.remove(); }));
        row.appendChild(mkBtn('US Letter · 216×279', '', ()=>{ if(applyWH(215.9,279.4)) box.remove(); }));
        // Ruler-laid squares. For anything bigger than a sheet of paper these are the only
        // reference most customers already own, and a 4-corner rectangle is the ONLY way a
        // ruler buys tilt correction: a single line gives a uniform scale (no perspective at
        // all), and even an L of two lines is just an affine fit, which measured no better
        // than the single line. Four corners is what makes the difference.
        row.appendChild(mkBtn('Ruler square · 300×300', '', ()=>{ if(applyWH(300,300)) box.remove(); }));
        row.appendChild(mkBtn('Ruler square · 12×12 in', '', ()=>{ if(applyWH(304.8,304.8)) box.remove(); }));
        const custom=document.createElement('div'); custom.style.cssText='display:none;flex-wrap:wrap;gap:8px;align-items:center;margin-top:10px;';
        row.appendChild(mkBtn('Custom…', '', ()=>{ custom.style.display = custom.style.display==='none' ? 'flex' : 'none';
          if(custom.style.display==='flex') setTimeout(()=>wIn.focus(),0); }));
        const cancel=document.createElement('button'); cancel.type='button'; cancel.textContent='✕';
        cancel.setAttribute('aria-label','Cancel'); cancel.style.cssText='min-height:44px;min-width:44px;padding:8px 12px;background:#181818;color:#eee;border:1px solid #2a2a2a;border-radius:8px;cursor:pointer;';
        cancel.onclick=()=>cleanup(); row.appendChild(cancel);
        const mkNum=(ph)=>{ const i=document.createElement('input'); i.type='number'; i.step='0.1'; i.min='0'; i.inputMode='decimal'; i.placeholder=ph;
          i.style.cssText='width:88px;min-height:44px;padding:8px 10px;background:#181818;border:1px solid #2a2a2a;border-radius:8px;color:#eee;font:16px Segoe UI,system-ui,sans-serif;'; return i; };
        const wIn=mkNum('width'), hIn=mkNum('height'), times=document.createElement('span'); times.textContent='×';
        const uu=document.createElement('span'); uu.textContent='mm'; uu.style.color='#9aa';
        custom.append(wIn, times, hIn, uu, mkBtn('Set','primary', ()=>{ if(applyWH(parseFloat(wIn.value), parseFloat(hIn.value)) ) box.remove(); }));
        box.append(title, sub, row, custom); document.body.appendChild(box);
        box.addEventListener('keydown',(e)=>{ e.stopPropagation(); if(e.key==='Escape'){ e.preventDefault(); cleanup(); } });
      }

      // Offer a one-tap scale from a sheet of paper the server auto-detected (its corners
      // arrive in data.detected_rectangle). Purely a suggestion: the user confirms the real
      // size (A4/Letter) — the app can't tell those two apart — and can dismiss. Only ever
      // shown when nothing else has calibrated the image, and once per photo.
      _offerDetectedPaper(){
        const rect = this.data && this.data.detected_rectangle;
        if(!rect || !Array.isArray(rect.corners) || rect.corners.length!==4) return;
        if(this.opts.manualHomography || this.opts.manualMmPerPx) return;   // already scaled
        if(this.data.calibration_confidence === 'high') return;             // trust a real square
        if(this._paperOffered) return; this._paperOffered = true;           // once per image

        const corners = rect.corners.map(p => [Number(p[0]), Number(p[1])]);
        this._previewDetectedRect = corners;   // drawn by _drawDetectedRect so the user sees it
        this.requestDraw();

        const finish = (fn)=>{ const b=document.getElementById('cal-paper-offer'); if(b) b.remove();
                               this._previewDetectedRect = null; if(fn) fn(); this.requestDraw(); };
        const choose = (wmm,hmm)=>{ if(this._applyRectScale(corners, wmm, hmm)) finish(()=>this.setMode('select'));
                                    else finish(); };

        const box=document.createElement('div'); box.id='cal-paper-offer';
        box.style.cssText='position:fixed;z-index:42;left:50%;top:12px;transform:translateX(-50%);background:#0e0e0e;border:2px solid #00d4ff;border-radius:12px;padding:12px 14px;box-shadow:0 8px 28px rgba(0,0,0,.6);max-width:calc(100vw - 16px);font:14px Segoe UI,system-ui,sans-serif;color:#eee;';
        const t=document.createElement('div');
        t.innerHTML='📄 <b>Sheet of paper detected.</b> Use it to set a tilt-corrected scale?';
        t.style.cssText='margin-bottom:8px;';
        const row=document.createElement('div'); row.style.cssText='display:flex;flex-wrap:wrap;gap:8px;align-items:center;';
        const guess = rect.guess==='letter' ? 'letter' : 'a4';
        const mkB=(label,primary,fn)=>{ const b=document.createElement('button'); b.type='button'; b.textContent=label;
          b.style.cssText='min-height:44px;padding:8px 14px;border-radius:8px;font-size:14px;cursor:pointer;'+(primary
            ?'background:#00d4ff;color:#000;border:none;font-weight:600;'
            :'background:#151515;color:#cde;border:1px solid #2a3540;'); b.onclick=fn; return b; };
        row.appendChild(mkB('A4 · 210×297', guess==='a4', ()=>choose(210,297)));
        row.appendChild(mkB('US Letter · 216×279', guess==='letter', ()=>choose(215.9,279.4)));
        row.appendChild(mkB('No thanks', false, ()=>finish()));
        box.append(t,row); document.body.appendChild(box);
        // Stack below the quality nudge if it's showing, so the two banners don't overlap.
        const qb=document.getElementById('cal-quality');
        if(qb) box.style.top = (Math.round(qb.getBoundingClientRect().bottom)+8)+'px';
      }

      // A gentle, dismissable capture-quality nudge shown once on load when the photo is
      // soft or dark (server-computed in data.quality). Never blocks — a customer can
      // "Use it anyway" — because a false positive on a low-texture part shouldn't trap
      // them; it just makes the accuracy cost of a bad photo visible before they measure.
      _showQualityHint(){
        const q = this.data && this.data.quality; if(!q || this._qualityShown) return;
        const issues=[]; if(q.blurry) issues.push('looks a little soft'); if(q.dark) issues.push('looks dark');
        if(!issues.length) return; this._qualityShown=true;
        const box=document.createElement('div'); box.id='cal-quality';
        box.style.cssText='position:fixed;z-index:43;left:50%;top:12px;transform:translateX(-50%);background:#1a1407;border:2px solid #e0a24a;border-radius:12px;padding:10px 14px;box-shadow:0 8px 28px rgba(0,0,0,.6);max-width:calc(100vw - 16px);font:14px Segoe UI,system-ui,sans-serif;color:#f0e6d0;';
        const t=document.createElement('div');
        t.innerHTML='📷 This photo '+issues.join(' and ')+' — a sharper, well-lit shot measures more accurately.';
        t.style.cssText='margin-bottom:8px;';
        const row=document.createElement('div'); row.style.cssText='display:flex;flex-wrap:wrap;gap:8px;';
        const mk=(label,primary,fn)=>{ const b=document.createElement('button'); b.type='button'; b.textContent=label;
          b.style.cssText='min-height:40px;padding:7px 13px;border-radius:8px;font-size:13px;cursor:pointer;'+(primary
            ?'background:#e0a24a;color:#1a1206;border:none;font-weight:600;'
            :'background:#241d10;color:#f0e6d0;border:1px solid #4a3d1f;'); b.onclick=fn; return b; };
        row.appendChild(mk('Upload another', true, ()=>{ try{ (window.CalibJob && window.CalibJob.beforeReload ? window.CalibJob.beforeReload() : Promise.resolve()).then(() => window.location.reload()); }catch(e){ window.location.reload(); } }));
        row.appendChild(mk('Use it anyway', false, ()=>{ box.remove();
          const po=document.getElementById('cal-paper-offer'); if(po) po.style.top='12px'; }));
        box.append(t,row); document.body.appendChild(box);
      }

      // Draw the auto-detected sheet's outline (a dashed ring + corner dots) so the user
      // can see WHAT was detected before accepting it as a scale.
      _drawDetectedRect(){
        const q=this._previewDetectedRect; if(!q || q.length!==4) return;
        const c=this.ctx;
        c.save();
        c.setLineDash([12,8]); c.strokeStyle='rgba(0,212,255,.95)'; c.lineWidth=Draw.px(this.canvas,2.5);
        c.beginPath(); c.moveTo(q[0][0],q[0][1]); for(let i=1;i<4;i++) c.lineTo(q[i][0],q[i][1]); c.closePath(); c.stroke();
        c.setLineDash([]); c.fillStyle='rgba(0,212,255,.95)';
        for(const [x,y] of q){ c.beginPath(); c.arc(x,y,Draw.px(this.canvas,6),0,Math.PI*2); c.fill(); }
        c.restore();
      }

      _precisionMode(){ const m=this.opts.mode; return m==='setscale'||m==='verifyscale'||m==='setscalerect'; }

      // The point the loupe should magnify: the one being dragged, else the hover point,
      // while in a precision scale mode and no size/entry dialog is covering the view.
      _loupeTarget(){
        if(!this._precisionMode()) return null;
        if(document.getElementById('cal-rect-input') || document.getElementById('cal-scale-input') ||
           document.getElementById('cal-verify') || document.getElementById('cal-paper-offer')) return null;
        if(this._selDrag!=null && this.selectedPoints[this._selDrag]) return this.selectedPoints[this._selDrag];
        return this.hover || null;
      }

      // A magnifier loupe: a circular, zoomed inset of the image centred on the target
      // point with a crosshair — so a fingertip that covers the pixel it is placing can
      // still see exactly where the point lands. Drawn in screen space, over everything.
      _drawLoupe(){
        const pt=this._loupeTarget(); if(!pt || !this.img) return;
        const c=this.ctx;
        const [pcx,pcy]=this.vp.imageToCanvas(pt[0], pt[1]);   // device px
        const R=Draw.px(this.canvas,54), zoom=2.6, off=Draw.px(this.canvas,16), pad=R+Draw.px(this.canvas,6);
        // Default above-left of the target; flip to the other side if that would clip.
        let lx=pcx-R-off, ly=pcy-R-off;
        if(lx<pad) lx=pcx+R+off;
        if(ly<pad) ly=pcy+R+off;
        lx=Math.max(pad, Math.min(this.canvas.width-pad, lx));
        ly=Math.max(pad, Math.min(this.canvas.height-pad, ly));

        c.save();
        c.setTransform(1,0,0,1,0,0);
        c.beginPath(); c.arc(lx,ly,R,0,Math.PI*2); c.clip();
        c.fillStyle='#0d0d0f'; c.fillRect(lx-R,ly-R,2*R,2*R);
        const K=this.vp.k*zoom;                          // magnified image->device scale
        c.setTransform(K,0,0,K, lx-pt[0]*K, ly-pt[1]*K); // centre pt under the loupe
        c.imageSmoothingEnabled=true;
        c.drawImage(this.img,0,0);
        c.restore();

        c.save(); c.setTransform(1,0,0,1,0,0);
        c.beginPath(); c.arc(lx,ly,R,0,Math.PI*2); c.lineWidth=Draw.px(this.canvas,2.5); c.strokeStyle='rgba(0,212,255,.95)'; c.stroke();
        const ch=Draw.px(this.canvas,9);
        c.lineWidth=Draw.px(this.canvas,1.5); c.strokeStyle='rgba(255,70,70,.95)';
        c.beginPath(); c.moveTo(lx-ch,ly); c.lineTo(lx+ch,ly); c.moveTo(lx,ly-ch); c.lineTo(lx,ly+ch); c.stroke();
        c.restore();
      }

      // Verify the active scale against a SECOND known feature. The user draws a line
      // across something whose real size they know; we measure it at the CURRENT scale
      // (perspective-corrected, same path as every other measurement) and show expected
      // vs actual with a signed % error. Nothing else catches a wrong-size square or a
      // mis-typed length — both read "calibrated" and export silently wrong.
      _promptVerify(a, b){
        const done=()=>{ const bx=document.getElementById('cal-verify'); if(bx) bx.remove();
                         this.selectedPoints=[]; this.hover=null; this.setMode('select'); this.requestDraw(); };
        const prev=document.getElementById('cal-verify'); if(prev) prev.remove();
        const box=document.createElement('div'); box.id='cal-verify';
        box.style.cssText='position:fixed;z-index:41;left:50%;top:14%;transform:translateX(-50%);background:#0e0e0e;border:2px solid #00d4ff;border-radius:10px;padding:12px 14px;box-shadow:0 8px 24px rgba(0,0,0,.6);max-width:calc(100vw - 16px);font:15px Segoe UI,system-ui,sans-serif;color:#eee;';
        const primaryBtn=(label,fn)=>{ const b2=document.createElement('button'); b2.type='button'; b2.textContent=label;
          b2.style.cssText='min-height:44px;padding:8px 16px;background:#00d4ff;color:#000;border:none;border-radius:8px;font-weight:600;font-size:15px;cursor:pointer;'; b2.onclick=fn; return b2; };
        document.body.appendChild(box);

        if(!this.isCalibrated()){
          const m=document.createElement('div'); m.textContent='Set a scale first, then verify it.'; m.style.cssText='margin-bottom:10px;';
          box.append(m, primaryBtn('OK', done)); return;
        }

        const unit=Units.get(this.opts.units);
        const measuredMM=Measure.length(this._measureCtx(null), a[0],a[1], b[0],b[1]);

        const title=document.createElement('div'); title.textContent='Check scale — this line is really:'; title.style.cssText='font-weight:600;margin-bottom:8px;';
        const row=document.createElement('div'); row.style.cssText='display:flex;flex-wrap:wrap;gap:8px;align-items:center;';
        const inp=document.createElement('input'); inp.type='number'; inp.step='0.01'; inp.min='0'; inp.inputMode='decimal'; inp.placeholder='known length';
        inp.style.cssText='flex:1 1 120px;min-width:120px;min-height:44px;padding:8px 10px;background:#181818;border:1px solid #2a2a2a;border-radius:8px;color:#eee;font:16px Segoe UI,system-ui,sans-serif;';
        const us=document.createElement('span'); us.textContent=unit.label; us.style.color='#9aa';
        const closeBtn=document.createElement('button'); closeBtn.type='button'; closeBtn.textContent='Done';
        closeBtn.style.cssText='min-height:44px;padding:8px 14px;background:#181818;color:#eee;border:1px solid #2a2a2a;border-radius:8px;cursor:pointer;'; closeBtn.onclick=done;
        const result=document.createElement('div'); result.style.cssText='margin-top:10px;font-size:14px;line-height:1.55;';

        const check=()=>{
          const v=parseFloat(inp.value); if(!(v>0)){ result.textContent=''; return; }
          const expected=unit.toMM(v);
          const pct=(measuredMM-expected)/expected*100, ap=Math.abs(pct);
          const color = ap<=2 ? '#3ecf8e' : (ap<=5 ? '#e2a24c' : '#ff6b6b');
          const verdict = ap<=2 ? '✓ scale looks good' : (ap<=5 ? '⚠ a little off — double-check' : '⚠ off — re-check your scale or the size you entered');
          result.innerHTML =
            `<div style="color:${color};font-weight:700;">${verdict}</div>`+
            `<div style="color:#cdd;">measured <b>${unit.fromMM(measuredMM).toFixed(2)} ${unit.label}</b> vs expected <b>${v.toFixed(2)} ${unit.label}</b></div>`+
            `<div style="color:${color};font-weight:600;font-variant-numeric:tabular-nums;">${pct>=0?'+':''}${pct.toFixed(1)}%</div>`;
        };
        row.append(inp, us, primaryBtn('Check', check), closeBtn);
        box.append(title, row, result);
        inp.addEventListener('keydown',(e)=>{ e.stopPropagation(); if(e.key==='Enter'){ e.preventDefault(); check(); } else if(e.key==='Escape'){ e.preventDefault(); done(); } });
        setTimeout(()=>{ inp.focus(); },0);
      }

      // Live guidance for the active tool, e.g. "Angle — click point 2 of 3", so a
      // user (especially on mobile, where hover tooltips don't exist) always knows
      // how many clicks the current tool needs and where they are in the sequence.
      _toolHint(){
        const m=this.opts.mode;
        const defs={ segment:['Measure',2], setscale:['Set scale',2], rectangle:['Area',2],
                     circle:['Circle',2], angle:['Angle',3], circle3pt:['3-pt circle',3],
                     note:['Note',1], polyline:['Path',0], pan:['Pan',0], select:['Select',0],
                     setscalerect:['Paper scale',4], verifyscale:['Verify scale',2] };
        const d=defs[m]; if(!d) return '';
        const [name,total]=d, n=this.selectedPoints.length;
        if(m==='setscalerect') return n>=4 ? '✏️ Paper scale — pick the paper size'
                                           : (n ? `✏️ Paper scale — corner ${Math.min(n+1,4)} of 4`
                                                : '✏️ Paper scale — tap the 4 corners of the sheet');
        if(m==='verifyscale') return n>=2 ? '✏️ Verify — enter the real length'
                                          : (n ? '✏️ Verify — click the 2nd point'
                                               : '✏️ Verify — draw across something of known size');
        if(m==='pan') return '';
        if(m==='select') return this.ann.selectedId!=null ? '✏️ Select — item selected (Delete removes it)' : '✏️ Select — tap a measurement';
        if(m==='note') return '✏️ Note — tap to place';
        if(m==='polyline') return n ? `✏️ Path — ${n} point(s) · double-tap or Enter to finish` : '✏️ Path — tap to start';
        return `✏️ ${name} — click point ${Math.min(n+1,total)} of ${total}`;
      }

      // The bottom status strip. Built as wrapping chips (a single DOM path for the
      // calibrated and uncalibrated cases) so nothing truncates the way the old
      // white-space:nowrap line did — critically, the "Set a scale" recovery action is a
      // real <button> that always stays tappable on a phone. After building, the strip's
      // measured height is fed back into --cal-kpi-h so the space reserved for it at the
      // bottom of the view tracks its real (possibly two-line) height.
      updateKPI(){
        const el=document.getElementById('cal-kpi'); if(!el) return;
        const s=this.getScale()||0; const unit=Units.get(this.opts.units);
        const narrow = window.innerWidth < 480;

        // Assemble the chips as data first so we can skip the whole DOM rebuild (and the
        // offsetHeight reflow) on frames where nothing shown changed — idle redraws, pans
        // and hovers don't churn the strip.
        const specs=[]; // {text, cls?, action?}
        const rd=this._selectedReadout();
        if(rd){ specs.push({text:`🔎 ${rd.label}: ${rd.text}`, cls:'cal-chip--lead'}); specs.push({text:'C to copy', cls:'cal-chip--muted'}); }
        else { const th=this._toolHint(); if(th) specs.push({text:th, cls:'cal-chip--lead'}); }

        if(s<=0){
          specs.push({text:'⚠️ Not calibrated', cls:'cal-chip--warn'});
          specs.push({text:'📐 Set a scale', cls:'cal-chip--action', action:true});
        }else{
          // A manual scale is user-defined (trusted); only auto-detected marker scale
          // carries a confidence, so only it can be flagged approximate.
          const lowConf = !this.opts.manualMmPerPx && this.data && this.data.calibration_confidence==='low';
          if(lowConf) specs.push({text:'⚠️ Approximate — verify with Set scale', cls:'cal-chip--warn'});
          // Flag a marker size restored from a previous photo so a stale remembered value
          // can't silently rescale an unrelated image without the user noticing.
          const remembered = !this.opts.manualMmPerPx && this._markerSizeFromMemory;
          const src = this.opts.manualHomography
                        ? (this._manualRef && this._manualRef.kind==='squared' ? `squared up ${this._manualRef.tilt_deg}°`
                           : this._manualRef ? `paper ${Math.round(this._manualRef.w)}×${Math.round(this._manualRef.h)} mm` : 'paper')
                        : (this.opts.manualMmPerPx ? 'manual line' : `${this.currentMarkerSizeMM()??'—'} mm sq`);
          const unitPerPx=unit.fromMM(s);
          specs.push({text: narrow ? `${unitPerPx.toPrecision(3)} ${unit.label}/px`
                                    : `Scale ${unitPerPx.toFixed(6)} ${unit.label}/px`});
          // WHICH PLANE we are measuring in changes every number on screen, so it is never
          // hidden — not even on a narrow phone. Silence here would mean a raised face and
          // the card's own plane looked identical while reading several percent apart.
          const fh = this.featureHeightMM();
          if(fh) specs.push({text:`⬆ measuring ${fh} mm above the card`, cls:'cal-chip--lead'});
          // A scale from a single drawn line (a ruler, a card edge) is UNIFORM: it is right
          // along the line, at the line's distance, and degrades everywhere else. Measured on
          // synthetic renders at 40 deg of tilt, a true 300 mm length read 232 mm measured
          // across the line and 150 mm further down the part. The absence of the
          // "perspective-corrected" chip was the only hint, and that chip is hidden on a
          // phone anyway — so say it outright, at every width.
          if(this.opts.manualMmPerPx && !this.opts.manualHomography)
            specs.push({text:'↔ uniform scale — not tilt-corrected', cls:'cal-chip--warn'});
          // Measuring far outside the reference is a correctness problem, not a nicety, so
          // it shows at every width — a 40 mm card on a 1.5 m part reads plausibly and is
          // meaningless, and nothing else on screen would hint at that.
          const rs = this.referenceSpanWarning();
          if(rs) specs.push({text:`${rs.level==='bad'?'⛔':'⚠️'} reference is ${Math.round(rs.ratio)}× too small for this span`,
                             cls:'cal-chip--warn'});
          // Remembered-size flag shows on EVERY width (including phones) — a stale value
          // silently rescaling an unrelated photo is a correctness footgun, so never hide it.
          if(remembered) specs.push({text:`⚠️ saved ${this.currentMarkerSizeMM()} mm sq — tap 📐 to verify`, cls:'cal-chip--warn'});
          if(!narrow) specs.push({text:`ref ${src}`});
          // A manual LINE scale is a single uniform mm/px with NO perspective correction,
          // so a tilted photo measures short — surface the flat-on nudge for that path
          // ONLY. A paper-rectangle scale (manualHomography) rectifies tilt, so it doesn't
          // need the warning and instead earns the ⟂ perspective-corrected chip below.
          if(this.opts.manualMmPerPx && !this.opts.manualHomography) specs.push({text:'📐 Manual scale — shoot flat-on for accuracy', cls:'cal-chip--muted'});
          // Only linear measurements (lengths/areas/angles) are rectified for tilt;
          // circles stay on the uniform scale (see _selectedReadout).
          if(!narrow && this._perspectiveActive()) specs.push({text:'⟂ perspective-corrected', cls:'cal-chip--ok'});
        }
        // On a phone keep the strip to ~two lines: lead + calibration state (both
        // load-bearing) plus zoom, dropping the secondary chips. All of it stays wider.
        specs.push({text:`Zoom ${Math.round(this.vp.k*100)}%`});
        if(!narrow){ const n=this.ann.items.length; specs.push({text:`Snap ${this.opts.snap?'on':'off'}`}); specs.push({text:`${n} item${n===1?'':'s'}`}); }

        const sig = specs.map(x=>(x.cls||'')+'|'+x.text).join('§');
        if(sig===this._kpiSig) return;   // unchanged → no rebuild, no reflow, no announce
        this._kpiSig=sig;

        el.textContent='';
        for(const x of specs){
          if(x.action){
            const b=document.createElement('button'); b.type='button'; b.className='cal-chip cal-chip--action';
            b.textContent=x.text; b.setAttribute('aria-label','Set a scale');
            b.onclick=()=>{ if(this._openScalePanel){ this._openScalePanel(); } else { this.setMode('setscale'); } };
            el.appendChild(b);
          }else{
            const sp=document.createElement('span'); sp.className='cal-chip'+(x.cls?' '+x.cls:''); sp.textContent=x.text; el.appendChild(sp);
          }
        }

        // Feed the real height back into --cal-kpi-h so the .cal-view bottom reservation and
        // the settings-sheet offset track a strip that wrapped to two lines — but ONLY when
        // settled (not mid-drag/placement). Writing it resizes the canvas box, and doing so
        // every frame while dragging an endpoint re-fit the image and made it visibly jump;
        // a redraw after the gesture updates it. _suppressRefit tells the ResizeObserver to
        // absorb the box change this write causes rather than re-fitting from it.
        if(!this.drag && !this.selectedPoints.length){
          const h=Math.round(el.offsetHeight);
          if(h>0 && h!==this._kpiH){
            this._kpiH=h; this._suppressRefit=true;
            document.documentElement.style.setProperty('--cal-kpi-h', h+'px');
          }
        }
      }

      _exportStore(){ return { items: this.ann.items.filter(Boolean) }; }
      _imgH(){ return this.img ? (this.img.naturalHeight || this.img.height) : 0; }
      // Gated like saveCSV/saveDXF/saveSVG: an uncalibrated PNG now labels honestly in pixels,
      // and the confirm says exactly that, so the customer isn't handed a scale-less drawing
      // believing it carries millimetres.
      savePNG(){ if(!this._confirmUncalibrated()) return; Xport.exportPNG(this.img, this._dataForMeasure(), this._exportStore(), this.opts.showGrid, this.opts.showMarkers, this.opts.units, this.opts.labelScale, this.opts.linePx, this._allowHomography()); }
      saveJSON(){
        // The exported homography maps to different spaces depending on its source: the
        // auto marker homography maps px -> UNIT square (scale by marker_size_mm), while a
        // manual paper-rectangle homography maps px -> mm directly (marker_size_mm=1). Tag
        // which, so a consumer reproduces the same perspective-corrected mm the app showed.
        const rect=this.opts.manualHomography;
        const payload=Ann.toExportJSON(this.imgSrc, {
          marker_size_mm: rect ? 1 : (this.data?.marker_size_mm??null),
          mm_per_px:this.getScale()||(this.data?.mm_per_px??null),
          pixels_per_mm:this.data?.pixels_per_mm??null,
          manual_scale:this.opts.manualMmPerPx||null,
          homography: rect ? rect : ((!this.opts.manualMmPerPx && this.data?.homography) ? this.data.homography : null),
          homography_maps: rect ? 'px_to_mm' : ((!this.opts.manualMmPerPx && this.data?.homography) ? 'px_to_unit_square' : null),
          manual_reference: !this._manualRef ? null : (this._manualRef.kind==='squared'
            ? { squared_up: true, tilt_deg:this._manualRef.tilt_deg, focal_source:this._manualRef.focal_source }
            : { width_mm:this._manualRef.w, height_mm:this._manualRef.h }),
          markers:this.data?.markers??[]
        }, this._exportStore(), this.opts.units);
        Xport.exportJSON(payload);
      }
      _confirmUncalibrated(){
        return this.isCalibrated() || confirm('Not calibrated — exported values will be in pixels, not millimetres. Set a scale first for real measurements. Export anyway?');
      }
      saveCSV(){ if(!this._confirmUncalibrated()) return; Xport.exportCSV(this._dataForMeasure(), this._exportStore(), this.opts.units, this.getScale(), this._allowHomography()); }
      saveDXF(){ if(!this._confirmUncalibrated()) return; return Xport.exportDXF(this._dataForMeasure(), this._exportStore(), { image_height:this._imgH(), mm_per_px:this.getScale()||this.data?.mm_per_px||0, allowHomography:this._allowHomography() }); }
      // A watertight solid, ready to slice. Every other export here is flat, so for an
      // extruded part this is the one that finishes the job instead of handing the shop a
      // drawing to re-draw. Needs a closed outline carrying a depth; any other closed
      // outline inside it (a bolt hole, a clip slot — the auto-trace already emits these)
      // becomes a through-hole.
      async saveSTL(){
        if(!this._confirmUncalibrated()) return false;
        const req = Xport._buildDXFRequest(this._dataForMeasure(), this._exportStore(),
                      { image_height:this._imgH(), mm_per_px:this.getScale()||this.data?.mm_per_px||0,
                        allowHomography:this._allowHomography() });
        const hasProfile = (req.geometry||[]).some(g => g.type==='polyline' && g.closed && g.depth_mm>0);
        if(!hasProfile){
          alert('No extrudable profile yet.\n\nTrace a closed outline around the part, select it, '
              + 'then use ⇥ Depth to say how far the shape runs. That outline plus the depth is '
              + 'the solid.');
          return false;
        }
        try{
          const r = await fetch(Xport._apiUrl('api/export/stl'), {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({ geometry:req.geometry, mm_per_px:req.mm_per_px,
                                   image_height:req.image_height })});
          if(!r.ok){ alert('3D export failed: ' + (await r.text())); return false; }
          const blob = await r.blob();
          const a=document.createElement('a'); a.download='part.stl';
          a.href=URL.createObjectURL(blob); a.click();
          setTimeout(()=>URL.revokeObjectURL(a.href), 1000);
          const n=r.headers.get('X-CamScan-Triangles'), v=r.headers.get('X-CamScan-Volume-MM3');
          const kind=r.headers.get('X-CamScan-Kind'), note=r.headers.get('X-CamScan-Note');
          const bossed=Number(r.headers.get('X-CamScan-Bossed-Holes')||0);
          if(n && v){
            // Say what was actually built: a cover comes out plate-down, and a hole near the
            // wall gets a solid boss — both worth knowing before it is on the printer.
            let msg = kind==='shell'
              ? `Cover STL saved — ${(Number(v)/1000).toFixed(1)} cm³, plate-down ready to print`
              : `STL saved — ${n} triangles, ${(Number(v)/1000).toFixed(1)} cm³`;
            if(bossed>0) msg += ` · ${bossed} hole${bossed>1?'s':''} near the wall got a solid boss`;
            if(note) msg += ` · ${note}`;
            this._toast(msg, {autoMs: (bossed>0 || note) ? 6000 : 3200});
          }
          return true;
        }catch(e){ alert('3D export needs the server. ('+e.message+')'); return false; }
      }

      saveSVG(){ if(!this._confirmUncalibrated()) return; return Xport.exportSVG(this._dataForMeasure(), this._exportStore(), { mm_per_px:this.getScale()||this.data?.mm_per_px||0, allowHomography:this._allowHomography() }); }
    }

    window.CalibrationOverlay = window.CalibrationOverlay || CalibrationOverlay;

    function initScan(){
      document.querySelectorAll('.cal-view').forEach(el=>{
        // Prefer data-img; fall back to data-img-fallback if needed (important for HTTPS/proxy)
        const primary = el.getAttribute('data-img');
        const fallback = el.getAttribute('data-img-fallback');
        const img = primary || fallback;

        const json=el.getAttribute('data-json');
        const canvas=el.querySelector('canvas');
        if(!img||!json||!canvas) return;
        // (Re)initialize when this is a fresh node OR when Dash/React REUSED the node and
        // swapped in a new photo (data-img changed). Dash reconciles #viewer's subtree in
        // place on each upload — the node is NOT removed/re-added — so keying only on
        // :not([data-initialized]) left the first overlay (and its FIRST image) in place, and
        // every "Add another photo" then captured that same first photo. Reboot on a changed
        // image, tearing down the stale overlay first so nothing leaks.
        if(el.getAttribute('data-initialized')==='1' && el.__bootedImg===img) return;
        if(el.__overlay && el.__overlay.destroy){ try{ el.__overlay.destroy(); }catch(e){} }
        el.setAttribute('data-initialized','1');
        el.__bootedImg = img;
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
    // Coalesce bursts of mutations into ONE rescan per frame. The observer watches the whole
    // document (so it survives Dash replacing the #viewer subtree), and unrelated churn — the
    // KPI chip rebuilds on every drag frame — would otherwise run a full-document initScan per
    // mutation. reapRemoved must run synchronously (the removed nodes aren't in the DOM by the
    // next frame); only the rescan is deferred.
    let _scanQueued=false;
    function queueScan(){
      if(_scanQueued) return;
      _scanQueued=true;
      requestAnimationFrame(()=>{ _scanQueued=false; initScan(); });
    }
    new MutationObserver((mutations)=>{
      for(const m of mutations) if(m.removedNodes && m.removedNodes.length) reapRemoved(m.removedNodes);
      queueScan();
    // Watch data-img: when Dash reuses the .cal-view node and only swaps the image attribute
    // for a new photo, there is no childList change to trigger a rescan. data-img (the image
    // data URL) is the photo's identity, so watching it alone is sufficient and precise.
    }).observe(document.documentElement,{childList:true,subtree:true,attributes:true,attributeFilter:['data-img']});
  });
})();
