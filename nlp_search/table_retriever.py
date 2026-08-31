# -*- coding: utf-8 -*-
"""Which 3-6 tables does this question actually need?

WHY THIS EXISTS
---------------
A worker's allowlist is its whole domain: the requests worker may touch 28
tables, the datasheets worker 26. That is the right SECURITY boundary and it
stays. It is the wrong ATTENTION boundary. The catalog is already the largest
line in the prompt bill, and a question about who a test is assigned to is
handed the thirteen per-test request-parameter tables it will never open.

The cost is not only tokens. Every extra table is another chance to answer the
question from the wrong one - the failure this module exists to reduce, where
correct-looking rows come back from a table that does not mean what the user
asked. Naming the three or four tables that matter, with a reason attached,
gives the worker a default to reach for and the verifier something to check the
executed SQL against.

WHY LEXICAL AND NOT A VECTOR STORE
----------------------------------
Two reasons, both about this specific corpus rather than embeddings in
general. The vocabulary is small, closed and already written down: the glossary
in build_catalog maps lab words onto tables by hand, the enum values are
embedded literally in the catalog, and TEST_CODE_CANON knows every spelling of
every test. Cosine similarity would have to rediscover that, less reliably.

And a score has to be explainable. When a question is answered from the wrong
table the first thing anyone asks is why that table was chosen, and "0.83"
is not an answer. Every point awarded here names the term that earned it.

SCORING
-------
Signals, in rough order of how much they are trusted:

  glossary        the hand-written lab-word -> table map. A direct hit is
                  worth more than anything the schema can tell you, because
                  a human wrote it down knowing what people mean.
  purpose         the one-line table purpose, which build_catalog says drives
                  routing more than column lists do.
  column          a question term matching a column name.
  enum            a question term matching a literal VALUE in the data, which
                  is a strong signal - "cancelled" only appears in
                  planner_entries.status.
  relationship    a table reachable by one reviewed join from a table that
                  already scored. Small, and capped, so a hub table does not
                  drag its whole neighbourhood in.
  domain          a modest nudge for the tables the routed worker owns.

Scores are normalised to 0-1 against the best-scoring table so the numbers
read the way the brief's example does. They are ordinal, not probabilities:
the gap between #1 and #4 is meaningful, the absolute value is not.

This module reads the generated catalog and semantics.RELATIONSHIPS. It runs
no SQL and makes no model call, so it cannot fail a question - retrieve()
returns [] rather than raising and the caller behaves as it did before.
"""
import re

from . import schema_catalog as cat

# --------------------------------------------------------------------------
# tuning
# --------------------------------------------------------------------------
# Weights are deliberately coarse. A finer scale invites tuning against
# whichever question was last wrong, and the ordering is what matters.
W_GLOSSARY = 5.0
W_PURPOSE = 2.0
W_COLUMN = 1.5
W_ENUM = 3.0
W_RELATIONSHIP = 1.0
W_DOMAIN = 0.75

MAX_TABLES = 6
MIN_TABLES = 3

# A relationship boost is worth having once. Without a cap, `datasheet` - which
# joins to twenty-odd tables - pulls every one of them above the tables the
# question actually named.
_MAX_RELATIONSHIP_BOOSTS = 3

# Words that match everything and therefore distinguish nothing. "test" is in
# this lab's every table name and half its columns; leaving it in ranks tables
# by how often they say "test" rather than by relevance.
_STOPWORDS = frozenset("""
a an and any are as at be been by can could did do does for from get give had
has have how i in into is it its list me many much my no not of on or our out
show that the their them there these they this those to under up us was we were
what when where which who whom whose why will with would you your
test tests testing data database table tables row rows record records value
values please tell there's
""".split())

# Lab vocabulary -> the tables that word implies. Distilled from the glossary
# build_catalog bakes into every worker prompt, kept here as structured data
# rather than prose so it can be scored instead of read.
#
# Deliberately asymmetric: the words that name the REQUESTED/SCHEDULED/RECORDED
# distinction get the most entries, because that distinction is the one the
# system gets wrong.
GLOSSARY = {
    # identity
    "job": ("iec_emc_requests",),
    "jobs": ("iec_emc_requests",),
    "tco": ("iec_emc_requests",),
    "project": ("iec_emc_requests",),
    "request": ("iec_emc_requests", "iec_emc_request_tests"),
    "requests": ("iec_emc_requests", "iec_emc_request_tests"),
    "requested": ("iec_emc_request_tests", "iec_emc_requests"),
    "customer": ("iec_emc_requests",),
    "requester": ("iec_emc_requests",),
    "product": ("iec_emc_requests",),
    "products": ("iec_emc_requests",),
    "eut": ("iec_emc_requests",),
    "model": ("iec_emc_requests",),
    "manufacturer": ("iec_emc_requests",),

    # schedule
    "schedule": ("planner_entries",),
    "scheduled": ("planner_entries",),
    "planner": ("planner_entries",),
    "calendar": ("planner_entries",),
    "booked": ("planner_entries",),
    "start": ("planner_entries",),
    "end": ("planner_entries",),
    "cancelled": ("planner_entries",),

    # assignment - the ambiguous one. BOTH readings are offered on purpose:
    # the request-level assignment and the schedule-level one are different
    # numbers, and the retriever's job is to put both tables in front of the
    # planner, not to pick.
    "assigned": ("iec_emc_request_tests", "planner_entries", "iec_emc_requests"),
    "assignment": ("iec_emc_request_tests", "planner_entries"),
    "engineer": ("planner_entries", "iec_emc_request_tests", "users"),
    "engineers": ("planner_entries", "iec_emc_request_tests", "users"),
    "tester": ("datasheet", "users"),
    "person": ("users",),
    "people": ("users",),
    "user": ("users",),
    "who": ("users",),
    "workload": ("planner_entries",),

    # datasheets / results
    "datasheet": ("datasheet",),
    "datasheets": ("datasheet",),
    "sheet": ("datasheet",),
    "filled": ("datasheet",),
    "result": ("datasheet",),
    "results": ("datasheet",),
    "pass": ("datasheet",),
    "passed": ("datasheet",),
    "fail": ("datasheet",),
    "failed": ("datasheet",),
    "failure": ("datasheet", "datasheet_status_history"),
    "outcome": ("datasheet",),
    "submitted": ("datasheet",),
    "approved": ("datasheet", "datasheet_status_history"),
    "draft": ("datasheet",),
    "completed": ("datasheet",),

    # review
    "review": ("datasheet_status_history", "planner_entries"),
    "reviewer": ("planner_entries", "datasheet_status_history"),
    "peer": ("planner_entries", "datasheet_status_history"),
    "rejected": ("datasheet_status_history", "iec_emc_requests"),
    "rejection": ("datasheet_status_history", "iec_emc_requests"),

    # measurements
    "measurement": ("datasheet_measurement",),
    "measurements": ("datasheet_measurement",),
    "reading": ("datasheet_measurement",),
    "readings": ("datasheet_measurement",),
    "frequency": ("datasheet_measurement",),
    "margin": ("datasheet_measurement",),
    "limit": ("datasheet_measurement",),
    "observation": ("datasheet_observation", "datasheet_observation_legend"),
    "observations": ("datasheet_observation", "datasheet_observation_legend"),
    "criterion": ("datasheet_observation", "datasheet_observation_legend"),
    "criteria": ("datasheet_observation", "datasheet_observation_legend"),

    # history
    "history": ("datasheet_draft_history", "datasheet_revision",
                "datasheet_status_history"),
    "changed": ("datasheet_draft_history",),
    "previous": ("datasheet_revision", "datasheet_draft_history"),
    "revision": ("datasheet_revision",),
    "modification": ("datasheet_modification",),
    "modifications": ("datasheet_modification",),
    "fitted": ("datasheet_modification",),

    # inventory
    "equipment": ("equipment", "datasheet_equipment"),
    "instrument": ("equipment",),
    "instruments": ("equipment",),
    "kit": ("equipment",),
    "calibration": ("equipment",),
    "calibrated": ("equipment",),
    "maintenance": ("maintenance", "equipment"),
    "inventory": ("equipment",),
    "asset": ("equipment",),
    "software": ("datasheet_software",),

    # standards
    "standard": ("iec_emc_request_product_standards",
                 "iec_emc_request_test_standards", "basic_standard_map"),
    "standards": ("iec_emc_request_product_standards",
                  "iec_emc_request_test_standards", "basic_standard_map"),
}

# The test codes, in every spelling. These are NOT scored as ordinary terms:
# "ESD" matches the purpose line of all eleven datasheet_<code> tables equally,
# so treating it as a word ranks them in a tie and the one table the question
# is actually about does not surface. Instead the code is canonicalised and
# used to name its own two tables directly - which is the strongest signal in
# the whole module, and the only one that is exact rather than lexical.
_TEST_CODE_RE = re.compile(
    r"\b(?:CE|RE|EFT|ESD|SURGE|HARMONIC|CRF|PFMF|RS_RI|RS|RS_INTERIM|FLICKER|"
    r"VOLTAGEFLICKER|VOLTAGE_DIPS|VOLTAGEDIPS|POWER_FREQ)\b", re.I)

W_TEST_CODE = 4.0

# Request-side per-test tables are not named after the canonical code - the
# request keeps its own spelling. Mapped explicitly rather than guessed.
_REQUEST_TEST_TABLE = {
    "CE": "iec_emc_request_test_ce",
    "CRF": "iec_emc_request_test_crf",
    "EFT": "iec_emc_request_test_eft",
    "ESD": "iec_emc_request_test_esd",
    "RE": "iec_emc_request_test_re",
    "SURGE": "iec_emc_request_test_surge",
    "HARMONIC": "iec_emc_request_test_harmonic",
    "VOLTAGEFLICKER": "iec_emc_request_test_flicker",
    "PFMF": "iec_emc_request_test_power_freq",
    "VOLTAGEDIPS": "iec_emc_request_test_voltage_dips",
    "RS_RI": "iec_emc_request_test_rs",
}


def _test_code_tables(question):
    """[(table, code)] for every test code the question names.

    Both sides are returned - the datasheet table holding what was MEASURED and
    the request table holding what was ASKED FOR - because which one the
    question means is exactly the distinction this system gets wrong, and the
    retriever is not the layer that should decide it.
    """
    try:
        from .semantics import TEST_CODE_CANON
    except Exception:  # noqa: BLE001 - fall back to no test-code signal
        return []
    out = []
    for raw in set(m.upper() for m in _TEST_CODE_RE.findall(question or "")):
        code = TEST_CODE_CANON.get(raw.replace(" ", "_"), raw)
        sheet = "datasheet_" + code.lower()
        if sheet in cat.ALLOWED_TABLES:
            out.append((sheet, code))
        req = _REQUEST_TEST_TABLE.get(code)
        if req:
            out.append((req, code))
    return out

_WORD_RE = re.compile(r"[a-z0-9_]+")


def _terms(question):
    """Content words, lower-cased, stopwords and test codes removed."""
    q = _TEST_CODE_RE.sub(" ", question or "")
    seen, out = set(), []
    for w in _WORD_RE.findall(q.lower()):
        if len(w) < 3 or w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out


def _singular(word):
    """Crude depluraliser - enough for column matching, no stemmer needed."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("es") and not word.endswith("ses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


def _in_purpose(term, table):
    purpose = (cat.TABLE_PURPOSE.get(table) or "").lower()
    return term in purpose or _singular(term) in purpose


def _discount(hits, total):
    """Weight for a term matching `hits` of `total` tables.

    1.0 when it picks out a single table, falling towards 0 as it matches
    everything. Linear rather than log because the table count here is 52, not
    a document corpus, and the shape that matters is "does this narrow things
    down at all" rather than a calibrated IDF.
    """
    if hits <= 0 or total <= 0:
        return 0.0
    if hits == 1:
        return 1.0
    frac = hits / float(total)
    return max(0.0, 1.0 - frac) / (1.0 + (hits - 1) * 0.25)


def _column_hit(term, table):
    """Does this term name a column on this table? Substring both ways.

    Both directions matter: "engineer" should hit `assigned_engineer_name`,
    and "calibration_due_date" in a question should hit `calibration_due`.
    """
    stem = _singular(term)
    for col in cat.COLUMNS.get(table, ()):
        if term == col or stem == col:
            return col
        if len(stem) >= 4 and (stem in col or col in stem):
            return col
    return None


def _enum_hit(term, table):
    """Does this term appear as an actual VALUE in this table?"""
    prefix = table + "."
    for key, values in cat.ENUM_VALUES.items():
        if not key.startswith(prefix):
            continue
        for v in values:
            if v and term == str(v).strip().lower():
                return "%s = '%s'" % (key.split(".", 1)[1], v)
    return None


def _neighbours():
    """table -> tables one reviewed join away, from semantics.RELATIONSHIPS.

    Imported lazily and defensively: semantics imports a good deal, and a
    retrieval helper must not be the reason a question fails.
    """
    out = {}
    try:
        from .semantics import RELATIONSHIPS
    except Exception:  # noqa: BLE001 - relationship boost is optional
        return out
    for rel in RELATIONSHIPS:
        names = [str(t).strip("` ") for t in (rel.get("tables") or ())]
        for a in names:
            for b in names:
                if a != b:
                    out.setdefault(a, set()).add(b)
    return out


def retrieve(question, domain=None, top_n=MAX_TABLES, allowed=None):
    """[{"name", "score", "reason"}] - the tables this question needs.

    `domain` is the routed worker, when one is known: its tables get a small
    nudge and become the candidate pool. `allowed` overrides that pool
    outright, so a caller can retrieve within a narrower allowlist than the
    domain's.

    Never raises. An empty list means "no opinion" and the caller should carry
    on with the full domain allowlist, exactly as before this module existed.
    """
    try:
        return _retrieve(question, domain, top_n, allowed)
    except Exception:  # noqa: BLE001 - retrieval is an optimisation, not a gate
        return []


def _retrieve(question, domain, top_n, allowed):
    terms = _terms(question)
    if not terms:
        return []

    pool = tuple(allowed) if allowed else cat.tables_for(domain)
    domain_tables = set(cat.DOMAIN_TABLES.get(domain, ()) if domain else ())

    scores = {}
    reasons = {}

    def award(table, points, why):
        if table not in pool:
            return
        scores[table] = scores.get(table, 0.0) + points
        reasons.setdefault(table, []).append(why)

    for term in terms:
        for table in GLOSSARY.get(term, ()):
            award(table, W_GLOSSARY, "'%s' is lab vocabulary for this table" % term)

        # A term that matches half the schema tells you nothing about which
        # half. "requests" appears in the purpose line of all twenty-four
        # iec_emc_request_* children, so scoring it flat ranked the accessories
        # and cables tables alongside the request table itself for "how many
        # requests are there". Discounting by how many tables a term hits is
        # the standard fix and keeps the score explainable - a rare word is
        # worth more than a common one, and you can see which was which.
        purpose_hits = [t for t in pool
                        if _in_purpose(term, t)]
        column_hits = [(t, _column_hit(term, t)) for t in pool]
        column_hits = [(t, c) for t, c in column_hits if c]

        p_disc = _discount(len(purpose_hits), len(pool))
        c_disc = _discount(len(column_hits), len(pool))

        for table in purpose_hits:
            award(table, W_PURPOSE * p_disc, "purpose mentions '%s'" % term)
        for table, col in column_hits:
            award(table, W_COLUMN * c_disc, "column %s" % col)
        for table in pool:
            enum = _enum_hit(term, table)
            if enum:
                award(table, W_ENUM, "holds the value '%s' (%s)" % (term, enum))

    for table, code in _test_code_tables(question):
        award(table, W_TEST_CODE, "the %s test's own table" % code)

    if not scores:
        return []

    if domain_tables:
        for table in list(scores):
            if table in domain_tables:
                award(table, W_DOMAIN, "owned by the %s worker" % domain)

    # One hop out from whatever already scored, so a question that names only
    # the product still surfaces the table holding the thing being counted.
    neighbours = _neighbours()
    seeded = sorted(scores, key=lambda t: scores[t], reverse=True)[:_MAX_RELATIONSHIP_BOOSTS]
    for table in seeded:
        for nb in neighbours.get(table, ()):
            if nb in scores:
                continue
            award(nb, W_RELATIONSHIP, "joins to %s" % table)

    best = max(scores.values()) or 1.0
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))

    # Keep everything within reach of the leader rather than a flat top-N: a
    # cross-domain question legitimately needs five tables and a "how many
    # products" needs one, and a fixed N is wrong for both.
    out = []
    for name, raw in ranked[:top_n]:
        if len(out) >= MIN_TABLES and raw < best * 0.25:
            break
        out.append({
            "name": name,
            "score": round(raw / best, 2),
            "reason": _reason(reasons.get(name, ())),
        })
    return out


def _reason(bits):
    """One short phrase, strongest signal first, deduplicated."""
    seen, kept = set(), []
    for b in bits:
        if b in seen:
            continue
        seen.add(b)
        kept.append(b)
    return "; ".join(kept[:3])


def prompt_block(tables):
    """The retrieved tables, rendered for the worker prompt.

    A hint, not a fence. The allowlist is still the domain's - this says where
    to look first, and saying "start here" rather than "only here" is
    deliberate: an early version phrased it as a restriction and the worker
    refused to answer a question whose table the retriever had ranked seventh.
    """
    if not tables:
        return ""
    lines = ["TABLES MOST LIKELY TO ANSWER THIS (start here; you may use others "
             "in your allowlist if these do not fit):"]
    for t in tables:
        lines.append("  %-32s %s" % (t["name"], t["reason"]))
    return "\n".join(lines)
