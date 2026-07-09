// Enhanced CalibUI — improved annotation tools & easy downloads
(function ensureCss(){
  const ID = "cal-ui-enhanced-css";
  if (document.getElementById(ID)) return;
  const st = document.createElement('style'); st.id = ID;
  st.textContent = `
    :root{
      --cal-topbar-h: 56px;
      --cal-accent: #00d4ff;
      --cal-accent-hover: #00b8e6;
      --cal-bg-dark: #0e0e0e;
      --cal-bg-medium: #181818;
      --cal-border: #2a2a2a;
      --cal-text: #eee;
    }
    @media (min-width:900px){ :root{ --cal-topbar-h: 60px; } }

    .cal-topbar{
      position: sticky;
      top: 0;
      z-index: 10;
      min-height: var(--cal-topbar-h);
      background: var(--cal-bg-dark);
      border-bottom: 2px solid var(--cal-border);
      display: flex;
      align-items: center;
      gap: 0.6rem;
      padding: 0.8rem 1rem;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      scrollbar-width: thin;
      flex-wrap: nowrap; /* Don't wrap on mobile - allow horizontal scroll */
    }

    /* Scroll hint gradient on mobile */
    @media (max-width: 767px){
      .cal-topbar::after{
        content: '';
        position: sticky;
        right: 0;
        width: 40px;
        height: 100%;
        background: linear-gradient(to left, var(--cal-bg-dark) 0%, transparent 100%);
        pointer-events: none;
        flex-shrink: 0;
        margin-left: auto;
      }
    }

    /* On desktop, allow wrapping into grouped rows (kept compact to preserve image space) */
    @media (min-width: 768px){
      .cal-topbar{
        flex-wrap: wrap;
        gap: 0.5rem 0.8rem;
        padding: 0.5rem 1rem;
      }
    }

    .cal-toolbar-section{
      display: flex;
      align-items: center;
      gap: 0.5rem;
      padding: 0.4rem 0;
      flex-shrink: 0; /* Prevent sections from shrinking */
    }

    @media (min-width: 768px){
      .cal-toolbar-section{
        gap: 0.4rem;
        padding: 0;
      }
    }

    .cal-toolbar-divider{
      width: 1px;
      height: 28px;
      background: var(--cal-border);
      margin: 0 0.15rem;
      flex-shrink: 0;
    }

    @media (min-width: 768px){
      .cal-toolbar-divider{
        height: 32px;
        margin: 0 0.25rem;
      }
    }

    /* Hide dividers on very small screens to save space */
    @media (max-width: 480px){
      .cal-toolbar-divider{
        display: none;
      }
    }

    .cal-icon{
      background: var(--cal-bg-medium);
      color: var(--cal-text);
      border: 1px solid var(--cal-border);
      border-radius: 8px;
      padding: 0.5rem;
      min-width: 44px;
      min-height: 44px;
      font: 14px/1.2 Segoe UI, system-ui, sans-serif;
      white-space: nowrap;
      cursor: pointer;
      transition: all 0.15s ease;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.35rem;
      position: relative;
      flex-shrink: 0;
    }

    @media (min-width: 640px){
      .cal-icon{
        padding: 0.5rem 0.75rem;
      }
    }

    .cal-icon:hover{
      background: #222;
      border-color: #3a3a3a;
    }

    .cal-icon[aria-pressed="true"]{
      background: var(--cal-accent);
      color: #000;
      border-color: var(--cal-accent);
      font-weight: 600;
    }

    .cal-icon[aria-pressed="true"]:hover{
      background: var(--cal-accent-hover);
    }

    .cal-icon:disabled{
      opacity: 0.4;
      cursor: not-allowed;
    }

    .cal-icon.cal-btn-download{
      background: linear-gradient(135deg, #00d4ff 0%, #0099ff 100%);
      color: #000;
      font-weight: 600;
      border: none;
      box-shadow: 0 2px 8px rgba(0, 212, 255, 0.3);
    }

    .cal-icon.cal-btn-download:hover{
      background: linear-gradient(135deg, #00b8e6 0%, #0088e6 100%);
      box-shadow: 0 3px 12px rgba(0, 212, 255, 0.4);
      transform: translateY(-1px);
    }

    .cal-icon .cal-icon-emoji{
      font-size: 18px;
      line-height: 1;
    }

    @media (min-width: 640px){
      .cal-icon .cal-icon-emoji{
        font-size: 16px;
      }
    }

    .cal-icon .cal-icon-text{
      font-size: 13px;
      display: none; /* Hidden on mobile */
    }

    @media (min-width: 640px){
      .cal-icon .cal-icon-text{
        display: inline; /* Show text on larger screens */
      }
    }

    /* Tooltip - only on desktop with mouse */
    @media (hover: hover) and (pointer: fine) {
      .cal-icon::after{
        content: attr(data-tooltip);
        position: absolute;
        bottom: calc(100% + 8px);
        left: 50%;
        transform: translateX(-50%);
        background: rgba(0, 0, 0, 0.9);
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

      .cal-icon:hover::after{
        opacity: 1;
      }
    }

    /* Scrollbar styling for toolbar */
    .cal-topbar::-webkit-scrollbar{
      height: 4px;
    }

    .cal-topbar::-webkit-scrollbar-track{
      background: var(--cal-bg-dark);
    }

    .cal-topbar::-webkit-scrollbar-thumb{
      background: var(--cal-border);
      border-radius: 2px;
    }

    .cal-topbar::-webkit-scrollbar-thumb:hover{
      background: #3a3a3a;
    }

    .cal-sheet{
      position: fixed;
      left: 0;
      right: 0;
      bottom: 28px;            /* sit above the fixed KPI status strip */
      z-index: 11;
      touch-action: pan-y pinch-zoom;
    }

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
      position: relative;
      display: flex;
      flex-direction: column;
      overflow: hidden;      /* the canvas fills the space; no page scroll under the toolbar */
    }

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

    /* Always-visible status strip pinned to the very bottom (carries the
       "not calibrated" warning), with the settings sheet stacked just above it. */
    .cal-kpi{
      position: fixed;
      left: 0; right: 0; bottom: 0;
      z-index: 12;
      margin: 0;
      padding: 5px 10px;
      color: #ddd;
      text-align: center;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      background: rgba(10,10,10,0.9);
      border-top: 1px solid var(--cal-border);
      font: 12px/1.35 Segoe UI, system-ui, sans-serif;
    }
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

    // --------- Bottom sheet ----------
    const sheetWrap = document.createElement('div');
    sheetWrap.className = 'cal-sheet';

    const details = document.createElement('details');
    const sum = document.createElement('summary');
    sum.innerHTML = '⚙️ More Settings & Options';
    details.append(sum);

    const body = document.createElement('div');
    body.className = 'cal-panel';

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
      const calInput = document.createElement('input');
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
      b.innerHTML = `<span class="cal-icon-emoji">${emoji}</span><span class="cal-icon-text">${text}</span>`;
      if (pressed) b.setAttribute('aria-pressed','true');
      b.onclick = () => {
        fn && fn();
        details.open = false;
        reflect();
      };
      return b;
    };

    // New Image button (goes back to upload)
    const newImageSection = document.createElement('div');
    newImageSection.className = 'cal-toolbar-section';

    const newImage = btn('⬅️', 'New', 'Upload New Image', () => {
      if (!overlay.ann || overlay.ann.items.length === 0 ||
          confirm('Start over with a new image? Current annotations will be lost.')) {
        window.location.reload();
      }
    });

    newImageSection.appendChild(newImage);
    top.appendChild(newImageSection);

    // Divider
    const divNew = document.createElement('div');
    divNew.className = 'cal-toolbar-divider';
    top.appendChild(divNew);

    // Mode buttons (annotation tools)
    const toolsSection = document.createElement('div');
    toolsSection.className = 'cal-toolbar-section';

    const modeBtns = modes.map(([m, icon, text, tooltip]) =>
      btn(icon, text, tooltip, () => overlay.setMode && overlay.setMode(m),
          overlay.opts && overlay.opts.mode === m)
    );

    toolsSection.append(...modeBtns);
    top.appendChild(toolsSection);

    // Divider
    const div1 = document.createElement('div');
    div1.className = 'cal-toolbar-divider';
    top.appendChild(div1);

    // Edit section
    const editSection = document.createElement('div');
    editSection.className = 'cal-toolbar-section';

    const undo = btn('↶', 'Undo', 'Undo Last Action', () => {
      if (overlay.undo) overlay.undo();
    });

    const del = btn('🗑', 'Delete', 'Delete Selected', () => {
      if (overlay.deleteSelected) overlay.deleteSelected();
    });
    del.disabled = !(overlay.ann && overlay.ann.selectedId != null);

    const finish = btn('✓', 'Finish', 'Finish current path (polyline)', () => {
      if (overlay.finishPolyline) overlay.finishPolyline();
    });
    finish.disabled = true;

    const clearAll = btn('🗑✖', 'Clear', 'Clear All Annotations', () => {
      if (confirm('Delete all annotations? This cannot be undone.')) {
        if (overlay.clearAll) overlay.clearAll();
      }
    });

    editSection.append(undo, finish, del, clearAll);
    top.appendChild(editSection);

    // Divider
    const div2 = document.createElement('div');
    div2.className = 'cal-toolbar-divider';
    top.appendChild(div2);

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

    zoomSection.append(zoomOut, zoomIn, fit);
    top.appendChild(zoomSection);

    // Divider
    const div3 = document.createElement('div');
    div3.className = 'cal-toolbar-divider';
    top.appendChild(div3);

    // Download section
    const downloadSection = document.createElement('div');
    downloadSection.className = 'cal-toolbar-section';
    downloadSection.style.position = 'relative';

    // Quick download PNG button
    const quickDownload = document.createElement('button');
    quickDownload.type = 'button';
    quickDownload.className = 'cal-icon cal-btn-download';
    quickDownload.setAttribute('data-tooltip', 'Quick Download PNG');
    quickDownload.innerHTML = '<span class="cal-icon-emoji">💾</span><span class="cal-icon-text">Save PNG</span>';
    quickDownload.onclick = () => { if (overlay.savePNG) overlay.savePNG(); };

    // Download options menu button
    const download = document.createElement('button');
    download.type = 'button';
    download.className = 'cal-icon';
    download.setAttribute('data-tooltip', 'More Download Options');
    download.innerHTML = '<span class="cal-icon-emoji">⬇️</span><span class="cal-icon-text">Options</span>';

    // Quick save menu
    const saveMenu = document.createElement('div');
    saveMenu.className = 'cal-quick-save-menu';

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

    saveMenu.append(savePNG, saveJSON, saveCSV, saveDXF, saveBoth);
    downloadSection.append(quickDownload, download, saveMenu);

    download.onclick = () => {
      const willOpen = !saveMenu.classList.contains('active');
      if (willOpen) {
        // Anchor the fixed menu to the button, right-aligned, kept on-screen.
        const r = download.getBoundingClientRect();
        saveMenu.style.top = Math.round(r.bottom + 6) + 'px';
        saveMenu.style.left = 'auto';
        saveMenu.style.right = Math.max(6, Math.round(window.innerWidth - r.right)) + 'px';
      }
      saveMenu.classList.toggle('active');
    };

    // Close menu when clicking outside
    document.addEventListener('click', (e) => {
      if (!downloadSection.contains(e.target)) {
        saveMenu.classList.remove('active');
      }
    });

    top.appendChild(downloadSection);

    // Focus mode: hide the tools for an unobstructed, full-height view of the image.
    // The floating "Show Tools" button (below) brings them back.
    const focus = btn('⛶', '', 'Focus mode (hide tools)', () => setCollapsed(true));
    top.appendChild(focus);

    // More button
    const more = btn('⋮', '', 'More Settings', () => {
      details.open = !details.open;
    });

    top.appendChild(more);

    wrap.prepend(top);

    // --------- Floating "Tools" button for focus mode ----------
    const fab = document.createElement('button');
    fab.type = 'button';
    fab.className = 'cal-fab';
    fab.innerHTML = '<span>🧰</span><span>Show Tools</span>';
    fab.title = 'Show toolbar and panels';
    fab.onclick = () => setCollapsed(false);
    wrap.appendChild(fab);

    // Reflect pressed tool + selection state
    function reflect(){
      const mode = overlay.opts && overlay.opts.mode;
      modeBtns.forEach((b, i) => {
        const active = mode === modes[i][0];
        b.setAttribute('aria-pressed', String(active));
      });
      del.disabled = !(overlay.ann && overlay.ann.selectedId != null);
      finish.disabled = !(mode === 'polyline' && overlay.selectedPoints && overlay.selectedPoints.length >= 2);
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
