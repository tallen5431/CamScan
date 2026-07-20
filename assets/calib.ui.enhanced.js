// Enhanced CalibUI — improved annotation tools & easy downloads
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
         instead of five slightly-different near-blacks. bg-0 is the base/letterbox. */
      --cal-bg-0: #0d0d0f;
      --cal-bg-1: #161618;
      --cal-bg-2: #1f1f23;
      /* Back-compat aliases (older rules referenced these names). */
      --cal-bg-dark: var(--cal-bg-1);
      --cal-bg-medium: var(--cal-bg-2);
      --cal-border: #2c2c31;
      --cal-text: #eee;
    }

    /* ---- Toolbar: two clusters (tools left, actions right). On a wide viewport it is
       one tidy row; when it can't fit, the right cluster drops cleanly to a second row
       (tools / actions) instead of the old 3-4 ragged rows with orphaned dividers. On a
       touch device it becomes a single horizontally-scrollable row (kept short) with a
       position-aware fade cue. ---- */
    .cal-topbar-scroll{ position: relative; }

    .cal-topbar{
      position: sticky;
      top: 0;
      z-index: 10;
      background: var(--cal-bg-1);
      border-bottom: 1px solid var(--cal-border);
      box-shadow: 0 1px 0 #000, 0 2px 8px rgba(0,0,0,0.35);
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 0.35rem 0.6rem;
      padding: 0.4rem 0.6rem;
    }

    .cal-tb-left, .cal-tb-right{
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.25rem 0.4rem;
    }
    .cal-tb-right{ justify-content: flex-end; }

    .cal-toolbar-section{
      display: flex;
      align-items: center;
      gap: 0.25rem;
      flex-shrink: 0;
    }
    /* Group separator that travels WITH the group when it wraps (a standalone 1px
       divider element used to orphan onto its own row — the ragged-look culprit). */
    .cal-tb-left .cal-toolbar-section + .cal-toolbar-section,
    .cal-tb-right .cal-toolbar-section + .cal-toolbar-section{
      border-left: 1px solid var(--cal-border);
      padding-left: 0.45rem;
      margin-left: 0.1rem;
    }

    .cal-icon{
      background: var(--cal-bg-2);
      color: var(--cal-text);
      border: 1px solid var(--cal-border);
      border-radius: 9px;
      padding: 0.4rem 0.55rem;
      min-width: 44px;
      min-height: 44px;
      font: 14px/1.2 Segoe UI, system-ui, sans-serif;
      white-space: nowrap;
      cursor: pointer;
      transition: background 0.15s ease, border-color 0.15s ease, transform 0.1s ease;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.35rem;
      position: relative;
      flex-shrink: 0;
    }

    .cal-icon:hover{ background: #26262c; border-color: #3a3a42; }
    .cal-icon:active{ transform: translateY(1px); }

    .cal-icon[aria-pressed="true"]{
      background: var(--cal-accent);
      color: #000;
      border-color: var(--cal-accent);
      font-weight: 600;
      box-shadow: 0 0 0 1px var(--cal-accent);
    }
    .cal-icon[aria-pressed="true"]:hover{ background: var(--cal-accent-hover); }
    .cal-icon:disabled{ opacity: 0.4; cursor: not-allowed; }

    /* Primary export button: a flat accent fill (not the lone gradient it used to be),
       clearly primary but distinct from the solid-accent aria-pressed active tool. */
    .cal-icon.cal-btn-download{
      background: var(--cal-accent);
      color: #000;
      font-weight: 600;
      border-color: var(--cal-accent);
    }
    .cal-icon.cal-btn-download:hover{ background: var(--cal-accent-hover); }

    /* Clear-All is destructive and was visually near-identical to Delete. Danger tint. */
    .cal-icon.cal-btn-danger{ border-color: #7a2626; color: #ff9b9b; }
    .cal-icon.cal-btn-danger:hover{ background: #2a1414; border-color: #a33; }

    /* A fixed square metric box for the glyph so full-color emoji (🖐📐🗑💾) and thin
       Unicode symbols (⌖ ∠ ◎ 〰 ➖ ⋮ …) share one footprint and stop rendering ragged. */
    .cal-icon .cal-icon-emoji{
      display: inline-flex; align-items: center; justify-content: center;
      width: 20px; height: 20px; font-size: 18px; line-height: 1;
    }
    .cal-icon .cal-icon-text{ font-size: 13px; display: inline; }

    /* Desktop mouse: icon-only glyph groups (labels only where flagged), smaller targets
       — this is what actually lets the whole bar collapse onto one row. Discoverability is
       preserved via the hover tooltip + aria-label. */
    @media (hover: hover) and (pointer: fine){
      .cal-icon{ min-width: 38px; min-height: 38px; padding: 0.35rem 0.45rem; }
      .cal-icon .cal-icon-text{ display: none; }
      .cal-icon--label .cal-icon-text{ display: inline; }
    }

    /* Touch: keep 44px targets; icon-only for the glyph tools (labels only on flagged
       primary controls) so the single scroll row stays short; the active tool is named in
       the status strip and every button keeps its aria-label. */
    @media (pointer: coarse){
      .cal-topbar{ flex-wrap: nowrap; overflow-x: auto; justify-content: flex-start;
                   -webkit-overflow-scrolling: touch; scrollbar-width: none; }
      .cal-topbar::-webkit-scrollbar{ display: none; }
      .cal-tb-left, .cal-tb-right{ flex-wrap: nowrap; }
      .cal-icon .cal-icon-text{ display: none; }
      .cal-icon--label .cal-icon-text{ display: inline; }
    }

    /* Position-aware scroll cue (touch only): fades appear on the side(s) that have more
       toolbar off-screen and vanish at each end — unlike the old sticky gradient that was
       pinned permanently to the right edge and never told you where you were. */
    .cal-scroll-cue{
      position: absolute; top: 0; width: 30px;
      pointer-events: none; z-index: 11; opacity: 0; transition: opacity 0.15s ease;
    }
    .cal-scroll-cue--l{ left: 0; background: linear-gradient(to right, var(--cal-bg-1), transparent); }
    .cal-scroll-cue--r{ right: 0; background: linear-gradient(to left, var(--cal-bg-1), transparent); }
    .cal-topbar-scroll[data-ov-left]  .cal-scroll-cue--l{ opacity: 1; }
    .cal-topbar-scroll[data-ov-right] .cal-scroll-cue--r{ opacity: 1; }
    @media (hover: hover) and (pointer: fine){ .cal-scroll-cue{ display: none; } }

    /* Keyboard focus ring. */
    .cal-icon:focus-visible,
    .cal-quick-save-menu button:focus-visible,
    .cal-panel input:focus-visible,
    .cal-panel select:focus-visible,
    .cal-kpi button:focus-visible{
      outline: 2px solid var(--cal-accent);
      outline-offset: 2px;
    }

    /* Tooltip - mouse only. Opens DOWNWARD so a top-row button's tip isn't clipped
       off the top of the viewport (the bar is now a single top row on desktop). */
    @media (hover: hover) and (pointer: fine) {
      .cal-icon::after{
        content: attr(data-tooltip);
        position: absolute;
        top: calc(100% + 8px);
        left: 50%;
        transform: translateX(-50%);
        background: rgba(0, 0, 0, 0.92);
        color: #fff;
        padding: 0.4rem 0.6rem;
        border-radius: 6px;
        font-size: 12px;
        white-space: nowrap;
        opacity: 0;
        pointer-events: none;
        transition: opacity 0.2s ease;
        z-index: 100;
      }
      .cal-icon:hover::after{ opacity: 1; }
    }

    .cal-sheet{
      position: fixed;
      left: 0;
      right: 0;
      bottom: var(--cal-kpi-h); /* sit directly above the fixed KPI status strip */
      z-index: 11;
      touch-action: pan-y pinch-zoom;
    }

    /* The "More Settings" summary bar duplicated the ⋮ toolbar button (which opens the
       same panel) while permanently covering a strip of the image. Hide it and open the
       panel from ⋮; any tool tap still closes it. */
    .cal-sheet > details > summary{ display: none; }

    .cal-sheet>details>summary{
      background: #111;
      color: #ddd;
      padding: 0.75rem;
      border-top: 2px solid var(--cal-border);
      text-align: center;
      cursor: pointer;
      font: 14px/1.2 Segoe UI, system-ui, sans-serif;
      font-weight: 500;
    }

    .cal-sheet>details>summary:hover{
      background: #1a1a1a;
    }

    .cal-panel{
      background: #090909;
      border-top: 2px solid var(--cal-border);
      max-height: 50vh;
      overflow: auto;
      padding: 1rem;
      font: 13px/1.4 Segoe UI, system-ui, sans-serif;
      color: #ddd;
    }

    .cal-panel label{
      display: block;
      margin: 0.75rem 0;
      cursor: pointer;
    }

    .cal-panel input[type="text"],
    .cal-panel input[type="number"]{
      width: 100%;
      padding: 0.5rem;
      margin-top: 0.25rem;
      background: var(--cal-bg-medium);
      border: 1px solid var(--cal-border);
      border-radius: 6px;
      color: var(--cal-text);
      font: 14px/1.2 Segoe UI, system-ui, sans-serif;
    }

    .cal-panel input[type="range"]{
      width: 100%;
      height: 6px;
      background: var(--cal-border);
      border-radius: 3px;
      outline: none;
      -webkit-appearance: none;
      appearance: none;
    }

    .cal-panel input[type="range"]::-webkit-slider-thumb{
      -webkit-appearance: none;
      appearance: none;
      width: 20px;
      height: 20px;
      background: var(--cal-accent);
      cursor: pointer;
      border-radius: 50%;
      border: 2px solid #000;
    }

    .cal-panel input[type="range"]::-moz-range-thumb{
      width: 20px;
      height: 20px;
      background: var(--cal-accent);
      cursor: pointer;
      border-radius: 50%;
      border: 2px solid #000;
    }

    .cal-panel input[type="range"]:hover::-webkit-slider-thumb{
      background: var(--cal-accent-hover);
    }

    .cal-panel input[type="range"]:hover::-moz-range-thumb{
      background: var(--cal-accent-hover);
    }

    .cal-panel select{
      padding: 0.5rem;
      background: var(--cal-bg-medium);
      border: 1px solid var(--cal-border);
      border-radius: 6px;
      color: var(--cal-text);
      font: 14px/1.2 Segoe UI, system-ui, sans-serif;
      cursor: pointer;
    }

    .cal-view{
      height: 100dvh;
      box-sizing: border-box;
      padding-bottom: var(--cal-kpi-h);  /* reserve the status strip's row so bottom-edge
                                            measurements aren't hidden (and untappable) under it */
      position: relative;
      display: flex;
      flex-direction: column;
      overflow: hidden;      /* the canvas fills the space; no page scroll under the toolbar */
    }

    /* Focus mode hides the toolbar/sheet for an unobstructed view — reclaim the status
       strip's reserved row too so the image truly fills the screen. (The KPI element is
       a sibling of .cal-view, so it's hidden from JS in setCollapsed, not via CSS here.) */
    .cal-view[data-tools="collapsed"]{ padding-bottom: 0; }

    /* The toolbar wrapper takes its natural (content) height; the canvas fills the rest,
       so the whole image is visible below the toolbar instead of being cut off. */
    .cal-view > .cal-tools{ flex: 0 0 auto; }
    .cal-view canvas{
      display: block;
      width: 100%;
      flex: 1 1 auto;
      min-height: 0;
    }

    .cal-view[data-tools="collapsed"] .cal-topbar{ display: none; }
    .cal-view[data-tools="collapsed"] .cal-sheet{ display: none; }

    .cal-fab{
      position: fixed;
      right: 1rem;
      bottom: 1rem;
      z-index: 12;
      background: var(--cal-accent);
      color: #000;
      border: none;
      border-radius: 999px;
      padding: 0.75rem 1.25rem;
      font: 600 14px/1.2 Segoe UI, system-ui, sans-serif;
      display: none;
      align-items: center;
      gap: 0.5rem;
      box-shadow: 0 4px 16px rgba(0, 212, 255, 0.4);
      cursor: pointer;
      white-space: nowrap;
    }

    .cal-fab:hover{
      background: var(--cal-accent-hover);
      box-shadow: 0 6px 20px rgba(0, 212, 255, 0.5);
      transform: translateY(-2px);
    }

    .cal-fab span:first-child{
      font-size: 18px;
    }

    .cal-view[data-tools="collapsed"] .cal-fab{
      display: inline-flex;
    }

    /* Quick save menu. position:fixed (not absolute) so it is NOT clipped by the
       toolbar's overflow-x:auto — its coordinates are set from the button on open. */
    .cal-quick-save-menu{
      position: fixed;
      background: var(--cal-bg-dark);
      border: 2px solid var(--cal-border);
      border-radius: 8px;
      padding: 0.5rem;
      min-width: 200px;
      display: none;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
      z-index: 30;
    }

    .cal-quick-save-menu.active{
      display: block;
    }

    .cal-quick-save-menu button{
      width: 100%;
      padding: 0.75rem;
      margin: 0.25rem 0;
      background: var(--cal-bg-medium);
      border: 1px solid var(--cal-border);
      border-radius: 6px;
      color: var(--cal-text);
      font: 14px/1.2 Segoe UI, system-ui, sans-serif;
      cursor: pointer;
      text-align: left;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      transition: all 0.15s ease;
    }

    .cal-quick-save-menu button:hover{
      background: #222;
      border-color: var(--cal-accent);
    }

    .cal-quick-save-menu button .icon{
      font-size: 16px;
    }

    /* Always-visible status strip pinned to the very bottom. Now a flex row of wrapping
       chips (single DOM path for calibrated + uncalibrated), so the load-bearing
       calibration warning and its "Set a scale" recovery button never truncate the way
       the old white-space:nowrap ellipsized line did. Its measured height feeds back into
       --cal-kpi-h (see updateKPI) so the reserved space below tracks a two-line strip. */
    .cal-kpi{
      position: fixed;
      left: 0; right: 0; bottom: 0;
      z-index: 12;
      margin: 0;
      padding: 5px 10px;
      min-height: 24px;
      box-sizing: border-box;
      color: #cfcfd6;
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      align-items: center;
      gap: 5px 7px;
      white-space: normal;
      background: rgba(13,13,15,0.94);
      border-top: 1px solid var(--cal-border);
      backdrop-filter: blur(4px);
      font: 12px/1.3 Segoe UI, system-ui, sans-serif;
      font-variant-numeric: tabular-nums;  /* equal-width digits: zoom/scale value changes don't reflow chip widths */
    }
    /* Before the first measurement view is built the strip has no chips (e.g. on the
       landing page); collapse it so it doesn't show as a stray empty bar. */
    .cal-kpi:empty{ display: none; }
    .cal-chip{
      display: inline-flex; align-items: center;
      padding: 2px 9px; border-radius: 999px;
      background: var(--cal-bg-2); border: 1px solid var(--cal-border);
      color: #cfcfd6; font-size: 12px;
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

    /* Settings panel sticky header with an explicit Done/close (the bare <details> had no
       discoverable dismissal — you had to tap a tool, which side-effects a tool change). */
    .cal-panel-head{
      display: flex; align-items: center; justify-content: space-between;
      position: sticky; top: 0; z-index: 1;
      margin: -1rem -1rem 0.75rem; padding: 0.75rem 1rem;
      background: var(--cal-panel-bg, #0f0f12);
      border-bottom: 1px solid var(--cal-border);
    }
    .cal-panel-head strong{ font-size: 15px; }
    .cal-panel-head .cal-panel-done{
      background: var(--cal-bg-2); color: var(--cal-text);
      border: 1px solid var(--cal-border); border-radius: 8px;
      min-height: 40px; padding: 0 16px; font: 600 14px Segoe UI, system-ui, sans-serif; cursor: pointer;
    }
    .cal-panel-head .cal-panel-done:hover{ background: #26262c; border-color: var(--cal-accent); }

    /* Fixed primary Save button for touch: export is the app's headline output, so it is
       one thumb-tap regardless of scroll position or active tool. Mouse users have the
       toolbar Save PNG button instead. Hidden in focus mode. */
    .cal-save-fab{
      position: fixed; right: 1rem; bottom: calc(var(--cal-kpi-h) + 1rem); z-index: 12;
      background: var(--cal-accent); color: #000; border: none; border-radius: 999px;
      padding: 0.7rem 1.15rem; font: 600 14px/1 Segoe UI, system-ui, sans-serif;
      display: none; align-items: center; gap: 0.45rem; cursor: pointer;
      box-shadow: 0 4px 16px rgba(0,212,255,0.4); white-space: nowrap;
    }
    .cal-save-fab:hover{ background: var(--cal-accent-hover); }
    .cal-save-fab .cal-icon-emoji{ font-size: 18px; }
    @media (pointer: coarse){ .cal-save-fab{ display: inline-flex; } }
    .cal-view[data-tools="collapsed"] .cal-save-fab{ display: none; }
  `;
  document.head.appendChild(st);
})();

window.CalibUI = (function(){
  function build(rootEl, overlay){
    // Remove old UI, if any
    const old = rootEl.querySelector('.cal-tools');
    if (old) old.remove();

    // Hoisted so the top-level "Calibrate" chip (built later) can focus the same input
    // that lives in the settings panel.
    let calInput = null;
    // Opens the settings panel and focuses the calibration-square-size field.
    function openCalibration(){
      details.open = true;
      setTimeout(() => { if (calInput) { calInput.focus(); if (calInput.select) calInput.select(); } }, 0);
    }

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
      // The KPI strip lives OUTSIDE .cal-view (it's a sibling of the viewer), so a
      // descendant CSS selector can't reach it — toggle it directly for focus mode.
      const kpi = document.getElementById('cal-kpi');
      if (kpi) kpi.style.display = collapsed ? 'none' : '';
      // Toggling focus changes the canvas box (toolbar + reserved KPI row appear/vanish),
      // so re-fit: without this the backing store went stale, leaving a blurry
      // browser-scaled image with misaligned taps until an unrelated resize fired.
      if (overlay && overlay._onBoxResize) overlay._onBoxResize();
      // Don't strand keyboard focus on a now-hidden button.
      try {
        if (collapsed) { if (fab) fab.focus(); }
        else { (settingsBtn || focusBtn || {}).focus && (settingsBtn || focusBtn).focus(); }
      } catch (e) {}
    }

    // --------- Bottom sheet ----------
    const sheetWrap = document.createElement('div');
    sheetWrap.className = 'cal-sheet';

    const details = document.createElement('details');
    const sum = document.createElement('summary');
    sum.innerHTML = '⚙️ More Settings & Options';
    details.append(sum);

    const body = document.createElement('div');
    body.className = 'cal-panel';

    // Sticky header with an explicit Done button so the sheet has a discoverable close
    // (previously it could only be dismissed by tapping a tool, which changed the tool).
    const panelHead = document.createElement('div');
    panelHead.className = 'cal-panel-head';
    const panelTitle = document.createElement('strong');
    panelTitle.textContent = '⚙️ Settings';
    const panelDone = document.createElement('button');
    panelDone.type = 'button'; panelDone.className = 'cal-panel-done';
    panelDone.textContent = 'Done';
    panelDone.setAttribute('aria-label', 'Close settings');
    // Restore focus to the opener so keyboard/switch users aren't stranded on <body>.
    panelDone.onclick = () => { details.open = false; try { settingsBtn.focus(); } catch (e) {} };
    panelHead.append(panelTitle, panelDone);
    body.appendChild(panelHead);

    // Fill the bottom sheet
    if (overlay) {
      const Units = window.CalibUnits;

      // ——— Calibration (the reference that turns pixels into real measurements) ———
      const calHead = document.createElement('div');
      calHead.innerHTML = '<strong>📐 Calibration</strong>';
      calHead.style.cssText = 'margin:0.25rem 0 0.5rem;font-size:15px;';
      body.appendChild(calHead);

      const calLabel = document.createElement('label');
      calLabel.innerHTML = 'Calibration square size (mm):';
      calInput = document.createElement('input');
      calInput.type = 'number'; calInput.min = '0.1'; calInput.step = '0.1';
      calInput.placeholder = 'e.g. 30';
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
      setScaleBtn.type = 'button'; setScaleBtn.className = 'cal-icon';
      setScaleBtn.style.cssText = 'width:100%;justify-content:center;margin-bottom:0.4rem;';
      setScaleBtn.innerHTML = '<span class="cal-icon-emoji">📐</span><span>Set scale from a known line</span>';
      setScaleBtn.onclick = () => { if (overlay.setMode) overlay.setMode('setscale'); details.open = false; };
      body.appendChild(setScaleBtn);

      const clearScaleBtn = document.createElement('button');
      clearScaleBtn.type = 'button'; clearScaleBtn.className = 'cal-icon';
      clearScaleBtn.style.cssText = 'width:100%;justify-content:center;margin-bottom:0.75rem;';
      clearScaleBtn.innerHTML = '<span class="cal-icon-emoji">↺</span><span>Clear manual scale (use square)</span>';
      clearScaleBtn.onclick = () => { if (overlay.clearManualScale) overlay.clearManualScale(); };
      body.appendChild(clearScaleBtn);

      // Units selector
      if (Units && Units.defs) {
        const unitLabel = document.createElement('label');
        unitLabel.innerHTML = '<strong>Units:</strong>';
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
        unitLabel.appendChild(sel);
        body.appendChild(unitLabel);
      }

      // Snap behaviour
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
      body.appendChild(snapLabel);

      // Note text
      const noteLabel = document.createElement('label');
      noteLabel.innerHTML = '<strong>Default note text:</strong>';
      const noteInput = document.createElement('input');
      noteInput.type = 'text';
      noteInput.placeholder = 'Enter default text for notes…';
      noteInput.value = overlay.noteText || '';
      noteInput.oninput = () => {
        overlay.noteText = noteInput.value;
      };
      noteLabel.appendChild(noteInput);
      body.appendChild(noteLabel);

      // Show markers toggle
      const markersLabel = document.createElement('label');
      const markersChk = document.createElement('input');
      markersChk.type = 'checkbox';
      markersChk.checked = !!(overlay.opts && overlay.opts.showMarkers);
      markersChk.onchange = () => {
        overlay.opts.showMarkers = markersChk.checked;
        if (overlay.redraw) overlay.redraw();
      };
      markersLabel.appendChild(markersChk);
      markersLabel.appendChild(document.createTextNode(' Show calibration markers'));
      body.appendChild(markersLabel);

      // Show grid toggle
      const gridLabel = document.createElement('label');
      const gridChk = document.createElement('input');
      gridChk.type = 'checkbox';
      gridChk.checked = !!(overlay.opts && overlay.opts.showGrid);
      gridChk.onchange = () => {
        overlay.opts.showGrid = gridChk.checked;
        if (overlay.redraw) overlay.redraw();
      };
      gridLabel.appendChild(gridChk);
      gridLabel.appendChild(document.createTextNode(' Show measurement grid'));
      body.appendChild(gridLabel);

      // Text size slider
      const textSizeLabel = document.createElement('label');
      textSizeLabel.innerHTML = '<strong>Annotation Text Size:</strong>';
      const textSizeSlider = document.createElement('input');
      textSizeSlider.type = 'range';
      textSizeSlider.min = '0.5';
      textSizeSlider.max = '3.0';
      textSizeSlider.step = '0.1';
      textSizeSlider.value = (overlay.opts && overlay.opts.labelScale) || 1.35;
      textSizeSlider.style.width = '100%';
      textSizeSlider.style.marginTop = '0.5rem';

      const textSizeValue = document.createElement('span');
      textSizeValue.textContent = `${textSizeSlider.value}x`;
      textSizeValue.style.marginLeft = '0.5rem';
      textSizeValue.style.fontWeight = 'bold';
      textSizeValue.style.color = 'var(--cal-accent)';

      textSizeSlider.oninput = () => {
        overlay.opts.labelScale = parseFloat(textSizeSlider.value);
        textSizeValue.textContent = `${textSizeSlider.value}x`;
        if (overlay.redraw) overlay.redraw();
      };

      textSizeLabel.appendChild(textSizeSlider);
      textSizeLabel.appendChild(textSizeValue);
      body.appendChild(textSizeLabel);

      // Line thickness slider
      const lineThickLabel = document.createElement('label');
      lineThickLabel.innerHTML = '<strong>Line Thickness:</strong>';
      const lineThickSlider = document.createElement('input');
      lineThickSlider.type = 'range';
      lineThickSlider.min = '1';
      lineThickSlider.max = '8';
      lineThickSlider.step = '1';
      lineThickSlider.value = (overlay.opts && overlay.opts.linePx) || 3;
      lineThickSlider.style.width = '100%';
      lineThickSlider.style.marginTop = '0.5rem';

      const lineThickValue = document.createElement('span');
      lineThickValue.textContent = `${lineThickSlider.value}px`;
      lineThickValue.style.marginLeft = '0.5rem';
      lineThickValue.style.fontWeight = 'bold';
      lineThickValue.style.color = 'var(--cal-accent)';

      lineThickSlider.oninput = () => {
        overlay.opts.linePx = parseInt(lineThickSlider.value);
        lineThickValue.textContent = `${lineThickSlider.value}px`;
        if (overlay.redraw) overlay.redraw();
      };

      lineThickLabel.appendChild(lineThickSlider);
      lineThickLabel.appendChild(lineThickValue);
      body.appendChild(lineThickLabel);
    }

    details.append(body);
    sheetWrap.append(details);
    wrap.append(sheetWrap);

    // --------- Top bar ----------
    const top = document.createElement('div');
    top.className = 'cal-topbar';
    top.setAttribute('role', 'toolbar');
    top.setAttribute('aria-label', 'Measurement tools');

    // Per-tool keyboard shortcut (matches the keydown handler). setscale has none, so
    // map by tool name — NOT by array index, which would be off by one for every tool
    // after setscale.
    const modeKeys = { pan:'0', select:'1', segment:'2', polyline:'3', rectangle:'4',
                       angle:'5', circle:'6', circle3pt:'7', note:'8' };

    const modes = [
      ['pan',       '🖐',  'Pan',        'Pan/Move Image'],
      ['select',    '⌖',  'Select',     'Select Annotations'],
      ['setscale',  '📐',  'Set Scale',  'Set scale from a line of known length'],
      ['segment',   '📏',  'Measure',    'Measure Distance'],
      ['polyline',  '〰',  'Path',       'Measure Path/Perimeter (double-click or Enter to finish)'],
      ['rectangle', '▭',  'Area',       'Measure Area'],
      ['angle',     '∠',  'Angle',      'Measure Angle'],
      ['circle',    '⭕',  'Circle',     'Measure Circle/Curve'],
      ['circle3pt', '◎',  '3-Point',    'Fit Circle to 3 Points'],
      ['note',      '🏷',  'Note',       'Add Note/Label']
    ];

    const btn = (emoji, text, tooltip, fn, pressed) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'cal-icon';
      b.setAttribute('data-tooltip', tooltip);
      // Accessible name for screen readers and touch users (the visible label is
      // hidden on mobile and the hover tooltip only exists for mouse/desktop).
      b.setAttribute('aria-label', tooltip);
      b.innerHTML = `<span class="cal-icon-emoji">${emoji}</span><span class="cal-icon-text">${text}</span>`;
      if (pressed) b.setAttribute('aria-pressed','true');
      b.onclick = () => {
        fn && fn();
        details.open = false;
        reflect();
      };
      return b;
    };

    // Two clusters: measurement tools on the LEFT, actions on the RIGHT. justify-content
    // space-between on .cal-topbar keeps them apart on one row and drops the right cluster
    // cleanly to a second row when narrow — no more 3-4 ragged rows with orphaned dividers.
    const left = document.createElement('div');  left.className = 'cal-tb-left';
    const right = document.createElement('div'); right.className = 'cal-tb-right';

    // New Image button (goes back to upload)
    const newImageSection = document.createElement('div');
    newImageSection.className = 'cal-toolbar-section';

    const newImage = btn('⬅️', 'New', 'Upload New Image', () => {
      if (!overlay.ann || overlay.ann.items.length === 0 ||
          confirm('Start over with a new image? Current annotations will be lost.')) {
        window.location.reload();
      }
    });
    newImage.classList.add('cal-icon--label');   // keep the word label even in icon-only layouts

    newImageSection.appendChild(newImage);
    left.appendChild(newImageSection);

    // Calibration chip — the reference size was buried two taps deep in the settings
    // sheet, so a wrong/default square silently scaled every measurement. Surface it as
    // a top-level control that shows the active size and opens the field on tap.
    const calSection = document.createElement('div');
    calSection.className = 'cal-toolbar-section';
    calSection.setAttribute('role', 'group');
    calSection.setAttribute('aria-label', 'Calibration');
    // Built directly (not via btn()) because btn()'s handler closes the settings panel
    // after firing — which would immediately undo openCalibration()'s panel-open.
    const calChip = document.createElement('button');
    calChip.type = 'button';
    calChip.className = 'cal-icon cal-icon--label';
    calChip.innerHTML = '<span class="cal-icon-emoji">📐</span><span class="cal-icon-text"></span>';
    calChip.onclick = () => openCalibration();
    calSection.appendChild(calChip);
    left.appendChild(calSection);

    // Updates the chip's visible size + accessible label from the current scale state.
    function reflectCalChip(){
      const mm = overlay.currentMarkerSizeMM && overlay.currentMarkerSizeMM();
      const manual = overlay.opts && overlay.opts.manualMmPerPx;
      let text, label;
      if (manual)      { text = 'manual';      label = 'Scale: manual (from a known line) — tap to change'; }
      else if (mm)     { text = `${mm} mm`;     label = `Calibration square: ${mm} mm — tap to change`; }
      else             { text = 'set size';     label = 'Calibration square size not set — tap to set'; }
      const t = calChip.querySelector('.cal-icon-text');
      if (t){ t.textContent = text; t.style.display = 'inline'; }  // always show the size, even on mobile
      calChip.setAttribute('data-tooltip', label);
      calChip.setAttribute('aria-label', label);
    }
    reflectCalChip();

    // Mode buttons (annotation tools)
    const toolsSection = document.createElement('div');
    toolsSection.className = 'cal-toolbar-section';

    const modeBtns = modes.map(([m, icon, text, tooltip]) => {
      const tip = modeKeys[m] ? `${tooltip} (key ${modeKeys[m]})` : tooltip;
      return btn(icon, text, tip, () => overlay.setMode && overlay.setMode(m),
                 overlay.opts && overlay.opts.mode === m);
    });

    toolsSection.setAttribute('role', 'group');
    toolsSection.setAttribute('aria-label', 'Tools');
    toolsSection.append(...modeBtns);
    left.appendChild(toolsSection);

    // Edit section
    const editSection = document.createElement('div');
    editSection.className = 'cal-toolbar-section';

    const undo = btn('↶', 'Undo', 'Undo (Ctrl+Z)', () => {
      if (overlay.undo) overlay.undo();
    });

    const redo = btn('↷', 'Redo', 'Redo (Ctrl+Shift+Z)', () => {
      if (overlay.redo) overlay.redo();
    });
    redo.disabled = !(overlay.canRedo && overlay.canRedo());

    const del = btn('🗑', 'Delete', 'Delete Selected (Del)', () => {
      if (overlay.deleteSelected) overlay.deleteSelected();
    });
    del.disabled = !(overlay.ann && overlay.ann.selectedId != null);

    const finish = btn('✓', 'Finish', 'Finish current path (polyline)', () => {
      if (overlay.finishPolyline) overlay.finishPolyline();
    });
    finish.disabled = true;

    const clearAll = btn('🗑✖', 'Clear', 'Clear All Annotations', () => {
      if (confirm('Delete all annotations? (You can undo this.)')) {
        if (overlay.clearAll) overlay.clearAll();
      }
    });
    clearAll.classList.add('cal-btn-danger');  // visually distinct from single Delete

    editSection.setAttribute('role', 'group');
    editSection.setAttribute('aria-label', 'Edit');
    editSection.append(undo, redo, finish, del, clearAll);
    right.appendChild(editSection);

    // Zoom section
    const zoomSection = document.createElement('div');
    zoomSection.className = 'cal-toolbar-section';

    const zoomOut = btn('➖', '', 'Zoom Out', () => {
      if (overlay.zoomStep) overlay.zoomStep(1/1.2);
    });

    const zoomIn = btn('➕', '', 'Zoom In', () => {
      if (overlay.zoomStep) overlay.zoomStep(1.2);
    });

    const fit = btn('⤢', 'Fit', 'Fit to Screen', () => {
      if (overlay.fitToContainer) overlay.fitToContainer();
    });

    zoomSection.setAttribute('role', 'group');
    zoomSection.setAttribute('aria-label', 'Zoom');
    zoomSection.append(zoomOut, zoomIn, fit);
    right.appendChild(zoomSection);

    // Download section
    const downloadSection = document.createElement('div');
    downloadSection.className = 'cal-toolbar-section';
    downloadSection.style.position = 'relative';

    // Quick download PNG button
    const quickDownload = document.createElement('button');
    quickDownload.type = 'button';
    quickDownload.className = 'cal-icon cal-btn-download cal-icon--label';
    quickDownload.setAttribute('data-tooltip', 'Quick Download PNG');
    quickDownload.setAttribute('aria-label', 'Quick download PNG');
    quickDownload.innerHTML = '<span class="cal-icon-emoji">💾</span><span class="cal-icon-text">Save PNG</span>';
    quickDownload.onclick = () => { if (overlay.savePNG) overlay.savePNG(); };

    // Download options menu button
    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'cal-icon cal-icon--label';
    download.setAttribute('data-tooltip', 'More Download Options');
    download.setAttribute('aria-label', 'More download options');
    download.setAttribute('aria-haspopup', 'menu');
    download.setAttribute('aria-expanded', 'false');
    download.innerHTML = '<span class="cal-icon-emoji">⬇️</span><span class="cal-icon-text">Options</span>';

    // Quick save menu
    const saveMenu = document.createElement('div');
    saveMenu.className = 'cal-quick-save-menu';
    saveMenu.setAttribute('role', 'menu');

    const savePNG = document.createElement('button');
    savePNG.innerHTML = '<span class="icon">🖼️</span><span>Save PNG Image</span>';
    savePNG.onclick = () => { if (overlay.savePNG) overlay.savePNG(); saveMenu.classList.remove('active'); };

    const saveJSON = document.createElement('button');
    saveJSON.innerHTML = '<span class="icon">📄</span><span>Save JSON Data</span>';
    saveJSON.onclick = () => { if (overlay.saveJSON) overlay.saveJSON(); saveMenu.classList.remove('active'); };

    const saveBoth = document.createElement('button');
    saveBoth.innerHTML = '<span class="icon">📦</span><span>Save Both (PNG + JSON)</span>';
    saveBoth.onclick = () => {
      if (overlay.savePNG) overlay.savePNG();
      if (overlay.saveJSON) setTimeout(() => overlay.saveJSON(), 500);
      saveMenu.classList.remove('active');
    };

    const saveCSV = document.createElement('button');
    saveCSV.innerHTML = '<span class="icon">📊</span><span>Save CSV Spreadsheet</span>';
    saveCSV.onclick = () => { if (overlay.saveCSV) overlay.saveCSV(); saveMenu.classList.remove('active'); };

    const saveDXF = document.createElement('button');
    saveDXF.innerHTML = '<span class="icon">📐</span><span>Save DXF (CAD Format)</span>';
    saveDXF.onclick = async () => { if (overlay.saveDXF) await overlay.saveDXF(); saveMenu.classList.remove('active'); };

    const saveSVG = document.createElement('button');
    saveSVG.innerHTML = '<span class="icon">🖋️</span><span>Save SVG (Vector, mm)</span>';
    saveSVG.onclick = () => { if (overlay.saveSVG) overlay.saveSVG(); saveMenu.classList.remove('active'); };

    saveMenu.append(savePNG, saveJSON, saveCSV, saveDXF, saveSVG, saveBoth);
    Array.from(saveMenu.children).forEach(b => b.setAttribute('role', 'menuitem'));
    downloadSection.append(quickDownload, download, saveMenu);
    downloadSection.setAttribute('role', 'group');
    downloadSection.setAttribute('aria-label', 'Download');

    function closeSaveMenu(returnFocus){
      saveMenu.classList.remove('active');
      download.setAttribute('aria-expanded', 'false');
      if (returnFocus) download.focus();
    }
    function openSaveMenu(){
      // Anchor the fixed menu to the button, right-aligned, kept on-screen.
      const r = download.getBoundingClientRect();
      saveMenu.style.top = Math.round(r.bottom + 6) + 'px';
      saveMenu.style.left = 'auto';
      saveMenu.style.right = Math.max(6, Math.round(window.innerWidth - r.right)) + 'px';
      saveMenu.classList.add('active');
      download.setAttribute('aria-expanded', 'true');
      const first = saveMenu.querySelector('button');
      if (first) setTimeout(() => first.focus(), 0);
    }
    download.onclick = () => {
      if (saveMenu.classList.contains('active')) closeSaveMenu(false); else openSaveMenu();
    };

    // Close menu when clicking outside
    const onDocClickCloseSave = (e) => {
      if (!downloadSection.contains(e.target)) closeSaveMenu(false);
    };
    document.addEventListener('click', onDocClickCloseSave);
    // Escape closes and returns focus to the trigger (keyboard users could not dismiss it before).
    saveMenu.addEventListener('keydown', (e) => {
      if (e.key === 'Escape'){ e.preventDefault(); closeSaveMenu(true); }
    });

    right.appendChild(downloadSection);

    // Meta section (focus + settings), grouped so the separator + wrapping behave.
    const metaSection = document.createElement('div');
    metaSection.className = 'cal-toolbar-section';

    // Focus mode: hide the tools for an unobstructed, full-height view of the image.
    // The floating "Show Tools" button (below) brings them back.
    const focusBtn = btn('⛶', '', 'Focus mode (hide tools)', () => setCollapsed(true));

    // Settings button. Built DIRECTLY (not via btn()) — btn()'s handler force-closes the
    // sheet after firing, so routing this toggle through it opened then instantly closed
    // the panel (the ⋮ button never worked). Relabelled "Settings": the sheet had no
    // discoverable, correctly-named opener (only the 📐 chip, which reads as calibration).
    const settingsBtn = document.createElement('button');
    settingsBtn.type = 'button';
    settingsBtn.className = 'cal-icon';
    settingsBtn.setAttribute('data-tooltip', 'Settings');
    settingsBtn.setAttribute('aria-label', 'Settings');
    settingsBtn.setAttribute('aria-haspopup', 'dialog');
    settingsBtn.setAttribute('aria-expanded', 'false');
    settingsBtn.innerHTML = '<span class="cal-icon-emoji">⚙️</span><span class="cal-icon-text"></span>';
    settingsBtn.onclick = () => { details.open = !details.open; };
    // Keep aria-expanded honest whether the sheet is toggled from here, the 📐 chip,
    // the Done button, or a tool tap (which closes it as a side effect). Also hide the
    // Save FAB while the sheet is open so it doesn't float over the panel controls
    // (setting '' restores media-query/focus-mode CSS control on close).
    details.addEventListener('toggle', () => {
      settingsBtn.setAttribute('aria-expanded', String(details.open));
      if (saveFab) saveFab.style.display = details.open ? 'none' : '';
    });

    metaSection.append(focusBtn, settingsBtn);
    right.appendChild(metaSection);

    top.append(left, right);

    // Wrap the toolbar so a position-aware scroll cue can overlay its edges on touch
    // (where the bar becomes a single horizontally-scrollable row).
    const scrollWrap = document.createElement('div');
    scrollWrap.className = 'cal-topbar-scroll';
    const cueL = document.createElement('div'); cueL.className = 'cal-scroll-cue cal-scroll-cue--l';
    const cueR = document.createElement('div'); cueR.className = 'cal-scroll-cue cal-scroll-cue--r';
    scrollWrap.append(top, cueL, cueR);
    wrap.prepend(scrollWrap);

    // Show a left/right fade only for the side(s) that still have toolbar off-screen, and
    // size the fades to the toolbar's real height. Runs on scroll + when the layout changes.
    function updateScrollCue(){
      // build() runs once per upload and the old toolbar is removed from the DOM; the
      // window 'resize' listener below would otherwise pile up across uploads. Self-remove
      // once our toolbar is detached (matches how the overlay tears down its own listeners).
      if (!top.isConnected){ window.removeEventListener('resize', updateScrollCue); return; }
      const max = top.scrollWidth - top.clientWidth;
      const sl = top.scrollLeft;
      if (max > 2) {
        scrollWrap.toggleAttribute('data-ov-left', sl > 2);
        scrollWrap.toggleAttribute('data-ov-right', sl < max - 2);
      } else {
        scrollWrap.removeAttribute('data-ov-left');
        scrollWrap.removeAttribute('data-ov-right');
      }
      const h = top.offsetHeight; cueL.style.height = cueR.style.height = h + 'px';
    }
    top.addEventListener('scroll', updateScrollCue, { passive: true });
    window.addEventListener('resize', updateScrollCue, { passive: true });
    requestAnimationFrame(updateScrollCue);

    // --------- Floating "Tools" button for focus mode ----------
    const fab = document.createElement('button');
    fab.type = 'button';
    fab.className = 'cal-fab';
    fab.innerHTML = '<span>🧰</span><span>Show Tools</span>';
    fab.title = 'Show toolbar and panels';
    fab.onclick = () => setCollapsed(false);
    wrap.appendChild(fab);

    // --------- Fixed primary Save button (touch only) ----------
    // Export is the app's headline output but lived at the far end of the mobile scroll
    // strip. This puts a one-tap PNG save in the thumb zone; the format menu (JSON/CSV/
    // DXF/SVG) stays on the Options button. Hidden in focus mode via CSS.
    const saveFab = document.createElement('button');
    saveFab.type = 'button';
    saveFab.className = 'cal-save-fab';
    saveFab.setAttribute('aria-label', 'Save annotated PNG');
    saveFab.innerHTML = '<span class="cal-icon-emoji">💾</span><span>Save</span>';
    saveFab.onclick = () => { if (overlay.savePNG) overlay.savePNG(); };
    wrap.appendChild(saveFab);

    // Deterministic teardown of the document/window listeners this build() attached, called
    // from overlay.destroy() when the #viewer subtree is swapped on a new upload — otherwise
    // one save-menu click listener + one scroll-cue resize listener would leak per upload.
    overlay._uiCleanup = () => {
      document.removeEventListener('click', onDocClickCloseSave);
      window.removeEventListener('resize', updateScrollCue);
    };

    // Reflect pressed tool + selection state
    function reflect(){
      const mode = overlay.opts && overlay.opts.mode;
      modeBtns.forEach((b, i) => {
        const active = mode === modes[i][0];
        b.setAttribute('aria-pressed', String(active));
      });
      del.disabled = !(overlay.ann && overlay.ann.selectedId != null);
      finish.disabled = !(mode === 'polyline' && overlay.selectedPoints && overlay.selectedPoints.length >= 2);
      undo.disabled = !(overlay.canUndo && overlay.canUndo());
      redo.disabled = !(overlay.canRedo && overlay.canRedo());
      reflectCalChip();
    }

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
