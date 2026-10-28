// CamScan — "Simple mode" layer.
//
// Progressive disclosure for a customer-facing intake tool. By DEFAULT the app runs in a
// stripped flow: snap a photo, see at a glance whether auto-detection worked, and add the
// view to the submission — the full measuring rail is hidden. A "Mark up" toggle reveals
// the complete toolset (advanced) for anyone who wants to annotate/measure themselves.
//
// The card leads through the same steps a 3D part needs from each photo: scale, trace, which
// view it is (Top / Side / End), and then Build — the words and the view names are the ones
// 🧩 Use in 3D part and the job panel use, so "Top" means one thing everywhere.
//
// Self-contained: it hides the measuring rail via a body class (the grid's auto tracks
// collapse cleanly — same mechanism as the app's focus mode) and reads the live overlay +
// the CalibJob / CalibPart3D APIs. It never measures or sets scale itself.
(function(){
  'use strict';
  var LS='camscan.simplemode';
  function isSimple(){ try{ var v=localStorage.getItem(LS); return v===null ? true : v==='1'; }catch(e){ return true; } }
  function setSimple(v){ try{ localStorage.setItem(LS, v?'1':'0'); }catch(e){} }
  function view(){ return document.querySelector('.cal-view'); }
  function overlay(){ var el=view(); return el && el.__overlay; }
  var ROLES=['Top','Side','End'];

  // Body-class CSS so a rail that appears AFTER upload is hidden immediately (no flash),
  // and the canvas fits the reclaimed space with no re-fit needed on first load.
  // The job button stays: it is docked in the photo's top bar (calib.job.js), and hiding it
  // left simple mode with no way to the list of photos already kept.
  var css=document.createElement('style');
  css.textContent =
    'body.camscan-simple .cal-rail{display:none !important;}'+
    'body.camscan-simple #cal-kpi{display:none !important;}'+
    '#cal-simple{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:39;'+
      'width:min(560px,calc(100vw - 20px));background:#12171c;border:1px solid #26323b;border-radius:14px;'+
      'box-shadow:0 12px 40px rgba(0,0,0,.55);padding:12px 14px;color:#e8eef2;font:14px Segoe UI,system-ui,sans-serif;box-sizing:border-box;}'+
    '#cal-simple .st{font-weight:600;margin-bottom:9px;line-height:1.35;}'+
    '#cal-simple .note{font-size:12.5px;color:#e6b970;margin:-4px 0 9px;line-height:1.4;}'+
    '#cal-simple .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;}'+
    '#cal-simple button{min-height:44px;border-radius:9px;cursor:pointer;font:600 14px Segoe UI,system-ui,sans-serif;}'+
    '#cal-simple .lbl{padding:8px 13px;background:#151a1f;color:#cfe6ee;border:1px solid #29414c;}'+
    '#cal-simple .lbl.on{background:#173a2a;color:#9ff0c6;border-color:#2f7a55;}'+
    '#cal-simple .prim{padding:9px 16px;background:#00d4ff;color:#04222b;border:none;}'+
    '#cal-simple .ghost{padding:8px 12px;background:transparent;color:#cfe6ee;border:1px solid #2a3a44;}'+
    '#cal-simple .adv{margin-left:auto;background:transparent;border:none;color:#7fb0c4;text-decoration:underline;padding:8px;min-height:40px;}'+
    // Collapsed: one row, so the selected outline's own buttons (Tidy, Use in 3D part) above
    // it stay reachable on a phone — the full card sat on top of them.
    '#cal-simple.mini{padding:6px 8px;}'+
    '#cal-simple.mini .row{flex-wrap:nowrap;gap:6px;}'+
    '#cal-simple.mini .mtx{flex:1 1 auto;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:#9fb2bb;font-size:12.5px;}'+
    '#cal-simple.mini .lbl{padding:6px 10px;}'+
    '#cal-simple-toggle{position:fixed;top:66px;right:12px;z-index:39;min-height:38px;padding:6px 12px;border-radius:18px;'+
      'background:#12171c;color:#cfe6ee;border:1px solid #29414c;cursor:pointer;font:13px Segoe UI,system-ui,sans-serif;display:none;}';
  document.head.appendChild(css);

  var card=null, toggleBtn=null, hint='', _sig=null, _hintTimer=null;
  var _expandedFor=null;   // the outline id the user expanded the collapsed card for

  function ensureToggle(){
    if(toggleBtn) return;
    toggleBtn=document.createElement('button'); toggleBtn.id='cal-simple-toggle'; toggleBtn.textContent='◂ Simple view';
    // Leave the tool in hand first: the poll below drops simple mode while one is (placing()),
    // so from mid-trace or mid-scale the toggle was undone within half a second.
    toggleBtn.onclick=function(){
      var o=overlay();
      if(o && placing(o)){ o._autoOutlineArm=false; try{ if(o.setMode) o.setMode('select'); else o.opts.mode='select'; }catch(e){} }
      setSimple(true); applyMode(true);
    };
    document.body.appendChild(toggleBtn);
  }

  // refit=true when the mode is toggled at runtime (rail shown/hidden after the overlay
  // already laid out) so the canvas re-fits to the new space.
  function applyMode(refit){
    var simple=isSimple(), o=overlay(), v=view();
    document.body.classList.toggle('camscan-simple', simple);
    ensureToggle();
    toggleBtn.style.display = (!simple && !!v) ? 'inline-flex' : 'none';
    if(refit && o && o._onBoxResize){ setTimeout(function(){ try{ o._onBoxResize(); }catch(e){} }, 0); }
    renderCard();
  }

  // Store the photo on screen into the job. CalibJob.captureCurrent does the work (one entry
  // per photo, named after its 3D view; only when something was done to it unless `force`);
  // the fallback is the older rule, for a page where calib.job.js predates it.
  function captureCurrent(label, force){
    var J=window.CalibJob;
    if(!J) return;
    if(typeof J.captureCurrent==='function'){ J.captureCurrent(label||null, !!force); return; }
    // A REOPENED stored view (tracked positionally by data-view-index) is already in the set.
    // hasCurrent() keys on a fresh upload's sourceId and so reads false for it — calling addView
    // here would re-add it under a generic "View N" label, silently relabeling e.g. "Top".
    // Persist its edits in place instead.
    if(J.isCurrentStored && J.isCurrentStored()){ if(J.syncOpen) J.syncOpen(); return; }
    if(J.addView && J.hasCurrent && !J.hasCurrent()){
      var n=(J.count && J.count()) || 0;
      J.addView(label || ('View '+(n+1)));
    }
  }
  // Re-open the uploader (tucked off-screen after the first photo) to add another WITHOUT a
  // page reload — the Dash callback swaps the viewer and the collected set persists.
  function triggerUpload(){
    var inp=document.querySelector('#uploader input[type="file"]');
    if(inp){ try{ inp.value=''; }catch(e){} inp.click(); }
  }

  // The 3D views (Top/Side/End/Cavity) taken from the photo on screen, and all assigned ones.
  function rolesHere(){
    var J=window.CalibJob;
    if(!J || !J.part || !J.photoId) return [];
    var pv=J.part().views||{}, here=J.photoId();
    return ['top','side','end','cavity'].filter(function(r){ return pv[r] && pv[r].source===here; });
  }
  function assignedRoles(){
    var P=window.CalibPart3D;
    if(P && typeof P.assigned==='function'){ try{ return P.assigned()||[]; }catch(e){} }
    var J=window.CalibJob, pv=(J && J.part) ? (J.part().views||{}) : {};
    return ['top','side','end','cavity'].filter(function(r){ return pv[r]; });
  }
  function cap(r){ return r.charAt(0).toUpperCase()+r.slice(1); }
  function hasOutline(o){
    return !!(o.ann && (o.ann.items||[]).some(function(a){ return a.type==='polyline' && a.closed; }));
  }
  function selectedOutline(o){ try{ return o.selectedProfile ? o.selectedProfile() : null; }catch(e){ return null; } }
  function mmText(v){ return (Math.round(v*10)/10)+' mm'; }

  // How THIS photo is scaled, in words that say where the scale came from — and its numbers,
  // so a wrong preset (the Letter length on a 300 mm ruler) shows on the card instead of
  // passing as a success. "Marker found" is said only for a real printed marker: it used to
  // be said for every scale, including a line drawn by hand on a ruler.
  function scaleText(o, si){
    var mmpp=Number(si.mm_per_px)||0;
    var per=mmpp>0 ? Number(mmpp.toPrecision(2))+' mm/px' : '';
    var r=o._manualRef, opts=o.opts||{};
    if(opts.manualHomography){
      if(r && r.kind==='squared'){
        var t=Number(r.tilt_deg);
        return 'Scale set and tilt-corrected (Square up'+(isFinite(t) ? ' '+t.toFixed(1)+'°' : '')+')';
      }
      return 'Scale set from the rectangle'+((r && r.w>0 && r.h>0) ? ' ('+Math.round(r.w)+' × '+Math.round(r.h)+' mm, tilt-corrected)' : ' (tilt-corrected)');
    }
    if(opts.manualMmPerPx){
      var L=o._scaleLine && o._scaleLine.mm;
      return 'Scale set from your ruler line ('+(L>0 ? mmText(L)+(per?', ':'') : '')+per+')';
    }
    var sq=o.currentMarkerSizeMM && o.currentMarkerSizeMM();
    return 'Printed marker found — scale set ('+(sq ? sq+' mm square'+(per?', ':'') : '')+per+')';
  }

  function statusOf(o){
    var cal = o.isCalibrated && o.isCalibrated();
    var si = o.getScaleInfo ? o.getScaleInfo() : null;
    var notes=[];
    if(cal && si){
      var opts=o.opts||{}, squared=!!(o._manualRef && o._manualRef.kind==='squared');
      // Square up reads the tilt with the lens's focal length; a chat-app copy has none, and
      // the server then assumes a typical phone lens. Said once here, where the scale is.
      var cam=o.data && o.data.camera;
      if(opts.manualMmPerPx && !opts.manualHomography && !squared && cam && cam.focal_source && cam.focal_source!=='exif')
        notes.push('This copy of the photo has no camera data (chat apps strip it), so Square up will assume a typical phone lens. The phone’s original photo corrects tilt more exactly.');
      var roles=rolesHere(), next;
      if(roles.length){
        var all=assignedRoles(), missing=ROLES.filter(function(R){ return all.indexOf(R.toLowerCase())<0; });
        next='This photo is the part’s '+roles.map(cap).join(' and ')+' view.'+
          (missing.length ? ' Next: 📷 Add another photo for the '+missing.join(' or ')+' view, or 🧩 Build 3D part.'
                          : ' Next: 🧩 Build 3D part.');
      } else if(!hasOutline(o)){
        next='Next: ✨ Trace outline (box round the part, then tap it).';
      } else if(selectedOutline(o)){
        next='Next: say which view this outline is — Top, Side or End.';
      } else {
        next='Next: tap the outline, then say which view it is — Top, Side or End.';
      }
      return { ok:true, text:'✓ '+scaleText(o, si)+'. '+next, notes:notes };
    }
    // The server's own finding, which its banner carried — and simple mode hides that banner
    // behind this card, so the card says it.
    if(o.hasUnconfirmedDetection && o.hasUnconfirmedDetection())
      notes.push('I saw a square-ish shape but couldn’t confirm it is a printed marker, so it isn’t used.');
    // Assuming the customer HAS a printed card is wrong for most of them — the common case
    // is a tape measure or a ruler already in the shot. Lead with what they are holding.
    return { ok:false, text:'⚠️ No scale yet — measurements are in pixels. Set one from a ruler, tape or '
           + 'anything of known length in the photo (or the 4 corners of a known rectangle — that also '
           + 'corrects camera angle).', notes:notes };
  }

  function mkBtn(cls, text, fn, id){
    var b=document.createElement('button'); b.type='button'; b.className=cls; b.textContent=text;
    if(id) b.id=id;
    b.onclick=fn; return b;
  }
  // Top / Side / End. With an outline selected the tap makes it that view of the part (and
  // keeps the photo); without one it keeps the photo under that name, to trace later.
  function roleChip(R, here, mini){
    var on=here.indexOf(R.toLowerCase())>=0;
    var b=mkBtn('lbl'+(on?' on':''), (on?'✓ ':'')+R, function(){
      var o=overlay(), P=window.CalibPart3D;
      captureCurrent(R, true);
      if(o && selectedOutline(o) && P && typeof P.assign==='function') P.assign(R.toLowerCase());
      _sig=null; renderCard();
    });
    b.setAttribute('data-role', R.toLowerCase());
    if(mini) b.title='This outline is the part’s '+R+' view';
    return b;
  }

  function renderCard(){
    // While this card says "no scale yet" (with its button), the top banner yields to it.
    var o0=overlay();
    document.body.classList.toggle('cal-scale-carried', !!(isSimple() && o0 && o0.img && !statusOf(o0).ok));
    if(!isSimple()){ if(card){ card.remove(); card=null; _sig=null; } return; }
    var o=overlay();
    if(!o || !o.img){ if(card){ card.remove(); card=null; _sig=null; } return; }   // landing / no photo
    var st=statusOf(o);
    var n=(window.CalibJob&&window.CalibJob.count)?window.CalibJob.count():0;
    var here=rolesHere(), all=assignedRoles();
    var sel=selectedOutline(o), outline=hasOutline(o);
    // On a phone, a selected outline's buttons sit just above this card; collapse it to one
    // row until the user asks for the rest.
    var narrow=(window.innerWidth||1000)<=600;
    var mini=!!(narrow && sel && _expandedFor!==sel.id);
    var sig=[st.text, st.notes.join('/'), n, hint, here.join(','), all.join(','), !!sel, outline, mini].join('|');
    if(card && sig===_sig) return;   // nothing shown changed → no rebuild
    _sig=sig;
    if(!card){ card=document.createElement('div'); card.id='cal-simple'; document.body.appendChild(card); }
    card.textContent='';
    card.classList.toggle('mini', mini);
    if(mini){
      var mr=document.createElement('div'); mr.className='row';
      var mt=document.createElement('span'); mt.className='mtx'; mt.textContent='This outline is the part’s…';
      mr.appendChild(mt);
      ROLES.forEach(function(R){ mr.appendChild(roleChip(R, here, true)); });
      var more=mkBtn('ghost', '▴', function(){ _expandedFor=sel.id; _sig=null; renderCard(); }, 'cal-simple-more');
      more.setAttribute('aria-label', 'Show the whole card');
      mr.appendChild(more);
      card.appendChild(mr);
      return;
    }
    var s=document.createElement('div'); s.className='st'; s.style.color = st.ok?'#8fe6b6':'#e6b970'; s.textContent=st.text; card.appendChild(s);
    st.notes.forEach(function(t){ var d=document.createElement('div'); d.className='note'; d.textContent=t; card.appendChild(d); });
    if(!st.ok){
      // The card says what to do, so it offers the button for it — rather than "tap Mark up,
      // then find 📐" — and the top banner, saying the same, steps aside for it.
      var sc=mkBtn('prim', '📐 Set scale', function(){ setSimple(false); applyMode(true);
        setTimeout(function(){ var c=document.getElementById('cal-tb-scale'); if(c) c.click(); }, 0); }, 'cal-simple-scale');
      sc.style.margin='-2px 0 10px';
      card.appendChild(sc);
    }
    if(hint){ var h=document.createElement('div'); h.style.cssText='color:#8fe6b6;font-size:12.5px;margin:-4px 0 9px;'; h.textContent=hint; card.appendChild(h); }
    var r1=document.createElement('div'); r1.className='row';
    var lab=document.createElement('span'); lab.textContent=sel ? 'This outline is the part’s:' : 'This photo is the:';
    lab.style.cssText='color:#9fb2bb;font-size:12.5px;'; r1.appendChild(lab);
    ROLES.forEach(function(R){ r1.appendChild(roleChip(R, here, false)); });
    card.appendChild(r1);
    var r2=document.createElement('div'); r2.className='row'; r2.style.marginTop='9px';
    // One primary at a time, and it is the next step towards the part: Build once a view is
    // assigned, Trace once the scale is set. Submit to Datum is always offered, never leading.
    var buildLead=all.length>0, traceLead=!buildLead && st.ok && !outline;
    var neu=mkBtn('ghost', '📷 Add another photo', function(){
      captureCurrent();     // store this one (when anything was done to it), then pick the next — no reload
      try{ if(window.CalibToast && typeof window.CalibToast.dismiss==='function') window.CalibToast.dismiss(); }catch(e){}
      triggerUpload();
    }, 'cal-simple-add');
    // Drop into markup and arm auto-trace: one tap on the part outlines it (draggable to
    // fix), producing a to-scale, extrudable DXF profile that rides along with the submission.
    var trace=mkBtn(traceLead?'prim':'ghost', '✨ Trace outline', function(){ setSimple(false); applyMode(true); var o=overlay(); if(o){ if(o.armAutoOutline) o.armAutoOutline(); else if(o.setMode) o.setMode('outline'); } }, 'cal-simple-trace');
    r2.appendChild(neu); r2.appendChild(trace);
    if(buildLead && window.CalibPart3D && typeof window.CalibPart3D.open==='function'){
      r2.appendChild(mkBtn('prim', '🧩 Build 3D part ('+all.length+')', function(){ window.CalibPart3D.open(); }, 'cal-simple-build'));
    }
    // An unscaled photo is not added by opening the panel: it carried no millimetres, and
    // landed in the job as "View 1 · no scale". The panel's chips still add it on purpose.
    var sub=mkBtn('ghost', 'Review & Submit'+(n?' ('+n+')':''), function(){
      var o=overlay(); if(o && o.isCalibrated && o.isCalibrated()) captureCurrent();
      if(window.CalibJob) window.CalibJob.open(); }, 'cal-simple-submit');
    var adv=mkBtn('adv', 'Mark up ▸', function(){ setSimple(false); applyMode(true); });
    r2.appendChild(sub); r2.appendChild(adv); card.appendChild(r2);
  }

  // Brief "saved" confirmation when the job changes (the card is the only feedback surface
  // in simple mode, since the submission panel isn't open).
  document.addEventListener('camscan-job-changed', function(){
    var n=(window.CalibJob&&window.CalibJob.count)?window.CalibJob.count():0;
    var built=assignedRoles().length>0;
    hint = n ? ('Saved — '+n+' photo'+(n===1?'':'s')+' kept with this part. Take another with 📷 Add another photo'+
                (built ? ', or 🧩 Build 3D part.' : ', or Review & Submit.')) : '';
    _sig=null; renderCard();
    if(_hintTimer) clearTimeout(_hintTimer);
    _hintTimer=setTimeout(function(){ _hintTimer=null; hint=''; _sig=null; renderCard(); }, 4000);
  });

  // A tool that places points on the photo needs the photo: the card covered its bottom
  // third, so two taps on a ruler lying near the bottom (where it usually lies, under the
  // part) landed on the card and nothing was placed. The card's own Set scale and Trace
  // buttons already left simple mode for this; the top bar's Set scale chip did not. Any
  // mode other than select / pan, or auto-trace armed, now does the same.
  function placing(o){
    var m=o && o.opts && o.opts.mode;
    return !!(o && o.img && ((m && m!=='select' && m!=='pan') || o._autoOutlineArm));
  }

  // Poll to catch the viewer appearing after upload and the scale state changing (e.g. the
  // paper-offer being accepted). Cheap: renderCard is signature-guarded.
  function boot(){
    applyMode(false);
    setInterval(function(){
      var v=view(), o=overlay();
      if(isSimple() && placing(o)){ setSimple(false); applyMode(true); return; }
      if(toggleBtn) toggleBtn.style.display = (!isSimple() && !!v) ? 'inline-flex' : 'none';
      renderCard();
    }, 500);
  }
  if(document.readyState!=='loading') boot(); else document.addEventListener('DOMContentLoaded', boot);
})();
