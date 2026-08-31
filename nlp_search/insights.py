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
import re

log = logging.getLogger(__name__)

# Every primitive returns rows carrying the campaign's TCO and the product name,
# never a bare number. A metric with nothing naming the thing it measures is how
# an answer ends up attached to the wrong product.
# XXX-XXX-999 with at least one dash and a trailing number.
_TCO_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){1,3}$")

_CAMPAIGN_JOIN = """
    FROM `datasheet` d
    JOIN planner_entries p ON p.id = d.planner_entry_id
    JOIN iec_emc_requests r ON r.id = p.test_request_id
"""

# The argument called `product` is whatever the USER called the thing under
# test, and that is not always its product_name.
#
# Asked what was changed before a unit passed, the model passed
# 'DEMO-50199311'. That is the model/SKU - it lives in
# iec_emc_requests.model_number and datasheet.eut_model_sku_number, and NOT in
# product_name, which holds 'DEMO Vantage Water Purifier'. Matching
# product_name alone therefore found no campaigns at all, and the empty result
# was reported as "No changes were recorded as introduced before the unit
# passed": an absence the filter had manufactured, stated as a finding. The
# change was a common-mode choke, and it was sitting in the modification record
# the whole time.
#
# So every column that NAMES the same thing is matched. This is deliberately
# generous - over-matching returns a superset the reader can see and argue with,
# while under-matching returns a confident nothing.
_PRODUCT_COLS_CAMPAIGN = ("r.product_name", "r.model_number",
                          "d.eut_model_sku_number", "d.eut_name")
# review_history selects from `datasheet` alone, with no request join, so only
# the sheet's own identifier columns are in scope there.
_PRODUCT_COLS_SHEET = ("d.product_name", "d.eut_model_sku_number", "d.eut_name")


def _product_where(cols):
    """An OR over every column that can carry the identifier the user typed."""
    return "(" + " OR ".join("%s LIKE %%(prod)s" % c for c in cols) + ")"


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
        where.append(_product_where(_PRODUCT_COLS_CAMPAIGN))
        params["prod"] = "%" + str(product).strip() + "%"
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
               -- ALL of them, and how many. This was LIMIT 1, which reported one
               -- code for a record sent back twice for different reasons - and
               -- that single value looked like a complete answer. Asked why a
               -- datasheet was sent back twice, the model read
               -- record_rejected_for=CAL_EXPIRED, stopped, and never saw
               -- INCOMPLETE_OBS or the fields the engineer changed. A field that
               -- silently shows the first of several is worse than one that shows
               -- none, because nothing about it invites a second look.
               (SELECT COUNT(*) FROM datasheet_status_history h
                 WHERE h.datasheet_id = d.id AND h.to_status = 'Rejected')
                 AS times_sent_back,
               (SELECT GROUP_CONCAT(h.reason_code ORDER BY h.revision_no, h.id
                                    SEPARATOR ', ')
                  FROM datasheet_status_history h
                 WHERE h.datasheet_id = d.id AND h.to_status = 'Rejected'
                   AND h.reason_code IS NOT NULL) AS record_rejected_for,
               r.is_synthetic
    """ + _CAMPAIGN_JOIN + " WHERE " + " AND ".join(where) + """
        -- test_date is NULL on a third of the datasheets and has only four
        -- distinct values across the rest, so ordering on it alone put
        -- undated campaigns first and tied same-day retests arbitrarily.
        -- Falling back to when the record was submitted, then created,
        -- keeps the sequence honest; tco_id is the final tiebreak.
        ORDER BY COALESCE(d.test_date, DATE(d.submitted_at), DATE(d.created_at)),
                 d.submitted_at, r.tco_id LIMIT %(lim)s
    """
    rows = _rows(conn, sql, **params)
    # Say what the letter MEANS, per row. Handed result='D' the model wrote "it
    # received a D in ESD and EFT" and then, in the next sentence, "it did meet
    # performance criteria in ESD and EFT" - a self-contradiction inside one
    # answer, because D is a grade whose direction you have to know. outcome()
    # already knows; the model should not have to.
    for r in rows:
        o = outcome(r)
        r["outcome"] = {"pass": "COMPLIANT - met its performance criterion",
                        "fail": "NOT COMPLIANT - did not meet its criterion",
                        "unknown": "no outcome recorded"}[o]
        # A campaign row cannot say what happened in each review round - it is one
        # row per campaign by design. So when there was more than one rejection it
        # says so and names the primitive that can, rather than leaving a list of
        # codes that looks like the whole story.
        # ONE rejection is enough to warrant the pointer. This said `> 1`, and
        # asked why DEMO-EMC-301 was rejected the model read a row with
        # times_sent_back=1, got no pointer, and answered from the campaign view -
        # concluding 301 was the only sheet ever sent back more than once. It was
        # DEMO-EMC-304, rejected twice, which it never looked at. A campaign row
        # cannot answer "why was it rejected" at any count, because the reason,
        # the reviewer's words and the fields that changed are all per round.
        if (r.get("times_sent_back") or 0) >= 1:
            r["review_note"] = (
                "sent back %d time(s), for %s. THIS ROW IS PER CAMPAIGN and cannot "
                "show which round found what, what the reviewer wrote, or what the "
                "engineer changed in response - and it is not a lab-wide count "
                "either, so do not conclude from it that nothing else was sent "
                "back. Call review_history for the rounds, or rejection_modes for "
                "the lab." % (r["times_sent_back"],
                              r.get("record_rejected_for") or "reasons not coded"))
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
        if outcome(c) != "fail":
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

    WHICH REVISION THE BREACH IS READ FROM, and why it is not the latest one.
    This used to pin to MAX(revision_no) to stop a re-issued record returning
    every reading twice - once from the withdrawn revision, once from the
    replacement - which is a real problem and the wrong fix for it. On a
    campaign that FAILED and was then remeasured, the latest revision is the
    CORRECTED one: DEMO-EMC-311's CE sheet breached 0.720 MHz by +8.8 dB on
    revision 1 and cleared it on revision 2, so pinning to the latest returned
    an EMPTY breach list for a campaign recorded as FAIL. Asked which reading
    was over the limit, the assistant answered "the breaching reading cannot be
    shown as a numeric value in this dataset" - a confident absence, with the
    number sitting in datasheet_measurement the whole time.

    So each CELL is read from the EARLIEST revision where it breached, which
    deduplicates a re-issued record just as well (one row per cell) without
    hiding the reading that caused the failure. `latest_measured` and
    `latest_margin` carry the same cell's current value alongside, because
    "what did it come down to" is the other half of the question and should not
    need a second call.
    """
    camps = [c for c in timeline(conn, product=product, tco=tco, limit=limit)
             if outcome(c) == "fail"]
    out = []
    for c in camps:
        breaches = _rows(conn, """
            SELECT b.revision_no AS breached_on_revision,
                   f.value AS frequency_mhz, q.value AS measured,
                   l.value AS limit_value, b.value AS margin_db, b.grid_key,
                   (SELECT q2.value FROM datasheet_measurement q2
                     WHERE q2.datasheet_id = b.datasheet_id
                       AND q2.grid_key = b.grid_key AND q2.row_no = b.row_no
                       AND q2.block_label <=> b.block_label
                       AND q2.col_key = 'qp'
                       AND q2.revision_no = (SELECT MAX(m3.revision_no)
                                               FROM datasheet_measurement m3
                                              WHERE m3.datasheet_id = b.datasheet_id))
                     AS latest_measured,
                   (SELECT g2.value FROM datasheet_measurement g2
                     WHERE g2.datasheet_id = b.datasheet_id
                       AND g2.grid_key = b.grid_key AND g2.row_no = b.row_no
                       AND g2.block_label <=> b.block_label
                       AND g2.col_key = 'qp_margin'
                       AND g2.revision_no = (SELECT MAX(m4.revision_no)
                                               FROM datasheet_measurement m4
                                              WHERE m4.datasheet_id = b.datasheet_id))
                     AS latest_margin
            FROM datasheet_measurement b
            JOIN `datasheet` d ON d.id = b.datasheet_id
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            JOIN datasheet_measurement f ON f.datasheet_id = b.datasheet_id
                 AND f.revision_no = b.revision_no AND f.grid_key = b.grid_key
                 AND f.row_no = b.row_no
                 AND f.block_label <=> b.block_label AND f.col_key = 'qp_freq'
            JOIN datasheet_measurement q ON q.datasheet_id = b.datasheet_id
                 AND q.revision_no = b.revision_no AND q.grid_key = b.grid_key
                 AND q.row_no = b.row_no
                 AND q.block_label <=> b.block_label AND q.col_key = 'qp'
            JOIN datasheet_measurement l ON l.datasheet_id = b.datasheet_id
                 AND l.revision_no = b.revision_no AND l.grid_key = b.grid_key
                 AND l.row_no = b.row_no
                 AND l.block_label <=> b.block_label AND l.col_key = 'qp_limit'
            WHERE r.tco_id = %(tco)s AND d.test_code = %(code)s
              AND b.col_key = 'qp_margin' AND b.value_num > 0
              AND b.revision_no = (SELECT MIN(m2.revision_no)
                                     FROM datasheet_measurement m2
                                    WHERE m2.datasheet_id = b.datasheet_id
                                      AND m2.grid_key = b.grid_key
                                      AND m2.row_no = b.row_no
                                      AND m2.block_label <=> b.block_label
                                      AND m2.col_key = 'qp_margin'
                                      AND m2.value_num > 0)
            ORDER BY b.value_num DESC
        """, tco=c["tco_id"], code=c["test_code"])
        c["breaches"] = breaches
        # SCOPED TO THIS DATASHEET, not to the job. Without the test_code filter
        # every failing test on a TCO received every review comment on that TCO:
        # DEMO-EMC-311's RS_RI row came back carrying the CE sheet's "Class B
        # limit applied; this EUT is Class A" comment while its own
        # times_sent_back was 0 - a row contradicting itself, and the reviewer's
        # words attached to a test they were never written about.
        c["reviewer_said"] = _rows(conn, """
            SELECT h.to_status, h.reason_code, h.comment, h.actor_name
            FROM datasheet_status_history h
            JOIN `datasheet` d ON d.id = h.datasheet_id
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            WHERE r.tco_id = %(tco)s AND d.test_code = %(code)s
              AND h.to_status IN ('Approved', 'Rejected')
            ORDER BY h.id
        """, tco=c["tco_id"], code=c["test_code"])
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# 3. metric_delta - "which frequencies improved most?"
# ---------------------------------------------------------------------------

# Two format slots: the TCO parameter name, then the revision parameter name.
# A NULL revision means "the latest", which is what a campaign-to-campaign
# comparison wants; naming one pins the pivot to a single frozen revision.
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
      AND m.revision_no = COALESCE(%%(%s)s,
            (SELECT MAX(m2.revision_no) FROM datasheet_measurement m2
              WHERE m2.datasheet_id = m.datasheet_id))
    GROUP BY m.grid_key, m.row_no
"""


def _pivot_revisions(conn, tco):
    """Which frozen revisions of this campaign actually carry qp readings.

    Read from the same rows the pivot reads, not from datasheet_revision: a
    revision can exist as a header without the measurement grid being filled,
    and offering to compare one that has no readings produces an empty result
    that looks like "no change".
    """
    sql = ("SELECT DISTINCT m.revision_no "
           "FROM datasheet_measurement m "
           "JOIN `datasheet` d ON d.id = m.datasheet_id "
           "JOIN planner_entries p ON p.id = d.planner_entry_id "
           "JOIN iec_emc_requests r ON r.id = p.test_request_id "
           "WHERE r.tco_id = %(tco)s "
           "  AND m.grid_key IN ('line_measurements', 'neutral_measurements') "
           "  AND m.col_key = 'qp' AND m.value_num IS NOT NULL "
           "ORDER BY m.revision_no")
    return [r["revision_no"] for r in _rows(conn, sql, tco=tco)]


def metric_delta(conn, tco_before, tco_after, rev_before=None,
                 rev_after=None, limit=40):
    """Per-frequency change between two campaigns of the same product.

    Matched on the FREQUENCY, not on row number: rows move when an engineer adds
    a reading, and a row-matched comparison would silently compare 0.72 MHz
    against 1.15 MHz and report a large improvement that never happened.

    Positive improvement_db means the emission came DOWN, which is the direction
    that helps - stated explicitly because the sign convention of a margin is
    the easiest thing in this whole feature to get backwards.
    """
    # SAME TCO MEANS COMPARE ITS REVISIONS, not a campaign against itself.
    #
    # timeline's own suggestion block emits metric_delta(tco_before='X',
    # tco_after='X') whenever a question goes past the campaign list, and until
    # now both sides pinned to MAX(revision_no) - so it compared revision 2 with
    # revision 2 and returned improvement_db=0.00 on every row. Read plainly,
    # that says nothing changed between the two attempts. It is the tool handing
    # the model a confident wrong answer and then recommending that it ask for
    # one.
    #
    # The data to answer it properly was already sitting there: DEMO-EMC-301 CE
    # holds the full quasi-peak pivot at revision 1 AND revision 2, and 304 ESD
    # at three. A rejected version against the fixed one is the comparison this
    # lab actually makes - "what changed in the readings after peer review sent
    # it back" - and it was the one shape metric_delta could not express.
    same_campaign = (str(tco_before or "").strip().upper()
                     == str(tco_after or "").strip().upper())
    if same_campaign:
        if rev_before is None and rev_after is None:
            revs = _pivot_revisions(conn, tco_before)
            if len(revs) < 2:
                return ("Only %d revision of %s carries quasi-peak readings, so "
                        "there is nothing to compare it against. A comparison "
                        "needs two frozen revisions, or two campaigns of the "
                        "same product."
                        % (len(revs), tco_before))
            rev_before, rev_after = revs[-2], revs[-1]
        if rev_before == rev_after:
            return ("revision %s cannot be compared with itself - that returns "
                    "zero change on every row, which reads as 'nothing changed' "
                    "and is not a measurement. Name two different revisions."
                    % rev_before)
    else:
        # "of the same product" was in this docstring and enforced nowhere.
        # Across two products the frequency join can still match - two units
        # measured at 0.72 MHz - and it would report an "improvement" between
        # unrelated things. That is worse than config_diff's version of the same
        # bug, because it comes out as a single confident number rather than a
        # suspicious wall of rows.
        refusal = _same_product(conn, tco_before, tco_after)
        if refusal:
            return refusal
    sql = ("SELECT b.grid_key, b.freq AS frequency_mhz, b.qp AS before_qp, "
           "a.qp AS after_qp, ROUND(b.qp - a.qp, 2) AS improvement_db, "
           "b.margin AS before_margin, a.margin AS after_margin, "
           "CASE WHEN b.margin > 0 AND a.margin <= 0 THEN 1 ELSE 0 END AS newly_compliant "
           "FROM (" + (_PIVOT % ("tb", "rb")) + ") b "
           "JOIN (" + (_PIVOT % ("ta", "ra")) + ") a "
           "  ON a.grid_key = b.grid_key AND a.freq = b.freq "
           "WHERE b.freq IS NOT NULL "
           "ORDER BY improvement_db DESC LIMIT %(lim)s")
    return _rows(conn, sql, tb=tco_before, ta=tco_after,
                 rb=rev_before, ra=rev_after, lim=int(limit))


# ---------------------------------------------------------------------------
# 4. modifications_before_pass - "which changes were introduced before it passed?"
# ---------------------------------------------------------------------------

# mod_state '0' is the unmodified baseline: it is the string 'Initial state' on
# every one of the 47 rows that carry it, one per datasheet. It is not a change,
# and offering it as one credits the wrong thing - "Initial state was introduced
# before the pass" reads as a finding and is noise.
_BASELINE_MOD_STATE = "0"

# Free text the engineer used to mean "nothing to declare". A deviation holding
# one of these is not a description of a change.
_NO_DEVIATION = frozenset(("", "na", "n/a", "none", "nil", "-", "--", "not applicable"))


def _revision_transition(conn, tco, test_code=None):
    """FAIL -> PASS inside ONE datasheet, or None. The resubmission case.

    Reads datasheet_revision, never datasheet.revision_no: that column is a
    NEXT-TO-EDIT pointer, one higher than any revision that exists on 47 of 47
    datasheets here, so filtering on it matches nothing and returns an empty
    result indistinguishable from "nothing changed".
    """
    revs = _rows(conn, """
        SELECT dr.revision_no, dr.result, dr.ambient_temperature,
               dr.relative_humidity, dr.deviation
        FROM datasheet_revision dr
        JOIN `datasheet` d ON d.id = dr.datasheet_id
        WHERE d.tco_id = %(tco)s
          AND (%(code)s IS NULL OR UPPER(d.test_code) = %(code)s)
        ORDER BY dr.revision_no
    """, tco=tco, code=(str(test_code).upper() if test_code else None))
    if len(revs) < 2:
        return None

    def bad(r):
        return str(r.get("result") or "").strip().upper() in ("FAIL", "C", "D")

    passed_at = next((r for r in reversed(revs) if not bad(r)), None)
    failed_at = next((r for r in revs if bad(r)), None)
    if not passed_at or not failed_at:
        return None
    if int(failed_at["revision_no"]) >= int(passed_at["revision_no"]):
        return None

    # Which fields the engineer actually edited, from the save history of the
    # revision that passed. changed_fields is the app's own record of the edit.
    changed = _rows(conn, """
        SELECT dh.revision_no, dh.changed_fields, dh.changed_count, dh.saved_by_name
        FROM datasheet_draft_history dh
        JOIN `datasheet` d ON d.id = dh.datasheet_id
        WHERE d.tco_id = %(tco)s
          AND (%(code)s IS NULL OR UPPER(d.test_code) = %(code)s)
          AND dh.revision_no = %(rev)s
        ORDER BY dh.saved_at
    """, tco=tco, code=(str(test_code).upper() if test_code else None),
        rev=passed_at["revision_no"])

    return {
        "failed_revision": failed_at["revision_no"],
        "passed_revision": passed_at["revision_no"],
        "conditions_before": {"ambient": failed_at.get("ambient_temperature"),
                              "humidity": failed_at.get("relative_humidity")},
        "conditions_after": {"ambient": passed_at.get("ambient_temperature"),
                            "humidity": passed_at.get("relative_humidity")},
        "deviation_before": failed_at.get("deviation"),
        "deviation_after": passed_at.get("deviation"),
        "saves_on_the_passing_revision": len(changed),
        "fields_changed": changed,
    }


def modifications_before_pass(conn, product, test_code=None):
    """What was on the unit when it passed that was not there when it last failed.

    Set difference on the description, because the modification record is
    cumulative - a unit that passes still carries the ferrite fitted two
    campaigns ago, and listing every fitted part as "introduced before the pass"
    would credit the wrong change.

    NOT test-specific: it works from the product's whole campaign timeline, so
    it answers for CE, ESD, RE or any other sheet. Pass test_code to pin it to
    one, which you should whenever the question came out of a conversation about
    a particular test.

    Four ways this answered confidently and wrongly before, all fixed here.
    Asked "what did they change before it passed" it replied "No changes were
    recorded", while the modification record held a common-mode choke:

      identifier missed      the caller passed 'DEMO-50199311', the model/SKU,
                             and product matching only looked at product_name.
                             Fixed in _PRODUCT_COLS_CAMPAIGN, above.
      empty read as absence  it returned {"passed": None, "introduced": []},
                             which is indistinguishable from "nothing was
                             changed". An identifier matching nothing now says
                             so in `note`, and must not be reported as a finding.
      the wrong test         it takes the FIRST pass in the timeline, so a
                             question about CE was answered from a CRF sheet.
                             test_code pins it.
      nothing to subtract    with no earlier failure, EVERY modification landed
                             in `introduced`. There is no "before the pass"
                             without a failure before it; `note` says that
                             instead of implying a fix.

    The deviation text is returned alongside, because the modification TABLE is
    not the only place a change is recorded: on the sheet that prompted all of
    this, datasheet_modification said 'Initial state' while `deviation` read
    "Class A limit line applied and the conducted emission remeasured after
    fitting a common-mode choke". Structured silence is not evidence.
    """
    code = str(test_code or "").strip().upper()
    camps = timeline(conn, product=product)
    if code:
        camps = [c for c in camps if (c.get("test_code") or "").upper() == code]

    def _shell(note):
        return {"product": product, "test_code": code or None, "passed": None,
                "last_failure": None, "introduced": [], "already_present": [],
                "note": note}

    if not camps:
        return _shell(
            "NO CAMPAIGN MATCHED product=%r%s. This is NOT evidence that nothing "
            "was changed - it means the identifier matched no rows. Do not report "
            "an absence: say the product could not be found, and check the name."
            % (product, " test_code=%r" % code if code else ""))

    passed = next((c for c in camps if outcome(c) == "pass"), None)
    if not passed:
        return _shell(
            "%d campaign(s) matched but NONE of them passed%s, so there is no pass "
            "to explain. Say the unit has not passed yet - not that nothing was "
            "changed." % (len(camps), " for " + code if code else ""))

    # POSITION in the timeline, not a date comparison. Comparing test_date
    # required both dates to exist and the earlier one to be STRICTLY earlier,
    # and neither holds here: test_date is NULL on 16 of 47 datasheets - both CE
    # sheets in the case that exposed this - and the 31 that have one share four
    # dates between them. A unit that fails and is retested the same day is the
    # normal case, and it could never find its own prior failure. timeline is
    # already ordered chronologically, so everything ahead of the pass in that
    # list is before it.
    at = next((i for i, c in enumerate(camps) if c is passed), 0)
    before = [c for c in camps[:at] if outcome(c) == "fail"]
    last_fail = before[-1] if before else None

    def mods(tco):
        # DISTINCT because the modification record is per DATASHEET and a job has
        # one datasheet per test: a job with three tests returned the same two
        # modifications three times each, and the reply listed six changes where
        # the engineer made two. The unit is modified once; every test on that
        # job then records the same state.
        return _rows(conn, """
            SELECT DISTINCT mo.mod_state, mo.description
            FROM datasheet_modification mo
            JOIN `datasheet` d ON d.id = mo.datasheet_id
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            WHERE r.tco_id = %(tco)s AND COALESCE(mo.mod_state, '') <> %(base)s
            ORDER BY mo.mod_state, mo.description
        """, tco=tco, base=_BASELINE_MOD_STATE)

    def deviation(tco):
        for r in _rows(conn, """
            SELECT DISTINCT d.deviation AS dev
            FROM `datasheet` d
            JOIN planner_entries p ON p.id = d.planner_entry_id
            JOIN iec_emc_requests r ON r.id = p.test_request_id
            WHERE r.tco_id = %(tco)s AND d.deviation IS NOT NULL
        """, tco=tco):
            text = str(r.get("dev") or "").strip()
            if text.lower() not in _NO_DEVIATION:
                return text
        return None

    at_pass = mods(passed["tco_id"])
    out = {"product": product, "test_code": code or None,
           "passed": passed, "last_failure": last_fail,
           "deviation_at_pass": deviation(passed["tco_id"]),
           "deviation_at_failure": deviation(last_fail["tco_id"]) if last_fail else None}

    if last_fail is None:
        # A unit can fail and pass WITHOUT a second campaign: the engineer fixes
        # the sheet and resubmits, so the transition is between REVISIONS of one
        # datasheet, not between two jobs. timeline works per campaign, so that
        # case arrives here with nothing before the pass and used to be reported
        # as "no failure precedes this pass" - which is false, and was measured
        # on a real record: IEC-EMC-010's CE sheet has revision 1 FAIL and
        # revision 2 PASS, with 22 draft-history saves between them.
        within = _revision_transition(conn, passed["tco_id"],
                                      passed.get("test_code"))
        if within:
            out.update({
                "introduced": [], "already_present": at_pass,
                "revision_transition": within,
                "note": ("no earlier CAMPAIGN failed, but this datasheet itself "
                         "went from FAIL to PASS between revisions %s and %s - "
                         "the fix was a resubmission, not a second job, which "
                         "makes THIS THE WRONG ANALYSIS for the question asked. "
                         "Call review_history(product=%r): it returns each "
                         "round's decision, the reviewer's own comment, and the "
                         "field-by-field before/after of what the engineer "
                         "changed. revision_transition below carries the same "
                         "diff in summary form - use it only if you do not call "
                         "review_history."
                         % (within.get("failed_revision"),
                            within.get("passed_revision"), product))})
            return out
        out.update({"introduced": [], "already_present": at_pass,
                    "note": ("no FAILED campaign precedes this pass%s, and this "
                             "datasheet's own revisions never recorded a failure "
                             "either, so nothing can be called "
                             "introduced-before-the-pass. What is listed in "
                             "already_present is simply what the unit carries; it is "
                             "not a fix for a failure."
                             % (" for " + code if code else ""))})
        return out

    at_fail = {m["description"] for m in mods(last_fail["tco_id"])}
    out["introduced"] = [m for m in at_pass if m["description"] not in at_fail]
    out["already_present"] = [m for m in at_pass if m["description"] in at_fail]
    if not out["introduced"]:
        out["note"] = ("the modification record shows nothing fitted between the "
                       "failure and the pass. Check deviation_at_failure before "
                       "concluding nothing changed - the engineer's own words are "
                       "kept there, and the modification table can be left at its "
                       "baseline while the deviation describes the change.")
    return out


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
            WHERE %s AND d.failure_reason_code IS NOT NULL
        """ % _product_where(_PRODUCT_COLS_CAMPAIGN), prod="%" + str(product).strip() + "%")
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
    rows = _rows(conn, sql, **params)
    if not rows:
        # HERE, EMPTY IS THE ANSWER. resolve_reason_codes already turned the
        # product into the codes it actually failed for, so nothing coming back
        # means nothing else has failed that way - a finding, not a gap. Left as
        # an empty list it collected the generic "confirm the value you filtered
        # on exists" note, which tells the model NOT to report the absence it was
        # asked about. Said plainly instead.
        return [{"answer": "NO OTHER PRODUCT has failed for %s. This is the "
                           "answer, not a missing result: the codes were "
                           "resolved and the search found nobody else."
                           % ", ".join(codes),
                 "codes_checked": ", ".join(codes),
                 "excluded_from_the_search": exclude_product or "(nothing)"}]
    return rows


def failure_modes(conn, limit=40):
    """Every classified failure mode in the lab: how many campaigns, how many products.

    The one primitive that takes no arguments, because the question it answers -
    "what do things fail for around here" - names nothing. Without it the model
    called failure_detail with an empty product, got an empty list, and reported
    that it could not identify a most common failure reason. Meanwhile a nearly
    identical question phrased "which mode affects the most products" was
    answered correctly from hand-written SQL, and a third one counted 4 products
    where there are 3 by treating a blank result as a failure. Same data, three
    phrasings, three different outcomes - which is exactly the variance a
    reviewed query removes.

    Ordered by products affected, not campaign count: three failing campaigns of
    one stubborn unit is a product problem, two products failing the same way is
    a pattern, and the second is what a manager needs to see first.

    Each row now says whether the mode is SHARED, because this is lab-wide and
    reads like an answer to a question it cannot answer. Asked whether any other
    product had failed the same way as the Lifecycle Probe Analyser, the model
    called this, got three modes back, and listed the Spectra Bench Photometer
    and the Orion Vacuum Pump Controller - both of which failed DIFFERENTLY, each
    being the only product with its own mode. Three rows looked like three
    answers. The right primitive was cohort, and now every row points at it.
    """
    rows = _rows(conn, """
        -- The NAMES come first and the count is gone. products_affected used to
        -- sit here beside products, and asked what had failed recently the model
        -- printed "products_affected: 4" and dropped the four names - every
        -- figure right and the only thing a reader wanted missing. A count that
        -- can be shown INSTEAD of the names is what allowed that, so it is not
        -- offered; anyone who wants it can count the list. Ordering still uses
        -- it, which needs no column.
        SELECT COALESCE(e.label, d.failure_reason_code) AS what_it_means,
               GROUP_CONCAT(DISTINCT r.product_name
                            ORDER BY r.product_name SEPARATOR '; ') AS products,
               GROUP_CONCAT(DISTINCT d.test_code
                            ORDER BY d.test_code SEPARATOR ', ') AS tests,
               d.failure_reason_code AS reason_code,
               COUNT(*) AS failing_campaigns,
               MIN(d.test_date) AS first_seen, MAX(d.test_date) AS last_seen
        FROM `datasheet` d
        JOIN planner_entries p ON p.id = d.planner_entry_id
        JOIN iec_emc_requests r ON r.id = p.test_request_id
        LEFT JOIN emc_reason_code e ON e.code = d.failure_reason_code
        WHERE d.failure_reason_code IS NOT NULL
        GROUP BY d.failure_reason_code, e.label
        ORDER BY COUNT(DISTINCT r.product_name) DESC, failing_campaigns DESC
        LIMIT %(lim)s
    """, lim=int(limit))
    # The caveat goes SECOND, right after the code, not last. Appended at the end
    # it was read last and ignored: the model still listed two products that had
    # failed differently as though they were peers. _render emits keys in dict
    # order, so position is the only lever on reading order there is.
    out = []
    for r in rows:
        n = r.get("products_affected") or 0
        if n > 1:
            note = "SHARED - %d different products have failed this way" % n
        else:
            note = ("NOT SHARED - %s is the ONLY product that has ever failed "
                    "this way. Do NOT offer this row as another product that "
                    "failed the same way as something else; it is the only one. "
                    "The question \"has anything else failed like X\" is answered "
                    "by cohort(reason_code=...), which excludes X and returns the "
                    "rest - and an empty answer from it means nobody else has."
                    % (r.get("products") or "one product"))
        ordered = {"reason_code": r.get("reason_code"), "is_it_shared": note}
        ordered.update({k: v for k, v in r.items() if k != "reason_code"})
        out.append(ordered)
    return out


def rejection_modes(conn, limit=40):
    """Why RECORDS get sent back in peer review, lab-wide. The other axis.

    Counted two ways on purpose. A datasheet bounced three times is three events
    and one datasheet, and the honest answer to "how many have been rejected"
    depends which was meant - a question that has already been got wrong once
    here, by me, when I wrote 9 as the expected answer to a question asking how
    many datasheets.
    """
    return _rows(conn, """
        -- products and tests were absent entirely. Asked whether anything had
        -- been sent back, the answer was five reason codes and two counts, and a
        -- reader could not tell WHICH datasheet on WHICH product had been
        -- returned - the one thing they were asking. The join to reach a product
        -- from a status-history row is the declared spine, three hops, and it was
        -- simply never made.
        SELECT COALESCE(e.label, 'no code recorded - see the reviewer comment')
                   AS what_it_means,
               GROUP_CONCAT(DISTINCT r.product_name
                            ORDER BY r.product_name SEPARATOR '; ') AS products,
               GROUP_CONCAT(DISTINCT d.test_code
                            ORDER BY d.test_code SEPARATOR ', ') AS tests,
               COALESCE(h.reason_code, '(unclassified)') AS reason_code,
               COUNT(*) AS rejection_events,
               MIN(SUBSTRING(h.comment, 1, 90)) AS example_comment
        FROM datasheet_status_history h
        JOIN `datasheet` d ON d.id = h.datasheet_id
        JOIN planner_entries p ON p.id = d.planner_entry_id
        JOIN iec_emc_requests r ON r.id = p.test_request_id
        LEFT JOIN emc_reason_code e ON e.code = h.reason_code
        WHERE h.to_status = 'Rejected'
        GROUP BY COALESCE(h.reason_code, '(unclassified)'), e.label
        ORDER BY rejection_events DESC
        LIMIT %(lim)s
    """, lim=int(limit))


def review_load(conn, limit=40):
    """Peer review by PERSON: what each reviewer decided, and how often.

    Added because nothing answered it and the model kept inventing an answer.
    Asked who was sending back the most work in peer review, it went to
    iec_emc_requests.rejected_at - a request being refused by an admin, a
    different thing entirely, and NULL on every row - and replied "there are zero
    rejections logged in peer review across all records". Six existed. Told the
    columns were empty, it tried again and counted datasheets currently SITTING
    in Peer Review status, which is a workload, not a decision.

    Both are here, separately, because the question is ambiguous in English and
    the two numbers are genuinely different: `sent_back` is a decision the
    reviewer made, `awaiting_them` is work on their desk right now.
    """
    return _rows(conn, """
        SELECT h.actor_name AS reviewer,
               SUM(h.to_status = 'Rejected') AS sent_back,
               SUM(h.to_status = 'Approved') AS approved,
               COUNT(*) AS decisions,
               COUNT(DISTINCT h.datasheet_id) AS datasheets_touched,
               GROUP_CONCAT(DISTINCT h.reason_code
                            ORDER BY h.reason_code SEPARATOR ', ') AS reasons_used,
               MIN(h.created_at) AS first_decision,
               MAX(h.created_at) AS last_decision,
               (SELECT COUNT(*) FROM `datasheet` d2
                 WHERE d2.status = 'Peer Review'
                   AND d2.peer_reviewer_name = h.actor_name) AS awaiting_them
        FROM datasheet_status_history h
        WHERE h.to_status IN ('Rejected', 'Approved')
          AND h.actor_name IS NOT NULL AND h.actor_name <> ''
        GROUP BY h.actor_name
        ORDER BY sent_back DESC, decisions DESC
        LIMIT %(lim)s
    """, lim=int(limit))


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


def _same_product(conn, tco_before, tco_after):
    """"" when the two jobs are the same product, else why they cannot be compared.

    NEITHER cross-campaign primitive checked this, and metric_delta's own
    docstring says "between two campaigns of the same product". Handed two
    different products, config_diff answered - at length. Among its "changed"
    fields were eut_model DEMO-50199002 -> DEMO-50199003 and eut_serial
    DEMOSN0000302 -> DEMOSN0000303, which is not a unit that was modified, it is
    two different units. 130 lines followed, every one of them looking like a
    finding and none of them being one.

    A comparison of two products is not a weaker answer than a comparison of two
    campaigns. It is a different question, and the reader cannot tell from the
    output which one they got - so it is refused by name rather than returned.
    """
    rows = _rows(conn, """
        SELECT r.tco_id, r.product_name, r.model_number
        FROM iec_emc_requests r WHERE r.tco_id IN (%(a)s, %(b)s)
    """, a=tco_before, b=tco_after)
    found = {r["tco_id"]: r for r in rows}
    missing = [t for t in (tco_before, tco_after) if t not in found]
    if missing:
        return ("No job with tco_id %s. Check the identifier - a comparison "
                "needs two real jobs." % " or ".join(repr(m) for m in missing))
    a, b = found[tco_before], found[tco_after]
    if (a["product_name"] or "").strip().lower() != (b["product_name"] or "").strip().lower():
        return (
            "%s is %s and %s is %s - these are DIFFERENT PRODUCTS, so there is "
            "nothing to compare between them. This analysis answers 'what "
            "changed between two tests OF THE SAME UNIT'; across two products "
            "every field differs and none of the differences mean anything. "
            "Say that plainly. If you wanted one product's history, call "
            "timeline with the product name and compare two of ITS tco_ids."
            % (tco_before, a["product_name"], tco_after, b["product_name"]))
    return ""


def config_diff(conn, tco_before, tco_after):
    """Field-level difference between two campaigns' submitted forms.

    Reads the frozen revision, not the live record: the live one has moved on,
    and "what changed between the failing test and the passing one" has to
    compare what was actually submitted each time.
    """
    refusal = _same_product(conn, tco_before, tco_after)
    if refusal:
        return refusal
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

# --------------------------------------------------------------------------
# the REVIEW axis of one datasheet, revision by revision
# --------------------------------------------------------------------------
# Nine of the ten primitives above are about the PRODUCT axis - did the unit fail
# the standard, what was fitted, did the readings improve. Exactly one,
# rejection_modes, is about the RECORD axis, and it is a lab-wide count. So
# "this datasheet was sent back twice, why?" - the commonest review question
# there is - had no primitive at all, and fell to model-authored SQL over three
# history tables with two different revision-numbering conventions.
#
# WHY THE WHOLE FORM IS COMPARED AND NOT A CHOSEN SUBSET
# A reviewer rejects for whatever is wrong, which is not restricted to the fields
# someone thought to project into columns. "The calibration date is missing" is
# fixed in eq_cal_due[]; "the wrong limit line" in a limit field; "value in the
# wrong unit" anywhere at all. Diffing projected scalars would answer only for
# the fields we happened to normalise, and silently return "nothing changed" for
# the rest - which reads as the engineer having ignored the reviewer.
#
# So the comparison is over the ENTIRE form_json of each frozen revision, key by
# key. What it does NOT do is hand those blobs to the model: two revisions is 12
# KB against a 15 KB result budget, and asking a model to diff 129 keys by eye is
# the arithmetic-by-LLM that this whole module exists to avoid. The diff is
# computed here, in code, and the model receives the fields that changed with
# their before and after values.
_REVIEW_NOISE = frozenset((
    "tco_id", "job_number", "assignment_id", "test_date", "tested_by_date",
    "peer_reviewer_id", "result",
))
MAX_CHANGED_FIELDS = 25
MAX_DIFF_VALUE_CHARS = 90


def _short(value):
    if isinstance(value, list):
        text = ", ".join(str(v) for v in value)
    else:
        text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= MAX_DIFF_VALUE_CHARS else text[:MAX_DIFF_VALUE_CHARS] + "..."


# A grid cell: ind_r5_c1, pf_50_col_3, meas_line1__c2. Twenty-four of these
# changing is ONE act by the engineer - "filled in the rest of the indirect
# discharge grid" - and listing them individually crowded out the field that
# actually mattered and hit the reporting cap on its own.
#
# __h\d+ is in here because a grid's HEADER row is part of the grid. Left out,
# meas_line0__h0 .. __h7 counted as eight separate changed fields per grid, so
# one re-keyed CE grid reported sixteen header changes alongside the two cell
# blocks - and "Frequency (MHz)" moving is not something anybody asked about.
# The trailing (?:\[\])? matters: a list-valued cell is stored as
# meas_line0__c0[], and with the pattern anchored straight to $ that key never
# matched, so only the HEADERS of a grid collapsed while its eight columns of
# readings were still listed one by one as "filled in from blank".
_CELL_RE = re.compile(
    r"^(.*?)(?:_r\d+_c\d+|_col_\d+|__c\d+|__h\d+|_r\d+_name)(?:\[\])?$")


def _cell_sig(value):
    """One grid cell, flattened for identity comparison across a re-key."""
    if isinstance(value, list):
        return "|".join(str(v).strip() for v in value)
    return "" if value is None else str(value).strip()


def _grids_by_stem(form):
    """{stem: {cell-suffix: value-signature}} for every grid in one form."""
    out = {}
    for key, val in form.items():
        match = _CELL_RE.match(key)
        stem = match.group(1) if match else ""
        if stem:
            out.setdefault(stem, {})[key[len(stem):]] = _cell_sig(val)
    return out


def _same_readings(before_grid, after_grid, minimum=3):
    """Is this the SAME grid under a different stem, cell for cell?"""
    shared = set(before_grid) & set(after_grid)
    if len(shared) < minimum or len(shared) < len(after_grid):
        return False
    return all(before_grid[k] == after_grid[k] for k in shared)


def _form_diff(before, after):
    """[{field, before, after}] over two whole forms, plus how many were dropped.

    Grid cells sharing a stem are collapsed into one entry counting them, so a
    completed observation grid reads as one change and not as twenty-four.
    """
    singles, grids = [], {}
    # A grid re-keyed to a different index - meas_line1__* copied to
    # meas_line0__* with byte-identical readings while meas_index flips 1 -> 0 -
    # is the FORM renumbering a grid, not the engineer measuring anything. Keys
    # diffed literally, that reported 33 cells as "FILLED IN (were blank)", so
    # "what changed between the two revisions" answered "the whole CE
    # measurement grid was entered" about readings that had not moved by a
    # single digit. Measured on datasheet 375 revision 1 -> 2.
    before_grids = _grids_by_stem(before)
    for key in sorted(set(before) | set(after)):
        if key in _REVIEW_NOISE:
            continue
        va, vb = before.get(key), after.get(key)
        if va == vb:
            continue
        m = _CELL_RE.match(key)
        if m and m.group(1):
            grids.setdefault(m.group(1), []).append((key, va, vb))
        else:
            singles.append({"field": key, "before": _short(va), "after": _short(vb)})

    for stem, cells in sorted(grids.items()):
        if len(cells) == 1:
            key, va, vb = cells[0]
            singles.append({"field": key, "before": _short(va), "after": _short(vb)})
            continue
        filled = sum(1 for _k, va, vb in cells if not str(va or "").strip()
                     and str(vb or "").strip())
        cleared = sum(1 for _k, va, vb in cells if str(va or "").strip()
                      and not str(vb or "").strip())

        moved_from = None
        if filled == len(cells):
            after_grid = {k[len(stem):]: _cell_sig(vb) for k, _va, vb in cells}
            for other, other_grid in sorted(before_grids.items()):
                if other != stem and _same_readings(other_grid, after_grid):
                    moved_from = other
                    break
        if moved_from:
            singles.append({
                "field": stem + " grid",
                "before": "the same readings, under %s" % moved_from,
                "after": "re-keyed from %s - THE READINGS DID NOT CHANGE, the "
                         "form renumbered the grid" % moved_from,
                "cells": ", ".join(k for k, _a, _b in cells[:8])})
            continue

        what = "%d cell(s) changed" % len(cells)
        if filled:
            what = "%d cell(s) FILLED IN (were blank)" % filled
        elif cleared:
            what = "%d cell(s) CLEARED" % cleared
        singles.append({"field": stem + " grid", "before": "", "after": what,
                        "cells": ", ".join(k for k, _a, _b in cells[:8])})

    dropped = max(0, len(singles) - MAX_CHANGED_FIELDS)
    return singles[:MAX_CHANGED_FIELDS], dropped


def review_history(conn, product=None, tco=None, limit=40):
    """Every review round on a datasheet: the decision, and what changed after it.

    One row per revision, oldest first: what the reviewer decided, the coded
    finding and their own words, and the fields the engineer changed between the
    PREVIOUS revision and this one - compared across the whole submitted form.

    Reads datasheet_revision.form_json, which is the version the reviewer was
    actually looking at. The live record has moved on since.
    """
    where, args = [], {"lim": int(limit)}
    if product:
        where.append(_product_where(_PRODUCT_COLS_SHEET))
        args["prod"] = "%" + str(product).strip() + "%"
    if tco:
        where.append("d.tco_id = %(tco)s")
        args["tco"] = str(tco).strip()
    if not where:
        return ("review_history needs a product or a tco - it is the history of ONE "
                "datasheet's review rounds. For the lab-wide picture of why records "
                "get sent back, call rejection_modes with no arguments.")

    sheets = _rows(conn,
                   "SELECT d.id, d.tco_id, d.test_code, d.product_name, d.status "
                   "FROM `datasheet` d WHERE " + " AND ".join(where) +
                   " ORDER BY d.tco_id, d.test_code LIMIT %(lim)s", **args)
    if not sheets:
        return ("No datasheet matches that. resolve_entity will tell you whether the "
                "product or job exists at all - a name that matches nothing looks "
                "exactly like a datasheet that was never reviewed.")

    out = []
    for sheet in sheets:
        revs = _rows(conn,
                     "SELECT revision_no, form_json FROM datasheet_revision "
                     "WHERE datasheet_id = %(d)s ORDER BY revision_no", d=sheet["id"])
        forms = {}
        for r in revs:
            try:
                forms[r["revision_no"]] = json.loads(r["form_json"] or "{}") or {}
            except (TypeError, ValueError):
                forms[r["revision_no"]] = {}

        decisions = _rows(conn, """
            SELECT h.revision_no, h.from_status, h.to_status, h.actor_name,
                   h.actor_role, h.comment, h.reason_code, e.label AS reason_label,
                   h.created_at
            FROM datasheet_status_history h
            LEFT JOIN emc_reason_code e ON e.code = h.reason_code
            WHERE h.datasheet_id = %(d)s AND h.to_status IN ('Approved','Rejected')
            ORDER BY h.revision_no, h.id
        """, d=sheet["id"])

        if not decisions:
            out.append({"tco_id": sheet["tco_id"], "test_code": sheet["test_code"],
                        "product_name": sheet["product_name"],
                        "review_round": None,
                        "note": "submitted %d time(s), never decided - currently %s"
                                % (len(revs), sheet["status"])})
            continue

        for d in decisions:
            rev = d["revision_no"]
            changed, dropped = _form_diff(forms.get(rev - 1, {}), forms.get(rev, {}))
            row = {
                "tco_id": sheet["tco_id"], "test_code": sheet["test_code"],
                "product_name": sheet["product_name"],
                "review_round": rev,
                "decision": d["to_status"],
                "reviewer": d["actor_name"],
                "decided_at": d["created_at"],
                "coded_finding": d["reason_code"] or "(not categorised)",
                "what_it_means": d["reason_label"] or "(no code recorded)",
                "reviewer_said": d["comment"],
                "fields_changed_since_previous_round": len(changed) + dropped,
                "changed": changed,
            }
            if rev == 1:
                # Nothing precedes the first submission, so the "diff" against an
                # absent revision 0 is the entire form. Reporting that as 103
                # changed fields beside a count of 0 was simply contradictory.
                row["changed"] = []
                row["fields_changed_since_previous_round"] = 0
                row["note"] = "first submission - nothing to compare against"
            elif dropped:
                row["changed_truncated"] = "%d more field(s) changed" % dropped
            out.append(row)
    return out


PRIMITIVES = {
    "failure_modes": failure_modes,
    "rejection_modes": rejection_modes,
    "review_load": review_load,
    "review_history": review_history,
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


def outcome(row):
    """"pass", "fail" or "unknown" for one campaign, whichever way it is recorded.

    The demo corpus writes result = PASS / FAIL. Real datasheets in this lab do
    not: they carry the IEC 61000-4 performance CRITERION in the same column, so
    result reads 'A' on eleven compliant tests and 'D' on the ones that failed,
    and a handful say FAIL outright. Counting only the literals PASS and FAIL
    reported "11 campaigns: 0 failed, 0 passed" for a real product - true of the
    strings, useless as an answer.

    A, B and C all mean the unit met its criterion and is compliant; D means it
    did not. That is the standard's own scale, not a guess.
    """
    r = str(row.get("result") or "").strip().upper()
    c = str(row.get("met_performance_criteria") or "").strip().upper()
    if r in ("PASS", "COMPLIES", "COMPLIANT") or r in ("A", "B", "C"):
        return "pass"
    if r in ("FAIL", "FAILED", "DOES NOT COMPLY") or r == "D":
        return "fail"
    if not r and c:                      # result blank, criterion recorded
        return "pass" if c in ("A", "B", "C") else ("fail" if c == "D" else "unknown")
    return "unknown"


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
    res = [outcome(r) for r in rows]
    out = {"campaigns_total": len(rows),
           "campaigns_failed": res.count("fail"),
           "campaigns_passed": res.count("pass")}
    if res.count("unknown"):
        out["campaigns_with_no_recorded_outcome"] = res.count("unknown")
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


def _empty_note(kwargs):
    """What to say when a primitive matched nothing.

    This line used to read "(no rows - say so plainly rather than reaching for a
    different table)", which is sound advice for an unfiltered analysis and
    catastrophic for a filtered one: it instructs the model to report an absence
    that the FILTER may have manufactured. It duly did. Asked why DEMO-EMC-301
    was rejected and whether others were too, the model called

        timeline(product='DEMO Lifecycle Probe Analyser', tco='DEMO-EMC-302')

    - a real product and a real TCO that belong to different jobs. The arguments
    are ANDed, so nothing matched, and the answer said "no recorded rejections
    for DEMO-EMC-302 or DEMO-EMC-303". Both had been rejected, and the previous
    turn in the same conversation had already said so.

    With two or more filters the likeliest explanation of zero rows is that they
    disagree, not that the lab has no such history, so the note now says which
    filters were applied and tells the model to drop back to one before
    concluding anything.
    """
    applied = [k for k, v in sorted(kwargs.items()) if v not in (None, "")]
    if len(applied) < 2:
        return ("  (no rows for this filter - before reporting an absence, "
                "confirm the value you filtered on exists; a value that is not "
                "in the data returns exactly this and looks identical to a real "
                "absence)")
    return ("  (NO ROWS. %d filters were applied together and they are ANDed: "
            "%s. Zero rows most often means they DISAGREE - a product and a TCO "
            "belonging to different jobs match nothing at all. Re-run with ONE "
            "of them, most precisely the TCO, before concluding anything. Do NOT "
            "report this as 'no history' or 'never rejected': you have not "
            "established that yet.)" % (len(applied), ", ".join(applied)))


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
            lines.append(_empty_note(kwargs))
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
    # A TCO is IEC-EMC-004 / DEMO-EMC-201 - letters, dashes, digits. Asked to
    # summarise "the DEMO Orion Analyzer O9", the model read the model-suffix as
    # an identifier and called timeline with product="Orion Analyzer", tco="O9".
    # Those are ANDed, so a real product plus a fictional TCO matched nothing and
    # the answer was "no timeline history available for this product" about a
    # product with three campaigns. Silently dropping the junk is right: the
    # product name was correct and sufficient.
    for key in ("tco", "tco_before", "tco_after"):
        val = str(kwargs.get(key) or "").strip()
        if val and not _TCO_RE.match(val):
            kwargs.pop(key, None)
            log.info("insight %s: ignoring %s=%r, not a TCO", name, key, val)
    # A TCO identifies exactly one job, so a product alongside it adds no
    # precision - it can only take rows away, and it did. Asked why DEMO-EMC-301
    # was rejected and whether any others were, the model carried the product
    # forward from the first part of the question and called
    #
    #     timeline(product='DEMO Lifecycle Probe Analyser', tco='DEMO-EMC-302')
    #
    # Both arguments real, both well-formed, and belonging to different jobs -
    # 302 is the Spectra Bench Photometer. ANDed, they matched nothing, and the
    # answer became "no recorded rejections for DEMO-EMC-302 or DEMO-EMC-303"
    # when both had been rejected and the previous turn had already said so.
    #
    # The _TCO_RE guard above cannot catch this: DEMO-EMC-302 is a perfectly
    # valid TCO. The contradiction is between two valid values, and the precise
    # one wins. Same principle as dropping a malformed TCO, in the other
    # direction: keep the argument that identifies the subject exactly.
    if kwargs.get("tco") and kwargs.pop("product", None) is not None:
        log.info("insight %s: dropped product= alongside tco=%r; a TCO already "
                 "names one job", name, kwargs["tco"])

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
    # An empty list is not an empty lab. timeline and failure_detail both need a
    # product or a TCO, and returning [] without one made the model announce
    # that it "could not identify a most common failure reason from history" -
    # reporting a missing argument as a finding about the data. Name the mistake
    # and name the primitive that does answer it.
    if name in ("timeline", "failure_detail") and not (kwargs.get("product")
                                                      or kwargs.get("tco")):
        return ("%s is per-product: it needs product= or tco=. This looks like a "
                "question about the whole lab, so call failure_modes (no "
                "arguments) for what products fail for and how many are "
                "affected, or rejection_modes for why records are sent back in "
                "peer review. Do NOT report this as 'no failures found'." % name)
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

    # A primitive that returns a STRING is explaining why it cannot answer -
    # config_diff and metric_delta refuse two different products, and only they
    # can know that because only they have the connection. Pass it through
    # untouched: _flatten iterates whatever it is given, so a bare string arrived
    # at the model one character per line, 380 lines of "D", "E", "M", "O". The
    # guards written before these lived in run() and never met the renderer,
    # which is why nothing had tripped over this before.
    if isinstance(data, str):
        if ledger is not None:
            ledger.note("insights", "%s declined: %s" % (name, data[:160]))
        return data

    summary = _summarise(data)
    pairs = _flatten(data) + list(summary.items())
    if ledger is not None:
        # Recorded as REAL ROWS, one per result, not as one row per field.
        # Flattening two failure modes into fourteen (field, value) pairs made the
        # evidence digest look like fourteen records, and the answer duly said
        # "these findings include 14 failure modes and 15 rejection modes" -
        # 2x7 and 3x5. The numbers were arithmetically present in the evidence,
        # which is why grounding passed them, and they described nothing.
        # The flattened pairs still go in as a second entry so individual values
        # stay citable; only the shape the model counts has changed.
        rows = [r for r in (data if isinstance(data, list) else [data])
                if isinstance(r, dict)]
        if rows:
            cols = list(rows[0].keys())
            ledger.record("insights", "%s(%s) -> %d row(s)" % (name, kwargs, len(rows)),
                          columns=cols,
                          rows=[[r.get(c) for c in cols] for r in rows])
        ledger.record("insights", "%s(%s) values" % (name, kwargs),
                      columns=["field", "value"],
                      rows=[[k, v] for k, v in pairs])
        ledger.note("insight", "%s returned %d row(s)"
                    % (name, len(rows) if rows else 0))
    text = _render(name, data, kwargs)
    # How many rows this actually is, in words, because the model guessed. Given
    # two failure modes it wrote "there are 14 failure modes identified, but only
    # two are detailed here" - 14 being a row count from elsewhere in the
    # evidence, which is why the grounding check passed it: the number was
    # present, the sentence around it was invented.
    if isinstance(data, list) and name in ("failure_modes", "rejection_modes",
                                           "cohort", "resolved_how"):
        text += ("\n\nThat is the COMPLETE list: %d row(s), nothing withheld. Do "
                 "not say more exist." % len(data))
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
    # The instruction goes on its OWN line, above the sentence. When the two were
    # one string the model quoted the whole thing, and a user asking whether a
    # unit would pass its next test received a reply opening with
    # 'IN WORDS (quote this, do not re-count):' - my scaffolding, verbatim, in a
    # lab's answer. Give it a clean sentence to lift and the instruction stays
    # behind.
    return ("COUNTS - use these rather than re-counting the rows:\n"
            + ". ".join(bits) + ".")


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
    fails = [c for c in data if outcome(c) == "fail"]
    passes = [c for c in data if outcome(c) == "pass"]
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
