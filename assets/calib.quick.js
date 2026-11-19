/* Quick measure: photograph a part, read its sizes — nothing else.
 *
 * On a job (a broken knob, a hinge cover, a shelf bracket) the question is "how big is it",
 * and the full tool — trace, tidy, views, 3D — is in the way. Quick measure is one screen:
 * the photo, its scale, and a list of lengths and hole diameters, each with how sure it is
 * ("52.3 ± 0.4 mm", CalibGeom.lengthUncertainty), to copy or read off. The Measure tool
 * stays in hand, so each two taps add a length.
 *
 * Entry: "📏 Quick measure" on the landing card (the next photo opens in it), or
 * window.CalibQuick.enter() with a photo open. ✓ Done goes back to the full tool, with the
 * measurements kept as ordinary annotations.
 */
(function(){
  'use strict';
  var NEXT = 'camscan.quickNext';           // sessionStorage: the next photo opens in Quick measure
  var panel = null, armedKey = null, sig = null, collapsed = false;

  function view(){ return document.querySelector('.cal-view'); }
  function overlay(){ var v = view(); return v && v.__overlay; }
  function isOn(){ return document.body.classList.contains('camscan-quick'); }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
  function toast(msg, ms){ var o = overlay(); try{ if(o && o._toast) o._toast(msg, { autoMs: ms || 2400 }); }catch(e){} }

  var CSS =
    'body.camscan-quick #cal-simple, body.camscan-quick #cal-simple-toggle, body.camscan-quick .cal-rail{display:none !important;}' +
    '#cal-quick{position:fixed;z-index:85;right:10px;top:64px;width:min(330px,calc(100vw - 20px));max-height:calc(100vh - 150px);' +
    'display:flex;flex-direction:column;background:#0f1418;border:1px solid #29414c;border-radius:12px;box-shadow:0 10px 30px rgba(0,0,0,.55);' +
    'font:13px/1.4 Segoe UI,system-ui,sans-serif;color:#dfe9ee;}' +
    '#cal-quick .q-hd{display:flex;align-items:center;gap:6px;padding:8px 8px 6px 12px;border-bottom:1px solid #1e2a31;}' +
    '#cal-quick .q-title{font-weight:700;font-size:14px;flex:1;}' +
    '#cal-quick button{min-height:40px;min-width:40px;border-radius:9px;border:1px solid #29414c;background:#151c21;color:#cfe6ee;' +
    'font:600 13px Segoe UI,system-ui,sans-serif;cursor:pointer;padding:6px 10px;}' +
    '#cal-quick button.prim{background:#00d4ff;color:#000;border-color:#00d4ff;}' +
    '#cal-quick .q-basis{padding:6px 12px;font-size:12px;color:#9fb2bb;}' +
    '#cal-quick .q-basis.warn{color:#f3c969;}' +
    '#cal-quick .q-list{overflow:auto;padding:2px 8px;}' +
    '#cal-quick .q-row{display:flex;align-items:center;gap:6px;padding:4px 4px;border-bottom:1px solid #182228;}' +
    '#cal-quick .q-tag{font-weight:800;color:#00d4ff;min-width:22px;}' +
    '#cal-quick .q-val{flex:1;font-variant-numeric:tabular-nums;font-size:14px;}' +
    '#cal-quick .q-val small{color:#9fb2bb;font-size:12px;}' +
    '#cal-quick .q-row button{min-height:34px;min-width:34px;padding:2px 6px;}' +
    '#cal-quick .q-empty{padding:10px 6px;color:#9fb2bb;font-size:12.5px;}' +
    '#cal-quick .q-acts{display:flex;flex-wrap:wrap;gap:6px;padding:8px;border-top:1px solid #1e2a31;}' +
    '#cal-quick .q-note{padding:0 12px 8px;font-size:11.5px;color:#c9b98f;}' +
    '#cal-quick.collapsed .q-list, #cal-quick.collapsed .q-acts, #cal-quick.collapsed .q-note, #cal-quick.collapsed .q-basis{display:none;}' +
    // A phone: under the photo, where the tool dock (hidden here) was — the photo stays clear.
    '@media (max-width:600px){#cal-quick{left:8px;right:8px;width:auto;top:auto;bottom:calc(8px + env(safe-area-inset-bottom, 0px));max-height:42vh;}' +
    '#cal-quick .q-basis{font-size:11.5px;padding:4px 12px;} #cal-quick .q-note{font-size:11px;}}';

  function ensureCss(){
    if(document.getElementById('cal-quick-css')) return;
    var st = document.createElement('style'); st.id = 'cal-quick-css'; st.textContent = CSS;
    document.head.appendChild(st);
  }

  // Letters for the list and the labels: A..Z, then AA, AB, …
  function letter(i){ var s = ''; i++; while(i > 0){ var m = (i - 1) % 26; s = String.fromCharCode(65 + m) + s; i = Math.floor((i - 1) / 26); } return s; }
  function measurable(o){
    return ((o && o.ann && o.ann.items) || []).filter(function(a){ return a.type === 'segment' || a.type === 'circle'; });
  }
  function tagAll(o){
    var items = measurable(o), used = {};
    items.forEach(function(a){ if(a.qtag) used[a.qtag] = true; });
    var k = 0, added = false;
    items.forEach(function(a){
      if(a.qtag) return;
      while(used[letter(k)]) k++;
      a.qtag = letter(k); used[a.qtag] = true; added = true;
    });
    if(added && o.requestDraw) o.requestDraw();       // the labels carry the letters too
    return items;
  }

  function rows(o){
    return tagAll(o).map(function(a){
      var r = o._readoutFor ? o._readoutFor(a) : null;
      var text = r ? r.text : '';
      // The circle readout adds a note after " • " (tilt): keep the value on the row.
      var parts = String(text).split(' • ');
      return { a: a, tag: a.qtag, kind: a.type === 'circle' ? '⌀' : '', value: parts[0], note: parts.slice(1).join(' • ') };
    });
  }

  function copyText(t){
    var done = function(){ toast('Copied: ' + (t.length > 60 ? t.slice(0, 57) + '…' : t)); };
    try{
      if(navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(t).then(done, fallback); return; }
    }catch(e){}
    fallback();
    function fallback(){
      try{
        var ta = document.createElement('textarea'); ta.value = t; ta.style.cssText = 'position:fixed;left:-9999px;top:0;';
        document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); done();
      }catch(e){ toast('Could not copy — select the text instead.'); }
    }
  }

  function arm(o, mode){
    if(!o || !o.setMode) return;
    try{ o._autoOutlineArm = false; o.setMode(mode); if(o.requestDraw) o.requestDraw(); }catch(e){}
  }

  function build(){
    ensureCss();
    if(panel) return panel;
    panel = document.createElement('div'); panel.id = 'cal-quick';
    panel.setAttribute('role', 'region'); panel.setAttribute('aria-label', 'Quick measure');
    panel.innerHTML =
      '<div class="q-hd"><span class="q-title">📏 Quick measure</span>' +
      '<button type="button" data-q="fold" aria-label="Fold the list" title="Fold the list">▾</button>' +
      '<button type="button" data-q="done" class="prim" aria-label="Done: back to the full tool">✓ Done</button></div>' +
      '<div class="q-basis" id="cal-quick-basis"></div>' +
      '<div class="q-list" id="cal-quick-list"></div>' +
      '<div class="q-note" id="cal-quick-note"></div>' +
      '<div class="q-acts">' +
      '<button type="button" data-q="len" class="prim">＋ Length</button>' +
      '<button type="button" data-q="hole">＋ ⌀ Hole</button>' +
      '<button type="button" data-q="copy">📋 Copy all</button>' +
      '<button type="button" data-q="scale">📐 Scale</button></div>';
    panel.addEventListener('click', onClick);
    document.body.appendChild(panel);
    return panel;
  }

  function onClick(e){
    var b = e.target.closest('button'); if(!b) return;
    var o = overlay(), q = b.getAttribute('data-q');
    if(q === 'done') return exit();
    if(q === 'fold'){ collapsed = !collapsed; panel.classList.toggle('collapsed', collapsed); b.textContent = collapsed ? '▸' : '▾'; return; }
    if(q === 'len'){ arm(o, 'segment'); toast('Tap the two ends of the length — zoom in for precision.'); return; }
    if(q === 'hole'){ arm(o, 'circle3pt'); toast('Tap three points round the hole\'s edge.'); return; }
    if(q === 'scale'){ var s = document.getElementById('cal-tb-scale'); if(s) s.click(); return; }
    if(q === 'copy'){
      var rs = rows(o);
      if(!rs.length){ toast('Nothing measured yet.'); return; }
      copyText(rs.map(function(r){ return r.tag + ': ' + (r.kind ? r.kind + ' ' : '') + r.value; }).join('\n'));
      return;
    }
    var id = b.getAttribute('data-id');
    if(!o || id == null) return;
    var a = o.ann.items.find(function(x){ return String(x.id) === id; });
    if(!a) return;
    if(q === 'copy1'){ var r = rows(o).find(function(x){ return x.a === a; }); if(r) copyText(r.value); return; }
    if(q === 'del'){
      if(o._pushHistory) o._pushHistory();
      o.ann.items = o.ann.items.filter(function(x){ return x !== a; });
      if(o.ann.selectedId === a.id) o.ann.selectedId = null;
      if(o.requestDraw) o.requestDraw();
      sig = null; render();
    }
  }

  function render(){
    var o = overlay();
    if(!panel || !o) return;
    var cal = !!(o.isCalibrated && o.isCalibrated());
    var basis = cal && o.scaleBasis ? o.scaleBasis() : null;
    var rs = rows(o);
    var s = JSON.stringify([cal, basis && basis.text, o.opts && o.opts.units, o.showPm && o.showPm(),
                            rs.map(function(r){ return [r.tag, r.value, r.note]; })]);
    if(s === sig) return;
    sig = s;
    var bEl = panel.querySelector('#cal-quick-basis');
    if(!cal){
      bEl.className = 'q-basis warn';
      bEl.innerHTML = 'No scale yet — tap <b>📐 Scale</b> (or lay the marker card in the shot) so lengths read in ' + esc((o.opts && o.opts.units) || 'mm') + '.';
    }else{
      bEl.className = 'q-basis' + (basis && !basis.perspective ? ' warn' : '');
      bEl.textContent = 'Scale: ' + (basis ? basis.text : 'set');
    }
    var list = panel.querySelector('#cal-quick-list');
    if(!rs.length){
      list.innerHTML = '<div class="q-empty">' + (cal ? 'Tap the two ends of a length on the photo. Zoom in for precision.'
                                                      : 'Set the scale first, then tap two points to measure.') + '</div>';
    }else{
      list.innerHTML = rs.map(function(r){
        return '<div class="q-row" data-qrow="' + esc(r.tag) + '"><span class="q-tag">' + esc(r.tag) + '</span>' +
          '<span class="q-val">' + (r.kind ? esc(r.kind) + ' ' : '') + esc(r.value) + (r.note ? '<br><small>' + esc(r.note) + '</small>' : '') + '</span>' +
          '<button type="button" data-q="copy1" data-id="' + esc(r.a.id) + '" aria-label="Copy ' + esc(r.tag) + '">⧉</button>' +
          '<button type="button" data-q="del" data-id="' + esc(r.a.id) + '" aria-label="Delete ' + esc(r.tag) + '">✕</button></div>';
      }).join('');
    }
    // When to reach for calipers: the reference too small for the lengths, or a ± over 1 %.
    var note = '', w = o.referenceSpanWarning ? o.referenceSpanWarning() : null;
    var worst = 0;
    rs.forEach(function(r){
      if(r.a.type !== 'segment' || !o.lengthPm) return;
      var px = Math.hypot(r.a.b[0] - r.a.a[0], r.a.b[1] - r.a.a[1]);
      var rd = o._readoutFor(r.a), u = (rd && rd.mm > 0) ? o.lengthPm(px, rd.mm) : null;
      if(u && u.relPct > worst) worst = u.relPct;
    });
    if(w) note = w.text;
    else if(worst > 1) note = 'Some lengths are only good to about ' + worst.toFixed(1) + ' % — check those with calipers if the part must fit tightly.';
    panel.querySelector('#cal-quick-note').textContent = note;
  }

  function refit(o){ if(o && o._onBoxResize) setTimeout(function(){ try{ o._onBoxResize(); }catch(e){} }, 0); }

  function enter(){
    var o = overlay();
    if(!o || !o.img){ try{ sessionStorage.setItem(NEXT, '1'); }catch(e){} return false; }
    try{ sessionStorage.removeItem(NEXT); }catch(e){}
    window.__camscanReturnToSimple = null;
    document.body.classList.add('camscan-quick');
    build(); sig = null; armedKey = null; render(); refit(o);
    return true;
  }
  function exit(){
    var o = overlay();
    document.body.classList.remove('camscan-quick');
    if(panel){ panel.remove(); panel = null; }
    sig = null; armedKey = null;
    if(o){ arm(o, 'select'); refit(o); }
  }

  // Once the photo has a scale, the Measure tool is put in hand (again after each new photo
  // or scale), so each two taps add a length.
  function scaleKey(o){
    var op = (o && o.opts) || {};
    return [op.manualMmPerPx || 0, op.manualHomography ? JSON.stringify(op.manualHomography) : '', !!(o && o.isCalibrated && o.isCalibrated())].join('|');
  }
  function tick(){
    var o = overlay();
    var next = false; try{ next = sessionStorage.getItem(NEXT) === '1'; }catch(e){}
    if(next && o && o.img && !isOn()) enter();
    if(!isOn()) return;
    if(!o || !o.img) return;
    if(!panel) build();
    var cal = !!(o.isCalibrated && o.isCalibrated());
    var key = cal ? scaleKey(o) + '#' + (o.img && o.img.src ? o.img.src.length : 0) : null;
    var m = o.opts && o.opts.mode;
    if(key && key !== armedKey && (m === 'select' || m === 'pan') && !document.querySelector('.cal-coach')){
      armedKey = key; arm(o, 'segment');
    }
    render();
  }

  // The landing card: "📏 Quick measure" opens the photo picker, and the photo opens here.
  // A tap on the ordinary upload box clears it (a trusted click — the button's own click on
  // the file input is not one).
  function ensureLanding(){
    var host = document.querySelector('.landing-card');
    if(!host || host.querySelector('#cal-landing-quick')) return;
    var up = document.getElementById('uploader');
    if(!up) return;
    var wrap = document.createElement('div'); wrap.id = 'cal-landing-quick';
    wrap.style.cssText = 'margin-top:12px;display:flex;flex-direction:column;align-items:center;gap:6px;';
    var b = document.createElement('button'); b.type = 'button'; b.id = 'cal-landing-quick-btn';
    b.textContent = '📏 Quick measure — just read a part\'s sizes';
    b.style.cssText = 'min-height:46px;width:100%;padding:10px 14px;border-radius:10px;cursor:pointer;background:#00d4ff;color:#000;border:none;font:700 14px Segoe UI,system-ui,sans-serif;';
    b.onclick = function(){
      try{ sessionStorage.setItem(NEXT, '1'); }catch(e){}
      var inp = up.querySelector('input[type=file]');
      if(inp) inp.click();
    };
    var card = document.createElement('a'); card.id = 'cal-landing-card-link';
    card.href = 'markers/camscan-wallet-card.svg'; card.target = '_blank'; card.rel = 'noopener';
    card.textContent = '🖨 Print the wallet marker card (print at 100%)';
    card.style.cssText = 'font-size:12.5px;color:#7fd8ef;';
    wrap.appendChild(b); wrap.appendChild(card);
    up.parentNode.insertBefore(wrap, up.nextSibling);
    up.addEventListener('click', function(e){ if(e.isTrusted){ try{ sessionStorage.removeItem(NEXT); }catch(x){} } }, true);
  }

  window.CalibQuick = { enter: enter, exit: exit, isOn: isOn };
  function boot(){
    ensureLanding();
    setInterval(function(){ ensureLanding(); tick(); }, 400);
  }
  if(document.readyState !== 'loading') boot(); else document.addEventListener('DOMContentLoaded', boot);
})();
