# -*- coding: utf-8 -*-
"""Is this plan answerable, and does it say one thing rather than two?

WHY THIS IS SEPARATE FROM THE PLANNER
-------------------------------------
The planner's job is to write down what it thinks the question means. Judging
whether that reading is safe to act on is a different job with a different
failure mode, and mixing them means the component that produced a bad plan is
also the one deciding it is fine.

WHY IT ADVISES RATHER THAN BLOCKS
---------------------------------
This is the part of the design that had to be argued with. The obvious build
is: plan fails validation, refuse to generate SQL, ask the user. The reason it
is not built that way is written all over the rest of this package - the
system's expensive failures have been over-refusal about as often as
over-confidence. It asked "which job did you mean" twice for a question that
named a product with four campaigns, and handed a menu back to a user who
wanted a history. A guard that turns every incomplete plan into a question
would do that on purpose, at scale.

So the verdict has two levels and only one of them stops anything:

  BLOCK   the plan is not merely thin, it is contradictory - the question
          asks for a state whose definitions disagree about what is even
          being counted, or names an entity that resolved to nothing. Acting
          on it produces a confident wrong number. There are few of these and
          each is listed explicitly.

  WARN    the plan is incomplete or ambiguous in a way the worker can work
          through with the evidence it has. The warning rides along in the
          prompt and the worker is told to resolve it; the answer still
          happens.

A BLOCK does not mean "refuse". It means "do not let the worker write SQL
against this reading without saying which reading it picked" - the clarify
text is handed to the model as the thing to ask, and the model may still
answer if the evidence settles it.
"""

from . import query_planner
from . import scope as scope_mod
from . import schema_catalog as cat

OK = "ok"
WARN = "warn"
BLOCK = "block"


def validate(plan, allowed_tables=None):
    """{"verdict", "ok", "errors", "warnings", "clarify"}.

    Never raises. A plan of None validates as OK with nothing to say, because
    the pipeline is then running exactly as it did before planning existed and
    this module has no opinion to offer.
    """
    try:
        return _validate(plan, allowed_tables)
    except Exception:  # noqa: BLE001 - the guard must not be the failure
        return _verdict(OK, [], [], None)


def _verdict(verdict, errors, warnings, clarify):
    return {"verdict": verdict, "ok": verdict != BLOCK, "errors": errors,
            "warnings": warnings, "clarify": clarify}


def _validate(plan, allowed_tables):
    if not plan:
        return _verdict(OK, [], [], None)

    errors, warnings, clarify = [], [], None

    operation = plan.get("operation")
    subject = plan.get("subject")
    entity = plan.get("entity") or {}
    metrics = plan.get("candidate_metrics") or []
    tables = plan.get("source_tables") or []

    # -- completeness ------------------------------------------------------
    # Missing is a warning, not a block. The worker reads the question too,
    # and a plan that could not name the operation is usually a phrasing the
    # regexes have not seen rather than a question nobody could answer.
    if operation not in query_planner.OPERATIONS:
        warnings.append(
            "The plan could not tell what kind of answer is wanted (count, "
            "list, breakdown, explanation). Re-read the question and decide "
            "before querying.")
    if subject not in query_planner.SUBJECTS:
        warnings.append(
            "The plan could not tell what the answer is about. Decide whether "
            "this is about tests, datasheets, requests, people or equipment - "
            "they live in different tables and give different numbers.")

    # -- scope -------------------------------------------------------------
    sc = plan.get("scope")
    if sc not in (scope_mod.REAL, scope_mod.DEMO, scope_mod.ALL):
        errors.append(
            "Data scope is not set. Every question is REAL unless it asks for "
            "demo data; answering without deciding risks mixing synthetic rows "
            "into a real answer.")

    # -- tables ------------------------------------------------------------
    pool = set(allowed_tables or cat.ALLOWED_TABLES)
    outside = [t for t in tables if t not in pool]
    if outside:
        warnings.append(
            "These tables are not in this worker's allowlist and cannot be "
            "queried here: %s. If the answer needs them, say so rather than "
            "substituting a table you can reach." % ", ".join(outside))
    if not tables:
        warnings.append(
            "No table was retrieved for this question. Work from the catalog "
            "and check the table you pick actually holds what was asked for.")

    # -- the ambiguous state: the case this whole layer exists for ----------
    # Two reviewed measures matching one word is not a defect in the data, it
    # is the word meaning two things. "Assigned" means 40 tests on the request
    # side and 16 on the schedule side, and picking one silently is how this
    # system produced its most confident wrong answers.
    if len(metrics) > 1:
        clarify = _clarify_for(plan, metrics)
        warnings.append(
            "'%s' has %d reviewed meanings here (%s) and they give different "
            "numbers. Report every reading, or say which one you used and why "
            "- do NOT pick one silently."
            % (plan.get("state") or "this term", len(metrics),
               ", ".join(metrics)))

    # -- entity ------------------------------------------------------------
    if entity.get("value"):
        if entity.get("excluded_by_scope"):
            errors.append(
                "%r exists only in the corpus this question excludes. Say so. "
                "Do NOT answer about a similarly-named record that is in "
                "scope." % entity.get("value"))
            clarify = clarify or (
                "%r has records only in the %s data. Did you want those?"
                % (entity.get("value"),
                   "demo" if sc == scope_mod.REAL else "real"))
        elif not entity.get("resolved"):
            errors.append(
                "%r did not resolve to anything in the database. Do NOT filter "
                "on it and report a count of zero - a zero from an unmatched "
                "filter means 'no such thing', which is a different answer."
                % entity.get("value"))
        elif entity.get("matched_how") == "approximate":
            warnings.append(
                "%r matched only approximately. Confirm which record is meant "
                "and use its exact value." % entity.get("value"))
        elif (entity.get("ambiguity_count") or 0) > 1:
            # Several campaigns on ONE product is not this case - the resolver
            # already collapsed those, so a count above one here means genuinely
            # different things share the name.
            warnings.append(
                "%r matches %d different records. Use the exact one meant, or "
                "report per record - do not merge them into one figure."
                % (entity.get("value"), entity["ambiguity_count"]))

    # -- internal contradictions -------------------------------------------
    if operation == "COUNT" and plan.get("grouping"):
        warnings.append(
            "This asks for a count but also groups by %s - return the "
            "breakdown AND its total, not one of the two."
            % plan.get("grouping"))
    if operation == "DESCRIBE" and subject != "SCHEMA":
        warnings.append(
            "This looks like a question about where data is stored. Answer it "
            "from the schema catalog with find_field - do not query rows.")
    if subject == "SCHEMA" and operation not in ("DESCRIBE", None):
        warnings.append(
            "A schema question is answered from the catalog, not by counting "
            "rows.")

    verdict = BLOCK if errors else (WARN if warnings else OK)
    return _verdict(verdict, errors, warnings, clarify)


def _clarify_for(plan, metrics):
    """The one question worth asking, when a term has several readings."""
    from . import semantics
    readings = []
    for name in metrics[:3]:
        m = semantics.METRICS.get(name) or {}
        if m.get("label"):
            readings.append(m["label"])
    if not readings:
        return None
    term = (plan.get("state") or "that").lower().replace("_", " ")
    return ("By %s, do you mean %s?"
            % (term, ", or ".join(readings)))


def prompt_block(verdict):
    """The blocking findings, for the worker prompt.

    Warnings are rendered by query_planner.prompt_block alongside the plan they
    qualify; this carries only the findings that change what the worker is
    allowed to do.
    """
    if not verdict or not verdict.get("errors"):
        return ""
    lines = ["PLAN PROBLEMS - these change what you may say:"]
    for e in verdict["errors"]:
        lines.append("  - %s" % e)
    if verdict.get("clarify"):
        lines.append("  If the evidence does not settle it, ask exactly this "
                     "and nothing more: %s" % verdict["clarify"])
    return "\n".join(lines)
