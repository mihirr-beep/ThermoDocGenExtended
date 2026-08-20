"""DOCX exporter for the IEC-FRM-503 EMI/EMC test plan template."""

from datetime import date, datetime
from io import BytesIO
import os

from docx import Document


TEMPLATE_FILENAME = "IEC-FRM-503 EMI EMC Test Plan.docx"


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d-%m-%Y")
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_text(item) for item in value if _text(item))
    if isinstance(value, dict):
        return "; ".join(f"{key}: {_text(item)}" for key, item in value.items() if _text(item))
    return str(value).strip()


def _set_cell(table, row_index, cell_index, value):
    if row_index >= len(table.rows):
        return
    row = table.rows[row_index]
    if cell_index >= len(row.cells):
        return
    row.cells[cell_index].text = _text(value)


def _first_value(data, *keys):
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _selected_test(data, *aliases):
    selected = data.get("selected_tests") or []
    if isinstance(selected, dict):
        selected = [key for key, value in selected.items() if value]
    selected_text = " ".join(_text(item).casefold() for item in selected)
    return any(alias.casefold() in selected_text for alias in aliases)


def _test_rows(data):
    """Return the fixed IEC-503 test matrix rows in template order."""
    tests = [
        ("Conducted Emission", ("CE", "Conducted Emission"), "150 kHz - 30 MHz"),
        ("Radiated Emission", ("RE", "Radiated Emission"), "30 MHz - 1 GHz"),
        ("Harmonic Current Emission", ("Harmonic",), "As per the standard"),
        ("Voltage Fluctuations and Flicker", ("Flicker", "VoltageFlicker"), "As per the standard"),
        ("Electrostatic Discharge Immunity", ("ESD",), "Contact / Air"),
        ("Radiated Susceptibility/Immunity", ("RS_RI", "RS", "Radiated Susceptibility"), "80 MHz - 6 GHz"),
        ("Radiated Susceptibility/Immunity (Interim)", ("RS_RI_Interim", "RS_Interim"), "As per the standard"),
        ("Electrical Fast Transient/Burst - Power Lines", ("EFT",), "Power lines"),
        ("Electrical Fast Transient/Burst - Signal Lines", ("EFT",), "Signal lines"),
        ("Surge Immunity - Power Lines", ("Surge",), "Power lines"),
        ("Surge Immunity - Signal Lines", ("Surge",), "Signal lines"),
        ("Conducted RF Disturbance Immunity - Power Lines", ("CRF",), "Power lines"),
        ("Conducted RF Disturbance Immunity - Signal Lines", ("CRF",), "Signal lines"),
        ("Power Frequency Magnetic Field Immunity", ("PFMF", "Power Frequency"), "50 / 60 Hz"),
        ("Voltage Dips and Interruptions Immunity", ("VoltageDips", "Voltage Dips"), "As per the standard"),
    ]
    remarks = data.get("test_remarks") or {}
    hours = data.get("test_hours") or {}
    rows = []
    for name, aliases, default_range in tests:
        key = next((key for key in list(hours) + list(remarks) if any(alias.casefold() in _text(key).casefold() for alias in aliases)), "")
        selected = _selected_test(data, *aliases)
        rows.append([
            "Yes" if selected else "No",
            name,
            _first_value(data, "basic_standard", "product_standards") or "As per the selected standard",
            default_range if selected else "",
            _first_value(data, "operating_frequency", "supply_vf") if selected else "",
            _text(hours.get(key)) if selected and isinstance(hours, dict) else "",
            _text(remarks.get(key)) if selected and isinstance(remarks, dict) else "",
        ])
    return rows


def build_test_plan_docx(request_data, template_path):
    """Populate an IEC-FRM-503 template and return a DOCX stream."""
    if not os.path.isfile(template_path):
        raise FileNotFoundError(template_path)
    document = Document(template_path)
    tables = document.tables
    if len(tables) < 5:
        raise ValueError("IEC-FRM-503 template does not contain the expected five tables")

    overview = tables[0]
    overview_values = {
        1: _first_value(request_data, "job_number", "job_id", "tco_id"),
        2: request_data.get("product_name"),
        3: request_data.get("manufacturer"),
        4: request_data.get("model_number"),
        5: request_data.get("serial_number"),
        6: request_data.get("test_samples"),
        7: request_data.get("sample_received_date", "submitted_at"),
    }
    for row_index, value in overview_values.items():
        _set_cell(overview, row_index, 1, value)
    _set_cell(overview, 8, 2, request_data.get("service_types"))
    _set_cell(overview, 9, 2, _first_value(request_data, "capability_available", "samples_available_in_lab"))
    _set_cell(overview, 10, 2, request_data.get("external_subcontract_service"))
    _set_cell(overview, 11, 2, request_data.get("temperature"))
    _set_cell(overview, 12, 2, request_data.get("relative_humidity"))
    _set_cell(overview, 13, 2, request_data.get("pressure"))
    _set_cell(overview, 14, 1, request_data.get("externally_provided_services"))

    equipment_table = tables[1]
    equipment_rows = request_data.get("selected_equipment") or request_data.get("equipment") or []
    if isinstance(equipment_rows, dict):
        equipment_rows = list(equipment_rows.values())
    for row_index, equipment in enumerate(equipment_rows[: len(equipment_table.rows) - 1], start=1):
        if isinstance(equipment, dict):
            values = [
                _first_value(equipment, "name", "equipment_name"),
                _first_value(equipment, "make", "manufacturer"),
                _first_value(equipment, "model_no", "model", "model_number"),
                _first_value(equipment, "serial_no", "serial_number"),
                _first_value(equipment, "calibration_due_date", "calibration_due"),
            ]
        else:
            values = [equipment, "", "", "", ""]
        for cell_index, value in enumerate(values):
            _set_cell(equipment_table, row_index, cell_index, value)

    test_table = tables[2]
    for row_index, values in enumerate(_test_rows(request_data), start=1):
        for cell_index, value in enumerate(values):
            _set_cell(test_table, row_index, cell_index, value)

    schedule = tables[3]
    _set_cell(schedule, 1, 1, request_data.get("test_duration"))
    _set_cell(schedule, 2, 1, request_data.get("test_commencement_date"))
    _set_cell(schedule, 3, 1, request_data.get("test_completion_date"))

    signoff = tables[4]
    _set_cell(signoff, 1, 1, _first_value(request_data, "assigned_engineer_name", "requester_name"))
    _set_cell(signoff, 1, 3, _first_value(request_data, "lab_manager_name", "approved_by"))
    _set_cell(signoff, 2, 1, _first_value(request_data, "requester_date", "submitted_at"))
    _set_cell(signoff, 2, 3, request_data.get("lab_manager_date"))
    _set_cell(signoff, 3, 1, _first_value(request_data, "requester_signature", "requester_name"))
    _set_cell(signoff, 3, 3, _first_value(request_data, "lab_manager_signature", "lab_manager_name"))

    output = BytesIO()
    document.save(output)
    output.seek(0)
    return output
