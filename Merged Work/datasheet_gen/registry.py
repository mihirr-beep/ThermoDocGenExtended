"""The 11 EMC tests, keyed by the code used in planner_entries.test_name (uppercased).

CE is handled by the bespoke CE module; the other 10 use the generic schema engine.
"""
import json
import os

# Source .docx templates live in the sibling `Reference/` folder (one level above
# this repo). Override with the DATASHEET_SRC_DIR env var if they live elsewhere.
SRC_DIR = os.environ.get(
    "DATASHEET_SRC_DIR",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "Reference")),
)

# code (== planner_entries.test_name upper) -> (form_no, display name, source docx)
REGISTRY = {
    "CE":             ("IEC-FRM-504", "Conducted Emission",            "IEC-FRM-504  CE Test Data sheet.docx"),
    "VOLTAGEDIPS":    ("IEC-FRM-505", "Voltage Dips & Interruptions",  "IEC-FRM-505 VDIPS Test Data Sheet.docx"),
    "VOLTAGEFLICKER": ("IEC-FRM-506", "Flicker",                       "IEC-FRM-506 Flicker Test Data Sheet.docx"),
    "HARMONIC":       ("IEC-FRM-507", "Harmonics",                     "IEC-FRM-507 Harmonics Test Data Sheet.docx"),
    "EFT":            ("IEC-FRM-508", "Electrical Fast Transient",     "IEC-FRM-508  EFT Test Data Sheet.docx"),
    "ESD":            ("IEC-FRM-509", "Electrostatic Discharge",       "IEC-FRM-509 ESD Test Data sheet.docx"),
    "SURGE":          ("IEC-FRM-510", "Surge",                         "IEC-FRM-510 Surge Data Sheet.docx"),
    "RE":             ("IEC-FRM-511", "Radiated Emission",             "IEC-FRM-511 RE Test Data Sheet.docx"),
    "RS_RI":          ("IEC-FRM-512", "Radiated Susceptibility",       "IEC-FRM-512 RS Test Data sheet.docx"),
    "CRF":            ("IEC-FRM-513", "Conducted RF Immunity",         "IEC-FRM-513 Conducted RF Immunity Test Data Sheet.docx"),
    "PFMF":           ("IEC-FRM-514", "Power-Frequency Magnetic Field", "IEC-FRM-514 PFMF Test Data Sheet.docx"),
}

# Codes handled by the generic engine (everything except the bespoke CE).
GENERIC_CODES = [c for c in REGISTRY if c != "CE"]


def normalize_code(test_name):
    return (test_name or "").strip().upper()


def source_path(code):
    return os.path.join(SRC_DIR, REGISTRY[code][2])


def load_schema(code):
    with open(os.path.join(os.path.dirname(__file__), "schemas", f"{code}.json"), encoding="utf-8") as fh:
        return json.load(fh)

