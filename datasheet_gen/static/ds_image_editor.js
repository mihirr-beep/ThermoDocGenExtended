/* Shared image editor for the datasheet Preview module.
 * Used by both ce_form.html and generic_form.html.
 *
 *   DSImageEditor.open({
 *     file:   File,                 // the current image
 *     boxMM:  [widthMM, heightMM],  // the document box this image lands in
 *     onApply: function(newFile){}  // called with the edited image File
 *   });
 *
 *   DSImageEditor.autoFit({file, boxMM, onApply})  // silent centre-crop on upload
 *
 * Modal UX:
 *   - A crop frame is shown BY DEFAULT, pre-set to the document-box ratio (boxMM)
 *     and centred on the upload, with a rule-of-thirds grid + corner handles so a
 *     new user immediately sees they can drag (to move) or grab a corner (to
 *     resize, ratio-locked).
 *   - "Set image to frame": instead of cropping, keeps the WHOLE image and stretches
 *     it to the box ratio — the width is preserved and the height is stretched to fill,
 *     so nothing is cropped away and no white border is added.
 *   - A live PREVIEW pane on the right (same shape/size as the document slot) shows
 *     exactly how the current crop / fit will look.
 *   - Rotate 90 L/R, grayscale, brightness, contrast. "Reset" returns to step 1
 *     (the original upload, default centred frame).
 * No external dependencies.
 */
(function () {
  "use strict";
  var mounted = false, els = {}, S = null;
  var MIN = 24;      // min crop size in stage display px
  var PREVMAX = 300; // preview longer-edge in display px

  var CSS = [
    ".dsie-ov{position:fixed;inset:0;z-index:1100;display:none;}",
    ".dsie-ov.open{display:block;}",
    ".dsie-bg{position:absolute;inset:0;background:rgba(0,0,0,.7);}",
    ".dsie-mid{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:16px;overflow:auto;}",
    ".dsie-panel{position:relative;background:#fff;border-radius:12px;box-shadow:0 25px 50px rgba(0,0,0,.45);width:100%;max-width:1060px;margin:auto;}",
    ".dsie-head{display:flex;align-items:center;justify-content:space-between;padding:12px 18px;border-bottom:1px solid #e5e7eb;}",
    ".dsie-head h3{margin:0;font-size:16px;font-weight:700;color:#0f172a;}",
    ".dsie-head p{margin:2px 0 0;font-size:11px;color:#64748b;}",
    ".dsie-x{border:none;background:none;font-size:22px;line-height:1;color:#9ca3af;cursor:pointer;}",
    ".dsie-tools{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;padding:11px 18px;border-bottom:1px solid #eef2f7;background:#fafbfc;}",
    ".dsie-grp{display:flex;align-items:center;gap:6px;font-size:12px;color:#334155;}",
    ".dsie-grp .lbl{font-weight:600;color:#64748b;}",
    ".dsie-seg{display:inline-flex;border:1px solid #cbd5e1;border-radius:8px;overflow:hidden;}",
    ".dsie-seg button{border:none;background:#fff;padding:6px 12px;font-size:12px;font-weight:600;color:#475569;cursor:pointer;}",
    ".dsie-seg button+button{border-left:1px solid #cbd5e1;}",
    ".dsie-seg button.active{background:#059669;color:#fff;}",
    ".dsie-chip{font-size:12px;padding:4px 9px;border:1px solid #cbd5e1;background:#fff;border-radius:7px;cursor:pointer;color:#334155;}",
    ".dsie-chip.active{border-color:#059669;background:#ecfdf5;color:#047857;font-weight:600;}",
    ".dsie-range{vertical-align:middle;width:92px;}",
    ".dsie-body{display:flex;gap:14px;align-items:stretch;padding:16px 18px;background:#f1f5f9;flex-wrap:wrap;}",
    ".dsie-col{display:flex;flex-direction:column;min-width:0;}",
    ".dsie-col.left{flex:1 1 420px;}",
    ".dsie-col.right{flex:0 0 auto;}",
    ".dsie-caption{font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.04em;margin-bottom:6px;}",
    ".dsie-stagewrap{flex:1;display:flex;align-items:center;justify-content:center;background:#e2e8f0;border-radius:8px;padding:8px;min-height:200px;}",
    ".dsie-stage{position:relative;display:inline-block;max-width:100%;user-select:none;touch-action:none;line-height:0;overflow:hidden;}",
    ".dsie-stage img{max-width:100%;max-height:56vh;display:block;}",
    ".dsie-frame{position:absolute;border:2px solid #34d399;box-shadow:0 0 0 9999px rgba(0,0,0,.5);display:none;cursor:move;touch-action:none;}",
    ".dsie-frame.on{display:block;}",
    ".dsie-grid{position:absolute;inset:0;pointer-events:none;background-image:repeating-linear-gradient(to right,rgba(255,255,255,.55) 0 1px,transparent 1px 33.333%),repeating-linear-gradient(to bottom,rgba(255,255,255,.55) 0 1px,transparent 1px 33.333%);}",
    ".dsie-h{position:absolute;width:14px;height:14px;background:#fff;border:2px solid #059669;border-radius:3px;box-shadow:0 1px 2px rgba(0,0,0,.4);}",
    ".dsie-h.nw{left:1px;top:1px;cursor:nwse-resize;}",
    ".dsie-h.ne{right:1px;top:1px;cursor:nesw-resize;}",
    ".dsie-h.se{right:1px;bottom:1px;cursor:nwse-resize;}",
    ".dsie-h.sw{left:1px;bottom:1px;cursor:nesw-resize;}",
    ".dsie-prevwrap{background:#e2e8f0;border-radius:8px;padding:8px;display:flex;align-items:center;justify-content:center;}",
    ".dsie-prev{display:block;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.4);border-radius:2px;}",
    ".dsie-foot{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px 18px;border-top:1px solid #e5e7eb;}",
    ".dsie-foot .right{display:flex;gap:8px;}",
    ".dsie-b{padding:6px 14px;font-size:13px;border-radius:8px;border:1px solid #d1d5db;background:#fff;cursor:pointer;}",
    ".dsie-b.green{background:#059669;color:#fff;border-color:#059669;}",
    ".dsie-b.link{border:none;background:none;color:#64748b;}",
    ".dsie-hint{font-size:11px;color:#94a3b8;}"
  ].join("");

  var HTML =
    '<div class="dsie-bg" data-close="1"></div>' +
    '<div class="dsie-mid" data-close="1">' +
    '  <div class="dsie-panel">' +
    '    <div class="dsie-head">' +
    '      <div><h3>Edit image</h3><p>Drag the frame to position it, or grab a corner to resize (locked to the document ratio). Or use “Set image to frame” to keep the whole photo and stretch it to fill (no white border).</p></div>' +
    '      <button type="button" class="dsie-x" data-act="cancel">&times;</button>' +
    '    </div>' +
    '    <div class="dsie-tools">' +
    '      <div class="dsie-grp"><span class="lbl">Mode</span>' +
    '        <span class="dsie-seg">' +
    '          <button type="button" data-mode="crop" id="dsie-mcrop" class="active">Crop</button>' +
    '          <button type="button" data-mode="fit"  id="dsie-mfit">Set image to frame</button>' +
    '        </span></div>' +
    '      <div class="dsie-grp"><span class="lbl">Rotate</span>' +
    '        <button type="button" class="dsie-chip" data-act="rotL">&#8634; 90&deg;</button>' +
    '        <button type="button" class="dsie-chip" data-act="rotR">90&deg; &#8635;</button></div>' +
    '      <div class="dsie-grp"><button type="button" class="dsie-chip" id="dsie-gray" data-act="gray">B&amp;W</button></div>' +
    '      <div class="dsie-grp"><span class="lbl">Brightness</span><input id="dsie-bright" class="dsie-range" type="range" min="50" max="150" value="100"></div>' +
    '      <div class="dsie-grp"><span class="lbl">Contrast</span><input id="dsie-contrast" class="dsie-range" type="range" min="50" max="150" value="100"></div>' +
    '    </div>' +
    '    <div class="dsie-body">' +
    '      <div class="dsie-col left">' +
    '        <div class="dsie-caption" id="dsie-leftcap">Original — adjust the frame</div>' +
    '        <div class="dsie-stagewrap">' +
    '          <div class="dsie-stage" id="dsie-stage">' +
    '            <img id="dsie-img" alt="edit">' +
    '            <div class="dsie-frame" id="dsie-frame">' +
    '              <div class="dsie-grid"></div>' +
    '              <div class="dsie-h nw" data-h="nw"></div><div class="dsie-h ne" data-h="ne"></div>' +
    '              <div class="dsie-h se" data-h="se"></div><div class="dsie-h sw" data-h="sw"></div>' +
    '            </div>' +
    '          </div>' +
    '        </div>' +
    '      </div>' +
    '      <div class="dsie-col right">' +
    '        <div class="dsie-caption">Preview — as it will appear</div>' +
    '        <div class="dsie-prevwrap"><canvas id="dsie-prev" class="dsie-prev"></canvas></div>' +
    '      </div>' +
    '    </div>' +
    '    <div class="dsie-foot">' +
    '      <div><button type="button" class="dsie-b link" data-act="reset">Reset</button>' +
    '        <span class="dsie-hint" id="dsie-hint"></span></div>' +
    '      <div class="right">' +
    '        <button type="button" class="dsie-b" data-act="cancel">Cancel</button>' +
    '        <button type="button" class="dsie-b green" data-act="apply">Apply</button>' +
    '      </div>' +
    '    </div>' +
    '  </div>' +
    '</div>';

  function mount() {
    if (mounted) return;
    var st = document.createElement("style"); st.textContent = CSS; document.head.appendChild(st);
    var ov = document.createElement("div"); ov.className = "dsie-ov"; ov.id = "dsie-ov"; ov.innerHTML = HTML;
    document.body.appendChild(ov);
    els.ov = ov;
    els.img = ov.querySelector("#dsie-img");
    els.stage = ov.querySelector("#dsie-stage");
    els.frame = ov.querySelector("#dsie-frame");
    els.prev = ov.querySelector("#dsie-prev");
    els.gray = ov.querySelector("#dsie-gray");
    els.bright = ov.querySelector("#dsie-bright");
    els.contrast = ov.querySelector("#dsie-contrast");
    els.hint = ov.querySelector("#dsie-hint");
    els.leftcap = ov.querySelector("#dsie-leftcap");
    els.mcrop = ov.querySelector("#dsie-mcrop");
    els.mfit = ov.querySelector("#dsie-mfit");
    // once the (rotated) working image is shown, (re)fit the default frame to it
    els.img.onload = function () { if (S) { setDefaultSel(); renderSel(); renderPreview(); } };

    ov.addEventListener("click", function (e) {
      var mode = e.target.getAttribute("data-mode");
      if (mode) { setMode(mode); return; }
      var act = e.target.getAttribute("data-act");
      if (e.target.getAttribute("data-close") === "1") { close(); return; }
      if (!act) return;
      if (act === "cancel") close();
      else if (act === "apply") apply();
      else if (act === "rotL") { S.angle -= 90; renderWork(); }
      else if (act === "rotR") { S.angle += 90; renderWork(); }
      else if (act === "gray") { S.gray = !S.gray; els.gray.classList.toggle("active", S.gray); applyFilter(); }
      else if (act === "reset") resetAll();
    });
    els.bright.addEventListener("input", function () { S.bright = this.value / 100; applyFilter(); });
    els.contrast.addEventListener("input", function () { S.contrast = this.value / 100; applyFilter(); });

    // ---- crop frame: move (drag inside) + resize (corner handles), ratio-locked ----
    function onDown(e) {
      if (!S || S.mode !== "crop" || !S.sel) return;
      var h = e.target.getAttribute("data-h");
      var kind = h || (e.target === els.frame || e.target.classList.contains("dsie-grid") ? "move" : null);
      if (!kind) return;
      e.preventDefault();
      var p = (e.touches ? e.touches[0] : e);
      S.drag = { kind: kind, sx: p.clientX, sy: p.clientY, orig: { x: S.sel.x, y: S.sel.y, w: S.sel.w, h: S.sel.h } };
    }
    function onMove(e) {
      if (!S || !S.drag) return;
      e.preventDefault();
      var p = (e.touches ? e.touches[0] : e);
      var r = els.img.getBoundingClientRect();
      var dx = p.clientX - S.drag.sx, dy = p.clientY - S.drag.sy, o = S.drag.orig, ratio = S.ratio;
      if (S.drag.kind === "move") {
        S.sel.x = Math.min(Math.max(o.x + dx, 0), r.width - o.w);
        S.sel.y = Math.min(Math.max(o.y + dy, 0), r.height - o.h);
      } else {
        var kind = S.drag.kind;
        // fixed opposite corner
        var ax = (kind === "nw" || kind === "sw") ? o.x + o.w : o.x;
        var ay = (kind === "nw" || kind === "ne") ? o.y + o.h : o.y;
        // dragged corner's original position, moved by the pointer delta, clamped to image
        var ox = (kind === "ne" || kind === "se") ? o.x + o.w : o.x;
        var oy = (kind === "sw" || kind === "se") ? o.y + o.h : o.y;
        var cx = Math.min(Math.max(ox + dx, 0), r.width);
        var cy = Math.min(Math.max(oy + dy, 0), r.height);
        var rawW = Math.abs(cx - ax), rawH = Math.abs(cy - ay);
        var w = rawW, h = w / ratio;
        if (h > rawH) { h = rawH; w = h * ratio; }
        if (w < MIN) { w = MIN; h = w / ratio; }
        // keep inside the image measured from the fixed anchor
        var availW = (kind === "nw" || kind === "sw") ? ax : (r.width - ax);
        var availH = (kind === "nw" || kind === "ne") ? ay : (r.height - ay);
        if (w > availW) { w = availW; h = w / ratio; }
        if (h > availH) { h = availH; w = h * ratio; }
        var nx = (kind === "nw" || kind === "sw") ? ax - w : ax;
        var ny = (kind === "nw" || kind === "ne") ? ay - h : ay;
        S.sel = { x: nx, y: ny, w: w, h: h };
      }
      renderSel(); renderPreview();
    }
    function onUp() { if (S) S.drag = null; }
    els.stage.addEventListener("mousedown", onDown);
    els.stage.addEventListener("touchstart", onDown, { passive: false });
    document.addEventListener("mousemove", onMove);
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("mouseup", onUp);
    document.addEventListener("touchend", onUp);
    mounted = true;
  }

  function filterStr() {
    return "grayscale(" + (S.gray ? 1 : 0) + ") brightness(" + S.bright + ") contrast(" + S.contrast + ")";
  }

  // Largest centred box-ratio rectangle that fits the current display image.
  function setDefaultSel() {
    var r = els.img.getBoundingClientRect(), rw = r.width, rh = r.height;
    if (!rw || !rh) return;
    var ratio = S.ratio || (16 / 9), w, h;
    if (rw / rh > ratio) { h = rh; w = h * ratio; } else { w = rw; h = w / ratio; }
    S.sel = { x: (rw - w) / 2, y: (rh - h) / 2, w: w, h: h };
  }

  function renderWork() {
    var n = S.natural, a = ((S.angle % 360) + 360) % 360;
    var cw = (a === 90 || a === 270) ? n.height : n.width;
    var ch = (a === 90 || a === 270) ? n.width : n.height;
    var c = document.createElement("canvas"); c.width = cw; c.height = ch;
    var ctx = c.getContext("2d");
    ctx.translate(cw / 2, ch / 2); ctx.rotate(a * Math.PI / 180); ctx.drawImage(n, -n.width / 2, -n.height / 2);
    S.work = c;
    els.img.src = c.toDataURL("image/png");  // triggers els.img.onload -> setDefaultSel + render
    applyFilter();
  }

  function applyFilter() {
    els.img.style.filter = filterStr();
    renderPreview();
  }

  function renderSel() {
    var show = (S.mode === "crop" && S.sel && S.sel.w >= 2 && S.sel.h >= 2);
    els.frame.classList.toggle("on", !!show);
    if (!show) return;
    els.frame.style.left = (els.img.offsetLeft + S.sel.x) + "px";
    els.frame.style.top = (els.img.offsetTop + S.sel.y) + "px";
    els.frame.style.width = S.sel.w + "px";
    els.frame.style.height = S.sel.h + "px";
  }

  function sizePreview() {
    var ratio = S.ratio || (16 / 9), pw, ph, ss = 2;
    if (ratio >= 1) { pw = PREVMAX; ph = Math.round(PREVMAX / ratio); }
    else { ph = PREVMAX; pw = Math.round(PREVMAX * ratio); }
    els.prev.width = pw * ss; els.prev.height = ph * ss;   // 2x backing store -> crisp preview
    els.prev.style.width = pw + "px"; els.prev.style.height = ph + "px";
    return { pw: pw * ss, ph: ph * ss };                   // draw in device px
  }

  function renderPreview() {
    if (!S || !S.work) return;
    var d = sizePreview(), pw = d.pw, ph = d.ph;
    var ctx = els.prev.getContext("2d");
    ctx.imageSmoothingEnabled = true; try { ctx.imageSmoothingQuality = "high"; } catch (e) {}
    ctx.clearRect(0, 0, pw, ph);
    ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, pw, ph);
    try { ctx.filter = filterStr(); } catch (e) {}
    if (S.mode === "fit") {
      // Keep the whole image; stretch it to fill the box ratio (width preserved,
      // height stretched) instead of letterboxing with a white border.
      ctx.drawImage(S.work, 0, 0, S.work.width, S.work.height, 0, 0, pw, ph);
    } else if (S.sel) {
      var r = els.img.getBoundingClientRect();
      var scale = S.work.width / r.width;   // work px per display px
      var sx = S.sel.x * scale, sy = S.sel.y * scale, sw = S.sel.w * scale, sh = S.sel.h * scale;
      ctx.drawImage(S.work, sx, sy, Math.max(1, sw), Math.max(1, sh), 0, 0, pw, ph);
    }
    try { ctx.filter = "none"; } catch (e) {}
  }

  function setMode(m) {
    if (!S) return;
    S.mode = m;
    els.mcrop.classList.toggle("active", m === "crop");
    els.mfit.classList.toggle("active", m === "fit");
    els.leftcap.textContent = (m === "fit")
      ? "Whole image — stretched to fill the frame"
      : "Original — adjust the frame";
    els.hint.textContent = (m === "fit")
      ? "The full image is kept; the width is preserved and the height is stretched to fill."
      : "Drag inside the frame to move; drag a corner to resize.";
    if (m === "crop" && (!S.sel || S.sel.w < 2)) setDefaultSel();
    renderSel(); renderPreview();
  }

  function resetAll() {
    S.angle = 0; S.ratio = S.boxRatio; S.gray = false; S.bright = 1; S.contrast = 1; S.sel = null; S.drag = null;
    els.gray.classList.remove("active");
    els.bright.value = 100; els.contrast.value = 100;
    setMode("crop");
    renderWork();
  }

  function baseName(name) { return (name || "image").replace(/\.[^.]+$/, ""); }

  function apply() {
    var work = S.work; if (!work) { close(); return; }
    var out = document.createElement("canvas"), octx;
    if (S.mode === "fit") {
      // whole image, STRETCHED into the box ratio: the width is preserved and the
      // height is stretched to fill, so nothing is cropped and no white border is added
      var ratio = S.ratio;
      var OW = Math.max(1, work.width), OH = Math.max(1, Math.round(work.width / ratio));
      out.width = OW; out.height = OH;
      octx = out.getContext("2d");
      try { octx.filter = filterStr(); } catch (e) {}
      octx.drawImage(work, 0, 0, work.width, work.height, 0, 0, OW, OH);
    } else {
      // crop to the selected frame (falls back to the whole image)
      var rect = els.img.getBoundingClientRect();
      var scale = work.width / rect.width, sx, sy, sw, sh;
      if (S.sel && S.sel.w > 4 && S.sel.h > 4) {
        sx = S.sel.x * scale; sy = S.sel.y * scale; sw = S.sel.w * scale; sh = S.sel.h * scale;
      } else { sx = 0; sy = 0; sw = work.width; sh = work.height; }
      out.width = Math.max(1, Math.round(sw)); out.height = Math.max(1, Math.round(sh));
      octx = out.getContext("2d");
      try { octx.filter = filterStr(); } catch (e) {}
      octx.drawImage(work, sx, sy, sw, sh, 0, 0, out.width, out.height);
    }
    var isPng = (S.file.type === "image/png");
    var cb = S.onApply;
    out.toBlob(function (blob) {
      var f = new File([blob], baseName(S.file.name) + "_edited." + (isPng ? "png" : "jpg"),
                       { type: blob.type || (isPng ? "image/png" : "image/jpeg") });
      close();
      if (cb) cb(f);
    }, isPng ? "image/png" : "image/jpeg", 0.95);
  }

  function close() { if (els.ov) els.ov.classList.remove("open"); }

  function boxRatioOf(box) {
    return (box && box[0] && box[1]) ? (box[0] / box[1]) : (16 / 9);
  }

  function open(opts) {
    if (!opts || !opts.file) return;
    mount();
    var box = opts.boxMM || [150, 90];
    var ratio = boxRatioOf(box);
    S = { file: opts.file, box: box, boxRatio: ratio, onApply: opts.onApply,
          angle: 0, ratio: ratio, gray: false, bright: 1, contrast: 1, sel: null, drag: null,
          mode: "crop", natural: null, work: null };
    els.gray.classList.remove("active"); els.bright.value = 100; els.contrast.value = 100;
    setMode("crop");
    var img = new Image();
    img.onload = function () { S.natural = img; renderWork(); els.ov.classList.add("open"); };
    img.onerror = function () { alert("Could not load the image for editing."); };
    img.src = URL.createObjectURL(opts.file);
  }

  // Centre-crop `opts.file` to the box ratio and hand back the cropped File via
  // opts.onApply WITHOUT opening the dialog. Used to conform an image on upload so
  // the document preview matches the slot immediately; the caller keeps the
  // original around so the user can still reframe (open) or stretch it to fill later.
  function autoFit(opts) {
    if (!opts || !opts.file) { if (opts && opts.onApply) opts.onApply(null); return; }
    var box = opts.boxMM || [150, 90];
    var ratio = boxRatioOf(box);
    var isPng = (opts.file.type === "image/png");
    var img = new Image();
    img.onload = function () {
      var nw = img.naturalWidth, nh = img.naturalHeight, cw, ch;
      if (!nw || !nh) { if (opts.onApply) opts.onApply(opts.file); return; }
      if (nw / nh > ratio) { ch = nh; cw = ch * ratio; } else { cw = nw; ch = cw / ratio; }
      var sx = (nw - cw) / 2, sy = (nh - ch) / 2;
      var out = document.createElement("canvas");
      out.width = Math.max(1, Math.round(cw)); out.height = Math.max(1, Math.round(ch));
      out.getContext("2d").drawImage(img, sx, sy, cw, ch, 0, 0, out.width, out.height);
      out.toBlob(function (blob) {
        if (!blob) { if (opts.onApply) opts.onApply(opts.file); return; }
        var f = new File([blob], baseName(opts.file.name) + "_fit." + (isPng ? "png" : "jpg"),
                         { type: blob.type || (isPng ? "image/png" : "image/jpeg") });
        if (opts.onApply) opts.onApply(f);
      }, isPng ? "image/png" : "image/jpeg", 0.95);
    };
    img.onerror = function () { if (opts.onApply) opts.onApply(opts.file); };
    img.src = URL.createObjectURL(opts.file);
  }

  window.DSImageEditor = { open: open, autoFit: autoFit };
})();
