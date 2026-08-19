# -*- coding: utf-8 -*-
"""Storage for the Test Procedure overrides an admin makes on the config page.

One row per datasheet, holding only what was changed: the two EUT-support wordings, the
procedure text, or any of them. procedures.effective_rule() lays a row over the built-in
rule, so a datasheet nobody has touched keeps working from code and an override that turns
out wrong is undone by deleting the row rather than by editing a module.

Deliberately a separate table from datasheet_fixed_values: the fixed values are per-field
constants that print inside a datasheet, while this is the wording of one field and the rule
that rewrites it. Sharing the row would have meant reaching into a JSON blob whose shape is
already load-bearing elsewhere.
"""
import json

from models import db, get_ist_now

try:                                    # a procedure is longer than TEXT on some rows
    from sqlalchemy.dialects.mysql import LONGTEXT
    _JSON_COL = LONGTEXT
except Exception:                       # noqa: BLE001 - sqlite and friends
    _JSON_COL = db.Text


class DatasheetProcedure(db.Model):
    __tablename__ = "datasheet_procedures"

    id = db.Column(db.Integer, primary_key=True)
    test_code = db.Column(db.String(40), unique=True, index=True, nullable=False)
    #: {"tabletop": str, "floor": str, "procedure": str, "mode": "phrase"|"variant"}
    #: Absent or empty keys mean "use the built-in rule for that part".
    values_json = db.Column(_JSON_COL)
    updated_at = db.Column(db.DateTime, default=get_ist_now, onupdate=get_ist_now)
    updated_by = db.Column(db.Integer, nullable=True)

    def values(self):
        try:
            return json.loads(self.values_json or "{}") or {}
        except Exception:  # noqa: BLE001 - a corrupt row must not break the form
            return {}


def ensure_procedure_table(app):
    """Create the table if it is absent. Idempotent, best-effort, never breaks boot."""
    try:
        with app.app_context():
            db.metadata.create_all(bind=db.engine, tables=[DatasheetProcedure.__table__])
    except Exception as exc:  # noqa: BLE001
        try:
            app.logger.error("datasheet_gen: could not create datasheet_procedures: %s", exc)
        except Exception:
            pass


def all_overrides():
    """{CODE: {...}} for every stored row. Raises nothing the caller must handle -
    procedures.stored_overrides() treats any failure as "no overrides"."""
    out = {}
    for row in DatasheetProcedure.query.all():
        vals = row.values()
        if vals:
            out[(row.test_code or "").upper()] = vals
    return out


def get_override(code):
    row = DatasheetProcedure.query.filter_by(test_code=(code or "").upper()).first()
    return (row.values() if row else {}), (row.updated_at if row else None)


def save_override(code, values, user_id=None):
    """Write the parts an admin filled in; drop the row when they cleared everything.

    Storing a blank is how "go back to the built-in wording" is expressed, so an empty
    payload deletes rather than saving a row of empty strings that would read as an
    override of "nothing".
    """
    code = (code or "").upper()
    clean = {k: (v or "").strip() for k, v in (values or {}).items()}
    clean = {k: v for k, v in clean.items() if v}
    row = DatasheetProcedure.query.filter_by(test_code=code).first()
    if not clean:
        if row is not None:
            db.session.delete(row)
            db.session.commit()
        return {}
    if row is None:
        row = DatasheetProcedure(test_code=code)
        db.session.add(row)
    row.values_json = json.dumps(clean, ensure_ascii=False)
    row.updated_by = user_id
    row.updated_at = get_ist_now()
    db.session.commit()
    return clean


def clear_override(code):
    row = DatasheetProcedure.query.filter_by(test_code=(code or "").upper()).first()
    if row is not None:
        db.session.delete(row)
        db.session.commit()
    return True


def updated_map():
    """{CODE: 'YYYY-MM-DD HH:MM'} for the landing page's cards."""
    out = {}
    try:
        for row in DatasheetProcedure.query.all():
            if row.updated_at:
                out[(row.test_code or "").upper()] = row.updated_at.strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        pass
    return out
