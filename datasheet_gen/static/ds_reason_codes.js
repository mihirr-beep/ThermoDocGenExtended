/* Fill any <select data-reason-family="..."> from the lab's reason taxonomy.
 *
 * WHY THIS IS FETCHED AND NOT RENDERED SERVER-SIDE
 * The same vocabulary has to appear in three places that must never disagree:
 * this dropdown, the API that validates what the dropdown submits, and the NLP
 * catalog that tells the model which codes exist. emc_reason_code is the single
 * source, so all three read it. A Jinja-rendered <option> list would have been
 * a fourth copy, and the lab can add a finding to the table without a deploy.
 *
 * Shared by ce_form and generic_form: the field is identical on both and a
 * second copy would drift.
 *
 * Degrades to "Not categorised" if the request fails or the table is absent.
 * A datasheet must always be savable; categorising the failure is a bonus.
 */
(function () {
  var selects = document.querySelectorAll('select[data-reason-family]');
  if (!selects.length) return;

  fetch('/api/reason-codes', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : {}; })
    .then(function (res) {
      Array.prototype.forEach.call(selects, function (sel) {
        var codes = res[sel.getAttribute('data-reason-family')] || [];
        // What the form was rendered with, so a saved code is not duplicated
        // and stays selected once the real label arrives.
        var chosen = sel.value;
        var seen = {};
        Array.prototype.forEach.call(sel.options, function (o) { seen[o.value] = o; });
        codes.forEach(function (c) {
          if (seen[c.code]) {
            seen[c.code].textContent = c.label;   // upgrade the placeholder label
            return;
          }
          var o = document.createElement('option');
          o.value = c.code;
          o.textContent = c.label;
          sel.appendChild(o);
        });
        if (chosen) sel.value = chosen;
      });
    })
    .catch(function () { /* leave the selects as rendered */ });
})();
