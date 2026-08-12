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

log = logging.getLogger(__name__)

# Every primitive returns rows carrying the campaign's TCO and the product name,
# never a bare number. A metric with nothing naming the thing it measures is how
# an answer ends up attached to the wrong product.
_CAMPAIGN_JOIN = """
    FROM `datasheet` d
    JOIN planner_entries p ON p.id = d.planner_entry_id
    JOIN iec_emc_requests r ON r.id = p.test_request_id
"""


def _open(db_params):
    """A read-only, time-capped connection - the same one semantics uses.

    Not the ORM session: these run inside a request that may already hold a
    transaction, and a SET SESSION TRANSACTION READ ONLY connection makes it
    structurally impossible for an analysis primitive to write. They are all
    SELECTs today; this keeps that true without relying on review.
    """
    from .semantics import _connect_ro
    return _connect_ro(db_params)


def _rows(conn, sql, **params):
    import pymysql.cursors
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(sql, params or None)
        return list(cur.fetchall())


# ---------------------------------------------------------------------------
# 1. timeline - "give me the testing history of Product ABC"
# ---------------------------------------------------------------------------

def timeline(conn, product=None, tco=None, limit=60):
    """Every campaign for a product, oldest first, with both axes attached.

    One row per campaign, not per revision: a re-issued record is the same test
    and listing it twice would read as an extra attempt the unit never made.
    review_rounds carries that instead.
    """
    where, params = [], {"lim": int(limit)}
    if product:
        where.append("r.product_name LIKE %(prod)s")
        params["prod"] = "%" + product + "%"
    if tco:
        where.append("r.tco_id = %(tco)s")
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
        ORDER BY d.test_date, r.tco_id LIMIT %(lim)s
    """
    rows = _rows(conn, sql, **params)
    _attach_worst_breach(conn, rows)
    return rows


def _attach_worst_breach(conn, rows):
    """The single worst reading on each failing campaign, inline.

    Strictly it belongs to failure_detail. It is here because asked WHY a
    product failed, the model called timeline, saw only codes and dates, wrote
    "the timeline alone gives no root cause", and stopped - even with the exact
    follow-up call printed underneath. Rather than keep arguing with it, put the
    headline number where it already is. One row per campaign, so this stays a
    summary and failure_detail is still the place for the full breach list.
    """
    for c in rows:
        if str(c.get("result") or "").upper() != "FAIL":
            continue
        worst = _rows(conn, """
            SELECT f.value AS frequency_mhz, q.value AS measured,
                   l.value AS limit_value, g.value AS margin_db
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
            WHERE r.tco_id = %(tco)s AND g.col_key = 'qp_margin' AND g.value_num > 0
              AND g.revision_no = (SELECT MAX(m2.revision_no)
                                     FROM datasheet_measurement m2
                                    WHERE m2.datasheet_id = g.datasheet_id)
            ORDER BY g.value_num DESC LIMIT 1
        """, tco=c["tco_id"])
        if worst:
            w = worst[0]
            # The sentence AND its parts. The ledger grounds whole cells, not
            # words inside them, so a reading delivered only as prose put
            # "0.720", "60.8" and "4.8" into a single un-citable string - and
            # the verifier, asked to check an answer quoting 0.72, could not
            # find it and replaced a correct explanation with a raw evidence
            # dump. The sentence is what the model should write; these four are
            # what make it checkable.
            c["worst_frequency_mhz"] = w["frequency_mhz"]
            c["worst_measured"] = w["measured"]
            c["worst_limit"] = w["limit_value"]
            c["worst_margin_db"] = w["margin_db"]
            c["worst_reading"] = ("%s MHz measured %s against a limit of %s, over by %s dB"
                                  % (w["frequency_mhz"], w["measured"],
                                     w["limit_value"], w["margin_db"]))


# ---------------------------------------------------------------------------
# 2. failure_detail - "why did it fail its first three tests?"
# ---------------------------------------------------------------------------

def failure_detail(conn, product=None, tco=None, limit=40):
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
    camps = [c for c in timeline(conn, product=product, tco=tco, limit=limit)
             if (c.get("result") or "").upper() == "FAIL"]
    out = []
    for c in camps:
        breaches = _rows(conn, """
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
            WHERE r.tco_id = %(tco)s AND g.col_key = 'qp_margin' AND g.value_num > 0
              AND g.revision_no = (SELECT MAX(m2.revision_no)
                                     FROM datasheet_measurement m2
                                    WHERE m2.datasheet_id = g.datasheet_id)
            ORDER BY g.value_num DESC
        """, tco=c["tco_id"])
        c["breaches"] = breaches
        c["reviewer_said"] = _rows(conn, """
            SELECT h.to_status, h.reason_code, h.comment, h.actor_name
            FROM datasheet_status_history h
            JOIN `datasheet` d ON d.id = h.datasheet_id
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            WHERE r.tco_id = %(tco)s AND h.to_status IN ('Approved', 'Rejected')
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
    WHERE r.tco_id = %%(%s)s
      AND m.grid_key IN ('line_measurements', 'neutral_measurements')
      AND m.revision_no = (SELECT MAX(m2.revision_no) FROM datasheet_measurement m2
                            WHERE m2.datasheet_id = m.datasheet_id)
    GROUP BY m.grid_key, m.row_no
"""


def metric_delta(conn, tco_before, tco_after, limit=40):
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
           "ORDER BY improvement_db DESC LIMIT %(lim)s")
    return _rows(conn, sql, tb=tco_before, ta=tco_after, lim=int(limit))


# ---------------------------------------------------------------------------
# 4. modifications_before_pass - "which changes were introduced before it passed?"
# ---------------------------------------------------------------------------

def modifications_before_pass(conn, product):
    """What was on the unit when it passed that was not there when it last failed.

    Set difference on the description, because the modification record is
    cumulative - a unit that passes still carries the ferrite fitted two
    campaigns ago, and listing every fitted part as "introduced before the pass"
    would credit the wrong change.
    """
    camps = timeline(conn, product=product)
    passed = next((c for c in camps if (c.get("result") or "").upper() == "PASS"), None)
    if not passed:
        return {"passed": None, "introduced": [], "already_present": []}
    before = [c for c in camps
              if c["test_date"] and passed["test_date"]
              and c["test_date"] < passed["test_date"]
              and (c.get("result") or "").upper() == "FAIL"]
    last_fail = before[-1] if before else None

    def mods(tco):
        return _rows(conn, """
            SELECT mo.mod_state, mo.description
            FROM datasheet_modification mo
            JOIN `datasheet` d ON d.id = mo.datasheet_id
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            WHERE r.tco_id = %(tco)s ORDER BY mo.row_no
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

_PLACEHOLDERS = ("all", "any", "every", "all products", "any product",
                 "everything", "*", "n/a", "none", "all failures", "unknown")


def resolve_reason_codes(conn, reason_code=None, product=None):
    """The failure codes to actually use, given whatever the model supplied.

    It kept writing reason_code="ALL" - the natural thing to type for "have
    others failed like this", and a code that matches nothing, so the query
    returned no rows and the answer became a confident "No, nobody else." Worse
    than a missing argument, because an empty result reads like a finding.

    So: a placeholder means "no filter", a product means "whatever THAT product
    failed for", and nothing at all means every code present. The model should
    not have to know the vocabulary to ask the question.
    """
    code = str(reason_code or "").strip()
    if code and code.lower() not in _PLACEHOLDERS:
        # Exact code first, then the taxonomy's human label. Asked about "all
        # the products that failed conducted emission" the model passed the
        # phrase rather than CE_LIMIT_EXCEEDED - reasonably, since that is what
        # the user said - and an exact-match filter would have returned nothing.
        # emc_reason_code.label exists precisely so the vocabulary is
        # discoverable; matching against it means nobody has to know the codes.
        hit = _rows(conn, """
            SELECT code FROM emc_reason_code
             WHERE UPPER(code) = UPPER(%(c)s)
                OR LOWER(label) LIKE CONCAT('%%', LOWER(%(c)s), '%%')
                OR LOWER(%(c)s) LIKE CONCAT('%%', LOWER(code), '%%')
        """, c=code)
        if hit:
            return [h["code"] for h in hit]
        return [code]          # unknown, but the caller meant something - let
                               # it return nothing rather than silently widen
    if product:
        rows = _rows(conn, """
            SELECT DISTINCT d.failure_reason_code AS c
            FROM `datasheet` d
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            WHERE r.product_name LIKE %(prod)s AND d.failure_reason_code IS NOT NULL
        """, prod="%" + product + "%")
        if rows:
            return [r["c"] for r in rows]
    rows = _rows(conn, "SELECT DISTINCT failure_reason_code AS c FROM `datasheet` "
                       "WHERE failure_reason_code IS NOT NULL")
    return [r["c"] for r in rows]


def cohort(conn, reason_code=None, exclude_product=None, limit=40):
    """Every other product that failed for the same classified reason.

    Grouped by product rather than listed by campaign: three failing campaigns
    of one unit is one product with a problem, and returning it as three
    "other products" would turn a single stubborn machine into a fleet-wide
    pattern.
    """
    codes = resolve_reason_codes(conn, reason_code, exclude_product)
    if not codes:
        return []
    params = {"lim": int(limit)}
    params.update({"rc%d" % i: c for i, c in enumerate(codes)})
    in_list = ", ".join("%%(rc%d)s" % i for i in range(len(codes)))
    excl = ""
    if exclude_product:
        excl = " AND r.product_name NOT LIKE %(ex)s "
        params["ex"] = "%" + exclude_product + "%"
    sql = ("""
        SELECT r.product_name, r.model_number, d.test_code,
               d.failure_reason_code,
               COUNT(*) AS failing_campaigns,
               MIN(d.test_date) AS first_seen, MAX(d.test_date) AS last_seen,
               GROUP_CONCAT(r.tco_id ORDER BY d.test_date SEPARATOR ', ') AS campaigns,
               MAX(r.is_synthetic) AS is_synthetic
    """ + _CAMPAIGN_JOIN
        + " WHERE d.failure_reason_code IN (" + in_list + ") " + excl + """
        GROUP BY r.product_name, r.model_number, d.test_code, d.failure_reason_code
        ORDER BY failing_campaigns DESC, first_seen LIMIT %(lim)s
    """)
    return _rows(conn, sql, **params)


def resolved_how(conn, reason_code=None, limit=40):
    """For a failure mode, what each product had fitted by the time it passed.

    The cross-product question people actually want answered is not "who else
    broke like this" but "what worked" - and that is only useful because the
    modification record is per campaign.
    """
    out = []
    for grp in cohort(conn, reason_code, limit=limit):
        fix = modifications_before_pass(conn, grp["product_name"])
        out.append({"product_name": grp["product_name"],
                    "failed_for": grp.get("failure_reason_code"),
                    "failing_campaigns": grp["failing_campaigns"],
                    "passed": bool(fix.get("passed")),
                    "introduced": [m["description"] for m in fix.get("introduced", [])]})
    return out


# ---------------------------------------------------------------------------
# 6. config_diff - "what changed / what was common?"
# ---------------------------------------------------------------------------

_NOISE = ("test_date", "result", "tco_id", "job_number", "meas_index[]")


def _form_of(conn, tco):
    rows = _rows(conn, """
        SELECT v.form_json FROM datasheet_revision v
        JOIN `datasheet` d ON d.id = v.datasheet_id
        JOIN planner_entries p ON p.id = d.planner_entry_id
        JOIN iec_emc_requests r ON r.id = p.test_request_id
        WHERE r.tco_id = %(tco)s ORDER BY v.revision_no DESC LIMIT 1
    """, tco=tco)
    if not rows or not rows[0].get("form_json"):
        return {}
    try:
        return json.loads(rows[0]["form_json"]) or {}
    except (TypeError, ValueError):
        return {}


def config_diff(conn, tco_before, tco_after):
    """Field-level difference between two campaigns' submitted forms.

    Reads the frozen revision, not the live record: the live one has moved on,
    and "what changed between the failing test and the passing one" has to
    compare what was actually submitted each time.
    """
    a, b = _form_of(conn, tco_before), _form_of(conn, tco_after)
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


def common_config(conn, tcos):
    """Fields that hold the SAME value across every campaign given.

    Answers "what did the two successful tests have in common" - and is worth
    treating carefully, because on a short list almost everything is common.
    The caller should say how many campaigns were compared so a reader can
    judge whether a shared value means anything.
    """
    forms = [_form_of(conn, t) for t in tcos]
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


# ---------------------------------------------------------------------------
# The tool boundary
# ---------------------------------------------------------------------------
# One entry point the model calls by name. Everything it returns also goes into
# the evidence ledger as though it were a query result, because it is one: the
# grounding verifier only lets an answer state a number it can find in the
# ledger, and a primitive whose figures never got recorded would have every one
# of them stripped as ungrounded.

PRIMITIVES = {
    "timeline": timeline,
    "failure_detail": failure_detail,
    "metric_delta": metric_delta,
    "modifications_before_pass": modifications_before_pass,
    "cohort": cohort,
    "resolved_how": resolved_how,
    "config_diff": config_diff,
    "common_config": common_config,
}

# Printed with every result. The guardrail lives at the point of use, not only
# in the system prompt: by the time the model is looking at a 5.3 dB
# improvement next to a fitted choke, the temptation to write "the choke fixed
# it" is immediate, and a rule 4000 tokens earlier is not.
_CAUSATION_NOTE = (
    "EVIDENCE, NOT CAUSE. These rows say what was measured, what was fitted and "
    "what the reviewer wrote. They do not say why. State the sequence and let it "
    "speak - 'X was fitted between the two tests, and the margin at 0.72 MHz "
    "improved by 5.3 dB' - rather than asserting that X caused it.")


def _summarise(data):
    """Counts the answer is likely to quote, computed here rather than by eye.

    "It failed its first three tests" is a true statement about four rows, and
    the number 3 appears in none of them - so the verifier, which only passes a
    figure it can find in the evidence, flagged a correct answer as unsupported
    and replaced it with rows from an unrelated query. Counting is the cheapest
    thing to get right and the most expensive to get wrong, so the primitive
    does it and puts the totals in the evidence where they can be checked.
    """
    if not isinstance(data, list) or not data:
        return {}
    rows = [r for r in data if isinstance(r, dict)]
    if not rows or "result" not in rows[0]:
        return {}
    res = [str(r.get("result") or "").upper() for r in rows]
    out = {"campaigns_total": len(rows),
           "campaigns_failed": res.count("FAIL"),
           "campaigns_passed": res.count("PASS")}
    codes = [r.get("failure_reason_code") for r in rows if r.get("failure_reason_code")]
    if codes:
        out["failure_reason_codes"] = ", ".join(sorted(set(codes)))
    bounced = [r.get("record_rejected_for") for r in rows if r.get("record_rejected_for")]
    if bounced:
        out["records_rejected_in_review"] = len(bounced)
        out["record_rejection_codes"] = ", ".join(sorted(set(bounced)))
    return out


def _flatten(data):
    """Every row of a primitive's result as flat (column, value) pairs."""
    if isinstance(data, dict):
        data = [data]
    out = []
    for row in data or []:
        if not isinstance(row, dict):
            continue
        for k, v in row.items():
            if isinstance(v, (list, tuple)):
                for sub in v:
                    if isinstance(sub, dict):
                        out.extend((sk, sv) for sk, sv in sub.items())
                    else:
                        out.append((k, sub))
            elif isinstance(v, dict):
                out.extend((sk, sv) for sk, sv in v.items())
            else:
                out.append((k, v))
    return out


def _render(name, data, kwargs):
    """A compact text block the model can read and quote from."""
    lines = ["## %s(%s)" % (name, ", ".join("%s=%r" % kv for kv in sorted(kwargs.items())))]

    def emit(rows, indent="  "):
        for row in rows:
            if not isinstance(row, dict):
                lines.append("%s%s" % (indent, row))
                continue
            simple, nested = [], []
            for k, v in row.items():
                if isinstance(v, (list, tuple)):
                    nested.append((k, v))
                elif v not in (None, ""):
                    simple.append("%s=%s" % (k, v))
            if simple:
                lines.append(indent + "  ".join(simple))
            for k, v in nested:
                lines.append("%s  %s:" % (indent, k))
                emit(v, indent + "    ")

    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, (list, tuple)):
                lines.append("  %s:" % key)
                emit(val, "    ")
            elif isinstance(val, dict):
                lines.append("  %s:" % key)
                emit([val], "    ")
            elif val not in (None, ""):
                lines.append("  %s=%s" % (key, val))
    else:
        if not data:
            lines.append("  (no rows - say so plainly rather than reaching for "
                         "a different table)")
        emit(data)
    lines.append("")
    lines.append(_CAUSATION_NOTE)
    return "\n".join(lines)


def run(db_params, name, ledger=None, **kwargs):
    """Run one analysis primitive by name and return it rendered for the model."""
    fn = PRIMITIVES.get(name)
    if fn is None:
        return ("No analysis called %r. Available: %s"
                % (name, ", ".join(sorted(PRIMITIVES))))
    kwargs = {k: v for k, v in kwargs.items() if v not in (None, "")}
    # Asked what every product changed to pass, the model filled the product
    # slot with the word "ALL" - a perfectly reasonable thing to write and a
    # product name that matches nothing, so the analysis came back empty and the
    # answer became "no recorded modifications were found", which is the
    # opposite of true. A placeholder is a missing value, not a filter.
    prod = str(kwargs.get("product") or "").strip().lower()
    if prod in ("all", "any", "every", "all products", "any product",
                "everything", "*", "n/a", "none"):
        kwargs.pop("product", None)
    # "have OTHER products failed like this" - the product asked about is not an
    # answer to its own question. Done here rather than only in the tool wrapper
    # so every caller gets it, including a direct call from a test or a future
    # second wrapper.
    if name in ("cohort", "resolved_how") and "product" in kwargs:
        kwargs.setdefault("exclude_product", kwargs.pop("product"))
        kwargs.pop("product", None)
    if name in ("modifications_before_pass", "common_config") and not kwargs.get("product"):
        return ("%s needs one product. For a question across every product that "
                "failed the same way, call resolved_how with that failure's "
                "reason_code instead - it reports what each of them had fitted "
                "by the time it passed." % name)
    conn = _open(db_params)
    try:
        data = fn(conn, **kwargs)
    except TypeError as exc:                 # wrong/missing argument for this one
        return "Cannot run %s: %s" % (name, exc)
    except Exception as exc:                 # noqa: BLE001 - surfaced, not raised
        log.warning("insight %s failed: %s", name, exc)
        if ledger is not None:
            ledger.record("insights", "%s(%r)" % (name, kwargs), error=str(exc))
        return "The %s analysis failed: %s" % (name, exc)
    finally:
        conn.close()

    summary = _summarise(data)
    pairs = _flatten(data) + list(summary.items())
    if ledger is not None:
        ledger.record("insights", "%s(%s)" % (name, kwargs),
                      columns=["field", "value"],
                      rows=[[k, v] for k, v in pairs])
        ledger.note("insight", "%s returned %d value(s)" % (name, len(pairs)))
    text = _render(name, data, kwargs)
    if summary:
        text += "\n\n" + _counts_sentence(summary)
    nxt = _next_steps(name, data, kwargs)
    if nxt:
        text += "\n\n" + nxt
    return text


def _counts_sentence(s):
    """The counts as a sentence, not as key = value.

    Handed 'campaigns_total = 4' the model copied it into the answer verbatim,
    and the verifier - which checks the tokens an answer asserts - flagged
    campaigns_total, campaigns_failed and campaigns_passed as claims it could
    not find in any cell. The repair then bolted on "the evidence does not
    provide information on total campaigns", which was flatly untrue while the
    numbers sat in the same tool result. Machinery in, machinery out: give it a
    sentence and it quotes a sentence.
    """
    bits = ["%d campaign(s) in total: %d failed, %d passed"
            % (s.get("campaigns_total", 0), s.get("campaigns_failed", 0),
               s.get("campaigns_passed", 0))]
    if s.get("failure_reason_codes"):
        bits.append("the unit failed for: %s" % s["failure_reason_codes"])
    if s.get("records_rejected_in_review"):
        bits.append("separately, %d record(s) were sent back in peer review (%s)"
                    % (s["records_rejected_in_review"],
                       s.get("record_rejection_codes", "")))
    return "IN WORDS (quote this, do not re-count): " + ". ".join(bits) + "."


def _next_steps(name, data, kwargs):
    """The call to make next, with its arguments already filled in.

    timeline answers "what happened" and nothing else, so a question about WHY
    needs a second call - and asked for one, the model read the campaign list,
    decided it had enough, and reported that no measurements existed. It was not
    refusing to look; it did not know which TCOs to pass, because working that
    out means finding the last FAIL and the first PASS in a list it had only
    just been given. So compute them here and print the exact call. A suggestion
    naming real arguments gets made; one saying "consider metric_delta" does not.
    """
    if name != "timeline" or not isinstance(data, list) or not data:
        return ""
    fails = [c for c in data if str(c.get("result") or "").upper() == "FAIL"]
    passes = [c for c in data if str(c.get("result") or "").upper() == "PASS"]
    product = kwargs.get("product") or ""
    lines = []
    if fails:
        lines.append('  analyse_history(analysis="failure_detail", %s)  '
                     '-> the readings that actually breached'
                     % ('product="%s"' % product if product
                        else 'tco="%s"' % fails[0]["tco_id"]))
    if fails and passes:
        last_fail, first_pass = fails[-1]["tco_id"], passes[0]["tco_id"]
        lines.append('  analyse_history(analysis="metric_delta", tco_before="%s", '
                     'tco_after="%s")  -> per-frequency change'
                     % (last_fail, first_pass))
        lines.append('  analyse_history(analysis="config_diff", tco_before="%s", '
                     'tco_after="%s")  -> fields that differ'
                     % (last_fail, first_pass))
    if passes and product:
        lines.append('  analyse_history(analysis="modifications_before_pass", '
                     'product="%s")  -> what was fitted before it passed' % product)
    codes = sorted({c.get("failure_reason_code") for c in data
                    if c.get("failure_reason_code")})
    for code in codes[:2]:
        lines.append('  analyse_history(analysis="cohort", reason_code="%s"%s)  '
                     '-> other products that failed the same way'
                     % (code, ', product="%s"' % product if product else ""))
    if not lines:
        return ""
    # Told to "make one of these calls next", the model listed them in its reply
    # as things it COULD do and asked whether to proceed - a menu instead of an
    # answer, to someone who had already asked the question. Suggestions get
    # offered; instructions get followed.
    return ("THIS IS ONLY THE TIMELINE. It says WHAT happened, not why or by how "
            "much.\n\nIf the question asked for anything beyond the list of "
            "campaigns, CALL ONE OF THESE NOW, before you reply. The arguments "
            "are already worked out - copy them:\n" + "\n".join(lines) +
            "\n\nDo NOT put these in your answer as options and ask whether to "
            "run them. The user asked a question; they want the result, not a "
            "list of queries you could have run. Call, then answer.")
