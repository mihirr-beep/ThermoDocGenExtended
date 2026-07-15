"""
Migrate legacy IEC/EMC request data into the normalized relational schema.

This script is additive:
- It creates the new normalized tables defined in models.py.
- It copies data out of the legacy `iec_emc_test_requests` table.
- It does not drop or modify the old schema.

Usage:
    python migrate_iec_emc_to_relational.py
    python migrate_iec_emc_to_relational.py --request-id 123
    python migrate_iec_emc_to_relational.py --limit 10
"""

import argparse
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set

from flask import Flask
from sqlalchemy import inspect, text

from mysql_config import config
from models import (
    db,
    get_ist_now,
    EMCRequest,
    EMCRequestAccessory,
    EMCRequestAdditionalModel,
    EMCRequestCable,
    EMCRequestCategory,
    EMCRequestDecisionRule,
    EMCRequestEUTSpec,
    EMCRequestFunctionalMode,
    EMCRequestProductEnvironment,
    EMCRequestProductStandard,
    EMCRequestSerialNumber,
    EMCRequestServiceType,
    EMCRequestSupplyVF,
    EMCRequestTest,
    EMCRequestTestCE,
    EMCRequestTestCRF,
    EMCRequestTestEFT,
    EMCRequestTestESD,
    EMCRequestTestFlicker,
    EMCRequestTestHarmonic,
    EMCRequestTestPowerFreq,
    EMCRequestTestRE,
    EMCRequestTestRS,
    EMCRequestTestRSInterim,
    EMCRequestTestStandard,
    EMCRequestTestSurge,
    EMCRequestTestVoltageDips,
    EMCRequestWireless,
)


TEST_ORDER = [
    'CE',
    'RE',
    'ESD',
    'HARMONIC',
    'FLICKER',
    'RS',
    'RS_INTERIM',
    'EFT',
    'SURGE',
    'CRF',
    'POWER_FREQ',
    'VOLTAGE_DIPS',
]

TEST_ALIAS_MAP = {
    'CE': 'CE',
    'CONDUCTEDEMISSION': 'CE',
    'RE': 'RE',
    'RADIATEDEMISSION': 'RE',
    'ESD': 'ESD',
    'ELECTROSTATICDISCHARGE': 'ESD',
    'HARMONIC': 'HARMONIC',
    'HARMONICCURRENTEMISSION': 'HARMONIC',
    'FLICKER': 'FLICKER',
    'VOLTAGEFLICKER': 'FLICKER',
    'VOLTAGECHANGES': 'FLICKER',
    'VOLTAGECHANGESVOLTAGEFLUCTUATIONSANDFLICKEREMISSION': 'FLICKER',
    'RS': 'RS',
    'RI': 'RS',
    'RSRI': 'RS',
    'RS_RI': 'RS',
    'RADIATEDSUSCEPTIBILITY': 'RS',
    'RSINTERIM': 'RS_INTERIM',
    'RSRIINTERIM': 'RS_INTERIM',
    'RS_RI_INTERIM': 'RS_INTERIM',
    'EFT': 'EFT',
    'SURGE': 'SURGE',
    'CRF': 'CRF',
    'PFMF': 'POWER_FREQ',
    'POWER': 'POWER_FREQ',
    'POWERFREQUENCYMAGNETICFIELD': 'POWER_FREQ',
    'POWERFREQUENCYMAGNETICFIELDIMMUNITY': 'POWER_FREQ',
    'VOLTAGE': 'VOLTAGE_DIPS',
    'VOLTAGEDIP': 'VOLTAGE_DIPS',
    'VOLTAGEDIPS': 'VOLTAGE_DIPS',
    'VOLTAGEDIPSSHORTINTERRUPTIONS': 'VOLTAGE_DIPS',
}

TEST_CONFIG = {
    'CE': {
        'standards_key': 'ce_standard',
        'fields': ['ce_voltage_freq', 'ce_freq_range', 'ce_cables', 'ce_class', 'ce_signal_lines', 'ce_custom_spec'],
    },
    'RE': {
        'standards_key': 're_standard',
        'fields': ['re_voltage_freq', 're_freq_range', 're_class', 're_custom_spec'],
    },
    'ESD': {
        'standards_key': 'esd_standard',
        'fields': ['esd_voltage_freq', 'esd_contact', 'esd_air', 'esd_custom_spec'],
    },
    'HARMONIC': {
        'standards_key': 'harmonic_standard',
        'fields': ['harmonic_voltage_freq', 'harmonic_class', 'harmonic_custom_spec'],
    },
    'FLICKER': {
        'standards_key': 'flicker_standard',
        'fields': ['flicker_voltage_freq', 'flicker_custom_specification', 'flicker_custom_spec'],
    },
    'RS': {
        'standards_key': 'rs_standard',
        'fields': ['rs_voltage_freq', 'rs_freq_range', 'rs_field_strength1', 'rs_field_strength2', 'rs_field_strength3', 'rs_ri_custom_spec'],
    },
    'RS_INTERIM': {
        'standards_key': 'rs_interim_standard',
        'fields': ['rs_interim_voltage_freq', 'rs_interim_freq_range', 'rs_interim_field_strength1', 'rs_interim_field_strength2', 'rs_interim_field_strength3', 'rs_ri_interim_custom_spec'],
    },
    'EFT': {
        'standards_key': 'eft_standard',
        'fields': ['eft_voltage_freq', 'eft_cables_power', 'eft_cables_signal', 'eft_test_level1', 'eft_test_level2', 'eft_test_level_custom_kv', 'eft_custom_spec'],
    },
    'SURGE': {
        'standards_key': 'surge_standard',
        'fields': ['surge_voltage_freq', 'surge_cables_power', 'surge_cables_signal', 'surge_cm1', 'surge_cm2', 'surge_dm1', 'surge_dm2', 'surge_custom_spec'],
    },
    'CRF': {
        'standards_key': 'crf_standard',
        'fields': ['crf_voltage_freq', 'crf_freq_range', 'crf_cables_power', 'crf_cables_signal', 'crf_test_level1', 'crf_test_level2', 'crf_custom_spec'],
    },
    'POWER_FREQ': {
        'standards_key': 'power_freq_standard',
        'fields': ['power_freq_voltage_freq', 'power_freq_test_level', 'power_freq_custom_spec'],
    },
    'VOLTAGE_DIPS': {
        'standards_key': 'voltage_dips_standard',
        'fields': [
            'voltage_dips_min',
            'voltage_dips_max',
            'voltage_dips_voltage_freq',
            'voltage_dips_voltage_dip1',
            'voltage_dips_voltage_dip2',
            'voltage_dips_voltage_dip3',
            'voltage_dips_interruption',
            'voltage_dips_time1',
            'voltage_dips_time2',
            'voltage_dips_time3',
            'voltage_dips_time4',
            'voltage_dips_custom_spec',
        ],
    },
}


class LegacyRequestRecord:
    """Lightweight adapter for rows read from the retired flat legacy table."""

    def __init__(self, row: Dict[str, Any]):
        self._row = dict(row)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._row[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            'service_types': as_list(self.service_types),
            'serial_numbers': as_list(self.serial_numbers),
            'additional_models': as_list(self.additional_models),
            'accessories': as_list(self.accessories),
            'cables': as_list(self.cables),
            'eut_specs': as_list(self.eut_specs),
            'supply_vf': as_list(self.supply_vf),
            'wireless': as_list(self.wireless),
            'product_standards': as_list(self.product_standards),
            'product_environment': parse_json_like(self.product_environment, default={}),
            'group': self.group,
            'class_type': self.class_type,
            'selected_tests': parse_selected_tests(self.selected_tests),
            'selected_tests_for_development': parse_selected_tests(self.selected_tests_for_development),
            'test_hours': parse_json_like(self.test_hours, default={}),
            'test_remarks': parse_json_like(self.test_remarks, default={}),
            'decision_rule': as_list(self.decision_rule),
            'functional_modes': as_list(self.functional_modes),
            'ce_signal_lines': as_list(self.ce_signal_lines),
        }

        category_value = parse_json_like(self.category, default=[])
        if isinstance(category_value, list):
            payload['category'] = [str(item).strip() for item in category_value if str(item).strip()]
        elif category_value in (None, ''):
            payload['category'] = []
        else:
            payload['category'] = [str(category_value).strip()]

        for config_row in TEST_CONFIG.values():
            payload[config_row['standards_key']] = as_list(getattr(self, config_row['standards_key'], None))

        return payload


def parse_selected_tests(value: Any) -> List[str]:
    """Parse selected-test payloads saved in different legacy formats."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, dict):
        result = []
        for key, state in value.items():
            normalized = str(state).strip().lower()
            if state is True or normalized in {'true', 'yes', '1', 'on', 'selected'}:
                key_text = str(key).strip()
                if key_text:
                    result.append(key_text)
        return result
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return []
        parsed = parse_json_like(text_value, default=None)
        if parsed is not None and parsed is not text_value:
            return parse_selected_tests(parsed)
        return [token.strip() for token in re.split(r'[,/|]', text_value) if token.strip()]
    value_text = str(value).strip()
    return [value_text] if value_text else []


def create_app() -> Flask:
    """Create a minimal Flask app for SQLAlchemy migrations."""
    app = Flask(__name__)
    environment = os.environ.get('FLASK_ENV', 'default')
    config_class = config.get(environment, config['default'])
    app.config.from_object(config_class)
    config_class.init_app(app)
    db.init_app(app)
    return app


def ensure_normalized_schema_compatibility() -> None:
    """Adjust normalized table column types when tables already exist."""
    statements = [
        "ALTER TABLE iec_emc_requests MODIFY COLUMN block_diagram LONGTEXT NULL",
        "ALTER TABLE iec_emc_requests MODIFY COLUMN model_variance_document LONGTEXT NULL",
        "ALTER TABLE iec_emc_requests MODIFY COLUMN requester_signature LONGTEXT NULL",
        "ALTER TABLE iec_emc_requests MODIFY COLUMN lab_manager_signature LONGTEXT NULL",
    ]
    for statement in statements:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception:
            db.session.rollback()

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    if {
        'iec_emc_request_test_ce',
        'iec_emc_request_test_ce_signal_lines',
    }.issubset(existing_tables):
        ce_columns = {column['name'] for column in inspector.get_columns('iec_emc_request_test_ce')}
        if 'ce_signal_lines' not in ce_columns:
            return

        rows = db.session.execute(text("""
            SELECT request_test_id, ce_signal_lines
            FROM iec_emc_request_test_ce
            WHERE ce_signal_lines IS NOT NULL
              AND ce_signal_lines <> ''
            ORDER BY request_test_id ASC
        """)).mappings().all()

        for row in rows:
            request_test_id = row.get('request_test_id')
            values = as_list(row.get('ce_signal_lines'))
            if request_test_id is None or not values:
                continue
            try:
                existing_rows = db.session.execute(
                    text("""
                        SELECT COUNT(*) AS signal_line_count
                        FROM iec_emc_request_test_ce_signal_lines
                        WHERE request_test_id = :request_test_id
                    """),
                    {
                        'request_test_id': request_test_id,
                    },
                ).scalar()
                if existing_rows:
                    continue

                for index, value in enumerate(values):
                    db.session.execute(
                        text("""
                            INSERT INTO iec_emc_request_test_ce_signal_lines (
                                request_test_id,
                                signal_line_value,
                                sort_order
                            )
                            VALUES (
                                :request_test_id,
                                :signal_line_value,
                                :sort_order
                            )
                        """),
                        {
                            'request_test_id': request_test_id,
                            'signal_line_value': serialize_value(value),
                            'sort_order': index,
                        },
                    )
                db.session.commit()
            except Exception:
                db.session.rollback()


def parse_json_like(value: Any, default: Any = None) -> Any:
    """Parse JSON-like strings while tolerating plain values."""
    if value is None:
        return default
    if isinstance(value, (list, dict, int, float, bool)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return default if default is not None else text
    return value


def as_list(value: Any) -> List[Any]:
    """Normalize a field to a list."""
    parsed = parse_json_like(value, default=[])
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [key for key, state in parsed.items() if state]
    if isinstance(parsed, str):
        return [parsed] if parsed.strip() else []
    return [parsed]


def as_mapping(value: Any) -> Dict[str, Any]:
    """Normalize a field to a dictionary."""
    parsed = parse_json_like(value, default={})
    return parsed if isinstance(parsed, dict) else {}


def serialize_value(value: Any) -> Optional[str]:
    """Serialize complex values into text columns."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, default=str)
    return str(value)


def normalize_token(value: Any) -> str:
    """Normalize token text for alias matching."""
    return re.sub(r'[^A-Za-z0-9_]+', '', str(value or '').upper())


def canonical_test_code(value: Any) -> Optional[str]:
    """Convert legacy test names into canonical relational test codes."""
    normalized = normalize_token(value)
    if not normalized:
        return None
    return TEST_ALIAS_MAP.get(normalized)


def extract_test_codes(value: Any) -> Set[str]:
    """Extract canonical test codes from arbitrary legacy payloads."""
    results: Set[str] = set()

    def visit(node: Any) -> None:
        if node is None:
            return
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if isinstance(node, dict):
            for key in ('shortcut', 'short_code', 'code', 'test', 'test_name', 'name', 'value'):
                if key in node:
                    visit(node.get(key))
            for key, state in node.items():
                if state is True or str(state).strip().lower() in ('true', 'yes', '1', 'on', 'selected'):
                    visit(key)
            return
        if isinstance(node, str):
            parsed = parse_json_like(node, default=node)
            if parsed is not node:
                visit(parsed)
                return
            for token in re.split(r'[,/|]', node):
                code = canonical_test_code(token)
                if code:
                    results.add(code)
            return
        code = canonical_test_code(node)
        if code:
            results.add(code)

    visit(value)
    return results


def canonicalize_mapping(value: Any) -> Dict[str, Any]:
    """Map legacy keyed objects like testHours/testRemarks to canonical test codes."""
    result: Dict[str, Any] = {}
    for key, raw_value in as_mapping(value).items():
        code = canonical_test_code(key)
        if code:
            result[code] = raw_value
    return result


def is_present(value: Any) -> bool:
    """Return True when a value carries meaningful data."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def coerce_float(value: Any) -> Optional[float]:
    """Coerce legacy numeric text into a float."""
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def add_ordered_rows(model_cls, request_id: int, values: Iterable[Any], attr_name: str) -> int:
    """Insert ordered child rows for a normalized request."""
    created = 0
    for index, value in enumerate(as_list(values)):
        text_value = serialize_value(value)
        if not text_value:
            continue
        row = model_cls(request_id=request_id, sort_order=index)
        setattr(row, attr_name, text_value)
        db.session.add(row)
        created += 1
    return created


def add_product_environments(request_id: int, value: Any) -> int:
    """Insert normalized product environment key/value rows."""
    created = 0
    parsed = parse_json_like(value, default={})
    if isinstance(parsed, dict):
        for index, (key, env_value) in enumerate(parsed.items()):
            row = EMCRequestProductEnvironment(
                request_id=request_id,
                environment_key=str(key),
                environment_value=serialize_value(env_value),
                sort_order=index,
            )
            db.session.add(row)
            created += 1
    else:
        text_value = serialize_value(value)
        if text_value:
            db.session.add(EMCRequestProductEnvironment(
                request_id=request_id,
                environment_key='raw',
                environment_value=text_value,
                sort_order=0,
            ))
            created += 1
    return created


def has_test_payload(legacy_request: LegacyRequestRecord, payload: Dict[str, Any], test_code: str) -> bool:
    """Return True when the legacy row contains data for a canonical test."""
    config_row = TEST_CONFIG[test_code]
    if as_list(payload.get(config_row['standards_key'])):
        return True
    if test_code == 'CE' and as_list(payload.get('ce_signal_lines')):
        return True
    for attr_name in config_row['fields']:
        if is_present(getattr(legacy_request, attr_name, None)):
            return True
    return False


def build_parent_request(legacy_request: LegacyRequestRecord) -> EMCRequest:
    """Copy common request fields into the new normalized parent table."""
    legacy_extras = db.session.execute(
        text(
            """
            SELECT rejection_reason, rejected_by, rejected_at
            FROM iec_emc_test_requests
            WHERE id = :request_id
            """
        ),
        {'request_id': legacy_request.id},
    ).mappings().first() or {}

    return EMCRequest(
        legacy_request_id=legacy_request.id,
        user_id=legacy_request.user_id,
        tco_id=legacy_request.tco_id,
        job_id=legacy_request.job_id,
        status=legacy_request.status or 'Draft',
        product_name=legacy_request.product_name,
        manufacturer=legacy_request.manufacturer,
        manufacturer_address=legacy_request.manufacturer_address,
        model_number=legacy_request.model_number,
        serial_number=legacy_request.serial_number,
        test_samples=legacy_request.test_samples or 1,
        samples_available_in_lab=legacy_request.samples_available_in_lab,
        has_model_variance=legacy_request.has_model_variance,
        model_variance=legacy_request.model_variance,
        model_variance_document=legacy_request.model_variance_document,
        project_details_intent=legacy_request.project_details_intent,
        has_wireless_interface=legacy_request.has_wireless_interface,
        dimension_unit=legacy_request.dimension_unit or 'mm',
        weight=legacy_request.weight,
        operating_frequency=legacy_request.operating_frequency,
        length=legacy_request.length,
        width=legacy_request.width,
        height=legacy_request.height,
        product_type=getattr(legacy_request, 'type', None),
        type_others=legacy_request.type_others,
        product_description=legacy_request.product_description,
        test_configuration=legacy_request.test_configuration,
        operation_modes=legacy_request.operation_modes,
        monitoring_parameters=legacy_request.monitoring_parameters,
        additional_info=legacy_request.additional_info,
        block_diagram=legacy_request.block_diagram,
        product_environment_other=legacy_request.product_environment_other,
        product_group=getattr(legacy_request, 'group', None),
        class_type=legacy_request.class_type,
        continue_testing=legacy_request.continue_testing,
        test_report_required=legacy_request.test_report_required,
        uncertainty_required=legacy_request.uncertainty_required,
        test_witness=legacy_request.test_witness,
        conformity_required=legacy_request.conformity_required,
        conformity_statement=legacy_request.conformity_statement,
        number_of_modes=legacy_request.number_of_modes,
        requester_name=legacy_request.requester_name,
        requester_department=legacy_request.requester_department,
        requester_group=legacy_request.requester_group,
        requester_division=legacy_request.requester_division,
        requester_site=legacy_request.requester_site,
        requester_email=legacy_request.requester_email,
        requester_contact=legacy_request.requester_contact,
        requester_designation=legacy_request.requester_designation,
        requester_date=legacy_request.requester_date,
        requester_expected_completion_date=legacy_request.requester_expected_completion_date,
        requester_status=legacy_request.requester_status,
        requester_signature=legacy_request.requester_signature,
        job_number=legacy_request.job_number,
        sample_condition=legacy_request.sample_condition,
        capability_available=legacy_request.capability_available,
        sample_received_date=legacy_request.sample_received_date,
        test_duration=legacy_request.test_duration,
        test_commencement_date=legacy_request.test_commencement_date,
        test_completion_date=legacy_request.test_completion_date,
        lab_manager_name=legacy_request.lab_manager_name,
        lab_manager_date=legacy_request.lab_manager_date,
        lab_manager_signature=legacy_request.lab_manager_signature,
        lab_manager_signed_at=legacy_request.lab_manager_signed_at,
        assigned_engineer_id=legacy_request.assigned_engineer_id,
        assigned_engineer_name=legacy_request.assigned_engineer_name,
        assignment_priority=legacy_request.assignment_priority,
        assignment_due_date=legacy_request.assignment_due_date,
        assignment_notes=legacy_request.assignment_notes,
        rejection_reason=legacy_extras.get('rejection_reason'),
        rejected_by=legacy_extras.get('rejected_by'),
        rejected_at=legacy_extras.get('rejected_at'),
        review_comments=legacy_request.review_comments,
        reviewed_by=legacy_request.reviewed_by,
        reviewed_at=legacy_request.reviewed_at,
        plan_update_history=legacy_request.plan_update_history,
        submitted_at=legacy_request.submitted_at,
        created_at=legacy_request.created_at or get_ist_now(),
        updated_at=legacy_request.updated_at or get_ist_now(),
    )


def populate_test_details(test_row: EMCRequestTest, legacy_request: LegacyRequestRecord) -> None:
    """Create the appropriate one-to-one detail row for a migrated test."""
    code = test_row.test_code
    if code == 'CE':
        db.session.add(EMCRequestTestCE(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.ce_voltage_freq,
            freq_range=legacy_request.ce_freq_range,
            cables=legacy_request.ce_cables,
            ce_class=legacy_request.ce_class,
            ce_signal_lines=as_list(legacy_request.ce_signal_lines),
            custom_spec=serialize_value(legacy_request.ce_custom_spec),
        ))
    elif code == 'RE':
        db.session.add(EMCRequestTestRE(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.re_voltage_freq,
            freq_range=legacy_request.re_freq_range,
            re_class=legacy_request.re_class,
            custom_spec=serialize_value(legacy_request.re_custom_spec),
        ))
    elif code == 'ESD':
        db.session.add(EMCRequestTestESD(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.esd_voltage_freq,
            contact_level=legacy_request.esd_contact,
            air_level=legacy_request.esd_air,
            custom_spec=serialize_value(legacy_request.esd_custom_spec),
        ))
    elif code == 'HARMONIC':
        db.session.add(EMCRequestTestHarmonic(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.harmonic_voltage_freq,
            harmonic_class=legacy_request.harmonic_class,
            custom_spec=serialize_value(legacy_request.harmonic_custom_spec),
        ))
    elif code == 'FLICKER':
        db.session.add(EMCRequestTestFlicker(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.flicker_voltage_freq,
            custom_specification=serialize_value(legacy_request.flicker_custom_specification),
            custom_spec=serialize_value(legacy_request.flicker_custom_spec),
        ))
    elif code == 'RS':
        db.session.add(EMCRequestTestRS(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.rs_voltage_freq,
            freq_range=legacy_request.rs_freq_range,
            field_strength1=legacy_request.rs_field_strength1,
            field_strength2=legacy_request.rs_field_strength2,
            field_strength3=legacy_request.rs_field_strength3,
            custom_spec=serialize_value(legacy_request.rs_ri_custom_spec),
        ))
    elif code == 'RS_INTERIM':
        db.session.add(EMCRequestTestRSInterim(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.rs_interim_voltage_freq,
            freq_range=legacy_request.rs_interim_freq_range,
            field_strength1=legacy_request.rs_interim_field_strength1,
            field_strength2=legacy_request.rs_interim_field_strength2,
            field_strength3=legacy_request.rs_interim_field_strength3,
            custom_spec=serialize_value(legacy_request.rs_ri_interim_custom_spec),
        ))
    elif code == 'EFT':
        db.session.add(EMCRequestTestEFT(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.eft_voltage_freq,
            cables_power=legacy_request.eft_cables_power,
            cables_signal=legacy_request.eft_cables_signal,
            test_level1=legacy_request.eft_test_level1,
            test_level2=legacy_request.eft_test_level2,
            test_level_custom_kv=legacy_request.eft_test_level_custom_kv,
            custom_spec=serialize_value(legacy_request.eft_custom_spec),
        ))
    elif code == 'SURGE':
        db.session.add(EMCRequestTestSurge(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.surge_voltage_freq,
            cables_power=legacy_request.surge_cables_power,
            cables_signal=legacy_request.surge_cables_signal,
            cm1=legacy_request.surge_cm1,
            cm2=legacy_request.surge_cm2,
            dm1=legacy_request.surge_dm1,
            dm2=legacy_request.surge_dm2,
            custom_spec=serialize_value(legacy_request.surge_custom_spec),
        ))
    elif code == 'CRF':
        db.session.add(EMCRequestTestCRF(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.crf_voltage_freq,
            freq_range=legacy_request.crf_freq_range,
            cables_power=legacy_request.crf_cables_power,
            cables_signal=legacy_request.crf_cables_signal,
            test_level1=legacy_request.crf_test_level1,
            test_level2=legacy_request.crf_test_level2,
            custom_spec=serialize_value(legacy_request.crf_custom_spec),
        ))
    elif code == 'POWER_FREQ':
        db.session.add(EMCRequestTestPowerFreq(
            request_test_id=test_row.id,
            voltage_freq=legacy_request.power_freq_voltage_freq,
            test_level=legacy_request.power_freq_test_level,
            custom_spec=serialize_value(legacy_request.power_freq_custom_spec),
        ))
    elif code == 'VOLTAGE_DIPS':
        db.session.add(EMCRequestTestVoltageDips(
            request_test_id=test_row.id,
            min_value=legacy_request.voltage_dips_min,
            max_value=legacy_request.voltage_dips_max,
            voltage_freq=legacy_request.voltage_dips_voltage_freq,
            voltage_dip1=legacy_request.voltage_dips_voltage_dip1,
            voltage_dip2=legacy_request.voltage_dips_voltage_dip2,
            voltage_dip3=legacy_request.voltage_dips_voltage_dip3,
            interruption=legacy_request.voltage_dips_interruption,
            time1=legacy_request.voltage_dips_time1,
            time2=legacy_request.voltage_dips_time2,
            time3=legacy_request.voltage_dips_time3,
            time4=legacy_request.voltage_dips_time4,
            custom_spec=serialize_value(legacy_request.voltage_dips_custom_spec),
        ))


def migrate_request(legacy_request: LegacyRequestRecord) -> str:
    """Migrate a single legacy request into the normalized schema."""
    existing = EMCRequest.query.filter_by(legacy_request_id=legacy_request.id).first()
    if existing:
        return 'skipped'

    payload = legacy_request.to_dict()
    new_request = build_parent_request(legacy_request)
    db.session.add(new_request)
    db.session.flush()

    add_ordered_rows(EMCRequestServiceType, new_request.id, payload.get('service_types'), 'service_type')
    add_ordered_rows(EMCRequestSerialNumber, new_request.id, payload.get('serial_numbers'), 'serial_number')
    add_ordered_rows(EMCRequestAdditionalModel, new_request.id, payload.get('additional_models'), 'model_number')
    add_ordered_rows(EMCRequestCategory, new_request.id, payload.get('category'), 'category_name')
    add_ordered_rows(EMCRequestAccessory, new_request.id, payload.get('accessories'), 'accessory_value')
    add_ordered_rows(EMCRequestCable, new_request.id, payload.get('cables'), 'cable_value')
    add_ordered_rows(EMCRequestEUTSpec, new_request.id, payload.get('eut_specs'), 'spec_value')
    add_ordered_rows(EMCRequestSupplyVF, new_request.id, payload.get('supply_vf'), 'value_text')
    add_ordered_rows(EMCRequestWireless, new_request.id, payload.get('wireless'), 'value_text')
    add_ordered_rows(EMCRequestProductStandard, new_request.id, payload.get('product_standards'), 'standard_value')
    add_ordered_rows(EMCRequestDecisionRule, new_request.id, payload.get('decision_rule'), 'rule_value')
    add_ordered_rows(EMCRequestFunctionalMode, new_request.id, payload.get('functional_modes'), 'mode_value')
    add_product_environments(new_request.id, payload.get('product_environment'))

    selected_codes = extract_test_codes(payload.get('selected_tests'))
    development_codes = extract_test_codes(payload.get('selected_tests_for_development'))
    hours_map = canonicalize_mapping(payload.get('test_hours'))
    remarks_map = canonicalize_mapping(payload.get('test_remarks'))
    all_codes = set(selected_codes) | set(development_codes)

    for code in TEST_ORDER:
        if has_test_payload(legacy_request, payload, code):
            all_codes.add(code)

    for code in TEST_ORDER:
        if code not in all_codes:
            continue

        test_row = EMCRequestTest(
            request_id=new_request.id,
            test_code=code,
            is_selected=code in selected_codes,
            is_developmental=code in development_codes,
            planned_hours=coerce_float(hours_map.get(code)),
            remarks=serialize_value(remarks_map.get(code)),
            workflow_status=legacy_request.status,
            assigned_engineer_id=legacy_request.assigned_engineer_id,
            assigned_engineer_name=legacy_request.assigned_engineer_name,
            planned_start_date=legacy_request.test_commencement_date,
            planned_end_date=legacy_request.test_completion_date,
            created_at=legacy_request.created_at or get_ist_now(),
            updated_at=legacy_request.updated_at or get_ist_now(),
        )
        db.session.add(test_row)
        db.session.flush()

        for index, standard_value in enumerate(as_list(payload.get(TEST_CONFIG[code]['standards_key']))):
            text_value = serialize_value(standard_value)
            if not text_value:
                continue
            db.session.add(EMCRequestTestStandard(
                request_test_id=test_row.id,
                standard_value=text_value,
                sort_order=index,
            ))

        populate_test_details(test_row, legacy_request)

    return 'migrated'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Migrate IEC/EMC requests to the normalized relational schema.')
    parser.add_argument('--request-id', type=int, help='Migrate a single legacy iec_emc_test_requests.id')
    parser.add_argument('--limit', type=int, help='Limit the number of rows migrated')
    return parser.parse_args()


def load_legacy_requests(args: argparse.Namespace) -> List[LegacyRequestRecord]:
    """Load rows from the retired flat legacy table without a runtime ORM model."""
    inspector = inspect(db.engine)
    if 'iec_emc_test_requests' not in inspector.get_table_names():
        return []

    query_sql = """
        SELECT *
        FROM iec_emc_test_requests
    """
    params: Dict[str, Any] = {}
    if args.request_id:
        query_sql += "\nWHERE id = :request_id"
        params['request_id'] = args.request_id
    query_sql += "\nORDER BY id ASC"
    if args.limit:
        query_sql += "\nLIMIT :limit"
        params['limit'] = args.limit

    rows = db.session.execute(text(query_sql), params).mappings().all()
    return [LegacyRequestRecord(row) for row in rows]


def main() -> None:
    args = parse_args()
    app = create_app()

    with app.app_context():
        db.create_all()
        ensure_normalized_schema_compatibility()
        legacy_requests = load_legacy_requests(args)
        if not legacy_requests:
            print('no legacy iec_emc_test_requests rows found')
            return

        migrated = 0
        skipped = 0

        for legacy_request in legacy_requests:
            try:
                result = migrate_request(legacy_request)
                db.session.commit()
                if result == 'migrated':
                    migrated += 1
                    print(f'migrated legacy request {legacy_request.id}')
                else:
                    skipped += 1
                    print(f'skipped legacy request {legacy_request.id} (already present)')
            except Exception as exc:
                db.session.rollback()
                print(f'failed legacy request {legacy_request.id}: {exc}')
                raise

        print(f'complete: migrated={migrated}, skipped={skipped}')


if __name__ == '__main__':
    main()
