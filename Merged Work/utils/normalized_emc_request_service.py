"""Write helpers for normalized EMC request records."""

import json
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set

from models import (
    db,
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


TEST_CODE_MAP = {
    'CE': 'CE',
    'RE': 'RE',
    'ESD': 'ESD',
    'HARMONIC': 'HARMONIC',
    'HARMONICCURRENTEMISSION': 'HARMONIC',
    'HARMONIC CURRENT EMISSION': 'HARMONIC',
    'FLICKER': 'FLICKER',
    'VOLTAGEFLICKER': 'FLICKER',
    'VOLTAGE FLICKER': 'FLICKER',
    'VOLTAGE CHANGES': 'FLICKER',
    'RS': 'RS',
    'RI': 'RS',
    'RS_RI': 'RS',
    'RS RI': 'RS',
    'RADIATEDSUSCEPTIBILITY': 'RS',
    'RSINTERIM': 'RS_INTERIM',
    'RS_RI_INTERIM': 'RS_INTERIM',
    'RS RI INTERIM': 'RS_INTERIM',
    'RS_RI_INTERIM': 'RS_INTERIM',
    'EFT': 'EFT',
    'SURGE': 'SURGE',
    'CRF': 'CRF',
    'PFMF': 'POWER_FREQ',
    'POWER': 'POWER_FREQ',
    'POWERFREQUENCYMAGNETICFIELD': 'POWER_FREQ',
    'POWER FREQUENCY MAGNETIC FIELD IMMUNITY': 'POWER_FREQ',
    'VOLTAGEDIPS': 'VOLTAGE_DIPS',
    'VOLTAGE DIPS': 'VOLTAGE_DIPS',
    'VOLTAGE DIPS SHORT INTERRUPTIONS': 'VOLTAGE_DIPS',
}


def _parse_json_like(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict, bool, int, float)):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            return value
    return value


def _as_list(value: Any) -> List[Any]:
    parsed = _parse_json_like(value, default=[])
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [key for key, state in parsed.items() if state]
    return [parsed]


def _as_repeating_rows(value: Any) -> List[Any]:
    parsed = _parse_json_like(value, default=[])
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, tuple):
        return list(parsed)
    if isinstance(parsed, dict):
        if any(isinstance(item, (dict, list, tuple)) for item in parsed.values()):
            def _sort_key(item):
                key = str(item[0] or '').strip()
                if key.isdigit():
                    return (0, int(key))
                return (1, key)

            return [item for _, item in sorted(parsed.items(), key=_sort_key)]
        return [parsed]
    return [parsed]


def _serialize_row_value(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, tuple):
        value = list(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value).strip()


def _safe_float(value: Any) -> Optional[float]:
    if value in (None, ''):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_test_code(value: Any) -> Optional[str]:
    text = str(value or '').strip()
    if not text:
        return None
    normalized = ''.join(ch for ch in text.upper() if ch.isalnum() or ch in {'_', ' '})
    normalized = ' '.join(normalized.split())
    return TEST_CODE_MAP.get(normalized) or TEST_CODE_MAP.get(normalized.replace(' ', '')) or None


def _selected_codes(value: Any) -> Set[str]:
    result: Set[str] = set()
    for item in _as_list(value):
        code = _normalize_test_code(item)
        if code:
            result.add(code)
    return result


def _canonical_map(raw_map: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    parsed = _parse_json_like(raw_map, default={})
    if not isinstance(parsed, dict):
        return result
    for key, value in parsed.items():
        code = _normalize_test_code(key)
        if code:
            result[code] = value
    return result


def _config_dict(value: Any) -> Dict[str, Any]:
    parsed = _parse_json_like(value, default={})
    return parsed if isinstance(parsed, dict) else {}


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value in (None, '', [], {}):
            continue
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
            continue
        return value
    return ''


def _range_display(value: Any) -> str:
    range_data = _config_dict(value)
    if not range_data:
        return ''
    if isinstance(range_data.get('frequency'), dict):
        range_data = range_data.get('frequency', {})

    from_value = str(range_data.get('from', '') or '').strip()
    from_unit = str(range_data.get('fromUnit', range_data.get('from_unit', '')) or '').strip()
    to_value = str(range_data.get('to', '') or '').strip()
    to_unit = str(range_data.get('toUnit', range_data.get('to_unit', '')) or '').strip()

    if from_value and to_value:
        start = f"{from_value} {from_unit}".strip()
        end = f"{to_value} {to_unit}".strip()
        return f"{start} to {end}".strip()
    if from_value:
        return f"{from_value} {from_unit}".strip()
    if to_value:
        return f"{to_value} {to_unit}".strip()
    return ''


def _format_with_unit(value: Any, unit: str) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    if unit.lower() in text.lower():
        return text
    return f"{text} {unit}"


def _is_truthy_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or '').strip().lower()
    return text in {'1', 'true', 'yes', 'y', 'on'}


def _selected_cable_summary(value: Any) -> str:
    cables = _config_dict(value)
    labels = []
    if _is_truthy_flag(cables.get('power')):
        labels.append('power')
    if _is_truthy_flag(cables.get('signal')):
        labels.append('signal')
    return ', '.join(labels)


def _selected_cable_count(value: Any, key: str, count_value: Any) -> str:
    count_text = str(count_value or '').strip()
    if count_text:
        return count_text
    cables = _config_dict(value)
    if _is_truthy_flag(cables.get(key)):
        return 'selected'
    return ''


def _selector_or_custom_value(selector_value: Any, custom_value: Any = '') -> str:
    selector_text = str(selector_value or '').strip()
    custom_text = str(custom_value or '').strip()
    if selector_text.lower().startswith('custom') and custom_text:
        return custom_text
    return selector_text


def _is_development_assistance(service_types: Any) -> bool:
    for item in _as_list(service_types):
        text = str(item or '').strip().lower()
        if 'development' in text and 'assist' in text:
            return True
    return False


def _set_ordered_rows(request_obj: EMCRequest, attr_name: str, model_cls, value_attr: str, values: Iterable[Any]) -> None:
    setattr(request_obj, attr_name, [])
    for index, value in enumerate(_as_repeating_rows(values)):
        text_value = _serialize_row_value(value)
        if not text_value:
            continue
        getattr(request_obj, attr_name).append(
            model_cls(sort_order=index, **{value_attr: text_value})
        )


def _parse_request_date(value: Any, fallback_date) -> Any:
    if not value:
        return fallback_date
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except Exception:
        return fallback_date


def _parse_optional_date(value: Any) -> Any:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except Exception:
        return None


def populate_emc_request_from_form(
    request_obj: EMCRequest,
    form_data: Dict[str, Any],
    *,
    normalize_dimensions_to_mm,
    resolve_test_standard_values,
    today_date,
) -> EMCRequest:
    """Populate a normalized EMCRequest from request form data."""
    test_configurations = form_data.get('testConfigurations', {}) or {}
    if isinstance(test_configurations, str):
        test_configurations = _parse_json_like(test_configurations, default={}) or {}
    if not isinstance(test_configurations, dict):
        test_configurations = {}
    ce_config = _config_dict(test_configurations.get('CE'))
    re_config = _config_dict(test_configurations.get('RE'))
    esd_config = _config_dict(test_configurations.get('ESD'))
    harmonic_config = _config_dict(test_configurations.get('Harmonic'))
    flicker_config = _config_dict(test_configurations.get('VoltageFlicker'))
    rs_config = _config_dict(test_configurations.get('RS_RI'))
    rs_interim_config = _config_dict(test_configurations.get('RS_RI_Interim'))
    eft_config = _config_dict(test_configurations.get('EFT'))
    surge_config = _config_dict(test_configurations.get('Surge'))
    crf_config = _config_dict(test_configurations.get('CRF'))
    pfmf_config = _config_dict(test_configurations.get('PFMF'))
    voltage_dips_config = _config_dict(test_configurations.get('VoltageDips'))

    existing_test_meta = {
        test.test_code: {
            'assigned_engineer_id': test.assigned_engineer_id,
            'assigned_engineer_name': test.assigned_engineer_name,
            'workflow_status': test.workflow_status,
            'planned_start_date': test.planned_start_date,
            'planned_end_date': test.planned_end_date,
            'created_at': test.created_at,
        }
        for test in getattr(request_obj, 'tests', []) or []
    }

    # Existing requests rebuild all ordered child collections from form input.
    # Flush orphan deletions first so unique constraints like (request_id, test_code)
    # do not collide with replacement rows inserted in the same transaction.
    if getattr(request_obj, 'id', None):
        request_obj.service_types = []
        request_obj.serial_numbers = []
        request_obj.additional_models = []
        request_obj.categories = []
        request_obj.accessories = []
        request_obj.cables = []
        request_obj.eut_specs = []
        request_obj.supply_vf_values = []
        request_obj.wireless_values = []
        request_obj.product_standards = []
        request_obj.product_environments = []
        request_obj.decision_rules = []
        request_obj.functional_modes = []
        request_obj.tests = []
        db.session.flush()

    request_obj.service_types = []
    request_obj.serial_numbers = []
    request_obj.additional_models = []
    request_obj.categories = []
    request_obj.accessories = []
    request_obj.cables = []
    request_obj.eut_specs = []
    request_obj.supply_vf_values = []
    request_obj.wireless_values = []
    request_obj.product_standards = []
    request_obj.product_environments = []
    request_obj.decision_rules = []
    request_obj.functional_modes = []
    request_obj.tests = []

    request_obj.product_name = form_data.get('productName', '') or 'Unnamed Product'
    request_obj.manufacturer = form_data.get('manufacturer', '')
    request_obj.manufacturer_address = form_data.get('manufacturerAddress', '')
    request_obj.model_number = form_data.get('modelNumber', '')
    request_obj.serial_number = form_data.get('serialNumber', '')
    request_obj.test_samples = int(form_data.get('testSamples', 1)) if form_data.get('testSamples') else 1
    request_obj.samples_available_in_lab = form_data.get('samplesAvailableInLab', '')

    request_obj.has_model_variance = form_data.get('hasModelVariance', '')
    request_obj.model_variance = form_data.get('modelVariance', '')
    request_obj.project_details_intent = form_data.get('projectDetailsIntent', '')
    model_variance_document = form_data.get('modelVarianceDocument', '')
    if isinstance(model_variance_document, (dict, list)):
        request_obj.model_variance_document = json.dumps(model_variance_document, default=str)
    else:
        request_obj.model_variance_document = model_variance_document or ''
    request_obj.has_wireless_interface = form_data.get('hasWirelessInterface', '')

    dim_unit, dim_length, dim_width, dim_height = normalize_dimensions_to_mm(
        form_data.get('dimensionUnit', 'mm'),
        form_data.get('length'),
        form_data.get('width'),
        form_data.get('height')
    )
    request_obj.dimension_unit = dim_unit
    request_obj.weight = _safe_float(form_data.get('weight'))
    request_obj.operating_frequency = form_data.get('operatingFrequency', '')
    request_obj.length = dim_length
    request_obj.width = dim_width
    request_obj.height = dim_height

    request_obj.product_type = form_data.get('type', '')
    request_obj.type_others = form_data.get('typeOthers', '') if request_obj.product_type == 'Others' else form_data.get('typeOthers', '')
    request_obj.product_description = form_data.get('productDescription', '')
    request_obj.test_configuration = form_data.get('testConfiguration', '')
    request_obj.operation_modes = form_data.get('operationModes', '')
    request_obj.monitoring_parameters = form_data.get('monitoringParameters', '')
    request_obj.additional_info = form_data.get('additionalInfo', '')

    block_diagram = str(form_data.get('blockDiagram', '') or '').strip()
    if block_diagram and block_diagram not in {'__PENDING_FILE__', 'undefined', 'null', '[object File]'}:
        request_obj.block_diagram = block_diagram

    request_obj.product_environment_other = form_data.get('productEnvironmentOther', '')
    request_obj.product_group = form_data.get('group', '')
    request_obj.class_type = form_data.get('class', '')
    request_obj.continue_testing = form_data.get('continueTesting', '')
    request_obj.test_report_required = form_data.get('testReportRequired', '')
    request_obj.uncertainty_required = form_data.get('uncertaintyRequired', '')
    request_obj.test_witness = form_data.get('testWitness', '')
    request_obj.conformity_required = form_data.get('conformityRequired', '')
    request_obj.conformity_statement = form_data.get('conformityStatement', '')
    request_obj.number_of_modes = int(form_data.get('numberOfModes', 0)) if form_data.get('numberOfModes') else None

    request_obj.requester_name = form_data.get('requesterName', '')
    request_obj.requester_department = form_data.get('requesterDepartment', '')
    request_obj.requester_group = form_data.get('requesterGroup', '')
    request_obj.requester_division = form_data.get('requesterDivision', '')
    request_obj.requester_site = form_data.get('requesterSite', '')
    request_obj.requester_email = form_data.get('requesterEmail', '')
    request_obj.requester_contact = form_data.get('requesterContact', '')
    request_obj.requester_designation = form_data.get('requesterDesignation', '')
    request_obj.requester_date = _parse_request_date(form_data.get('requesterDate'), today_date)
    request_obj.requester_expected_completion_date = _parse_optional_date(form_data.get('requesterExpectedCompletionDate'))
    request_obj.requester_status = form_data.get('requesterStatus', request_obj.requester_status or 'At Review')
    request_obj.requester_signature = (
        form_data.get('requesterSignature', '')
        or form_data.get('requesterSignatureName', '')
        or form_data.get('requesterName', '')
    )

    request_obj.job_number = form_data.get('jobNumber', '')
    request_obj.sample_condition = form_data.get('sampleCondition', '')
    request_obj.capability_available = form_data.get('capabilityAvailable', '')
    request_obj.sample_received_date = _parse_optional_date(form_data.get('sampleReceivedDate'))
    request_obj.test_duration = form_data.get('testDuration', '')
    request_obj.test_commencement_date = _parse_optional_date(form_data.get('testCommencementDate'))
    request_obj.test_completion_date = _parse_optional_date(form_data.get('testCompletionDate'))
    request_obj.lab_manager_name = form_data.get('labManagerName', '')
    request_obj.lab_manager_date = _parse_optional_date(form_data.get('labManagerDate'))
    request_obj.lab_manager_signature = form_data.get('labManagerSignature', '')

    _set_ordered_rows(request_obj, 'service_types', EMCRequestServiceType, 'service_type', form_data.get('serviceTypes', []))
    _set_ordered_rows(request_obj, 'serial_numbers', EMCRequestSerialNumber, 'serial_number', form_data.get('serialNumbers', []))
    _set_ordered_rows(request_obj, 'additional_models', EMCRequestAdditionalModel, 'model_number', form_data.get('additionalModels', []))
    _set_ordered_rows(request_obj, 'categories', EMCRequestCategory, 'category_name', form_data.get('category', []))
    _set_ordered_rows(request_obj, 'accessories', EMCRequestAccessory, 'accessory_value', form_data.get('accessories', []))
    _set_ordered_rows(request_obj, 'cables', EMCRequestCable, 'cable_value', form_data.get('cables', []))
    _set_ordered_rows(request_obj, 'eut_specs', EMCRequestEUTSpec, 'spec_value', form_data.get('eutSpecs', []))
    _set_ordered_rows(request_obj, 'supply_vf_values', EMCRequestSupplyVF, 'value_text', form_data.get('supplyVf', []))
    _set_ordered_rows(request_obj, 'wireless_values', EMCRequestWireless, 'value_text', form_data.get('wireless', []))
    _set_ordered_rows(request_obj, 'product_standards', EMCRequestProductStandard, 'standard_value', form_data.get('productStandards', []))
    _set_ordered_rows(request_obj, 'functional_modes', EMCRequestFunctionalMode, 'mode_value', form_data.get('functionalModes', []))

    decision_rule = form_data.get('decisionRule', '')
    if decision_rule:
        request_obj.decision_rules.append(EMCRequestDecisionRule(sort_order=0, rule_value=decision_rule))

    product_environment = _parse_json_like(form_data.get('productEnvironment', {}), default={})
    if isinstance(product_environment, dict):
        for index, (key, value) in enumerate(product_environment.items()):
            request_obj.product_environments.append(
                EMCRequestProductEnvironment(
                    sort_order=index,
                    environment_key=str(key),
                    environment_value=json.dumps(value, default=str) if isinstance(value, (dict, list)) else (str(value) if value is not None else None),
                )
            )

    selected_codes = _selected_codes(form_data.get('selectedTests', []))
    development_codes = _selected_codes(form_data.get('selectedTestsForDevelopment', []))
    if not development_codes and _is_development_assistance(form_data.get('serviceTypes', [])):
        development_codes = set(selected_codes)
    hours_map = _canonical_map(form_data.get('testHours', {}))
    remarks_map = _canonical_map(form_data.get('testRemarks', {}))
    all_codes = set(selected_codes) | set(development_codes) | set(hours_map) | set(remarks_map)

    test_presence_keys = {
        'CE': ['ce_standard', 'ce_voltageFreq', 'ce_freqRange', 'ce_cables', 'ce_class'],
        'RE': ['re_standard', 're_voltageFreq', 're_freqRange', 're_class'],
        'ESD': ['esd_standard', 'esd_voltageFreq', 'esd_contact', 'esd_air'],
        'HARMONIC': ['harmonic_standard', 'harmonic_voltageFreq', 'harmonic_class'],
        'FLICKER': ['flicker_standard', 'flicker_voltageFreq', 'flicker_custom_specification'],
        'RS': ['rs_standard', 'rs_voltageFreq', 'rs_freqRange', 'rs_fieldStrength1'],
        'RS_INTERIM': ['rs_interim_standard', 'rs_interim_voltageFreq', 'rs_interim_freqRange', 'rs_interim_fieldStrength1'],
        'EFT': ['eft_standard', 'eft_voltageFreq', 'eft_cables_power', 'eft_testLevel1'],
        'SURGE': ['surge_standard', 'surge_voltageFreq', 'surge_cables_power', 'surge_cm1'],
        'CRF': ['crf_standard', 'crf_voltageFreq', 'crf_freqRange', 'crf_cables_power'],
        'POWER_FREQ': ['power_freq_standard', 'power_freq_voltageFreq', 'power_freq_testLevel'],
        'VOLTAGE_DIPS': ['voltage_dips_standard', 'voltage_dips_voltageFreq', 'voltage_dips_voltageDip1'],
    }
    for code, keys in test_presence_keys.items():
        if any(form_data.get(key) not in (None, '', [], {}) for key in keys):
            all_codes.add(code)

    if 'CE' in all_codes:
        all_codes.add('CE')

    def add_test_row(code: str) -> EMCRequestTest:
        existing = existing_test_meta.get(code, {})
        test_row = EMCRequestTest(
            test_code=code,
            is_selected=code in selected_codes,
            is_developmental=code in development_codes,
            planned_hours=_safe_float(hours_map.get(code)),
            remarks=str(remarks_map.get(code)).strip() if remarks_map.get(code) not in (None, '') else None,
            workflow_status=existing.get('workflow_status') or request_obj.status,
            assigned_engineer_id=existing.get('assigned_engineer_id'),
            assigned_engineer_name=existing.get('assigned_engineer_name'),
            planned_start_date=existing.get('planned_start_date'),
            planned_end_date=existing.get('planned_end_date'),
            created_at=existing.get('created_at') or request_obj.created_at,
            updated_at=request_obj.updated_at,
        )
        request_obj.tests.append(test_row)
        return test_row

    for code in sorted(all_codes):
        test_row = add_test_row(code)

        standards = []
        if code == 'CE':
            standards = resolve_test_standard_values(form_data.get('ce_standard', []), 'CE', form_data.get('productStandards', []))
            ce_cables = _first_non_empty(form_data.get('ce_cables', ''), _selected_cable_summary(ce_config.get('cables')))
            ce_signal_lines = form_data.get('ce_signal_lines', [])
            if not ce_signal_lines:
                ce_signal_lines = ce_config.get('signalLineTypes', []) if 'signal' in ce_cables else []
            detail = EMCRequestTestCE(
                voltage_freq=form_data.get('ce_voltageFreq', ''),
                freq_range=_first_non_empty(form_data.get('ce_freqRange', ''), _selector_or_custom_value(ce_config.get('spec'), _range_display(ce_config.get('customSpecRange')))),
                cables=ce_cables,
                ce_class=form_data.get('ce_class', ''),
                ce_signal_lines=_as_list(ce_signal_lines),
                custom_spec=json.dumps(ce_config, default=str) if ce_config else (
                    json.dumps(form_data.get('ce_custom_spec'), default=str) if isinstance(form_data.get('ce_custom_spec'), dict) else form_data.get('ce_custom_spec')
                ),
            )
            test_row.ce_detail = detail
        elif code == 'RE':
            standards = resolve_test_standard_values(form_data.get('re_standard', []), 'RE', form_data.get('productStandards', []))
            test_row.re_detail = EMCRequestTestRE(
                voltage_freq=form_data.get('re_voltageFreq', ''),
                freq_range=_first_non_empty(form_data.get('re_freqRange', ''), _selector_or_custom_value(re_config.get('spec'), _range_display(re_config.get('customSpecRange')))),
                re_class=form_data.get('re_class', ''),
                custom_spec=json.dumps(re_config, default=str) if re_config else (
                    json.dumps(form_data.get('re_custom_spec'), default=str) if isinstance(form_data.get('re_custom_spec'), dict) else form_data.get('re_custom_spec')
                ),
            )
        elif code == 'ESD':
            standards = resolve_test_standard_values(form_data.get('esd_standard', []), 'ESD', form_data.get('productStandards', []))
            esd_payload = esd_config or form_data.get('esd_custom_spec')
            test_row.esd_detail = EMCRequestTestESD(
                voltage_freq=form_data.get('esd_voltageFreq', ''),
                contact_level=_first_non_empty(
                    form_data.get('esd_contact', ''),
                    _selector_or_custom_value(esd_config.get('spec'), _format_with_unit(esd_config.get('customContactKV'), 'kV') if _is_truthy_flag(esd_config.get('customContact')) else ''),
                ),
                air_level=_first_non_empty(
                    form_data.get('esd_air', ''),
                    _selector_or_custom_value(esd_config.get('spec'), _format_with_unit(esd_config.get('customAirKV'), 'kV') if _is_truthy_flag(esd_config.get('customAir')) else ''),
                ),
                custom_spec=json.dumps(esd_payload, default=str) if isinstance(esd_payload, dict) else esd_payload,
            )
        elif code == 'HARMONIC':
            standards = resolve_test_standard_values(form_data.get('harmonic_standard', []), 'HARMONIC', form_data.get('productStandards', []))
            test_row.harmonic_detail = EMCRequestTestHarmonic(
                voltage_freq=form_data.get('harmonic_voltageFreq', ''),
                harmonic_class=harmonic_config.get('class', form_data.get('harmonic_class', '')) if isinstance(harmonic_config, dict) else form_data.get('harmonic_class', ''),
                custom_spec=json.dumps(harmonic_config, default=str) if harmonic_config else None,
            )
        elif code == 'FLICKER':
            standards = resolve_test_standard_values(form_data.get('flicker_standard', []), 'FLICKER', form_data.get('productStandards', []))
            custom_spec_text = form_data.get('flicker_custom_specification', '')
            if isinstance(flicker_config, dict) and flicker_config.get('specificationType') == 'custom':
                custom_spec_text = flicker_config.get('customSpecification', '')
            test_row.flicker_detail = EMCRequestTestFlicker(
                voltage_freq=form_data.get('flicker_voltageFreq', ''),
                custom_specification=custom_spec_text,
                custom_spec=json.dumps(flicker_config, default=str) if flicker_config else None,
            )
        elif code == 'RS':
            standards = resolve_test_standard_values(form_data.get('rs_standard', []), 'RS', form_data.get('productStandards', []))
            rs_payload = rs_config or form_data.get('rs_ri_custom_spec')
            test_row.rs_detail = EMCRequestTestRS(
                voltage_freq=form_data.get('rs_voltageFreq', ''),
                freq_range=_first_non_empty(form_data.get('rs_freqRange', ''), _selector_or_custom_value(rs_config.get('frequency'), _range_display(rs_config.get('customSpecRange')))),
                field_strength1=_first_non_empty(form_data.get('rs_fieldStrength1', ''), _selector_or_custom_value(rs_config.get('testLevel'), _format_with_unit(rs_config.get('testLevelCustomVm'), 'V/m'))),
                field_strength2=form_data.get('rs_fieldStrength2', ''),
                field_strength3=form_data.get('rs_fieldStrength3', ''),
                custom_spec=json.dumps(rs_payload, default=str) if isinstance(rs_payload, dict) else rs_payload,
            )
        elif code == 'RS_INTERIM':
            standards = resolve_test_standard_values(form_data.get('rs_interim_standard', []), 'RS_INTERIM', form_data.get('productStandards', []))
            rs_interim_payload = rs_interim_config or form_data.get('rs_ri_interim_custom_spec')
            test_row.rs_interim_detail = EMCRequestTestRSInterim(
                voltage_freq=form_data.get('rs_interim_voltageFreq', ''),
                freq_range=_first_non_empty(form_data.get('rs_interim_freqRange', ''), _selector_or_custom_value(rs_interim_config.get('frequency'), _range_display(rs_interim_config.get('customSpecRange')))),
                field_strength1=_first_non_empty(form_data.get('rs_interim_fieldStrength1', ''), _selector_or_custom_value(rs_interim_config.get('testLevel'), _format_with_unit(rs_interim_config.get('testLevelCustomVm'), 'V/m'))),
                field_strength2=form_data.get('rs_interim_fieldStrength2', ''),
                field_strength3=form_data.get('rs_interim_fieldStrength3', ''),
                custom_spec=json.dumps(rs_interim_payload, default=str) if isinstance(rs_interim_payload, dict) else rs_interim_payload,
            )
        elif code == 'EFT':
            standards = resolve_test_standard_values(form_data.get('eft_standard', []), 'EFT', form_data.get('productStandards', []))
            eft_payload = eft_config or form_data.get('eft_custom_spec')
            test_row.eft_detail = EMCRequestTestEFT(
                voltage_freq=form_data.get('eft_voltageFreq', ''),
                cables_power=_first_non_empty(form_data.get('eft_cables_power', ''), _selected_cable_count(eft_config.get('cables'), 'power', eft_config.get('cablesPowerCount'))),
                cables_signal=_first_non_empty(form_data.get('eft_cables_signal', ''), _selected_cable_count(eft_config.get('cables'), 'signal', eft_config.get('cablesSignalCount'))),
                test_level1=_first_non_empty(form_data.get('eft_testLevel1', ''), eft_config.get('testLevel')),
                test_level2=form_data.get('eft_testLevel2', ''),
                test_level_custom_kv=_safe_float(_first_non_empty(form_data.get('eft_testLevelCustomKv'), eft_config.get('testLevelCustomKv'))),
                custom_spec=json.dumps(eft_payload, default=str) if isinstance(eft_payload, dict) else eft_payload,
            )
        elif code == 'SURGE':
            standards = resolve_test_standard_values(form_data.get('surge_standard', []), 'SURGE', form_data.get('productStandards', []))
            surge_payload = surge_config or form_data.get('surge_custom_spec')
            test_row.surge_detail = EMCRequestTestSurge(
                voltage_freq=form_data.get('surge_voltageFreq', ''),
                cables_power=_first_non_empty(form_data.get('surge_cables_power', ''), _selected_cable_count(surge_config.get('cables'), 'power', surge_config.get('cablesPowerCount'))),
                cables_signal=_first_non_empty(form_data.get('surge_cables_signal', ''), _selected_cable_count(surge_config.get('cables'), 'signal', surge_config.get('cablesSignalCount'))),
                cm1=_first_non_empty(form_data.get('surge_cm1', ''), _format_with_unit(surge_config.get('customCommonKV'), 'kV') if _is_truthy_flag(surge_config.get('customCommon')) else ''),
                cm2=form_data.get('surge_cm2', ''),
                dm1=_first_non_empty(form_data.get('surge_dm1', ''), _format_with_unit(surge_config.get('customDifferentialKV'), 'kV') if _is_truthy_flag(surge_config.get('customDifferential')) else ''),
                dm2=form_data.get('surge_dm2', ''),
                custom_spec=json.dumps(surge_payload, default=str) if isinstance(surge_payload, dict) else surge_payload,
            )
        elif code == 'CRF':
            standards = resolve_test_standard_values(form_data.get('crf_standard', []), 'CRF', form_data.get('productStandards', []))
            crf_payload = crf_config or form_data.get('crf_custom_spec')
            test_row.crf_detail = EMCRequestTestCRF(
                voltage_freq=form_data.get('crf_voltageFreq', ''),
                freq_range=_first_non_empty(form_data.get('crf_freqRange', ''), _selector_or_custom_value(crf_config.get('frequency'), _range_display(crf_config.get('customSpecRange')))),
                cables_power=_first_non_empty(form_data.get('crf_cables_power', ''), _selected_cable_count(crf_config.get('cables'), 'power', crf_config.get('cablesPowerCount'))),
                cables_signal=_first_non_empty(form_data.get('crf_cables_signal', ''), _selected_cable_count(crf_config.get('cables'), 'signal', crf_config.get('cablesSignalCount'))),
                test_level1=_first_non_empty(form_data.get('crf_testLevel1', ''), crf_config.get('testLevel')),
                test_level2=_first_non_empty(form_data.get('crf_testLevel2', ''), _format_with_unit(crf_config.get('testLevelCustomVrms'), 'Vrms')),
                custom_spec=json.dumps(crf_payload, default=str) if isinstance(crf_payload, dict) else crf_payload,
            )
        elif code == 'POWER_FREQ':
            standards = resolve_test_standard_values(form_data.get('power_freq_standard', []), 'POWER_FREQ', form_data.get('productStandards', []))
            pf_payload = pfmf_config or form_data.get('pfmf_custom_spec')
            test_row.power_freq_detail = EMCRequestTestPowerFreq(
                voltage_freq=form_data.get('power_freq_voltageFreq', ''),
                test_level=_first_non_empty(form_data.get('power_freq_testLevel', ''), _selector_or_custom_value(pfmf_config.get('testLevel'), _format_with_unit(pfmf_config.get('testLevelCustomAm'), 'A/m'))),
                custom_spec=json.dumps(pf_payload, default=str) if isinstance(pf_payload, dict) else pf_payload,
            )
        elif code == 'VOLTAGE_DIPS':
            standards = resolve_test_standard_values(form_data.get('voltage_dips_standard', []), 'VOLTAGE_DIPS', form_data.get('productStandards', []))
            vd_payload = form_data.get('voltageDips_custom_spec')
            voltage_dip_custom = _selector_or_custom_value(voltage_dips_config.get('voltageDip'), voltage_dips_config.get('voltageDipCustom'))
            voltage_variations_custom = _selector_or_custom_value(voltage_dips_config.get('voltageVariations'), voltage_dips_config.get('voltageVariationsCustom'))
            short_interruption_custom = _selector_or_custom_value(voltage_dips_config.get('shortInterruption'), voltage_dips_config.get('shortInterruptionCustom'))
            if not vd_payload and voltage_dips_config:
                vd_payload = voltage_dips_config
            test_row.voltage_dips_detail = EMCRequestTestVoltageDips(
                min_value=form_data.get('voltage_dips_min', ''),
                max_value=form_data.get('voltage_dips_max', ''),
                voltage_freq=form_data.get('voltage_dips_voltageFreq', ''),
                voltage_dip1=_first_non_empty(form_data.get('voltage_dips_voltageDip1', ''), voltage_dip_custom),
                voltage_dip2=_first_non_empty(form_data.get('voltage_dips_voltageDip2', ''), voltage_variations_custom),
                voltage_dip3=form_data.get('voltage_dips_voltageDip3', ''),
                interruption=_first_non_empty(form_data.get('voltage_dips_interruption', ''), short_interruption_custom),
                time1=form_data.get('voltage_dips_time1', ''),
                time2=form_data.get('voltage_dips_time2', ''),
                time3=form_data.get('voltage_dips_time3', ''),
                time4=form_data.get('voltage_dips_time4', ''),
                custom_spec=json.dumps(vd_payload, default=str) if isinstance(vd_payload, dict) else vd_payload,
            )

        for index, standard in enumerate(_as_list(standards)):
            standard_value = str(standard).strip() if standard is not None else ''
            if standard_value:
                test_row.standards.append(
                    EMCRequestTestStandard(sort_order=index, standard_value=standard_value)
                )

    return request_obj
