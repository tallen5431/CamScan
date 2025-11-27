// calibrationOverlay.js — robust boot: waits for deps, then define + auto-init
(function () {
  // --- small CSS (once) for mobile friendliness ---
  (function injectCssOnce() {
    const ID = "calibration-overlay-css";
    if (document.getElementById(ID)) return;
    const st = document.createElement("style");
    st.id = ID;
    st.textContent = `
      .cal-toolbar{display:flex;gap:.5rem;flex-wrap:wrap;justify-content:center;margin:8px 0}
      .cal-toolbar *{font:14px Segoe UI, system-ui, sans-serif}
      .cal-toolbar button, .cal-toolbar select, .cal-toolbar label,
      .cal-toolbar input[type=range], .cal-toolbar input[type=text]{
        padding:.45rem .6rem;border-radius:10px;border:1px solid #444;background:#1e1e1e;color:#eee
      }
      .cal-toolbar button:hover{filter:brightness(1.1)}
      .cal-toolbar input[type=checkbox]{vertical-align:middle;margin-right:.35rem}
      .cal-sep{width:1px;align-self:stretch;background:#444;margin:0 .25rem}
      .cal-mini{opacity:.85;font-size:12px}
      .cal-inline{display:inline-flex;align-items:center;gap:.35rem}
      .cal-range{min-width:140px}
      .cal-kpi{margin:6px 0 0;color:#ddd;font:15px Segoe UI, system-ui, sans-serif}
      .cal-view canvas{border:4px solid #555;border-radius:10px;max-width:98vw;height:auto;touch-action:none;display:block;margin:0 auto}
    `;
    document.head.appendChild(st);
  })();

  // --- wait until the modular libs are present (Dash asset order can vary) ---
  function waitForDeps(maxMs, intervalMs, onReady) {
    const t0 = performance.now();
    const tick = () => {
      const ok = !!(window.CalibUnits && window.CalibGeom && window.CalibDraw && window.CalibAnn && window.CalibExport);
      if (ok) return onReady();
      if (performance.now() - t0 > maxMs) {
        console.error("[CalibrationOverlay] Required modules not found after timeout. Ensure these are in /assets: calib.units.js, calib.geometry.js, calib.draw.js, calib.annotations.js, calib.export.js, then this file.");
        return;
      }
      setTimeout(tick, intervalMs);
    };
    tick();
  }

  waitForDeps(6000, 80, function defineAndBoot() {
    const Units = window.CalibUnits;
    const Geom  = window.CalibGeom;
    const Draw  = window.CalibDraw;
    const Ann   = window.CalibAnn;
    const Xport = window.CalibExport;

    class CalibrationOverlay {
      constructor(canvasEl, imgSrc, jsonSrc) {
        this.canvas = canvasEl;
        this.ctx = this.canvas.getContext('2d', { alpha: false });
        this.imgSrc = imgSrc;
        this.jsonSrc = jsonSrc;
        this.img = null;
        this.data = null;
        this.imgFallback = null; // optional fallback src (file URL)

        // view
        this.scaleX = 1; this.scaleY = 1;
        this.selectedPoints = [];
        this.hover = null;

        // options
        this.opts = {
          snap: true, snapPx: 15, lockMarkerId: null,
          showGrid: false, showMarkers: true,
          units: "mm", labelScale: 1.4, linePx: 3,
          autoAdd: true, mode: "segment" // segment | polyline | rectangle | angle | note
        };

        // annotations
        this.ann = Ann.createStore();
        this.noteText = "";

        this.init();
      }

      // Primary data-URL with file-URL fallback (from data-img-fallback)
      async init() {
        this.img = new Image();
        this.img.decoding = "async";
        this.img.loading = "eager";

        // Read optional fallback from the container (provided by app.py)
        const wrap = this.canvas.closest('.cal-view');
        this.imgFallback = wrap ? wrap.getAttribute('data-img-fallback') : null;

        this.img.onload = async () => {
          await this.loadJSON();
          this.resizeCanvas();
          this.installEvents();
          this.redraw();
          this.buildToolbar();
        };

        this.img.onerror = () => {
          // If primary (e.g., long data URL) fails, try the file URL once
          if (this.imgFallback && this.img.src !== this.imgFallback) {
            console.warn('[CalibrationOverlay] Primary image failed, trying fallback:', this.imgFallback);
            this.img.src = this.imgFallback;
            return;
          }
          // No fallback or fallback also failed
          this.drawPlaceholder('⚠️ Failed to load image');
        };

        // Start with primary (data URL)
        try {
          this.img.src = this.imgSrc;
        } catch (e) {
          console.warn("[CalibrationOverlay] Setting data URL failed, trying fallback…", e);
          if (this.imgFallback) this.img.src = this.imgFallback;
        }
      }

      async loadJSON() {
        try {
          // Force a network read (avoid stale cache)
          const res = await fetch(this.jsonSrc, { cache: 'no-store' });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const raw = await res.json();
          this.data = this.normalize(raw);
        } catch (e) {
          console.warn('Calibration JSON invalid or missing', e);
          this.data = { markers: [], image_size: { width: this.img.width, height: this.img.height }, marker_size_mm: null };
        }
      }

      normalize(raw) {
        if (!raw || !raw.markers) {
          // When img is a data URL, basename is useless; fall back to first key if present
          try {
            const parts = String(this.imgSrc).split('/');
            const basename = parts[parts.length - 1];
            if (raw && raw[basename]) return raw[basename];
            const k = raw && Object.keys(raw)[0];
            if (k) return raw[k];
          } catch (_) {}
          return { markers: [], image_size: { width: this.img.width, height: this.img.height }, marker_size_mm: null };
        }
        return raw;
      }

      // sizing
      resizeCanvas() {
        const r = window.devicePixelRatio || 1;
        const maxW = Math.min(window.innerWidth * 0.98, this.img.width);
        const s = maxW / this.img.width;
        const cssW = Math.round(this.img.width * s);
        const cssH = Math.round(this.img.height * s);
        this.canvas.style.width = `${cssW}px`;
        this.canvas.style.height = `${cssH}px`;
        this.canvas.width  = Math.round(cssW * r);
        this.canvas.height = Math.round(cssH * r);
        this.scaleX = (this.canvas.width / this.img.width);
        this.scaleY = (this.canvas.height / this.img.height);
      }
      _px(v){ return Draw.px(this.canvas, v); }
      _font(px){ return Draw.font(px); }

      // events (pointer for mobile + desktop)
      installEvents() {
        // Ensure touch-action none at runtime as well
        this.canvas.style.touchAction = "none";

        const onPointerDown = (e) => {
          // prevent scroll-jank on touch down inside canvas
          this.canvas.setPointerCapture?.(e.pointerId);
          const ptRaw = this.screenToImage(e);
          const pt = this.opts.snap ? (this.snapToCorner(ptRaw) || ptRaw) : ptRaw;

          // selection first
          const hit = Ann.hitTest(this.ann, pt[0], pt[1]);
          if (hit) { this.ann.selectedId = hit.id; this.redraw(); return; }

          // note: place immediately
          if (this.opts.mode === "note") {
            Ann.addNote(this.ann, pt, this.noteText || 'Note'); this.redraw(); return;
          }

          // collect points
          this.selectedPoints.push(pt);

          // auto-commit
          if (this.opts.mode === "segment" && this.selectedPoints.length === 2) {
            const mm_per_px = this.getScale() || (this.data?.mm_per_px ?? 0);
            Ann.addSegment(this.ann, this.selectedPoints[0], this.selectedPoints[1], mm_per_px, this.opts.units, this.opts.lockMarkerId);
            this.selectedPoints = []; this.redraw();
          } else if (this.opts.mode === "rectangle" && this.selectedPoints.length === 2) {
            const mm_per_px = this.getScale() || (this.data?.mm_per_px ?? 0);
            const a = this.selectedPoints[0], b = this.selectedPoints[1];
            Ann.addRectangle(this.ann, a, b, mm_per_px, this.opts.units, this.opts.lockMarkerId);
            this.selectedPoints = []; this.redraw();
          } else if (this.opts.mode === "angle" && this.selectedPoints.length === 3) {
            const mm_per_px = this.getScale() || (this.data?.mm_per_px ?? 0);
            Ann.addAngle(this.ann, this.selectedPoints[0], this.selectedPoints[1], this.selectedPoints[2], mm_per_px, this.opts.units, this.opts.lockMarkerId);
            this.selectedPoints = []; this.redraw();
          } else {
            // polyline waits for Finish or double-tap
            this.redraw();
          }
        };

        const onPointerMove = (e) => {
          const pt = this.screenToImage(e);
          this.hover = this.opts.snap ? (this.snapToCorner(pt) || pt) : pt;
          if (this.selectedPoints.length >= 1) { this.redraw(); }
        };

        let lastDown = 0;
        const onPointerUp = (e) => {
          const now = performance.now();
          if (this.opts.mode === "polyline" && this.selectedPoints.length >= 2) {
            if (now - lastDown < 300) { this.finishPolyline(); }
          }
          lastDown = now;
          this.canvas.releasePointerCapture?.(e.pointerId);
        };

        const onWheel = (e) => {
          // mobile trackpads often fire wheel; ignore zoom for now to keep UX simple
          e.preventDefault();
        };

        window.addEventListener('resize', () => { this.resizeCanvas(); this.redraw(); });
        window.addEventListener('orientationchange', () => { this.resizeCanvas(); this.redraw(); });
        this.canvas.addEventListener('pointerdown', onPointerDown, { passive: true });
        this.canvas.addEventListener('pointermove', onPointerMove, { passive: true });
        this.canvas.addEventListener('pointerup', onPointerUp, { passive: true });
        this.canvas.addEventListener('wheel', onWheel, { passive: false });
        window.addEventListener('keydown', (e) => this.onKey(e), { passive: true });
      }

      screenToImage(evt) {
        const rect = this.canvas.getBoundingClientRect();
        const px = (evt.clientX - rect.left) * (this.canvas.width / rect.width);
        const py = (evt.clientY - rect.top) * (this.canvas.height / rect.height);
        return [px / this.scaleX, py / this.scaleY];
      }

      onKey(e) {
        if (e.key === 'Backspace' || e.key === 'Delete') {
          if (this.ann.selectedId != null) {
            this.ann.items = this.ann.items.filter(a => a.id !== this.ann.selectedId);
            this.ann.selectedId = null; this.redraw();
          } else if (this.selectedPoints.length) {
            this.selectedPoints.pop(); this.hover = null; this.redraw();
          }
        } else if (e.key === 'Escape') {
          this.selectedPoints = []; this.hover = null; this.ann.selectedId = null; this.redraw();
        } else if (e.key.toLowerCase() === 's') {
          this.opts.snap = !this.opts.snap; this.updateKPI();
        } else if (e.key.toLowerCase() === 'g') {
          this.opts.showGrid = !this.opts.showGrid; this.redraw();
        } else if (e.key === '+' || e.key === '=') {
          this.opts.labelScale = Math.min(3, this.opts.labelScale + 0.1); this.redraw();
        } else if (e.key === '-') {
          this.opts.labelScale = Math.max(0.6, this.opts.labelScale - 0.1); this.redraw();
        }
      }

      // scale + snap
      snapToCorner(pt) {
        if (!this.data || !Array.isArray(this.data.markers)) return null;
        let best = null, bestD = this.opts.snapPx;
        for (const m of this.data.markers) {
          for (const p of (m.corners || [])) {
            const d = Math.hypot(pt[0] - p.x, pt[1] - p.y);
            if (d < bestD) { bestD = d; best = [p.x, p.y]; }
          }
        }
        return best;
      }

      getScale() {
        if (!this.data || !Array.isArray(this.data.markers) || this.data.markers.length === 0) return 0;
        if (this.opts.lockMarkerId) {
          const m = this.data.markers.find(m => String(m.id) === String(this.opts.lockMarkerId));
          return m && m.mm_per_px ? m.mm_per_px : 0;
        }
        const ref = this.selectedPoints.length === 2
          ? [(this.selectedPoints[0][0] + this.selectedPoints[1][0]) / 2, (this.selectedPoints[0][1] + this.selectedPoints[1][1]) / 2]
          : (this.hover || this.selectedPoints[0] || [0, 0]);
        const nearest = this.getScaleAtPoint(ref);
        return nearest || this.data?.markers?.[0]?.mm_per_px || 0;
      }

      getScaleAtPoint(pt) {
        if (!this.data || !Array.isArray(this.data.markers) || !this.data.markers.length) return null;
        let best = null, bestD = 1e18;
        for (const m of this.data.markers) {
          const pts = (m.corners || []).map(p => [p.x, p.y]); if (pts.length < 4 || !m.mm_per_px) continue;
          const c = pts.reduce((a, p) => [a[0] + p[0] / pts.length, a[1] + p[1] / pts.length], [0, 0]);
          const d = Math.hypot(pt[0] - c[0], pt[1] - c[1]); if (d < bestD) { bestD = d; best = m; }
        }
        return best ? best.mm_per_px : null;
      }

      // polyline finish
      finishPolyline() {
        if (this.selectedPoints.length >= 2) {
          const mm_per_px = this.getScale() || (this.data?.mm_per_px ?? 0);
          Ann.addPolyline(this.ann, this.selectedPoints, mm_per_px, this.opts.units, this.opts.lockMarkerId);
        }
        this.selectedPoints = []; this.redraw();
      }

      // render
      drawPlaceholder(msg) {
        const c = this.ctx, w = this.canvas.width, h = this.canvas.height;
        c.fillStyle = "#111"; c.fillRect(0, 0, w, h); c.strokeStyle = "#333";
        for (let x = 0; x < w; x += 50) { c.beginPath(); c.moveTo(x, 0); c.lineTo(x, h); c.stroke(); }
        for (let y = 0; y < h; y += 50) { c.beginPath(); c.moveTo(0, y); c.lineTo(w, y); c.stroke(); }
        c.fillStyle = "#ccc"; c.font = "20px Segoe UI"; c.textAlign = "center"; c.fillText(msg, w / 2, h / 2);
      }

      redraw() {
        const c = this.ctx; c.save(); c.setTransform(1, 0, 0, 1, 0, 0); c.clearRect(0, 0, this.canvas.width, this.canvas.height);
        c.scale(this.scaleX, this.scaleY);
        c.drawImage(this.img, 0, 0);
        if (this.opts.showGrid) Draw.drawGrid(c, this.img.width, this.img.height);
        if (this.opts.showMarkers) Draw.drawMarkers(c, this.canvas, this.data, this.opts.linePx);
        this.drawAnnotations();
        this.drawMeasurementPreview();
        c.restore();
        this.updateKPI();
      }

      drawAnnotations() {
        const c = this.ctx, unit = Units.get(this.opts.units);
        const dotR = this._px(8), linePx = this._px(this.opts.linePx);
        for (const a of this.ann.items) {
          const sel = (a.id === this.ann.selectedId);
          if (a.type === 'segment') {
            const mm_per_px = a.mm_per_px || this.getScale() || 0;
            const dx = a.b[0] - a.a[0], dy = a.b[1] - a.a[1];
            const val = unit.fromMM(Math.hypot(dx, dy) * mm_per_px);
            c.lineWidth = linePx; c.strokeStyle = sel ? "rgba(255,170,0,1)" : "lime";
            c.beginPath(); c.moveTo(a.a[0], a.a[1]); c.lineTo(a.b[0], a.b[1]); c.stroke();
            c.fillStyle = sel ? "rgba(255,170,0,1)" : "lime";
            for (const [x, y] of [a.a, a.b]) { c.beginPath(); c.arc(x, y, dotR, 0, Math.PI * 2); c.fill(); c.strokeStyle = "#000"; c.lineWidth = this._px(2); c.stroke(); }
            const mid = [(a.a[0] + a.b[0]) / 2, (a.a[1] + a.b[1]) / 2];
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
          } else if (a.type === 'note') {
            const tx = a.p[0], ty = a.p[1];
            c.fillStyle = sel ? "rgba(255,170,0,1)" : "deepskyblue";
            c.beginPath(); c.arc(tx, ty, this._px(9), 0, Math.PI * 2); c.fill(); c.strokeStyle = "#000"; c.lineWidth = this._px(2); c.stroke();
            if (a.text) {
              const pad = 8 * this.opts.labelScale, f = Math.round(18 * this.opts.labelScale);
              const boxW = Math.max(140, a.text.length * 10) * (this.opts.labelScale * 0.8), boxH = f + 2 * pad;
              const lx = tx + 14, ly = ty - boxH / 2;
              c.fillStyle = "rgba(0,0,0,.7)"; c.fillRect(lx, ly, boxW, boxH);
              c.strokeStyle = "rgba(255,255,255,.35)"; c.lineWidth = this._px(1.5); c.strokeRect(lx, ly, boxW, boxH);
              c.fillStyle = "#fff"; c.textAlign = "left"; c.textBaseline = "middle"; c.font = this._font(f);
              c.fillText(a.text, lx + pad, ly + boxH / 2);
            }
          } else if (a.type === 'polyline') {
            const pts = a.pts || []; if (pts.length < 2) continue;
            const mm_per_px = a.mm_per_px || this.getScale() || 0;
            let pxSum = 0; for (let i = 1; i < pts.length; i++) pxSum += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
            const val = unit.fromMM(pxSum * mm_per_px);
            c.lineWidth = linePx; c.strokeStyle = sel ? "rgba(255,170,0,1)" : "orange";
            c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); for (let i = 1; i < pts.length; i++) c.lineTo(pts[i][0], pts[i][1]); c.stroke();
            c.fillStyle = sel ? "rgba(255,170,0,1)" : "orange";
            for (const [x, y] of pts) { c.beginPath(); c.arc(x, y, dotR, 0, Math.PI * 2); c.fill(); c.strokeStyle = "#000"; c.lineWidth = this._px(2); c.stroke(); }
            const mid = pts[Math.floor(pts.length / 2)];
            Draw.boxLabel(c, this.canvas, mid[0], mid[1], `${val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
          } else if (a.type === 'rectangle') {
            const [x1, y1, x2, y2] = a.rect;
            const mm_per_px = a.mm_per_px || this.getScale() || 0;
            const wmm = (x2 - x1) * mm_per_px, hmm = (y2 - y1) * mm_per_px, amm = wmm * hmm; // in mm^2
            c.lineWidth = linePx; c.strokeStyle = sel ? "rgba(255,170,0,1)" : "orange"; c.strokeRect(x1, y1, x2 - x1, y2 - y1);
            Draw.boxLabel(c, this.canvas, (x1 + x2) / 2, y1 - 10, `${unit.fromMM(wmm).toFixed(3)}×${unit.fromMM(hmm).toFixed(3)} ${unit.label} • A ${(amm).toFixed(1)} mm²`, this.opts.labelScale);
          } else if (a.type === 'angle') {
            c.lineWidth = linePx; c.strokeStyle = sel ? "rgba(255,170,0,1)" : "orange";
            c.beginPath(); c.moveTo(a.v[0], a.v[1]); c.lineTo(a.a[0], a.a[1]); c.moveTo(a.v[0], a.v[1]); c.lineTo(a.b[0], a.b[1]); c.stroke();
            const ang = Geom.angleABC(a.a, a.v, a.b);
            Draw.boxLabel(c, this.canvas, a.v[0], a.v[1] - 20, `θ ${ang.toFixed(2)}°`, this.opts.labelScale);
          }
        }
      }

      drawMeasurementPreview() {
        if (this.selectedPoints.length === 0 && !this.hover) return;
        const c = this.ctx, unit = Units.get(this.opts.units);
        const linePx = this._px(this.opts.linePx), dotR = this._px(8);
        c.fillStyle = "orange"; c.strokeStyle = "rgba(255,200,0,.9)"; c.lineWidth = linePx;
        for (const [x, y] of this.selectedPoints) { c.beginPath(); c.arc(x, y, dotR, 0, Math.PI * 2); c.fill(); c.strokeStyle = "#000"; c.lineWidth = this._px(2); c.stroke(); }

        const H = this.hover;
        if (this.opts.mode === "segment" && this.selectedPoints.length === 1 && H) {
          this._drawSegmentPreview(this.selectedPoints[0], H, unit);
        } else if (this.opts.mode === "polyline" && this.selectedPoints.length >= 1) {
          const pts = H ? [...this.selectedPoints, H] : [...this.selectedPoints];
          this._drawPolylinePreview(pts, unit);
        } else if (this.opts.mode === "rectangle" && this.selectedPoints.length === 1 && H) {
          this._drawRectPreview(this.selectedPoints[0], H, unit);
        } else if (this.opts.mode === "angle" && this.selectedPoints.length >= 1) {
          const a = (this.selectedPoints.length >= 1) ? this.selectedPoints[0] : null;
          const v = (this.selectedPoints.length >= 2) ? this.selectedPoints[1] : H;
          const b = (this.selectedPoints.length >= 3) ? this.selectedPoints[2] : H;
          if (a && v && b) this._drawAnglePreview(a, v, b);
        }
      }

      _drawSegmentPreview(a, b, unit) {
        const c = this.ctx; c.save(); c.setLineDash([10, 8]);
        c.strokeStyle = "rgba(255,200,0,.9)"; c.beginPath(); c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); c.stroke(); c.restore();
        const sMM = this.getScale() || 0, val = unit.fromMM(Math.hypot(b[0] - a[0], b[1] - a[1]) * sMM);
        const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]; Draw.boxLabel(c, this.canvas, mid[0], mid[1], `~${val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
      }
      _drawPolylinePreview(pts, unit) {
        const c = this.ctx; c.save(); c.setLineDash([10, 8]);
        c.strokeStyle = "rgba(255,200,0,.9)"; c.beginPath(); c.moveTo(pts[0][0], pts[0][1]); for (let i = 1; i < pts.length; i++) c.lineTo(pts[i][0], pts[i][1]); c.stroke(); c.restore();
        const sMM = this.getScale() || 0; let px = 0; for (let i = 1; i < pts.length; i++) px += Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]);
        const val = unit.fromMM(px * sMM); const mid = pts[Math.floor(pts.length / 2)];
        Draw.boxLabel(c, this.canvas, mid[0], mid[1], `~${val.toFixed(3)} ${unit.label}`, this.opts.labelScale);
      }
      _drawRectPreview(a, b, unit) {
        const c = this.ctx; const x1 = Math.min(a[0], b[0]), y1 = Math.min(a[1], b[1]); const x2 = Math.max(a[0], b[0]), y2 = Math.max(a[1], b[1]);
        c.save(); c.setLineDash([10, 8]); c.strokeStyle = "rgba(255,200,0,.9)"; c.strokeRect(x1, y1, x2 - x1, y2 - y1); c.restore();
        const sMM = this.getScale() || 0, wmm = (x2 - x1) * sMM, hmm = (y2 - y1) * sMM, amm = wmm * hmm;
        Draw.boxLabel(c, this.canvas, (x1 + x2) / 2, y1 - 10, `~${Units.get(this.opts.units).fromMM(wmm).toFixed(3)}×${Units.get(this.opts.units).fromMM(hmm).toFixed(3)} ${Units.get(this.opts.units).label} • A ${(amm).toFixed(1)} mm²`, this.opts.labelScale);
      }
      _drawAnglePreview(a, v, b) {
        const c = this.ctx; c.save(); c.setLineDash([10, 8]); c.strokeStyle = "rgba(255,200,0,.9)";
        c.beginPath(); c.moveTo(v[0], v[1]); c.lineTo(a[0], a[1]); c.moveTo(v[0], v[1]); c.lineTo(b[0], b[1]); c.stroke(); c.restore();
        const ang = Geom.angleABC(a, v, b); Draw.boxLabel(c, this.canvas, v[0], v[1] - 20, `~θ ${ang.toFixed(2)}°`, this.opts.labelScale);
      }

      // toolbar + KPI + export
      buildToolbar() {
        const wrap = this.canvas.closest('.cal-view'); if (!wrap) return;
        const bar = wrap.querySelector('.cal-toolbar'); if (!bar) return;
        bar.innerHTML = '';
        const addSep = () => { const d = document.createElement('div'); d.className = 'cal-sep'; bar.appendChild(d); };

        // Mode
        const modeSel = document.createElement('select');
        ["segment","polyline","rectangle","angle","note"].forEach(m=>{
          const o=document.createElement('option'); o.value=m; o.textContent=m[0].toUpperCase()+m.slice(1); modeSel.appendChild(o);
        });
        modeSel.value = this.opts.mode;
        modeSel.onchange = ()=>{ this.opts.mode = modeSel.value; this.selectedPoints=[]; this.hover=null; this.redraw(); };

        // Snap
        const snapLbl = document.createElement('label'); snapLbl.className='cal-inline';
        const snapChk = document.createElement('input'); snapChk.type='checkbox'; snapChk.checked=this.opts.snap;
        snapChk.onchange=()=>{ this.opts.snap=snapChk.checked; this.updateKPI(); };
        const snapWrap = document.createElement('label'); snapWrap.className='cal-inline';
        const snapRange = document.createElement('input'); snapRange.type='range'; snapRange.min='5'; snapRange.max='50'; snapRange.value=String(this.opts.snapPx); snapRange.className='cal-range';
        const snapVal = document.createElement('span'); snapVal.className='cal-mini'; snapVal.textContent=String(this.opts.snapPx);
        snapRange.oninput = ()=>{ this.opts.snapPx=parseInt(snapRange.value,10)||15; snapVal.textContent=String(this.opts.snapPx); };
        snapLbl.appendChild(snapChk); snapLbl.appendChild(document.createTextNode('Snap'));
        snapWrap.appendChild(document.createTextNode('Snap px')); snapWrap.appendChild(snapRange); snapWrap.appendChild(snapVal);

        // Lock to marker
        const lockSel = document.createElement('select');
        const optAuto = document.createElement('option'); optAuto.value=''; optAuto.textContent='Scale: Auto (nearest)'; lockSel.appendChild(optAuto);
        (this.data.markers||[]).forEach(m=>{ const o=document.createElement('option'); o.value=String(m.id); o.textContent=`Scale: Marker #${m.id}`; lockSel.appendChild(o); });
        lockSel.onchange = ()=>{ this.opts.lockMarkerId = lockSel.value || null; this.redraw(); };

        // Units
        const unitSel = document.createElement('select');
        Object.keys(Units.defs).forEach(k=>{ const o=document.createElement('option'); o.value=k; o.textContent=Units.defs[k].label; unitSel.appendChild(o); });
        unitSel.value=this.opts.units; unitSel.onchange=()=>{ this.opts.units=unitSel.value; this.redraw(); };

        // Size + Line
        const sizeWrap = document.createElement('label'); sizeWrap.className='cal-inline';
        const sizeRange = document.createElement('input'); sizeRange.type='range'; sizeRange.min='0.6'; sizeRange.max='3'; sizeRange.step='0.1'; sizeRange.value=String(this.opts.labelScale);
        const sizeVal = document.createElement('span'); sizeVal.className='cal-mini'; sizeVal.textContent=this.opts.labelScale.toFixed(1);
        sizeRange.oninput=()=>{ this.opts.labelScale=Math.max(0.6, Math.min(3, parseFloat(sizeRange.value)||1.4)); sizeVal.textContent=this.opts.labelScale.toFixed(1); this.redraw(); };
        sizeWrap.appendChild(document.createTextNode('Label size')); sizeWrap.appendChild(sizeRange); sizeWrap.appendChild(sizeVal);

        const thickWrap = document.createElement('label'); thickWrap.className='cal-inline';
        const thickRange = document.createElement('input'); thickRange.type='range'; thickRange.min='1'; thickRange.max='10'; thickRange.value=String(this.opts.linePx);
        const thickVal = document.createElement('span'); thickVal.className='cal-mini'; thickVal.textContent=String(this.opts.linePx);
        thickRange.oninput=()=>{ this.opts.linePx=parseInt(thickRange.value,10)||3; thickVal.textContent=String(this.opts.linePx); this.redraw(); };
        thickWrap.appendChild(document.createTextNode('Line')); thickWrap.appendChild(thickRange); thickWrap.appendChild(thickVal);

        // markers + auto-add
        const mkLbl=document.createElement('label'); mkLbl.className='cal-inline';
        const mkChk=document.createElement('input'); mkChk.type='checkbox'; mkChk.checked=this.opts.showMarkers; mkChk.onchange=()=>{ this.opts.showMarkers=mkChk.checked; this.redraw(); };
        mkLbl.appendChild(mkChk); mkLbl.appendChild(document.createTextNode('Markers'));

        const aaLbl=document.createElement('label'); aaLbl.className='cal-inline';
        const aaChk=document.createElement('input'); aaChk.type='checkbox'; aaChk.checked=this.opts.autoAdd; aaChk.onchange=()=>{ this.opts.autoAdd=aaChk.checked; };
        aaLbl.appendChild(aaChk); aaLbl.appendChild(document.createTextNode('Auto-add'));

        // note text + finish polyline
        const noteInput=document.createElement('input'); noteInput.type='text'; noteInput.placeholder='Note text…'; noteInput.value=this.noteText; noteInput.oninput=()=>{ this.noteText=noteInput.value; };
        const btnFinish=document.createElement('button'); btnFinish.textContent='Finish'; btnFinish.onclick=()=>{ if(this.opts.mode==='polyline') this.finishPolyline(); };

        // edit/clear/grid
        const btnUndo=document.createElement('button'); btnUndo.textContent='Undo'; btnUndo.onclick=()=>{ if(this.selectedPoints.length) this.selectedPoints.pop(); else if(this.ann.selectedId!=null){ this.ann.items=this.ann.items.filter(a=>a.id!==this.ann.selectedId); this.ann.selectedId=null;} this.hover=null; this.redraw(); };
        const btnClear=document.createElement('button'); btnClear.textContent='Clear all'; btnClear.onclick=()=>{ this.selectedPoints=[]; this.hover=null; this.ann.selectedId=null; this.ann.items=[]; this.redraw(); };
        const btnGrid=document.createElement('button'); btnGrid.textContent='Grid'; btnGrid.onclick=()=>{ this.opts.showGrid=!this.opts.showGrid; this.redraw(); };

        // export
        const btnPNG=document.createElement('button'); btnPNG.textContent='Save PNG'; btnPNG.onclick=()=> Xport.exportPNG(this.img, this.data, this.ann, this.opts.showGrid, this.opts.showMarkers, this.opts.units);
        const btnJSON=document.createElement('button'); btnJSON.textContent='Save JSON'; btnJSON.onclick=()=>{
          const payload = Ann.toExportJSON(this.imgSrc, {
            marker_size_mm: this.data?.marker_size_mm ?? null,
            mm_per_px: this.data?.mm_per_px ?? null,
            pixels_per_mm: this.data?.pixels_per_mm ?? null,
            markers: this.data?.markers ?? []
          }, this.ann, this.opts.units);
          Xport.exportJSON(payload);
        };

        const kpi = document.createElement('div'); kpi.className='cal-kpi'; kpi.id='cal-kpi';

        // order for mobile
        bar.appendChild(modeSel);
        bar.appendChild(unitSel);
        bar.appendChild(snapLbl);
        bar.appendChild(snapWrap);
        bar.appendChild(lockSel);
        bar.appendChild(sizeWrap);
        bar.appendChild(thickWrap);
        bar.appendChild(mkLbl);
        bar.appendChild(aaLbl);
        bar.appendChild(noteInput);
        bar.appendChild(btnFinish);
        bar.appendChild(btnUndo);
        bar.appendChild(btnClear);
        bar.appendChild(btnGrid);
        bar.appendChild(btnPNG);
        bar.appendChild(btnJSON);
        bar.appendChild(kpi);

        this.updateKPI();
      }

      updateKPI() {
        const el = document.getElementById('cal-kpi'); if (!el) return;
        const s = this.getScale() || 0; const um_per_px = s * 1000;
        const unit = Units.get(this.opts.units); const unitPerPx = unit.fromMM(s);
        el.textContent = `Scale: ${um_per_px.toFixed(2)} µm/px  |  ${unitPerPx.toFixed(6)} ${unit.label}/px  |  Snap: ${this.opts.snap ? 'on' : 'off'}  |  Annotations: ${this.ann.items.length}`;
      }
    }

    // Expose (optional) for debugging / manual boot
    window.CalibrationOverlay = window.CalibrationOverlay || CalibrationOverlay;

    // Auto-init: scan .cal-view once DOM is ready (works with Dash)
    function scanAndInit() {
      document.querySelectorAll('.cal-view:not([data-initialized])').forEach(el => {
        const img = el.getAttribute('data-img');
        const json = el.getAttribute('data-json');
        const canvas = el.querySelector('canvas');
        if (!img || !json || !canvas) return;
        el.setAttribute('data-initialized', '1');
        const id = canvas.id || ('cal-canvas-' + Math.random().toString(36).slice(2));
        canvas.id = id;
        new CalibrationOverlay(canvas, img, json);
      });
    }
    if (document.readyState !== 'loading') scanAndInit(); else document.addEventListener('DOMContentLoaded', scanAndInit);
    const mo = new MutationObserver(() => scanAndInit());
    mo.observe(document.documentElement, { childList: true, subtree: true });
  });
})();
