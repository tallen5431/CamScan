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

  function addView(label){
    var o=overlay();
    if(!o || !o.img){ setStatus('Load and measure a photo first, then add it.', true); return; }
    var rec;
    try{ rec=o.getViewRecord(label); }catch(e){ setStatus('Could not capture this view: '+e.message, true); return; }
    rec.capturedAt=new Date().toISOString();
    job.views.push(rec); save(); changed();
    setStatus('✓ Added “'+label+'”. Tap ⬅ New to add another view, or Send when you\'re done.');
  }
  function removeView(i){ job.views.splice(i,1); save(); changed(); }

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
        var del=chip('Remove', function(){ removeView(i); openPanel(); }, false);
        del.style.minHeight='36px'; del.style.padding='5px 10px';
        vr.appendChild(im); vr.appendChild(meta); vr.appendChild(del); card.appendChild(vr);
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
    card.appendChild(field('Your email (so we can reply)', 'contact', {type:'email', placeholder:'you@example.com'}));
    card.appendChild(field('Anything else', 'notes', {area:true, placeholder:'tolerances, finish, deadline…'}));

    // actions
    var actions=document.createElement('div'); actions.style.cssText='display:flex;flex-wrap:wrap;gap:9px;margin-top:16px;align-items:center;';
    var send=chip('Send to Datum', submit, true); send.style.minHeight='46px'; send.style.padding='10px 20px'; send.style.fontSize='15px';
    var dl=chip('Download summary', downloadSummary, false); dl.style.minHeight='46px';
    var clr=chip('Clear', function(){ if(confirm('Discard this submission and all collected views?')){ reset(); changed(); openPanel(); } }, false);
    clr.style.minHeight='46px'; clr.style.marginLeft='auto';
    actions.appendChild(send); actions.appendChild(dl); actions.appendChild(clr);
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
    var email=(job.brief.contact||'').trim();
    if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)){ setStatus('Please add your email so we can reply.', true); return false; }
    return true;
  }

  function submit(){
    if(!validate()) return;
    setStatus('Sending…');
    var payload={ id:job.id, createdAt:job.createdAt, submittedAt:new Date().toISOString(),
      brief:job.brief,
      views:job.views.map(function(v){ return { label:v.label, image:v.image, scale:v.scale,
        measurements:v.measurements, units:v.units, capturedAt:v.capturedAt }; }),
      userAgent:navigator.userAgent };
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

  function render(){ reflectButton(); }
  // Refresh the button AND notify other layers (the simple-mode card) that the job changed.
  function changed(){ reflectButton(); try{ document.dispatchEvent(new CustomEvent('camscan-job-changed')); }catch(e){} }

  // Minimal API so the simple-mode layer can drive submission without duplicating logic.
  window.CalibJob = {
    addView: addView,
    open: openPanel,
    count: function(){ return job.views.length; },
    isEmpty: function(){ return !job.views.length; }
  };

  // Boot: add the button, keep its state in sync as the viewer appears/disappears.
  function boot(){
    reflectButton();
    new MutationObserver(function(){ reflectButton(); }).observe(document.documentElement, {childList:true, subtree:true});
  }
  if(document.readyState!=='loading') boot(); else document.addEventListener('DOMContentLoaded', boot);
})();
