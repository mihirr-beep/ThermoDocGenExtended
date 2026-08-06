# -*- coding: utf-8 -*-
"""Admin-editable store for datasheet CONSTANT / FIXED values.

Everything that used to be hardcoded in the schemas / service / JS — Measurement
Uncertainty, Functional-Check SOP reference, fixed Software Used, Test-Limit
numbers, and the fixed Test-Specification parameters — now lives in the DB so an
admin can change it later without a code change. Two tables:

  * ``datasheet_fixed_values``  one row per datasheet (test_code) whose
    ``values_json`` holds that datasheet's fixed values (see SEED_FIXED_VALUES).
  * ``basic_standard_map``      the Product-Standard -> Basic-Standard mapping,
    one row per (test_code, product token). Adding a standard = adding a row.

The *selection logic* (which limit rows apply for a class/standard/frequency,
how many basic standards to join) stays in code; only the *values* come from
here. Kept self-contained (own idempotent table creation + seed), mirroring
records.py — no edits to models.py.
"""
import json
import re

from models import db, get_ist_now

try:
    from sqlalchemy.dialects.mysql import LONGTEXT
    _JSON_COL = LONGTEXT
except Exception:  # pragma: no cover - non-MySQL fallback
    _JSON_COL = db.Text


# ==========================================================================
# Models
# ==========================================================================
class DatasheetFixedValue(db.Model):
    __tablename__ = "datasheet_fixed_values"

    id = db.Column(db.Integer, primary_key=True)
    test_code = db.Column(db.String(40), unique=True, index=True, nullable=False)
    name = db.Column(db.String(120))
    values_json = db.Column(_JSON_COL)
    updated_at = db.Column(db.DateTime, default=get_ist_now, onupdate=get_ist_now)
    updated_by = db.Column(db.Integer, nullable=True)

    def values(self):
        try:
            return json.loads(self.values_json or "{}")
        except Exception:
            return {}


class BasicStandardMap(db.Model):
    __tablename__ = "basic_standard_map"

    id = db.Column(db.Integer, primary_key=True)
    # NULL test_code == the shared emission mapping (used by CE + RE). A specific
    # test_code (HARMONIC / VOLTAGEFLICKER) overrides it for that datasheet.
    test_code = db.Column(db.String(40), nullable=True, index=True)
    product_token = db.Column(db.String(120), nullable=False, default="")  # normalized substring
    product_label = db.Column(db.String(200))                              # human-readable
    basic_standard = db.Column(db.String(300), nullable=False)
    is_default = db.Column(db.Boolean, default=False)   # the "else" fallback (single-result tests)
    sort_order = db.Column(db.Integer, default=0)
    active = db.Column(db.Boolean, default=True)
    updated_at = db.Column(db.DateTime, default=get_ist_now, onupdate=get_ist_now)

    def to_dict(self):
        return {
            "id": self.id, "test_code": self.test_code or "",
            "product_token": self.product_token, "product_label": self.product_label or "",
            "basic_standard": self.basic_standard, "is_default": bool(self.is_default),
            "sort_order": self.sort_order or 0, "active": bool(self.active),
        }


# ==========================================================================
# Seed data (the exact values that were hardcoded before this change)
# ==========================================================================
SEED_FIXED_VALUES = {
    "CE": {
        "name": "Conducted Emission",
        "measurement_uncertainty": "± 3.368 dB",
        "sop_reference": "IEC-SOP-505",
        "software": [{"c0": "PMM Suite", "c1": "2.54"}],
        "test_limits": {
            "by_class": {
                "A": {"limit_qp_015_050": "79", "limit_avg_015_050": "66",
                      "limit_qp_050_5": "73", "limit_avg_050_5": "60",
                      "limit_qp_5_30": "73", "limit_avg_5_30": "60"},
                "B": {"limit_qp_015_050": "66-56", "limit_avg_015_050": "56-46",
                      "limit_qp_050_5": "56", "limit_avg_050_5": "46",
                      "limit_qp_5_30": "60", "limit_avg_5_30": "50"},
            }
        },
    },
    "RE": {
        "name": "Radiated Emission",
        "measurement_uncertainty": "± 3.705 dB",
        "sop_reference": "IEC-SOP-507",
        "software": [{"c0": "TDK Emission Lab", "c1": "14.43"}],
        "product_standard_display": {
            "iec61326": "IEC 61326-1:2020",
            "en61326": "EN 61326-1:2021",
            "ices": "ICES-001:Issue 5:2020",
            "subpart15b": "FCC Part 15 Subpart B:2024",
            "part15": "FCC Part 15 Subpart B:2024",
            "cfr": "FCC Part 15 Subpart B:2024",
            "fcc": "FCC Part 15 Subpart B:2024",
            "cispr11": "CISPR 11:2016",
            "en55011": "EN 55011:2016+A2:2021"
        },
        "spec_defaults": {
            "resolution_bandwidth_col_1": "120k", "resolution_bandwidth_col_2": "1M",
            "video_bandwidth_col_1": "1M", "video_bandwidth_col_2": "3M",
            "step_size_col_1": "40k", "step_size_col_2": "400k",
            "turn_table_rotation_step_col_1": "15°", "turn_table_rotation_step_col_2": "22.5°",
            "antenna_height_variation_step_for_pre_scan_mea_2": "1",
            "antenna_height_variation_step_for_pre_scan_mea_3": "1",
            "antenna_height_variation_for_final_measurement_2": "1-4",
            "antenna_height_variation_for_final_measurement_3": "1-2",
            "pre_scan_measurement_time_col_1": "20", "pre_scan_measurement_time_col_2": "20",
            "final_scan_measurement_time_col_1": "1", "final_scan_measurement_time_col_2": "1",
            "attenuation_col_1": "Auto", "attenuation_col_2": "Auto",
            "test_distance": "3",
            "polarization_col_1": "Horizontal and Vertical", "polarization_col_2": "Horizontal and Vertical",
            "detector_col_1": "Peak and Quasi-peak", "detector_col_2": "Peak and Average",
        },
        "test_limits": {
            "qp_30m_1g": {
                "CISPR": {"A": [["30 to 230", "50"], ["230 to 1000", "57"]],
                          "B": [["30 to 230", "40"], ["230 to 1000", "47"]]},
                "FCC": {"A": [["30 to 88", "49.54"], ["88 to 216", "53.98"],
                              ["216 to 960", "56.90"], ["960 to 1000", "59.54"]],
                        "B": [["30 to 88", "39.6"], ["88 to 216", "43.52"],
                              ["216 to 960", "46.02"], ["960 to 1000", "54"]]},
            },
            "pa_1g_6g": {
                "FCC": {"A": [["1 to 3 GHz", "79.54", "59.54"], ["3 to 6 GHz", "79.54", "59.54"]],
                        "B": [["1000 to 6000 MHz", "74", "54"]]},
            },
        },
    },
    "HARMONIC": {
        "name": "Harmonics",
        "measurement_uncertainty": "± 5.94%",
        "sop_reference": "IEC-SOP-509",
        "software": [{"c0": "Net.Control", "c1": "3.2.6"}],
        "spec_defaults": {
            "frequency_range": "50 Hz to 2 kHz",
            "maximum_harmonics": "40th Harmonics",
            "measurement_time": "10 minutes",
            "test_port": "Power Line",
        },
        "test_limits": {
            "odd": [["3", "2.30"], ["5", "1.14"], ["7", "0.77"], ["9", "0.40"],
                    ["11", "0.33"], ["13", "0.21"], ["15 ≤ n ≤ 39", "0.15 * (15/n)"]],
            "even": [["2", "1.08"], ["4", "0.43"], ["6", "0.30"], ["8 ≤ n ≤ 40", "0.23 * (8/n)"]],
        },
    },
    "VOLTAGEFLICKER": {
        "name": "Flicker",
        "measurement_uncertainty": "± 3.69%",
        "sop_reference": "IEC-SOP-510",
        "software": [{"c0": "Net.Control", "c1": "3.2.6"}],
        "spec_defaults": {
            "short_term_flicker_measurement_time": "10 minutes",
            "long_term_flicker_measurement_time": "120 minutes",
        },
        "test_limits": {
            "limits_rows": [["Pst", "1"], ["Plt", "0.65"], ["Tmax (s)", "0.5"],
                            ["dmax (%)", "4"], ["dc (%)", "3.3"]],
            "meas_rows": [["Pst", "", "1"], ["Plt", "", "0.65"], ["Tmax (s)", "", "0.5"],
                          ["dmax (%)", "", "4"], ["dc (%)", "", "3.3"]],
            "fc_rows": [["Line 1:", "", "", "", "", ""],
                        ["Limits:", "0.65", "1", "3.3", "4", "0.5"],
                        ["Results:", "", "", "", "", ""]],
        },
    },
}

# Product-Standard -> Basic-Standard rows.
#   test_code None  -> shared emission mapping (CE + RE): map each product standard
#                      part and join the distinct basics (no default).
#   test_code set   -> single-result mapping (first token match, else is_default).
SEED_BASIC_MAP = [
    # --- Emission (CE, RE): test_code = None ---
    (None, "iec61326", "IEC 61326-1", "CISPR 11:2015+A1:2016+A2:2019", False, 10),
    (None, "en61326", "EN 61326-1", "EN 55011:2016+A2:2021", False, 20),
    (None, "ices", "ICES-001", "CISPR 11:2015+A1:2016+A2:2019", False, 30),
    (None, "part15", "47 CFR Part 15 Subpart B", "ANSI C63.4:2024", False, 40),
    (None, "cfr", "CFR", "ANSI C63.4:2024", False, 50),
    (None, "fcc", "FCC", "ANSI C63.4:2024", False, 60),
    (None, "en55011", "EN 55011", "EN 55011:2016+A2:2021", False, 70),
    (None, "cispr11", "CISPR 11", "CISPR 11:2015+A1:2016+A2:2019", False, 80),
    (None, "c634", "ANSI C63.4", "ANSI C63.4:2024", False, 90),
    # --- HARMONIC: en61326/en55011 -> EN, else IEC (default) ---
    ("HARMONIC", "en61326", "EN 61326-1", "EN 61000-3-2:2019+A1:2021", False, 10),
    ("HARMONIC", "en55011", "EN 55011", "EN 61000-3-2:2019+A1:2021", False, 20),
    ("HARMONIC", "", "(default)", "IEC 61000-3-2:2018+A1:2020", True, 100),
    # --- VOLTAGEFLICKER: en61326/en60601 -> EN, else IEC (default) ---
    ("VOLTAGEFLICKER", "en61326", "EN 61326-1", "EN 61000-3-3:2013+A2:2021", False, 10),
    ("VOLTAGEFLICKER", "en60601", "EN 60601", "EN 61000-3-3:2013+A2:2021", False, 20),
    ("VOLTAGEFLICKER", "", "(default)", "IEC 61000-3-3:2013+A2:2021", True, 100),
]


# ==========================================================================
# Ensure + seed
# ==========================================================================
def ensure_config_tables(app):
    """Create the two config tables (if absent) and seed them (if empty).
    Idempotent, best-effort; never breaks boot."""
    try:
        with app.app_context():
            try:
                db.metadata.create_all(
                    bind=db.engine,
                    tables=[DatasheetFixedValue.__table__, BasicStandardMap.__table__],
                )
            except Exception as exc:  # noqa: BLE001
                app.logger.error("datasheet_gen: could not create config tables: %s", exc)
                return
            _seed_if_empty(app)
    except Exception as exc:  # noqa: BLE001
        try:
            app.logger.error("datasheet_gen: ensure_config_tables failed: %s", exc)
        except Exception:
            pass


def _seed_if_empty(app):
    try:
        if DatasheetFixedValue.query.first() is None:
            for code, vals in SEED_FIXED_VALUES.items():
                db.session.add(DatasheetFixedValue(
                    test_code=code, name=vals.get("name", code),
                    values_json=json.dumps(vals, ensure_ascii=False),
                ))
            db.session.commit()
            app.logger.info("datasheet_gen: seeded datasheet_fixed_values (%d)", len(SEED_FIXED_VALUES))
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.error("datasheet_gen: seed fixed values failed: %s", exc)
    try:
        if BasicStandardMap.query.first() is None:
            for tc, tok, label, basic, is_def, order in SEED_BASIC_MAP:
                db.session.add(BasicStandardMap(
                    test_code=tc, product_token=tok, product_label=label,
                    basic_standard=basic, is_default=is_def, sort_order=order, active=True,
                ))
            db.session.commit()
            app.logger.info("datasheet_gen: seeded basic_standard_map (%d)", len(SEED_BASIC_MAP))
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        app.logger.error("datasheet_gen: seed basic map failed: %s", exc)


# ==========================================================================
# Read-through cache
# ==========================================================================
# These two tables are tiny (4 and 15 rows) and admin-edited perhaps monthly,
# but they are read repeatedly while building ONE datasheet form - measured at
# 3x datasheet_fixed_values + 4x basic_standard_map per page load. Against a
# remote database that is 7 needless round trips on every form open, so they
# are cached in-process and invalidated explicitly when an admin saves.
_CACHE = {}
_CACHE_TTL_S = 300


def _cached(key, loader):
    import time
    hit = _CACHE.get(key)
    if hit is not None and (time.time() - hit[0]) < _CACHE_TTL_S:
        return hit[1]
    value = loader()
    _CACHE[key] = (time.time(), value)
    return value


def invalidate_cache():
    """Drop the cached fixed values / standard map. Call after an admin edit."""
    _CACHE.clear()


# ==========================================================================
# Accessors (read side — used by service / generic_service / routes)
# ==========================================================================
def get_fixed_values(test_code):
    """Return the fixed-values dict for a datasheet code (DB first, seed fallback)."""
    code = (test_code or "").upper()

    def _load():
        try:
            row = DatasheetFixedValue.query.filter_by(test_code=code).first()
            if row is not None:
                return row.values()
        except Exception:
            pass
        return dict(SEED_FIXED_VALUES.get(code, {}))

    # copy on the way out: callers mutate the dict they get back
    return dict(_cached(("fixed", code), _load))


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _map_rows(test_code):
    """Active mapping rows for a datasheet: its own rows if any, else the shared
    (NULL test_code) emission rows. Falls back to the in-code seed if the DB is
    unavailable. Cached - see the note above _CACHE."""
    return _cached(("map", (test_code or "").upper() or None),
                   lambda: _map_rows_uncached(test_code))


def _map_rows_uncached(test_code):
    code = (test_code or "").upper() or None
    try:
        rows = []
        if code:
            rows = (BasicStandardMap.query
                    .filter_by(test_code=code, active=True)
                    .order_by(BasicStandardMap.sort_order.asc()).all())
        if not rows:
            rows = (BasicStandardMap.query
                    .filter(BasicStandardMap.test_code.is_(None), BasicStandardMap.active.is_(True))
                    .order_by(BasicStandardMap.sort_order.asc()).all())
        if rows:
            return [r.to_dict() for r in rows]
    except Exception:
        pass
    # seed fallback
    want = code if any(r[0] == code for r in SEED_BASIC_MAP) else None
    return [{"product_token": t, "product_label": l, "basic_standard": b,
             "is_default": d, "sort_order": o}
            for (tc, t, l, b, d, o) in SEED_BASIC_MAP if tc == want]


def basic_standard(product_standard, test_code=None):
    """Derive the Basic Standard(s) for a Product Standard from the mapping table.

    Single-result tests (those with a default row, e.g. HARMONIC/FLICKER) return
    the first token match, else the default. Emission tests (no default row, CE/RE)
    map EACH ';'-separated product standard and join the DISTINCT basics."""
    rows = sorted(_map_rows(test_code), key=lambda r: r.get("sort_order", 0))
    default_row = next((r for r in rows if r.get("is_default")), None)
    matchers = [r for r in rows if not r.get("is_default") and r.get("product_token")]

    if default_row is not None:                      # single-result (HARMONIC / FLICKER)
        key = _norm(product_standard)
        for r in matchers:
            if r["product_token"] in key:
                return r["basic_standard"]
        return default_row["basic_standard"]

    out = []                                         # emission (CE / RE): multi-map + join
    for part in re.split(r"[;\n]+", product_standard or ""):
        key = _norm(part)
        if not key:
            continue
        for r in matchers:
            if r["product_token"] in key:
                if r["basic_standard"] not in out:
                    out.append(r["basic_standard"])
                break
    return "; ".join(out)
