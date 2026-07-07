/* Shared image editor for the datasheet Preview module.
 * Used by both ce_form.html and generic_form.html.
 *
 *   DSImageEditor.open({
 *     file:   File,                 // the current image
 *     boxMM:  [widthMM, heightMM],  // the document box this image lands in
 *     onApply: function(newFile){}  // called with the edited image File
 *   });
 *
 * Tools: crop locked to the document box ratio (boxMM), auto-fitted (centred) to
 * any upload; rotate 90 L/R; grayscale; brightness & contrast. Also exposes
 * DSImageEditor.autoFit({file, boxMM, onApply}) which centre-crops to the box
 * ratio WITHOUT opening the dialog (used to conform an image on upload).
 * No external dependencies.
 */
(function () {
  "use strict";
  var mounted = false, els = {}, S = null;

  var CSS = [
    ".dsie-ov{position:fixed;inset:0;z-index:1100;display:none;}",
    ".dsie-ov.open{display:block;}",
    ".dsie-bg{position:absolute;inset:0;background:rgba(0,0,0,.7);}",
    ".dsie-mid{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;padding:16px;overflow:auto;}",
    ".dsie-panel{position:relative;background:#fff;border-radius:12px;box-shadow:0 25px 50px rgba(0,0,0,.45);width:100%;max-width:880px;margin:auto;}",
    ".dsie-head{display:flex;align-items:center;justify-content:space-between;padding:12px 18px;border-bottom:1px solid #e5e7eb;}",
    ".dsie-head h3{margin:0;font-size:16px;font-weight:700;color:#0f172a;}",
    ".dsie-head p{margin:2px 0 0;font-size:11px;color:#64748b;}",
    ".dsie-x{border:none;background:none;font-size:22px;line-height:1;color:#9ca3af;cursor:pointer;}",
    ".dsie-tools{display:flex;flex-wrap:wrap;gap:12px 20px;align-items:center;padding:11px 18px;border-bottom:1px solid #eef2f7;background:#fafbfc;}",
    ".dsie-grp{display:flex;align-items:center;gap:6px;font-size:12px;color:#334155;}",
    ".dsie-grp .lbl{font-weight:600;color:#64748b;}",
    ".dsie-chip{font-size:12px;padding:4px 9px;border:1px solid #cbd5e1;background:#fff;border-radius:7px;cursor:pointer;color:#334155;}",
    ".dsie-chip.active{border-color:#059669;background:#ecfdf5;color:#047857;font-weight:600;}",
    ".dsie-cropnote{font-size:12px;color:#334155;background:#ecfdf5;border:1px solid #a7f3d0;border-radius:7px;padding:4px 9px;}",
    ".dsie-range{vertical-align:middle;width:96px;}",
    ".dsie-stagewrap{padding:16px 18px;text-align:center;background:#0f172a;}",
    ".dsie-stage{position:relative;display:inline-block;max-width:100%;user-select:none;touch-action:none;line-height:0;}",
    ".dsie-stage img{max-width:100%;max-height:54vh;display:block;}",
    ".dsie-sel{position:absolute;border:2px dashed #34d399;display:none;pointer-events:none;box-shadow:0 0 0 9999px rgba(0,0,0,.45);}",
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
    '      <div><h3>Edit image</h3><p>Crop, rotate, grayscale and adjust brightness/contrast. Applied to the image used in the generated document.</p></div>' +
    '      <button type="button" class="dsie-x" data-act="cancel">&times;</button>' +
    '    </div>' +
    '    <div class="dsie-tools">' +
    '      <div class="dsie-grp"><span class="lbl">Crop</span><span class="dsie-cropnote" id="dsie-cropnote">drag to re-position</span></div>' +
    '      <div class="dsie-grp"><span class="lbl">Rotate</span>' +
    '        <button type="button" class="dsie-chip" data-act="rotL">&#8634; 90&deg;</button>' +
    '        <button type="button" class="dsie-chip" data-act="rotR">90&deg; &#8635;</button></div>' +
    '      <div class="dsie-grp"><button type="button" class="dsie-chip" id="dsie-gray" data-act="gray">B&amp;W</button></div>' +
    '      <div class="dsie-grp"><span class="lbl">Brightness</span><input id="dsie-bright" class="dsie-range" type="range" min="50" max="150" value="100"></div>' +
    '      <div class="dsie-grp"><span class="lbl">Contrast</span><input id="dsie-contrast" class="dsie-range" type="range" min="50" max="150" value="100"></div>' +
    '    </div>' +
    '    <div class="dsie-stagewrap">' +
    '      <div class="dsie-stage" id="dsie-stage"><img id="dsie-img" alt="edit"><div class="dsie-sel" id="dsie-sel"></div></div>' +
    '    </div>' +
    '    <div class="dsie-foot">' +
    '      <div><button type="button" class="dsie-b link" data-act="reset">Reset all</button>' +
    '        <span class="dsie-hint" id="dsie-hint"></span></div>' +
    '      <div class="right">' +
    '        <button type="button" class="dsie-b" data-act="clearsel">Clear crop</button>' +
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
    els.sel = ov.querySelector("#dsie-sel");
    els.stage = ov.querySelector("#dsie-stage");
    els.gray = ov.querySelector("#dsie-gray");
    els.bright = ov.querySelector("#dsie-bright");
    els.contrast = ov.querySelector("#dsie-contrast");
    els.hint = ov.querySelector("#dsie-hint");
    els.cropnote = ov.querySelector("#dsie-cropnote");
    els.img.onload = function () { if (S) setDefaultSel(); };  // auto-fit a box-ratio crop to any upload

    ov.addEventListener("click", function (e) {
      var act = e.target.getAttribute("data-act");
      if (e.target.getAttribute("data-close") === "1") { close(); return; }
      if (!act) return;
      if (act === "cancel") close();
      else if (act === "apply") apply();
      else if (act === "rotL") { S.angle -= 90; renderWork(); }
      else if (act === "rotR") { S.angle += 90; renderWork(); }
      else if (act === "gray") { S.gray = !S.gray; els.gray.classList.toggle("active", S.gray); applyFilter(); }
      else if (act === "clearsel") { S.sel = null; renderSel(); }
      else if (act === "reset") resetAll();
    });
    els.bright.addEventListener("input", function () { S.bright = this.value / 100; applyFilter(); });
    els.contrast.addEventListener("input", function () { S.contrast = this.value / 100; applyFilter(); });

    // crop drag
    function pt(e) {
      var r = els.img.getBoundingClientRect();
      var cx = (e.touches ? e.touches[0].clientX : e.clientX), cy = (e.touches ? e.touches[0].clientY : e.clientY);
      return { x: Math.min(Math.max(cx - r.left, 0), r.width), y: Math.min(Math.max(cy - r.top, 0), r.height), rw: r.width, rh: r.height };
    }
    function down(e) { if (e.target !== els.img && e.target !== els.sel && e.target !== els.stage) return; e.preventDefault(); var p = pt(e); S.sx = p.x; S.sy = p.y; S.drawing = true; S.sel = { x: p.x, y: p.y, w: 0, h: 0 }; renderSel(); }
    function move(e) {
      if (!S || !S.drawing) return;
      var p = pt(e), x0 = S.sx, y0 = S.sy, dirX = (p.x >= x0) ? 1 : -1, dirY = (p.y >= y0) ? 1 : -1;
      var aw = Math.abs(p.x - x0), ah = Math.abs(p.y - y0);
      if (S.ratio) {                                  // lock aspect ratio (w/h)
        aw = Math.min(aw, dirX > 0 ? p.rw - x0 : x0);
        ah = aw / S.ratio;
        var maxH = dirY > 0 ? p.rh - y0 : y0;
        if (ah > maxH) { ah = maxH; aw = ah * S.ratio; }
      }
      S.sel = { x: dirX > 0 ? x0 : x0 - aw, y: dirY > 0 ? y0 : y0 - ah, w: aw, h: ah };
      renderSel();
    }
    function up() { if (S) S.drawing = false; }
    els.stage.addEventListener("mousedown", down); document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
    els.stage.addEventListener("touchstart", down, { passive: false }); document.addEventListener("touchmove", move, { passive: false }); document.addEventListener("touchend", up);
    mounted = true;
  }

  // Auto-fit the largest centred crop matching the document box ratio (S.ratio,
  // derived from boxMM) to the current (rotated) image, so the default framing
  // equals the document slot the image will occupy.
  function setDefaultSel() {
    var r = els.img.getBoundingClientRect(), rw = r.width, rh = r.height;
    if (!rw || !rh) return;
    var ratio = S.ratio || (16 / 9);
    var w, h;
    if (rw / rh > ratio) { h = rh; w = h * ratio; } else { w = rw; h = w / ratio; }
    S.sel = { x: (rw - w) / 2, y: (rh - h) / 2, w: w, h: h };
    renderSel();
  }

  function applyFilter() {
    els.img.style.filter = "grayscale(" + (S.gray ? 1 : 0) + ") brightness(" + S.bright + ") contrast(" + S.contrast + ")";
  }

  function renderWork() {
    var n = S.natural, a = ((S.angle % 360) + 360) % 360;
    var cw = (a === 90 || a === 270) ? n.height : n.width;
    var ch = (a === 90 || a === 270) ? n.width : n.height;
    var c = document.createElement("canvas"); c.width = cw; c.height = ch;
    var ctx = c.getContext("2d");
    ctx.translate(cw / 2, ch / 2); ctx.rotate(a * Math.PI / 180); ctx.drawImage(n, -n.width / 2, -n.height / 2);
    S.work = c;
    els.img.src = c.toDataURL("image/png");
    S.sel = null; renderSel(); applyFilter();
  }

  function renderSel() {
    if (!S.sel || S.sel.w < 2 || S.sel.h < 2) { els.sel.style.display = "none"; els.hint.textContent = ""; return; }
    els.sel.style.display = "block";
    els.sel.style.left = (els.img.offsetLeft + S.sel.x) + "px";
    els.sel.style.top = (els.img.offsetTop + S.sel.y) + "px";
    els.sel.style.width = S.sel.w + "px";
    els.sel.style.height = S.sel.h + "px";
    els.hint.textContent = "crop selected";
  }

  function resetAll() {
    S.angle = 0; S.ratio = S.boxRatio || (16 / 9); S.gray = false; S.bright = 1; S.contrast = 1; S.sel = null;
    els.gray.classList.remove("active");
    els.bright.value = 100; els.contrast.value = 100;
    renderWork();
  }

  function baseName(name) { return (name || "image").replace(/\.[^.]+$/, ""); }

  function apply() {
    var work = S.work; if (!work) { close(); return; }
    var rect = els.img.getBoundingClientRect();
    var scale = work.width / rect.width;
    var sx, sy, sw, sh;
    if (S.sel && S.sel.w > 4 && S.sel.h > 4) {
      sx = S.sel.x * scale; sy = S.sel.y * scale; sw = S.sel.w * scale; sh = S.sel.h * scale;
    } else { sx = 0; sy = 0; sw = work.width; sh = work.height; }
    var out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(sw)); out.height = Math.max(1, Math.round(sh));
    var octx = out.getContext("2d");
    try { octx.filter = "grayscale(" + (S.gray ? 1 : 0) + ") brightness(" + S.bright + ") contrast(" + S.contrast + ")"; } catch (e) {}
    octx.drawImage(work, sx, sy, sw, sh, 0, 0, out.width, out.height);
    var isPng = (S.file.type === "image/png");
    var cb = S.onApply;
    out.toBlob(function (blob) {
      var f = new File([blob], baseName(S.file.name) + "_edited." + (isPng ? "png" : "jpg"), { type: blob.type || (isPng ? "image/png" : "image/jpeg") });
      close();
      if (cb) cb(f);
    }, isPng ? "image/png" : "image/jpeg", 0.92);
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
          angle: 0, ratio: ratio, gray: false, bright: 1, contrast: 1, sel: null, drawing: false, natural: null, work: null };
    els.gray.classList.remove("active"); els.bright.value = 100; els.contrast.value = 100;
    if (els.cropnote) els.cropnote.textContent = box[0] + " × " + box[1] + " mm — drag to re-position";
    var img = new Image();
    img.onload = function () { S.natural = img; renderWork(); els.ov.classList.add("open"); };
    img.onerror = function () { alert("Could not load the image for editing."); };
    img.src = URL.createObjectURL(opts.file);
  }

  // Centre-crop `opts.file` to the box ratio and hand back the cropped File via
  // opts.onApply WITHOUT opening the dialog. Used to conform an image on upload
  // so the preview matches the document slot immediately; the caller keeps the
  // original around so the user can still reframe a different portion via open().
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
      }, isPng ? "image/png" : "image/jpeg", 0.92);
    };
    img.onerror = function () { if (opts.onApply) opts.onApply(opts.file); };
    img.src = URL.createObjectURL(opts.file);
  }

  window.DSImageEditor = { open: open, autoFit: autoFit };
})();
