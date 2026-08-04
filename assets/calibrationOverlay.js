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
        btn.onclick=()=>{ try{ window.location.reload(); }catch(e){} };
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
          this.ann.selectedId=null; this.hover=null; this._invalidateTraceAnchor(); this.requestDraw();
        }
      }
      redo(){
        if(this._redoStack.length){
          this._undoStack.push(this._snapshot());
          this.ann.items=this._redoStack.pop();
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
        if(this.opts.showMarkers) Draw.drawMarkers(c, this.canvas, this.data, this.opts.linePx);
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

      _drawAnnotations(){
        if(!this.opts.showAnn) return;
        const c=this.ctx, unit=Units.get(this.opts.units);
        const C=Draw.colors;
        const dotR = this._handleR(), linePx=Draw.px(this.canvas,this.opts.linePx);
        const showLabels = this.opts.showLabels !== false;
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
            if(showLabels) Draw.boxLabel(c, this.canvas, mid[0], mid[1], this._fmtLen(mm, pxLen), this.opts.labelScale, C.segment);
          } else if(a.type==='note'){
            const [tx,ty]=a.p; c.fillStyle=sel?C.selected:C.note;
            c.beginPath(); c.arc(tx,ty,this._handleR(1.6),0,Math.PI*2); c.fill();
            c.strokeStyle="#000"; c.lineWidth=Draw.px(this.canvas,2); c.stroke();
            if(a.text && showLabels){ const f=Math.round(18*this.opts.labelScale); const pad=8*this.opts.labelScale; const lx=tx+14;
              c.font=Draw.font(f); c.textAlign="left"; c.textBaseline="middle";
              const boxW=c.measureText(a.text).width + 2*pad, boxH=f+2*pad, ly=ty-boxH/2;
              c.fillStyle="rgba(0,0,0,.72)"; c.fillRect(lx,ly,boxW,boxH);
              c.strokeStyle="rgba(255,255,255,.35)"; c.lineWidth=Draw.px(this.canvas,1.5); c.strokeRect(lx,ly,boxW,boxH);
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
            const hR = sel ? this._handleR(1.5) : dotR, ringW = Draw.px(this.canvas,1.5);
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
              Draw.boxLabel(c, this.canvas, ax, ay, label, this.opts.labelScale, C.polyline);
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
            if(showLabels) Draw.boxLabel(c, this.canvas, (x1+x2)/2, y1-10, rlabel, this.opts.labelScale, C.rectangle);
          } else if(a.type==='angle'){
            c.lineWidth=sel?selW:linePx; c.strokeStyle=sel?C.selected:C.angle;
            c.beginPath(); c.moveTo(a.v[0],a.v[1]); c.lineTo(a.a[0],a.a[1]); c.moveTo(a.v[0],a.v[1]); c.lineTo(a.b[0],a.b[1]); c.stroke();
            const ang=Measure.angle(this._measureCtx(a), a.a, a.v, a.b); if(showLabels) Draw.boxLabel(c, this.canvas, a.v[0], a.v[1]-20, `θ ${ang.toFixed(2)}°`, this.opts.labelScale, C.angle);
          } else if(a.type==='circle'){
            // Shared with the PNG exporter so the live canvas and the export never
            // drift (line thickness, label size/position all come from opts here).
            if(window.CalibCircles){
              window.CalibCircles.drawCircle(c, this.canvas, a, this._dataForMeasure(), this.opts.units,
                { selected: sel, labelScale: this.opts.labelScale, linePx: this.opts.linePx,
                  fallbackScale: this.getScale(), allowHomography: this._allowHomography(),
                  showLabel: showLabels, handleR: this._handleR(1.2) });
            }
          }
        }
      }

      _drawPreview(){
        if(this.selectedPoints.length===0 && !this.hover) return;
        const c=this.ctx, unit=Units.get(this.opts.units);
        const linePx=Draw.px(this.canvas,this.opts.linePx), dotR=this._handleR();
        // Snap cursor: when snapping is on, ring the point the next click will land on
        // (marker corner or an existing measurement's endpoint) so chaining is visible.
        if(this.opts.snap && this.hover && this.opts.mode!=='pan' && this.opts.mode!=='select'){
          c.save(); c.strokeStyle='rgba(0,212,255,.95)'; c.lineWidth=Draw.px(this.canvas,2);
          c.beginPath(); c.arc(this.hover[0], this.hover[1], this._handleR(1.6), 0, Math.PI*2); c.stroke();
          c.restore();
        }
        c.fillStyle='orange'; c.strokeStyle='rgba(255,200,0,.9)'; c.lineWidth=linePx;
        for(const [x,y] of this.selectedPoints){ c.beginPath(); c.arc(x,y,dotR,0,Math.PI*2); c.fill(); c.strokeStyle='#000'; c.lineWidth=Draw.px(this.canvas,2); c.stroke(); }
        const H=this.hover;
        if((this.opts.mode==='segment'||this.opts.mode==='setscale'||this.opts.mode==='verifyscale') && this.selectedPoints.length===1 && H){ const a=this.selectedPoints[0], b=H;
          c.save(); c.setLineDash([10,8]); c.beginPath(); c.moveTo(a[0],a[1]); c.lineTo(b[0],b[1]); c.stroke(); c.restore();
          const mid=[(a[0]+b[0])/2,(a[1]+b[1])/2];
          if(this.opts.mode==='setscale'){
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `set scale — click 2nd point`, this.opts.labelScale);
          } else if(this.opts.mode==='verifyscale'){
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `verify — click 2nd point`, this.opts.labelScale);
          } else {
            const mm=Measure.length(this._measureCtx(null), a[0],a[1], b[0],b[1]);
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `~${this._fmtLen(mm, Math.hypot(b[0]-a[0], b[1]-a[1]))}`, this.opts.labelScale);
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
          Draw.boxLabel(c, this.canvas, mid[0], mid[1], need>0?`tap ${need} more corner${need===1?'':'s'}`:`pick paper size`, this.opts.labelScale);
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
        if(a.type==='segment'){ const px=Math.hypot(a.b[0]-a.a[0],a.b[1]-a.a[1]); const t=this._fmtLen(Measure.length(ctx,a.a[0],a.a[1],a.b[0],a.b[1]),px); return {label:'Length', text:t, copy:cal?unit.fromMM(Measure.length(ctx,a.a[0],a.a[1],a.b[0],a.b[1])).toFixed(3):String(Math.round(px))}; }
        if(a.type==='polyline'){ const pts=a.pts||[]; const closed=!!a.closed && pts.length>=3;
          let px=0; for(let i=1;i<pts.length;i++) px+=Math.hypot(pts[i][0]-pts[i-1][0],pts[i][1]-pts[i-1][1]);
          if(closed) px+=Math.hypot(pts[0][0]-pts[pts.length-1][0], pts[0][1]-pts[pts.length-1][1]);
          const mm=Measure.polyline(ctx,pts,closed);
          if(closed){
            if(cal){ const ar=unit.areaFromMM2(Measure.polygonArea(ctx,pts)); return {label:'Outline', text:`peri ${this._fmtLen(mm,px)} · A ${ar.toFixed(3)} ${unit.areaLabel}`, copy:unit.fromMM(mm).toFixed(3)}; }
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
      getMeasurements(){ return this.ann.items.map(a=>this._readoutFor(a)).filter(Boolean).map(r=>({label:r.label, text:r.text})); }
      // A plain-language description of how THIS view is scaled, so the shop can trust it.
      getScaleInfo(){
        const s=this.getScale()||0; const unit=Units.get(this.opts.units);
        let source='none';
        if(this.opts.manualHomography) source='paper/rectangle';   // perspective flag conveys tilt-correction
        else if(this.opts.manualMmPerPx) source='manual line';
        else if(this.currentMarkerSizeMM()) source=`printed square ${this.currentMarkerSizeMM()} mm`;
        return { calibrated:s>0, mm_per_px:s, unit:unit.label,
                 perspective:this._perspectiveActive(), source,
                 confidence:(this.data&&this.data.calibration_confidence)||null };
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
            ref:this._scaleRef(this._manualRef, s)
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
        for(const a of this.ann.items){
          if(a.type==='note') continue;
          const s=this._scaleForPoint(this._annCentroid(a));
          if(s>0) a.mm_per_px=s;
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
        this.opts.manualHomography = null; this._manualRef = null;
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
        // A line scale is uniform — drop any paper-rectangle homography so we don't claim
        // tilt correction the single line can't provide.
        this.opts.manualHomography = null; this._manualRef = null;
        this.opts.manualMmPerPx = knownMM/pixelLength;
        this._rescaleAnnotations();
        this.requestDraw();
      }
      clearManualScale(){ this.opts.manualMmPerPx = null; this.opts.manualHomography = null; this._manualRef = null; this._rescaleAnnotations(); this.requestDraw(); }
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
          'font:600 13px Segoe UI,system-ui,sans-serif;box-shadow:0 8px 28px rgba(0,0,0,.5);max-width:90vw;text-align:center;';
        el.textContent=msg; document.body.appendChild(el);
        let timer=null;
        const api={
          update(m, kind){ el.textContent=m; el.style.borderColor = kind==='err' ? '#e06a4a' : (kind==='ok' ? '#3fae74' : '#2a3a44'); },
          dismiss(){ if(timer) clearTimeout(timer); if(el.parentNode) el.parentNode.removeChild(el); }
        };
        if(opts.autoMs) timer=setTimeout(api.dismiss, opts.autoMs);
        return api;
      }

      // Arm the next tap (in Outline mode) to auto-trace the part it lands on.
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

      _runAutoTrace(seed, simplify, isRetrace){
        if(!this.img){ return; }
        const self=this;
        const natW=this.img.naturalWidth||this.img.width, natH=this.img.naturalHeight||this.img.height;
        const sentScale=Math.min(1, 1600/Math.max(natW, natH));   // cap the POST size; map results back
        const cw=Math.max(1,Math.round(natW*sentScale)), ch=Math.max(1,Math.round(natH*sentScale));
        const cnv=document.createElement('canvas'); cnv.width=cw; cnv.height=ch;
        cnv.getContext('2d').drawImage(this.img, 0, 0, cw, ch);
        let durl; try{ durl=cnv.toDataURL('image/jpeg', 0.85); }catch(e){ return; }

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
        // Carry the Area box (if any) as the ROI, in the SAME sent-image coords as the seed.
        let roiSent=null;
        if(Array.isArray(this._lastAutoRoi) && this._lastAutoRoi.length===4){
          const r=this._lastAutoRoi;
          roiSent=[r[0]*sentScale, r[1]*sentScale, r[2]*sentScale, r[3]*sentScale];
        }

        const toast=this._toast(isRetrace ? 'Re-tracing…' : 'Finding the outline…');
        fetch(this._apiUrl('api/trace'), { method:'POST', headers:{'Content-Type':'application/json'},
            body:JSON.stringify({ image:durl, seed:seedSent, exclude:exclude, simplify:simplify, roi:roiSent }) })
          .then(r=>r.json())
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
              toast.update('Outline'+tail+' traced — drag any dot to fix it, or use the Detail slider.', 'ok');
              setTimeout(toast.dismiss, isRetrace ? 1400 : 2800);
              self._showTraceDetail(nPts, nHoles);
            }else{
              toast.update(isRetrace ? 'Re-trace found nothing — try a different detail level.'
                                     : 'No part found there — tap right on the part, or trace it by hand.', 'err');
              setTimeout(toast.dismiss, 3400);
            }
          })
          .catch(function(){ toast.update('Auto-trace unavailable — trace by hand instead.', 'err'); setTimeout(toast.dismiss, 3000); });
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
      _dismissTraceDetail(){ const el=document.getElementById('cal-trace-detail'); if(el) el.remove(); }

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
          if(mm>0){ this.setManualScaleFromPixels(pixelLen, mm); this.setMode('select'); }
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
        return this.data;
      }
      // Whether the perspective homography should rectify measurements right now. A manual
      // paper rectangle carries its own homography (use it); a manual LINE scale is uniform
      // by nature so it disables the auto homography; otherwise the auto homography applies.
      _allowHomography(){ return this.opts.manualHomography ? true : !this.opts.manualMmPerPx; }

      // Set a perspective-corrected scale from a paper rectangle's 4 corners. H maps image
      // px -> plane mm; mmPerPx is a representative uniform scale kept for circles (which
      // aren't rectified), the KPI readout, and the fallback path. ref describes the sheet.
      setManualRectScale(H, mmPerPx, ref){
        if(!H) return;
        this._markerSizeFromMemory=false;
        this.opts.manualHomography=H;
        if(mmPerPx>0) this.opts.manualMmPerPx=mmPerPx;   // wins over any detected marker
        this._manualRef=ref||null;
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
        this.setManualRectScale(Hm, mmPerPx, { w:long, h:short });
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
        const sub=document.createElement('div'); sub.textContent='Tilt is corrected from the 4 corners, so an angled photo still measures true.'; sub.style.cssText='font-size:12px;color:#9aa;margin-bottom:10px;max-width:36ch;';
        const row=document.createElement('div'); row.style.cssText='display:flex;flex-wrap:wrap;gap:8px;align-items:center;';
        const mkBtn=(label,cls,fn)=>{ const b=document.createElement('button'); b.type='button'; b.textContent=label;
          b.style.cssText='min-height:44px;padding:8px 14px;border-radius:8px;font-size:14px;cursor:pointer;'+(cls==='primary'
            ?'background:#00d4ff;color:#000;border:none;font-weight:600;'
            :'background:#151515;color:#cde;border:1px solid #2a3540;'); b.onclick=fn; return b; };
        row.appendChild(mkBtn('A4 · 210×297', '', ()=>{ if(applyWH(210,297)) box.remove(); }));
        row.appendChild(mkBtn('US Letter · 216×279', '', ()=>{ if(applyWH(215.9,279.4)) box.remove(); }));
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
        row.appendChild(mk('Upload another', true, ()=>{ try{ window.location.reload(); }catch(e){} }));
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
                        ? (this._manualRef ? `paper ${Math.round(this._manualRef.w)}×${Math.round(this._manualRef.h)} mm` : 'paper')
                        : (this.opts.manualMmPerPx ? 'manual line' : `${this.currentMarkerSizeMM()??'—'} mm sq`);
          const unitPerPx=unit.fromMM(s);
          specs.push({text: narrow ? `${unitPerPx.toPrecision(3)} ${unit.label}/px`
                                    : `Scale ${unitPerPx.toFixed(6)} ${unit.label}/px`});
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
      savePNG(){ Xport.exportPNG(this.img, this._dataForMeasure(), this._exportStore(), this.opts.showGrid, this.opts.showMarkers, this.opts.units, this.opts.labelScale, this.opts.linePx, this._allowHomography()); }
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
          manual_reference: this._manualRef ? { width_mm:this._manualRef.w, height_mm:this._manualRef.h } : null,
          markers:this.data?.markers??[]
        }, this._exportStore(), this.opts.units);
        Xport.exportJSON(payload);
      }
      _confirmUncalibrated(){
        return this.isCalibrated() || confirm('Not calibrated — exported values will be in pixels, not millimetres. Set a scale first for real measurements. Export anyway?');
      }
      saveCSV(){ if(!this._confirmUncalibrated()) return; Xport.exportCSV(this._dataForMeasure(), this._exportStore(), this.opts.units, this.getScale(), this._allowHomography()); }
      saveDXF(){ if(!this._confirmUncalibrated()) return; return Xport.exportDXF(this._dataForMeasure(), this._exportStore(), { image_height:this._imgH(), mm_per_px:this.getScale()||this.data?.mm_per_px||0, allowHomography:this._allowHomography() }); }
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
