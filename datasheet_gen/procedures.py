# -*- coding: utf-8 -*-
"""One place that decides what a datasheet's TEST PROCEDURE says.

Before this, the EUT-support wording lived in four places - two functions in
generic_service and two Jinja arms in generic_form.html - and only RE, RS_RI and
HARMONIC had a rule at all. The rest printed whatever placeholder the template
shipped ("a 0.8/0.1m wooden table/insulation support") whichever configuration was
tested. Everything the form shows, the document prints and the admin page edits is
now resolved from the table below, so the three cannot disagree.

Two shapes, because the datasheets need two:

  phrase   The shipped text carries one span that EUT Configuration rewrites -
           usually a combined placeholder. `phrases` lists every rendering that
           span may currently hold, so applying the rule is both idempotent (the
           wanted phrase is left alone) and reversible (switching the dropdown back
           rewrites it again). The span includes its article and any trailing
           clause, so both branches read as English: SURGE's floor branch is
           "above 0.1 m insulation ..." where its tabletop branch is "on a 0.8 m
           wooden table ...".

  variant  Tabletop and Floor standing are different procedures, not different
           phrases. ESD grounds the HCP and the VCP and discharges on both for a
           tabletop EUT, and only the VCP for a floor-standing one - there is no
           phrase to swap. The variant replaces everything after the opening
           sentence, which is left alone because it names the basic standard and
           normalize_procedure_basic owns it.

Ports are separate from configuration: SURGE, EFT and CRF carry a block per test
port, and _surge_filter_procedure keeps the blocks for the ports actually tested.
"""
import re

TABLETOP = "tabletop"
FLOOR = "floor"


def config_side(cfg):
    """'Floor standing' -> FLOOR, anything else (incl. blank) -> TABLETOP.

    Blank means tabletop deliberately: every one of these procedures ships its
    tabletop wording, so an unanswered configuration leaves the text as it was
    rather than rewriting it to a floor-standing claim nobody made.
    """
    return FLOOR if "floor" in (cfg or "").strip().lower() else TABLETOP


#: The EUT-support rule per datasheet. Wordings are the ones the lab asked for, to
#: the letter - "of 0.8 m height" on the datasheets that phrase it that way, a bare
#: "0.8 m wooden table" on SURGE and EFT, and PFMF's trailing GRP clause included
#: because its two branches sit on different planes.
SUPPORT_RULES = {
    "RE": {
        "mode": "phrase",
        "tabletop": "on a non-conductive table of 0.8 m height",
        "floor": "on an insulation support of 0.1 m height",
        "phrases": (
            "on non-conductive table/ insulation support of 0.8/0.1m height",
            "on non-conductive table/ insulation support of 0.8/0.1 m height",
            "on a non-conductive table/ insulation support of 0.8/0.1m height",
            "on a non-conductive table of 0.8 m height",
            "on a non-conductive table of 0.8m height",
            "on an insulation support of 0.1 m height",
            "on an insulation support of 0.1m height",
        ),
    },
    "RS_RI": {
        "mode": "phrase",
        "tabletop": "on a non-conductive table of 0.8 m height",
        "floor": "on an insulation support of 0.1 m height",
        "phrases": (
            "on non-conductive table/ insulation support of 0.8/0.1m height",
            "on non-conductive table/ insulation support of 0.8/0.1 m height",
            "on a non-conductive table/ insulation support of 0.8/0.1m height",
            "on a non-conductive table of 0.8 m height",
            "on a non-conductive table of 0.8m height",
            "on an insulation support of 0.1 m height",
            "on an insulation support of 0.1m height",
        ),
    },
    "HARMONIC": {
        "mode": "phrase",
        "tabletop": "on a wooden table of 0.8 m height",
        "floor": "on an insulation support of 0.1 m height",
        "phrases": (
            "on a wooden table/insulation support at 0.8/0.1m height",
            "on a wooden table/ insulation support at 0.8/0.1m height",
            "on a wooden table/insulation support at 0.8/0.1 m height",
            "on a wooden table/ insulation support at 0.8/0.1 m height",
            "on a wooden table/insulation support of 0.8/0.1m height",
            "on a wooden table at 0.8 m height",
            "on a wooden table at 0.8m height",
            "on a wooden table of 0.8 m height",
            "on an insulation support at 0.1 m height",
            "on an insulation support at 0.1m height",
            "on an insulation support of 0.1 m height",
        ),
    },
    "VOLTAGEFLICKER": {
        "mode": "phrase",
        "tabletop": "on a wooden table of 0.8 m height",
        "floor": "on an insulation support of 0.1 m height",
        "phrases": (
            "on a wooden table/insulation support of 0.8/0.1m height",
            "on a wooden table/ insulation support of 0.8/0.1m height",
            "on a wooden table/insulation support of 0.8/0.1 m height",
            "on a wooden table/insulation support at 0.8/0.1m height",
            "on a wooden table of 0.8 m height",
            "on a wooden table of 0.8m height",
            "on an insulation support of 0.1 m height",
            "on an insulation support of 0.1m height",
        ),
    },
    "VOLTAGEDIPS": {
        "mode": "phrase",
        "tabletop": "on a wooden table of 0.8 m height",
        "floor": "on an insulation support of 0.1 m height",
        "phrases": (
            "on a 0.8/0.1m wooden table/insulation support",
            "on a 0.8/0.1 m wooden table/insulation support",
            "on a 0.8/0.1m wooden table/ insulation support",
            "on a wooden table of 0.8 m height",
            "on a wooden table of 0.8m height",
            "on an insulation support of 0.1 m height",
            "on an insulation support of 0.1m height",
        ),
    },
    "SURGE": {
        "mode": "phrase",
        "tabletop": "on a 0.8 m wooden table which is in turn placed on the GRP",
        "floor": "above 0.1 m insulation which is in turn placed on the GRP",
        "phrases": (
            "above 0.1m insulation which is in turn placed on the GRP",
            "above 0.1 m insulation which is in turn placed on the GRP",
            "on a 0.8m wooden table which is in turn placed on the GRP",
            "on a 0.8 m wooden table which is in turn placed on the GRP",
        ),
    },
    "EFT": {
        "mode": "phrase",
        "tabletop": "on a 0.8 m wooden table which is in turn placed on the GRP",
        "floor": "above 0.1 m insulation which is in turn placed on the GRP",
        "phrases": (
            "above 0.1m insulation which is in turn placed on the GRP",
            "above 0.1 m insulation which is in turn placed on the GRP",
            "on a 0.8m wooden table which is in turn placed on the GRP",
            "on a 0.8 m wooden table which is in turn placed on the GRP",
        ),
    },
    "PFMF": {
        "mode": "phrase",
        "tabletop": "on a 0.5m wooden table which was in turn placed on the non-GRP",
        "floor": "on a 0.1m insulation support which was in turn placed on the GRP",
        "phrases": (
            "on a 0.5m wooden table which was in turn placed on the non-GRP",
            "on a 0.5 m wooden table which was in turn placed on the non-GRP",
            "on a 0.1m insulation support which was in turn placed on the GRP",
            "on a 0.1 m insulation support which was in turn placed on the GRP",
        ),
    },
    # ESD is the variant case: the two configurations ground different coupling
    # planes and discharge on different ones, so there is no span to swap. Both
    # texts are the laboratory's own, verbatim; the opening sentence is not part of
    # them because it names the basic standard.
    "ESD": {
        "mode": "variant",
        "tabletop": (
            "The EUT was tested on a 0.8 m test bench which was placed on a GRP on the "
            "floor. The surface of the table is metallic, providing the required HCP. The "
            "EUT was placed on the HCP above a 0.5 mm insulating sheet. The HCP and the "
            "VCP were grounded to the GRP using two 470 kΩ resistors in series. The ESD "
            "generator return cable was bonded directly to the GRP.\n\n"
            "The user accessible points were identified on the EUT and marked as ESD test "
            "points. The test was performed by setting a lower level initially and "
            "gradually increasing to the specified higher level. In contact discharge 10 "
            "positive and 10 negative discharges were applied on each metallic "
            "(conductive) test points. In air discharge, 10 positive and 10 negative "
            "discharges were applied on each non- metallic (non-conductive) test "
            "points.\n\n"
            "Indirect discharge consisting of 10 positive and 10 negative discharges was "
            "applied on the VCP and HCP by rotating the EUT in 90-degree angle steps, "
            "exposing all faces of the EUT to the coupling planes. During the test, "
            "performance of EUT was monitored as per the criteria specified in standard."
        ),
        "floor": (
            "The EUT was tested on a 0.1m insulation support which was placed on a GRP "
            "(Ground Reference Plane) on the floor. The vertical coupling plane (VCP) were "
            "grounded to the GRP using two 470 kΩ resistors in series. The ESD generator "
            "return cable was bonded directly to the GRP.\n\n"
            "The user accessible points were identified on the EUT and marked as ESD test "
            "points. The test was performed by setting a lower level initially and "
            "gradually increasing to the specified higher level. In contact discharge 10 "
            "positive and 10 negative discharges were applied on each metallic "
            "(conductive) test points. In air discharge, 10 positive and 10 negative "
            "discharges were applied on each non- metallic (non-conductive) test "
            "points.\n\n"
            "Indirect discharge consisting of 10 positive and 10 negative discharges was "
            "applied on the VCP by rotating the EUT in 90-degree angle steps, exposing all "
            "faces of the EUT to the coupling plane. During the test, performance of EUT "
            "was monitored as per the criteria specified in standard."
        ),
    },
}

#: The per-port blocks a procedure carries. SURGE and EFT ship both and
#: _surge_filter_procedure drops the untested one; CRF shipped only Power Line, so
#: its Signal Line block lives here and is inserted when that port is tested.
PORT_BLOCKS = {
    "CRF": {
        "Signal Line": (
            "Signal Line:\n"
            "The EUT was tested in the CRF test site with a ground reference plane. The "
            "EUT was placed on a 0.1 m insulation support on the GRP. The RF power from "
            "the signal source was amplified and fed through the RF port of the EM clamp "
            "and superimposed on the signal lines of the EUT with reference to ground "
            "plane."
        ),
    },
}

#: Coupling Method follows the Test Port - a power line is driven through a CDN, a
#: signal line through an EM clamp. Applied as a DEFAULT: the field stays editable,
#: so a test that used something else still says so.
COUPLING_BY_PORT = {
    "CRF": {
        "Power Line": "CDN",
        "Signal Line": "EM Clamp",
    },
}


def support_rule(code):
    """The rule in force, override included. SUPPORT_RULES is the built-in default."""
    return effective_rule(code)


#: Every phrase here starts "on a"/"on an"/"above", and a stored draft may carry a different
#: article: the mapper this replaces swapped only the BARE phrase and left whatever article
#: the template had, so real drafts hold "on a insulation support at 0.1m height" - "a"
#: before a vowel - and RE's older ones hold no article at all. Matching all three renderings
#: is what makes the rule idempotent on text saved before it existed.
_ARTICLES = ("on a ", "on an ", "on the ", "on ")


def _expanded_phrases(rule):
    """The rule's phrases plus their article variants, longest first.

    Longest first matters: the combined placeholder ("on a wooden table/insulation support
    at 0.8/0.1m height") has to be consumed before the shorter single wordings inside it.
    """
    out = []
    for phrase in rule.get("phrases") or ():
        out.append(phrase)
        for art in _ARTICLES:
            if phrase.startswith(art):
                rest = phrase[len(art):]
                for other in _ARTICLES:
                    alt = other + rest
                    if alt != phrase:
                        out.append(alt)
                break
    seen, uniq = set(), []
    for p in sorted(out, key=len, reverse=True):
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def apply_support(code, text, cfg, rules=None):
    """Rewrite `text`'s EUT-support wording for this EUT Configuration.

    Idempotent and reversible on both shapes, so it is safe to run on the prefill,
    on every change of the dropdown, and again at generation - which is exactly
    where it runs, because a draft saved before a rule existed has to be corrected
    too. Returns the text unchanged when the datasheet has no rule.
    """
    txt = text or ""
    # effective_rule() lays any stored override over the built-in rule, so what an admin
    # saved on the config page is what the form rewrites and the document prints. Passing
    # `rules` explicitly bypasses the store, which is what the tests and the admin page's
    # own preview want.
    rule = (rules.get((code or "").upper()) if rules is not None
            else effective_rule(code))
    if not txt or not rule:
        return txt
    want = rule.get(config_side(cfg)) or ""
    if not want:
        return txt
    if rule.get("mode") == "variant":
        head, sep, _rest = txt.partition("\n\n")
        # An opening sentence naming the standard is kept; a text that is only the
        # body (no blank line) is replaced whole.
        if sep and head.strip():
            return head + "\n\n" + want
        return want
    for phrase in _expanded_phrases(rule):
        if phrase != want:
            txt = txt.replace(phrase, want)
    return txt


def coupling_default(code, port):
    """'CDN' for a power line, 'EM Clamp' for a signal line, '' when not ours."""
    return (COUPLING_BY_PORT.get((code or "").upper()) or {}).get((port or "").strip(), "")


def port_block(code, port):
    return (PORT_BLOCKS.get((code or "").upper()) or {}).get((port or "").strip(), "")


def ensure_port_block(code, text, port):
    """Add the block for `port` if the procedure does not carry it yet.

    CRF ships only its Power Line paragraph, so a signal-line test printed a
    procedure that described the wrong port. The block is appended rather than
    inserted anywhere clever: the filter that keeps the tested ports reads blocks
    by their heading, not by position.
    """
    txt = text or ""
    block = port_block(code, port)
    if not block:
        return txt
    head = block.split("\n", 1)[0].rstrip(":").strip().lower()
    for para in txt.split("\n\n"):
        if para.strip().lower().startswith(head):
            return txt
    return (txt.rstrip() + "\n\n" + block) if txt.strip() else block


def rules_for_ui(codes=None):
    """The table the form and the admin page render, as plain data.

    One shape for both, so the browser's rewriting and the admin page's preview
    cannot drift from what the document is built from.
    """
    out = {}
    wanted = list(codes) if codes else sorted(
        set(SUPPORT_RULES) | set(stored_overrides()))
    for code in wanted:
        rule = effective_rule(code)
        if not rule:
            continue
        out[code] = {
            "mode": rule.get("mode"),
            "tabletop": rule.get("tabletop", ""),
            "floor": rule.get("floor", ""),
            # expanded, so the browser corrects the same older renderings the server does
            "phrases": _expanded_phrases(rule),
        }
    return out


# ---------------------------------------------------------------------------------------
# The admin store: an override per datasheet, on top of the table above.
#
# The built-in rules stay the default and the fallback. A row here holds only what an
# admin changed - the two wordings, the procedure text, or both - so a datasheet nobody
# has touched keeps working from code, and a stored row that turns out wrong is undone by
# deleting it rather than by editing this file.
#
# Every read is best-effort: the datasheet forms and the generator must not stop working
# because a config table is missing or a query failed, so a failure falls back to the
# built-in rule and says so in the log.
# ---------------------------------------------------------------------------------------
#: Cached, because every form load and every generation reads it. The TTL matters as much
#: as the explicit invalidation: a save clears the cache in the worker that served it, and
#: any other worker picks the change up within the TTL. Same arrangement, and the same 5
#: minutes, as fixed_store.
_OVERRIDE_CACHE = {"at": 0.0, "rules": None}
_OVERRIDE_TTL_S = 300


def invalidate_override_cache():
    """Drop the cached overrides. Called after an admin saves or resets."""
    _OVERRIDE_CACHE["rules"] = None
    _OVERRIDE_CACHE["at"] = 0.0


def stored_overrides():
    """{CODE: {tabletop, floor, procedure}} from the database, or {} when unavailable."""
    import time
    now = time.time()
    if (_OVERRIDE_CACHE["rules"] is not None
            and (now - _OVERRIDE_CACHE["at"]) < _OVERRIDE_TTL_S):
        return _OVERRIDE_CACHE["rules"]
    out = {}
    try:
        from .procedure_store import all_overrides
        out = all_overrides()
    except Exception:  # noqa: BLE001 - a missing table must not break a datasheet
        out = {}
    _OVERRIDE_CACHE["rules"] = out
    _OVERRIDE_CACHE["at"] = now
    return out


def effective_rule(code):
    """The rule in force for this datasheet: the built-in one, with any stored wording on
    top. Returns None when the datasheet has no rule at all."""
    code = (code or "").upper()
    base = SUPPORT_RULES.get(code)
    over = (stored_overrides().get(code) or {})
    if not base:
        # A datasheet with no built-in rule can still be given one from the admin page,
        # as long as both wordings are supplied - otherwise there is nothing to swap.
        if over.get("tabletop") and over.get("floor"):
            return {"mode": over.get("mode") or "phrase",
                    "tabletop": over["tabletop"], "floor": over["floor"],
                    "phrases": tuple(over.get("phrases") or
                                     (over["tabletop"], over["floor"]))}
        return None
    if not over:
        return base
    rule = dict(base)
    for key in ("tabletop", "floor", "mode"):
        if over.get(key):
            rule[key] = over[key]
    # The stored wordings join the phrase list, so switching the toggle rewrites a text
    # that already carries one of them.
    phrases = list(base.get("phrases") or ())
    for key in ("tabletop", "floor"):
        val = over.get(key)
        if val and val not in phrases:
            phrases.append(val)
    rule["phrases"] = tuple(phrases)
    return rule


def stored_procedure(code):
    """The procedure text an admin saved for this datasheet, or '' to use the schema's."""
    return (stored_overrides().get((code or "").upper()) or {}).get("procedure") or ""


#: The port a procedure block belongs to, by the start of its first line. SURGE puts the
#: heading on a line of its own; EFT opens the paragraph with it ("Power Line: The power
#: supply ..."), so both are matched on the first line rather than the whole block. Kept
#: here beside the blocks themselves so the admin page's preview, the form and
#: _surge_filter_procedure all agree on what counts as a port heading.
PORT_HEADINGS = (
    ("Power Line", ("power line", "power lines")),
    ("Signal Line", ("signal line", "signal lines")),
)


def block_port(block):
    """'Power Line' / 'Signal Line' for a procedure paragraph, or '' for the preamble."""
    head = (block or "").strip().split("\n")[0].strip().lower().rstrip(":")
    for name, prefixes in PORT_HEADINGS:
        if head.startswith(prefixes):
            return name
    return ""


def procedure_port_sections(text):
    """The ports this procedure text carries a block for, in the order they appear.

    What the admin page offers as filters: a datasheet whose procedure has no port heading
    (Harmonic, Flicker) gets no port filter, and one that has both (Surge, EFT) gets both.
    """
    out = []
    for block in re.split(r"\n\s*\n", text or ""):
        port = block_port(block)
        if port and port not in out:
            out.append(port)
    return out
