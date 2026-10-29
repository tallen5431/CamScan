// CamScan — multi-view submission ("job") flow.
//
// Turns the single-photo measuring tool into a customer INTAKE tool: the customer
// captures several labelled views of one part (Top / Side / End / detail), each measured
// and scaled with the existing tools, then submits the whole set — annotated images,
// measurements, and a short part brief — to Datum Laboratories in one go.
//
// State lives in IndexedDB (localStorage where that is unavailable) so it SURVIVES the page
// reload between photos (the "New image" button reloads the page) — see "job state" below. This module is self-contained: it adds its own button
// and panel and reads the live overlay from `.cal-view`.__overlay — no coupling to the
// rest of the UI. Nothing here sets a scale or measures; it only collects finished views.
(function(){
  'use strict';
  var LS_KEY = 'camscan.job.v1';
  // The orthographic views that let a modeller reconstruct a part without a 3D scan. They are
  // the same three views 🧩 Use in 3D part assigns (calib.part3d.js), so "Top" means one thing
  // everywhere: this list once said Top / Front / Side while the part wanted Top / Side / End,
  // and the end view — the one that gives a cover its rounded edges — was never asked for.
  // Each says where the ruler goes, because a ruler held under or beside the face it is meant
  // to scale reads that face several percent wrong (see CAPTURE_RULES #1).
  var RECOMMENDED = [
    // A hollow cover seen from above shows its lower outer edge, not its top face: with the
    // ruler on top that edge reads small (45 mm below it: 7.4% small, README).
    { label:'Top',  why:'from above, ruler on the top face (length × width). A hollow cover’s outline is its bottom edge: put the ruler level with that, or enter its height as a negative 📐 raised face' },
    { label:'Side', why:'square-on to the side, ruler against it (length × height)' },
    { label:'End',  why:'square-on to the end, ruler against the end face (width × height)' }
  ];
  var EXTRA_LABELS = ['Detail', 'Other'];
  var ROLE_LABEL = { top:'Top', side:'Side', end:'End', cavity:'Cavity' };

  function apiUrl(path){
    var base='/';
    try{ var cfg=JSON.parse(document.getElementById('_dash-config').textContent); base=cfg.requests_pathname_prefix||'/'; }catch(e){}
    if(base.charAt(base.length-1)!=='/') base+='/';
    return base + path.replace(/^\//,'');
  }
  function overlay(){ var el=document.querySelector('.cal-view'); return el && el.__overlay; }
  function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }

  // ---- job state ----
  //
  // WHERE IT LIVES. The job used to live in localStorage, which browsers cap at about 5 MB.
  // Each view stores two images (annotated + raw, ~0.5 MB each at 1600 px — measured on the
  // customer's own photos), so a job filled up at about FIVE photos — fewer than the six that
  // customer sent, and fewer than a 3D part needs (top, side, end, board, underside). It now
  // lives in IndexedDB, whose quota is a share of the disk, not a few megabytes.
  //
  // localStorage remains the fallback where IndexedDB is unavailable or broken (some private
  // modes), and the source a job saved by an older version is migrated from: its first save
  // here writes IndexedDB and then drops the old key, so a stale copy can never shadow it.
  //
  // The catch is timing: an IndexedDB write finishes later, where a localStorage write was
  // done before the call returned. Nothing may rely on a write started while the page is
  // unloading, so the reload buttons wait for the write (beforeReload) and a reopened side's
  // edits are saved shortly after they happen (autosave), not only at unload.
  function blankJob(){
    return { id:'job-'+Date.now().toString(36), createdAt:new Date().toISOString(),
             brief:{ part:'', material:'', quantity:'', whatBroke:'', contact:'', notes:'' }, views:[] };
  }
  function load(){
    try{ var j=JSON.parse(localStorage.getItem(LS_KEY)); if(j && j.views) return j; }catch(e){}
    return null;
  }
  var _localJob = load();
  var job = _localJob || blankJob();

  var IDB_NAME = 'camscan', IDB_STORE = 'kv';
  var _db = null;                 // Promise<IDBDatabase|null>
  var _idbOK = false;             // decided once hydration finishes
  var _hydrated = false, _pendingSave = false;
  function idb(){
    if(_db) return _db;
    _db = new Promise(function(res){
      try{
        if(!window.indexedDB) return res(null);
        var rq = window.indexedDB.open(IDB_NAME, 1);
        rq.onupgradeneeded = function(){ try{ rq.result.createObjectStore(IDB_STORE); }catch(e){} };
        rq.onsuccess = function(){ res(rq.result); };
        rq.onerror = function(){ res(null); };
        rq.onblocked = function(){ res(null); };
      }catch(e){ res(null); }
    });
    return _db;
  }
  function idbReq(mode, fn){
    return idb().then(function(db){
      if(!db) return { ok:false };
      return new Promise(function(res){
        try{
          var tx = db.transaction(IDB_STORE, mode), store = tx.objectStore(IDB_STORE), rq = fn(store);
          var out = { ok:true };
          rq.onsuccess = function(){ out.value = rq.result; };
          tx.oncomplete = function(){ res(out); };              // durable only once committed
          tx.onerror = tx.onabort = function(){ res({ ok:false }); };
        }catch(e){ res({ ok:false }); }
      });
    });
  }
  function idbGet(){ return idbReq('readonly', function(s){ return s.get(LS_KEY); }); }
  function idbDel(){ return idbReq('readwrite', function(s){ return s.delete(LS_KEY); }); }

  // One write in flight at a time; anything saved meanwhile is folded into the next write,
  // which stores the job as it is THEN. Resolves true once the latest state is committed.
  var _writing = null, _dirty = false;
  function idbWrite(){
    if(_writing){ _dirty = true; return _writing; }
    _dirty = false;
    _writing = idbReq('readwrite', function(s){ return s.put(job, LS_KEY); }).then(function(r){
      _writing = null;
      if(!r.ok){ storageFull(); return false; }
      try{ localStorage.removeItem(LS_KEY); }catch(e){}       // migrated: no stale shadow copy
      if(_dirty) return idbWrite();
      return true;
    });
    return _writing;
  }
  function storageFull(){
    setStatus('⚠️ Storage is full — send or download this submission, then start a new one.', true);
  }
  function writeLocal(){
    try{ localStorage.setItem(LS_KEY, JSON.stringify(job)); return true; }
    catch(e){ storageFull(); return false; }      // quota — tell the user to submit/download now
  }
  function save(){
    job.savedAt = Date.now();
    if(!_hydrated){ _pendingSave = true; return true; }
    if(_idbOK){ idbWrite(); return true; }
    return writeLocal();
  }
  // Resolves once everything saved so far is durable.
  function persist(){
    flushSave();
    if(_idbOK) return _writing || Promise.resolve(true);
    return Promise.resolve(true);
  }
  // Load the stored job before anything renders. A stuck database must never stop the app,
  // so after a short wait it falls back to localStorage for this session.
  function hydrate(){
    var timeout = new Promise(function(res){ setTimeout(function(){ res({ timeout:true }); }, 2500); });
    return Promise.race([idbGet(), timeout]).then(function(r){
      if(r && r.ok){
        _idbOK = true;
        var stored = r.value;
        if(stored && stored.views && (!_localJob || (stored.savedAt || 0) >= (_localJob.savedAt || 0))) job = stored;
      }
      _hydrated = true;
      if(_pendingSave || (_idbOK && _localJob)){ _pendingSave = false; save(); }   // migrate a legacy job now
    });
  }
  // Debounced persist: save() re-serializes the whole job (every view's base64 image), so
  // calling it on every brief keystroke re-stringifies megabytes per character. Coalesce those
  // into one write; flushSave() forces it out on blur / panel-close / unload so nothing is lost.
  var _saveTimer=null;
  function saveSoon(){ if(_saveTimer) clearTimeout(_saveTimer); _saveTimer=setTimeout(function(){ _saveTimer=null; save(); }, 400); }
  function flushSave(){ if(_saveTimer){ clearTimeout(_saveTimer); _saveTimer=null; } return save(); }
  function reset(){ job = blankJob();
    // A reopened side's positional tag would now index the NEW job's views: the first photo
    // added to it would be overwritten by whatever is on screen at the next sync.
    var el=document.querySelector('.cal-view'); if(el) el.removeAttribute('data-view-index');
    try{ localStorage.removeItem(LS_KEY); }catch(e){}
    if(_idbOK) idbDel(); }

  // Per-photo identity: the cal-view's data-json URL carries a unique upload id, so we can
  // update-in-place instead of duplicating when the same photo is (re)captured.
  function currentSourceId(){ var el=document.querySelector('.cal-view'); return (el && el.getAttribute('data-json')) || null; }
  function hasCurrent(){ var sid=currentSourceId(); return !!(sid && job.views.some(function(v){ return v.sourceId===sid; })); }

  function addView(label){
    var o=overlay();
    if(!o || !o.img){ setStatus('Load a photo first, then add it.', true); return false; }
    var rec;
    try{ rec=o.getViewRecord(label); }catch(e){ setStatus('Could not capture this view: '+e.message, true); return false; }
    rec.capturedAt=new Date().toISOString();
    // Update-or-add, one entry per photo. A REOPENED saved side is identified by its
    // data-view-index (its rebuilt .cal-view carries no server sourceId, so sourceId dedup
    // would miss it and PUSH a duplicate); update that entry in place. Otherwise dedupe a
    // freshly-uploaded photo by its sourceId.
    var openIdx=currentOpenIndex();
    if(openIdx!=null && job.views[openIdx]){
      rec.sourceId=job.views[openIdx].sourceId||null;   // keep the original photo's identity
      if(job.views[openIdx].photoKey) rec.photoKey=job.views[openIdx].photoKey;   // ...and its 3D-part key
      job.views[openIdx]=rec;
    }else{
      rec.sourceId=currentSourceId();
      var idx = rec.sourceId ? job.views.findIndex(function(v){ return v.sourceId===rec.sourceId; }) : -1;
      if(idx>=0){ if(job.views[idx].photoKey) rec.photoKey=job.views[idx].photoKey; job.views[idx]=rec; }
      else job.views.push(rec);
    }
    var ok=save(); changed();
    // Only claim success if it actually persisted; on quota failure save() already showed the
    // amber "storage full" warning — don't clobber it with a green "✓ Saved".
    if(ok) setStatus('✓ Saved “'+(label||rec.label||'view')+'”. Add another photo, or Send when done.');
    return ok;
  }

  // ---- keeping the photo on screen -------------------------------------------------------
  // A fresh upload used to join the job only when a label chip was tapped. Everything else —
  // 🧩 Use in 3D part, New, a tap on another side, a reload, the phone dropping the tab while
  // the camera was open — left it out, so the part kept a Top view whose photo, scale and
  // trace were gone: it could be built, but never reopened to fix. Now anything the user has
  // put into a photo keeps it, through this one path.
  //
  // What counts as "something to keep": a scale, any annotation, or a 3D view taken from it.
  // `manualOnly` narrows the scale to one the user set, for the automatic capture below: a
  // printed marker found on upload is the server's work and comes back by itself on a
  // re-upload, so it must not quietly add every photo that happens to show one.
  function hasContent(o, manualOnly){
    if(!o || !o.img) return false;
    if(o.ann && o.ann.items && o.ann.items.length) return true;
    if(o.opts && (o.opts.manualMmPerPx || o.opts.manualHomography)) return true;
    if(!manualOnly && o.isCalibrated && o.isCalibrated()) return true;
    return rolesOf(photoId()).length > 0;
  }
  // The 3D roles taken from the photo with this key, in build order.
  function rolesOf(key){
    if(!key || !job.part3d || !job.part3d.views) return [];
    var pv = job.part3d.views;
    return ['top', 'side', 'end', 'cavity'].filter(function(r){ return pv[r] && pv[r].source === key; });
  }
  // Every key a stored entry has answered to as photoId() — see photoId() for why both exist.
  function entryKeys(v){
    var out = [];
    if(v && v.sourceId && String(v.sourceId).indexOf('data:') !== 0) out.push(v.sourceId);
    if(v && v.photoKey) out.push(v.photoKey);
    return out;
  }
  function entryRoles(v){
    var out = [];
    entryKeys(v).forEach(function(k){ rolesOf(k).forEach(function(r){ if(out.indexOf(r) < 0) out.push(r); }); });
    return out;
  }
  // Dash swaps a new upload into the SAME .cal-view node, and the overlay reboots onto it a
  // frame later. In between, the node already carries the new photo's id while the overlay
  // still holds the old photo: capturing then would store the old photo under the new id.
  function overlayIsCurrent(){
    var el=document.querySelector('.cal-view');
    if(!el || el.__bootedImg===undefined) return true;    // not booted by the loader (or a stub)
    return el.__bootedImg===(el.getAttribute('data-img')||el.getAttribute('data-img-fallback'));
  }
  function isDefaultLabel(l){
    return !l || /^View \d+$/.test(l) || /^(Top|Side|End|Cavity)$/.test(l);
  }
  // Store (or refresh) the photo on screen. `label` names a new entry; with none, the entry is
  // named after the 3D view it holds ("Top"), else "View N". An entry already stored keeps the
  // label the user gave it, but a default one ("View 2", or the role it held before) follows
  // the role it is given now - except that a main role the photo still holds wins over
  // "Cavity": a cavity traced on the End photo renamed it "Cavity" while it went on being the
  // End view. (Only over Cavity: the 3D part calls this BEFORE it moves the role, so a Top
  // photo being made the Side still holds 'top' here and must still become "Side".)
  // `force` stores the photo even with nothing in it yet — for a tap on a label chip, which is
  // the user asking for exactly that.
  // Returns true when the photo is in the job afterwards.
  function captureCurrent(label, force){
    var o=overlay();
    if(!o || !o.img || !overlayIsCurrent()) return false;
    // A photo the user removed while it was on screen stays removed: its marks and its 3D
    // view still read as "something to keep", so the next reload, New or edit used to store
    // it again — while its view went on saying "photo removed". Only a label tap brings it back.
    var gone = (o.__jobRemoved && o.__jobRemoved.img === o.img) ? o.__jobRemoved : null;
    if(gone && !force) return false;
    var held=rolesOf(photoId());
    if(label==='Cavity' && held.length && held[0]!=='cavity') label=ROLE_LABEL[held[0]];
    var idx=currentOpenIndex();
    if(idx!=null){
      if(label && label!==job.views[idx].label && isDefaultLabel(job.views[idx].label)) job.views[idx].label=label;
      syncOpenView();
      return true;
    }
    if(!force && !hasContent(o)) return false;
    if(!label) label = held.length ? ROLE_LABEL[held[0]] : 'View '+(job.views.length+1);
    // photoId() is the upload's own id both before and after this (the stored entry keeps it
    // as its sourceId, and currentOpenIndex() finds the entry by it), so a 3D view assigned
    // from this photo still points at it.
    if(addView(label)===false) return false;
    if(gone) restoreRemoved(gone);
    return currentOpenIndex()!=null;
  }
  // Bring back a removed photo the user re-added: it answers to its old keys again, so the 3D
  // views taken from it follow it and stop saying "photo removed".
  function restoreRemoved(gone){
    var o=overlay(); if(o) o.__jobRemoved=null;
    var last=job.views.length-1, e=job.views[last];
    if(!e || (currentOpenIndex()!=null && currentOpenIndex()!==last)) return;
    if(gone.photoKey && !e.photoKey) e.photoKey=gone.photoKey;
    // A reopened side's inline data: id is never matched (see currentOpenIndex), so it is
    // found by position again, as when it was opened.
    var el=document.querySelector('.cal-view');
    if(currentOpenIndex()==null && el && String(e.sourceId||'').indexOf('data:')===0) el.setAttribute('data-view-index', String(last));
    var keys=entryKeys(gone).concat(entryKeys(e));
    var pv=job.part3d && job.part3d.views;
    if(pv) ['top', 'side', 'end', 'cavity'].forEach(function(r){
      if(pv[r] && keys.indexOf(pv[r].source)>=0) delete pv[r].photo_removed;
    });
    save(); changed();
  }

  function removeView(i){
    // A 3D view built from this photo keeps its last outline, but nothing can update it again
    // once the photo is gone, and the Build dialog would go on saying it "follows its outline".
    // Ask, then flag it (calib.part3d.js shows the flag).
    var e=job.views[i];
    var onScreen=(currentOpenIndex()===i);
    var roles=entryRoles(e);
    if(roles.length){
      var names=roles.map(function(r){ return ROLE_LABEL[r]; }).join(' and ');
      if(!window.confirm('This photo’s outline is the part’s '+names+' view. Remove the photo but keep its last outline?')) return false;
      roles.forEach(function(r){ job.part3d.views[r].photo_removed=true; });
    }
    job.views.splice(i,1);
    // The overlay still holds the photo when it was the one on screen: remember it, or the
    // next capture (reload, New, autosave) would quietly store it again. A new photo or
    // another side boots a new overlay, which forgets this.
    var o=overlay();
    if(onScreen && o && o.img) o.__jobRemoved={ img: o.img, sourceId: e && e.sourceId || null, photoKey: e && e.photoKey || null };
    // A reopened side is tracked by a POSITIONAL data-view-index; the splice shifts every
    // later index, so re-sync the open overlay's tag — otherwise a later syncOpenView/addView
    // writes the open side's record over the WRONG (shifted) view and loses data.
    var el=document.querySelector('.cal-view');
    var a=el && el.getAttribute('data-view-index');
    if(a!=null){
      var oi=parseInt(a,10);
      if(oi===i) el.removeAttribute('data-view-index');          // the open side itself was removed
      else if(oi>i) el.setAttribute('data-view-index', String(oi-1));
    }
    save(); changed();
    return true;
  }

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
      handoffAck=true; _sending=false;
      if(handoffTimer){ clearTimeout(handoffTimer); handoffTimer=null; }
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
    // Drawn as a secondary (outline) button, not in the accent fill: for someone making their
    // own part, the way forward is 🧩 Build 3D part and Export; a filled "Submit to Datum" from
    // the first photo on read as the next step and led to a vendor form.
    btn.style.cssText='position:fixed;z-index:38;left:12px;bottom:calc(var(--cal-kpi-h, 46px) + 14px);'+
      'min-height:44px;padding:9px 15px;border-radius:22px;border:1px solid #2f5866;background:#12171c;color:#cfe6ee;'+
      'font:600 14px Segoe UI,system-ui,sans-serif;box-shadow:0 6px 20px rgba(0,0,0,.5);cursor:pointer;display:none;';
    _btnFloat=btn.style.cssText;
    btn.onclick=openPanel;
    document.body.appendChild(btn);
    var st=document.createElement('style');
    // Phone: the button shows the icon and count only, and the Sides bar sits left of the
    // Simple view toggle instead of under it.
    st.textContent='@media (max-width: 600px){ #cal-job-btn .cal-job-long{ display:none; }'+
      ' #cal-job-btn{ padding:6px 9px !important; }'+
      ' #cal-side-switcher{ left:10px !important; right:auto !important; margin:0 !important; max-width:calc(100vw - 150px) !important; } }';
    document.head.appendChild(st);
  }
  var _btnSig=null, _btnFloat=null;
  // Where the button lives. Floating over the bottom-left corner, it sat on top of the
  // selected outline's buttons (Delete, Tidy…) on a desktop and behind the trace's Detail
  // bar on a phone. So while a photo is open it goes in that photo's top bar, beside Export;
  // it only floats when there is no top bar to hold it.
  function dockHost(){
    var bars=document.querySelectorAll('.cal-view .cal-tb-right');
    for(var i=0;i<bars.length;i++){ if(bars[i].getClientRects().length) return bars[i]; }
    return null;
  }
  function reflectButton(){
    ensureButton();
    var show = !!document.querySelector('.cal-view') || job.views.length>0;
    var host = show ? dockHost() : null;
    // Moving the button is itself a DOM mutation, and this runs from a subtree
    // MutationObserver: only move it when it is not already where it belongs.
    if(host && btn.parentNode!==host){
      host.insertBefore(btn, host.firstChild);
      btn.style.cssText='display:none;align-items:center;gap:0;min-height:40px;padding:6px 12px;border-radius:999px;'+
        'border:1px solid #2f5866;background:transparent;color:#cfe6ee;font:600 13px Segoe UI,system-ui,sans-serif;cursor:pointer;white-space:nowrap;';
    } else if(!host && btn.parentNode!==document.body){
      document.body.appendChild(btn);
      btn.style.cssText=_btnFloat;
    }
    // Idempotence guard: writing btn.innerHTML is itself a DOM mutation too — skip the
    // write when nothing shown changed, or the write would re-trigger the observer forever
    // and hang the page.
    var sig = (show?'1':'0')+'|'+job.views.length+'|'+(host?'d':'f');
    if(sig===_btnSig && btn.style.display===(show?'inline-flex':'none')) return;
    _btnSig=sig;
    btn.style.display = show ? 'inline-flex' : 'none';
    // A phone's top bar has room for the icon and the count, not the words.
    btn.innerHTML = '📋<span class="cal-job-long">&nbsp;Submit to Datum</span>' +
      (job.views.length ? '<span>&nbsp;<span class="cal-job-long">(</span>'+job.views.length+'<span class="cal-job-long">)</span></span>' : '');
    btn.setAttribute('aria-label', 'Submit to Datum' + (job.views.length ? ' ('+job.views.length+' views)' : ''));
    btn.title='Submit to Datum';
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
    input.oninput=function(){ job.brief[key]=input.value; saveSoon(); };   // debounced persist
    input.onchange=function(){ job.brief[key]=input.value; flushSave(); };  // flush on blur/commit
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

    card.appendChild(partRow());

    // add current view
    var o=overlay();
    var addSec=document.createElement('div'); addSec.style.cssText='margin-top:16px;padding:12px;background:#0e1519;border:1px solid #22303a;border-radius:10px;';
    var addTitle=document.createElement('div'); addTitle.style.cssText='font-weight:600;margin-bottom:6px;';
    addTitle.textContent='Keep the photo on screen as:';
    addSec.appendChild(addTitle);
    if(o && o.img){
      var uncal = !(o.isCalibrated && o.isCalibrated());
      if(uncal){ var warn=document.createElement('div'); warn.textContent='⚠️ No scale set on this view — set one first so measurements are in mm.';
        warn.style.cssText='font-size:12px;color:#e0a24a;margin-bottom:8px;'; addSec.appendChild(warn); }
      var row=document.createElement('div'); row.style.cssText='display:flex;flex-wrap:wrap;gap:7px;';
      // The photo's name and its 3D view are one word (Top / Side / End). A chip used to rename
      // the photo used as Top to "Side" while it stayed the Top view, so the panel read
      // "Side · 🧩 Top". Show the current name, and move the 3D view with a role name.
      var oi=currentOpenIndex(), main=rolesOf(photoId()).filter(function(r){ return r!=='cavity'; });
      var curLabel=(oi!=null && job.views[oi]) ? job.views[oi].label : (main.length ? ROLE_LABEL[main[0]] : null);
      RECOMMENDED.concat(EXTRA_LABELS.map(function(l){return {label:l};})).forEach(function(v){
        var on=(v.label===curLabel);
        var c=chip((on?'✓ ':'')+v.label, function(){ relabelCurrent(v.label, main); }, false);
        c.setAttribute('aria-pressed', on?'true':'false');
        if(on){ c.style.borderColor='#00d4ff'; c.style.color='#9fe6f5'; }
        row.appendChild(c);
      });
      addSec.appendChild(row);
    } else {
      var hint=document.createElement('div'); hint.style.cssText='font-size:13px;color:#9fb2bb;';
      // Named after the button that is really there: the toolbar's "New" became 📷 Add photo,
      // and from the start page there is no toolbar at all.
      hint.textContent = document.querySelector('.cal-view')
        ? 'No photo is open. Tap 📷 Add photo to take/upload a view, measure it, then come back here to add it.'
        : 'No photo is open. Open a saved one under Collected views below, or close this and upload a photo.';
      addSec.appendChild(hint);
    }
    card.appendChild(addSec);

    // guided shot list
    var guide=document.createElement('div'); guide.style.cssText='margin-top:14px;';
    var gt=document.createElement('div'); gt.style.cssText='font-weight:600;margin-bottom:6px;'; gt.textContent='Suggested shots';
    guide.appendChild(gt);
    // A shot is done when a photo carries its label OR its outline is the part's view of that
    // name: assigning Top in 🧩 Use in 3D part is the user saying "this is the top shot".
    var pv=part().views;
    RECOMMENDED.forEach(function(v){
      var have=!!pv[v.label.toLowerCase()] ||
        job.views.some(function(w){ return (w.label||'').toLowerCase()===v.label.toLowerCase(); });
      var r=document.createElement('div'); r.style.cssText='font-size:13px;color:'+(have?'#7fe0b0':'#9fb2bb')+';margin:2px 0;';
      r.setAttribute('data-shot', v.label.toLowerCase()); r.setAttribute('data-have', have?'1':'0');
      r.innerHTML=(have?'✓ ':'○ ')+'<b>'+esc(v.label)+'</b> — '+esc(v.why);
      guide.appendChild(r);
    });
    var tip=document.createElement('div'); tip.style.cssText='font-size:12px;color:#7f8f98;margin-top:6px;';
    // "The same reference flat in every shot" put the ruler on the floor for the side and end
    // views, below the face being measured — +5% on a 20 mm-high face (CAPTURE_RULES #1).
    tip.textContent='In every photo, lay the ruler flat ON the face that photo shows. A ruler held below or beside the part gives the wrong size.';
    guide.appendChild(tip);
    guide.appendChild(captureGuide());
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
        var roles=entryRoles(v).map(function(r){ return ROLE_LABEL[r]; });
        meta.innerHTML='<div style="font-weight:600;">'+esc(v.label||'View '+(i+1))+'</div>'+
          '<div style="font-size:12px;color:#9fb2bb;">'+mm+' measurement'+(mm===1?'':'s')+' · '+esc(sc)+
          (roles.length ? ' · 🧩 '+esc(roles.join(', ')) : '')+'</div>';
        var btns=document.createElement('div'); btns.style.cssText='display:flex;gap:6px;flex:0 0 auto;';
        if(v.restore && v.restore.raw){
          var open=chip('Open', function(){ openViewForEditing(i); }, false);
          open.style.minHeight='36px'; open.style.padding='5px 10px'; open.title='Re-open this side in CamScan to model it';
          btns.appendChild(open);
        }
        var del=chip('Remove', function(){ if(removeView(i)) openPanel(); }, false);
        del.style.minHeight='36px'; del.style.padding='5px 10px';
        btns.appendChild(del);
        vr.appendChild(im); vr.appendChild(meta); vr.appendChild(btns); card.appendChild(vr);
      });
      // Same dimension, two views, two different answers — one of the scales is wrong, and
      // nothing else in the app would ever say so. Shown here because this is where the user
      // can still fix it: re-open the offending side and re-set its scale.
      scaleDisagreementRows().forEach(function(row){ card.appendChild(row); });
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
  // The 3D part at a glance, with the way to build it. This panel was the only place that
  // listed the photos, yet it said nothing about the part being made from them: a user making
  // their own STL had to find the 🧩 chip in the top bar, and the panel's one filled button
  // sent the photos to Datum instead.
  function partRow(){
    var pv=part().views;
    var row=document.createElement('div'); row.id='cal-job-part';
    row.style.cssText='margin-top:14px;padding:10px 12px;background:#13202a;border:1px solid #29414c;border-radius:10px;'+
      'display:flex;flex-wrap:wrap;gap:8px;align-items:center;';
    var t=document.createElement('div'); t.style.cssText='flex:1 1 220px;font-size:13.5px;';
    t.innerHTML='<b>3D part:</b> '+['top','side','end'].map(function(r){
      return '<span data-role="'+r+'" style="color:'+(pv[r]?'#7fe0b0':'#8aa0aa')+';">'+ROLE_LABEL[r]+' '+(pv[r]?'✓':'—')+'</span>';
    }).join(' &nbsp;')+(pv.cavity ? ' &nbsp;<span data-role="cavity" style="color:#7fe0b0;">Cavity ✓</span>' : '');
    row.appendChild(t);
    var n=['top','side','end','cavity'].filter(function(r){ return pv[r]; }).length;
    var P=window.CalibPart3D;
    if(n && P && typeof P.open==='function'){
      var b=chip('🧩 Build 3D part', function(){ closePanel(); P.open(); }, true); b.id='cal-job-build';
      row.appendChild(b);
    } else {
      var h=document.createElement('div'); h.style.cssText='flex:1 1 100%;font-size:12px;color:#8aa0aa;';
      h.textContent='Trace a photo’s outline, then 🧩 Use in 3D part → Top, Side or End.';
      row.appendChild(h);
    }
    return row;
  }
  // A label chip in the job panel. A Top/Side/End name on a photo that already is one of the
  // part's views moves that view too (the Build dialog's "Move to…"), or says where to change
  // it when the other view is taken. Other names (Detail…) only rename the photo.
  function relabelCurrent(label, main){
    var role=label.toLowerCase(), P=window.CalibPart3D, pv=part().views;
    if(main.length && ROLE_LABEL[role] && role!=='cavity' && main.indexOf(role)<0){
      if(pv[role] || !P || typeof P.move!=='function'){
        setStatus('This photo is the part’s '+ROLE_LABEL[main[0]]+' view, and the '+label+' view is already set. '+
                  'Change it in 🧩 Build 3D part (Move to…).', true);
        return false;
      }
      P.move(main[0], role);
    }
    var ok=addView(label);
    if(ok){ openPanel(); setStatus('✓ Saved “'+label+'”.'+(main.length && main.indexOf(role)<0 && ROLE_LABEL[role] && role!=='cavity'
                                                              ? ' It is now the part’s '+label+' view.' : '')); }
    return ok;
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
      // The keys the 3D part's views name their photo by (see photoId): without them a
      // loaded job's Top/Side/End could not find the photo they were traced on.
      if(v.photoKey) o.photoKey=v.photoKey;
      if(v.sourceId && String(v.sourceId).indexOf('data:')!==0) o.sourceId=v.sourceId;
      return o;
    });
  }
  // The 3D part as assembled in the Build dialog — which outline is the Top / Side / End /
  // Cavity, its alignment and Match factor, and the Build settings (wall, sizes, ends) — for a
  // job file or a submission. It used to stay in this browser only: a job opened on another
  // device, or by Datum from a submission, had every photo but no part.
  function exportPart(){
    var p=job.part3d;
    if(!p || typeof p!=='object') return null;
    var has=(p.views && Object.keys(p.views).length) || (p.opts && Object.keys(p.opts).length);
    return has ? JSON.parse(JSON.stringify({ views:p.views||{}, opts:p.opts||{} })) : null;
  }
  // A part read back from a file: only plain objects, and only the four roles.
  function importPart(raw){
    var out={ views:{}, opts:{} };
    if(!raw || typeof raw!=='object' || Array.isArray(raw)) return out;
    var vs=(raw.views && typeof raw.views==='object' && !Array.isArray(raw.views)) ? raw.views : {};
    ['top', 'side', 'end', 'cavity'].forEach(function(r){
      var v=vs[r];
      if(v && typeof v==='object' && !Array.isArray(v) && Array.isArray(v.points) && v.points.length>=3) out.views[r]=v;
    });
    if(raw.opts && typeof raw.opts==='object' && !Array.isArray(raw.opts)) out.opts=raw.opts;
    return out;
  }

  // ---- Save / Load a whole job as a portable .camscan.json bundle -------------------
  // Save writes every collected view's reloadable snapshot to one file; Load reads it back
  // and rehydrates the set, so a submitted part can be re-opened in CamScan with each side
  // editable (scale + the customer's trace restored) instead of re-measured from scratch.
  function saveJobToFile(){
    syncOpenView();   // fold in any edits to the side currently open before writing the file
    if(!job.views.length){ setStatus('Add at least one view before saving.', true); return; }
    var bundle={ kind:'camscan.job', version:1, id:job.id, createdAt:job.createdAt,
                 savedAt:new Date().toISOString(), brief:job.brief, views:exportViews(true),
                 part3d:exportPart() };
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
            views:bundle.views.slice(),
            part3d:importPart(bundle.part3d) };
      // The on-screen overlay belongs to the JUST-DISCARDED job; its positional data-view-index
      // now indexes into the NEW job's views. Clear it so a later Open/save/pagehide sync can't
      // write the old overlay's image+marks over a freshly-loaded view (silent corruption).
      var stale=document.querySelector('.cal-view'); if(stale) stale.removeAttribute('data-view-index');
      save();   // best-effort; if the bundle is big this may hit quota — the job still works in memory
      changed();
      openPanel();
      var reloadable=job.views.filter(function(v){ return v.restore&&v.restore.raw; }).length;
      var roles=['top', 'side', 'end', 'cavity'].filter(function(r){ return job.part3d.views[r]; });
      setStatus('Loaded '+job.views.length+' view'+(job.views.length===1?'':'s')+
        (roles.length ? ' and the 3D part ('+roles.map(function(r){ return ROLE_LABEL[r]; }).join(', ')+')' : '')+'. '+
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
    var viewer=document.getElementById('viewer');
    if(!viewer){ setStatus('Viewer is not ready — reload the page and try again.', true); return; }
    // Persist any edits made to the side currently open BEFORE we swap it away, so flipping
    // between sides never loses work. A fresh photo on screen is stored first if anything was
    // done to it: tapping a side used to drop it silently, with its End view left pointing
    // at a photo no side could open.
    if(currentOpenIndex()==null) captureCurrent(); else syncOpenView();
    // Re-read AFTER syncing: re-opening the side you're already on makes syncOpenView REPLACE
    // job.views[i] with the freshly-captured record — the pre-sync `v` would reboot from the
    // stale snapshot and drop the just-made edits.
    v=job.views[i];
    var st=v.restore;
    // Modelling wants the full markup rail, not the stripped intake flow — and stays in it
    // (calib.simple.js would otherwise bring the card back after the next trace).
    window.__camscanReturnToSimple=null;
    try{ localStorage.setItem('camscan.simplemode','0'); }catch(e){}
    document.body.classList.remove('camscan-simple');

    var calibUrl='data:application/json;charset=utf-8,'+encodeURIComponent(JSON.stringify(st.calib||{markers:[]}));
    var wrap=document.createElement('div'); wrap.className='cal-wrap';
    var view=document.createElement('div'); view.className='cal-view'; view.id='cal-view';
    view.setAttribute('data-img', st.raw);
    view.setAttribute('data-json', calibUrl);
    view.setAttribute('data-view-index', String(i));   // which stored view this overlay IS
    view.setAttribute('data-job-reopened', '1');       // built here, not by Dash: see reapReopened
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
    renderSideSwitcher();
    setStatus('Opened “'+(v.label||('View '+(i+1)))+'” for editing.');
  }

  // Which stored view the on-screen overlay currently IS. A reopened side carries a positional
  // data-view-index. A fresh upload that has been stored is found by its upload id instead,
  // so autosave, syncOpenView and the pagehide flush cover it too — before this, its edits
  // after the first capture were never saved. (It is not tagged with data-view-index: Dash
  // reuses the same .cal-view node for the next upload, and the tag would carry over to the
  // new photo and make it overwrite this one.) Upload ids are unique; a reopened side's
  // inline data: URL is not (two photos with the same size and scale share it), so only the
  // former is matched.
  function currentOpenIndex(){
    var el=document.querySelector('.cal-view');
    var a=el && el.getAttribute('data-view-index');
    if(a!=null){
      var n=parseInt(a,10); return (isNaN(n)||n<0||n>=job.views.length) ? null : n;
    }
    var sid=currentSourceId();
    if(!sid || sid.indexOf('data:')===0) return null;
    for(var i=0;i<job.views.length;i++){ if(job.views[i].sourceId===sid) return i; }
    return null;
  }
  // Capture edits to the currently-open side back into the job (annotations, measurements,
  // geometry, and the reloadable snapshot) so switching sides / saving keeps the work.
  function syncOpenView(){
    var idx=currentOpenIndex(); if(idx==null) return;
    var cur=job.views[idx], o=overlay();
    if(!cur || !o || !o.img || !o.getViewRecord) return;
    try{
      var rec=o.getViewRecord(cur.label||'');
      rec.sourceId=cur.sourceId; rec.capturedAt=cur.capturedAt||new Date().toISOString();
      // The photo's 3D-part identity must outlive re-capture: a cavity assigned after the user
      // switches away and back is still on the SAME photo as the outline assigned before.
      if(cur.photoKey) rec.photoKey=cur.photoKey;
      job.views[idx]=rec; save();
    }catch(e){ /* best-effort — a failed capture just keeps the previous snapshot */ }
  }

  // ---- side switcher: one-tap flipping between the loaded sides while modelling ----------
  var _switcher=null, _switchSig=null;
  function reloadableViews(){
    var out=[]; job.views.forEach(function(v,i){ if(v.restore&&v.restore.raw) out.push({v:v,i:i}); });
    return out;
  }
  function renderSideSwitcher(){
    var views=reloadableViews();
    var viewer=document.querySelector('.cal-view');
    var simple=false; try{ simple=document.body.classList.contains('camscan-simple'); }catch(e){}
    var active=currentOpenIndex();
    // A photo on screen that is not in the job gets its own entry, so it is plain that it is
    // not stored yet — and that tapping another side will store it (openViewForEditing).
    var ov=overlay();
    var unsaved = !!viewer && active==null && !!(ov && ov.img);
    // Only while modelling a multi-side job: a viewer is open, full tools (not simple mode),
    // and there is more than one side (stored or on screen) to move between.
    var show = !!viewer && !simple && (views.length + (unsaved?1:0))>=2;
    if(!show){ if(_switcher){ _switcher.remove(); _switcher=null; _switchSig=null; } return; }
    // Each side's 3D role is shown on its button, so the redraw has to follow the roles too.
    var sig=views.map(function(o){ return (o.v.label||'')+o.i+':'+entryRoles(o.v).join(','); }).join('|')+'|'+active+'|'+unsaved;
    if(_switcher && sig===_switchSig) return;   // nothing shown changed → no rebuild (observer-safe)
    _switchSig=sig;
    if(!_switcher){
      _switcher=document.createElement('div'); _switcher.id='cal-side-switcher';
      // Centred with margins, not left:50% + translate: that version could only grow to half
      // the screen's width and wrapped to two rows on a phone, over the Simple view toggle.
      _switcher.style.cssText='position:fixed;top:54px;left:0;right:0;margin:0 auto;width:max-content;z-index:37;'+
        'display:flex;gap:7px;align-items:center;flex-wrap:wrap;justify-content:center;max-width:calc(100vw - 20px);'+
        'background:#12171c;border:1px solid #26323b;border-radius:12px;box-shadow:0 8px 26px rgba(0,0,0,.5);padding:7px 11px;';
      document.body.appendChild(_switcher);
    }
    _switcher.textContent='';
    var lab=document.createElement('span'); lab.textContent='Sides:'; lab.style.cssText='color:#9fb2bb;font-size:12px;';
    _switcher.appendChild(lab);
    views.forEach(function(o){
      var on=(o.i===active);
      var name=o.v.label||('View '+(o.i+1));
      // The role is what the user picked the photo for ("Top · 🧩"); when the label already
      // says it, only the 🧩 is added.
      var roles=entryRoles(o.v).map(function(r){ return ROLE_LABEL[r]; });
      var tag=roles.length ? (roles.join('/')===name ? ' · 🧩' : ' · '+roles.join('/')+' 🧩') : '';
      var b=document.createElement('button'); b.type='button'; b.textContent=name+tag;
      if(roles.length) b.setAttribute('data-roles', roles.join(',').toLowerCase());
      b.style.cssText='min-height:34px;padding:5px 11px;border-radius:8px;cursor:pointer;font:600 13px Segoe UI,system-ui,sans-serif;'+
        (on?'background:#00d4ff;color:#04222b;border:none;':'background:#151a1f;color:#cfe6ee;border:1px solid #29414c;');
      b.onclick=function(){ if(o.i!==currentOpenIndex()) openViewForEditing(o.i); };
      _switcher.appendChild(b);
    });
    if(unsaved){
      var u=document.createElement('button'); u.type='button'; u.id='cal-side-unsaved';
      u.textContent='This photo (unsaved)';
      u.title='Not in the part yet — tap to keep it. Scaling or tracing it keeps it too.';
      u.style.cssText='min-height:34px;padding:5px 11px;border-radius:8px;cursor:pointer;font:600 13px Segoe UI,system-ui,sans-serif;'+
        'background:#2a2416;color:#f3d48a;border:1px dashed #c9a24a;';
      u.onclick=function(){ captureCurrent(null, true); renderSideSwitcher(); };
      _switcher.appendChild(u);
    }
  }

  // One send at a time. Without this a double tap on "Send to Datum" — easy on a phone, and
  // the button gives no feedback until the fetch resolves — POSTed the whole job twice, so
  // Datum got two submission directories and two e-mails for one part. It also closed a race
  // on the embed path: a handoff ack arriving just after the fallback timer fired delivered
  // the job to BOTH the parent page and /api/submit.
  var _sending = false;

  function submit(){
    if(_sending){ setStatus('Already sending — one moment…'); return; }
    syncOpenView();   // capture edits to the open side so the submission reflects them
    if(!validate()) return;
    _sending = true;
    if(EMBED){ handoff(); return; }
    sendToServer();
  }

  // Embedded path: hand the collected views to the parent page's quote form. If the page
  // doesn't acknowledge within a short window, fall back to our own /api/submit pipeline.
  function handoff(){
    setStatus('Adding to your quote…');
    var msg={ type:'camscan:submit', version:1, brief:job.brief, views:exportViews(false), part3d:exportPart() };
    var win = hostWin || window.parent || window;
    // The message carries the customer's brief (name, contact e-mail, notes) AND every view's
    // raw photo, so it is only ever posted to a NAMED origin — never targetOrigin '*'. A '*'
    // post is delivered to whatever document currently occupies the target window, which need
    // not be the page that framed us. Prefer the handshake origin; otherwise the referrer's
    // origin (the framing page). With neither, skip the hand-off and use our own /api/submit
    // rather than broadcasting. trustedHost() re-checks the origin so tightening its allow-list
    // later automatically tightens this path too.
    var origin = hostOrigin;
    if(!origin){ try{ origin = document.referrer ? new URL(document.referrer).origin : null; }catch(e){ origin=null; } }
    if(!origin || !trustedHost(origin)){ sendToServer(); return; }   // keeps the in-flight flag
    handoffAck=false;
    try{ win.postMessage(msg, origin); }
    catch(e){ sendToServer(); return; }
    if(handoffTimer) clearTimeout(handoffTimer);
    var wait = (typeof window.__CAMSCAN_HANDOFF_TIMEOUT==='number') ? window.__CAMSCAN_HANDOFF_TIMEOUT : 2200;
    handoffTimer=setTimeout(function(){ if(!handoffAck) sendToServer(); }, wait);
  }

  function sendToServer(){
    _sending = true;   // also covers the hand-off fallback path, which calls us directly
    setStatus('Sending…');
    var payload={ id:job.id, createdAt:job.createdAt, submittedAt:new Date().toISOString(),
      brief:job.brief, views:exportViews(true), part3d:exportPart(), userAgent:navigator.userAgent };
    fetch(apiUrl('api/submit'), { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) })
      // Not every failure answers with JSON — Flask's 413 handler for an oversized body
      // replies in plain text, and r.json() rejecting there surfaced a real "too large" as a
      // bare "Network error". Read the body as text and parse leniently.
      .then(function(r){
        return r.text().then(function(t){
          var j=null; try{ j=JSON.parse(t); }catch(_){}
          return {ok:r.ok, status:r.status, j:j, text:t};
        });
      })
      .then(function(res){
        _sending = false;
        if(res.ok && res.j && res.j.ok){
          setStatus('✅ '+(res.j.message||'Sent to Datum Laboratories. We\'ll be in touch.'));
          reset(); changed();
          setTimeout(function(){ closePanel(); }, 2200);
        } else {
          var msg = (res.j && res.j.error)
            || (res.status===413 ? 'This submission is too large to send. Remove a view, or use “Download summary” and email it to us.' : '')
            || 'Could not send. Try “Download summary” and email it to us.';
          setStatus('⚠️ '+msg, true);
        }
      })
      .catch(function(e){
        _sending = false;
        setStatus('⚠️ Network error — try “Download summary” and email it to us. ('+e.message+')', true);
      });
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
      // esc() the src too: a loaded .camscan.json is arbitrary user input, and a label-style
      // string with a double quote in views[].image would break out of the attribute and
      // inject markup into the summary the customer then opens locally.
      if(v.image) html+='<img src="'+esc(v.image)+'" style="max-width:100%;border:1px solid #ccc;margin:8px 0;">';
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

  // The job outlives a reload on purpose (one photo per page load), so it also outlives the
  // part it was for: start a different part next week and its 3D views were still Top, Side
  // and Cavity from the last one, with nothing on screen saying why. Say so on the landing
  // card, and offer the way out there — before a photo of the new part joins the old job.
  var _landingSig=null;
  function ensureLandingJob(){
    var host=document.querySelector('.landing-card');
    if(!host) return;
    var roles=['top','side','end','cavity'].filter(function(r){ return job.part3d && job.part3d.views && job.part3d.views[r]; });
    var n=job.views.length, sig=n+'|'+roles.join(',')+'|'+(job.brief.part||'')+'|'+(window.CalibPart3D?1:0);
    var el=host.querySelector('#cal-landing-job');
    // Above the photo drop zone, so it is read before a photo joins the part. (#uploader can
    // sit a wrapper below the card, so climb to the card's own child.) Dash re-renders the
    // card's own children and can leave this after them, so the place is re-checked each time.
    var up=document.getElementById('uploader');
    while(up && up.parentNode!==host) up=up.parentNode;
    if(el && up && el.nextElementSibling!==up) host.insertBefore(el, up);
    if(sig===_landingSig && (el || (!n && !roles.length))) return;
    _landingSig=sig;
    if(el) el.remove();
    if(!n && !roles.length) return;
    var LBL={top:'Top',side:'Side',end:'End',cavity:'Cavity'};
    // After a reload this card was all the start page showed of the part: Build and the photos
    // were only reachable through "Submit to Datum". Offer both here.
    var P=window.CalibPart3D, views=reloadableViews();
    var tops=views.filter(function(o){ return entryRoles(o.v).indexOf('top')>=0; });
    var last=tops.length ? tops[tops.length-1] : views[views.length-1];   // the Top photo first
    el=document.createElement('div'); el.id='cal-landing-job';
    el.style.cssText='margin:12px 0 0;padding:10px 12px;border-radius:10px;background:#16202a;border:1px solid #29414c;'+
      'font:13px/1.45 Segoe UI,system-ui,sans-serif;color:#cfe6ee;display:flex;flex-wrap:wrap;gap:8px;align-items:center;';
    var t=document.createElement('div'); t.style.cssText='flex:1 1 220px;';
    t.innerHTML='<b>Part in progress'+(job.brief.part?': '+esc(job.brief.part):'')+'</b><br>'+
      (n ? n+' photo'+(n===1?'':'s') : 'no photos saved')+
      (roles.length ? ' · 3D views: '+roles.map(function(r){ return LBL[r]; }).join(', ') : '')+
      // True in both modes now: a photo is kept once it is scaled, traced or used in the part
      // (captureCurrent). It used to promise this while full mode silently dropped the photo.
      '<br><span style="color:#8aa0aa;font-size:12px;">A photo you scale or trace is kept with this part.</span>';
    var b=chip('Start a new part', function(){
      if(!window.confirm('Start a new part? The '+(n ? n+' saved photo'+(n===1?'':'s') : 'saved views')+
        ' of the current part will be cleared from this device. (Tap “💾 Save job” first to keep a copy.)')) return;
      reset(); changed(); render();
    }, false);
    b.id='cal-landing-newpart';
    var sv=chip('💾 Save job', saveJobToFile, false);
    el.appendChild(t);
    if(roles.length && P && typeof P.open==='function'){
      var bd=chip('🧩 Continue: Build 3D part', function(){ P.open(); }, true); bd.id='cal-landing-build';
      el.appendChild(bd);
    }
    if(last){
      var op=chip('📂 Open '+(tops.length ? 'the Top photo' : 'last photo'),
                  function(){ openViewForEditing(last.i); }, false);
      op.id='cal-landing-open';
      el.appendChild(op);
    }
    el.appendChild(sv); el.appendChild(b);
    if(up) host.insertBefore(el, up); else host.appendChild(el);
  }

  // A side reopened above is a node this module put into #viewer, which Dash does not know
  // about: the next upload adds Dash's own .cal-view AFTER it instead of replacing it. Every
  // querySelector('.cal-view') — this module's, the 3D part's, simple mode's — then kept
  // finding the old side first, so the new photo's outline was assigned from, and saved
  // over, the side that was open before. Save that side and take it away.
  function reapReopened(){
    var all=document.querySelectorAll('.cal-view');
    if(all.length<2) return;
    var fresh=false;
    for(var i=0;i<all.length;i++){ if(all[i].getAttribute('data-job-reopened')!=='1') fresh=true; }
    if(!fresh) return;
    for(var k=0;k<all.length;k++){
      var el=all[k];
      if(el.getAttribute('data-job-reopened')!=='1') continue;
      if(el===document.querySelector('.cal-view')){ try{ syncOpenView(); }catch(e){} }
      var wrap=(el.parentNode && el.parentNode.classList && el.parentNode.classList.contains('cal-wrap')) ? el.parentNode : el;
      wrap.remove();
    }
  }
  function render(){ reapReopened(); reflectButton(); ensureLandingLoad(); ensureLandingJob(); renderSideSwitcher(); }
  // Refresh the button AND notify other layers (the simple-mode card) that the job changed.
  // The side switcher follows too: a photo stored or given a role changes its buttons without
  // any DOM mutation for the observer below to see.
  function changed(){ reflectButton(); renderSideSwitcher(); try{ document.dispatchEvent(new CustomEvent('camscan-job-changed')); }catch(e){} }

  // Dimensions already measured on THIS part, in other views, usable as a scale reference
  // here. The same physical object appears in every photo of a job, so a length measured
  // where a reference WAS in plane is a valid reference in a photo that had none — which is
  // the only thing that makes an unreferenced shot (the part laid out on the ground, say)
  // measurable at all. Excludes the view currently open, so a view cannot scale off itself.
  // What actually makes a photo measurable, in the order the errors matter. Every number here
  // is measured, not estimated — against a virtual camera with a known pose for the geometry
  // (tests/synthetic_camera.py) and against the real pipeline for the rest. They are stated
  // because "hold it flat" is advice nobody can act on, while "a 20 mm-thick part reads 5 mm
  // long on a 100 mm dimension" tells someone exactly what it costs to get it wrong.
  var CAPTURE_RULES = [
    { t:'Put the reference ON the face you are measuring',
      d:'This is the single biggest error, and it is free to avoid. A face raised above the ' +
        'card is magnified by the camera: +2.6% at 10 mm up, +5.3% at 20 mm, +11.1% at 40 mm ' +
        '(at arm’s length). It does NOT go away in a square-on photo. Rest the card on the ' +
        'face itself and the error is zero.' },
    { t:'Use a reference at least a fifth as long as the part',
      d:'Scale error multiplies by how far you extrapolate. A 40 mm card on a 1.5 m running ' +
        'board is a thirty-eighth of the span, and a pixel of error on the card becomes ' +
        'centimetres on the board. A steel rule, a sheet of A4, a tape measure — anything ' +
        'big and rigid beats a small card.' },
    { t:'A ruler line plus Square up corrects tilt along the ruler; a rectangle corrects it in every direction',
      d:'A line on its own scales everything uniformly, so at 40° of camera tilt the same photo ' +
        'reads −22% across the part and −50% along it. Tidy ▾ → ⊞ Square up then reads the tilt ' +
        'from the ruler’s parallel edges (or the part’s long sides) and corrects it along the ruler. ' +
        'Four corners of something you know the size of (a sheet of paper, the marker) undo the ' +
        'camera angle in every direction: a length in that plane measures within about 1% at 20° ' +
        'of tilt and 2% at 40°.' },
    { t:'Stand back and zoom in, rather than moving in close',
      d:'Depth error scales as height over distance, so the same 20 mm-thick part reads +8.7% ' +
        'from 250 mm away and +3.4% from 600 mm. Just keep the reference big enough in frame ' +
        'to be found.' },
    { t:'Get the whole part in the frame, with background around it',
      d:'The outline tracer needs to see where the part ends. If it runs out of frame there is ' +
        'no edge to find, and a part photographed against something its own colour — a dark ' +
        'part on its own shadow — needs help: draw a ▭ Area box snugly round the part, then tap ' +
        'the part, and nothing outside the box can be traced; or tap ✖ Not the part on the shadow.' },
    { t:'Shoot the same part from more than one side',
      d:'One photo gives an outline; thickness and profile come from the second and third. ' +
        'Measuring one dimension in two views also lets CamScan check the scales against each ' +
        'other, which is the only way a wrong scale ever shows itself.' }
  ];

  // Collapsed by default: it is reference material, and the shot list above is the thing most
  // visits need. Open state is remembered so someone working through it is not re-collapsed
  // every time the panel re-renders.
  var _guideOpen = false;
  function captureGuide(){
    var wrap = document.createElement('div');
    wrap.style.cssText = 'margin-top:9px;';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.style.cssText = 'background:none;border:0;color:#7fb0c4;font-size:12.5px;cursor:pointer;' +
      'padding:0;text-decoration:underline;';
    var body = document.createElement('div');
    body.style.cssText = 'font-size:12.5px;color:#9fb2bb;line-height:1.5;margin-top:7px;' +
      'border-left:2px solid #22303a;padding-left:10px;';
    CAPTURE_RULES.forEach(function(r){
      var row = document.createElement('div');
      row.style.cssText = 'margin:0 0 8px;';
      row.innerHTML = '<b style="color:#cfe3ea;">' + esc(r.t) + '</b><br>' + esc(r.d);
      body.appendChild(row);
    });
    function reflect(){
      btn.textContent = (_guideOpen ? '▾ ' : '▸ ') + 'What makes a photo measurable';
      body.style.display = _guideOpen ? 'block' : 'none';
    }
    btn.onclick = function(){ _guideOpen = !_guideOpen; reflect(); };
    reflect();
    wrap.appendChild(btn); wrap.appendChild(body);
    return wrap;
  }

  // ---- 3D part: outlines assigned to views (Top / Side / End / Cavity), across photos ----
  // Kept in the job so it survives the page reload between photos, and is cleared with it.
  // Each entry carries the photo it came from: a cavity traced on the SAME photo as its view
  // keeps its exact position relative to that view's outline when the part is built.
  function part(){
    if(!job.part3d || typeof job.part3d !== 'object') job.part3d = { views:{}, opts:{} };
    if(!job.part3d.views) job.part3d.views = {};
    if(!job.part3d.opts) job.part3d.opts = {};
    return job.part3d;
  }
  function photoId(){
    // A REOPENED side is identified by its job entry. Its rebuilt .cal-view carries an inline
    // data: URL of its calibration rather than an upload id, and that is identical for any two
    // photos of the same size and calibration — two ruler-scaled photos from one phone, say —
    // which made a cavity from one photo look "traced on the same photo" as an outline from
    // another (caught by tests/test_part3d_e2e.py). The key is stored on the entry, because
    // positions shift when a view is removed.
    var oi = currentOpenIndex();
    if(oi != null && job.views[oi]){
      var v = job.views[oi];
      if(v.sourceId && String(v.sourceId).indexOf('data:') !== 0) return v.sourceId;
      if(!v.photoKey){ v.photoKey = 'photo-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8); save(); }
      return v.photoKey;
    }
    var sid = currentSourceId();
    if(sid && sid.indexOf('data:') !== 0) return sid;     // a fresh upload: its unique upload id
    var o = overlay(), src = o && o.img && String(o.img.src || '');
    return src ? 'img-' + src.length + '-' + src.slice(-48) : 'unknown';
  }
  function setPartView(role, prof){
    if(!prof || !Array.isArray(prof.points)) return false;
    var p = part(), here = photoId(), old = p.views[role];
    // Rotate and flip line a view up with the others, and they describe how the part lies in
    // ITS photo. A new outline from the same photo — a re-trace, or an outline deleted and
    // drawn again, which has to be assigned again — lies the same way, so it keeps them (and
    // the side's "covers"); re-aligning three views by hand after every re-trace was the cost.
    // An outline from another photo starts square. A Match factor ("Scale Top to match End")
    // belongs to the photo too: assigning Top again on the same outline used to drop it, and
    // the next preview went back to the size-mismatch refusal with nothing said.
    var same = !!(old && old.source === here);
    var rec = { points: prof.points, mm_per_px: prof.mm_per_px, image_height: prof.image_height,
                size_mm: prof.size_mm, source: here, at: new Date().toISOString(),
                ann_id: (prof.ann_id != null ? prof.ann_id : null),   // the outline it follows
                rot: same ? (old.rot || 0) : 0, flip_h: same ? !!old.flip_h : false, flip_v: same ? !!old.flip_v : false };
    if(same && old.covers) rec.covers = old.covers;
    if(same && Number(old.scale) > 0){
      rec.scale = old.scale;
      if(old.scale_note) rec.scale_note = old.scale_note;
      if(old.scale_undo != null) rec.scale_undo = old.scale_undo;
    }
    p.views[role] = rec;
    save(); changed();
    return true;
  }
  function clearPartView(role){ delete part().views[role]; save(); changed(); }

  // Views reduced to what the agreement check needs: did this view get a real scale, and
  // what did it measure? Kept separate from the DOM so the rule itself stays testable.
  function viewsForAgreement(){
    return job.views.map(function(v, i){
      return { label: v.label || ('View ' + (i + 1)),
               calibrated: !!(v.scale && v.scale.calibrated),
               measurements: (v.measurements || []).map(function(m){
                 return { label: m.label, mm: m.mm };
               }) };
    });
  }

  // Dimensions measured in more than one view that do not agree, worst first. Empty when the
  // rule module has not loaded — script order is not guaranteed, and a missing warning is far
  // better than a panel that will not open.
  function scaleWarnings(){
    var G = window.CalibGeom;
    if(!G || typeof G.crossViewDisagreements !== 'function') return [];
    try{ return G.crossViewDisagreements(viewsForAgreement()) || []; }catch(e){ return []; }
  }

  // One row per disagreeing dimension, naming both readings and both views, because "these
  // disagree" is not actionable on its own — the user needs to know WHICH side to re-scale.
  function scaleDisagreementRows(){
    return scaleWarnings().map(function(h){
      var bad = h.level === 'bad';
      var el = document.createElement('div');
      el.style.cssText = 'margin:2px 0 7px;padding:9px 11px;border-radius:9px;font-size:12.5px;line-height:1.45;' +
        (bad ? 'background:#2b1a16;border:1px solid #6b3a2c;color:#f0b9a6;'
             : 'background:#2a2416;border:1px solid #5d4a22;color:#e8cf9a;');
      el.innerHTML = '<b>' + (bad ? 'Scales disagree' : 'Check these two') + '</b> — “' + esc(h.label) +
        '” measures <b>' + h.low.mm.toFixed(1) + ' mm</b> on ' + esc(h.low.view) +
        ' but <b>' + h.high.mm.toFixed(1) + ' mm</b> on ' + esc(h.high.view) +
        ' (' + Math.round(h.spread * 100) + '% apart). ' +
        (bad ? 'One of those scales is wrong — re-open the side that looks off and set its scale again.'
             : 'Worth a look: a part with thickness reads large when the card is not on the same face.');
      return el;
    });
  }

  function referenceDimensions(){
    var openIdx = currentOpenIndex();
    var sid = currentSourceId();
    var out = [];
    job.views.forEach(function(v, i){
      if(i === openIdx) return;                       // not this view
      if(sid && v.sourceId === sid) return;           // nor the same photo under another index
      // Only from a view that was itself properly scaled — otherwise this just launders a
      // bad number into another photo and makes it look authoritative.
      if(!(v.scale && v.scale.calibrated)) return;
      (v.measurements || []).forEach(function(m){
        if(typeof m.mm === 'number' && isFinite(m.mm) && m.mm > 0){
          out.push({ view: v.label || ('View ' + (i + 1)), label: m.label || 'length', mm: m.mm });
        }
      });
    });
    // Longest first: a big reference extrapolates far better than a small one.
    out.sort(function(a, b){ return b.mm - a.mm; });
    return out;
  }

  // Minimal API so the simple-mode layer can drive submission without duplicating logic.
  window.CalibJob = {
    referenceDimensions: referenceDimensions,
    scaleWarnings: scaleWarnings,   // dimensions that two views disagree about, worst first
    part: part,                     // the 3D part being assembled from views (see calib.part3d.js)
    setPartView: setPartView, photoId: photoId,
    clearPartView: clearPartView,
    savePart: function(){ saveSoon(); },
    addView: addView,
    // Store (or refresh) the photo on screen when anything was done to it; see captureCurrent.
    captureCurrent: captureCurrent,
    // Remove a stored photo; asks first when the part uses its outline. False if cancelled.
    removeView: removeView,
    open: openPanel,
    submit: submit,          // send/hand-off the collected set (used by simple mode + tests)
    count: function(){ return job.views.length; },
    isEmpty: function(){ return !job.views.length; },
    hasCurrent: hasCurrent,  // is the photo on screen already captured into the set?
    // Is the on-screen overlay an already-stored view being re-edited (tracked positionally)?
    // Distinct from hasCurrent, which keys on a fresh upload's sourceId.
    isCurrentStored: function(){ return currentOpenIndex() != null; },
    // The name the photo on screen is stored under, or null when it is not stored.
    currentLabel: function(){ var i=currentOpenIndex(); return (i!=null && job.views[i]) ? (job.views[i].label || null) : null; },
    syncOpen: syncOpenView,  // persist the reopened side's edits in place (no relabel)
    isEmbedded: function(){ return EMBED; },
    // Call before reloading the page: captures the open side and waits (briefly) until the
    // job is durable. A write merely STARTED during unload is not guaranteed to land.
    beforeReload: beforeReload,
    flush: persist,          // resolves once everything saved so far is committed
    ready: null,             // set below: resolves once the stored job is loaded
    // What is actually stored (not what is in memory) — for checks that something persisted.
    persisted: function(){
      if(_idbOK) return idbGet().then(function(r){ return (r && r.ok) ? (r.value || null) : null; });
      try{ return Promise.resolve(JSON.parse(localStorage.getItem(LS_KEY))); }catch(e){ return Promise.resolve(null); }
    },
    clearStored: function(){
      try{ localStorage.removeItem(LS_KEY); }catch(e){}
      return idbDel().then(function(){ return true; });
    },
    storage: function(){ return _idbOK ? 'indexeddb' : 'localstorage'; }
  };

  function beforeReload(){
    // New and a reload used to drop a fresh photo that had a scale, a trace or a 3D view
    // (only a reopened side was synced). Not while the photo-quality warning or the load
    // error is up, though: there the user is throwing the shot away for a better one.
    var discarding = !!(document.getElementById('cal-quality') || document.querySelector('.cal-load-error'));
    try{ if(currentOpenIndex()==null){ if(!discarding) captureCurrent(); } else syncOpenView(); }catch(e){}
    var limit = new Promise(function(res){ setTimeout(res, 2000); });   // never block a reload forever
    return Promise.race([persist(), limit]);
  }

  // Autosave a REOPENED side shortly after each change, so its record is current long before
  // any unload. Not every change goes through the undo history: a dragged dot is pushed
  // straight onto it, and a new scale, Square up, undo and redo never touch it — all of
  // those were saved only by the unload write, which a phone may cut short. Every change
  // does redraw, though: so after the drawing settles, compare what would be stored with
  // what was, and save when it differs. (Hovering redraws too; it changes nothing stored.)
  var _autoTimer = null, _autoSig = null, _autoOverlay = null;
  function _storedSig(o){
    try{
      return JSON.stringify([o.ann && o.ann.items, o.opts && o.opts.manualMmPerPx, o.opts && o.opts.manualHomography,
                             o._manualRef, o._scaleLine, !!o._markerConfirmed, o._refExtentPx,
                             o.data && o.data.marker_size_mm, o.opts && o.opts.units]);
    }catch(e){ return null; }
  }
  //
  // A FRESH photo is stored the same way on its first change the user made — a scale or a
  // mark (hasContent with manualOnly) — so a reload, a crash or the phone dropping the tab
  // never takes a scaled, traced photo with it; from then on it is autosaved like any side.
  function _autoCheck(){
    _autoTimer = null;
    var o = overlay();
    if(!o) return;
    var sig = _storedSig(o);
    if(o !== _autoOverlay){ _autoOverlay = o; _autoSig = sig; return; }   // just opened: nothing changed yet
    if(sig === _autoSig) return;
    _autoSig = sig;
    try{
      if(currentOpenIndex() != null) syncOpenView();
      else if(hasContent(o, true)) captureCurrent();
    }catch(e){}
  }
  function hookAutosave(){
    var o = overlay();
    if(!o || o.__jobAutosave || typeof o.requestDraw !== 'function') return;
    var orig = o.requestDraw.bind(o);
    o.requestDraw = function(){
      var r = orig.apply(null, arguments);
      if(_autoTimer) clearTimeout(_autoTimer);
      _autoTimer = setTimeout(_autoCheck, 1200);
      return r;
    };
    o.__jobAutosave = true;
    _autoOverlay = null;
    setTimeout(_autoCheck, 0);   // take the as-opened state as the baseline
  }

  // Boot: add the button, keep its state in sync as the viewer appears/disappears.
  function boot(){
    render();   // reflectButton + ensureLandingLoad + renderSideSwitcher (all signature-guarded)
    hookAutosave();
    new MutationObserver(function(){ render(); hookAutosave(); }).observe(document.documentElement, {childList:true, subtree:true});
    // Showing another photo can be a style change, which the observer above does not see;
    // the Submit button must follow it into that photo's top bar.
    setInterval(reflectButton, 1000);
    // The top-bar 'New' button reloads the page; syncOpenView otherwise runs only on
    // switch/save/submit, so flush the open reopened side back into the job here too — else a
    // reload rehydrates it from its pre-edit snapshot and the new marks are silently lost.
    window.addEventListener('pagehide', function(){ try{ syncOpenView(); }catch(e){} try{ flushSave(); }catch(e){} });
  }
  // Load the stored job first, then build the UI from it.
  var _ready = hydrate();
  window.CalibJob.ready = _ready;
  function start(){ _ready.then(boot, boot); }
  if(document.readyState!=='loading') start(); else document.addEventListener('DOMContentLoaded', start);
})();
