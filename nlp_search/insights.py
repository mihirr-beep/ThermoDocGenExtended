# -*- coding: utf-8 -*-
"""Analysis primitives: the arithmetic behind "why", done in SQL not by a model.

WHY NOT LET THE MODEL WRITE THIS SQL
------------------------------------
Every question this module serves needs a self-join over datasheet_measurement,
windowed across two campaigns and pivoted from its long form back into rows.
That is hard SQL. gpt-5-nano can write it, and most of the time it will be
right - but when it is wrong it does not raise, it returns a number. A lab that
cannot tell a computed 5.3 dB improvement from an invented one has gained
nothing over guessing.

So the model's job shrinks to what it is reliably good at: reading the question,
choosing a primitive, and filling in a product name. The arithmetic is here,
where it is deterministic, reviewable, and testable. This is the same lesson the
grounding work already produced - the model understands intent fine; it fails
downstream of understanding.

WHAT THESE DELIBERATELY DO NOT DO
---------------------------------
None of them returns a cause. metric_delta says the 0.72 MHz margin improved by
8.2 dB between the first failure and the pass, and modifications_before_pass
says a common-mode choke was fitted in between. That those two facts are
related is an inference a competent engineer will draw and this system must not
assert: the database records what changed and what the reviewer wrote, never
why. Presenting evidence FOR a cause is useful and honest. Presenting it AS a
cause is the failure mode that makes people stop trusting the tool.

THE TWO AXES
------------
Nothing here mixes them. A product's failure lives in datasheet_revision.result
and failure_reason_code; a record's rejection lives in
datasheet_status_history.reason_code. "Failed" without qualification is
ambiguous in this building, and answering the wrong one confidently is worse
than saying nothing.
"""
import json
import logging

from sqlalchemy import text

log = logging.getLogger(__name__)

# Every primitive returns rows carrying the campaign's TCO and the product name,
# never a bare number. A metric with nothing naming the thing it measures is how
# an answer ends up attached to the wrong product.
_CAMPAIGN_JOIN = """
    FROM `datasheet` d
    JOIN planner_entries p ON p.id = d.planner_entry_id
    JOIN iec_emc_requests r ON r.id = p.test_request_id
"""


def _rows(db, sql, **params):
    return [dict(m) for m in db.session.execute(text(sql), params).mappings().all()]


# ---------------------------------------------------------------------------
# 1. timeline - "give me the testing history of Product ABC"
# ---------------------------------------------------------------------------

def timeline(db, product=None, tco=None, limit=60):
    """Every campaign for a product, oldest first, with both axes attached.

    One row per campaign, not per revision: a re-issued record is the same test
    and listing it twice would read as an extra attempt the unit never made.
    review_rounds carries that instead.
    """
    where, params = [], {"lim": int(limit)}
    if product:
        where.append("r.product_name LIKE :prod")
        params["prod"] = "%" + product + "%"
    if tco:
        where.append("r.tco_id = :tco")
        params["tco"] = tco
    if not where:
        return []
    sql = """
        SELECT r.tco_id, r.product_name, r.model_number, d.test_code,
               d.test_date, d.result, d.failure_reason_code,
               d.met_performance_criteria,
               (SELECT COUNT(*) FROM datasheet_revision v
                 WHERE v.datasheet_id = d.id) AS review_rounds,
               (SELECT h.reason_code FROM datasheet_status_history h
                 WHERE h.datasheet_id = d.id AND h.to_status = 'Rejected'
                   AND h.reason_code IS NOT NULL LIMIT 1) AS record_rejected_for,
               r.is_synthetic
    """ + _CAMPAIGN_JOIN + " WHERE " + " AND ".join(where) + """
        ORDER BY d.test_date, r.tco_id LIMIT :lim
    """
    return _rows(db, sql, **params)


# ---------------------------------------------------------------------------
# 2. failure_detail - "why did it fail its first three tests?"
# ---------------------------------------------------------------------------

def failure_detail(db, product=None, tco=None, limit=40):
    """The failing campaigns, each with the readings that actually breached.

    The breach list is computed from qp_margin > 0 rather than trusted from a
    summary column: the margin is the measurement's own arithmetic, so a row
    here cannot claim a breach the numbers do not support.

    Pinned to the campaign's LATEST revision. Without that, a campaign whose
    record was re-issued after a review rejection returned every reading twice
    - once from the withdrawn revision, once from the replacement - and the
    duplicate looked exactly like a second genuine breach at the same
    frequency. A re-issued record is the same test, not another failure.
    """
    camps = [c for c in timeline(db, product=product, tco=tco, limit=limit)
             if (c.get("result") or "").upper() == "FAIL"]
    out = []
    for c in camps:
        breaches = _rows(db, """
            SELECT f.value AS frequency_mhz, q.value AS measured,
                   l.value AS limit_value, g.value AS margin_db, q.grid_key
            FROM datasheet_measurement g
            JOIN `datasheet` d ON d.id = g.datasheet_id
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            JOIN datasheet_measurement f ON f.datasheet_id = g.datasheet_id
                 AND f.revision_no = g.revision_no AND f.grid_key = g.grid_key
                 AND f.row_no = g.row_no AND f.col_key = 'qp_freq'
            JOIN datasheet_measurement q ON q.datasheet_id = g.datasheet_id
                 AND q.revision_no = g.revision_no AND q.grid_key = g.grid_key
                 AND q.row_no = g.row_no AND q.col_key = 'qp'
            JOIN datasheet_measurement l ON l.datasheet_id = g.datasheet_id
                 AND l.revision_no = g.revision_no AND l.grid_key = g.grid_key
                 AND l.row_no = g.row_no AND l.col_key = 'qp_limit'
            WHERE r.tco_id = :tco AND g.col_key = 'qp_margin' AND g.value_num > 0
              AND g.revision_no = (SELECT MAX(m2.revision_no)
                                     FROM datasheet_measurement m2
                                    WHERE m2.datasheet_id = g.datasheet_id)
            ORDER BY g.value_num DESC
        """, tco=c["tco_id"])
        c["breaches"] = breaches
        c["reviewer_said"] = _rows(db, """
            SELECT h.to_status, h.reason_code, h.comment, h.actor_name
            FROM datasheet_status_history h
            JOIN `datasheet` d ON d.id = h.datasheet_id
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            WHERE r.tco_id = :tco AND h.to_status IN ('Approved', 'Rejected')
            ORDER BY h.id
        """, tco=c["tco_id"])
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# 3. metric_delta - "which frequencies improved most?"
# ---------------------------------------------------------------------------

_PIVOT = """
    SELECT m.grid_key, m.row_no,
           MAX(CASE WHEN m.col_key = 'qp_freq'  THEN m.value_num END) AS freq,
           MAX(CASE WHEN m.col_key = 'qp'       THEN m.value_num END) AS qp,
           MAX(CASE WHEN m.col_key = 'qp_limit' THEN m.value_num END) AS lim,
           MAX(CASE WHEN m.col_key = 'qp_margin' THEN m.value_num END) AS margin
    FROM datasheet_measurement m
    JOIN `datasheet` d ON d.id = m.datasheet_id
    JOIN planner_entries p ON p.id = d.planner_entry_id
    JOIN iec_emc_requests r ON r.id = p.test_request_id
    WHERE r.tco_id = :%s
      AND m.grid_key IN ('line_measurements', 'neutral_measurements')
      AND m.revision_no = (SELECT MAX(m2.revision_no) FROM datasheet_measurement m2
                            WHERE m2.datasheet_id = m.datasheet_id)
    GROUP BY m.grid_key, m.row_no
"""


def metric_delta(db, tco_before, tco_after, limit=40):
    """Per-frequency change between two campaigns of the same product.

    Matched on the FREQUENCY, not on row number: rows move when an engineer adds
    a reading, and a row-matched comparison would silently compare 0.72 MHz
    against 1.15 MHz and report a large improvement that never happened.

    Positive improvement_db means the emission came DOWN, which is the direction
    that helps - stated explicitly because the sign convention of a margin is
    the easiest thing in this whole feature to get backwards.
    """
    sql = ("SELECT b.grid_key, b.freq AS frequency_mhz, b.qp AS before_qp, "
           "a.qp AS after_qp, ROUND(b.qp - a.qp, 2) AS improvement_db, "
           "b.margin AS before_margin, a.margin AS after_margin, "
           "CASE WHEN b.margin > 0 AND a.margin <= 0 THEN 1 ELSE 0 END AS newly_compliant "
           "FROM (" + (_PIVOT % "tb") + ") b "
           "JOIN (" + (_PIVOT % "ta") + ") a "
           "  ON a.grid_key = b.grid_key AND a.freq = b.freq "
           "WHERE b.freq IS NOT NULL "
           "ORDER BY improvement_db DESC LIMIT :lim")
    return _rows(db, sql, tb=tco_before, ta=tco_after, lim=int(limit))


# ---------------------------------------------------------------------------
# 4. modifications_before_pass - "which changes were introduced before it passed?"
# ---------------------------------------------------------------------------

def modifications_before_pass(db, product):
    """What was on the unit when it passed that was not there when it last failed.

    Set difference on the description, because the modification record is
    cumulative - a unit that passes still carries the ferrite fitted two
    campaigns ago, and listing every fitted part as "introduced before the pass"
    would credit the wrong change.
    """
    camps = timeline(db, product=product)
    passed = next((c for c in camps if (c.get("result") or "").upper() == "PASS"), None)
    if not passed:
        return {"passed": None, "introduced": [], "already_present": []}
    before = [c for c in camps
              if c["test_date"] and passed["test_date"]
              and c["test_date"] < passed["test_date"]
              and (c.get("result") or "").upper() == "FAIL"]
    last_fail = before[-1] if before else None

    def mods(tco):
        return _rows(db, """
            SELECT mo.mod_state, mo.description
            FROM datasheet_modification mo
            JOIN `datasheet` d ON d.id = mo.datasheet_id
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            WHERE r.tco_id = :tco ORDER BY mo.row_no
        """, tco=tco)

    at_pass = mods(passed["tco_id"])
    at_fail = {m["description"] for m in mods(last_fail["tco_id"])} if last_fail else set()
    return {
        "passed": passed,
        "last_failure": last_fail,
        "introduced": [m for m in at_pass if m["description"] not in at_fail],
        "already_present": [m for m in at_pass if m["description"] in at_fail],
    }


# ---------------------------------------------------------------------------
# 5. cohort - "have other products seen a similar failure?"
# ---------------------------------------------------------------------------

def cohort(db, reason_code, exclude_product=None, limit=40):
    """Every other product that failed for the same classified reason.

    Grouped by product rather than listed by campaign: three failing campaigns
    of one unit is one product with a problem, and returning it as three
    "other products" would turn a single stubborn machine into a fleet-wide
    pattern.
    """
    params = {"rc": reason_code, "lim": int(limit)}
    excl = ""
    if exclude_product:
        excl = " AND r.product_name NOT LIKE :ex "
        params["ex"] = "%" + exclude_product + "%"
    sql = """
        SELECT r.product_name, r.model_number, d.test_code,
               COUNT(*) AS failing_campaigns,
               MIN(d.test_date) AS first_seen, MAX(d.test_date) AS last_seen,
               GROUP_CONCAT(r.tco_id ORDER BY d.test_date SEPARATOR ', ') AS campaigns,
               MAX(r.is_synthetic) AS is_synthetic
    """ + _CAMPAIGN_JOIN + """
        WHERE d.failure_reason_code = :rc """ + excl + """
        GROUP BY r.product_name, r.model_number, d.test_code
        ORDER BY failing_campaigns DESC, first_seen LIMIT :lim
    """
    return _rows(db, sql, **params)


def resolved_how(db, reason_code, limit=40):
    """For a failure mode, what each product had fitted by the time it passed.

    The cross-product question people actually want answered is not "who else
    broke like this" but "what worked" - and that is only useful because the
    modification record is per campaign.
    """
    out = []
    for grp in cohort(db, reason_code, limit=limit):
        fix = modifications_before_pass(db, grp["product_name"])
        out.append({"product_name": grp["product_name"],
                    "failing_campaigns": grp["failing_campaigns"],
                    "passed": bool(fix.get("passed")),
                    "introduced": [m["description"] for m in fix.get("introduced", [])]})
    return out


# ---------------------------------------------------------------------------
# 6. config_diff - "what changed / what was common?"
# ---------------------------------------------------------------------------

_NOISE = ("test_date", "result", "tco_id", "job_number", "meas_index[]")


def _form_of(db, tco):
    row = db.session.execute(text("""
        SELECT v.form_json FROM datasheet_revision v
        JOIN `datasheet` d ON d.id = v.datasheet_id
        JOIN planner_entries p ON p.id = d.planner_entry_id
        JOIN iec_emc_requests r ON r.id = p.test_request_id
        WHERE r.tco_id = :tco ORDER BY v.revision_no DESC LIMIT 1
    """), {"tco": tco}).first()
    if not row or not row[0]:
        return {}
    try:
        return json.loads(row[0]) or {}
    except (TypeError, ValueError):
        return {}


def config_diff(db, tco_before, tco_after):
    """Field-level difference between two campaigns' submitted forms.

    Reads the frozen revision, not the live record: the live one has moved on,
    and "what changed between the failing test and the passing one" has to
    compare what was actually submitted each time.
    """
    a, b = _form_of(db, tco_before), _form_of(db, tco_after)
    changed, added, removed = [], [], []
    for k in sorted(set(a) | set(b)):
        if k in _NOISE or k.startswith(("line_", "neutral_", "img_")):
            continue          # measurements have their own primitive
        va, vb = a.get(k), b.get(k)
        if va == vb:
            continue
        if k not in a:
            added.append({"field": k, "value": vb})
        elif k not in b:
            removed.append({"field": k, "value": va})
        else:
            changed.append({"field": k, "before": va, "after": vb})
    return {"before": tco_before, "after": tco_after,
            "changed": changed, "added": added, "removed": removed}


def common_config(db, tcos):
    """Fields that hold the SAME value across every campaign given.

    Answers "what did the two successful tests have in common" - and is worth
    treating carefully, because on a short list almost everything is common.
    The caller should say how many campaigns were compared so a reader can
    judge whether a shared value means anything.
    """
    forms = [_form_of(db, t) for t in tcos]
    forms = [f for f in forms if f]
    if len(forms) < 2:
        return {"campaigns": tcos, "common": [], "note": "needs at least two campaigns"}
    keys = set(forms[0])
    for f in forms[1:]:
        keys &= set(f)
    common = []
    for k in sorted(keys):
        if k in _NOISE or k.startswith(("line_", "neutral_", "img_")):
            continue
        v = forms[0][k]
        if all(f[k] == v for f in forms[1:]) and v not in (None, "", [], {}):
            common.append({"field": k, "value": v})
    return {"campaigns": tcos, "compared": len(forms), "common": common}
