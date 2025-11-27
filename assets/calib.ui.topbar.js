// Top toolbar (modes, zoom, undo/delete, panel toggle) — mobile friendly
window.CalibUITopbar = (function(){
  function create(rootEl, overlay, panelAPI){
    // Mount inside the placeholder created by app.py
    const host = rootEl.querySelector('#cal-toolbar') || rootEl;

    // Build bar
    const bar = document.createElement('div');
    bar.className = 'cal-topbar';

    // Helpers
    const isTextInput = () => {
      const el = document.activeElement;
      if (!el) return false;
      const tag = (el.tagName||'').toLowerCase();
      return tag === 'input' || tag === 'textarea' || el.isContentEditable;
    };
    const btn = (label, title, on, {toggle=false}={}) => {
      const b = document.createElement('button');
      b.className = 'cal-btn';
      b.type = 'button';
      b.title = title;
      b.setAttribute('aria-label', title);
      b.textContent = label;
      b.onclick = on;
      if (toggle) b.dataset.toggle = '1';
      return b;
    };
    const group = (...els) => { const g = document.createElement('div'); g.className='cal-group'; els.forEach(e=>g.appendChild(e)); return g; };

    // Mode buttons (toggle group)
    const MODES = [
      ['pan',      '🖐', 'Pan / drag (Shift also pans)'],
      ['select',   '▣',  'Select / move measurement'],
      ['segment',  '—',  'Add segment measurement'],
      ['rectangle','▭',  'Add rectangle measurement'],
      ['polyline', '〰', 'Add polyline measurement'],
      ['angle',    '∠',  'Add angle measurement'],
      ['note',     '📝', 'Add note']
    ];
    const modeButtons = new Map();
    const setMode = (m) => { overlay.setMode(m); reflect(); };
    const modeEls = MODES.map(([m, icon, title]) => {
      const el = btn(icon, title, () => setMode(m), {toggle:true});
      modeButtons.set(m, el); return el;
    });

    // Zoom / view
    const zoomOut = btn('−', 'Zoom out', () => overlay.zoomStep(0.9));
    const zoomIn  = btn('+', 'Zoom in',  () => overlay.zoomStep(1.1));
    const fit     = btn('⤢', 'Fit to view', () => overlay.fitToContainer());

    // Undo / Delete
    const undoBtn   = btn('↶', 'Undo last point or last item', () => overlay.undo());
    const delBtn    = btn('🗑', 'Delete selected measurement', () => {
      if (overlay.ann && overlay.ann.selectedId != null){
        overlay.ann.items = overlay.ann.items.filter(a => a.id !== overlay.ann.selectedId);
        overlay.ann.selectedId = null;
        (overlay.requestDraw||overlay.redraw).call(overlay);
      }
    });

    // Bottom sheet toggle
    const moreBtn = btn('⋯', 'More tools', () => panelAPI && panelAPI.toggle());

    // Assemble
    bar.append(
      group(...modeEls),
      group(zoomOut, zoomIn, fit),
      group(undoBtn, delBtn),
      group(moreBtn)
    );
    host.innerHTML = ''; host.appendChild(bar);

    // Keyboard shortcuts
    const onKey = (ev) => {
      if (isTextInput()) return; // don't hijack typing
      const k = ev.key;
      // Undo (Ctrl/Cmd + Z)
      if ((ev.ctrlKey || ev.metaKey) && (k === 'z' || k === 'Z')){ overlay.undo(); ev.preventDefault(); return; }
      // Delete selected
      if (k === 'Delete' || k === 'Backspace'){
        if (overlay.ann && overlay.ann.selectedId != null){
          overlay.ann.items = overlay.ann.items.filter(a => a.id !== overlay.ann.selectedId);
          overlay.ann.selectedId = null;
          (overlay.requestDraw||overlay.redraw).call(overlay);
          ev.preventDefault();
        }
        return;
      }
      // Cancel current point (Esc)
      if (k === 'Escape') { overlay.undo(); ev.preventDefault(); return; }
    };
    window.addEventListener('keydown', onKey, {passive:false});

    // Reflect UI state continuously (lightweight)
    function reflect(){
      // Active mode styling
      for (const [m, el] of modeButtons.entries()){
        if (m === (overlay.opts && overlay.opts.mode)) el.classList.add('active');
        else el.classList.remove('active');
      }
      // Enable/disable Undo / Delete
      const hasPoints = (overlay.selectedPoints && overlay.selectedPoints.length > 0);
      const hasSel = !!(overlay.ann && overlay.ann.selectedId != null);
      undoBtn.disabled = !(hasPoints || hasSel);
      delBtn.disabled  = !hasSel;
    }
    let raf = 0; (function tick(){ reflect(); raf = requestAnimationFrame(tick); })();

    // Minimal CSS (scoped)
    (function cssOnce(){
      const ID = 'cal-topbar-css'; if (document.getElementById(ID)) return;
      const css = `
        .cal-topbar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:6px 8px;background:#111;border-bottom:1px solid #2a2a2a}
        .cal-group{display:flex;gap:6px}
        .cal-topbar .cal-btn{appearance:none;background:#1a1a1a;border:1px solid #2a2a2a;border-radius:10px;padding:8px 10px;font-size:16px;color:#eee;min-width:44px;min-height:40px}
        .cal-topbar .cal-btn.active{background:#2a2a2a}
        .cal-topbar .cal-btn:disabled{opacity:.45}
        @media(hover:hover){.cal-topbar .cal-btn:hover{background:#242424}}
      `;
      const st = document.createElement('style'); st.id = ID; st.textContent = css; document.head.appendChild(st);
    })();

    // Public API
    function reflectMode(){ reflect(); }
    return { root:bar, reflectMode };
  }
  return { create };
})();
