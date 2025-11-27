// CalibGestures — pointer + wheel gestures with drag callbacks
window.CalibGestures = (function () {
  function attach(canvas, viewport, opts) {
    opts = opts || {};
    const canPanStart = opts.canPanStart;
    const onTransform = opts.onTransform || function () {};
    const onHover = opts.onHover || function () {};
    const onClick = opts.onClick || function () {};
    const onDown = opts.onDown || function () {};
    const onDrag = opts.onDrag || function () {};
    const onUp = opts.onUp || function () {};

    canvas.style.touchAction = "none";
    canvas.addEventListener("contextmenu", function (e) { e.preventDefault(); });

    // Pointer bookkeeping
    const pointers = new Map(); // id -> {cx, cy}
    let primaryId = null;
    let dragId = null;
    let isPanning = false;
    let pinch = null; // {startDist, startK, anchor}

    const CLICK_EPS = 6;
    let downInfo = null; // {id, cx, cy, imgX, imgY, time}

    function toCanvasXY(e) {
      if (viewport && typeof viewport.eventToCanvasXY === "function") {
        return viewport.eventToCanvasXY(e);
      }
      const r = canvas.getBoundingClientRect();
      const cx = e.clientX - r.left;
      const cy = e.clientY - r.top;
      return [cx, cy];
    }

    function toImageXY(cx, cy) {
      if (viewport && typeof viewport.canvasToImage === "function") {
        return viewport.canvasToImage(cx, cy);
      }
      return [cx, cy];
    }

    function handleWheel(e) {
      e.preventDefault();
      const [cx, cy] = toCanvasXY(e);
      const anchor = toImageXY(cx, cy);
      // dy sign: wheel down -> zoom out
      const dy = e.deltaY || 0;
      const factor = dy < 0 ? 1.1 : 1 / 1.1;
      const k = viewport.k != null ? viewport.k : 1;
      if (typeof viewport.setZoomAround === "function") {
        viewport.setZoomAround(k * factor, anchor);
        onTransform();
      }
    }

    function handlePointerDown(e) {
      canvas.setPointerCapture && canvas.setPointerCapture(e.pointerId);
      const [cx, cy] = toCanvasXY(e);
      pointers.set(e.pointerId, { cx: cx, cy: cy });

      const img = toImageXY(cx, cy);
      onDown(img, e);

      // Track primary pointer for click / drag
      if (primaryId === null) {
        primaryId = e.pointerId;
        dragId = e.pointerId;
        downInfo = { id: e.pointerId, cx: cx, cy: cy, imgX: img[0], imgY: img[1], time: Date.now() };
      }

      // Check for pinch start
      if (pointers.size === 2) {
        const ids = Array.from(pointers.keys());
        const p1 = pointers.get(ids[0]);
        const p2 = pointers.get(ids[1]);
        const midCx = (p1.cx + p2.cx) / 2;
        const midCy = (p1.cy + p2.cy) / 2;
        const dx = p1.cx - p2.cx;
        const dy = p1.cy - p2.cy;
        const dist = Math.hypot(dx, dy);
        const anchor = toImageXY(midCx, midCy);
        pinch = {
          startDist: dist || 1,
          startK: viewport.k != null ? viewport.k : 1,
          anchor: anchor
        };
        isPanning = false;
        dragId = null;
        downInfo = null;
        return;
      }

      // Single-pointer pan?
      if (pointers.size === 1) {
        if (!canPanStart || canPanStart(e)) {
          isPanning = true;
        } else {
          isPanning = false;
        }
      }
    }

    function handlePointerMove(e) {
      if (!pointers.has(e.pointerId)) {
        // hover without capture
        const [cx, cy] = toCanvasXY(e);
        const img = toImageXY(cx, cy);
        onHover(img, e);
        return;
      }

      const [cx, cy] = toCanvasXY(e);
      const prev = pointers.get(e.pointerId) || { cx: cx, cy: cy };
      pointers.set(e.pointerId, { cx: cx, cy: cy });

      // Pinch zoom
      if (pinch && pointers.size >= 2) {
        const ids = Array.from(pointers.keys());
        const p1 = pointers.get(ids[0]);
        const p2 = pointers.get(ids[1]);
        const dx = p1.cx - p2.cx;
        const dy = p1.cy - p2.cy;
        const dist = Math.hypot(dx, dy) || 1;
        const f = dist / pinch.startDist;
        const newK = pinch.startK * f;
        if (typeof viewport.setZoomAround === "function") {
          viewport.setZoomAround(newK, pinch.anchor);
          onTransform();
        }
        return;
      }

      // Pan
      if (isPanning && primaryId === e.pointerId && typeof viewport.panByCanvasDelta === "function") {
        const dx = cx - prev.cx;
        const dy = cy - prev.cy;
        viewport.panByCanvasDelta(dx, dy);
        onTransform();
        return;
      }

      // Drag / hover
      const img = toImageXY(cx, cy);
      if (dragId === e.pointerId) {
        onDrag(img, e);
      } else {
        onHover(img, e);
      }
    }

    function handlePointerUp(e) {
      const hadPointer = pointers.has(e.pointerId);
      let cx, cy;
      if (hadPointer) {
        const info = pointers.get(e.pointerId);
        cx = info.cx;
        cy = info.cy;
        pointers.delete(e.pointerId);
      } else {
        const pt = toCanvasXY(e);
        cx = pt[0];
        cy = pt[1];
      }
      canvas.releasePointerCapture && canvas.releasePointerCapture(e.pointerId);

      const img = toImageXY(cx, cy);
      onUp(img, e);

      // Click detection (before we clear primary/drag)
      if (downInfo && e.pointerId === downInfo.id) {
        const moved = Math.hypot(cx - downInfo.cx, cy - downInfo.cy);
        if (moved < CLICK_EPS) {
          onClick([downInfo.imgX, downInfo.imgY], e);
        }
        downInfo = null;
      }

      // End pinch if we lost a finger
      if (pinch && pointers.size < 2) {
        pinch = null;
      }

      // End pan if primary lifted
      if (primaryId === e.pointerId) {
        primaryId = null;
        isPanning = false;
      }
      if (dragId === e.pointerId) {
        dragId = null;
      }
    }

    canvas.addEventListener("wheel", handleWheel, { passive: false });
    canvas.addEventListener("pointerdown", handlePointerDown, { passive: true });
    canvas.addEventListener("pointermove", handlePointerMove, { passive: true });
    canvas.addEventListener("pointerup", handlePointerUp, { passive: true });
    canvas.addEventListener("pointercancel", handlePointerUp, { passive: true });

    return {
      detach: function () {
        canvas.removeEventListener("wheel", handleWheel);
        canvas.removeEventListener("pointerdown", handlePointerDown);
        canvas.removeEventListener("pointermove", handlePointerMove);
        canvas.removeEventListener("pointerup", handlePointerUp);
        canvas.removeEventListener("pointercancel", handlePointerUp);
      }
    };
  }

  return { attach: attach };
})();
