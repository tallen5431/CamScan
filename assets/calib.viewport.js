// Viewport: image<->canvas mapping, DPR-aware sizing, anchor zoom
window.CalibViewport = (function(){
  function parsePx(v){ const n = parseFloat(v); return Number.isFinite(n) ? n : 0; }

  function create(canvas, imgOrNull){
    const vp = {
      k: 1,           // scale (canvas px per image px)
      panX: 0,        // canvas px
      panY: 0,        // canvas px
      dpr: Math.max(1, window.devicePixelRatio || 1),
      _cw: 0, _ch: 0, // content-box size (CSS px) cached
      _imgW: imgOrNull?.width || 1,
      _imgH: imgOrNull?.height || 1,

      // --- sizing/backing store ------------------------------------------------
      _measureContentBox(){
        const r = canvas.getBoundingClientRect();
        const cs = getComputedStyle(canvas);
        const bl = parsePx(cs.borderLeftWidth),  br = parsePx(cs.borderRightWidth);
        const bt = parsePx(cs.borderTopWidth),   bb = parsePx(cs.borderBottomWidth);
        const pl = parsePx(cs.paddingLeft),      pr = parsePx(cs.paddingRight);
        const pt = parsePx(cs.paddingTop),       pb = parsePx(cs.paddingBottom);
        const cw = Math.max(1, r.width  - bl - br - pl - pr);
        const ch = Math.max(1, r.height - bt - bb - pt - pb);
        this._cw = cw; this._ch = ch;
        return { cw, ch };
      },
      _updateBackingStore(){
        const { cw, ch } = this._measureContentBox();
        const w = Math.round(cw * this.dpr);
        const h = Math.round(ch * this.dpr);
        if (canvas.width !== w || canvas.height !== h){
          canvas.width = w; canvas.height = h;
        }
      },

      // --- transforms ----------------------------------------------------------
      applyToContext(ctx){
        // Map image (px) -> canvas (device px)
        ctx.setTransform(this.k, 0, 0, this.k, this.panX, this.panY);
      },
      imageToCanvas(x, y){
        return [x * this.k + this.panX, y * this.k + this.panY];
      },
      canvasToImage(cx, cy){
        return [(cx - this.panX) / this.k, (cy - this.panY) / this.k];
      },

      // pointer → canvas (device px) using content box (ignores borders/padding)
      eventToCanvasXY(e){
        const r = canvas.getBoundingClientRect();
        const cs = getComputedStyle(canvas);
        const bl = parsePx(cs.borderLeftWidth),  bt = parsePx(cs.borderTopWidth);
        const pl = parsePx(cs.paddingLeft),      pt = parsePx(cs.paddingTop);
        const cw = this._cw || r.width,          ch = this._ch || r.height;
        const cxCSS = e.clientX - r.left - bl - pl;
        const cyCSS = e.clientY - r.top  - bt - pt;
        const cx = cxCSS * (canvas.width  / Math.max(1, cw));
        const cy = cyCSS * (canvas.height / Math.max(1, ch));
        return [cx, cy];
      },
      screenToImage(e){
        const [cx, cy] = this.eventToCanvasXY(e);
        return this.canvasToImage(cx, cy);
      },

      // --- zoom/pan ------------------------------------------------------------
      setZoomAround(newK, anchorImg){
        // Keep anchorImg at same canvas position when zoom changes
        newK = Math.max(0.05, Math.min(40, newK));
        const [ax, ay] = anchorImg;
        const cBefore = this.imageToCanvas(ax, ay);
        this.k = newK;
        const cAfterX = ax * this.k;
        const cAfterY = ay * this.k;
        this.panX = cBefore[0] - cAfterX;
        this.panY = cBefore[1] - cAfterY;
      },
      panByCanvasDelta(dx, dy){
        this.panX += dx; this.panY += dy;
      },
      centerAnchor(){
        // Center of canvas in *image* coords
        const cx = canvas.width  * 0.5;
        const cy = canvas.height * 0.5;
        return this.canvasToImage(cx, cy);
      },
      pxToImg(px){ return px / this.k; },

      // --- fitting/reset -------------------------------------------------------
      fit(canvasEl, img){
        if (img){ this._imgW = img.width; this._imgH = img.height; }
        this._updateBackingStore();
        const kx = canvas.width  / this._imgW;
        const ky = canvas.height / this._imgH;
        this.k = Math.max(0.0001, Math.min(kx, ky));
        // center
        this.panX = (canvas.width  - this._imgW * this.k) * 0.5;
        this.panY = (canvas.height - this._imgH * this.k) * 0.5;
      },
      // Fit so image height matches canvas height (may crop left/right)
      fitHeight(canvasEl, img){
        if (img){ this._imgW = img.width; this._imgH = img.height; }
        this._updateBackingStore();
        const ky = canvas.height / this._imgH;
        this.k = Math.max(0.0001, ky);
        // center horizontally, full height
        this.panX = (canvas.width - this._imgW * this.k) * 0.5;
        this.panY = 0;
      },
      reset(){
        this.k = 1; this.panX = 0; this.panY = 0;
      }
    };

    // Initial backstore sync
    vp._updateBackingStore();
    return vp;
  }

  return { create };
})();
