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
    /* A phone keeps the top bar to ONE row: a second row cost ~50 px of photo. The word labels
       that made it wrap (Add photo, Build 3D part (n)) give way to the icon, and the part's
       view count rides on the icon as a badge. */
    .cal-icon .cal-badge{ display: none; }
    @media (max-width: 600px){
      .cal-topbar{ flex-wrap: nowrap; gap: 0.3rem; padding: 0.4rem 0.4rem; }
      .cal-tb-left, .cal-tb-right{ gap: 0.25rem; min-width: 0; }
      .cal-icon{ padding: 0.4rem 0.45rem; }
      .cal-icon.cal-narrow-icon .cal-icon-text{ display: none; }
      /* The scale chip keeps its word (the accent ring says "unscaled" too) but is the one
         button that gives way: with 🧩 shown and a new, unscaled photo, "Set scale" made the
         row 404 px on a 390 px phone and pushed ⛶ off the edge. */
      #cal-tb-scale{ flex-shrink: 1; min-width: 44px; overflow: hidden; }
      #cal-tb-scale .cal-icon-text{ min-width: 0; overflow: hidden; text-overflow: ellipsis; }
      .cal-icon .cal-badge{
        display: inline-flex; position: absolute; top: -5px; right: -5px;
        min-width: 18px; height: 18px; padding: 0 4px; box-sizing: border-box; border-radius: 9px;
        align-items: center; justify-content: center;
        background: #000; color: var(--cal-accent); border: 1px solid var(--cal-accent);
        font: 700 11px/1 Segoe UI, system-ui, sans-serif;
      }
    }

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
      /* flex-start, not the base rule's centre: 12 tools + undo/redo are ~838 px tall, and in
         an 813 px rail (1400x900) a centred column spilled 19 px off the TOP, where overflow
         can't be scrolled to, and clipped Select. Spill only at the bottom, which scrolls. */
      .cal-rail{
        flex-flow: column nowrap; align-items: stretch; width: 86px;
        justify-content: flex-start;
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
      /* A selected outline offers five actions; on a phone they wrap to a second row rather
         than run off the screen, clear of the zoom buttons on the right. */
      flex-wrap: wrap; align-content: flex-end; max-width: calc(100% - 84px);
    }
    /* Phone: one row, docked clear of the home-indicator strip. Wrapped, a selected outline's
       five buttons stood three rows (~145 px) deep over the photo at 390x844 and covered the
       outline's own lower corners; the secondary ones now sit behind "⋯ More" (see reflect),
       and anything still too wide scrolls sideways instead of stacking. */
    @media (max-width: 600px){
      .cal-context{
        gap: 6px; flex-wrap: nowrap; overflow-x: auto; overflow-y: hidden;
        margin-bottom: calc(8px + env(safe-area-inset-bottom, 0px));
        scrollbar-width: none;
      }
      .cal-context::-webkit-scrollbar{ display: none; }
      /* Shrink, never scroll off: with a wider system font (measured on the CI runner's) the
         row outgrew the screen and pushed "⋯" out of reach. Labels truncate instead; More
         keeps its size. */
      .cal-context button{ padding: 8px 12px; min-height: 40px; font-size: 13px; flex: 0 1 auto; min-width: 0;
                           white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .cal-context button.cal-ctx-more{ flex: 0 0 auto; }
    }
    .cal-context button.show.cal-ctx-folded{ display: none; }
    .cal-context button.cal-ctx-more{ min-width: 44px; padding: 8px 10px; justify-content: center; font-size: 18px; background: var(--cal-bg-2); color: var(--cal-text); border: 1px solid var(--cal-border); }
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
    /* The Set-scale chooser's Apply / Verify / Clear / Close sit outside .cal-coach-actions,
       so they carried no size and measured ~19 px tall on a phone: size the classes themselves. */
    .cal-coach-primary, .cal-coach-secondary{
      min-height: 44px; padding: 8px 14px; border-radius: 10px;
      font: 600 14px Segoe UI, system-ui, sans-serif; cursor: pointer;
    }
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

  // The "no scale yet" notice sits over the top of the photo — right where the Set-scale bar
  // opens — and used to stay there while the user did exactly what it asked. It now steps
  // aside once a scale is being set or has been set, and any banner can be dismissed with a
  // tap. A new photo brings it back (build() runs once per photo); a changed message does too.
  function noticeBanner(){ return document.getElementById('upload-error'); }
  function hideScaleNotice(){
    const b = noticeBanner();
    if (b && b.querySelector('.cal-scale-notice')) b.classList.add('cal-dismissed');
  }
  function armNoticeBanner(){
    const b = noticeBanner();
    if (!b) return;
    b.classList.remove('cal-dismissed');
    if (b.__calArmed) return;
    b.__calArmed = true;
    if (!document.getElementById('cal-notice-style')) {
      const st = document.createElement('style'); st.id = 'cal-notice-style';
      // Other layers that already say "no scale yet" — the first-run coach, the simple-mode
      // card — mark the body while they show, and the banner yields to them instead of
      // stacking a third copy of the same message over the photo. State, not an event:
      // the banner's content can arrive after either of them has rendered.
      st.textContent = '#upload-error.cal-dismissed{display:none!important}' +
        'body.cal-scale-carried #upload-error:has(.cal-scale-notice),' +
        'body.cal-coach-up #upload-error:has(.cal-scale-notice){display:none!important}' +
        '#upload-error{cursor:pointer}#upload-error::after{content:"  ✕";opacity:.6;font-weight:700}';
      document.head.appendChild(st);
    }
    b.title = 'Tap to dismiss';
    b.addEventListener('click', () => b.classList.add('cal-dismissed'));
    new MutationObserver(() => b.classList.remove('cal-dismissed'))
      .observe(b, { childList: true, characterData: true, subtree: true });
  }

  // A modal card opening over the photo: the last toast ("Saved as the Top view… Build 3D
  // part") lives 8 s at z-index 70, above the card, and its button would take a tap meant for
  // the card's row under it. CalibToast.dismiss() where it exists; otherwise the page-wide
  // handle the overlay's _toast keeps (its placer timer stops once the node is detached).
  function dismissToast(){
    try {
      if (window.CalibToast && typeof window.CalibToast.dismiss === 'function') { window.CalibToast.dismiss(); return; }
      const t = window.__calLastToast;
      if (t && t.parentNode) t.parentNode.removeChild(t);
    } catch (e) {}
  }

  // Leave simple mode the way the simple card's own Set scale / Trace buttons do: its card is
  // fixed over the bottom ~40% of the photo (560x289 px at 1366x768), which is where a ruler
  // laid along a step usually is, and it swallowed both taps of the scale line. The simple
  // layer's poll removes its card and shows "◂ Simple view" within 500 ms of the flag; the
  // body class and the card go now so the very next tap reaches the canvas.
  function leaveSimple(overlay){
    if (!document.body.classList.contains('camscan-simple')) return;
    // Left for a step that needs taps on the photo: the simple layer (calib.simple.js) brings
    // the guided card back once that step is done, instead of leaving the user in the full
    // toolbar with no "Next:" — and saving '0' for every later photo too.
    if (!window.__camscanReturnToSimple) window.__camscanReturnToSimple = true;
    try { localStorage.setItem('camscan.simplemode', '0'); } catch (e) {}
    document.body.classList.remove('camscan-simple');
    const card = document.getElementById('cal-simple');
    if (card) card.style.display = 'none';
    if (overlay && overlay._onBoxResize) setTimeout(() => { try { overlay._onBoxResize(); } catch (e) {} }, 0);
  }

  function build(rootEl, overlay){
    armNoticeBanner();
    // Remove any chrome from a previous build on this view (defensive — one build per
    // upload in practice). The <canvas> is a direct grid child and is left in place.
    rootEl.querySelectorAll('.cal-topbar, .cal-rail, .cal-sheet, .cal-zoom, .cal-context, .cal-coach, .cal-fab')
      .forEach(el => el.remove());
    document.querySelectorAll('#cal-tidy-menu, #cal-more-menu').forEach(el => el.remove());

    let calInput = null;  // hoisted so the top-bar 📐 chip can focus the panel's size field
    function openCalibration(){
      dismissCoach();
      details.open = true;
      setTimeout(() => { if (calInput) { calInput.focus(); if (calInput.select) calInput.select(); } }, 0);
    }

    // One canonical "how do you want to set the scale?" chooser. Every scale entry point
    // (the 📐 chip, the KPI "Set a scale" chip, the coach, the settings sheet) routes here
    // instead of scattering the user across half a dozen buttons and modes. It only launches
    // existing flows — it doesn't reimplement any scale math.
    let scalePanel = null;
    function closeScalePanel(){ if (scalePanel && scalePanel.parentNode) scalePanel.remove(); scalePanel = null; }
    // fromUser: a tap on a Set scale button leaves simple mode, as the simple card's own
    // button does. The automatic first prompt on an unscaled photo does not — closing it must
    // leave the simple card and its own Set scale button in place — and instead leaves simple
    // mode once a method that needs taps on the photo is picked (go() below).
    function openScalePanel(fromUser){
      dismissCoach();
      hideScaleNotice();
      if (fromUser === true) leaveSimple(overlay);
      if (scalePanel) return;                 // already open — don't stack
      dismissToast();
      scalePanel = document.createElement('div');
      scalePanel.className = 'cal-coach';     // reuse the dimmed-backdrop + card styling
      scalePanel.addEventListener('click', (e) => { if (e.target === scalePanel) closeScalePanel(); });
      const card = document.createElement('div'); card.className = 'cal-coach-card';
      card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true'); card.setAttribute('aria-label', 'Set the scale');
      card.innerHTML = '<h3>📐 Set the scale</h3>' +
        '<p style="margin:0.1rem 0 0.6rem;color:#9aa;font-size:13px;">Pick one — every measurement rescales instantly.</p>';

      const mkBig = (emoji, title, sub, fn) => {
        const b = document.createElement('button'); b.type = 'button'; b.className = 'cal-panel-btn';
        b.style.cssText = 'display:flex;gap:0.6rem;align-items:flex-start;text-align:left;width:100%;margin:0.3rem 0;min-height:44px;';
        b.innerHTML = `<span style="font-size:18px;line-height:1.2;">${emoji}</span>` +
          `<span><b>${title}</b><br><span style="color:#9aa;font-size:12px;">${sub}</span></span>`;
        b.onclick = fn;
        return b;
      };
      // Every method below needs taps on the photo, and the simple card would sit on them.
      const go = (mode) => { closeScalePanel(); leaveSimple(overlay); if (overlay.setMode) overlay.setMode(mode); };
      // 1 — a ruler or tape lying on the part: what people actually have, so it leads. Two
      // numbered marks along the middle, not the far ends: a ruler's ends are often worn,
      // rounded or under the part, and the middle is where Square up looks for its edges.
      const rulerBtn = mkBig('📏', 'Ruler or tape on the part',
        'Tap two numbered marks along its middle, then type the distance between them. '
        + 'Lay it ON the face you measure, not beside the part.',
        () => go('setscale'));
      rulerBtn.id = 'cal-scale-ruler';
      card.appendChild(rulerBtn);
      // 2 — printed square / marker: pick a size. Second: it is the most exact reference
      // when the user has one, but few do.
      const sqHead = document.createElement('div');
      sqHead.innerHTML = '<b>🟩 I printed a square or the CamScan marker</b><br>' +
        '<span style="color:#9aa;font-size:12px;">Tap its printed edge size:</span>';
      sqHead.style.cssText = 'margin:0.6rem 0 0.35rem;';
      card.appendChild(sqHead);
      const sizeRow = document.createElement('div');
      sizeRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:0.4rem;margin:0 0 0.7rem;';
      [30, 40, 60].forEach(v => {
        const c = document.createElement('button'); c.type = 'button'; c.className = 'cal-panel-btn';
        c.style.cssText = 'padding:0.5rem 0.9rem;flex:0 0 auto;min-height:44px;';
        c.textContent = v === 40 ? `${v} mm ★` : `${v} mm`;
        if (v === 40) c.title = 'Recommended marker size';
        c.onclick = () => { if (overlay.setMarkerSizeMM) overlay.setMarkerSizeMM(v); if (calInput) calInput.value = String(v); closeScalePanel(); };
        sizeRow.appendChild(c);
      });
      const otherBtn = document.createElement('button'); otherBtn.type = 'button'; otherBtn.className = 'cal-panel-btn';
      otherBtn.style.cssText = 'padding:0.5rem 0.9rem;flex:0 0 auto;min-height:44px;';
      otherBtn.textContent = 'Other…';
      otherBtn.onclick = () => { closeScalePanel(); openCalibration(); };
      sizeRow.appendChild(otherBtn);
      card.appendChild(sizeRow);

      // 3 — a rectangle (tilt-corrected).
      card.appendChild(mkBig('📄', 'A rectangle you know the size of (tilt-corrected)',
        'Tap its 4 corners — a sheet of paper, or a square you lay out with a ruler. '
        + 'Best for big parts: the reference should cover a good part of what you measure.',
        () => go('setscalerect')));

      // 4 — the plane the feature actually sits in. This is the biggest single source of
      // error in the app and the only one the user can neither see nor guess: the reference's
      // homography rectifies the REFERENCE's plane, so a part's top face reads d/(d-h) too
      // large — about +5% for a part 20 mm thick at arm's length — and, unlike camera
      // tilt, that error is exactly as large in a perfectly square-on photo.
      const planeWrap = document.createElement('div');
      planeWrap.style.cssText = 'margin-top:0.7rem;padding:0.6rem 0.7rem;border:1px solid #2a3a44;border-radius:9px;background:#0e1519;';
      const planeHead = document.createElement('div');
      planeHead.style.cssText = 'font-weight:600;font-size:13.5px;margin-bottom:0.15rem;';
      planeHead.textContent = '⬆ Measuring a raised face?';
      const planeHint = document.createElement('div');
      planeHint.style.cssText = 'color:#9fb2bb;font-size:12px;line-height:1.45;margin-bottom:0.45rem;';
      planeHint.textContent = 'If the face you are measuring sits above the reference (a part with '
        + 'thickness), say how far above. A negative value means the outline is below the '
        + 'reference (a ruler resting on top of the part, measuring its base). Best of all: rest '
        + 'the reference ON that face and re-shoot — that is exact, this is a correction.';
      const planeRow = document.createElement('div');
      planeRow.style.cssText = 'display:flex;gap:0.4rem;align-items:center;flex-wrap:wrap;';
      const planeIn = document.createElement('input');
      // No min: a face below the reference is a real case (the ruler lies on the part's top).
      planeIn.type = 'number'; planeIn.step = '0.5';
      planeIn.placeholder = 'mm above reference';
      planeIn.value = (overlay.featureHeightMM && overlay.featureHeightMM()) || '';
      planeIn.style.cssText = 'width:8.5rem;min-height:40px;padding:0.4rem 0.5rem;background:#0b0f12;'
        + 'border:1px solid #29414c;border-radius:8px;color:#eef;font-size:15px;';
      planeIn.addEventListener('keydown', (e) => e.stopPropagation());
      const planeMsg = document.createElement('div');
      planeMsg.style.cssText = 'font-size:12px;line-height:1.45;margin-top:0.45rem;color:#9fb2bb;';
      const showAdvice = (h) => {
        const adv = overlay.parallaxAdvice ? overlay.parallaxAdvice(h) : null;
        planeMsg.textContent = adv || (h ? '' : 'Measuring in the reference\u2019s own plane.');
        planeMsg.style.color = adv ? '#e6b970' : '#9fb2bb';
      };
      const applyBtn = document.createElement('button');
      applyBtn.type = 'button'; applyBtn.className = 'cal-coach-secondary'; applyBtn.textContent = 'Apply';
      applyBtn.onclick = async () => {
        const h = parseFloat(planeIn.value);
        applyBtn.disabled = true;
        const ok = await overlay.setFeatureHeight(Number.isFinite(h) ? h : 0);
        applyBtn.disabled = false;
        if (!ok && Number.isFinite(h) && h) {
          planeIn.value = '';
          planeMsg.style.color = '#e6b970';
          planeMsg.textContent = 'Cannot correct this photo: it needs the auto-detected square. '
            + 'A manual scale carries no camera geometry.';
        } else {
          showAdvice(Number.isFinite(h) ? h : 0);
        }
      };
      const planeClear = document.createElement('button');
      planeClear.type = 'button'; planeClear.className = 'cal-coach-secondary';
      planeClear.textContent = 'In the reference\u2019s plane';
      planeClear.onclick = async () => { planeIn.value = ''; await overlay.setFeatureHeight(0); showAdvice(0); };
      planeIn.addEventListener('input', () => {
        const h = parseFloat(planeIn.value);
        showAdvice(Number.isFinite(h) ? h : 0);
      });
      planeRow.append(planeIn, applyBtn, planeClear);
      planeWrap.append(planeHead, planeHint, planeRow, planeMsg);
      card.appendChild(planeWrap);
      showAdvice((overlay.featureHeightMM && overlay.featureHeightMM()) || 0);

      const foot = document.createElement('div');
      foot.style.cssText = 'display:flex;gap:0.5rem;align-items:center;margin-top:0.7rem;';
      const verifyBtn = document.createElement('button'); verifyBtn.type = 'button'; verifyBtn.className = 'cal-coach-secondary';
      verifyBtn.textContent = '✅ Verify'; verifyBtn.title = 'Measure a 2nd known feature to check the scale';
      verifyBtn.onclick = () => go('verifyscale');
      const clearBtn = document.createElement('button'); clearBtn.type = 'button'; clearBtn.className = 'cal-coach-secondary';
      clearBtn.textContent = '↺ Clear'; clearBtn.title = 'Clear a manual scale and fall back to the detected square';
      clearBtn.onclick = () => { if (overlay.clearManualScale) overlay.clearManualScale(); closeScalePanel(); };
      const doneBtn = document.createElement('button'); doneBtn.type = 'button'; doneBtn.className = 'cal-coach-primary';
      doneBtn.textContent = 'Close'; doneBtn.style.marginLeft = 'auto'; doneBtn.onclick = () => closeScalePanel();
      foot.append(verifyBtn, clearBtn, doneBtn);
      card.appendChild(foot);

      scalePanel.appendChild(card);
      rootEl.appendChild(scalePanel);
      setTimeout(() => { try { rulerBtn.focus(); } catch (e) {} }, 0);
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
      calHint.style.cssText = 'font-size:12px;color:#9aa;margin:-0.4rem 0 0.5rem;';
      body.appendChild(calHint);

      // Quick-pick the standard printed marker sizes (40 mm is the recommended one).
      const calChips = document.createElement('div');
      calChips.style.cssText = 'display:flex;flex-wrap:wrap;gap:0.4rem;margin:0 0 0.7rem;';
      [30, 40, 60].forEach(v => {
        const c = document.createElement('button'); c.type = 'button'; c.className = 'cal-panel-btn';
        c.style.cssText = 'padding:0.35rem 0.7rem;flex:0 0 auto;';
        c.textContent = v === 40 ? `${v} mm ★` : `${v} mm`;
        if (v === 40) c.title = 'Recommended marker size';
        c.onclick = () => { if (overlay.setMarkerSizeMM) overlay.setMarkerSizeMM(v); if (calInput) calInput.value = String(v); };
        calChips.appendChild(c);
      });
      body.appendChild(calChips);

      // One launcher for every other way to set scale (line / paper / verify / clear),
      // instead of four separate buttons cluttering the panel.
      const setScaleBtn = document.createElement('button');
      setScaleBtn.type = 'button'; setScaleBtn.className = 'cal-panel-btn';
      setScaleBtn.innerHTML = '<span>📐</span><span>Set scale another way…</span>';
      setScaleBtn.onclick = () => { details.open = false; openScalePanel(); };
      body.appendChild(setScaleBtn);

      // Everything below is display/units chrome — collapsed by default so the setting that
      // actually drives measurements (the scale, above) isn't buried under cosmetic sliders.
      const disp = document.createElement('details');
      const dispSum = document.createElement('summary');
      dispSum.textContent = 'Display & units';
      dispSum.style.cssText = 'margin:0.7rem 0 0.4rem;font-size:15px;font-weight:bold;cursor:pointer;';
      disp.appendChild(dispSum);
      const dispBody = document.createElement('div');
      disp.appendChild(dispBody);

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
        dispBody.appendChild(unitLabel);
      }

      const snapLabel = document.createElement('label');
      const snapChk = document.createElement('input'); snapChk.type = 'checkbox';
      snapChk.checked = !!(overlay.opts && overlay.opts.snap);
      snapChk.onchange = () => { overlay.opts.snap = snapChk.checked; if (overlay.updateKPI) overlay.updateKPI(); };
      snapLabel.appendChild(snapChk); snapLabel.appendChild(document.createTextNode(' Snap to marker corners'));
      dispBody.appendChild(snapLabel);

      const noteLabel = document.createElement('label');
      noteLabel.innerHTML = '<strong>Default note text:</strong>';
      const noteInput = document.createElement('input'); noteInput.type = 'text';
      noteInput.placeholder = 'Enter default text for notes…'; noteInput.value = overlay.noteText || '';
      noteInput.oninput = () => { overlay.noteText = noteInput.value; };
      noteLabel.appendChild(noteInput);
      dispBody.appendChild(noteLabel);

      const markersLabel = document.createElement('label');
      const markersChk = document.createElement('input'); markersChk.type = 'checkbox';
      markersChk.checked = !!(overlay.opts && overlay.opts.showMarkers);
      markersChk.onchange = () => { overlay.opts.showMarkers = markersChk.checked; if (overlay.redraw) overlay.redraw(); };
      markersLabel.appendChild(markersChk); markersLabel.appendChild(document.createTextNode(' Show calibration markers'));
      dispBody.appendChild(markersLabel);

      const gridLabel = document.createElement('label');
      const gridChk = document.createElement('input'); gridChk.type = 'checkbox';
      gridChk.checked = !!(overlay.opts && overlay.opts.showGrid);
      gridChk.onchange = () => { overlay.opts.showGrid = gridChk.checked; if (overlay.redraw) overlay.redraw(); };
      gridLabel.appendChild(gridChk); gridLabel.appendChild(document.createTextNode(' Show measurement grid'));
      dispBody.appendChild(gridLabel);

      const textSizeLabel = document.createElement('label');
      textSizeLabel.innerHTML = '<strong>Annotation text size:</strong>';
      const textSizeSlider = document.createElement('input');
      // step 0.05 so the default labelScale (1.35) lands ON the grid — with step 0.1 a range
      // input snaps 1.35 to 1.4 and the readout would show "1.4x" while the actual scale stayed
      // 1.35 until the user dragged it.
      textSizeSlider.type = 'range'; textSizeSlider.min = '0.5'; textSizeSlider.max = '3.0'; textSizeSlider.step = '0.05';
      textSizeSlider.value = (overlay.opts && overlay.opts.labelScale) || 1.35;
      textSizeSlider.style.width = '100%'; textSizeSlider.style.marginTop = '0.5rem';
      const textSizeValue = document.createElement('span');
      textSizeValue.textContent = `${textSizeSlider.value}x`;
      textSizeValue.style.cssText = 'margin-left:0.5rem;font-weight:bold;color:var(--cal-accent);';
      textSizeSlider.oninput = () => { overlay.opts.labelScale = parseFloat(textSizeSlider.value); textSizeValue.textContent = `${textSizeSlider.value}x`; if (overlay.redraw) overlay.redraw(); };
      textSizeLabel.appendChild(textSizeSlider); textSizeLabel.appendChild(textSizeValue);
      dispBody.appendChild(textSizeLabel);

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
      dispBody.appendChild(lineThickLabel);

      // Measurement labels on/off — turn off to edit a dense outline without the readout
      // boxes covering the points.
      const labelsLabel = document.createElement('label');
      const labelsChk = document.createElement('input'); labelsChk.type = 'checkbox';
      labelsChk.checked = (overlay.opts && overlay.opts.showLabels) !== false;
      labelsChk.onchange = () => { overlay.opts.showLabels = labelsChk.checked; if (overlay.redraw) overlay.redraw(); };
      labelsLabel.appendChild(labelsChk); labelsLabel.appendChild(document.createTextNode(' Show measurement labels'));
      dispBody.appendChild(labelsLabel);

      // Point size — the vertex-handle radius (constant on-screen size). Smaller = easier to
      // place a dense outline precisely; larger = easier to grab.
      const ptSizeLabel = document.createElement('label');
      ptSizeLabel.innerHTML = '<strong>Point size:</strong>';
      const ptSizeSlider = document.createElement('input');
      ptSizeSlider.type = 'range'; ptSizeSlider.min = '2'; ptSizeSlider.max = '12'; ptSizeSlider.step = '1';
      ptSizeSlider.value = (overlay.opts && overlay.opts.handlePx) || 5;
      ptSizeSlider.style.width = '100%'; ptSizeSlider.style.marginTop = '0.5rem';
      const ptSizeValue = document.createElement('span');
      ptSizeValue.textContent = `${ptSizeSlider.value}px`;
      ptSizeValue.style.cssText = 'margin-left:0.5rem;font-weight:bold;color:var(--cal-accent);';
      ptSizeSlider.oninput = () => { overlay.opts.handlePx = parseInt(ptSizeSlider.value); ptSizeValue.textContent = `${ptSizeSlider.value}px`; if (overlay.redraw) overlay.redraw(); };
      ptSizeLabel.appendChild(ptSizeSlider); ptSizeLabel.appendChild(ptSizeValue);
      dispBody.appendChild(ptSizeLabel);

      body.appendChild(disp);

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

    // ---- 📷 Add photo (was "New") ----
    // New used to say "annotations will be lost" and then drop the PHOTO while keeping its 3D
    // view: beforeReload only syncs a reopened side, and a fresh upload has no
    // data-view-index, so after the reload the landing read "no photos saved · 3D views: Top"
    // and the Top outline could never be reopened, re-scaled or squared up. It now stores the
    // photo in the part first, under its 3D role's name when it has one.
    const ROLE_LABEL = { top: 'Top', side: 'Side', end: 'End', cavity: 'Cavity' };
    function assignedRoleLabel(){
      const J = window.CalibJob;
      try {
        const views = (J && J.part && J.part().views) || {};
        const pid = J.photoId && J.photoId();
        const r = Object.keys(ROLE_LABEL).find(k => views[k] && views[k].source === pid);
        return r ? ROLE_LABEL[r] : null;
      } catch (e) { return null; }
    }
    // Worth keeping: a scale, any annotation, or a 3D view traced on it. A bare unscaled shot
    // with nothing on it is the one case that is dropped.
    function photoWorthKeeping(role){
      return !!(role || (overlay.isCalibrated && overlay.isCalibrated()) ||
                (overlay.ann && overlay.ann.items.length));
    }
    // Store the photo on screen in the part. Resolves to the label it is kept under, or null.
    function keepCurrentPhoto(){
      const J = window.CalibJob;
      if (!J) return Promise.resolve(null);
      const role = assignedRoleLabel();
      try {
        // A reopened side is already in the part: persist its edits in place, no relabel.
        if (J.isCurrentStored && J.isCurrentStored()) {
          if (J.syncOpen) J.syncOpen();
          return Promise.resolve((J.currentLabel && J.currentLabel()) || role || 'its side');
        }
        // Already added under a name the user chose ("Front" from a label chip): addView
        // replaces the label in place, so recapturing as "View 1" or "Side" would put Front
        // back on the checklist as missing. Recapture under the stored name; the job API has
        // no sync label read, so take it from the committed store.
        if (J.hasCurrent && J.hasCurrent()) {
          const v = document.querySelector('.cal-view');
          const sid = v && v.getAttribute('data-json');
          return Promise.resolve(J.flush && J.flush())
            .then(() => (J.persisted ? J.persisted() : null))
            .then(stored => {
              const rec = stored && (stored.views || []).find(x => x.sourceId === sid);
              if (!rec || !rec.label) return 'its side';
              J.addView(rec.label);   // same entry (dedup by sourceId), latest marks
              return rec.label;
            })
            .catch(() => 'its side');
        }
        if (!photoWorthKeeping(role)) return Promise.resolve(null);
        const label = role || ('View ' + (((J.count && J.count()) || 0) + 1));
        // The shared capture, where the job layer has it; otherwise the same steps by hand.
        if (typeof J.captureCurrent === 'function') J.captureCurrent(label);
        else if (J.addView) J.addView(label);
        if ((J.hasCurrent && J.hasCurrent()) || (J.isCurrentStored && J.isCurrentStored())) return Promise.resolve(label);
      } catch (e) {}
      return Promise.resolve(null);
    }
    const newImage = iconBtn('📷', 'Add photo', 'Add another photo — this one is kept in the part', () => {
      const J = window.CalibJob;
      const willKeep = !!J && ((J.isCurrentStored && J.isCurrentStored()) || (J.hasCurrent && J.hasCurrent()) ||
                               photoWorthKeeping(assignedRoleLabel()));
      // Confirm only when something would be thrown away: marks on a photo with no job to
      // keep it in.
      if (!willKeep && overlay.ann && overlay.ann.items.length &&
          !confirm('Start over with a new image? Current annotations will be lost.')) return;
      (willKeep ? keepCurrentPhoto() : Promise.resolve(null)).then(kept => {
        // The store refused it (full, say): its own warning is up; don't reload over the marks.
        if (willKeep && !kept && overlay.ann && overlay.ann.items.length &&
            !confirm('This photo could not be saved to the part. Start over with a new image anyway?')) return;
        let wait = Promise.resolve();
        if (kept) {
          const msg = kept === 'its side' ? 'Your changes to this photo were saved to the part.'
            : `This photo was saved to the part as "${kept}"; reopen it from Sides.`;
          if (overlay._toast) overlay._toast(msg, { autoMs: 4000 });
          // Long enough to read the line before the page goes (~1.2 s for 12 words).
          wait = new Promise(res => setTimeout(res, 1200));
        }
        // The job (other photos, 3D views) must be saved before the page goes: its store
        // writes complete asynchronously, and a write merely started during unload may not.
        const save = (J && J.beforeReload) ? J.beforeReload() : Promise.resolve();
        Promise.all([save, wait]).then(() => window.location.reload());
      });
    });
    newImage.id = 'cal-tb-new';
    newImage.classList.add('cal-narrow-icon');

    // Calibration chip — the reference size drives every measurement, so surface it up top.
    const calChip = document.createElement('button');
    calChip.type = 'button'; calChip.className = 'cal-icon'; calChip.id = 'cal-tb-scale';
    calChip.innerHTML = '<span class="cal-icon-emoji">📐</span><span class="cal-icon-text"></span>';
    calChip.onclick = () => openScalePanel(true);

    // The simple card and the chooser already say "Set scale": on a phone the chip says less.
    function narrowScreen(){ try { return window.matchMedia('(max-width: 600px)').matches; } catch (e) { return false; } }
    function reflectCalChip(){
      const mm = overlay.currentMarkerSizeMM && overlay.currentMarkerSizeMM();
      const paper = overlay.opts && overlay.opts.manualHomography;
      const manual = overlay.opts && overlay.opts.manualMmPerPx;
      const uncal = !(overlay.isCalibrated && overlay.isCalibrated());
      let text, label;
      const squared = paper && overlay._manualRef && overlay._manualRef.kind === 'squared';
      // Named for what the user laid down — "manual" and "paper" were the code's words, and a
      // ruler user never saw the word ruler anywhere on screen.
      if (squared)     { text = 'ruler ✓ squared'; label = 'Scale: ruler line, squared up from the part\'s parallel sides (tilt-corrected) — tap to change'; }
      else if (paper)  { text = 'rectangle'; label = 'Scale: from a rectangle of known size (tilt-corrected) — tap to change'; }
      else if (manual) { text = 'ruler';    label = 'Scale: from a line along your ruler — tap to change'; }
      // "40 mm" on a photo with no scale read as if a scale were set (it is only the size a
      // printed square WOULD be): say what is actually true, and what the chip does.
      else if (uncal)  { text = narrowScreen() ? 'Scale' : 'Set scale'; label = 'No scale yet — tap to set one'; }
      else if (mm)     { text = `${mm} mm`;  label = `Calibration square: ${mm} mm — tap to change`; }
      else             { text = 'Set size';  label = 'Not calibrated — tap to set your square size'; }
      const t = calChip.querySelector('.cal-icon-text');
      if (t) t.textContent = text;
      calChip.classList.toggle('cal-chip-uncal', uncal);
      calChip.setAttribute('data-tooltip', label);
      calChip.setAttribute('aria-label', label);
    }
    reflectCalChip();

    // The 3D part is assembled across photos, so its way in has to be visible on every photo
    // once it has begun — not only in the export menu, where nobody looked for it.
    const partChip = document.createElement('button');
    partChip.type = 'button'; partChip.className = 'cal-icon cal-narrow-icon'; partChip.id = 'cal-tb-part';
    partChip.innerHTML = '<span class="cal-icon-emoji">🧩</span><span class="cal-icon-text"></span><span class="cal-badge"></span>';
    partChip.onclick = () => window.CalibPart3D && window.CalibPart3D.open();
    let partSig = null;
    function reflectPartChip(){
      const roles = (window.CalibPart3D && window.CalibPart3D.assigned) ? window.CalibPart3D.assigned() : [];
      const sig = roles.join(',');
      if (sig === partSig) return;
      partSig = sig;
      partChip.style.display = roles.length ? '' : 'none';
      partChip.querySelector('.cal-icon-text').textContent = `Build 3D part (${roles.length})`;
      partChip.querySelector('.cal-badge').textContent = String(roles.length);
      // Once a view is assigned, building the part IS the next step: the chip is the accented
      // primary, not one grey icon among the file buttons.
      partChip.classList.toggle('cal-btn-primary', roles.length >= 1);
      const label = 'Build the 3D part from its views: ' + roles.join(', ');
      partChip.setAttribute('data-tooltip', label); partChip.setAttribute('aria-label', label);
    }
    reflectPartChip();
    document.addEventListener('camscan-job-changed', reflectPartChip);   // removed in _uiCleanup

    left.append(newImage, calChip, partChip);

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
      // The only export that is not flat. For an extruded part this is the finished thing a
      // slicer can open, instead of a drawing somebody has to extrude again by hand.
      mkSave('🧊', 'Save STL (3D print)', () => overlay.saveSTL && overlay.saveSTL()),
      // A part that changes shape along its length (a tapered toe, a rounded end, a cavity
      // that fits over something) is built from several views, across photos.
      mkSave('🧩', 'Build 3D part from views…', () => window.CalibPart3D && window.CalibPart3D.open()),
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
                       angle:'5', circle:'6', circle3pt:'7', note:'8', outline:'9' };
    // Display order (index parity with modeBtns drives reflect(); the keyboard map is
    // name-keyed, so this order is free to be the most usable one).
    const modes = [
      ['select',    '⌖',  'Select',   'Select / move annotations'],
      ['pan',       '🖐',  'Pan',      'Pan the image'],
      ['segment',   '📏',  'Measure',  'Measure a distance'],
      ['polyline',  '〰',  'Path',     'Measure a path / perimeter (double-tap or Enter to finish)'],
      // One tap here and a tap on the part traces it: the tool used to drop into placing dots
      // by hand, with auto-trace a second button that only then appeared.
      ['outline',   '✨',  'Trace',    'Trace the part: tap it and its outline is found — a to-scale, extrudable profile (✎ Draw by hand to place the dots yourself)'],
      ['rectangle', '▭',  'Area',     'Measure a rectangular area'],
      ['angle',     '∠',  'Angle',    'Measure an angle'],
      ['circle',    '⭕',  'Circle',   'Measure a circle (2 points)'],
      ['circle3pt', '◎',  '3-pt',     'Fit a circle to 3 points'],
      ['note',      '🏷',  'Note',     'Add a note / label'],
      ['setscale',  '📐',  'Set scale','Set the scale from a line of known length'],
      ['setscalerect','📄','Paper',    'Set a perspective-corrected scale from a sheet of paper — tap its 4 corners'],
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
      b.onclick = () => {
        if (m === 'outline' && overlay.armAutoOutline) overlay.armAutoOutline();
        else if (overlay.setMode) overlay.setMode(m);
        details.open = false; dismissCoach(); reflect();
      };
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
    const ctxAuto = document.createElement('button'); ctxAuto.type = 'button'; ctxAuto.className = 'cal-ctx-finish';
    ctxAuto.innerHTML = '✨ Auto-trace'; ctxAuto.setAttribute('aria-label', 'Auto-detect the part outline — then tap the part');
    ctxAuto.onclick = () => { if (overlay.armAutoOutline) overlay.armAutoOutline(); reflect(); };
    // While Trace is armed: the other two ways round a part the tap cannot separate.
    const ctxHand = document.createElement('button'); ctxHand.type = 'button'; ctxHand.className = 'cal-ctx-finish';
    ctxHand.id = 'cal-ctx-hand';
    ctxHand.innerHTML = '✎ Draw by hand'; ctxHand.setAttribute('aria-label', 'Place the outline\'s dots yourself instead of auto-tracing');
    ctxHand.onclick = () => { if (overlay.drawByHand) overlay.drawByHand(); reflect(); };
    const ctxBox = document.createElement('button'); ctxBox.type = 'button'; ctxBox.className = 'cal-ctx-finish';
    ctxBox.id = 'cal-ctx-box';
    ctxBox.innerHTML = '▭ Draw a box'; ctxBox.setAttribute('aria-label', 'Drag a box round the whole part — it traces inside the box');
    ctxBox.onclick = () => { if (overlay.armBoxTrace) overlay.armBoxTrace(); reflect(); };
    // A closed outline is a PROFILE. Push it straight through by a thickness and it becomes
    // the solid — for extruded trim, or a cover's top outline pushed to its height, that is
    // the whole part. "Depth" read as "length of a running board" to someone who had traced
    // the TOP, and invited ~700 mm where the cover is ~30 mm tall; Extrude and a question that
    // works for any view say what it does. Only shown with a closed outline selected.
    const ctxDepth = document.createElement('button'); ctxDepth.type = 'button'; ctxDepth.className = 'cal-ctx-finish';
    ctxDepth.id = 'cal-ctx-extrude';
    ctxDepth.innerHTML = '⇥ Extrude'; ctxDepth.setAttribute('aria-label', 'Extrude: push this outline straight through by a thickness');
    ctxDepth.title = 'Quick solid: push this outline straight through by a thickness';
    ctxDepth.onclick = () => {
      const prof = overlay.selectedProfile && overlay.selectedProfile();
      if (!prof) return;
      const prev = document.getElementById('cal-depth-input'); if (prev) prev.remove();
      const box = document.createElement('div'); box.id = 'cal-depth-input';
      box.style.cssText = 'position:fixed;z-index:41;left:50%;top:16%;transform:translateX(-50%);'
        + 'background:#0e0e0e;border:2px solid #00d4ff;border-radius:10px;padding:12px 14px;'
        + 'box-shadow:0 8px 24px rgba(0,0,0,.6);font:15px Segoe UI,system-ui,sans-serif;color:#eee;'
        + 'max-width:calc(100vw - 16px);';
      const h = document.createElement('div'); h.textContent = 'How thick is the part, straight through this outline?';
      h.style.cssText = 'font-weight:700;margin-bottom:4px;';
      const sub = document.createElement('div');
      sub.textContent = 'Traced from above: its height. Ends slope or round? Use 🧩 Use in 3D part. '
        + 'Leave blank to go back to a flat outline.';
      sub.style.cssText = 'font-size:12px;color:#9aa;margin-bottom:10px;max-width:34ch;';
      const row = document.createElement('div'); row.style.cssText = 'display:flex;gap:8px;align-items:center;';
      const inp = document.createElement('input'); inp.type = 'number'; inp.min = '0'; inp.step = '0.1';
      inp.inputMode = 'decimal'; inp.placeholder = 'mm';
      inp.value = prof.depth_mm > 0 ? String(prof.depth_mm) : '';
      inp.style.cssText = 'width:120px;min-height:44px;padding:8px 10px;background:#181818;'
        + 'border:1px solid #2a2a2a;border-radius:8px;color:#eee;font:16px Segoe UI,system-ui,sans-serif;';
      const mk = (label, primary, fn) => { const b = document.createElement('button'); b.type = 'button';
        b.textContent = label;
        b.style.cssText = 'min-height:44px;padding:8px 14px;border-radius:8px;cursor:pointer;font-size:14px;'
          + (primary ? 'background:#00d4ff;color:#000;border:none;font-weight:600;'
                     : 'background:#151515;color:#cde;border:1px solid #2a3540;');
        b.onclick = fn; return b; };
      const close = () => box.remove();
      // Optional: hollow it into a cover. Most replacement covers are a thin plate with a wall
      // round the edge; the solid version of a 900 x 180 x 25 mm step pad is 6.5x the plastic
      // and would not fit over the board it covers.
      const wallRow = document.createElement('label');
      wallRow.style.cssText = 'display:flex;gap:8px;align-items:center;margin-top:10px;font-size:13px;color:#cde;';
      const wallInp = document.createElement('input'); wallInp.type = 'number'; wallInp.min = '0';
      wallInp.step = '0.1'; wallInp.inputMode = 'decimal'; wallInp.placeholder = 'solid';
      wallInp.id = 'cal-wall-input';
      wallInp.value = prof.wall_mm > 0 ? String(prof.wall_mm) : '';
      wallInp.style.cssText = 'width:90px;min-height:40px;padding:6px 10px;background:#181818;'
        + 'border:1px solid #2a2a2a;border-radius:8px;color:#eee;font:15px Segoe UI,system-ui,sans-serif;';
      const wallTxt = document.createElement('span');
      wallTxt.textContent = 'mm wall — hollow it as a cover (blank = solid)';
      wallRow.append(wallInp, wallTxt);
      const apply = () => {
        overlay.setExtrusionDepth(parseFloat(inp.value));
        if (overlay.setWallThickness) overlay.setWallThickness(parseFloat(wallInp.value));
        close(); reflect();
      };
      row.append(inp,
        mk('Set', true, apply),
        mk('Clear', false, () => { overlay.setExtrusionDepth(0); close(); reflect(); }),
        mk('✕', false, close));
      box.append(h, sub, row, wallRow); document.body.appendChild(box);
      box.addEventListener('keydown', (e) => { e.stopPropagation();
        if (e.key === 'Escape') { e.preventDefault(); close(); }
        if (e.key === 'Enter') { e.preventDefault(); apply(); } });
      setTimeout(() => { try { inp.focus(); inp.select(); } catch (e) {} }, 0);
    };

    // Shown once a trace exists: the repair for the failure the plain trace cannot avoid —
    // a dark part next to its own shadow, which no threshold can separate.
    const ctxNotPart = document.createElement('button'); ctxNotPart.type = 'button';
    ctxNotPart.className = 'cal-ctx-finish';
    ctxNotPart.innerHTML = '✖ Not the part';
    ctxNotPart.setAttribute('aria-label', 'Tap something the trace wrongly included');
    ctxNotPart.title = 'Tap a shadow or background the outline swallowed, and it re-traces';
    ctxNotPart.onclick = () => { if (overlay.armBackgroundTap) overlay.armBackgroundTap(); };

    // Assign the selected outline to a view of the 3D part (Top / Side / End / Cavity). A part
    // that is not the same shape all the way along is built from several views, each traced
    // in whichever photo shows it best — see calib.part3d.js.
    const ctx3d = document.createElement('button'); ctx3d.type = 'button'; ctx3d.className = 'cal-ctx-finish';
    ctx3d.id = 'cal-ctx-3d';
    ctx3d.innerHTML = '🧩 Use in 3D part';
    ctx3d.setAttribute('aria-label', 'Use this outline as a view of the 3D part');
    ctx3d.title = 'Use this outline as the Top, Side, End or Cavity of a 3D part';
    ctx3d.onclick = () => { if (window.CalibPart3D) window.CalibPart3D.pick(ctx3d); };

    // A traced side wobbles with every scuff and shadow along the edge; this makes each long
    // side one straight line on the photo's edge instead of dragging its dots one by one.
    const ctxStraight = document.createElement('button'); ctxStraight.type = 'button'; ctxStraight.className = 'cal-ctx-finish';
    ctxStraight.id = 'cal-ctx-straighten';
    ctxStraight.innerHTML = '📏 Straighten';
    ctxStraight.setAttribute('aria-label', 'Straighten this outline onto the part\'s edges');
    ctxStraight.title = 'Make each long side one straight line on the part\'s edge; rounded corners stay as traced';
    ctxStraight.onclick = () => { if (overlay.straightenSelected) overlay.straightenSelected(); };
    // The detailed second pass: straight sides on the edge AND each corner given the radius
    // the photo shows (a straightened corner is sharp; a cover's often is not).
    const ctxRefine = document.createElement('button'); ctxRefine.type = 'button'; ctxRefine.className = 'cal-ctx-finish';
    ctxRefine.id = 'cal-ctx-refine';
    ctxRefine.innerHTML = '✨ Refine';
    ctxRefine.setAttribute('aria-label', 'Refine this outline: straight sides and rounded corners from the photo');
    ctxRefine.title = 'Straight sides on the part\'s edge, and each corner given the radius the photo shows';
    ctxRefine.onclick = () => { if (overlay.straightenSelected) overlay.straightenSelected('refine'); };
    // An edge that is neither straight nor one radius — or a side the trace put on the wrong
    // line, dragged back near the right one — follows the photo's edge, dot by dot.
    const ctxSnap = document.createElement('button'); ctxSnap.type = 'button'; ctxSnap.className = 'cal-ctx-finish';
    ctxSnap.id = 'cal-ctx-snap';
    ctxSnap.innerHTML = '🧲 Snap to edge';
    ctxSnap.setAttribute('aria-label', 'Snap this outline onto the photo\'s edge near it, adding dots where it curves');
    ctxSnap.title = 'Move the outline onto the edge in the photo within a few pixels of it, with the dots its curves need';
    ctxSnap.onclick = () => { if (overlay.straightenSelected) overlay.straightenSelected('snap'); };
    // Corrects the photo's tilt from the ruler's edges under the scale line, or — when none
    // are found — the outline's two long sides, which are parallel on the part (square_up.py).
    const ctxSquare = document.createElement('button'); ctxSquare.type = 'button'; ctxSquare.className = 'cal-ctx-finish';
    ctxSquare.id = 'cal-ctx-squareup';
    ctxSquare.innerHTML = '⊞ Square up';
    ctxSquare.setAttribute('aria-label', 'Square up: correct the camera tilt using the ruler (or the part\'s sides)');
    ctxSquare.title = 'Correct the camera tilt from your ruler\'s edges (or, without them, the part\'s long sides)';
    ctxSquare.onclick = () => { if (overlay.squareUp) overlay.squareUp(); };
    // A symmetric part comes back from a photo lopsided (light on one side, shadow on the
    // other); this averages the outline with its own mirror image.
    const ctxSym = document.createElement('button'); ctxSym.type = 'button'; ctxSym.className = 'cal-ctx-finish';
    ctxSym.id = 'cal-ctx-symmetric';
    ctxSym.innerHTML = '⇋ Symmetric';
    ctxSym.setAttribute('aria-label', 'Make this outline symmetric');
    ctxSym.title = 'Average the outline with its mirror image — for a part that is symmetric';
    ctxSym.onclick = () => { if (overlay.symmetrizeSelected) overlay.symmetrizeSelected('auto'); };
    // The four clean-up tools share one button and a small menu: as separate buttons they
    // stacked four rows deep over the photo on a phone.
    const ctxTidy = document.createElement('button'); ctxTidy.type = 'button'; ctxTidy.className = 'cal-ctx-finish';
    ctxTidy.id = 'cal-ctx-tidy';
    ctxTidy.innerHTML = '✨ Tidy ▾';
    ctxTidy.setAttribute('aria-label', 'Tidy this outline: straighten, refine, symmetric, square up');
    ctxTidy.setAttribute('aria-haspopup', 'menu'); ctxTidy.setAttribute('aria-expanded', 'false');
    const tidyMenu = document.createElement('div'); tidyMenu.id = 'cal-tidy-menu'; tidyMenu.setAttribute('role', 'menu');
    tidyMenu.style.cssText = 'position:fixed;z-index:90;display:none;flex-direction:column;gap:6px;padding:8px;'
      + 'background:#0e1418;border:1px solid #29414c;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.55);'
      + 'max-width:min(330px,calc(100vw - 16px));';
    [[ctxStraight, 'Each long side one straight line on the part\'s edge'],
     [ctxRefine, 'Straight sides, and corners rounded as the photo shows'],
     [ctxSnap, 'Follow the photo\'s edge near the dots — drag them close first'],
     [ctxSym, 'Average with its mirror image, for a symmetric part'],
     [ctxSquare, 'Correct camera tilt using the ruler (or the part\'s sides)']].forEach(([b, sub]) => {
      b.classList.add('show'); b.setAttribute('role', 'menuitem');
      b.style.cssText = 'display:flex;flex-direction:column;align-items:flex-start;gap:2px;width:100%;text-align:left;'
        + 'border-radius:10px;padding:9px 12px;min-height:44px;';
      const sm = document.createElement('span'); sm.textContent = sub;
      sm.style.cssText = 'font:500 11.5px Segoe UI,system-ui,sans-serif;opacity:.75;';
      b.appendChild(sm);
      const go = b.onclick; b.onclick = (e) => { closeTidy(); if (go) go(e); };
      tidyMenu.appendChild(b);
    });
    document.body.appendChild(tidyMenu);
    function closeTidy(){ tidyMenu.style.display = 'none'; ctxTidy.setAttribute('aria-expanded', 'false'); }
    ctxTidy.onclick = () => {
      if (tidyMenu.style.display === 'flex') return closeTidy();
      tidyMenu.style.display = 'flex';
      const r = ctxTidy.getBoundingClientRect(), m = tidyMenu.getBoundingClientRect();
      tidyMenu.style.left = Math.max(8, Math.min(window.innerWidth - m.width - 8, r.left)) + 'px';
      tidyMenu.style.top = Math.max(8, r.top - m.height - 8) + 'px';
      ctxTidy.setAttribute('aria-expanded', 'true');
    };
    const onDocClickCloseTidy = (e) => { if (!tidyMenu.contains(e.target) && e.target !== ctxTidy && !ctxTidy.contains(e.target)) closeTidy(); };
    document.addEventListener('click', onDocClickCloseTidy);   // removed in _uiCleanup
    tidyMenu.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeTidy(); ctxTidy.focus(); } });
    // ⋯ More (phone, with an outline selected): Delete, Not the part and Extrude fold into it
    // so the row holds only the way forward — Tidy ▾ and Use in 3D part — in one 40 px row.
    // The menu lists whichever of them would be showing, and runs the real button's action.
    const ctxMore = document.createElement('button'); ctxMore.type = 'button';
    ctxMore.className = 'cal-ctx-more'; ctxMore.id = 'cal-ctx-more';
    // Just the glyph: at 390 px the row has ~306 px beside the zoom buttons, and Tidy ▾ plus
    // Use in 3D part take ~250 of them — "⋯ More" ran half off the end.
    ctxMore.innerHTML = '⋯';
    ctxMore.title = 'More: Delete, Not the part, Extrude';
    ctxMore.setAttribute('aria-label', 'More actions: delete, not the part, extrude');
    ctxMore.setAttribute('aria-haspopup', 'menu'); ctxMore.setAttribute('aria-expanded', 'false');
    const foldable = [ctxDelete, ctxNotPart, ctxDepth];
    const moreMenu = document.createElement('div'); moreMenu.id = 'cal-more-menu'; moreMenu.setAttribute('role', 'menu');
    moreMenu.style.cssText = tidyMenu.style.cssText;
    document.body.appendChild(moreMenu);
    function closeMore(){ moreMenu.style.display = 'none'; ctxMore.setAttribute('aria-expanded', 'false'); }
    ctxMore.onclick = () => {
      if (moreMenu.style.display === 'flex') return closeMore();
      closeTidy();
      moreMenu.textContent = '';
      foldable.filter(b => b.classList.contains('show')).forEach(b => {
        const item = document.createElement('button'); item.type = 'button'; item.setAttribute('role', 'menuitem');
        item.className = b.className.replace(/\b(show|cal-ctx-folded)\b/g, '').trim();
        item.textContent = b.textContent;
        item.style.cssText = 'display:flex;align-items:center;width:100%;text-align:left;border:none;border-radius:10px;'
          + 'padding:9px 12px;min-height:44px;font:700 14px Segoe UI,system-ui,sans-serif;cursor:pointer;';
        item.onclick = (e) => { closeMore(); if (b.onclick) b.onclick(e); reflect(); };
        moreMenu.appendChild(item);
      });
      moreMenu.style.display = 'flex';
      const r = ctxMore.getBoundingClientRect(), m = moreMenu.getBoundingClientRect();
      moreMenu.style.left = Math.max(8, Math.min(window.innerWidth - m.width - 8, r.left)) + 'px';
      moreMenu.style.top = Math.max(8, r.top - m.height - 8) + 'px';
      ctxMore.setAttribute('aria-expanded', 'true');
      const first = moreMenu.querySelector('button'); if (first) setTimeout(() => first.focus(), 0);
    };
    const onDocClickCloseMore = (e) => { if (!moreMenu.contains(e.target) && !ctxMore.contains(e.target)) closeMore(); };
    document.addEventListener('click', onDocClickCloseMore);   // removed in _uiCleanup
    moreMenu.addEventListener('keydown', (e) => { if (e.key === 'Escape') { closeMore(); ctxMore.focus(); } });
    const narrowMq = window.matchMedia ? window.matchMedia('(max-width: 600px)') : null;
    ctx.append(ctxDelete, ctxAuto, ctxBox, ctxHand, ctxNotPart, ctxTidy, ctxDepth, ctx3d, ctxMore, ctxFinish);
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
      const wasShown = !!coach;   // only "spend" the first-run flag if a coach was actually up
      const hadFocus = coach && coach.contains(document.activeElement);
      if (coach && coach.parentNode) coach.remove();
      coach = null;
      document.body.classList.remove('cal-coach-up');
      if (wasShown) {
        coachSeenMem = true;
        try { localStorage.setItem('calib.coachSeen', '1'); } catch (e) {}
      }
      if (hadFocus) { try { calChip.focus(); } catch (e) {} }
    }
    function maybeShowCoach(){
      if (coachSeenMem) return;
      // Simple mode's card already carries step 1 with its own Set scale button: the coach on
      // top of it made two "no scale yet" prompts on the very first photo.
      if (document.body.classList.contains('camscan-simple')) return;
      // A reopened saved side (tagged by the job loader) already has its scale set and the
      // customer's trace in place — the "set the scale, then measure" coach doesn't apply.
      try { if (rootEl && rootEl.getAttribute && rootEl.getAttribute('data-view-index') != null) return; } catch (e) {}
      let seen = false; try { seen = localStorage.getItem('calib.coachSeen') === '1'; } catch (e) {}
      if (seen) return;
      dismissToast();
      coach = document.createElement('div'); coach.className = 'cal-coach';
      document.body.classList.add('cal-coach-up');   // its step 1 says what the banner does
      // Tapping the dimmed backdrop dismisses (so a user who follows "tap the photo" isn't
      // blocked by an inert overlay); taps on the card itself do not.
      coach.addEventListener('click', (e) => { if (e.target === coach) dismissCoach(); });
      const card = document.createElement('div'); card.className = 'cal-coach-card';
      card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true'); card.setAttribute('aria-label', 'Getting started');
      card.innerHTML =
        '<h3>🧩 Photo to 3D part</h3>' +
        '<div class="cal-coach-step"><span class="n">1</span><span><b>Set the scale</b> (📐) along the ruler: tap two numbered marks far apart along its middle and type the distance.</span></div>' +
        '<div class="cal-coach-step"><span class="n">2</span><span><b>Trace the outline</b>: tap the part (if it misses part of it, draw a ▭ box round it first). Use <b>Tidy</b> to straighten it.</span></div>' +
        '<div class="cal-coach-step"><span class="n">3</span><span><b>Use in 3D part</b> (Top, Side or End), then <b>Build 3D part</b> and download it.</span></div>';
      const acts = document.createElement('div'); acts.className = 'cal-coach-actions';
      const setBtn = document.createElement('button'); setBtn.type = 'button'; setBtn.className = 'cal-coach-secondary';
      setBtn.textContent = '📐 Set the scale'; setBtn.onclick = () => openScalePanel(true);
      const goBtn = document.createElement('button'); goBtn.type = 'button'; goBtn.className = 'cal-coach-primary';
      goBtn.textContent = 'Got it'; goBtn.onclick = () => dismissCoach();
      acts.append(setBtn, goBtn); card.appendChild(acts); coach.appendChild(card);
      rootEl.appendChild(coach);
      setTimeout(() => { try { goBtn.focus(); } catch (e) {} }, 0);
    }

    // Deterministic teardown of the document listeners this build() attached (the overlay's
    // destroy() calls this when the #viewer subtree is swapped on a new upload, or another
    // side is opened). Each one left behind kept a dead overlay — photo and all — alive.
    overlay._uiCleanup = () => {
      document.removeEventListener('click', onDocClickCloseSave);
      document.removeEventListener('click', onDocClickCloseTidy);
      document.removeEventListener('click', onDocClickCloseMore);
      document.removeEventListener('camscan-job-changed', reflectPartChip);
    };
    // Let overlay-owned chrome (the KPI "Set a scale" chip) route through the one chooser.
    overlay._openScalePanel = openScalePanel;

    // ---- Keep the UI in sync with engine state ----
    function reflect(){
      const mode = overlay.opts && overlay.opts.mode;
      if (mode === 'setscale' || mode === 'setscalerect' || (overlay.isCalibrated && overlay.isCalibrated())) hideScaleNotice();
      modeBtns.forEach((b, i) => b.setAttribute('aria-pressed', String(mode === modes[i][0])));
      ctxDelete.classList.toggle('show', !!(overlay.ann && overlay.ann.selectedId != null));
      const pathN = overlay.selectedPoints ? overlay.selectedPoints.length : 0;
      ctxFinish.classList.toggle('show', (mode === 'polyline' && pathN >= 2) || (mode === 'outline' && pathN >= 3));
      ctxFinish.innerHTML = mode === 'outline' ? '✓ Close outline' : '✓ Finish path';
      // Trace armed: offer the box and drawing by hand. Drawing by hand: offer auto-trace back.
      const armed = mode === 'outline' && pathN === 0 && !!overlay._autoOutlineArm;
      ctxAuto.classList.toggle('show', mode === 'outline' && pathN === 0 && !armed);
      ctxBox.classList.toggle('show', armed);
      ctxHand.classList.toggle('show', armed);
      const traced = !!(overlay.backgroundTapCount && overlay._lastAutoSeed);
      ctxNotPart.classList.toggle('show', traced);
      if (traced) {
        const n = overlay.backgroundTapCount();
        ctxNotPart.innerHTML = n ? `✖ Not the part (${n})` : '✖ Not the part';
      }
      const prof = overlay.selectedProfile && overlay.selectedProfile();
      ctxDepth.classList.toggle('show', !!prof);
      ctx3d.classList.toggle('show', !!prof);
      ctxTidy.classList.toggle('show', !!prof);
      if (!prof) closeTidy();
      // Keep any 3D view assigned from an outline on this photo in step with it (debounced).
      if (window.CalibPart3D && window.CalibPart3D.syncSoon) window.CalibPart3D.syncSoon();
      if (prof) ctxDepth.innerHTML = prof.depth_mm > 0
        ? (`⇥ ${prof.depth_mm} mm` + (prof.wall_mm > 0 ? ` · ${prof.wall_mm} wall` : ''))
        : '⇥ Extrude';
      // Phone with an outline selected: fold the secondary actions behind ⋯ More.
      const fold = !!(prof && narrowMq && narrowMq.matches);
      foldable.forEach(b => b.classList.toggle('cal-ctx-folded', fold));
      ctxMore.classList.toggle('show', fold && foldable.some(b => b.classList.contains('show')));
      if (!fold) closeMore();
      undo.disabled = !(overlay.canUndo && overlay.canUndo());
      redo.disabled = !(overlay.canRedo && overlay.canRedo());
      reflectCalChip();
    }

    const originalRedraw = typeof overlay.redraw === 'function' ? overlay.redraw.bind(overlay) : function(){};
    overlay.redraw = () => { originalRedraw(); reflect(); };

    reflect();
    maybeShowCoach();

    // If this photo has no usable scale, guide the user straight to the chooser instead of
    // leaving them to notice a warning chip. Skip when the first-run coach is up (it already
    // routes here) or when a detected paper sheet is about to be offered (avoid stacking).
    try {
      const uncal = !(overlay.isCalibrated && overlay.isCalibrated());
      const paperPending = !!(overlay.data && overlay.data.detected_rectangle);
      if (uncal && !paperPending && !coach) {
        setTimeout(() => {
          if (!(overlay.isCalibrated && overlay.isCalibrated()) && !scalePanel) openScalePanel();
        }, 450);
      }
    } catch (e) {}
  }

  return { build };
})();
