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
  // `action` is one {label, fn} or a list of them (the first is highlighted).
  function toast(msg, ms, action){
    var o = overlay();
    if(!o || !o._toast) return null;
    return o._toast(msg, Array.isArray(action) ? { autoMs: ms || 4000, actions: action } : { autoMs: ms || 4000, action: action });
  }
  // A finger, not a mouse: the 3D view's gestures and hints differ (a one-finger vertical
  // swipe has to scroll the dialog on a phone, not tip the part over).
  function coarse(){ try{ return window.matchMedia('(pointer: coarse)').matches; }catch(e){ return false; } }
  // Roles that already hold an outline, in order.
  function assigned(){
    var job = J(); if(!job || !job.part) return [];
    var p = job.part();
    return ['top', 'side', 'end', 'cavity'].filter(function(r){ return p.views[r]; });
  }

  // [role, name, what it measures, how to take its photo]. The tip is there because none of
  // one customer's six extra photos was a usable Side view — all oblique — and nothing said
  // what one looks like until the build refused it.
  var ROLES = [
    ['top',    'Top',    'outline from above — length × width', 'Camera straight down over the middle, ruler lying on the part.'],
    ['side',   'Side',   'profile from the side — length × height', 'Camera level with the part and square to its length, ruler against the side face.'],
    ['end',    'End',    'cross-section from the end — width × height', 'Camera level, looking straight along the part, ruler against the end face (not below it).'],
    ['cavity', 'Cavity', 'the inside — what it fits over', 'Straight on to the opening, ruler in the same plane as its edge.'],
  ];
  var LABEL = { top:'Top', side:'Side', end:'End', cavity:'Cavity' };
  // Which of the part's sizes each view shows, in its own (across, up) order — as part_builder's AXES.
  var AXES = { top: ['length', 'width'], side: ['length', 'height'], end: ['width', 'height'] };
  // An outline this far off the sizes the other views already set is more likely the wrong
  // photo (an oblique top shot assigned as Side measured 76% short) than a scale slip, which
  // runs to about 12% for a ruler held below the face.
  var ASSIGN_TOLERANCE = 0.25;
  // Past this, the dialog's Match buttons are not offered: rescaling a photo by a third hides
  // a wrong photo rather than fixing a scale.
  var MATCH_LIMIT = 0.30;
  var UNSCALED = 'this photo has no scale right now — using the last scaled copy';

  // A view's two sizes along its role's AXES, as the server will lay it out: a Top or Side of a
  // long part is laid along its length unless turned, a quarter turn swaps them, and a Match
  // scale multiplies both.
  function viewSize(v, role){
    if(!v || !v.size_mm) return null;
    var s = Number(v.scale) > 0 ? Number(v.scale) : 1, a = v.size_mm[0] * s, b = v.size_mm[1] * s;
    var turns = ((role === 'top' || role === 'side') && b > a ? 1 : 0) + (v.rot || 0);
    return (turns % 2) ? [b, a] : [a, b];
  }
  // The worst disagreement between sizes `sz` for `role` and the views already set — the
  // same comparison the build makes, at assign time while the photo is still on screen.
  function sizeCheck(role, sz, views){
    var worst = null;
    ['top', 'side', 'end'].forEach(function(r){
      if(r === role || !views[r] || !AXES[role]) return;
      var o = viewSize(views[r], r); if(!o) return;
      AXES[role].forEach(function(ax, k){
        var j = AXES[r].indexOf(ax); if(j < 0 || !(o[j] > 0)) return;
        var off = sz[k] / o[j] - 1;
        if(!worst || Math.abs(off) > Math.abs(worst.off)) worst = { axis: ax, have: sz[k], want: o[j], other: r, off: off };
      });
    });
    return worst;
  }
  function within(w){ return !w || Math.abs(w.off) <= ASSIGN_TOLERANCE; }
  // Why an outline does not fit as `role`, with what it probably is instead; null when it fits.
  function assignWarning(role, prof, views){
    if(!AXES[role] || !prof.size_mm) return null;
    var sz = viewSize({ size_mm: prof.size_mm }, role), w = sizeCheck(role, sz, views);
    if(within(w)) return null;
    var msg = 'This outline’s ' + w.axis + ' (' + w.have.toFixed(0) + ' mm) is ' + Math.round(Math.abs(w.off) * 100) + '% ' +
      (w.off > 0 ? 'longer' : 'shorter') + ' than the ' + LABEL[w.other] + ' view’s (' + w.want.toFixed(0) + ' mm). ';
    // The same size as a view already set, both ways: most likely another photo of that face.
    var same = null;
    ['top', 'side', 'end'].some(function(c){
      var cur = views[c], vc = cur && viewSize(cur, c);
      if(c === role || !vc) return false;
      var szc = viewSize({ size_mm: prof.size_mm }, c);
      if(Math.max(Math.abs(szc[0] / vc[0] - 1), Math.abs(szc[1] / vc[1] - 1)) <= ASSIGN_TOLERANCE) same = c;
      return !!same;
    });
    if(same) return msg + 'It is the size of your ' + LABEL[same] + ' view — this looks like a ' + same + ' view, not a ' + role + ' view.';
    if(within(sizeCheck(role, [sz[1], sz[0]], views))){
      return msg + 'It matches turned a quarter: save it, then press ⟲ on its row in Build 3D part.';
    }
    // An empty role it would fit without complaint.
    var better = null;
    ['top', 'side', 'end'].some(function(c){
      if(c === role || views[c]) return false;
      var wc = sizeCheck(c, viewSize({ size_mm: prof.size_mm }, c), views);
      if(wc && within(wc)) better = 'It fits as the ' + LABEL[c] + ' view instead.';
      return !!better;
    });
    return msg + (better || ('It may not be a straight-on ' + role + ' view (taken at an angle, or a different face).'));
  }
  // What the part still needs, by size: a Top alone has no height, and saying which photo
  // gives it is the next step.
  function missingNote(views){
    var by = { length: ['top', 'side'], width: ['top', 'end'], height: ['side', 'end'] }, out = [];
    ['length', 'width', 'height'].forEach(function(ax){
      var src = by[ax];
      if(views[src[0]] || views[src[1]]) return;
      out.push('Add a ' + LABEL[src[0]] + ' or ' + LABEL[src[1]] + ' photo for the ' + ax +
               (ax === 'height' ? ', or type the height in Build 3D part' : ''));
    });
    return out.length ? out.join('; ') + '.' : 'Every size has a view.';
  }

  // The photo an outline came from goes into the job too: a view whose photo is not stored
  // cannot be opened to fix after a reload — or after a phone drops the tab while the camera
  // is open, which is when the next photo gets taken.
  function storePhoto(role){
    var job = J(); if(!job) return;
    try{
      if(typeof job.captureCurrent === 'function'){ job.captureCurrent(LABEL[role]); return; }
      if(job.isCurrentStored && job.isCurrentStored()){ if(job.syncOpen) job.syncOpen(); return; }
      if(!job.addView) return;
      // Already stored from an earlier role (a cavity on the End photo): keep that label.
      var label = LABEL[role];
      if(job.hasCurrent && job.hasCurrent() && job.photoId){
        var here = job.photoId(), views = job.part().views;
        ['top', 'side', 'end'].some(function(r){
          if(r !== role && views[r] && views[r].source === here){ label = LABEL[r]; return true; }
          return false;
        });
      }
      job.addView(label);
    }catch(e){ /* best effort: the view itself is saved either way */ }
  }

  // ---- assigning the selected outline to a view -----------------------------------------
  // `force`: the user saw the size warning and chose Save anyway.
  function assign(role, force){
    var o = overlay(), job = J();
    if(!o || !job) return false;
    if(!force && o._confirmUncalibrated && !o._confirmUncalibrated()) return false;
    var prof = o.partProfile && o.partProfile();
    if(!prof){
      toast(o.isCalibrated && !o.isCalibrated() ? 'Set this photo’s scale first, then choose the outline’s view.'
                                                : 'Select a closed outline first, then choose its view.');
      return false;
    }
    var p = job.part();
    if(!force){
      var warn = assignWarning(role, prof, p.views);
      if(warn){
        toast(warn, 30000, [{ label: 'Save anyway', fn: function(){ assign(role, true); } },
                            { label: 'Cancel', fn: function(){} }]);
        return false;
      }
    }
    storePhoto(role);
    job.setPartView(role, prof);
    p = job.part();
    if(role === 'cavity' && !p.opts.cavity_view) p.opts.cavity_view = 'end';
    job.savePart();
    // Larger first, whichever way the photo was held: "160 × 134", never "134 × 160".
    var s = prof.size_mm ? prof.size_mm.slice().sort(function(a, b){ return b - a; }) : null;
    var have = ['top', 'side', 'end', 'cavity'].filter(function(r){ return p.views[r]; }).map(function(r){ return LABEL[r]; });
    toast('Saved as the ' + LABEL[role] + ' view (' + (s ? s[0].toFixed(1) + ' × ' + s[1].toFixed(1) : '?') + ' mm). ' +
          '3D part views: ' + have.join(', ') + '. ' + missingNote(p.views),
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
      b.innerHTML = '<b>' + esc(r[1]) + '</b> <span style="color:#9aa;font-size:12px;">— ' + esc(r[2]) + '</span>' +
        '<br><span class="cal-part-tip" style="color:#7f9aa5;font-size:11.5px;">📷 ' + esc(r[3]) + '</span>' + note;
      b.style.cssText = 'display:block;width:100%;text-align:left;min-height:44px;margin:4px 0;padding:8px 10px;' +
        'border-radius:8px;background:#151515;color:#eee;border:1px solid #2a3540;cursor:pointer;';
      b.onclick = function(){ box.remove(); assign(r[0]); };
      box.appendChild(b);
    });
    var x = document.createElement('button'); x.type = 'button'; x.textContent = 'Cancel';
    x.style.cssText = 'margin-top:6px;min-height:44px;padding:6px 12px;border-radius:8px;background:none;color:#9aa;border:1px solid #2a3540;cursor:pointer;';
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
    // A photo whose scale was cleared (to set it again, say) measures in pixels: copying that
    // over its view turned a 200 mm Top into 400 mm about a second after Clear, with nothing
    // said. Keep the last scaled copy and flag it until the photo has a scale again.
    // isCalibrated, as assign's check uses: a photo with no target still carries the server's
    // rough data.mm_per_px (0.16 on a plain test photo), which is not a scale anyone set.
    var noScale = !!(o.isCalibrated && !o.isCalibrated());
    ['top', 'side', 'end', 'cavity'].forEach(function(r){
      var v = p.views[r];
      if(!v || v.ann_id == null || v.source !== here || v.photo_removed) return;
      var alive = (o.ann && o.ann.items || []).some(function(a){ return a.id === v.ann_id && a.type === 'polyline' && a.closed; });
      if(!alive){ if(!v.orphan){ v.orphan = true; n++; } return; }
      var prof = noScale ? null : o.partProfile(v.ann_id);
      if(!prof){
        if(noScale && v.unscaled !== UNSCALED){ v.unscaled = UNSCALED; n++; }
        return;
      }
      if(JSON.stringify([prof.points, prof.mm_per_px, prof.image_height]) !== JSON.stringify([v.points, v.mm_per_px, v.image_height])){
        v.points = prof.points; v.mm_per_px = prof.mm_per_px; v.image_height = prof.image_height;
        v.size_mm = prof.size_mm; v.updated = new Date().toISOString(); n++;
      }
      if(v.orphan){ delete v.orphan; n++; }
      if(v.unscaled){ delete v.unscaled; n++; }
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
    // A Match scale goes into the photo's mm per pixel: one photo has one scale, and the
    // server then reads the outline exactly as if the photo had been scaled that way — the
    // same thing a `scale` factor does on a server that takes one, and on one that does not.
    var k = Number(v.scale) > 0 ? Number(v.scale) : 1;
    var s = { points: v.points, mm_per_px: (Number(v.mm_per_px) || 1) * k, image_height: v.image_height,
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
    var out = { views: views, depth_mm: o.depth || null,
                clearance_mm: (o.clearance != null && o.clearance !== '') ? o.clearance : 0.4,
                wall_mm: o.wall || null, mirror: !!o.mirror, upside_down: !!o.upside_down, preview: !!preview };
    // Sizes measured on the part itself, which the views are scaled to.
    var known = {};
    ['length', 'width', 'height'].forEach(function(ax){ var x = o.known && Number(o.known[ax]); if(x > 0) known[ax] = x; });
    if(Object.keys(known).length) out.known_mm = known;
    // Which view the user trusts for a size, where views disagree.
    var src = {};
    Object.keys(o.axis_source || {}).forEach(function(ax){
      var r = o.axis_source[ax];
      if(p.views[r] && AXES[r] && AXES[r].indexOf(ax) >= 0) src[ax] = r;
    });
    if(Object.keys(src).length) out.axis_source = src;
    // The ends' slope and edge radius stand in for a Side view; a traced one shapes them itself.
    if(!p.views.side && o.ends && (o.ends.slope_deg > 0 || o.ends.radius_mm > 0)){
      out.ends = { slope_deg: o.ends.slope_deg || 0, radius_mm: o.ends.radius_mm || 0 };
    }
    return out;
  }

  // "Use End" on a fit line: that view sets the size from now on.
  function useView(axis, role){
    var job = J(); if(!job) return;
    var o = job.part().opts;
    o.axis_source = Object.assign({}, o.axis_source || {});
    o.axis_source[axis] = role;
    job.savePart(); schedulePreview();
  }
  // "Scale End to match Top": the End photo's scale was off (a ruler held below the face reads
  // 5-12% large), so rescale every view from that photo — its cavity too — by `f`, keeping the
  // factor it had before for Undo.
  function matchScale(role, f, note){
    var job = J(); if(!job || !(f > 0)) return;
    var p = job.part(), v = p.views[role]; if(!v) return;
    ['top', 'side', 'end', 'cavity'].forEach(function(r){
      var w = p.views[r];
      if(!w || (w !== v && !(v.source && w.source === v.source))) return;
      var cur = Number(w.scale) > 0 ? Number(w.scale) : 1;
      w.scale_undo = cur;
      w.scale = Math.round(cur * f * 1e5) / 1e5;
      w.scale_note = note;
    });
    job.savePart(); render();
  }
  function undoScale(role){
    var job = J(); if(!job) return;
    var p = job.part(), v = p.views[role]; if(!v) return;
    ['top', 'side', 'end', 'cavity'].forEach(function(r){
      var w = p.views[r];
      if(!w || (w !== v && !(v.source && w.source === v.source)) || w.scale == null) return;
      var back = Number(w.scale_undo) > 0 ? Number(w.scale_undo) : 1;
      delete w.scale_undo;
      if(Math.abs(back - 1) < 1e-6){ delete w.scale; delete w.scale_note; } else w.scale = back;
    });
    job.savePart(); render();
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
    var CT = window.CalibToast;
    if(CT && typeof CT.dismiss === 'function'){ try{ CT.dismiss(); }catch(e){} }
    else{
      var lt = window.__calLastToast;
      if(lt && lt.parentNode) lt.parentNode.removeChild(lt);
    }
    var back = document.createElement('div'); back.id = 'cal-part-panel';
    back.style.cssText = 'position:fixed;inset:0;z-index:40;background:rgba(0,0,0,.6);display:flex;' +
      'align-items:flex-start;justify-content:center;overflow:auto;padding:clamp(6px,3vw,18px);box-sizing:border-box;';
    back.addEventListener('click', function(e){ if(e.target === back) close(); });
    var card = document.createElement('div'); card.id = 'cal-part-card'; card.tabIndex = -1;
    card.setAttribute('role', 'dialog'); card.setAttribute('aria-label', 'Build a 3D part from views');
    card.style.cssText = 'width:min(640px,100%);background:#11161a;border:1px solid #26323b;border-radius:14px;outline:none;' +
      'box-shadow:0 20px 60px rgba(0,0,0,.6);padding:16px 16px 0;color:#e8eef2;font:14px Segoe UI,system-ui,sans-serif;';
    back.appendChild(card);
    document.body.appendChild(back);
    // Escape closes it straight away: focus stayed on the top-bar button that opened it,
    // outside the dialog, so a listener on the dialog never heard the key.
    if(onKey) document.removeEventListener('keydown', onKey, true);
    onKey = function(e){
      if(e.key !== 'Escape' || !document.getElementById('cal-part-panel')) return;
      if(document.getElementById('cal-part-pick')) return;      // the role picker closes first
      e.stopPropagation(); close();
    };
    document.addEventListener('keydown', onKey, true);
    render();
    // One view: straight to the size it is missing. Otherwise the card, so keys reach it.
    var need = document.getElementById('cal-part-onenote');
    var dep = need && document.getElementById('cal-part-depth');
    try{ if(need) need.scrollIntoView({ block: 'nearest' }); (dep || card).focus({ preventScroll: true }); }catch(e){}
  }
  var onKey = null;
  function close(){
    var p = document.getElementById('cal-part-panel'); if(p) p.remove();
    if(onKey){ document.removeEventListener('keydown', onKey, true); onKey = null; }
  }

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
    // The whole row is the target, 44 px tall: a bare checkbox is 13 px under a finger.
    wrap.style.cssText = 'display:flex;gap:8px;align-items:center;font-size:13px;color:#cde;min-height:44px;cursor:pointer;';
    var el = document.createElement('input'); el.type = 'checkbox'; el.id = id; el.checked = !!on;
    el.style.cssText = 'width:20px;height:20px;margin:0;flex:none;';
    var text = document.createElement('span'); text.textContent = label;
    wrap.appendChild(el); wrap.appendChild(text);
    return { wrap: wrap, el: el, text: text };
  }

  // The size a lone view cannot show, which the "depth" field then stands in for.
  function missingAxis(views){
    var have = {};
    ['top', 'side', 'end'].forEach(function(r){ if(views[r]) AXES[r].forEach(function(a){ have[a] = true; }); });
    return ['height', 'width', 'length'].filter(function(a){ return !have[a]; })[0] || null;
  }
  var DEPTH_LABEL = { height: 'Height / thickness if no view shows it (mm)', width: 'Width if no view shows it (mm)',
                      length: 'Length if no view shows it (mm)' };
  var ASK_FOR = { height: 'Side/End', width: 'Top/End', length: 'Top/Side' };

  function render(){
    var card = document.getElementById('cal-part-card'); if(!card) return;
    var job = J(); if(!job) return;
    var p = job.part(), o = p.opts, touch = coarse();
    var nViews = ['top', 'side', 'end'].filter(function(r){ return p.views[r]; }).length;
    card.innerHTML = '';
    var hd = document.createElement('div'); hd.style.cssText = 'display:flex;align-items:center;gap:10px;margin-bottom:6px;';
    var t = document.createElement('div'); t.textContent = 'Build a 3D part from views'; t.style.cssText = 'font-weight:700;font-size:16px;flex:1;';
    var x = document.createElement('button'); x.type = 'button'; x.textContent = '✕'; x.setAttribute('aria-label', 'Close');
    x.style.cssText = 'min-width:44px;min-height:44px;border-radius:8px;background:none;color:#9aa;border:1px solid #2a3540;cursor:pointer;';
    x.onclick = close;
    hd.append(t, x); card.appendChild(hd);
    var sub = document.createElement('div'); sub.id = 'cal-part-intro';
    sub.style.cssText = 'font-size:12.5px;color:#9fb2bb;margin-bottom:10px;line-height:1.45;';
    // Lining views up means nothing until there are two of them.
    sub.textContent = 'Each view is a traced outline, from whichever photo shows it best (select an outline, then 🧩 Use in 3D part). ' +
      (nViews >= 2 ? 'The part keeps only what every view agrees is material. Use ⟲ / ⇋ / ⇵ until the outlines below line up.' : '');
    card.appendChild(sub);

    ROLES.forEach(function(r){
      var v = p.views[r[0]];
      var row = document.createElement('div'); row.id = 'cal-part-row-' + r[0];
      row.style.cssText = 'display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:7px 9px;border:1px solid #22303a;border-radius:9px;margin-bottom:6px;';
      var meta = document.createElement('div'); meta.style.cssText = 'flex:1 1 150px;min-width:0;';
      // A view that is not following a live, scaled outline says why, in amber — "follows its
      // outline" under a view whose photo was removed, or has no scale, was simply untrue.
      var flag = !v ? null
        : v.photo_removed ? (typeof v.photo_removed === 'string' ? v.photo_removed : 'its photo was removed — using the last copy (it can’t be edited)')
        : v.orphan ? 'its outline was deleted — using the last copy'
        : v.unscaled ? (typeof v.unscaled === 'string' ? v.unscaled : UNSCALED) : null;
      var scaled = v && Number(v.scale) > 0 && Math.abs(Number(v.scale) - 1) > 1e-6;
      meta.innerHTML = '<b>' + esc(r[1]) + '</b> <span style="font-size:12px;color:' + (v ? '#9fe6c2' : '#7f8f98') + ';">' +
        (v ? ((v.size_mm ? v.size_mm[0].toFixed(1) + ' × ' + v.size_mm[1].toFixed(1) + ' mm' : 'set') +
              ((v.rot || v.flip_h || v.flip_v) ? ' · turned' : '')) : 'not set — ' + esc(r[2])) + '</span>' +
        (scaled ? '<br><span data-scaled="' + r[0] + '" style="font-size:12px;color:#9fd3ff;">scaled ×' + Number(v.scale).toFixed(3) +
                  (v.scale_note ? ' — ' + esc(v.scale_note) : '') + '</span>' : '') +
        (flag ? '<br><span data-flag="' + r[0] + '" style="font-size:12px;color:#f3c969;">⚠ ' + esc(flag) + '</span>'
         : (v && v.ann_id != null ? '<br><span style="font-size:11.5px;color:#7f9aa5;">follows its outline' +
            (v.updated ? ' · updated ' + esc(new Date(v.updated).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})) : '') + '</span>' : ''));
      row.appendChild(meta);
      if(v){
        var acts = [['rot', '⟲', 'Rotate a quarter turn'], ['fliph', '⇋', 'Flip end for end'], ['flipv', '⇵', 'Flip upside down'],
                    ['clear', '✕', 'Remove this view']];
        if(scaled) acts.unshift(['unscale', '↶', 'Undo the scale match']);
        acts.forEach(function(b){
          var btn = document.createElement('button'); btn.type = 'button'; btn.textContent = b[1]; btn.title = b[2];
          btn.setAttribute('data-act', b[0]); btn.setAttribute('aria-label', b[2] + ' (' + r[1] + ')');
          btn.style.cssText = 'min-width:44px;min-height:44px;border-radius:8px;background:#151515;color:#cde;border:1px solid #2a3540;cursor:pointer;';
          btn.onclick = function(){
            if(b[0] === 'clear'){ job.clearPartView(r[0]); render(); return; }
            if(b[0] === 'unscale'){ undoScale(r[0]); return; }
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
        mv.style.cssText = 'min-height:44px;max-width:110px;border-radius:8px;background:#151515;color:#cde;border:1px solid #2a3540;';
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
        sel.style.cssText = 'min-height:40px;border-radius:8px;background:#0b1013;color:#eef;border:1px solid #29414c;';
        [['all', 'the whole length'], ['start', 'only the left end (as drawn below)'], ['end', 'only the right end (as drawn below)']]
          .forEach(function(op){ var o = document.createElement('option'); o.value = op[0]; o.textContent = op[1]; sel.appendChild(o); });
        sel.value = v.covers || 'all';
        sel.onchange = function(){ if(sel.value === 'all') delete v.covers; else v.covers = sel.value; job.savePart(); schedulePreview(); };
        cv.appendChild(sel);
        card.appendChild(cv);
      }
    });

    // The part itself, turnable — a flipped view or a wrong chamfer shows here at a glance,
    // where the three flat silhouettes below make you work it out.
    var v3 = document.createElement('div'); v3.id = 'cal-part-3d-wrap';
    v3.style.cssText = 'position:relative;background:#0b1013;border:1px solid #22303a;border-radius:10px;margin-bottom:8px;';
    var cv3 = document.createElement('canvas'); cv3.id = 'cal-part-3d';
    // On a phone a one-finger vertical swipe scrolls the dialog (pan-y): with touch-action
    // none, a swipe that started on the canvas tipped the part over and the downloads below
    // could not be reached from there.
    cv3.style.cssText = 'display:block;width:100%;height:' + (touch ? 'min(300px,40vh)' : 'min(440px,55vh)') + ';' +
      'touch-action:' + (touch ? 'pan-y' : 'none') + ';cursor:grab;border-radius:10px;';
    var hintTxt = touch ? 'drag sideways to turn · pinch to zoom · double-tap to reset'
                        : 'drag to turn · scroll to zoom · double-click to reset';
    cv3.setAttribute('aria-label', 'The part in 3D — ' + hintTxt);
    v3.appendChild(cv3);
    var bar3 = document.createElement('div');
    bar3.style.cssText = 'position:absolute;left:8px;top:8px;display:flex;gap:6px;flex-wrap:wrap;';
    [['iso', 'Iso'], ['top', 'Top'], ['side', 'Side'], ['end', 'End'], ['under', 'Under']].forEach(function(b){
      var bt = document.createElement('button'); bt.type = 'button'; bt.textContent = b[1]; bt.setAttribute('data-view3d', b[0]);
      bt.style.cssText = 'min-height:40px;min-width:40px;padding:3px 10px;border-radius:7px;background:rgba(21,26,31,.85);color:#cfe6ee;border:1px solid #29414c;font-size:12px;cursor:pointer;';
      bt.onclick = function(){ setView3d(b[0]); };
      bar3.appendChild(bt);
    });
    v3.appendChild(bar3);
    var hint3 = document.createElement('div'); hint3.id = 'cal-part-3d-hint';
    hint3.style.cssText = 'position:absolute;right:10px;bottom:6px;font-size:11px;color:#6f8791;pointer-events:none;';
    hint3.textContent = hintTxt;
    v3.appendChild(hint3);
    hook3d(cv3);

    // One view and nothing standing in for the size it cannot show: the preview stays empty,
    // and the only instruction used to be under it, below the fold. Say it here, on the field.
    var lack = missingAxis(p.views);
    var note = null;
    if(nViews === 1 && lack && !o.depth && !(o.known && Number(o.known[lack]) > 0)){
      note = document.createElement('div'); note.id = 'cal-part-onenote';
      note.style.cssText = 'margin:10px 0 0;padding:8px 10px;border-radius:9px;background:#2a2416;border:1px solid #5d4a22;color:#e8cf9a;font-size:12.5px;line-height:1.45;';
      note.textContent = 'One view so far. Type the part’s ' + lack + ' to preview it, or add a ' + ASK_FOR[lack] + ' view.';
    }
    // Normally the part comes straight under its views, in sight while they are turned and
    // flipped. With one view it is empty until that size is typed, so the field comes first.
    if(note) card.appendChild(note); else card.appendChild(v3);
    var opts = document.createElement('div'); opts.style.cssText = 'display:flex;flex-wrap:wrap;gap:10px;margin:10px 0 4px;';
    var fDepth = field(DEPTH_LABEL[lack] || DEPTH_LABEL.height, 'cal-part-depth', o.depth, { placeholder: 'from views' });
    if(note){
      fDepth.el.style.borderColor = '#f3c969'; fDepth.el.style.boxShadow = '0 0 0 2px rgba(243,201,105,.35)';
    }
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

    // Sizes already measured on the part: a tape on the real cover beats any photo's scale.
    var kn = o.known || {};
    var known = document.createElement('div'); known.style.cssText = 'display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 4px;';
    var fKnown = ['length', 'width', 'height'].map(function(ax){
      return field('Known overall ' + ax + ' (mm)', 'cal-part-known-' + ax, kn[ax], { placeholder: 'optional' });
    });
    fKnown.forEach(function(f){ known.appendChild(f.wrap); });
    card.appendChild(known);
    // With no Side view the ends come out square; a sloped, rounded toe can be typed instead.
    var fSlope = null, fRad = null;
    if(!p.views.side){
      var en = o.ends || {};
      var ends = document.createElement('div'); ends.id = 'cal-part-ends';
      ends.style.cssText = 'display:flex;flex-wrap:wrap;gap:10px;margin:6px 0 4px;';
      fSlope = field('End slope (°) — no Side view', 'cal-part-end-slope', en.slope_deg, { placeholder: 'square' });
      fRad = field('End edge radius (mm)', 'cal-part-end-radius', en.radius_mm, { placeholder: 'sharp' });
      ends.append(fSlope.wrap, fRad.wrap);
      card.appendChild(ends);
    }

    var flags = document.createElement('div'); flags.style.cssText = 'display:flex;flex-wrap:wrap;gap:0 16px;margin:4px 0 8px;';
    var cMirror = check('Mirror — the matching part for the other side', 'cal-part-mirror', o.mirror);
    var cUpside = check('Upside down for printing', 'cal-part-upside', o.upside_down);
    // A cover already downloads plate-down, open side up — ready to print. Ticking a box
    // called "Upside down for printing" turned it open side down over a deck that then needed
    // supports; with a wall, say what ticking it really does.
    function upsideLabel(){
      cUpside.text.textContent = num(fWall.el) ? 'Turn over: open side down (needs supports)' : 'Upside down for printing';
    }
    flags.append(cMirror.wrap, cUpside.wrap);
    card.appendChild(flags);
    if(note) card.appendChild(v3);

    function num(el){ var v = parseFloat(el.value); return (isFinite(v) && v > 0) ? v : null; }
    upsideLabel();
    function sync(){
      o.depth = num(fDepth.el); o.wall = num(fWall.el);
      o.mirror = cMirror.el.checked; o.upside_down = cUpside.el.checked;
      var k = {};
      fKnown.forEach(function(f, i){ var v = num(f.el); if(v) k[['length', 'width', 'height'][i]] = v; });
      if(Object.keys(k).length) o.known = k; else delete o.known;
      if(fSlope){
        var sd = num(fSlope.el), rd = num(fRad.el);
        if(sd || rd) o.ends = { slope_deg: sd || 0, radius_mm: rd || 0 }; else delete o.ends;
      }
      if(cav){
        o.cavity_view = cav.view.el.value; o.cavity_open = cav.open.el.value; o.cavity_align = cav.align.el.value;
        var c = parseFloat(cav.clr.el.value); o.clearance = (isFinite(c) && c >= 0) ? c : 0.4;
        o.cavity_depth = num(cav.dep.el);
      }
      upsideLabel();
      job.savePart(); schedulePreview();
    }
    [fDepth.el, fWall.el, cMirror.el, cUpside.el].concat(fKnown.map(function(f){ return f.el; }))
      .concat(fSlope ? [fSlope.el, fRad.el] : [])
      .concat(cav ? [cav.view.el, cav.clr.el, cav.open.el, cav.dep.el, cav.align.el] : [])
      .forEach(function(el){ el.addEventListener('change', sync); el.addEventListener('input', sync); });

    var pv = document.createElement('div'); pv.id = 'cal-part-preview';
    pv.style.cssText = 'background:#0b1013;border:1px solid #22303a;border-radius:10px;min-height:120px;padding:6px;';
    card.appendChild(pv);

    // Print bed: kept per viewer, not in the job — it is the printer's, not the part's.
    var bedRow = document.createElement('div'); bedRow.style.cssText = 'display:flex;flex-wrap:wrap;gap:10px;margin:8px 0 0;';
    var bd = bed();
    var fBedX = field('Print bed width (mm)', 'cal-part-bed-x', bd[0]), fBedY = field('Print bed depth (mm)', 'cal-part-bed-y', bd[1]);
    bedRow.append(fBedX.wrap, fBedY.wrap);
    card.appendChild(bedRow);
    [fBedX.el, fBedY.el].forEach(function(el){
      el.addEventListener('input', function(){
        var bx = num(fBedX.el), by = num(fBedY.el);
        if(bx && by){ setBed([bx, by]); if(lastInfo) setStatus(describe(lastInfo), lastBad); }
      });
    });

    // Status and downloads stay in sight at the bottom: on a phone the dialog is three
    // screens tall, and a refused build used to say why only at the very end of it.
    var foot = document.createElement('div'); foot.id = 'cal-part-foot';
    // bottom: minus the dialog's own padding, so it sits on the screen's edge, not above it.
    foot.style.cssText = 'position:sticky;bottom:calc(-1 * clamp(6px,3vw,18px));z-index:2;background:#11161a;margin:10px -16px 0;' +
      'padding:6px 16px 12px;border-top:1px solid #22303a;border-radius:0 0 14px 14px;';
    var st = document.createElement('div'); st.id = 'cal-part-status';
    st.style.cssText = 'font-size:12.5px;line-height:1.45;color:#9fb2bb;min-height:18px;overflow:auto;max-height:' + (touch ? '22vh' : '30vh') + ';';
    st.addEventListener('click', function(e){
      var b = e.target && e.target.closest ? e.target.closest('button') : null; if(!b) return;
      if(b.hasAttribute('data-use-axis')) useView(b.getAttribute('data-use-axis'), b.getAttribute('data-role'));
      else if(b.hasAttribute('data-match')) matchScale(b.getAttribute('data-match'), parseFloat(b.getAttribute('data-f')), b.getAttribute('data-note') || '');
    });
    foot.appendChild(st);
    var acts = document.createElement('div'); acts.style.cssText = 'display:flex;gap:9px;margin-top:8px;flex-wrap:wrap;';
    var build = document.createElement('button'); build.type = 'button'; build.id = 'cal-part-build';
    build.textContent = '🧊 Download STL';
    build.style.cssText = 'min-height:46px;padding:10px 20px;border-radius:10px;background:#00d4ff;color:#000;border:none;font-weight:700;font-size:15px;cursor:pointer;';
    build.onclick = function(){ download('stl'); };
    acts.appendChild(build);
    // For CAD: a real solid with one face per flat side, which Fusion can edit — an STL comes
    // in as a mesh whose every triangle becomes its own face.
    [['step', touch ? '📐 STEP' : '📐 STEP (edit in CAD)', 'A solid body for Fusion / CAD: one face per flat side, ready to edit'],
     ['obj', 'OBJ', 'A mesh with one polygon per flat side, for programs that take meshes']].forEach(function(f){
      var bb = document.createElement('button'); bb.type = 'button'; bb.id = 'cal-part-build-' + f[0];
      bb.textContent = f[1]; bb.title = f[2];
      bb.style.cssText = 'min-height:46px;padding:10px ' + (touch ? '12px' : '16px') + ';border-radius:10px;background:#151a1f;color:#cfe6ee;border:1px solid #29414c;font-weight:600;font-size:14px;cursor:pointer;';
      bb.onclick = function(){ download(f[0]); };
      acts.appendChild(bb);
    });
    foot.appendChild(acts);
    card.appendChild(foot);
    schedulePreview();
  }

  // ---- print bed ---------------------------------------------------------------------------
  // A 700 mm cover fits no common bed (220-360 mm, and a 350 mm bed's diagonal is 495 mm),
  // and nothing said so until the slicer did. Every slicer can cut a part; this says how many
  // pieces and where, before the download.
  var BED_KEY = 'camscan.printBed';
  function bed(){
    try{
      var b = JSON.parse(localStorage.getItem(BED_KEY));
      if(Array.isArray(b) && Number(b[0]) > 0 && Number(b[1]) > 0) return [Number(b[0]), Number(b[1])];
    }catch(e){}
    return [256, 256];
  }
  function setBed(b){ try{ localStorage.setItem(BED_KEY, JSON.stringify(b)); }catch(e){} }
  // Does an L × W footprint lie on a bed, square to it or turned towards the diagonal?
  function fitsBed(L, W, b){
    for(var d = 0; d <= 90; d += 0.5){
      var t = d * Math.PI / 180, c = Math.cos(t), s = Math.sin(t);
      if(L * c + W * s <= b[0] + 1e-6 && L * s + W * c <= b[1] + 1e-6) return true;
    }
    return false;
  }
  function bedLine(size){
    if(!size || !(size[0] > 0) || !(size[1] > 0)) return '';
    var b = bed(), L = Math.max(size[0], size[1]), W = Math.min(size[0], size[1]);
    if(fitsBed(L, W, b)) return '';
    var name = b[0] === b[1] ? b[0].toFixed(0) + ' mm' : b[0].toFixed(0) + ' × ' + b[1].toFixed(0) + ' mm';
    // Wider than the bed itself: pieces cut across the length would each need the diagonal.
    if(W > Math.max(b[0], b[1]) + 1e-6) return '⚠ Wider than your ' + name + ' bed, even cut into pieces along its length.';
    // The longest piece of this width that still fits, then that many even pieces.
    var lo = 1, hi = L;
    for(var i = 0; i < 40; i++){ var mid = (lo + hi) / 2; if(fitsBed(mid, W, b)) lo = mid; else hi = mid; }
    var n = Math.max(2, Math.ceil(L / lo - 1e-9)), cuts = [];
    for(i = 1; i < n; i++) cuts.push((L * i / n).toFixed(0));
    return '⚠ Larger than your ' + name + ' bed — needs about ' + n + ' pieces along its length (cuts near ' +
      cuts.join(' / ') + ' mm). Use your slicer’s Cut tool, or split the STEP in Fusion.';
  }

  var lastInfo = null, lastBad = false;
  function setStatus(html, bad){
    var st = document.getElementById('cal-part-status'); if(!st) return;
    st.innerHTML = html; st.style.color = bad ? '#f0b9a6' : '#9fb2bb';
  }
  var CAP = { length: 'Length', width: 'Width', height: 'Height' };
  var SBTN = 'min-height:40px;margin:4px 6px 2px 0;padding:6px 12px;border-radius:8px;background:#1b2229;color:#e8eef2;' +
             'border:1px solid #2f4a57;font:600 13px Segoe UI,system-ui,sans-serif;cursor:pointer;';
  // One fit, both readings and who won: "Width: Top 164.6 mm, End 175.7 mm (6.7% apart) —
  // using Top · [Use End]". "The end view was stretched 6.7% in width" gave neither number and
  // no way to say the other photo was the one to trust.
  function fitLine(f){
    var views = (J() && J().part) ? J().part().views : {};
    var from = LABEL[f.setter] || (f.setter === 'known' ? 'typed' : f.setter === 'depth' ? 'depth' : String(f.setter));
    var line = esc(CAP[f.axis] || f.axis) + ': ' + esc(from) + ' ' + Number(f.want_mm).toFixed(1) + ' mm, ' +
      esc(LABEL[f.view] || f.view) + ' ' + Number(f.have_mm).toFixed(1) + ' mm (' + Math.abs(f.pct).toFixed(1) + '% apart) — using ' +
      (f.setter === 'known' ? 'your number' : esc(from)) +
      (f.kind === 'rescale' ? ' (the ' + esc(LABEL[f.view] || f.view) + ' view was rescaled as a whole)' : '');
    if(LABEL[f.setter] && views[f.view] && AXES[f.view] && AXES[f.view].indexOf(f.axis) >= 0){
      line += ' · <button type="button" data-use-axis="' + esc(f.axis) + '" data-role="' + esc(f.view) + '" style="' + SBTN + '">Use ' +
        esc(LABEL[f.view]) + '</button>';
    }
    return line;
  }
  function describe(info){
    var lines = [];
    if(info.size_mm){
      lines.push('Part: <b>' + info.size_mm.map(function(v){ return v.toFixed(1); }).join(' × ') + ' mm</b>' +
        (info.volume_mm3 ? ' · ' + (info.volume_mm3 / 1000).toFixed(1) + ' cm³' : ''));
      var bl = bedLine(info.size_mm);
      if(bl) lines.push('<span data-bed="1" style="color:#f3c969;">' + esc(bl) + '</span>');
    }
    (info.fits || []).forEach(function(f){
      if(Math.abs(f.pct) < 0.05) return;
      if(f.have_mm != null && f.want_mm != null && f.setter) lines.push(fitLine(f));
      else lines.push('The ' + esc(f.view) + ' view was stretched ' + Math.abs(f.pct).toFixed(1) + '% in ' + esc(f.axis) + ' to match.');
    });
    (info.deskew || []).forEach(function(d){
      if(Math.abs(d.deg) >= 0.05) lines.push(esc(LABEL[d.view] || d.view) + ' view turned ' + Math.abs(d.deg).toFixed(1) + '° to square it.');
    });
    if(info.ends && (info.ends.slope_deg > 0 || info.ends.radius_mm > 0)){
      lines.push('Ends: ' + [info.ends.slope_deg > 0 ? Number(info.ends.slope_deg).toFixed(0) + '° slope' : '',
                              info.ends.radius_mm > 0 ? Number(info.ends.radius_mm).toFixed(0) + ' mm edge radius' : '']
        .filter(Boolean).join(', ') + ' (typed — no Side view).');
    }
    (info.warnings || []).forEach(function(w){ lines.push('⚠️ ' + esc(w)); });
    if(info.note) lines.push(esc(info.note));
    if(info.mirrored) lines.push('Downloads <b>mirrored</b> — the part for the other side. The preview shows it as traced.');
    // Which way up the file comes. The server says it in words where it can; an older one
    // reported a cover's automatic plate-down turn as "upside down", which with the box
    // unticked read as the opposite of the checkbox.
    if(info.orientation){
      if(!(info.note && info.note.indexOf(info.orientation) >= 0)) lines.push('Downloads ' + esc(info.orientation) + '.');
    }else if(info.kind === 'shell' || info.wall_mm){
      lines.push(info.upside_down ? 'Downloads plate-down, open side up — ready to print.'
                                  : 'Downloads open side down — the deck needs supports.');
    }else if(info.upside_down) lines.push('Downloads upside down for printing.');
    return lines.join('<br>');
  }
  // A refused build that names two views that disagree: {view, axis, have_mm, want_mm, setter},
  // from the server's own `detail` or, from one that sends only the text, read out of it.
  function mismatchOf(j){
    var d = j && j.detail;
    if(d && d.view && d.axis && d.have_mm > 0 && d.want_mm > 0) return d;
    var m = /The (top|side|end) view's (length|width|height) is \d+% (?:longer|shorter) than the (top|side|end) view's \(([\d.]+) vs ([\d.]+) mm\)/
      .exec((j && j.error) || '');
    return m ? { view: m[1], axis: m[2], setter: m[3], have_mm: parseFloat(m[4]), want_mm: parseFloat(m[5]) } : null;
  }
  // "Scale End to match Top ×0.893" and the other way round: which photo to trust is the
  // user's call (the one whose ruler lay on the face). Not past MATCH_LIMIT, where the
  // likelier cause is the wrong photo, and the message says so.
  function matchButtons(d){
    if(!d) return '';
    var views = J().part().views, off = Math.abs(d.have_mm / d.want_mm - 1);
    if(!views[d.view] || !(off > 0) || off >= MATCH_LIMIT) return '';
    var f = d.want_mm / d.have_mm, V = LABEL[d.view] || d.view, S = LABEL[d.setter];
    var out = '<br><button type="button" data-match="' + esc(d.view) + '" data-f="' + f.toFixed(5) + '" data-note="matched to ' +
      esc(S ? S + ' ' + d.axis : 'the typed ' + d.axis) + '" style="' + SBTN + '">Scale ' + esc(V) + ' to match ' +
      esc(S || 'your ' + d.axis) + ' ×' + f.toFixed(3) + '</button>';
    if(S && views[d.setter]){
      out += '<button type="button" data-match="' + esc(d.setter) + '" data-f="' + (1 / f).toFixed(5) + '" data-note="matched to ' +
        esc(V + ' ' + d.axis) + '" style="' + SBTN + '">Scale ' + esc(S) + ' to match ' + esc(V) + ' ×' + (1 / f).toFixed(3) + '</button>';
    }
    return out;
  }

  function runPreview(){
    var pv = document.getElementById('cal-part-preview'); if(!pv) return;
    var my = ++seq;
    var b = body(true);
    if(!Object.keys(b.views).length){ pv.innerHTML = ''; lastInfo = null; setStatus('Assign at least one outline to a view first.'); return; }
    fetch(apiUrl('api/export/part'), { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(b) })
      .then(function(r){ return r.json().catch(function(){ return { ok:false, error:'The server could not build a preview.' }; }); })
      .then(function(j){
        if(my !== seq) return;                      // a newer preview is on its way
        var btn = document.getElementById('cal-part-build');
        if(!j || !j.ok){
          pv.innerHTML = ''; lastInfo = null;
          mesh3d = null; draw3d();                  // the last part is not what would download
          setStatus(esc((j && j.error) || 'Could not build the part.') + matchButtons(mismatchOf(j)), true);
          if(btn) btn.disabled = true;
          return;
        }
        if(btn) btn.disabled = false;
        drawPreview(pv, j.preview);
        mesh3d = (j.preview && j.preview.mesh) || null;
        view3d.fit = null;                          // a new part: fit the view to it again
        draw3d();
        lastInfo = j.info || {}; lastBad = !!(lastInfo.warnings && lastInfo.warnings.length);
        setStatus(describe(lastInfo), lastBad);
      })
      .catch(function(e){ if(my === seq) setStatus('Preview needs the server (' + esc(e.message) + ').', true); });
  }

  // ---- the 3D view: a small canvas renderer, no library ------------------------------------
  // Flat-shaded triangles, back faces dropped, drawn far to near. A few thousand triangles
  // turn smoothly; nothing to download, so it works wherever the app does.
  var mesh3d = null;
  var VIEWS3D = { iso: [-0.65, 0.55], top: [0, Math.PI / 2], side: [0, 0], end: [-Math.PI / 2, 0],
                  under: [-0.65, -0.75] };                     // a hollow cover's inside
  // `preset`: the button last pressed, until the part is turned by hand. `fit`: the part's
  // projected width and height in that preset, so a straight-on view fills the canvas — the
  // bounding sphere of a 679 mm part left its 165 × 80 mm end as a 64 px speck. Kept while
  // turning by hand, so the part does not jump in size under the finger.
  var view3d = { yaw: VIEWS3D.iso[0], pitch: VIEWS3D.iso[1], zoom: 1, preset: 'iso', fit: null };
  var FIT_PRESETS = { top: 1, side: 1, end: 1 };
  function setView3d(name){
    var v = VIEWS3D[name] || VIEWS3D.iso;
    view3d.yaw = v[0]; view3d.pitch = v[1]; view3d.zoom = 1;
    view3d.preset = VIEWS3D[name] ? name : 'iso'; view3d.fit = null;
    draw3d();
  }
  function draw3d(){
    var cv = document.getElementById('cal-part-3d'); if(!cv) return;
    var dpr = window.devicePixelRatio || 1, W = cv.clientWidth || 300, H = cv.clientHeight || 200;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    var g = cv.getContext('2d'); g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = '#0b1013'; g.fillRect(0, 0, W, H);
    var m = mesh3d;
    if(!m || !m.v || !m.v.length || !m.f || !m.f.length){ cv.removeAttribute('data-faces'); return; }
    var V = m.v, F = m.f, lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for(var i = 0; i < V.length; i++) for(var k = 0; k < 3; k++){ if(V[i][k] < lo[k]) lo[k] = V[i][k]; if(V[i][k] > hi[k]) hi[k] = V[i][k]; }
    var c = [(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2];
    var R = Math.max(1e-6, Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]) / 2);
    var cy = Math.cos(view3d.yaw), sy = Math.sin(view3d.yaw), cp = Math.cos(view3d.pitch), sp = Math.sin(view3d.pitch);
    // Camera space: X right, Y up, D into the screen (larger = farther).
    var P = new Array(V.length), plo = [Infinity, Infinity], phi = [-Infinity, -Infinity];
    for(i = 0; i < V.length; i++){
      var x = V[i][0] - c[0], y = V[i][1] - c[1], z = V[i][2] - c[2];
      var xr = x * cy - y * sy, yr = x * sy + y * cy;
      P[i] = [xr, z * cp + yr * sp, yr * cp - z * sp];
      for(k = 0; k < 2; k++){ if(P[i][k] < plo[k]) plo[k] = P[i][k]; if(P[i][k] > phi[k]) phi[k] = P[i][k]; }
    }
    // Straight on, the bounding box's centre lands on the canvas centre, so fitting the
    // projected box needs no shift.
    if(!view3d.fit && FIT_PRESETS[view3d.preset]) view3d.fit = [Math.max(1e-6, phi[0] - plo[0]), Math.max(1e-6, phi[1] - plo[1])];
    var sc = view3d.fit ? view3d.zoom * 0.9 * Math.min(W / view3d.fit[0], H / view3d.fit[1])
                        : view3d.zoom * 0.46 * Math.min(W, H) / R;
    var L = [-0.45, 0.6, -0.66], Ln = Math.hypot(L[0], L[1], L[2]); L = [L[0] / Ln, L[1] / Ln, L[2] / Ln];
    var list = [];
    for(i = 0; i < F.length; i++){
      var a = P[F[i][0]], b = P[F[i][1]], d = P[F[i][2]];
      var e1 = [b[0] - a[0], b[1] - a[1], b[2] - a[2]], e2 = [d[0] - a[0], d[1] - a[1], d[2] - a[2]];
      var n = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]];
      var nl = Math.hypot(n[0], n[1], n[2]); if(nl === 0) continue;
      n = [n[0] / nl, n[1] / nl, n[2] / nl];
      if(n[2] > 0) continue;                                   // facing away
      var lit = 0.12 + 0.88 * Math.pow(Math.max(0, n[0] * L[0] + n[1] * L[1] + n[2] * L[2]), 1.4);
      list.push([(a[2] + b[2] + d[2]) / 3, a, b, d, lit]);
    }
    list.sort(function(p, q){ return q[0] - p[0]; });
    for(i = 0; i < list.length; i++){
      var t = list[i], col = 'rgb(' + Math.round(25 + 110 * t[4]) + ',' + Math.round(55 + 170 * t[4]) + ',' + Math.round(70 + 175 * t[4]) + ')';
      g.beginPath();
      g.moveTo(W / 2 + t[1][0] * sc, H / 2 - t[1][1] * sc);
      g.lineTo(W / 2 + t[2][0] * sc, H / 2 - t[2][1] * sc);
      g.lineTo(W / 2 + t[3][0] * sc, H / 2 - t[3][1] * sc);
      g.closePath(); g.fillStyle = col; g.fill(); g.strokeStyle = col; g.lineWidth = 0.6; g.stroke();
    }
    cv.setAttribute('data-faces', String(list.length));       // for tests: how many faces were drawn
  }
  function hook3d(cv){
    var drag = null, pts = {}, lastTap = 0;
    function pinchDist(){ var k = Object.keys(pts); return k.length === 2 ? Math.hypot(pts[k[0]][0] - pts[k[1]][0], pts[k[0]][1] - pts[k[1]][1]) : 0; }
    function midY(){ var k = Object.keys(pts); return k.length === 2 ? (pts[k[0]][1] + pts[k[1]][1]) / 2 : 0; }
    // Start (or restart, when a finger is added or lifted) from where the fingers are now, so
    // the part does not jump.
    function begin(e){
      var k = Object.keys(pts), p0 = pts[k[0]] || [e.clientX, e.clientY];
      drag = { x: p0[0], y: p0[1], yaw: view3d.yaw, pitch: view3d.pitch, zoom: view3d.zoom,
               pinch: k.length === 2 ? pinchDist() : 0, mid: midY(), moved: drag ? drag.moved : false };
    }
    cv.addEventListener('pointerdown', function(e){
      pts[e.pointerId] = [e.clientX, e.clientY];
      if(Object.keys(pts).length === 1) drag = null;
      begin(e);
      try{ cv.setPointerCapture(e.pointerId); }catch(err){}
      cv.style.cursor = 'grabbing';
    });
    cv.addEventListener('pointermove', function(e){
      if(!drag || !pts[e.pointerId]) return;
      pts[e.pointerId] = [e.clientX, e.clientY];
      var finger = e.pointerType === 'touch';
      if(Object.keys(pts).length === 2 && drag.pinch){
        // Two fingers: pinch zooms; on a phone, moving both up or down tips the part.
        view3d.zoom = Math.min(8, Math.max(0.3, drag.zoom * pinchDist() / drag.pinch));
        if(finger) view3d.pitch = Math.min(1.55, Math.max(-1.55, drag.pitch + (midY() - drag.mid) * 0.01));
      }else{
        var k0 = Object.keys(pts)[0], p0 = pts[k0];
        view3d.yaw = drag.yaw + (p0[0] - drag.x) * 0.01;
        // One finger turns the part only sideways: its vertical swipe belongs to the page.
        if(!finger) view3d.pitch = Math.min(1.55, Math.max(-1.55, drag.pitch + (p0[1] - drag.y) * 0.01));
        if(Math.abs(p0[0] - drag.x) + Math.abs(p0[1] - drag.y) > 8) drag.moved = true;
      }
      if(Object.keys(pts).length === 2) drag.moved = true;
      view3d.preset = null;
      draw3d();
    });
    function end(e){
      var was = drag, tap = e.type === 'pointerup' && e.pointerType === 'touch' && was && !was.moved && Object.keys(pts).length === 1;
      delete pts[e.pointerId];
      if(!Object.keys(pts).length){ drag = null; cv.style.cursor = 'grab'; }
      else begin(e);
      // Double-tap resets: a phone sends no dblclick once the canvas handles its own touches.
      if(tap){
        var now = Date.now();
        if(now - lastTap < 300){ lastTap = 0; setView3d('iso'); } else lastTap = now;
      }
    }
    cv.addEventListener('pointerup', end); cv.addEventListener('pointercancel', end);
    cv.addEventListener('wheel', function(e){
      e.preventDefault();
      view3d.zoom = Math.min(8, Math.max(0.3, view3d.zoom * Math.exp(-e.deltaY * 0.0015)));
      draw3d();
    }, { passive: false });
    cv.addEventListener('dblclick', function(){ setView3d('iso'); });
    if(!hook3d.resize){ hook3d.resize = true; window.addEventListener('resize', draw3d); }
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
      .catch(function(e){ setStatus(esc(e.message) + matchButtons(mismatchOf({ error: e.message })), true); });
  }

  window.CalibPart3D = { open: open, pick: pick, assign: assign, body: body, assigned: assigned, move: move,
                         sync: sync, syncSoon: syncSoon,
                         // The dialog's pure rules, for tests/unit_p2_build_dialog.py.
                         _rules: { bedLine: bedLine, fitsBed: fitsBed, assignWarning: assignWarning, mismatchOf: mismatchOf,
                                   viewSize: viewSize, missingNote: missingNote, describe: describe } };
})();
