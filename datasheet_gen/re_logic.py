"""RE (Radiated Emission, IEC-FRM-511) derivations.

The Test Limits printed on an RE datasheet depend on the *combination* of the
standard family (CISPR & ICES vs FCC) and the EUT class (A/B). The family is
derived from the Product Standard (the same tokens that drive the Basic
Standard). Limit values are the SME-supplied figures (see RE-Working.xlsx +
the limit screenshots).

Exposed:
  standard_family(product_standard)      -> 'CISPR' | 'FCC' | ''
  limit_rows(product_standard, cls, ...) -> (qp_rows, pa_rows) as generic table rows
"""
import re as _re

from .service import _s

# --------------------------------------------------------------------------
# Standard family (which limit table applies)
# --------------------------------------------------------------------------
_FCC_TOKENS = ("part15", "cfr", "fcc", "c634", "ansi")
_CISPR_TOKENS = ("cispr", "en55011", "iec61326", "en61326", "ices")


def families(product_standard):
    """The SET of standard families named in the product standard: any of
    {'CISPR', 'FCC'}. A multi-standard EUT (e.g. IEC 61326-1 + FCC Part 15)
    returns both, so both limit tables get printed."""
    fams = set()
    for part in _re.split(r"[;\n/&,]+", _s(product_standard)):
        key = _re.sub(r"[^a-z0-9]", "", part.lower())
        if not key:
            continue
        if any(t in key for t in _FCC_TOKENS):
            fams.add("FCC")
        if any(t in key for t in _CISPR_TOKENS):
            fams.add("CISPR")
    return fams


def standard_family(product_standard):
    """'FCC' for 47 CFR Part 15 / ANSI C63.4; 'CISPR' for CISPR 11 / EN 55011 /
    IEC 61326 / EN 61326 / ICES-001. '' when unknown. Evaluated per ';'-joined
    product standard, first decisive match wins (FCC checked first)."""
    for part in _re.split(r"[;\n/&,]+", _s(product_standard)):
        key = _re.sub(r"[^a-z0-9]", "", part.lower())
        if not key:
            continue
        if any(t in key for t in _FCC_TOKENS):
            return "FCC"
        if any(t in key for t in _CISPR_TOKENS):
            return "CISPR"
    return ""


# --------------------------------------------------------------------------
# Limit data (dBµV/m @ 3 m). Source: SME limit screenshots (RE-Working.xlsx).
# --------------------------------------------------------------------------
# 30 MHz - 1 GHz, Quasi-peak. (family, class) -> [(band label, QP limit), ...]
_QP_30M_1G = {
    ("CISPR", "A"): [("30 to 230", "50"), ("230 to 1000", "57")],
    ("CISPR", "B"): [("30 to 230", "40"), ("230 to 1000", "47")],
    ("FCC", "A"): [("30 to 88", "49.54"), ("88 to 216", "53.98"),
                   ("216 to 960", "56.90"), ("960 to 1000", "59.54")],
    ("FCC", "B"): [("30 to 88", "39.6"), ("88 to 216", "43.52"),
                   ("216 to 960", "46.02"), ("960 to 1000", "54")],
}
# 1 GHz - 6 GHz, Peak & Average. (family, class) -> [(band, peak, avg), ...]
# CISPR & ICES has no 1-6 GHz radiated-emission limit in the SME sheet.
_PA_1G_6G = {
    ("FCC", "A"): [("1 to 3 GHz", "79.54", "59.54"), ("3 to 6 GHz", "79.54", "59.54")],
    ("FCC", "B"): [("1000 to 6000 MHz", "74", "54")],
}


def _norm_class(cls):
    """'Class A' / 'a' / 'A' -> 'A'. Anything else -> ''."""
    v = _s(cls).upper()
    if v.startswith("CLASS"):
        v = v[5:].strip()
    return v[:1] if v[:1] in ("A", "B") else ""


def limit_rows(product_standard=None, cls=None, family=None):
    """Return (qp_rows, pa_rows) for the standard family + class, as generic-table
    rows ({c0,c1} for QP; {c0,c1,c2} for Peak/Average). Unknown combination ->
    empty lists (the engineer fills the tables in manually)."""
    fam = (family or standard_family(product_standard) or "").upper()
    c = _norm_class(cls)
    qp = [{"c0": b, "c1": v} for (b, v) in _QP_30M_1G.get((fam, c), [])]
    pa = [{"c0": b, "c1": p, "c2": a} for (b, p, a) in _PA_1G_6G.get((fam, c), [])]
    return qp, pa
