// CamScan — "Simple mode" layer.
//
// Progressive disclosure for a customer-facing intake tool. By DEFAULT the app runs in a
// stripped flow: snap a photo, see at a glance whether auto-detection worked, and add the
// view to the submission — the full measuring rail is hidden. A "Mark up" toggle reveals
// the complete toolset (advanced) for anyone who wants to annotate/measure themselves.
//
// Self-contained: it hides the measuring rail via a body class (the grid's auto tracks
// collapse cleanly — same mechanism as the app's focus mode) and reads the live overlay +
// the CalibJob API. It never measures or sets scale itself.
(function(){
  'use strict';
  var LS='camscan.simplemode';
  function isSimple(){ try{ var v=localStorage.getItem(LS); return v===null ? true : v==='1'; }catch(e){ return true; } }
  function setSimple(v){ try{ localStorage.setItem(LS, v?'1':'0'); }catch(e){} }
  function view(){ return document.querySelector('.cal-view'); }
  function overlay(){ var el=view(); return el && el.__overlay; }

  // Body-class CSS so a rail that appears AFTER upload is hidden immediately (no flash),
  // and the canvas fits the reclaimed space with no re-fit needed on first load.
  var css=document.createElement('style');
  css.textContent =
    'body.camscan-simple .cal-rail{display:none !important;}'+
    'body.camscan-simple #cal-kpi{display:none !important;}'+
    'body.camscan-simple #cal-job-btn{display:none !important;}'+
    '#cal-simple{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);z-index:39;'+
      'width:min(560px,calc(100vw - 20px));background:#12171c;border:1px solid #26323b;border-radius:14px;'+
      'box-shadow:0 12px 40px rgba(0,0,0,.55);padding:12px 14px;color:#e8eef2;font:14px Segoe UI,system-ui,sans-serif;box-sizing:border-box;}'+
    '#cal-simple .st{font-weight:600;margin-bottom:9px;line-height:1.35;}'+
    '#cal-simple .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;}'+
    '#cal-simple button{min-height:44px;border-radius:9px;cursor:pointer;font:600 14px Segoe UI,system-ui,sans-serif;}'+
    '#cal-simple .lbl{padding:8px 13px;background:#151a1f;color:#cfe6ee;border:1px solid #29414c;}'+
    '#cal-simple .prim{padding:9px 16px;background:#00d4ff;color:#04222b;border:none;}'+
    '#cal-simple .ghost{padding:8px 12px;background:transparent;color:#cfe6ee;border:1px solid #2a3a44;}'+
    '#cal-simple .adv{margin-left:auto;background:transparent;border:none;color:#7fb0c4;text-decoration:underline;padding:8px;min-height:40px;}'+
    '#cal-simple-toggle{position:fixed;top:66px;right:12px;z-index:39;min-height:38px;padding:6px 12px;border-radius:18px;'+
      'background:#12171c;color:#cfe6ee;border:1px solid #29414c;cursor:pointer;font:13px Segoe UI,system-ui,sans-serif;display:none;}';
  document.head.appendChild(css);

  var card=null, toggleBtn=null, hint='', _sig=null;

  function ensureToggle(){
    if(toggleBtn) return;
    toggleBtn=document.createElement('button'); toggleBtn.id='cal-simple-toggle'; toggleBtn.textContent='◂ Simple view';
    toggleBtn.onclick=function(){ setSimple(true); applyMode(true); };
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

  function statusOf(o){
    var cal = o.isCalibrated && o.isCalibrated();
    var si = o.getScaleInfo ? o.getScaleInfo() : null;
    if(cal && si){
      return { ok:true, text:'✓ Marker found — scale set ('+ (si.source||'scaled') + (si.perspective?', tilt-corrected':'') +'). Ready to add this view.' };
    }
    return { ok:false, text:'⚠️ No marker detected. Retake with the printed card flat and fully in frame — or tap “Mark up” to set the scale by hand.' };
  }

  function renderCard(){
    if(!isSimple()){ if(card){ card.remove(); card=null; _sig=null; } return; }
    var o=overlay();
    if(!o || !o.img){ if(card){ card.remove(); card=null; _sig=null; } return; }   // landing / no photo
    var st=statusOf(o);
    var n=(window.CalibJob&&window.CalibJob.count)?window.CalibJob.count():0;
    var sig=st.text+'|'+n+'|'+hint;
    if(card && sig===_sig) return;   // nothing shown changed → no rebuild
    _sig=sig;
    if(!card){ card=document.createElement('div'); card.id='cal-simple'; document.body.appendChild(card); }
    card.textContent='';
    var s=document.createElement('div'); s.className='st'; s.style.color = st.ok?'#8fe6b6':'#e6b970'; s.textContent=st.text; card.appendChild(s);
    if(hint){ var h=document.createElement('div'); h.style.cssText='color:#8fe6b6;font-size:12.5px;margin:-4px 0 9px;'; h.textContent=hint; card.appendChild(h); }
    var r1=document.createElement('div'); r1.className='row';
    var lab=document.createElement('span'); lab.textContent='Add view:'; lab.style.cssText='color:#9fb2bb;font-size:12.5px;'; r1.appendChild(lab);
    ['Top','Front','Side','Detail'].forEach(function(L){
      var b=document.createElement('button'); b.className='lbl'; b.textContent=L;
      b.onclick=function(){ if(window.CalibJob) window.CalibJob.addView(L); };
      r1.appendChild(b);
    });
    card.appendChild(r1);
    var r2=document.createElement('div'); r2.className='row'; r2.style.marginTop='9px';
    var neu=document.createElement('button'); neu.className='ghost'; neu.textContent='📷 New photo';
    neu.onclick=function(){ try{ location.reload(); }catch(e){} };
    var sub=document.createElement('button'); sub.className='prim'; sub.textContent='Review & Submit'+(n?' ('+n+')':'');
    sub.onclick=function(){ if(window.CalibJob) window.CalibJob.open(); };
    var adv=document.createElement('button'); adv.className='adv'; adv.textContent='Mark up ▸';
    adv.onclick=function(){ setSimple(false); applyMode(true); };
    r2.appendChild(neu); r2.appendChild(sub); r2.appendChild(adv); card.appendChild(r2);
  }

  // Brief "saved" confirmation when a view is added (the card is the only feedback surface
  // in simple mode, since the submission panel isn't open).
  document.addEventListener('camscan-job-changed', function(){
    var n=(window.CalibJob&&window.CalibJob.count)?window.CalibJob.count():0;
    hint = n ? ('Saved — '+n+' view'+(n===1?'':'s')+' collected. Take another with 📷 New photo, or Review & Submit.') : '';
    _sig=null; renderCard();
    setTimeout(function(){ hint=''; _sig=null; renderCard(); }, 4000);
  });

  // Poll to catch the viewer appearing after upload and the scale state changing (e.g. the
  // paper-offer being accepted). Cheap: renderCard is signature-guarded.
  function boot(){
    applyMode(false);
    setInterval(function(){
      var v=view();
      if(toggleBtn) toggleBtn.style.display = (!isSimple() && !!v) ? 'inline-flex' : 'none';
      renderCard();
    }, 500);
  }
  if(document.readyState!=='loading') boot(); else document.addEventListener('DOMContentLoaded', boot);
})();
