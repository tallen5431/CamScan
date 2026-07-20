// Enhanced CalibUI — task-oriented layout: a labelled tool rail (left on desktop, a
// bottom dock on touch/narrow), a slim top bar for calibration + export, floating zoom,
// contextual Delete/Finish actions, and a first-run coach. The canvas is placed with CSS
// grid so it never has to be reparented, keeping the gesture/sizing engine untouched.
(function ensureCss(){
  const ID = "cal-ui-enhanced-css";
  if (document.getElementById(ID)) return;
  const st = document.createElement('style'); st.id = ID;
  st.textContent = `
    :root{
      --cal-kpi-h: 28px;       /* height reserved at the bottom for the status strip */
      --cal-accent: #00d4ff;
      --cal-accent-hover: #00b8e6;
      /* A 3-step surface scale so the chrome reads as one deliberate elevation system
         instead of several slightly-different near-blacks. bg-0 is the base/letterbox. */
      --cal-bg-0: #0d0d0f;
      --cal-bg-1: #161618;
      --cal-bg-2: #1f1f23;
      --cal-bg-dark: var(--cal-bg-1);    /* back-compat aliases (older rules used these) */
      --cal-bg-medium: var(--cal-bg-2);
      --cal-border: #2c2c31;
      --cal-text: #eee;
    }

    /* ===== Layout ===== CSS grid so the <canvas> stays a direct child of .cal-view and is
       never moved. Mobile-first: top bar / canvas / bottom tool dock. Wide screens: a
       vertical tool rail on the left and the canvas filling the rest (the rail occupies
       space a landscape photo was wasting as horizontal letterbox). */
    .cal-view{
      height: 100dvh; box-sizing: border-box;
      padding-bottom: var(--cal-kpi-h);  /* reserve the fixed status strip's row */
      position: relative; overflow: hidden; background: var(--cal-bg-0);
      display: grid;
      grid-template-columns: 1fr;
      grid-template-rows: auto 1fr auto;
      grid-template-areas: "top" "canvas" "rail";
    }
    @media (min-width: 900px){
      .cal-view{
        grid-template-columns: auto 1fr;
        grid-template-rows: auto 1fr;
        grid-template-areas: "top top" "rail canvas";
      }
    }
    .cal-view[data-tools="collapsed"]{ padding-bottom: 0; }
    .cal-view canvas{
      grid-area: canvas; display: block;
      width: 100%; height: 100%; min-width: 0; min-height: 0; touch-action: none;
    }

    /* Focus mode: hide all chrome so the image fills the screen. Because the rail/top-bar
       grid tracks are auto-sized, hiding their content collapses the tracks to 0 and the
       canvas cell takes the whole grid. (#cal-kpi is a sibling of .cal-view, toggled in JS.) */
    .cal-view[data-tools="collapsed"] .cal-topbar,
    .cal-view[data-tools="collapsed"] .cal-rail,
    .cal-view[data-tools="collapsed"] .cal-zoom,
    .cal-view[data-tools="collapsed"] .cal-context,
    .cal-view[data-tools="collapsed"] .cal-sheet,
    .cal-view[data-tools="collapsed"] .cal-coach{ display: none; }

    /* ===== Top bar ===== calibration + global actions only (the measuring tools live in
       the rail), so it stays one slim row even on a small phone. */
    .cal-topbar{
      grid-area: top; z-index: 6;
      display: flex; align-items: center; justify-content: space-between;
      flex-wrap: wrap; gap: 0.35rem 0.6rem; padding: 0.4rem 0.6rem;
      background: var(--cal-bg-1); border-bottom: 1px solid var(--cal-border);
      box-shadow: 0 1px 0 #000, 0 2px 8px rgba(0,0,0,0.35);
    }
    .cal-tb-left, .cal-tb-right{ display: flex; align-items: center; gap: 0.3rem; }

    .cal-icon{
      background: var(--cal-bg-2); color: var(--cal-text);
      border: 1px solid var(--cal-border); border-radius: 9px;
      padding: 0.4rem 0.55rem; min-width: 44px; min-height: 44px;
      font: 14px/1.2 Segoe UI, system-ui, sans-serif; white-space: nowrap; cursor: pointer;
      transition: background 0.15s ease, border-color 0.15s ease, transform 0.1s ease;
      display: inline-flex; align-items: center; justify-content: center; gap: 0.35rem;
      position: relative; flex-shrink: 0;
    }
    @media (hover: hover) and (pointer: fine){ .cal-icon{ min-width: 38px; min-height: 38px; } }
    .cal-icon:hover{ background: #26262c; border-color: #3a3a42; }
    .cal-icon:active{ transform: translateY(1px); }
    .cal-icon:disabled{ opacity: 0.4; cursor: not-allowed; }
    .cal-icon .cal-icon-emoji{
      display: inline-flex; align-items: center; justify-content: center;
      width: 20px; height: 20px; font-size: 18px; line-height: 1;
    }
    .cal-icon .cal-icon-text{ font-size: 13px; }   /* buttons with empty text render icon-only */
    .cal-icon.cal-btn-primary{ background: var(--cal-accent); color: #000; font-weight: 600; border-color: var(--cal-accent); }
    .cal-icon.cal-btn-primary:hover{ background: var(--cal-accent-hover); }
    /* Calibration chip: prominent, and amber-outlined while nothing is calibrated yet. */
    .cal-icon.cal-chip-uncal{ border-color: rgba(255,180,0,0.55); color: #ffcf6b; }

    /* ===== Tool rail / bottom dock ===== labelled tool buttons (icon + word) so every
       tool is self-describing — no cryptic glyphs, no blind horizontal scroll. */
    .cal-rail{
      grid-area: rail; box-sizing: border-box; background: var(--cal-bg-1);
      display: flex; gap: 4px; padding: 6px; overflow: auto;
      flex-flow: row wrap; justify-content: center; align-content: flex-start;
      border-top: 1px solid var(--cal-border);
    }
    @media (min-width: 900px){
      .cal-rail{
        flex-flow: column nowrap; align-items: stretch; width: 86px;
        border-top: none; border-right: 1px solid var(--cal-border);
        scrollbar-width: thin;
      }
    }
    .cal-tool{
      display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px;
      min-width: 52px; min-height: 50px; padding: 5px 4px;
      background: transparent; border: 1px solid transparent; border-radius: 10px;
      color: var(--cal-text); cursor: pointer;
      font: 600 10.5px/1.05 Segoe UI, system-ui, sans-serif;
      transition: background 0.12s ease, border-color 0.12s ease;
    }
    @media (min-width: 900px){ .cal-tool{ width: 100%; min-height: 56px; } }
    .cal-tool:hover{ background: var(--cal-bg-2); }
    .cal-tool[aria-pressed="true"]{ background: var(--cal-accent); color: #000; border-color: var(--cal-accent); }
    .cal-tool:disabled{ opacity: 0.4; cursor: not-allowed; }
    .cal-tool .cal-tool-emoji{
      display: inline-flex; align-items: center; justify-content: center;
      width: 24px; height: 24px; font-size: 21px; line-height: 1;
    }
    .cal-tool .cal-tool-label{ font-size: 10.5px; white-space: nowrap; }
    /* Group divider inside the rail — a horizontal line on the desktop rail; hidden on the
       bottom dock (where a forced row-break just wasted a whole row). */
    .cal-rail-sep{ display: none; }
    @media (min-width: 900px){
      .cal-rail-sep{ display: block; flex: 0 0 auto; width: 70%; height: 1px; align-self: center; margin: 5px 0; background: var(--cal-border); }
    }

    /* ===== Floating zoom cluster ===== placed in the canvas grid cell (bottom-right) so it
       overlays the image and never collides with the bottom tool dock on mobile. */
    /* min-width/height:0 so this overlay never floors the 1fr canvas grid row on a short
       viewport; pointer-events:none on the container (auto on the buttons) + a circular hit
       shape so taps in the gaps/corners fall through to the canvas underneath. */
    .cal-zoom{
      grid-area: canvas; align-self: end; justify-self: end; min-width: 0; min-height: 0;
      margin: 12px; z-index: 7; pointer-events: none;
      display: flex; flex-direction: column; gap: 6px;
    }
    .cal-zoom button{
      pointer-events: auto; width: 42px; height: 42px; border-radius: 50%; clip-path: circle(50%);
      background: rgba(22,22,24,0.94); color: var(--cal-text);
      border: 1px solid var(--cal-border); font-size: 20px; line-height: 1; cursor: pointer;
      display: flex; align-items: center; justify-content: center;
      box-shadow: 0 2px 8px rgba(0,0,0,0.45);
    }
    .cal-zoom button:hover{ background: var(--cal-bg-2); border-color: var(--cal-accent); }

    /* ===== Contextual actions (bottom-left of the canvas cell): Delete appears with a
       selection, Finish during a path. Only shown when relevant — never a greyed button. */
    .cal-context{
      grid-area: canvas; align-self: end; justify-self: start; min-width: 0; min-height: 0;
      margin: 12px; z-index: 7; display: flex; gap: 8px; pointer-events: none;
    }
    .cal-context button{
      pointer-events: auto; display: none; align-items: center; gap: 6px;
      border: none; border-radius: 999px; padding: 10px 16px; min-height: 44px;
      font: 700 14px/1 Segoe UI, system-ui, sans-serif; cursor: pointer;
      box-shadow: 0 4px 16px rgba(0,0,0,0.45);
    }
    .cal-context button.show{ display: inline-flex; }
    .cal-ctx-delete{ background: #c0392b; color: #fff; }
    .cal-ctx-delete:hover{ background: #d0463a; }
    .cal-ctx-finish{ background: var(--cal-accent); color: #000; }
    .cal-ctx-finish:hover{ background: var(--cal-accent-hover); }

    /* ===== First-run coach ===== a fixed viewport overlay (not confined to the canvas grid
       cell) so it is never clipped by .cal-view's overflow:hidden on a short/landscape phone
       and never leaves the top bar's gear tappable to open a sheet behind it. The card uses
       margin:auto inside an overflow:auto flex box so it centres when there's room and scrolls
       from the top when there isn't (the align-items:center + overflow trap avoided). */
    .cal-coach{
      position: fixed; inset: 0; z-index: 40; padding: 16px; box-sizing: border-box;
      display: flex; overflow: auto;
      background: rgba(7,7,9,0.72); backdrop-filter: blur(2px);
    }
    .cal-coach-card{
      margin: auto; max-width: 360px; width: 100%; box-sizing: border-box;
      background: var(--cal-bg-1); border: 1px solid var(--cal-border); border-radius: 16px;
      padding: 22px; box-shadow: 0 16px 48px rgba(0,0,0,0.6); color: var(--cal-text);
    }
    .cal-coach-card h3{ margin: 0 0 12px; font-size: 18px; }
    .cal-coach-step{ display: flex; gap: 10px; align-items: flex-start; margin: 11px 0; font-size: 14px; line-height: 1.45; color: #d4d4dc; }
    .cal-coach-step b{ color: #fff; }
    .cal-coach-step .n{
      flex: 0 0 24px; height: 24px; border-radius: 50%;
      background: var(--cal-accent); color: #000; font-weight: 700; font-size: 13px;
      display: flex; align-items: center; justify-content: center;
    }
    .cal-coach-actions{ display: flex; gap: 8px; margin-top: 18px; }
    .cal-coach-actions button{ flex: 1; min-height: 44px; border-radius: 10px; font: 600 14px Segoe UI, system-ui, sans-serif; cursor: pointer; }
    .cal-coach-primary{ background: var(--cal-accent); color: #000; border: none; }
    .cal-coach-primary:hover{ background: var(--cal-accent-hover); }
    .cal-coach-secondary{ background: transparent; color: var(--cal-text); border: 1px solid var(--cal-border); }
    .cal-coach-secondary:hover{ border-color: var(--cal-accent); }

    /* Keyboard focus ring. */
    .cal-icon:focus-visible, .cal-tool:focus-visible, .cal-zoom button:focus-visible,
    .cal-context button:focus-visible, .cal-quick-save-menu button:focus-visible,
    .cal-panel input:focus-visible, .cal-panel select:focus-visible,
    .cal-kpi button:focus-visible, .cal-coach-actions button:focus-visible{
      outline: 2px solid var(--cal-accent); outline-offset: 2px;
    }

    /* Tooltip for icon-only top-bar buttons (mouse only). Opens downward. */
    @media (hover: hover) and (pointer: fine) {
      .cal-icon[data-tooltip]::after{
        content: attr(data-tooltip);
        position: absolute; top: calc(100% + 8px); left: 50%; transform: translateX(-50%);
        background: rgba(0,0,0,0.92); color: #fff; padding: 0.4rem 0.6rem; border-radius: 6px;
        font-size: 12px; white-space: nowrap; opacity: 0; pointer-events: none;
        transition: opacity 0.2s ease; z-index: 100;
      }
      .cal-icon[data-tooltip]:hover::after{ opacity: 1; }
    }

    /* ===== Settings bottom sheet ===== */
    .cal-sheet{
      position: fixed; left: 0; right: 0; bottom: var(--cal-kpi-h); z-index: 11;
      touch-action: pan-y pinch-zoom;
    }
    .cal-sheet > details > summary{ display: none; }
    .cal-panel{
      background: #0d0d10; border-top: 2px solid var(--cal-border);
      max-height: 55vh; overflow: auto; padding: 1rem;
      font: 13px/1.4 Segoe UI, system-ui, sans-serif; color: #ddd;
    }
    .cal-panel label{ display: block; margin: 0.75rem 0; cursor: pointer; }
    .cal-panel input[type="text"], .cal-panel input[type="number"]{
      width: 100%; padding: 0.5rem; margin-top: 0.25rem;
      background: var(--cal-bg-medium); border: 1px solid var(--cal-border);
      border-radius: 6px; color: var(--cal-text); font: 16px/1.2 Segoe UI, system-ui, sans-serif;
    }
    .cal-panel input[type="range"]{
      width: 100%; height: 6px; background: var(--cal-border); border-radius: 3px;
      outline: none; -webkit-appearance: none; appearance: none;
    }
    .cal-panel input[type="range"]::-webkit-slider-thumb{
      -webkit-appearance: none; appearance: none; width: 20px; height: 20px;
      background: var(--cal-accent); cursor: pointer; border-radius: 50%; border: 2px solid #000;
    }
    .cal-panel input[type="range"]::-moz-range-thumb{
      width: 20px; height: 20px; background: var(--cal-accent); cursor: pointer;
      border-radius: 50%; border: 2px solid #000;
    }
    .cal-panel input[type="range"]:hover::-webkit-slider-thumb{ background: var(--cal-accent-hover); }
    .cal-panel input[type="range"]:hover::-moz-range-thumb{ background: var(--cal-accent-hover); }
    .cal-panel select{
      padding: 0.5rem; background: var(--cal-bg-medium); border: 1px solid var(--cal-border);
      border-radius: 6px; color: var(--cal-text); font: 14px/1.2 Segoe UI, system-ui, sans-serif; cursor: pointer;
    }
    .cal-panel-head{
      display: flex; align-items: center; justify-content: space-between;
      position: sticky; top: 0; z-index: 1; margin: -1rem -1rem 0.75rem; padding: 0.75rem 1rem;
      background: #0f0f12; border-bottom: 1px solid var(--cal-border);
    }
    .cal-panel-head strong{ font-size: 15px; }
    .cal-panel-head .cal-panel-done{
      background: var(--cal-bg-2); color: var(--cal-text); border: 1px solid var(--cal-border);
      border-radius: 8px; min-height: 40px; padding: 0 16px; font: 600 14px Segoe UI, system-ui, sans-serif; cursor: pointer;
    }
    .cal-panel-head .cal-panel-done:hover{ background: #26262c; border-color: var(--cal-accent); }
    .cal-panel .cal-panel-btn{
      width: 100%; justify-content: center; margin-bottom: 0.5rem; min-height: 44px;
      background: var(--cal-bg-2); color: var(--cal-text); border: 1px solid var(--cal-border);
      border-radius: 8px; font: 14px Segoe UI, system-ui, sans-serif; cursor: pointer;
      display: inline-flex; align-items: center; justify-content: center; gap: 0.4rem;
    }
    .cal-panel .cal-panel-btn:hover{ background: #26262c; border-color: var(--cal-accent); }
    .cal-panel .cal-panel-btn.danger{ border-color: #7a2626; color: #ff9b9b; }
    .cal-panel .cal-panel-btn.danger:hover{ background: #2a1414; border-color: #a33; }

    /* ===== Export menu ===== */
    .cal-export{ position: relative; }
    .cal-quick-save-menu{
      position: fixed; background: var(--cal-bg-dark); border: 2px solid var(--cal-border);
      border-radius: 8px; padding: 0.5rem; min-width: 210px; display: none;
      box-shadow: 0 8px 24px rgba(0,0,0,0.6); z-index: 30;
    }
    .cal-quick-save-menu.active{ display: block; }
    .cal-quick-save-menu button{
      width: 100%; padding: 0.75rem; margin: 0.25rem 0;
      background: var(--cal-bg-medium); border: 1px solid var(--cal-border); border-radius: 6px;
      color: var(--cal-text); font: 14px/1.2 Segoe UI, system-ui, sans-serif; cursor: pointer;
      text-align: left; display: flex; align-items: center; gap: 0.5rem; transition: all 0.15s ease;
    }
    .cal-quick-save-menu button:hover{ background: #222; border-color: var(--cal-accent); }
    .cal-quick-save-menu button .icon{ font-size: 16px; }

    /* ===== Status strip (wrapping chips; measured height feeds back into --cal-kpi-h) ===== */
    .cal-kpi{
      position: fixed; left: 0; right: 0; bottom: 0; z-index: 12; margin: 0;
      padding: 5px 10px; min-height: 24px; box-sizing: border-box; color: #cfcfd6;
      display: flex; flex-wrap: wrap; justify-content: center; align-items: center;
      gap: 5px 7px; white-space: normal;
      background: rgba(13,13,15,0.94); border-top: 1px solid var(--cal-border);
      backdrop-filter: blur(4px);
      font: 12px/1.3 Segoe UI, system-ui, sans-serif;
      font-variant-numeric: tabular-nums;  /* equal-width digits so value changes don't reflow chips */
    }
    .cal-kpi:empty{ display: none; }   /* no stray empty bar on the landing page */
    .cal-chip{
      display: inline-flex; align-items: center; padding: 2px 9px; border-radius: 999px;
      background: var(--cal-bg-2); border: 1px solid var(--cal-border); color: #cfcfd6; font-size: 12px;
    }
    .cal-chip--lead{ background: transparent; border-color: transparent; color: #f2f2f4; font-weight: 600; padding-left: 0; }
    .cal-chip--muted{ background: transparent; border-color: transparent; color: #8a8a92; }
    .cal-chip--ok{ background: rgba(0,212,255,0.12); color: #8fe6ff; border-color: rgba(0,212,255,0.35); }
    .cal-chip--warn{ background: rgba(255,180,0,0.14); color: #ffcf6b; border-color: rgba(255,180,0,0.42); font-weight: 600; }
    .cal-chip.cal-chip--action{
      background: var(--cal-accent); color: #000; border-color: var(--cal-accent);
      font-weight: 700; cursor: pointer; min-height: 30px; padding: 2px 12px;
    }
    .cal-chip.cal-chip--action:hover{ background: var(--cal-accent-hover); }

    /* ===== Focus-mode "Show tools" FAB ===== */
    .cal-fab{
      position: fixed; right: 1rem; bottom: 1rem; z-index: 12;
      background: var(--cal-accent); color: #000; border: none; border-radius: 999px;
      padding: 0.75rem 1.25rem; font: 600 14px/1.2 Segoe UI, system-ui, sans-serif;
      display: none; align-items: center; gap: 0.5rem;
      box-shadow: 0 4px 16px rgba(0,212,255,0.4); cursor: pointer; white-space: nowrap;
    }
    .cal-fab:hover{ background: var(--cal-accent-hover); transform: translateY(-2px); }
    .cal-fab span:first-child{ font-size: 18px; }
    .cal-view[data-tools="collapsed"] .cal-fab{ display: inline-flex; }
  `;
  document.head.appendChild(st);
})();

window.CalibUI = (function(){
  // In-memory "coach already seen" fallback so the first-run coach still shows only once
  // per session when localStorage is unavailable (private mode / storage disabled).
  let coachSeenMem = false;

  function build(rootEl, overlay){
    // Remove any chrome from a previous build on this view (defensive — one build per
    // upload in practice). The <canvas> is a direct grid child and is left in place.
    rootEl.querySelectorAll('.cal-topbar, .cal-rail, .cal-sheet, .cal-zoom, .cal-context, .cal-coach, .cal-fab')
      .forEach(el => el.remove());

    let calInput = null;  // hoisted so the top-bar 📐 chip can focus the panel's size field
    function openCalibration(){
      dismissCoach();
      details.open = true;
      setTimeout(() => { if (calInput) { calInput.focus(); if (calInput.select) calInput.select(); } }, 0);
    }

    // ---- Focus mode ----
    let collapsed = false;
    function setCollapsed(v){
      collapsed = !!v;
      if (collapsed) rootEl.setAttribute('data-tools','collapsed');
      else rootEl.removeAttribute('data-tools');
      // #cal-kpi is a sibling of .cal-view, so CSS under .cal-view can't reach it.
      const kpi = document.getElementById('cal-kpi');
      if (kpi) kpi.style.display = collapsed ? 'none' : '';
      // The canvas box changed (chrome shown/hidden) — re-fit so it isn't left blurry with
      // misaligned taps until an unrelated resize fires.
      if (overlay && overlay._onBoxResize) overlay._onBoxResize();
      try {
        if (collapsed) { if (fab) fab.focus(); }
        else if (focusBtn) focusBtn.focus();
      } catch (e) {}
    }

    // ---- Settings bottom sheet ----
    const sheetWrap = document.createElement('div');
    sheetWrap.className = 'cal-sheet';
    const details = document.createElement('details');
    const sum = document.createElement('summary');
    sum.textContent = 'Settings';
    details.append(sum);

    const body = document.createElement('div');
    body.className = 'cal-panel';

    const panelHead = document.createElement('div');
    panelHead.className = 'cal-panel-head';
    const panelTitle = document.createElement('strong');
    panelTitle.textContent = '⚙️ Settings';
    const panelDone = document.createElement('button');
    panelDone.type = 'button'; panelDone.className = 'cal-panel-done';
    panelDone.textContent = 'Done'; panelDone.setAttribute('aria-label', 'Close settings');
    panelDone.onclick = () => { details.open = false; try { settingsBtn.focus(); } catch (e) {} };
    panelHead.append(panelTitle, panelDone);
    body.appendChild(panelHead);

    if (overlay) {
      const Units = window.CalibUnits;

      const calHead = document.createElement('div');
      calHead.innerHTML = '<strong>📐 Calibration</strong>';
      calHead.style.cssText = 'margin:0.25rem 0 0.5rem;font-size:15px;';
      body.appendChild(calHead);

      const calLabel = document.createElement('label');
      calLabel.innerHTML = 'Calibration square size (mm):';
      calInput = document.createElement('input');
      calInput.type = 'number'; calInput.min = '0.1'; calInput.step = '0.1'; calInput.placeholder = 'e.g. 30';
      const curMM = overlay.currentMarkerSizeMM && overlay.currentMarkerSizeMM();
      calInput.value = curMM ? String(curMM) : '';
      calInput.oninput = () => { const v = parseFloat(calInput.value); if (v > 0 && overlay.setMarkerSizeMM) overlay.setMarkerSizeMM(v); };
      calLabel.appendChild(calInput);
      body.appendChild(calLabel);

      const calHint = document.createElement('div');
      calHint.textContent = 'Enter the real printed edge length of your calibration square — every measurement rescales instantly.';
      calHint.style.cssText = 'font-size:12px;color:#9aa;margin:-0.4rem 0 0.6rem;';
      body.appendChild(calHint);

      const setScaleBtn = document.createElement('button');
      setScaleBtn.type = 'button'; setScaleBtn.className = 'cal-panel-btn';
      setScaleBtn.innerHTML = '<span>📐</span><span>Set scale from a known line</span>';
      setScaleBtn.onclick = () => { if (overlay.setMode) overlay.setMode('setscale'); details.open = false; };
      body.appendChild(setScaleBtn);

      const clearScaleBtn = document.createElement('button');
      clearScaleBtn.type = 'button'; clearScaleBtn.className = 'cal-panel-btn';
      clearScaleBtn.innerHTML = '<span>↺</span><span>Clear manual scale (use square)</span>';
      clearScaleBtn.onclick = () => { if (overlay.clearManualScale) overlay.clearManualScale(); };
      body.appendChild(clearScaleBtn);

      if (Units && Units.defs) {
        const unitLabel = document.createElement('label');
        unitLabel.innerHTML = '<strong>Units:</strong> ';
        const sel = document.createElement('select');
        Object.keys(Units.defs).forEach(k => {
          const opt = document.createElement('option'); opt.value = k; opt.textContent = Units.defs[k].label || k; sel.appendChild(opt);
        });
        sel.value = (overlay.opts && overlay.opts.units) || 'mm';
        sel.onchange = () => { overlay.opts.units = sel.value; if (overlay.redraw) overlay.redraw(); };
        unitLabel.appendChild(sel);
        body.appendChild(unitLabel);
      }

      const snapLabel = document.createElement('label');
      const snapChk = document.createElement('input'); snapChk.type = 'checkbox';
      snapChk.checked = !!(overlay.opts && overlay.opts.snap);
      snapChk.onchange = () => { overlay.opts.snap = snapChk.checked; if (overlay.updateKPI) overlay.updateKPI(); };
      snapLabel.appendChild(snapChk); snapLabel.appendChild(document.createTextNode(' Snap to marker corners'));
      body.appendChild(snapLabel);

      const noteLabel = document.createElement('label');
      noteLabel.innerHTML = '<strong>Default note text:</strong>';
      const noteInput = document.createElement('input'); noteInput.type = 'text';
      noteInput.placeholder = 'Enter default text for notes…'; noteInput.value = overlay.noteText || '';
      noteInput.oninput = () => { overlay.noteText = noteInput.value; };
      noteLabel.appendChild(noteInput);
      body.appendChild(noteLabel);

      const markersLabel = document.createElement('label');
      const markersChk = document.createElement('input'); markersChk.type = 'checkbox';
      markersChk.checked = !!(overlay.opts && overlay.opts.showMarkers);
      markersChk.onchange = () => { overlay.opts.showMarkers = markersChk.checked; if (overlay.redraw) overlay.redraw(); };
      markersLabel.appendChild(markersChk); markersLabel.appendChild(document.createTextNode(' Show calibration markers'));
      body.appendChild(markersLabel);

      const gridLabel = document.createElement('label');
      const gridChk = document.createElement('input'); gridChk.type = 'checkbox';
      gridChk.checked = !!(overlay.opts && overlay.opts.showGrid);
      gridChk.onchange = () => { overlay.opts.showGrid = gridChk.checked; if (overlay.redraw) overlay.redraw(); };
      gridLabel.appendChild(gridChk); gridLabel.appendChild(document.createTextNode(' Show measurement grid'));
      body.appendChild(gridLabel);

      const textSizeLabel = document.createElement('label');
      textSizeLabel.innerHTML = '<strong>Annotation text size:</strong>';
      const textSizeSlider = document.createElement('input');
      textSizeSlider.type = 'range'; textSizeSlider.min = '0.5'; textSizeSlider.max = '3.0'; textSizeSlider.step = '0.1';
      textSizeSlider.value = (overlay.opts && overlay.opts.labelScale) || 1.35;
      textSizeSlider.style.width = '100%'; textSizeSlider.style.marginTop = '0.5rem';
      const textSizeValue = document.createElement('span');
      textSizeValue.textContent = `${textSizeSlider.value}x`;
      textSizeValue.style.cssText = 'margin-left:0.5rem;font-weight:bold;color:var(--cal-accent);';
      textSizeSlider.oninput = () => { overlay.opts.labelScale = parseFloat(textSizeSlider.value); textSizeValue.textContent = `${textSizeSlider.value}x`; if (overlay.redraw) overlay.redraw(); };
      textSizeLabel.appendChild(textSizeSlider); textSizeLabel.appendChild(textSizeValue);
      body.appendChild(textSizeLabel);

      const lineThickLabel = document.createElement('label');
      lineThickLabel.innerHTML = '<strong>Line thickness:</strong>';
      const lineThickSlider = document.createElement('input');
      lineThickSlider.type = 'range'; lineThickSlider.min = '1'; lineThickSlider.max = '8'; lineThickSlider.step = '1';
      lineThickSlider.value = (overlay.opts && overlay.opts.linePx) || 3;
      lineThickSlider.style.width = '100%'; lineThickSlider.style.marginTop = '0.5rem';
      const lineThickValue = document.createElement('span');
      lineThickValue.textContent = `${lineThickSlider.value}px`;
      lineThickValue.style.cssText = 'margin-left:0.5rem;font-weight:bold;color:var(--cal-accent);';
      lineThickSlider.oninput = () => { overlay.opts.linePx = parseInt(lineThickSlider.value); lineThickValue.textContent = `${lineThickSlider.value}px`; if (overlay.redraw) overlay.redraw(); };
      lineThickLabel.appendChild(lineThickSlider); lineThickLabel.appendChild(lineThickValue);
      body.appendChild(lineThickLabel);

      // Clear-all lives here (destructive + rare) rather than cluttering the working chrome.
      const clearAllBtn = document.createElement('button');
      clearAllBtn.type = 'button'; clearAllBtn.className = 'cal-panel-btn danger';
      clearAllBtn.style.marginTop = '1rem';
      clearAllBtn.innerHTML = '<span>🗑</span><span>Clear all annotations</span>';
      clearAllBtn.onclick = () => { if (confirm('Delete all annotations? (You can undo this.)') && overlay.clearAll) { overlay.clearAll(); } };
      body.appendChild(clearAllBtn);
    }

    details.append(body);
    sheetWrap.append(details);
    rootEl.appendChild(sheetWrap);

    // ---- Top bar ----
    const top = document.createElement('div');
    top.className = 'cal-topbar';
    top.setAttribute('role', 'group');
    top.setAttribute('aria-label', 'Calibration and file actions');
    const left = document.createElement('div');  left.className = 'cal-tb-left';
    const right = document.createElement('div'); right.className = 'cal-tb-right';

    const iconBtn = (emoji, text, tooltip, fn) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'cal-icon';
      b.setAttribute('data-tooltip', tooltip); b.setAttribute('aria-label', tooltip);
      b.innerHTML = `<span class="cal-icon-emoji">${emoji}</span>${text ? `<span class="cal-icon-text">${text}</span>` : ''}`;
      b.onclick = () => { fn && fn(); reflect(); };
      return b;
    };

    const newImage = iconBtn('⬅️', 'New', 'Upload a new image', () => {
      if (!overlay.ann || overlay.ann.items.length === 0 ||
          confirm('Start over with a new image? Current annotations will be lost.')) {
        window.location.reload();
      }
    });

    // Calibration chip — the reference size drives every measurement, so surface it up top.
    const calChip = document.createElement('button');
    calChip.type = 'button'; calChip.className = 'cal-icon';
    calChip.innerHTML = '<span class="cal-icon-emoji">📐</span><span class="cal-icon-text"></span>';
    calChip.onclick = () => openCalibration();

    function reflectCalChip(){
      const mm = overlay.currentMarkerSizeMM && overlay.currentMarkerSizeMM();
      const manual = overlay.opts && overlay.opts.manualMmPerPx;
      const uncal = !(overlay.isCalibrated && overlay.isCalibrated());
      let text, label;
      if (manual)      { text = 'manual';   label = 'Scale: manual (from a known line) — tap to change'; }
      else if (mm)     { text = `${mm} mm`;  label = `Calibration square: ${mm} mm — tap to change`; }
      else             { text = 'Set size';  label = 'Not calibrated — tap to set your square size'; }
      const t = calChip.querySelector('.cal-icon-text');
      if (t) t.textContent = text;
      calChip.classList.toggle('cal-chip-uncal', uncal);
      calChip.setAttribute('data-tooltip', label);
      calChip.setAttribute('aria-label', label);
    }
    reflectCalChip();

    left.append(newImage, calChip);

    // Export button + menu (single entry point; PNG is the first, most-common option).
    const exportWrap = document.createElement('div'); exportWrap.className = 'cal-export';
    const exportBtn = document.createElement('button');
    exportBtn.type = 'button'; exportBtn.className = 'cal-icon cal-btn-primary';
    exportBtn.setAttribute('data-tooltip', 'Export / save'); exportBtn.setAttribute('aria-label', 'Export');
    exportBtn.setAttribute('aria-haspopup', 'menu'); exportBtn.setAttribute('aria-expanded', 'false');
    exportBtn.innerHTML = '<span class="cal-icon-emoji">💾</span>';

    const saveMenu = document.createElement('div');
    saveMenu.className = 'cal-quick-save-menu'; saveMenu.setAttribute('role', 'menu');
    const mkSave = (icon, text, fn) => {
      const b = document.createElement('button'); b.type = 'button'; b.setAttribute('role', 'menuitem');
      b.innerHTML = `<span class="icon">${icon}</span><span>${text}</span>`;
      b.onclick = async () => { await fn(); saveMenu.classList.remove('active'); exportBtn.setAttribute('aria-expanded', 'false'); };
      return b;
    };
    saveMenu.append(
      mkSave('🖼️', 'Save PNG image', () => overlay.savePNG && overlay.savePNG()),
      mkSave('📄', 'Save JSON data', () => overlay.saveJSON && overlay.saveJSON()),
      mkSave('📊', 'Save CSV spreadsheet', () => overlay.saveCSV && overlay.saveCSV()),
      mkSave('📐', 'Save DXF (CAD)', () => overlay.saveDXF && overlay.saveDXF()),
      mkSave('🖋️', 'Save SVG (vector, mm)', () => overlay.saveSVG && overlay.saveSVG()),
      mkSave('📦', 'Save both (PNG + JSON)', () => { if (overlay.savePNG) overlay.savePNG(); if (overlay.saveJSON) setTimeout(() => overlay.saveJSON(), 500); }),
    );
    function closeSaveMenu(returnFocus){
      saveMenu.classList.remove('active'); exportBtn.setAttribute('aria-expanded', 'false');
      if (returnFocus) exportBtn.focus();
    }
    function openSaveMenu(){
      const r = exportBtn.getBoundingClientRect();
      saveMenu.style.top = Math.round(r.bottom + 6) + 'px';
      saveMenu.style.left = 'auto';
      saveMenu.style.right = Math.max(6, Math.round(window.innerWidth - r.right)) + 'px';
      saveMenu.classList.add('active'); exportBtn.setAttribute('aria-expanded', 'true');
      const first = saveMenu.querySelector('button'); if (first) setTimeout(() => first.focus(), 0);
    }
    exportBtn.onclick = () => { if (saveMenu.classList.contains('active')) closeSaveMenu(false); else openSaveMenu(); };
    const onDocClickCloseSave = (e) => { if (!exportWrap.contains(e.target)) closeSaveMenu(false); };
    document.addEventListener('click', onDocClickCloseSave);
    saveMenu.addEventListener('keydown', (e) => { if (e.key === 'Escape'){ e.preventDefault(); closeSaveMenu(true); } });
    exportWrap.append(exportBtn, saveMenu);

    const settingsBtn = document.createElement('button');
    settingsBtn.type = 'button'; settingsBtn.className = 'cal-icon';
    settingsBtn.setAttribute('data-tooltip', 'Settings'); settingsBtn.setAttribute('aria-label', 'Settings');
    settingsBtn.setAttribute('aria-haspopup', 'dialog'); settingsBtn.setAttribute('aria-expanded', 'false');
    settingsBtn.innerHTML = '<span class="cal-icon-emoji">⚙️</span>';
    settingsBtn.onclick = () => { details.open = !details.open; if (details.open) dismissCoach(); };
    details.addEventListener('toggle', () => { settingsBtn.setAttribute('aria-expanded', String(details.open)); });

    const focusBtn = iconBtn('⛶', '', 'Focus mode (hide tools)', () => setCollapsed(true));

    right.append(exportWrap, settingsBtn, focusBtn);
    top.append(left, right);
    rootEl.appendChild(top);

    // ---- Tool rail / bottom dock ----
    const modeKeys = { pan:'0', select:'1', segment:'2', polyline:'3', rectangle:'4',
                       angle:'5', circle:'6', circle3pt:'7', note:'8' };
    // Display order (index parity with modeBtns drives reflect(); the keyboard map is
    // name-keyed, so this order is free to be the most usable one).
    const modes = [
      ['select',    '⌖',  'Select',   'Select / move annotations'],
      ['pan',       '🖐',  'Pan',      'Pan the image'],
      ['segment',   '📏',  'Measure',  'Measure a distance'],
      ['polyline',  '〰',  'Path',     'Measure a path / perimeter (double-tap or Enter to finish)'],
      ['rectangle', '▭',  'Area',     'Measure a rectangular area'],
      ['angle',     '∠',  'Angle',    'Measure an angle'],
      ['circle',    '⭕',  'Circle',   'Measure a circle (2 points)'],
      ['circle3pt', '◎',  '3-pt',     'Fit a circle to 3 points'],
      ['note',      '🏷',  'Note',     'Add a note / label'],
      ['setscale',  '📐',  'Set scale','Set the scale from a line of known length'],
    ];

    const rail = document.createElement('div');
    rail.className = 'cal-rail';
    rail.setAttribute('role', 'group'); rail.setAttribute('aria-label', 'Measuring tools');

    const modeBtns = modes.map(([m, icon, label, tooltip]) => {
      const b = document.createElement('button');
      b.type = 'button'; b.className = 'cal-tool';
      const tip = modeKeys[m] ? `${tooltip} (key ${modeKeys[m]})` : tooltip;
      b.setAttribute('aria-label', tip); b.title = tip;
      b.innerHTML = `<span class="cal-tool-emoji">${icon}</span><span class="cal-tool-label">${label}</span>`;
      if (overlay.opts && overlay.opts.mode === m) b.setAttribute('aria-pressed', 'true');
      b.onclick = () => { if (overlay.setMode) overlay.setMode(m); details.open = false; dismissCoach(); reflect(); };
      return b;
    });
    modeBtns.forEach(b => rail.appendChild(b));

    // Undo/redo live at the end of the rail (a labelled edit group) — thumb-reachable in the
    // bottom dock, and it keeps the top bar to one row on a phone.
    const railSep = document.createElement('div'); railSep.className = 'cal-rail-sep';
    const toolAction = (emoji, label, aria, fn) => {
      const b = document.createElement('button'); b.type = 'button'; b.className = 'cal-tool';
      b.setAttribute('aria-label', aria); b.title = aria;
      b.innerHTML = `<span class="cal-tool-emoji">${emoji}</span><span class="cal-tool-label">${label}</span>`;
      b.onclick = () => { fn(); reflect(); };
      return b;
    };
    const undo = toolAction('↶', 'Undo', 'Undo (Ctrl+Z)', () => overlay.undo && overlay.undo());
    const redo = toolAction('↷', 'Redo', 'Redo (Ctrl+Shift+Z)', () => overlay.redo && overlay.redo());
    rail.append(railSep, undo, redo);
    rootEl.appendChild(rail);

    // ---- Floating zoom cluster ----
    const zoom = document.createElement('div');
    zoom.className = 'cal-zoom'; zoom.setAttribute('role', 'group'); zoom.setAttribute('aria-label', 'Zoom');
    const zBtn = (glyph, aria, fn) => { const b = document.createElement('button'); b.type = 'button'; b.setAttribute('aria-label', aria); b.title = aria; b.textContent = glyph; b.onclick = fn; return b; };
    zoom.append(
      zBtn('+', 'Zoom in', () => overlay.zoomStep && overlay.zoomStep(1.2)),
      zBtn('−', 'Zoom out', () => overlay.zoomStep && overlay.zoomStep(1/1.2)),
      zBtn('⤢', 'Fit to screen', () => overlay.fitToContainer && overlay.fitToContainer()),
    );
    rootEl.appendChild(zoom);

    // ---- Contextual actions (Delete when selected, Finish during a path) ----
    const ctx = document.createElement('div'); ctx.className = 'cal-context';
    const ctxDelete = document.createElement('button'); ctxDelete.type = 'button'; ctxDelete.className = 'cal-ctx-delete';
    ctxDelete.innerHTML = '🗑 Delete'; ctxDelete.setAttribute('aria-label', 'Delete selected annotation');
    ctxDelete.onclick = () => { if (overlay.deleteSelected) overlay.deleteSelected(); };
    const ctxFinish = document.createElement('button'); ctxFinish.type = 'button'; ctxFinish.className = 'cal-ctx-finish';
    ctxFinish.innerHTML = '✓ Finish path'; ctxFinish.setAttribute('aria-label', 'Finish the current path');
    ctxFinish.onclick = () => { if (overlay.finishPolyline) overlay.finishPolyline(); };
    ctx.append(ctxDelete, ctxFinish);
    rootEl.appendChild(ctx);

    // ---- Focus-mode "Show tools" FAB ----
    const fab = document.createElement('button');
    fab.type = 'button'; fab.className = 'cal-fab';
    fab.innerHTML = '<span>🧰</span><span>Show tools</span>'; fab.title = 'Show the toolbar';
    fab.onclick = () => setCollapsed(false);
    rootEl.appendChild(fab);

    // ---- First-run coach ----
    let coach = null;
    function dismissCoach(){
      // If focus was inside the coach (e.g. "Got it"), move it somewhere sensible rather than
      // stranding it on <body>. If a tool click triggered the dismissal, focus is already on
      // that tool — leave it alone.
      const hadFocus = coach && coach.contains(document.activeElement);
      if (coach && coach.parentNode) coach.remove();
      coach = null; coachSeenMem = true;
      try { localStorage.setItem('calib.coachSeen', '1'); } catch (e) {}
      if (hadFocus) { try { calChip.focus(); } catch (e) {} }
    }
    function maybeShowCoach(){
      if (coachSeenMem) return;
      let seen = false; try { seen = localStorage.getItem('calib.coachSeen') === '1'; } catch (e) {}
      if (seen) return;
      coach = document.createElement('div'); coach.className = 'cal-coach';
      // Tapping the dimmed backdrop dismisses (so a user who follows "tap the photo" isn't
      // blocked by an inert overlay); taps on the card itself do not.
      coach.addEventListener('click', (e) => { if (e.target === coach) dismissCoach(); });
      const card = document.createElement('div'); card.className = 'cal-coach-card';
      card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true'); card.setAttribute('aria-label', 'Getting started');
      card.innerHTML =
        '<h3>📏 Measure in three steps</h3>' +
        '<div class="cal-coach-step"><span class="n">1</span><span>Set your <b>calibration square size</b> (the 📐 chip up top) — or use the <b>Set&nbsp;scale</b> tool if there is no square.</span></div>' +
        '<div class="cal-coach-step"><span class="n">2</span><span>Pick a <b>measuring tool</b> from the toolbar.</span></div>' +
        '<div class="cal-coach-step"><span class="n">3</span><span><b>Tap points</b> on the photo — real millimetres appear instantly.</span></div>';
      const acts = document.createElement('div'); acts.className = 'cal-coach-actions';
      const setBtn = document.createElement('button'); setBtn.type = 'button'; setBtn.className = 'cal-coach-secondary';
      setBtn.textContent = '📐 Set square size'; setBtn.onclick = () => openCalibration();
      const goBtn = document.createElement('button'); goBtn.type = 'button'; goBtn.className = 'cal-coach-primary';
      goBtn.textContent = 'Got it'; goBtn.onclick = () => dismissCoach();
      acts.append(setBtn, goBtn); card.appendChild(acts); coach.appendChild(card);
      rootEl.appendChild(coach);
      setTimeout(() => { try { goBtn.focus(); } catch (e) {} }, 0);
    }

    // Deterministic teardown of the document listener this build() attached (the overlay's
    // destroy() calls this when the #viewer subtree is swapped on a new upload).
    overlay._uiCleanup = () => { document.removeEventListener('click', onDocClickCloseSave); };

    // ---- Keep the UI in sync with engine state ----
    function reflect(){
      const mode = overlay.opts && overlay.opts.mode;
      modeBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(mode === modes[i][0])));
      ctxDelete.classList.toggle('show', !!(overlay.ann && overlay.ann.selectedId != null));
      ctxFinish.classList.toggle('show', mode === 'polyline' && overlay.selectedPoints && overlay.selectedPoints.length >= 2);
      undo.disabled = !(overlay.canUndo && overlay.canUndo());
      redo.disabled = !(overlay.canRedo && overlay.canRedo());
      reflectCalChip();
    }

    const originalRedraw = typeof overlay.redraw === 'function' ? overlay.redraw.bind(overlay) : function(){};
    overlay.redraw = () => { originalRedraw(); reflect(); };

    reflect();
    maybeShowCoach();
  }

  return { build };
})();
