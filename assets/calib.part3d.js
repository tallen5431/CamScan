// CamScan — build a 3D part from several traced views.
//
// One traced outline pushed through a depth is the whole part only when the shape is the same
// all the way along. A part like a running-board step pad is not: its toe is chamfered (seen
// from the side), its end is rounded (seen from the end), and its inside must fit what it
// covers. So outlines are assigned to views — Top, Side, End, and the Cavity — from whichever
// photos show them best, and the server (part_builder.py) keeps only what every view agrees is
// material, then cuts the cavity out with a fit clearance.
//
// The views live in the job (CalibJob.part()), so they survive the reload between photos.
// Nothing here measures: each outline arrives exactly as the DXF/STL exports produce it.
(function(){
  'use strict';
  function overlay(){ var el = document.querySelector('.cal-view'); return el && el.__overlay; }
  function J(){ return window.CalibJob; }
  function esc(s){ return String(s == null ? '' : s).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
  function apiUrl(path){
    var base = '/';
    try{ var cfg = JSON.parse(document.getElementById('_dash-config').textContent); base = cfg.requests_pathname_prefix || '/'; }catch(e){}
    if(base.charAt(base.length - 1) !== '/') base += '/';
    return base + path;
  }
  function toast(msg, ms, action){
    var o = overlay();
    if(o && o._toast) o._toast(msg, { autoMs: ms || 4000, action: action });
  }
  // Roles that already hold an outline, in order.
  function assigned(){
    var job = J(); if(!job || !job.part) return [];
    var p = job.part();
    return ['top', 'side', 'end', 'cavity'].filter(function(r){ return p.views[r]; });
  }

  var ROLES = [
    ['top',    'Top',    'outline from above — length × width'],
    ['side',   'Side',   'profile from the side — length × height'],
    ['end',    'End',    'cross-section from the end — width × height'],
    ['cavity', 'Cavity', 'the inside — what it fits over'],
  ];
  var LABEL = { top:'Top', side:'Side', end:'End', cavity:'Cavity' };

  // ---- assigning the selected outline to a view -----------------------------------------
  function assign(role){
    var o = overlay(), job = J();
    if(!o || !job) return false;
    if(o._confirmUncalibrated && !o._confirmUncalibrated()) return false;
    var prof = o.partProfile && o.partProfile();
    if(!prof){ toast('Select a closed outline first, then choose its view.'); return false; }
    job.setPartView(role, prof);
    var p = job.part();
    if(role === 'cavity' && !p.opts.cavity_view) p.opts.cavity_view = 'end';
    job.savePart();
    var w = prof.size_mm ? prof.size_mm[0].toFixed(1) : '?', h = prof.size_mm ? prof.size_mm[1].toFixed(1) : '?';
    var have = ['top', 'side', 'end', 'cavity'].filter(function(r){ return p.views[r]; }).map(function(r){ return LABEL[r]; });
    toast('Saved as the ' + LABEL[role] + ' view (' + w + ' × ' + h + ' mm). 3D part views: ' + have.join(', ') + '.',
          8000, { label: '🧩 Build 3D part', fn: open });
    var panel = document.getElementById('cal-part-panel');
    if(panel) render();
    return true;
  }

  function pick(anchor){
    var prev = document.getElementById('cal-part-pick'); if(prev){ prev.remove(); return; }
    var box = document.createElement('div'); box.id = 'cal-part-pick';
    box.style.cssText = 'position:fixed;z-index:42;left:50%;top:16%;transform:translateX(-50%);background:#0e0e0e;' +
      'border:2px solid #00d4ff;border-radius:10px;padding:12px 14px;box-shadow:0 8px 24px rgba(0,0,0,.6);' +
      'font:14px Segoe UI,system-ui,sans-serif;color:#eee;max-width:calc(100vw - 16px);';
    var h = document.createElement('div'); h.textContent = 'This outline is the part’s…';
    h.style.cssText = 'font-weight:700;margin-bottom:8px;';
    box.appendChild(h);
    // Say which roles are already filled, and from where: the views live in the job, so ones
    // assigned on another photo — or in an earlier session — are easy to forget, and picking
    // that role again silently replaces them.
    var p = J() && J().part ? J().part() : { views: {} };
    var here = J() && J().photoId ? J().photoId() : null;
    ROLES.forEach(function(r){
      var b = document.createElement('button'); b.type = 'button'; b.setAttribute('data-role', r[0]);
      var cur = p.views[r[0]], note = '';
      if(cur){
        var sz = cur.size_mm ? cur.size_mm[0].toFixed(0) + ' × ' + cur.size_mm[1].toFixed(0) + ' mm' : 'set';
        note = '<br><span style="color:#f3c969;font-size:12px;">✓ already set (' + esc(sz) + ', ' +
          (here && cur.source === here ? 'this photo' : 'another photo') + ') — choosing it replaces that</span>';
      }
      b.innerHTML = '<b>' + esc(r[1]) + '</b> <span style="color:#9aa;font-size:12px;">— ' + esc(r[2]) + '</span>' + note;
      b.style.cssText = 'display:block;width:100%;text-align:left;min-height:42px;margin:4px 0;padding:8px 10px;' +
        'border-radius:8px;background:#151515;color:#eee;border:1px solid #2a3540;cursor:pointer;';
      b.onclick = function(){ box.remove(); assign(r[0]); };
      box.appendChild(b);
    });
    var x = document.createElement('button'); x.type = 'button'; x.textContent = 'Cancel';
    x.style.cssText = 'margin-top:6px;min-height:36px;padding:6px 12px;border-radius:8px;background:none;color:#9aa;border:1px solid #2a3540;cursor:pointer;';
    x.onclick = function(){ box.remove(); };
    box.appendChild(x);
    box.addEventListener('keydown', function(e){ if(e.key === 'Escape'){ e.stopPropagation(); box.remove(); } });
    document.body.appendChild(box);
  }

  // ---- views follow their outlines -------------------------------------------------------
  // A view used to be a copy taken when it was assigned, so Refine, Square up, a dragged dot or
  // a new scale on its photo afterwards never reached the part unless the outline was assigned
  // again — easy to forget, and nothing said so. Now each view remembers its outline; whenever
  // that outline's photo is open, the view is brought up to date from it. If the outline was
  // deleted, the view keeps its last copy and says so.
  function sync(){
    var o = overlay(), job = J();
    if(!o || !o.partProfile || !job || !job.part || !job.photoId) return 0;
    var p = job.part(), here = job.photoId(), n = 0;
    ['top', 'side', 'end', 'cavity'].forEach(function(r){
      var v = p.views[r];
      if(!v || v.ann_id == null || v.source !== here) return;
      var alive = (o.ann && o.ann.items || []).some(function(a){ return a.id === v.ann_id && a.type === 'polyline' && a.closed; });
      if(!alive){ if(!v.orphan){ v.orphan = true; n++; } return; }
      var prof = o.partProfile(v.ann_id);
      if(!prof) return;
      if(JSON.stringify([prof.points, prof.mm_per_px, prof.image_height]) !== JSON.stringify([v.points, v.mm_per_px, v.image_height])){
        v.points = prof.points; v.mm_per_px = prof.mm_per_px; v.image_height = prof.image_height;
        v.size_mm = prof.size_mm; v.updated = new Date().toISOString(); n++;
      }
      if(v.orphan){ delete v.orphan; n++; }
    });
    if(n){
      job.savePart();
      if(document.getElementById('cal-part-panel')) render();
    }
    return n;
  }
  var syncTimer = null;
  function syncSoon(){ if(syncTimer) clearTimeout(syncTimer); syncTimer = setTimeout(function(){ syncTimer = null; sync(); }, 600); }

  // ---- the request ---------------------------------------------------------------------
  function viewSpec(v, role){
    var s = { points: v.points, mm_per_px: v.mm_per_px, image_height: v.image_height,
              rot: v.rot || 0, flip_h: !!v.flip_h, flip_v: !!v.flip_v, source: v.source || '' };
    if(role === 'side' && (v.covers === 'start' || v.covers === 'end')) s.covers = v.covers;
    return s;
  }
  // Give an outline a different role — it was assigned as End and is really the Side — without
  // going back to its photo. Whatever held the target role takes this one's place. Turns and
  // flips meant something in the old role only, so they start again.
  function move(from, to){
    var job = J(); if(!job) return;
    var p = job.part(), a = p.views[from], b = p.views[to];
    if(!a || from === to) return;
    [a, b].forEach(function(v){ if(v){ v.rot = 0; v.flip_h = false; v.flip_v = false; delete v.covers; } });
    p.views[to] = a;
    if(b) p.views[from] = b; else delete p.views[from];
    if(to === 'cavity' && !p.opts.cavity_view) p.opts.cavity_view = 'end';
    job.savePart();
    render();
    try{ document.dispatchEvent(new CustomEvent('camscan-job-changed')); }catch(e){}   // top-bar 🧩 count
  }
  function body(preview){
    sync();
    var p = J().part(), o = p.opts, views = {};
    ['top', 'side', 'end'].forEach(function(r){ if(p.views[r]) views[r] = viewSpec(p.views[r], r); });
    if(p.views.cavity){
      views.cavity = Object.assign(viewSpec(p.views.cavity, 'cavity'), {
        view: o.cavity_view || 'end', align: o.cavity_align || 'bottom',
        open: o.cavity_open || 'through', depth_mm: o.cavity_depth || null });
    }
    return { views: views, depth_mm: o.depth || null,
             clearance_mm: (o.clearance != null && o.clearance !== '') ? o.clearance : 0.4,
             wall_mm: o.wall || null, mirror: !!o.mirror, upside_down: !!o.upside_down, preview: !!preview };
  }

  // ---- the dialog ----------------------------------------------------------------------
  var seq = 0, timer = null;
  function schedulePreview(){ if(timer) clearTimeout(timer); timer = setTimeout(runPreview, 350); }

  function open(){
    var prev = document.getElementById('cal-part-panel'); if(prev) prev.remove();
    sync();                                   // views from this photo: bring them up to date first
    // The trace's Detail slider and the last message sit above everything; with the dialog
    // open they only cover it.
    var ov = overlay();
    if(ov){
      if(ov._dismissTraceDetail) ov._dismissTraceDetail();
    }
    var lt = window.__calLastToast;
    if(lt && lt.parentNode) lt.parentNode.removeChild(lt);
    var back = document.createElement('div'); back.id = 'cal-part-panel';
    back.style.cssText = 'position:fixed;inset:0;z-index:40;background:rgba(0,0,0,.6);display:flex;' +
      'align-items:flex-start;justify-content:center;overflow:auto;padding:18px;box-sizing:border-box;';
    back.addEventListener('click', function(e){ if(e.target === back) close(); });
    back.addEventListener('keydown', function(e){ if(e.key === 'Escape'){ e.stopPropagation(); close(); } });
    var card = document.createElement('div'); card.id = 'cal-part-card';
    card.style.cssText = 'width:min(640px,100%);background:#11161a;border:1px solid #26323b;border-radius:14px;' +
      'box-shadow:0 20px 60px rgba(0,0,0,.6);padding:16px 16px 18px;color:#e8eef2;font:14px Segoe UI,system-ui,sans-serif;';
    back.appendChild(card);
    document.body.appendChild(back);
    render();
  }
  function close(){ var p = document.getElementById('cal-part-panel'); if(p) p.remove(); }

  function field(label, id, value, attrs){
    var wrap = document.createElement('label');
    wrap.style.cssText = 'display:flex;flex-direction:column;gap:3px;font-size:12px;color:#9fb2bb;flex:1 1 120px;';
    wrap.appendChild(document.createTextNode(label));
    var el;
    if(attrs && attrs.options){
      el = document.createElement('select');
      attrs.options.forEach(function(op){ var o = document.createElement('option'); o.value = op[0]; o.textContent = op[1]; el.appendChild(o); });
      el.value = value;
    }else{
      el = document.createElement('input'); el.type = 'number'; el.step = '0.1'; el.min = '0'; el.inputMode = 'decimal';
      el.value = (value == null) ? '' : String(value);
      if(attrs && attrs.placeholder) el.placeholder = attrs.placeholder;
    }
    el.id = id;
    el.style.cssText = 'min-height:38px;padding:6px 8px;background:#0b1013;border:1px solid #29414c;border-radius:8px;color:#eef;font:14px Segoe UI,system-ui,sans-serif;';
    wrap.appendChild(el);
    return { wrap: wrap, el: el };
  }
  function check(label, id, on){
    var wrap = document.createElement('label');
    wrap.style.cssText = 'display:flex;gap:8px;align-items:center;font-size:13px;color:#cde;min-height:32px;';
    var el = document.createElement('input'); el.type = 'checkbox'; el.id = id; el.checked = !!on;
    wrap.appendChild(el); wrap.appendChild(document.createTextNode(label));
    return { wrap: wrap, el: el };
  }

  function render(){
    var card = document.getElementById('cal-part-card'); if(!card) return;
    var job = J(); if(!job) return;
    var p = job.part(), o = p.opts;
    card.innerHTML = '';
    var hd = document.createElement('div'); hd.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:6px;';
    var t = document.createElement('div'); t.textContent = 'Build a 3D part from views'; t.style.cssText = 'font-weight:700;font-size:16px;flex:1;';
    var x = document.createElement('button'); x.type = 'button'; x.textContent = '✕'; x.setAttribute('aria-label', 'Close');
    x.style.cssText = 'min-width:36px;min-height:36px;border-radius:8px;background:none;color:#9aa;border:1px solid #2a3540;cursor:pointer;';
    x.onclick = close;
    hd.append(t, x); card.appendChild(hd);
    var sub = document.createElement('div');
    sub.style.cssText = 'font-size:12.5px;color:#9fb2bb;margin-bottom:10px;line-height:1.45;';
    sub.textContent = 'Each view is a traced outline, from whichever photo shows it best (select an outline, then 🧩 Use in 3D part). ' +
      'The part keeps only what every view agrees is material. Use ⟲ / ⇋ / ⇵ until the outlines below line up.';
    card.appendChild(sub);

    ROLES.forEach(function(r){
      var v = p.views[r[0]];
      var row = document.createElement('div'); row.id = 'cal-part-row-' + r[0];
      row.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:7px 9px;border:1px solid #22303a;border-radius:9px;margin-bottom:6px;';
      var meta = document.createElement('div'); meta.style.cssText = 'flex:1 1 150px;min-width:0;';
      meta.innerHTML = '<b>' + esc(r[1]) + '</b> <span style="font-size:12px;color:' + (v ? '#9fe6c2' : '#7f8f98') + ';">' +
        (v ? ((v.size_mm ? v.size_mm[0].toFixed(1) + ' × ' + v.size_mm[1].toFixed(1) + ' mm' : 'set') +
              ((v.rot || v.flip_h || v.flip_v) ? ' · turned' : '')) : 'not set — ' + esc(r[2])) + '</span>' +
        (v && v.orphan ? '<br><span style="font-size:12px;color:#f3c969;">⚠ its outline was deleted — using the last copy</span>'
         : (v && v.ann_id != null ? '<br><span style="font-size:11.5px;color:#7f9aa5;">follows its outline' +
            (v.updated ? ' · updated ' + esc(new Date(v.updated).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})) : '') + '</span>' : ''));
      row.appendChild(meta);
      if(v){
        [['rot', '⟲', 'Rotate a quarter turn'], ['fliph', '⇋', 'Flip end for end'], ['flipv', '⇵', 'Flip upside down'],
         ['clear', '✕', 'Remove this view']].forEach(function(b){
          var btn = document.createElement('button'); btn.type = 'button'; btn.textContent = b[1]; btn.title = b[2];
          btn.setAttribute('data-act', b[0]); btn.setAttribute('aria-label', b[2] + ' (' + r[1] + ')');
          btn.style.cssText = 'min-width:36px;min-height:36px;border-radius:8px;background:#151515;color:#cde;border:1px solid #2a3540;cursor:pointer;';
          btn.onclick = function(){
            if(b[0] === 'clear'){ job.clearPartView(r[0]); render(); return; }
            if(b[0] === 'rot') v.rot = ((v.rot || 0) + 1) % 4;
            if(b[0] === 'fliph') v.flip_h = !v.flip_h;
            if(b[0] === 'flipv') v.flip_v = !v.flip_v;
            job.savePart(); render();
          };
          row.appendChild(btn);
        });
        var mv = document.createElement('select'); mv.setAttribute('data-act', 'move');
        mv.setAttribute('aria-label', 'Move the ' + r[1] + ' outline to another view');
        mv.title = 'Wrong view? Move this outline to another one';
        mv.style.cssText = 'min-height:36px;max-width:110px;border-radius:8px;background:#151515;color:#cde;border:1px solid #2a3540;';
        var o0 = document.createElement('option'); o0.value = ''; o0.textContent = 'Move to…'; mv.appendChild(o0);
        ROLES.forEach(function(q){
          if(q[0] === r[0]) return;
          var oo = document.createElement('option'); oo.value = q[0];
          oo.textContent = q[1] + (p.views[q[0]] ? ' (swap)' : ''); mv.appendChild(oo);
        });
        mv.onchange = function(){ if(mv.value) move(r[0], mv.value); };
        row.appendChild(mv);
      }
      card.appendChild(row);
      if(v && r[0] === 'side'){
        // A side profile traced of one end only (the toe, photographed close) shapes that end;
        // the rest of the length continues at the height of its cut edge.
        var cv = document.createElement('label');
        cv.style.cssText = 'display:flex;gap:8px;align-items:center;font-size:12.5px;color:#9fb2bb;margin:-2px 0 8px 10px;';
        cv.appendChild(document.createTextNode('This side profile covers'));
        var sel = document.createElement('select'); sel.id = 'cal-part-sidecovers';
        sel.style.cssText = 'min-height:34px;border-radius:8px;background:#0b1013;color:#eef;border:1px solid #29414c;';
        [['all', 'the whole length'], ['start', 'only the left end (as drawn below)'], ['end', 'only the right end (as drawn below)']]
          .forEach(function(op){ var o = document.createElement('option'); o.value = op[0]; o.textContent = op[1]; sel.appendChild(o); });
        sel.value = v.covers || 'all';
        sel.onchange = function(){ if(sel.value === 'all') delete v.covers; else v.covers = sel.value; job.savePart(); schedulePreview(); };
        cv.appendChild(sel);
        card.appendChild(cv);
      }
    });

    var opts = document.createElement('div'); opts.style.cssText = 'display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 4px;';
    var fDepth = field('Depth if a view is missing (mm)', 'cal-part-depth', o.depth, { placeholder: 'from views' });
    var fWall = field('Hollow — wall (mm), open underneath', 'cal-part-wall', o.wall, { placeholder: 'solid' });
    opts.append(fDepth.wrap, fWall.wrap);
    var cav = null;
    if(p.views.cavity){
      cav = {
        view: field('Cavity traced in', 'cal-part-cavview', o.cavity_view || 'end', { options: [['end', 'End view'], ['side', 'Side view'], ['top', 'Top view']] }),
        clr: field('Fit clearance (mm)', 'cal-part-clearance', (o.clearance != null && o.clearance !== '') ? o.clearance : 0.4),
        open: field('Cavity runs', 'cal-part-cavopen', o.cavity_open || 'through', { options: [['through', 'all the way through'], ['min', 'in from the start'], ['max', 'in from the far end']] }),
        dep: field('…this far (mm)', 'cal-part-cavdepth', o.cavity_depth, { placeholder: 'all the way' }),
        align: field('If from another photo, sit it', 'cal-part-cavalign', o.cavity_align || 'bottom', { options: [['bottom', 'at the bottom'], ['center', 'centred'], ['top', 'at the top']] }),
      };
      opts.append(cav.view.wrap, cav.clr.wrap, cav.open.wrap, cav.dep.wrap, cav.align.wrap);
    }
    card.appendChild(opts);
    var flags = document.createElement('div'); flags.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px 16px;margin:4px 0 8px;';
    var cMirror = check('Mirror — the matching part for the other side', 'cal-part-mirror', o.mirror);
    var cUpside = check('Upside down for printing', 'cal-part-upside', o.upside_down);
    flags.append(cMirror.wrap, cUpside.wrap);
    card.appendChild(flags);

    function num(el){ var v = parseFloat(el.value); return (isFinite(v) && v > 0) ? v : null; }
    function sync(){
      o.depth = num(fDepth.el); o.wall = num(fWall.el);
      o.mirror = cMirror.el.checked; o.upside_down = cUpside.el.checked;
      if(cav){
        o.cavity_view = cav.view.el.value; o.cavity_open = cav.open.el.value; o.cavity_align = cav.align.el.value;
        var c = parseFloat(cav.clr.el.value); o.clearance = (isFinite(c) && c >= 0) ? c : 0.4;
        o.cavity_depth = num(cav.dep.el);
      }
      job.savePart(); schedulePreview();
    }
    [fDepth.el, fWall.el, cMirror.el, cUpside.el].concat(cav ? [cav.view.el, cav.clr.el, cav.open.el, cav.dep.el, cav.align.el] : [])
      .forEach(function(el){ el.addEventListener('change', sync); el.addEventListener('input', sync); });

    var pv = document.createElement('div'); pv.id = 'cal-part-preview';
    pv.style.cssText = 'background:#0b1013;border:1px solid #22303a;border-radius:10px;min-height:120px;padding:6px;';
    card.appendChild(pv);
    var st = document.createElement('div'); st.id = 'cal-part-status';
    st.style.cssText = 'font-size:12.5px;margin-top:8px;line-height:1.45;color:#9fb2bb;min-height:18px;';
    card.appendChild(st);
    var acts = document.createElement('div'); acts.style.cssText = 'display:flex;gap:9px;margin-top:10px;';
    var build = document.createElement('button'); build.type = 'button'; build.id = 'cal-part-build';
    build.textContent = '🧊 Download STL';
    build.style.cssText = 'min-height:46px;padding:10px 20px;border-radius:10px;background:#00d4ff;color:#000;border:none;font-weight:700;font-size:15px;cursor:pointer;';
    build.onclick = function(){ download('stl'); };
    acts.appendChild(build);
    // For CAD: a real solid with one face per flat side, which Fusion can edit — an STL comes
    // in as a mesh whose every triangle becomes its own face.
    [['step', '📐 STEP (edit in CAD)', 'A solid body for Fusion / CAD: one face per flat side, ready to edit'],
     ['obj', 'OBJ', 'A mesh with one polygon per flat side, for programs that take meshes']].forEach(function(f){
      var bb = document.createElement('button'); bb.type = 'button'; bb.id = 'cal-part-build-' + f[0];
      bb.textContent = f[1]; bb.title = f[2];
      bb.style.cssText = 'min-height:46px;padding:10px 16px;border-radius:10px;background:#151a1f;color:#cfe6ee;border:1px solid #29414c;font-weight:600;font-size:14px;cursor:pointer;';
      bb.onclick = function(){ download(f[0]); };
      acts.appendChild(bb);
    });
    acts.style.flexWrap = 'wrap';
    card.appendChild(acts);
    schedulePreview();
  }

  function setStatus(html, bad){
    var st = document.getElementById('cal-part-status'); if(!st) return;
    st.innerHTML = html; st.style.color = bad ? '#f0b9a6' : '#9fb2bb';
  }
  function describe(info){
    var lines = [];
    if(info.size_mm) lines.push('Part: <b>' + info.size_mm.map(function(v){ return v.toFixed(1); }).join(' × ') + ' mm</b>' +
      (info.volume_mm3 ? ' · ' + (info.volume_mm3 / 1000).toFixed(1) + ' cm³' : ''));
    (info.fits || []).forEach(function(f){
      if(Math.abs(f.pct) >= 0.05) lines.push('The ' + esc(f.view) + ' view was stretched ' + Math.abs(f.pct).toFixed(1) + '% in ' +
        esc(f.axis) + ' to match.');
    });
    (info.warnings || []).forEach(function(w){ lines.push('⚠️ ' + esc(w)); });
    if(info.note) lines.push(esc(info.note));
    if(info.mirrored) lines.push('Downloads <b>mirrored</b> — the part for the other side. The preview shows it as traced.');
    if(info.upside_down) lines.push('Downloads upside down for printing.');
    return lines.join('<br>');
  }

  function runPreview(){
    var pv = document.getElementById('cal-part-preview'); if(!pv) return;
    var my = ++seq;
    var b = body(true);
    if(!Object.keys(b.views).length){ pv.innerHTML = ''; setStatus('Assign at least one outline to a view first.'); return; }
    fetch(apiUrl('api/export/part'), { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b) })
      .then(function(r){ return r.json().catch(function(){ return { ok:false, error:'The server could not build a preview.' }; }); })
      .then(function(j){
        if(my !== seq) return;                      // a newer preview is on its way
        var btn = document.getElementById('cal-part-build');
        if(!j || !j.ok){ pv.innerHTML = ''; setStatus(esc((j && j.error) || 'Could not build the part.'), true); if(btn) btn.disabled = true; return; }
        if(btn) btn.disabled = false;
        drawPreview(pv, j.preview);
        setStatus(describe(j.info || {}), (j.info && j.info.warnings && j.info.warnings.length));
      })
      .catch(function(e){ if(my === seq) setStatus('Preview needs the server (' + esc(e.message) + ').', true); });
  }

  // Three-view drawing, laid out like an engineering drawing: Top above Side (sharing the
  // length axis), End beside Side (sharing the height axis). Filled = the part that will be
  // built; blue line = the traced outline; dashed orange = the cavity.
  function drawPreview(host, pv){
    var vws = (pv && pv.views) || {};
    function bbox(polys){
      var lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
      (polys || []).forEach(function(poly){ (poly || []).forEach(function(q){
        lo[0] = Math.min(lo[0], q[0]); lo[1] = Math.min(lo[1], q[1]); hi[0] = Math.max(hi[0], q[0]); hi[1] = Math.max(hi[1], q[1]); }); });
      return isFinite(lo[0]) ? [lo, hi] : null;
    }
    var size = pv.size_mm;
    if(!size){
      var bt = bbox((vws.top || {}).result), bs = bbox((vws.side || {}).result);
      size = [bt ? bt[1][0] : 1, bt ? bt[1][1] : 1, bs ? bs[1][1] : 1];
    }
    var X = size[0] || 1, Y = size[1] || 1, Z = size[2] || 1, gap = 0.12 * Math.max(X, Y, Z);
    var panels = { top: [0, 0, X, Y], side: [0, Y + gap, X, Z], end: [X + gap, Y + gap, Y, Z] };
    var W = X + gap + Y, H = Y + gap + Z, pad = 18;
    var k = Math.min((host.clientWidth || 600) - 2 * pad, 600) / W;
    k = Math.min(k, 380 / H);
    var svgW = W * k + 2 * pad, svgH = H * k + 2 * pad + 14;
    function path(poly, pnl){
      return (poly || []).map(function(q, i){
        var sx = pad + (pnl[0] + q[0]) * k, sy = pad + 14 + (pnl[1] + pnl[3] - q[1]) * k;
        return (i ? 'L' : 'M') + sx.toFixed(1) + ' ' + sy.toFixed(1);
      }).join(' ') + ' Z';
    }
    var out = ['<svg xmlns="http://www.w3.org/2000/svg" width="' + svgW.toFixed(0) + '" height="' + svgH.toFixed(0) + '" viewBox="0 0 ' + svgW.toFixed(0) + ' ' + svgH.toFixed(0) + '" style="max-width:100%;height:auto;display:block;margin:auto;">'];
    ['top', 'side', 'end'].forEach(function(r){
      var pnl = panels[r], v = vws[r] || {};
      out.push('<text x="' + (pad + pnl[0] * k).toFixed(1) + '" y="' + (pad + 10 + pnl[1] * k).toFixed(1) + '" fill="#7f8f98" font-size="11" font-family="Segoe UI,system-ui,sans-serif">' +
        LABEL[r] + (v.outline ? '' : ' (not traced)') + '</text>');
      if(v.result && v.result.length){
        out.push('<path data-view="' + r + '" d="' + v.result.map(function(poly){ return path(poly, pnl); }).join(' ') +
          '" fill="#3a5563" fill-rule="evenodd" stroke="#6e8c99" stroke-width="1"/>');
      }
      if(v.outline) out.push('<path d="' + path(v.outline, pnl) + '" fill="none" stroke="#00d4ff" stroke-width="1.5"/>');
    });
    if(pv.cavity && pv.cavity.outline && panels[pv.cavity.view]){
      var pc = panels[pv.cavity.view];
      out.push('<path d="' + pv.cavity.outline.map(function(poly){ return path(poly, pc); }).join(' ') +
        '" fill="none" stroke="#ff9f43" stroke-width="1.5" stroke-dasharray="5 3"/>');
    }
    out.push('</svg>');
    host.innerHTML = out.join('');
  }

  function download(fmt){
    var b = body(false);
    if(fmt === 'step' || fmt === 'obj') b.format = fmt;
    if(!Object.keys(b.views).length){ setStatus('Assign at least one outline to a view first.', true); return; }
    setStatus('Building…');
    fetch(apiUrl('api/export/part'), { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b) })
      .then(function(r){
        if(!r.ok) return r.text().then(function(t){ throw new Error(t || ('HTTP ' + r.status)); });
        var info = {};
        try{ info = JSON.parse(r.headers.get('X-CamScan-Info') || '{}'); }catch(e){}
        info.volume_mm3 = Number(r.headers.get('X-CamScan-Volume-MM3')) || null;
        var cd = r.headers.get('Content-Disposition') || '', m = /filename=([^;]+)/.exec(cd);
        var name = m ? m[1].trim() : 'part.stl';
        return r.blob().then(function(blob){
          var a = document.createElement('a'); a.download = name; a.href = URL.createObjectURL(blob); a.click();
          setTimeout(function(){ URL.revokeObjectURL(a.href); }, 1000);
          var tip = fmt === 'step' ? '<br>Open it in Fusion (or any CAD program) as a solid body — each flat side is one face you can press-pull or fillet.'
                  : fmt === 'obj' ? '<br>In Fusion: Insert Mesh, then Convert Mesh — each flat side comes in as one face. STEP needs no converting.' : '';
          setStatus('✓ Saved ' + esc(name) + tip + '<br>' + describe(info));
        });
      })
      .catch(function(e){ setStatus(esc(e.message), true); });
  }

  window.CalibPart3D = { open: open, pick: pick, assign: assign, body: body, assigned: assigned, move: move,
                         sync: sync, syncSoon: syncSoon };
})();
