/* Read-only pass for a datasheet form.
 *
 * WHY THIS EXISTS
 * ---------------
 * The report wizard shows each test's datasheet form so an admin can see exactly
 * what the lab engineer entered. It must not be editable from there: a datasheet
 * moves Draft -> Peer Review -> Approved, and the report is built from the
 * APPROVED document. An admin saving from inside the report wizard would edit a
 * record that has already been reviewed, or reassign a reviewer, without any of
 * that appearing in the review trail.
 *
 * WHY A DOM PASS AND NOT A SECOND TEMPLATE
 * ----------------------------------------
 * A read-only copy of ce_form.html / generic_form.html would be two thousand
 * lines duplicated, and it would drift - which is the whole reason the wizard
 * embeds the real form instead of re-rendering it. This walks whatever the form
 * rendered, so a field added to the datasheet UI tomorrow is locked here without
 * anyone remembering to.
 *
 * WHAT IT IS NOT
 * --------------
 * Not an authorisation control. The save endpoints check permissions themselves,
 * and an admin has always been allowed to edit a datasheet from the planner -
 * that is the right route for it. This removes the invitation to do it from the
 * report wizard by mistake; it does not remove the ability.
 *
 * Loaded only when the form route was asked for a read-only render, and it also
 * checks the URL itself, so dropping the file on a page cannot silently lock it.
 */
(function () {
  "use strict";

  function param(name) {
    try {
      return new URLSearchParams(window.location.search).get(name);
    } catch (e) { return null; }
  }
  var wanted = param("view") === "1" || param("readonly") === "1";
  // Embedded in the report wizard, which prints its own read-only notice right
  // above the frame. A second banner inside it says the same thing twice and eats
  // the height the datasheet needs.
  var embedded = param("embed") === "1";
  if (!wanted) return;

  var FORM_IDS = ["dsForm", "ceForm"];
  // The action bar's controls, by id, in both forms. The bar itself is found by
  // walking up from whichever of these exists rather than by matching a class -
  // Tailwind class strings on that div are layout, and change.
  var ACTION_IDS = ["draftBtn", "genBtn", "genFinalBtn", "draftDocxBtn",
                    "peerReviewerSel", "autoSaveStatus"];

  function form() {
    for (var i = 0; i < FORM_IDS.length; i++) {
      var f = document.getElementById(FORM_IDS[i]);
      if (f) return f;
    }
    return document.querySelector("form");
  }

  function lockField(el) {
    if (el.dataset.roDone === "1") return;
    el.dataset.roDone = "1";
    // disabled, not just readonly: readonly leaves selects, checkboxes, radios
    // and file inputs fully operable. The value still renders either way, which
    // is the point - this is a view of what was entered.
    el.disabled = true;
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") el.readOnly = true;
    el.classList.add("ds-readonly-field");
  }

  function lockAll(root) {
    var els = (root || document).querySelectorAll("input, select, textarea, button");
    Array.prototype.forEach.call(els, function (el) {
      if (el.closest("#dsReadonlyBar")) return;      // our own banner
      lockField(el);
    });
  }

  function hideActions() {
    var anchor = null;
    ACTION_IDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (!el) return;
      if (!anchor && (id === "genBtn" || id === "draftBtn")) anchor = el;
      el.style.display = "none";
    });
    // The bar holds a Cancel link and the "Peer reviewer" label too, so hide the
    // container once found - otherwise a read-only page still offers a reviewer
    // dropdown label and a Cancel that navigates the iframe to the planner.
    if (anchor && anchor.parentElement) anchor.parentElement.style.display = "none";
  }

  function banner() {
    if (embedded) return;
    if (document.getElementById("dsReadonlyBar")) return;
    var f = form();
    if (!f) return;
    var d = document.createElement("div");
    d.id = "dsReadonlyBar";
    d.style.cssText = "margin:0 0 14px;padding:10px 14px;border:1px solid #f5c26b;" +
      "background:#fff8e6;border-radius:8px;color:#7a4b00;font-size:13px;line-height:1.5;";
    d.innerHTML = "<strong>Read-only.</strong> This is the datasheet as the lab " +
      "engineer submitted it, shown so you can check what the report will contain. " +
      "It follows its own review pipeline &mdash; to change anything here, open the " +
      "test from the planner and take it through peer review again.";
    f.parentNode.insertBefore(d, f);
  }

  function apply() {
    banner();
    lockAll(document);
    hideActions();
    // Autosave fires on input. Nothing can change now, but the form also calls it
    // from row-add helpers, so it is neutralised rather than trusted.
    try {
      if (typeof window.triggerAutoSave === "function") {
        window.triggerAutoSave = function () {};
      }
    } catch (e) { /* not all forms define it */ }
  }

  function start() {
    apply();
    // Both forms build grids, extra-photo slots and observation rows with
    // JavaScript AFTER load, so a single pass would miss whatever appears next.
    try {
      new MutationObserver(function (muts) {
        for (var i = 0; i < muts.length; i++) {
          var added = muts[i].addedNodes;
          for (var j = 0; j < added.length; j++) {
            if (added[j].nodeType === 1) lockAll(added[j]);
          }
        }
      }).observe(document.body, {childList: true, subtree: true});
    } catch (e) { /* very old browser: the initial pass still applied */ }
    // One more pass after the form's own load handlers have run.
    window.setTimeout(apply, 400);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  var css = document.createElement("style");
  css.textContent =
    ".ds-readonly-field{background:#f7f8f9 !important;color:#3c4552 !important;" +
    "cursor:default !important;opacity:1 !important}" +
    /* Word-processor grey on a disabled control makes measured values hard to
       read, and reading them is the entire purpose of this view. */
    ".ds-readonly-field:disabled{-webkit-text-fill-color:#3c4552}";
  document.head.appendChild(css);
})();
