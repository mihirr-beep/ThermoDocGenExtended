# -*- coding: utf-8 -*-
"""Structural map of the IEC-FRM-516 EMI EMC Test Report.

Single source of truth for "which Heading-1 section belongs to which datasheet
test code", plus the fixed front-matter/static sections that are always kept.

The report's per-test sections mirror the per-test datasheet documents (they are
generated from the same IEC-FRM-5xx source forms), which is what lets the
builder reuse ``datasheet_gen``'s schemas and context builders for the heavy
lifting - see ``mapping.py``.
"""
import os

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "word_templates",
                             "IEC-FRM-516_REPORT.docx")

FORM_NO = "IEC-FRM-516"

# Heading-1 title (uppercased, whitespace-collapsed) -> datasheet registry code.
# Order matters: it is the order the sections appear in the document, and the
# order tests are reported in.
SECTION_TO_CODE = [
    ("CONDUCTED EMISSION TEST",                       "CE"),
    ("RADIATED EMISSION TEST",                        "RE"),
    ("HARMONIC CURRENT EMISSION TEST",                "HARMONIC"),
    ("VOLTAGE FLUCTUATION AND FLICKER EMISSION TEST", "VOLTAGEFLICKER"),
    ("ELECTROSTATIC DISCHARGE IMMUNITY TEST",         "ESD"),
    ("RADIATED SUSCEPTIBILITY TEST",                  "RS_RI"),
    ("ELECTRICAL FAST TRANSIENT / BURST IMMUNITY TEST", "EFT"),
    ("SURGE IMMUNITY TEST",                           "SURGE"),
    ("CONDUCTED RADIO FREQUENCY IMMUNITY TEST",       "CRF"),
    ("POWER FREQUENCY MAGNETIC FIELD IMMUNITY TEST",  "PFMF"),
    ("VOLTAGE DIPS & INTERRUPTIONS IMMUNITY TEST",    "VOLTAGEDIPS"),
]

CODE_BY_SECTION = dict(SECTION_TO_CODE)
SECTION_BY_CODE = {c: s for s, c in SECTION_TO_CODE}
REPORT_CODE_ORDER = [c for _s, c in SECTION_TO_CODE]

# Heading-1 sections that are never test-specific and are always retained.
STATIC_SECTIONS = (
    "TEST REPORT SUMMARY",
    "EUT INFORMATION",
    "IMMUNITY CRITERIA AND DECISION RULE",
)

# Per-test Heading-2 subsections, as they appear in the document.
SUB_SPEC = "TEST SPECIFICATION"
SUB_DEVIATION = "DEVIATION FROM THE STANDARD"
SUB_LIMITS = "TEST LIMITS"
SUB_PROCEDURE = "TEST PROCEDURE"
SUB_MEASUREMENT = "MEASUREMENT DATA"
SUB_OBSERVATION = "TEST OBSERVATION"
SUB_PICTURES = "TEST SETUP PICTURES"
SUB_EQUIPMENT = "TEST EQUIPMENT USED"
SUB_SOFTWARE = "SOFTWARE USED"
SUB_RESULT = "RESULT"

# The emission tests report measured data + limits; the immunity tests report an
# observation grid instead. (Matches the datasheet schemas exactly.)
EMISSION_CODES = ("CE", "RE", "HARMONIC", "VOLTAGEFLICKER")
IMMUNITY_CODES = ("ESD", "RS_RI", "EFT", "SURGE", "CRF", "PFMF", "VOLTAGEDIPS")


# --------------------------------------------------------------------------
# 1.1 TEST METHOD summary table
# --------------------------------------------------------------------------
# Row label in the report's Test Method table -> datasheet code. The labels in
# the shipped template are truncated in places ("Conducted Emissio"), so they
# are matched on a normalised prefix rather than exactly (see mapping.py).
TEST_METHOD_ROWS = [
    ("Conducted Emission",                                        "CE"),
    ("Radiated Emission Test",                                    "RE"),
    ("Harmonic Current Emission",                                 "HARMONIC"),
    ("Voltage Changes, Voltage Fluctuations and Flicker Emission", "VOLTAGEFLICKER"),
    ("Electrostatic Discharge Immunity",                          "ESD"),
    ("Radiated Susceptibility",                                   "RS_RI"),
    ("Electrical Fast Transient/Burst Immunity",                  "EFT"),
    ("Surge Immunity",                                            "SURGE"),
    ("Conducted RF Immunity",                                     "CRF"),
    ("Power Frequency Magnetic Field Immunity",                   "PFMF"),
    ("Voltage Dips, Short Interruptions",                         "VOLTAGEDIPS"),
]

# Applicable Port/Enclosure column of 1.1 (fixed per test by the standard).
TEST_METHOD_PORT = {
    "CE": "Power Port", "RE": "Enclosure", "HARMONIC": "Power Port",
    "VOLTAGEFLICKER": "Power Port", "ESD": "Enclosure", "RS_RI": "Enclosure",
    "EFT": "Power Port", "SURGE": "Power Port", "CRF": "Power Port",
    "PFMF": "Enclosure", "VOLTAGEDIPS": "Power Port",
}

# --------------------------------------------------------------------------
# 1.4 MEASUREMENT UNCERTAINTY table
# --------------------------------------------------------------------------
# Only the four emission tests carry a measurement uncertainty in the report.
# The values come from datasheet_fixed_values (admin-editable); these are the
# row labels used in the document.
UNCERTAINTY_ROWS = [
    ("Radiated Emission", "RE"),
    ("Conducted Emission", "CE"),
    ("Harmonic Current Emission", "HARMONIC"),
    ("Voltage Fluctuation and Flicker Emission", "VOLTAGEFLICKER"),
]


def canonical(title):
    """Normalise a Heading text for lookup in SECTION_TO_CODE/STATIC_SECTIONS."""
    import re
    return re.sub(r"\s+", " ", (title or "")).strip().upper()


def code_for_section(title):
    return CODE_BY_SECTION.get(canonical(title))


def is_static_section(title):
    return canonical(title) in STATIC_SECTIONS
