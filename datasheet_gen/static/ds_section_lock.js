/* Per-section "fixed value" lock for the CE and generic datasheet forms.
 *
 * Fields that arrive pre-filled with a fixed / auto-derived value are tagged in
 * the template with `data-lock="1"`. On load they render read-only and greyed.
 * Each section that contains at least one such field gets a small pencil button
 * in its top-right corner; clicking it unlocks every locked field in THAT section
 * for editing (and back). The manually-entered fields (EUT Input Voltage &
 * Frequency, Ambient Temperature, Relative Humidity, Tested By, dates, ...) are
 * never tagged, so they stay ordinary editable inputs.
 *
 * Locked inputs use `readonly` (NOT `disabled`) and locked selects stay enabled,
 * so their values are still submitted with the form / captured on autosave.
 */
(function () {
  "use strict";

  var CSS = [
    ".ds-locked{background-color:#f3f4f6 !important;color:#6b7280 !important;",
    "  cursor:not-allowed !important;border-color:#e5e7eb !important;box-shadow:none !important;}",
    "select.ds-locked{pointer-events:none;}",
    ".ds-edit-btn{position:absolute;top:1rem;right:1rem;z-index:5;display:inline-flex;",
    "  align-items:center;gap:.35rem;font-size:.72rem;font-weight:600;line-height:1;",
    "  padding:.35rem .6rem;border-radius:.5rem;border:1px solid #bfdbfe;color:#2563eb;",
    "  background:#eff6ff;cursor:pointer;transition:background .15s,border-color .15s,color .15s;}",
    ".ds-edit-btn:hover{background:#dbeafe;}",
    ".ds-edit-btn.ds-edit-active{border-color:#86efac;color:#15803d;background:#f0fdf4;}",
    ".ds-edit-btn.ds-edit-active:hover{background:#dcfce7;}"
  ].join("");

  function injectCss() {
    if (document.getElementById("ds-section-lock-css")) return;
    var s = document.createElement("style");
    s.id = "ds-section-lock-css";
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  function setLocked(el, locked) {
    if (el.tagName.toLowerCase() === "select") {
      el.classList.toggle("ds-locked", locked);
      if (locked) {
        el.setAttribute("tabindex", "-1");
        el.setAttribute("aria-disabled", "true");
      } else {
        el.removeAttribute("tabindex");
        el.removeAttribute("aria-disabled");
      }
    } else {
      el.readOnly = locked;
      el.classList.toggle("ds-locked", locked);
    }
  }

  function iconEdit() { return "✎ Edit"; }        // pencil
  function iconDone() { return "✓ Done"; }        // check

  function wireSection(section) {
    var locked = Array.prototype.slice.call(
      section.querySelectorAll('[data-lock="1"]')
    );
    if (!locked.length) return;

    // Start locked.
    locked.forEach(function (el) { setLocked(el, true); });

    // The section is the positioning context for the absolute button.
    var cs = window.getComputedStyle(section);
    if (cs.position === "static") section.style.position = "relative";

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "ds-edit-btn";
    btn.setAttribute("data-editing", "0");
    btn.title = "Edit the fixed values in this section";
    btn.innerHTML = iconEdit();

    btn.addEventListener("click", function () {
      var willEdit = btn.getAttribute("data-editing") !== "1";
      locked.forEach(function (el) { setLocked(el, !willEdit); });
      btn.setAttribute("data-editing", willEdit ? "1" : "0");
      btn.innerHTML = willEdit ? iconDone() : iconEdit();
      btn.classList.toggle("ds-edit-active", willEdit);
      if (willEdit && locked[0]) {
        try { locked[0].focus(); } catch (e) { /* ignore */ }
      }
    });

    section.appendChild(btn);
  }

  function init() {
    var form = document.getElementById("ceForm") || document.getElementById("dsForm");
    if (!form) return;                 // not a datasheet form page
    injectCss();
    Array.prototype.forEach.call(form.querySelectorAll("section"), wireSection);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
