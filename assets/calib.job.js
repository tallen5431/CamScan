// CamScan — multi-view submission ("job") flow.
//
// Turns the single-photo measuring tool into a customer INTAKE tool: the customer
// captures several labelled views of one part (Top / Front / Side / detail), each measured
// and scaled with the existing tools, then submits the whole set — annotated images,
// measurements, and a short part brief — to Datum Laboratories in one go.
//
// State lives in localStorage so it SURVIVES the page reload between photos (the "New
// image" button reloads the page). This module is self-contained: it adds its own button
// and panel and reads the live overlay from `.cal-view`.__overlay — no coupling to the
// rest of the UI. Nothing here sets a scale or measures; it only collects finished views.
(function(){
  'use strict';
  var LS_KEY = 'camscan.job.v1';
  // The orthographic views that let a modeller reconstruct a part without a 3D scan:
  // top gives the outline + hole pattern, front/side give heights & depths.
  var RECOMMENDED = [
    { label:'Top',   why:'outline + hole pattern (part flat on the sheet)' },
    { label:'Front', why:'height / thickness' },
    { label:'Side',  why:'depth / profile' }
  ];
  var EXTRA_LABELS = ['Detail', 'Other'];

  function apiUrl(path){
    var base='/';
    try{ var cfg=JSON.parse(document.getElementById('_dash-config').textContent); base=cfg.requests_pathname_prefix||'/'; }catch(e){}
    if(base.charAt(base.length-1)!=='/') base+='/';
    return base + path.replace(/^\//,'');
  }
  function overlay(){ var el=document.querySelector('.cal-view'); return el && el.__overlay; }
  function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

  // ---- job state ----
  function load(){
    try{ var j=JSON.parse(localStorage.getItem(LS_KEY)); if(j && j.views) return j; }catch(e){}
    return { id:'job-'+Date.now().toString(36), createdAt:new Date().toISOString(),
             brief:{ part:'', material:'', quantity:'', whatBroke:'', contact:'', notes:'' }, views:[] };
  }
  var job = load();
  function save(){
    try{ localStorage.setItem(LS_KEY, JSON.stringify(job)); return true; }
    catch(e){ // quota — the downscaled images add up. Tell the user to submit/download now.
      setStatus('⚠️ Storage is full — send or download this submission, then start a new one.', true);
      return false; }
  }
  function reset(){ job = { id:'job-'+Date.now().toString(36), createdAt:new Date().toISOString(),
             brief:{ part:'', material:'', quantity:'', whatBroke:'', contact:'', notes:'' }, views:[] };
    try{ localStorage.removeItem(LS_KEY); }catch(e){} }

  // Per-photo identity: the cal-view's data-json URL carries a unique upload id, so we can
  // update-in-place instead of duplicating when the same photo is (re)captured.
  function currentSourceId(){ var el=document.querySelector('.cal-view'); return (el && el.getAttribute('data-json')) || null; }
  function hasCurrent(){ var sid=currentSourceId(); return !!(sid && job.views.some(function(v){ return v.sourceId===sid; })); }

  function addView(label){
    var o=overlay();
    if(!o || !o.img){ setStatus('Load a photo first, then add it.', true); return; }
    var rec;
    try{ rec=o.getViewRecord(label); }catch(e){ setStatus('Could not capture this view: '+e.message, true); return; }
    rec.capturedAt=new Date().toISOString();
    rec.sourceId=currentSourceId();
    var idx = rec.sourceId ? job.views.findIndex(function(v){ return v.sourceId===rec.sourceId; }) : -1;
    if(idx>=0) job.views[idx]=rec; else job.views.push(rec);   // update-or-add: one entry per photo
    save(); changed();
    setStatus('✓ Saved “'+(label||rec.label||'view')+'”. Add another photo, or Send when done.');
  }
  function removeView(i){ job.views.splice(i,1); save(); changed(); }

  // ---- embed / handoff -------------------------------------------------------
  // When CamScan is embedded on the Datum Labs site (iframe URL carries ?embed=1), "Send"
  // hands the collected views to the parent page's quote form via postMessage instead of
  // emailing through /api/submit — so everything lands in ONE intake. A short handshake
  // learns the exact parent origin; if the page never acknowledges (older page, or opened
  // standalone) we fall back to /api/submit so a submission is never lost.
  var EMBED = /[?&]embed=1(?:&|$)/.test(location.search);
  var hostWin = null, hostOrigin = null, handoffAck = false, handoffTimer = null;

  function trustedHost(origin){
    if(!origin) return false;
    if(/^https?:\/\/([a-z0-9-]+\.)*datumlaboratories\.com$/i.test(origin)) return true;
    if(/^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(origin)) return true;   // local dev
    try{ if(document.referrer && new URL(document.referrer).origin===origin) return true; }catch(e){}
    return false;
  }
  window.addEventListener('message', function(e){
    var d=e.data; if(!d || typeof d!=='object') return;
    if(d.type==='camscan:host' && trustedHost(e.origin)){ hostWin=e.source; hostOrigin=e.origin; }
    else if(d.type==='camscan:ack' && (e.origin===hostOrigin || trustedHost(e.origin))){
      handoffAck=true; if(handoffTimer){ clearTimeout(handoffTimer); handoffTimer=null; }
      setStatus('✅ Added to your quote — finish on the page.'); reset(); changed();
      setTimeout(function(){ closePanel(); }, 1400);
    }
  });
  // Announce readiness so the parent posts us its origin even if it missed our iframe load.
  if(EMBED){ try{ (window.parent||window).postMessage({type:'camscan:child-ready', version:1}, '*'); }catch(e){} }

  // ---- UI ----
  var btn, panel, statusEl;
  function ensureButton(){
    if(btn) return;
    btn=document.createElement('button'); btn.type='button'; btn.id='cal-job-btn';
    btn.style.cssText='position:fixed;z-index:38;left:12px;bottom:calc(var(--cal-kpi-h, 46px) + 14px);'+
      'min-height:44px;padding:9px 15px;border-radius:22px;border:none;background:#00d4ff;color:#04222b;'+
      'font:600 14px Segoe UI,system-ui,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.5);cursor:pointer;display:none;';
    btn.onclick=openPanel;
    document.body.appendChild(btn);
  }
  var _btnSig=null;
  function reflectButton(){
    ensureButton();
    var show = !!document.querySelector('.cal-view') || job.views.length>0;
    // Idempotence guard: writing btn.innerHTML is itself a DOM mutation, and this runs
    // from a subtree MutationObserver — so skip the write when nothing shown changed, or
    // the write would re-trigger the observer forever and hang the page.
    var sig = (show?'1':'0')+'|'+job.views.length;
    if(sig===_btnSig) return;
    _btnSig=sig;
    btn.style.display = show ? 'inline-flex' : 'none';
    btn.innerHTML = '📋 Submit to Datum' + (job.views.length ? ' ('+job.views.length+')' : '');
  }

  function chip(label, onClick, primary){
    var b=document.createElement('button'); b.type='button'; b.textContent=label;
    b.style.cssText='min-height:40px;padding:7px 13px;border-radius:8px;cursor:pointer;font-size:13px;'+(primary
      ?'background:#00d4ff;color:#04222b;border:none;font-weight:600;'
      :'background:#151a1f;color:#cfe6ee;border:1px solid #29414c;');
    b.onclick=onClick; return b;
  }
  function field(labelText, key, opts){
    opts=opts||{};
    var wrap=document.createElement('label'); wrap.style.cssText='display:block;margin:8px 0;';
    var lab=document.createElement('div'); lab.textContent=labelText; lab.style.cssText='font-size:12px;color:#9fb2bb;margin-bottom:3px;';
    var input=opts.area?document.createElement('textarea'):document.createElement('input');
    if(!opts.area) input.type=opts.type||'text';
    if(opts.placeholder) input.placeholder=opts.placeholder;
    if(opts.area) input.rows=2;
    input.value=job.brief[key]||'';
    input.style.cssText='width:100%;min-height:'+(opts.area?'52px':'44px')+';padding:9px 10px;background:#0e1216;'+
      'border:1px solid #29414c;border-radius:8px;color:#eef;font:15px Segoe UI,system-ui,sans-serif;box-sizing:border-box;resize:vertical;';
    input.oninput=function(){ job.brief[key]=input.value; save(); };
    wrap.appendChild(lab); wrap.appendChild(input); return wrap;
  }

  function openPanel(){
    closePanel();
    var back=document.createElement('div'); back.id='cal-job-panel';
    back.style.cssText='position:fixed;inset:0;z-index:60;background:rgba(4,6,9,.72);display:flex;'+
      'align-items:flex-start;justify-content:center;overflow:auto;padding:18px;box-sizing:border-box;';
    back.addEventListener('click', function(e){ if(e.target===back) closePanel(); });
    var card=document.createElement('div');
    card.style.cssText='width:100%;max-width:560px;background:#12171c;border:1px solid #263039;border-radius:14px;'+
      'box-shadow:0 20px 60px rgba(0,0,0,.6);padding:18px 18px 20px;color:#e8eef2;font:14px Segoe UI,system-ui,sans-serif;';

    // header
    var head=document.createElement('div'); head.style.cssText='display:flex;justify-content:space-between;align-items:center;gap:10px;';
    var h=document.createElement('div'); h.innerHTML='<div style="font-size:18px;font-weight:700;">Submit part to Datum Laboratories</div>'+
      '<div style="font-size:12.5px;color:#9fb2bb;margin-top:2px;">A few scaled views let us model a replacement without a 3D scan.</div>';
    var x=document.createElement('button'); x.type='button'; x.textContent='✕'; x.setAttribute('aria-label','Close');
    x.style.cssText='min-height:40px;min-width:40px;background:#1b2228;color:#dfe;border:1px solid #2a3a44;border-radius:8px;cursor:pointer;';
    x.onclick=closePanel; head.appendChild(h); head.appendChild(x); card.appendChild(head);

    // add current view
    var o=overlay();
    var addSec=document.createElement('div'); addSec.style.cssText='margin-top:16px;padding:12px;background:#0e1519;border:1px solid #22303a;border-radius:10px;';
    var addTitle=document.createElement('div'); addTitle.style.cssText='font-weight:600;margin-bottom:6px;';
    addTitle.textContent='Add the view on screen as:';
    addSec.appendChild(addTitle);
    if(o && o.img){
      var uncal = !(o.isCalibrated && o.isCalibrated());
      if(uncal){ var warn=document.createElement('div'); warn.textContent='⚠️ No scale set on this view — set one first so measurements are in mm.';
        warn.style.cssText='font-size:12px;color:#e0a24a;margin-bottom:8px;'; addSec.appendChild(warn); }
      var row=document.createElement('div'); row.style.cssText='display:flex;flex-wrap:wrap;gap:7px;';
      RECOMMENDED.concat(EXTRA_LABELS.map(function(l){return {label:l};})).forEach(function(v){
        row.appendChild(chip(v.label, function(){ addView(v.label); }, false));
      });
      addSec.appendChild(row);
    } else {
      var hint=document.createElement('div'); hint.style.cssText='font-size:13px;color:#9fb2bb;';
      hint.textContent='No photo is open. Tap ⬅ New (top-left) to take/upload a view, measure it, then come back here to add it.';
      addSec.appendChild(hint);
    }
    card.appendChild(addSec);

    // guided shot list
    var guide=document.createElement('div'); guide.style.cssText='margin-top:14px;';
    var gt=document.createElement('div'); gt.style.cssText='font-weight:600;margin-bottom:6px;'; gt.textContent='Suggested shots';
    guide.appendChild(gt);
    RECOMMENDED.forEach(function(v){
      var have=job.views.some(function(w){ return (w.label||'').toLowerCase()===v.label.toLowerCase(); });
      var r=document.createElement('div'); r.style.cssText='font-size:13px;color:'+(have?'#7fe0b0':'#9fb2bb')+';margin:2px 0;';
      r.innerHTML=(have?'✓ ':'○ ')+'<b>'+esc(v.label)+'</b> — '+esc(v.why);
      guide.appendChild(r);
    });
    var tip=document.createElement('div'); tip.style.cssText='font-size:12px;color:#7f8f98;margin-top:6px;';
    tip.textContent='Keep the same reference (card/marker) flat in every shot, and measure features that sit flat on it.';
    guide.appendChild(tip);
    card.appendChild(guide);

    // collected views
    var vt=document.createElement('div'); vt.style.cssText='font-weight:600;margin:16px 0 6px;';
    vt.textContent='Collected views ('+job.views.length+')';
    card.appendChild(vt);
    if(!job.views.length){
      var none=document.createElement('div'); none.style.cssText='font-size:13px;color:#9fb2bb;'; none.textContent='Nothing added yet.';
      card.appendChild(none);
    } else {
      job.views.forEach(function(v,i){
        var vr=document.createElement('div'); vr.style.cssText='display:flex;gap:10px;align-items:center;padding:8px;border:1px solid #22303a;border-radius:9px;margin-bottom:7px;';
        var im=document.createElement('img'); im.src=v.image||''; im.alt=v.label;
        im.style.cssText='width:54px;height:54px;object-fit:cover;border-radius:6px;background:#0a0e11;flex:0 0 auto;';
        var meta=document.createElement('div'); meta.style.cssText='flex:1 1 auto;min-width:0;';
        var mm=(v.measurements||[]).length;
        var sc=v.scale&&v.scale.calibrated ? (v.scale.source||'scaled')+(v.scale.perspective?' · tilt-corrected':'') : 'no scale';
        meta.innerHTML='<div style="font-weight:600;">'+esc(v.label||'View '+(i+1))+'</div>'+
          '<div style="font-size:12px;color:#9fb2bb;">'+mm+' measurement'+(mm===1?'':'s')+' · '+esc(sc)+'</div>';
        var btns=document.createElement('div'); btns.style.cssText='display:flex;gap:6px;flex:0 0 auto;';
        if(v.restore && v.restore.raw){
          var open=chip('Open', function(){ openViewForEditing(i); }, false);
          open.style.minHeight='36px'; open.style.padding='5px 10px'; open.title='Re-open this side in CamScan to model it';
          btns.appendChild(open);
        }
        var del=chip('Remove', function(){ removeView(i); openPanel(); }, false);
        del.style.minHeight='36px'; del.style.padding='5px 10px';
        btns.appendChild(del);
        vr.appendChild(im); vr.appendChild(meta); vr.appendChild(btns); card.appendChild(vr);
      });
    }

    // brief
    var bt=document.createElement('div'); bt.style.cssText='font-weight:600;margin:16px 0 4px;'; bt.textContent='About the part';
    card.appendChild(bt);
    card.appendChild(field('Part name / number', 'part', {placeholder:'e.g. gearbox cover bracket'}));
    var two=document.createElement('div'); two.style.cssText='display:flex;gap:10px;flex-wrap:wrap;';
    var f1=field('Material', 'material', {placeholder:'e.g. ABS, aluminium'}); f1.style.flex='1 1 160px';
    var f2=field('Quantity', 'quantity', {type:'number', placeholder:'1'}); f2.style.flex='1 1 90px';
    two.appendChild(f1); two.appendChild(f2); card.appendChild(two);
    card.appendChild(field('What broke / what you need', 'whatBroke', {area:true, placeholder:'e.g. mounting tab snapped off; need an exact replacement'}));
    card.appendChild(field(EMBED ? 'Your email (optional — the quote form will ask)' : 'Your email (so we can reply)', 'contact', {type:'email', placeholder:'you@example.com'}));
    card.appendChild(field('Anything else', 'notes', {area:true, placeholder:'tolerances, finish, deadline…'}));

    if(EMBED){
      var eh=document.createElement('div'); eh.style.cssText='font-size:12.5px;color:#7fb0c4;margin-top:10px;';
      eh.textContent='These measured views will drop straight into the quote form on the page — you’ll add your details and send there.';
      card.appendChild(eh);
    }

    // actions
    var actions=document.createElement('div'); actions.style.cssText='display:flex;flex-wrap:wrap;gap:9px;margin-top:16px;align-items:center;';
    var send=chip(EMBED ? 'Add to quote →' : 'Send to Datum', submit, true); send.style.minHeight='46px'; send.style.padding='10px 20px'; send.style.fontSize='15px';
    var save=chip('💾 Save job', saveJobToFile, false); save.style.minHeight='46px'; save.title='Save every side to a .camscan.json you can re-open here later';
    var load=chip('⤓ Load job', pickJobFile, false); load.style.minHeight='46px'; load.title='Open a saved .camscan.json and re-edit each side';
    var dl=chip('Download summary', downloadSummary, false); dl.style.minHeight='46px';
    var clr=chip('Clear', function(){ if(confirm('Discard this submission and all collected views?')){ reset(); changed(); openPanel(); } }, false);
    clr.style.minHeight='46px'; clr.style.marginLeft='auto';
    actions.appendChild(send); actions.appendChild(save); actions.appendChild(load); actions.appendChild(dl); actions.appendChild(clr);
    card.appendChild(actions);

    statusEl=document.createElement('div'); statusEl.id='cal-job-status';
    statusEl.style.cssText='margin-top:10px;font-size:13px;min-height:18px;color:#9fe6c2;';
    card.appendChild(statusEl);

    back.appendChild(card); document.body.appendChild(back);
  }
  function closePanel(){ var p=document.getElementById('cal-job-panel'); if(p) p.remove(); statusEl=null; }
  function setStatus(msg, warn){
    if(statusEl){ statusEl.textContent=msg; statusEl.style.color=warn?'#e0a24a':'#9fe6c2'; }
  }

  function validate(){
    if(!job.views.length){ setStatus('Add at least one view before sending.', true); return false; }
    // Embedded: the page's quote form collects (and requires) the email, so don't ask twice.
    if(!EMBED){
      var email=(job.brief.contact||'').trim();
      if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)){ setStatus('Please add your email so we can reply.', true); return false; }
    }
    return true;
  }

  // Generate a DXF for a view from its captured geometry (mm, CAD Y-up), client-side.
  function _dxfFor(v){
    try{
      if(v.geometry && v.geometry.geometry && window.CalibExport && window.CalibExport.dxfFromGeometry)
        return window.CalibExport.dxfFromGeometry(v.geometry.geometry, v.geometry.mm_per_px, v.geometry.image_height);
    }catch(e){}
    return null;
  }
  // Enrich each view with the CAD-ready payload (geometry + CSV dimension table + a DXF)
  // so both the handoff and the server submission carry model-ready data, not just an image.
  function exportViews(withCapturedAt){
    return job.views.map(function(v){
      var o={ label:v.label, image:v.image, scale:v.scale, measurements:v.measurements, units:v.units,
              geometry:(v.geometry||null), csv:(v.csv||null), dxf:_dxfFor(v),
              // The reloadable snapshot (raw image + calibration + editable annotations) so
              // the receiver can re-open this side in CamScan and model it, not just re-measure.
              restore:(v.restore||null) };
      if(withCapturedAt) o.capturedAt=v.capturedAt;
      return o;
    });
  }

  // ---- Save / Load a whole job as a portable .camscan.json bundle -------------------
  // Save writes every collected view's reloadable snapshot to one file; Load reads it back
  // and rehydrates the set, so a submitted part can be re-opened in CamScan with each side
  // editable (scale + the customer's trace restored) instead of re-measured from scratch.
  function saveJobToFile(){
    if(!job.views.length){ setStatus('Add at least one view before saving.', true); return; }
    var bundle={ kind:'camscan.job', version:1, id:job.id, createdAt:job.createdAt,
                 savedAt:new Date().toISOString(), brief:job.brief, views:exportViews(true) };
    var blob=new Blob([JSON.stringify(bundle)], {type:'application/json'});
    var name='camscan-job-'+((job.brief.part||job.id||'part').replace(/[^a-z0-9]+/gi,'-').replace(/^-+|-+$/g,'')||'part')+'.camscan.json';
    var a=document.createElement('a'); a.download=name; a.href=URL.createObjectURL(blob); a.click();
    setTimeout(function(){ URL.revokeObjectURL(a.href); }, 1500);
    setStatus('Saved '+job.views.length+' view'+(job.views.length===1?'':'s')+' to '+name+' — load it back any time.');
  }

  function loadJobFromFile(file){
    if(!file){ return; }
    var reader=new FileReader();
    reader.onload=function(){
      var bundle;
      try{ bundle=JSON.parse(reader.result); }catch(e){ setStatus('That file is not a valid CamScan job.', true); return; }
      if(!bundle || bundle.kind!=='camscan.job' || !Array.isArray(bundle.views)){
        setStatus('That is not a CamScan job bundle (.camscan.json).', true); return;
      }
      job={ id:bundle.id||('job-'+Date.now().toString(36)),
            createdAt:bundle.createdAt||new Date().toISOString(),
            brief:Object.assign({ part:'', material:'', quantity:'', whatBroke:'', contact:'', notes:'' }, bundle.brief||{}),
            views:bundle.views.slice() };
      save();   // best-effort; if the bundle is big this may hit quota — the job still works in memory
      changed();
      openPanel();
      var reloadable=job.views.filter(function(v){ return v.restore&&v.restore.raw; }).length;
      setStatus('Loaded '+job.views.length+' view'+(job.views.length===1?'':'s')+'. '+
        (reloadable?('Tap “Open” on a view to edit it.'):'These views have no reloadable image data.'), !reloadable);
    };
    reader.readAsText(file);
  }

  // A single hidden file input drives every "Load job" entry point.
  var _loadInput=null;
  function pickJobFile(){
    if(!_loadInput){
      _loadInput=document.createElement('input'); _loadInput.type='file';
      _loadInput.accept='.json,.camscan.json,application/json'; _loadInput.style.display='none';
      _loadInput.addEventListener('change', function(){ var f=_loadInput.files&&_loadInput.files[0]; _loadInput.value=''; if(f) loadJobFromFile(f); });
      document.body.appendChild(_loadInput);
    }
    _loadInput.click();
  }

  // Re-open a saved view as an editable overlay WITHOUT a Dash reload: build the same
  // .cal-view the upload callback makes (raw image + calibration), stash the annotations on
  // it, and let the overlay loader (initScan) boot on it. The overlay applies __restore once
  // the image + calibration have loaded. This is the "model each side" surface.
  function openViewForEditing(i){
    var v=job.views[i];
    if(!v || !v.restore || !v.restore.raw){ setStatus('This view has no reloadable image data.', true); return; }
    var st=v.restore;
    var viewer=document.getElementById('viewer');
    if(!viewer){ setStatus('Viewer is not ready — reload the page and try again.', true); return; }
    // Modelling wants the full markup rail, not the stripped intake flow.
    try{ localStorage.setItem('camscan.simplemode','0'); }catch(e){}
    document.body.classList.remove('camscan-simple');

    var calibUrl='data:application/json;charset=utf-8,'+encodeURIComponent(JSON.stringify(st.calib||{markers:[]}));
    var wrap=document.createElement('div'); wrap.className='cal-wrap';
    var view=document.createElement('div'); view.className='cal-view'; view.id='cal-view';
    view.setAttribute('data-img', st.raw);
    view.setAttribute('data-json', calibUrl);
    view.setAttribute('style','text-align:center;');
    view.__restore={ ann:(st.ann||[]), units:(st.units||'mm'), manual:(st.manual||null) };
    var canvas=document.createElement('canvas'); canvas.id='cal-canvas';
    canvas.style.display='block'; canvas.style.margin='0 auto';
    view.appendChild(canvas); wrap.appendChild(view);
    viewer.textContent=''; viewer.appendChild(wrap);   // loader destroys the old overlay + boots this one

    // Tuck the landing/upload card off-screen (same treatment the upload callback uses).
    var tp=document.getElementById('top-panel');
    if(tp){ tp.style.cssText='position:fixed;left:-9999px;top:0;width:1px;height:1px;overflow:hidden;opacity:0;'; }
    closePanel();
    setStatus('Opened “'+(v.label||('View '+(i+1)))+'” for editing.');
  }

  function submit(){
    if(!validate()) return;
    if(EMBED){ handoff(); return; }
    sendToServer();
  }

  // Embedded path: hand the collected views to the parent page's quote form. If the page
  // doesn't acknowledge within a short window, fall back to our own /api/submit pipeline.
  function handoff(){
    setStatus('Adding to your quote…');
    var msg={ type:'camscan:submit', version:1, brief:job.brief, views:exportViews(false) };
    var win = hostWin || window.parent || window;
    var origin = hostOrigin;
    if(!origin){ try{ origin = document.referrer ? new URL(document.referrer).origin : '*'; }catch(e){ origin='*'; } }
    handoffAck=false;
    try{ win.postMessage(msg, origin); }catch(e){ try{ win.postMessage(msg, '*'); }catch(_){} }
    if(handoffTimer) clearTimeout(handoffTimer);
    var wait = (typeof window.__CAMSCAN_HANDOFF_TIMEOUT==='number') ? window.__CAMSCAN_HANDOFF_TIMEOUT : 2200;
    handoffTimer=setTimeout(function(){ if(!handoffAck) sendToServer(); }, wait);
  }

  function sendToServer(){
    setStatus('Sending…');
    var payload={ id:job.id, createdAt:job.createdAt, submittedAt:new Date().toISOString(),
      brief:job.brief, views:exportViews(true), userAgent:navigator.userAgent };
    fetch(apiUrl('api/submit'), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) })
      .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, j:j}; }); })
      .then(function(res){
        if(res.ok && res.j && res.j.ok){
          setStatus('✅ '+(res.j.message||'Sent to Datum Laboratories. We\'ll be in touch.'));
          reset(); changed();
          setTimeout(function(){ closePanel(); }, 2200);
        } else {
          setStatus('⚠️ '+((res.j&&res.j.error)||'Could not send. Try “Download summary” and email it to us.'), true);
        }
      })
      .catch(function(e){ setStatus('⚠️ Network error — try “Download summary” and email it to us. ('+e.message+')', true); });
  }

  // A self-contained HTML summary the customer can save/print or email to us if the
  // server send is unavailable — same content the emailed packet carries.
  function downloadSummary(){
    if(!job.views.length){ setStatus('Add at least one view first.', true); return; }
    var b=job.brief;
    var html='<!doctype html><meta charset="utf-8"><title>CamScan submission — '+esc(b.part||job.id)+'</title>'+
      '<body style="font:14px/1.5 system-ui,Segoe UI,sans-serif;color:#111;max-width:820px;margin:24px auto;padding:0 16px;">'+
      '<h1 style="font-size:20px;">Part submission — Datum Laboratories</h1>'+
      '<table style="border-collapse:collapse;">'+
      row('Part', b.part)+row('Material', b.material)+row('Quantity', b.quantity)+
      row('What broke / needed', b.whatBroke)+row('Contact', b.contact)+row('Notes', b.notes)+
      row('Submitted', new Date().toLocaleString())+'</table>';
    job.views.forEach(function(v,i){
      html+='<h2 style="font-size:16px;margin-top:22px;">'+esc(v.label||('View '+(i+1)))+'</h2>';
      var sc=v.scale&&v.scale.calibrated ? esc((v.scale.source||'scaled'))+(v.scale.perspective?' (tilt-corrected)':'') : 'no scale set';
      html+='<div style="color:#555;">Scale: '+sc+'</div>';
      if(v.image) html+='<img src="'+v.image+'" style="max-width:100%;border:1px solid #ccc;margin:8px 0;">';
      if((v.measurements||[]).length){ html+='<ul>'; v.measurements.forEach(function(m){ html+='<li>'+esc(m.label)+': '+esc(m.text)+'</li>'; }); html+='</ul>'; }
    });
    html+='</body>';
    var blob=new Blob([html], {type:'text/html'});
    var a=document.createElement('a'); a.download='datum-submission-'+(b.part?b.part.replace(/[^a-z0-9]+/gi,'-'):job.id)+'.html';
    a.href=URL.createObjectURL(blob); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); }, 1500);
    setStatus('Downloaded a summary you can email us if needed.');
  }
  function row(k,v){ return '<tr><td style="padding:3px 12px 3px 0;color:#555;vertical-align:top;">'+esc(k)+'</td><td style="padding:3px 0;">'+esc(v||'—')+'</td></tr>'; }

  // Put a "Load a saved job" entry on the landing card, so a modeller can re-open a
  // submitted part before any photo is on screen (the submit button only appears once a
  // view exists). Injected once; the guard also stops the subtree observer from looping.
  function ensureLandingLoad(){
    var host=document.querySelector('.landing-card');
    if(!host || host.querySelector('#cal-landing-load')) return;
    var wrap=document.createElement('div'); wrap.id='cal-landing-load';
    wrap.style.cssText='margin-top:14px;padding-top:14px;border-top:1px solid #26262b;text-align:center;';
    var b=document.createElement('button'); b.type='button'; b.textContent='⤓ Load a saved job (.camscan.json)';
    b.style.cssText='min-height:44px;padding:9px 14px;border-radius:9px;cursor:pointer;background:#151a1f;color:#cfe6ee;border:1px solid #29414c;font:600 13px Segoe UI,system-ui,sans-serif;';
    b.onclick=pickJobFile;
    var hint=document.createElement('div'); hint.textContent='Re-open a submitted part to model each side.';
    hint.style.cssText='font-size:12px;color:#76767e;margin-top:7px;';
    wrap.appendChild(b); wrap.appendChild(hint); host.appendChild(wrap);
  }

  function render(){ reflectButton(); ensureLandingLoad(); }
  // Refresh the button AND notify other layers (the simple-mode card) that the job changed.
  function changed(){ reflectButton(); try{ document.dispatchEvent(new CustomEvent('camscan-job-changed')); }catch(e){} }

  // Minimal API so the simple-mode layer can drive submission without duplicating logic.
  window.CalibJob = {
    addView: addView,
    open: openPanel,
    submit: submit,          // send/hand-off the collected set (used by simple mode + tests)
    count: function(){ return job.views.length; },
    isEmpty: function(){ return !job.views.length; },
    hasCurrent: hasCurrent,  // is the photo on screen already captured into the set?
    isEmbedded: function(){ return EMBED; }
  };

  // Boot: add the button, keep its state in sync as the viewer appears/disappears.
  function boot(){
    reflectButton(); ensureLandingLoad();
    new MutationObserver(function(){ reflectButton(); ensureLandingLoad(); }).observe(document.documentElement, {childList:true, subtree:true});
  }
  if(document.readyState!=='loading') boot(); else document.addEventListener('DOMContentLoaded', boot);
})();
