// Gestures: wheel, pinch, two-finger pan, shift/right pan — now with onDown/onDrag/onUp
window.CalibGestures = (function(){
  function attach(canvas, viewport, {
    onTransform=()=>{}, onHover=()=>{}, onClick=()=>{},
    onDown=()=>{}, onDrag=()=>{}, onUp=()=>{}
  }){
    canvas.style.touchAction = 'none';
    canvas.addEventListener('contextmenu', e=>e.preventDefault());

    const G = {
      ids:new Set(), pts:new Map(), active:false,
      startDist:0, startK:1, anchorImg:[0,0],
      pan:null, clickArmed:false, downPos:null, dragging:false
    };

    const canvasXY = (e) => viewport.eventToCanvasXY(e, canvas);

    function onWheel(e){
      e.preventDefault();
      const anchor = viewport.screenToImage(e, canvas);
      const factor = Math.exp(-e.deltaY*0.001);
      viewport.setZoomAround(viewport.k * factor, anchor);
      onTransform();
    }

    function onPointerDown(e){
      canvas.setPointerCapture?.(e.pointerId);
      G.ids.add(e.pointerId);
      G.pts.set(e.pointerId, canvasXY(e));
      G.downPos = canvasXY(e);
      G.dragging = false;
      G.clickArmed = (e.button===0 && !e.shiftKey);

      // two-finger pinch start
      if(G.ids.size===2){
        const ids=[...G.ids]; const p1=G.pts.get(ids[0]), p2=G.pts.get(ids[1]);
        G.active=true;
        G.startDist = Math.hypot(p2[0]-p1[0], p2[1]-p1[1]);
        G.startK    = viewport.k;
        const mid=[(p1[0]+p2[0])/2,(p1[1]+p2[1])/2];
        G.anchorImg = viewport.canvasToImage(mid[0], mid[1]);
        G.clickArmed=false;
      }

      // pan with Shift / middle / right
      if(e.shiftKey || e.button===1 || e.button===2){
        const [cx,cy]=canvasXY(e);
        G.pan = { cx, cy };
        G.clickArmed=false;
      }

      // notify overlay (for hit-testing / prepare drag)
      const [cx,cy] = canvasXY(e);
      onDown( viewport.canvasToImage(cx,cy), e );
    }

    function onPointerMove(e){
      if(G.ids.has(e.pointerId)) G.pts.set(e.pointerId, canvasXY(e));

      // pinch zoom
      if(G.active && G.ids.size>=2){
        const ids=[...G.ids]; const p1=G.pts.get(ids[0]), p2=G.pts.get(ids[1]);
        if(p1 && p2){
          const dist=Math.hypot(p2[0]-p1[0], p2[1]-p1[1]);
          if(G.startDist>0){
            const factor = dist / G.startDist;
            viewport.setZoomAround(G.startK*factor, G.anchorImg);
            const mid=[(p1[0]+p2[0])/2,(p1[1]+p2[1])/2];
            G.anchorImg = viewport.canvasToImage(mid[0], mid[1]);
            onTransform();
          }
        }
        return;
      }

      // mouse/touch pan
      if(G.pan){
        const [cx,cy] = canvasXY(e);
        viewport.panByCanvasDelta(cx - G.pan.cx, cy - G.pan.cy);
        G.pan = { cx, cy };
        onTransform();
        return;
      }

      // drag gesture (left button moved beyond threshold)
      const [cx,cy] = canvasXY(e);
      if(G.clickArmed && G.downPos){
        const moved = Math.hypot(cx-G.downPos[0], cy-G.downPos[1]);
        if(moved >= 6){
          G.dragging = true;
          onDrag( viewport.canvasToImage(cx,cy), e );
          return;
        }
      }

      onHover( viewport.canvasToImage(cx,cy) );
    }

    function onPointerUp(e){
      canvas.releasePointerCapture?.(e.pointerId);
      G.ids.delete(e.pointerId);
      G.pts.delete(e.pointerId);
      if(G.ids.size<2) G.active=false;

      if(G.pan){ G.pan=null; }

      const [cx,cy] = canvasXY(e);
      if(G.dragging){
        onUp( viewport.canvasToImage(cx,cy), e );
      }else if(G.clickArmed && G.downPos){
        const moved = Math.hypot(cx-G.downPos[0], cy-G.downPos[1]);
        if(moved < 6){ onClick( viewport.canvasToImage(cx,cy) ); }
      }

      G.clickArmed=false;
      G.downPos=null;
      G.dragging=false;
    }

    canvas.addEventListener('wheel', onWheel, { passive:false });
    canvas.addEventListener('pointerdown', onPointerDown, { passive:true });
    canvas.addEventListener('pointermove', onPointerMove, { passive:true });
    canvas.addEventListener('pointerup', onPointerUp, { passive:true });
    canvas.addEventListener('pointercancel', onPointerUp, { passive:true });

    return { detach(){
      canvas.removeEventListener('wheel', onWheel);
      canvas.removeEventListener('pointerdown', onPointerDown);
      canvas.removeEventListener('pointermove', onPointerMove);
      canvas.removeEventListener('pointerup', onPointerUp);
      canvas.removeEventListener('pointercancel', onPointerUp);
    }};
  }

  return { attach };
})();
