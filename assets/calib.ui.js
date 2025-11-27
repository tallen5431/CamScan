// CalibUI — improved mobile layout, zoom controls & collapsible toolbar
(function ensureCss(){
  const ID = "cal-ui-compact-css-v3";
  if (document.getElementById(ID)) return;
  const st = document.createElement('style'); st.id = ID;
  st.textContent = `
    :root{ --cal-topbar-h:44px; }
    @media (min-width:900px){ :root{ --cal-topbar-h:48px; } }

    .cal-topbar{
      position:sticky; top:0; z-index:10; height:var(--cal-topbar-h);
      background:#0e0e0e; border-bottom:1px solid #222;
      display:flex; align-items:center; gap:.4rem;
      padding:0 .5rem;
      overflow-x:auto; -webkit-overflow-scrolling:touch;
      scrollbar-width:none;
    }
    .cal-topbar::-webkit-scrollbar{ display:none; }

    .cal-icon{
      background:#181818; color:#eee;
      border:1px solid #2a2a2a;
      border-radius:10px;
      padding:.4rem .7rem;
      min-width:44px;
      font:14px/1 Segoe UI, system-ui, sans-serif;
      white-space:nowrap;
      cursor:pointer;
    }
    .cal-icon[aria-pressed="true"]{
      background:#2b2b2b; color:#fff;
    }

    .cal-sheet{
      position:fixed;
      left:0; right:0; bottom:0;
      z-index:11;
      touch-action:pan-y pinch-zoom;
    }
    .cal-sheet>details>summary{
      background:#111; color:#ddd;
      padding:.6rem;
      border-top:1px solid #222;
      text-align:center;
      cursor:pointer;
      font:14px/1.1 Segoe UI, system-ui, sans-serif;
    }

    .cal-panel{
      background:#090909;
      border-top:1px solid #222;
      max-height:50vh;
      overflow:auto;
      padding:.5rem .75rem .75rem;
      font:13px/1.3 Segoe UI, system-ui, sans-serif;
      color:#ddd;
    }

    /* Make image get the space */
    .cal-view{
      height:100dvh;
      position:relative;
    }
    .cal-view canvas{
      display:block;
      width:100%;
      height:100%;
    }

    /* Focus mode: hide chrome, show floating Tools button */
    .cal-view[data-tools="collapsed"] .cal-topbar{ display:none; }
    .cal-view[data-tools="collapsed"] .cal-sheet{ display:none; }

    .cal-fab{
      position:fixed;
      right:.75rem;
      bottom:.9rem;
      z-index:12;
      background:#181818;
      color:#eee;
      border:1px solid #2a2a2a;
      border-radius:999px;
      padding:.55rem .8rem;
      font:14px/1.1 Segoe UI, system-ui, sans-serif;
      display:none;
      align-items:center;
      gap:.35rem;
      box-shadow:0 2px 10px rgba(0,0,0,.45);
      cursor:pointer;
      white-space:nowrap;
    }
    .cal-fab span:first-child{ font-size:16px; }
    .cal-view[data-tools="collapsed"] .cal-fab{ display:inline-flex; }
  `;
  document.head.appendChild(st);
})();

window.CalibUI = (function(){
  function build(rootEl, overlay){
    // Remove old UI, if any
    const old = rootEl.querySelector('.cal-tools');
    if (old) old.remove();

    const wrap = document.createElement('div');
    wrap.className = 'cal-tools';
    rootEl.prepend(wrap);

    // Focus mode state
    let collapsed = false;
    function setCollapsed(v){
      collapsed = !!v;
      if (collapsed) {
        rootEl.setAttribute('data-tools','collapsed');
      } else {
        rootEl.removeAttribute('data-tools');
      }
    }

    // --------- Bottom sheet (can be filled elsewhere) ----------
    const sheetWrap = document.createElement('div');
    sheetWrap.className = 'cal-sheet';

    const details = document.createElement('details');
    const sum = document.createElement('summary');
    sum.textContent = 'More ▾';
    details.append(sum);

    const body = document.createElement('div');
    body.className = 'cal-panel';

    // Fill the bottom sheet with a few advanced controls so "More" does something useful
    if (overlay) {
      const Units = window.CalibUnits;
      body.style.display = 'flex';
      body.style.flexDirection = 'column';
      body.style.gap = '.4rem';

      // Units selector
      if (Units && Units.defs) {
        const row = document.createElement('div');
        const lbl = document.createElement('label');
        lbl.textContent = 'Units: ';
        const sel = document.createElement('select');
        Object.keys(Units.defs).forEach(k => {
          const opt = document.createElement('option');
          opt.value = k;
          opt.textContent = Units.defs[k].label || k;
          sel.appendChild(opt);
        });
        sel.value = (overlay.opts && overlay.opts.units) || 'mm';
        sel.onchange = () => {
          overlay.opts.units = sel.value;
          if (overlay.redraw) overlay.redraw();
        };
        lbl.appendChild(sel);
        row.appendChild(lbl);
        body.appendChild(row);
      }

      // Snap behaviour
      const snapRow = document.createElement('div');
      const snapLabel = document.createElement('label');
      const snapChk = document.createElement('input');
      snapChk.type = 'checkbox';
      snapChk.checked = !!(overlay.opts && overlay.opts.snap);
      snapChk.onchange = () => {
        overlay.opts.snap = snapChk.checked;
        if (overlay.updateKPI) overlay.updateKPI();
      };
      snapLabel.appendChild(snapChk);
      snapLabel.appendChild(document.createTextNode(' Snap to marker corners'));
      snapRow.appendChild(snapLabel);
      body.appendChild(snapRow);

      // Note text
      const noteRow = document.createElement('div');
      const noteInput = document.createElement('input');
      noteInput.type = 'text';
      noteInput.placeholder = 'Default note text…';
      noteInput.value = overlay.noteText || '';
      noteInput.oninput = () => {
        overlay.noteText = noteInput.value;
      };
      noteRow.appendChild(noteInput);
      body.appendChild(noteRow);

      // Export options
      const expRow = document.createElement('div');
      expRow.style.display = 'flex';
      expRow.style.flexWrap = 'wrap';
      expRow.style.gap = '.35rem';

      const expOnlyLabel = document.createElement('label');
      const expOnlyChk = document.createElement('input');
      expOnlyChk.type = 'checkbox';
      expOnlyChk.checked = !!(overlay.opts && overlay.opts.exportVisibleOnly);
      expOnlyChk.onchange = () => {
        overlay.opts.exportVisibleOnly = expOnlyChk.checked;
      };
      expOnlyLabel.appendChild(expOnlyChk);
      expOnlyLabel.appendChild(
        document.createTextNode(' Export visible only (use toolbar save for PNG/JSON)')
      );
      expRow.appendChild(expOnlyLabel);

      body.appendChild(expRow);
    }

    details.append(body);

    sheetWrap.append(details);
    wrap.append(sheetWrap);

    // Toggle sheet on tap (but not when interacting with inputs)
    // --------- Top bar ----------
    const top = document.createElement('div');
    top.className = 'cal-topbar';

    const modes = [
      ['pan',      '🖐'],
      ['select',   '⌖'],
      ['segment',  '📏'],
      ['rectangle','▭'],
      ['angle',    '∠'],
      ['note',     '🏷']
    ];

    const btn = (label, fn, pressed) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'cal-icon';
      b.textContent = label;
      if (pressed) b.setAttribute('aria-pressed','true');
      b.onclick = () => {
        fn && fn();
        // Close sheet when a toolbar button is used (better on mobile)
        details.open = false;
        reflect();
      };
      return b;
    };

    const modeBtns = modes.map(([m, icon]) =>
      btn(icon, () => overlay.setMode && overlay.setMode(m), overlay.opts && overlay.opts.mode === m)
    );

    const undo = btn('↶', () => {
      if (overlay.undo) overlay.undo();
    });

    const del = (function(){
      const b = btn('🗑', () => {
        if (overlay.deleteSelected) overlay.deleteSelected();
      });
      b.disabled = !(overlay.ann && overlay.ann.selectedId != null);
      return b;
    })();

    const zoomOut = btn('➖', () => {
      if (overlay.zoomStep) overlay.zoomStep(1/1.2);
    });
    const zoomIn = btn('➕', () => {
      if (overlay.zoomStep) overlay.zoomStep(1.2);
    });
    const fit = btn('⤢', () => {
      if (overlay.fitToContainer) overlay.fitToContainer();
    });
    const fitH = btn('⇕', () => {
      if (overlay.fitToHeight) overlay.fitToHeight();
    });

    const toggleToolsBtn = btn('🗕', () => setCollapsed(!collapsed));

    const more = btn('⋮', () => {
      details.open = !details.open;
    });

    // Reflect pressed tool + selection state
    function reflect(){
      const mode = overlay.opts && overlay.opts.mode;
      modeBtns.forEach((b, i) => {
        const active = mode === modes[i][0];
        b.setAttribute('aria-pressed', String(active));
      });
      del.disabled = !(overlay.ann && overlay.ann.selectedId != null);
    }

    top.append(
      ...modeBtns,
      undo,
      del,
      zoomOut,
      zoomIn,
      fit,
      fitH,
      toggleToolsBtn,
      more
    );
    wrap.prepend(top);

    // --------- Floating "Tools" button for focus mode ----------
    const fab = document.createElement('button');
    fab.type = 'button';
    fab.className = 'cal-fab';
    fab.innerHTML = '<span>🧰</span><span>Tools</span>';
    fab.title = 'Show toolbar and panels';
    fab.onclick = () => setCollapsed(false);
    wrap.appendChild(fab);

    // Keep UI in sync with overlay redraws
    const originalRedraw = typeof overlay.redraw === 'function'
      ? overlay.redraw.bind(overlay)
      : function(){};

    overlay.redraw = () => {
      originalRedraw();
      reflect();
    };

    reflect();
  }

  return { build };
})();
