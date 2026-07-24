"""Helpers for reading normalized IEC/EMC requests by legacy-facing ids."""

import re
from typing import Optional

from sqlalchemy import func

from models import EMCRequest


RequestRecord = EMCRequest
_TCO_NUMERIC_TOKEN_RE = re.compile(r'^(?:IECEMC|EMC|TCO)?(\d+)$', re.IGNORECASE)


def get_tco_lookup_candidates(tco_id: str) -> list[str]:
    """Return normalized TCO aliases for legacy lookup/search compatibility."""
    normalized_tco_id = (tco_id or '').strip()
    if not normalized_tco_id:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: str) -> None:
        cleaned_value = str(value or '').strip()
        normalized_key = cleaned_value.lower()
        if not cleaned_value or normalized_key in seen:
            return
        seen.add(normalized_key)
        candidates.append(cleaned_value)

    add_candidate(normalized_tco_id)

    collapsed = re.sub(r'[\s_-]+', '', normalized_tco_id).upper()
    token_match = _TCO_NUMERIC_TOKEN_RE.match(collapsed)
    if token_match:
        sequence_number = int(token_match.group(1))
        sequence_text = f'{sequence_number:03d}'
        add_candidate(f'IEC-EMC-{sequence_text}')
        add_candidate(f'EMC-{sequence_text}')
        add_candidate(f'TCO-{sequence_text}')
        add_candidate(sequence_text)

    return candidates


def get_request_by_legacy_or_normalized_id(request_id: int) -> Optional[RequestRecord]:
    """Return a normalized request by normalized id, then legacy id as fallback.

    Internal UI routes now pass normalized ``EMCRequest.id`` values. During the
    migration window those numeric ids can overlap with another row's
    ``legacy_request_id``; querying both columns at once can therefore resolve
    the wrong request. Prefer the normalized primary key first and only fall
    back to the migrated legacy id when no direct id match exists.
    """
    direct_match = EMCRequest.query.filter_by(id=request_id).first()
    if direct_match is not None:
        return direct_match
    return EMCRequest.query.filter_by(legacy_request_id=request_id).first()


def get_request_by_tco_id(tco_id: str) -> Optional[RequestRecord]:
    """Return a normalized request by TCO id."""
    candidates = get_tco_lookup_candidates(tco_id)
    if not candidates:
        return None

    normalized_candidates = [candidate.lower() for candidate in candidates]
    candidate_priority = {
        candidate.lower(): index for index, candidate in enumerate(candidates)
    }

    matches = EMCRequest.query.filter(
        func.lower(func.trim(EMCRequest.tco_id)).in_(normalized_candidates)
    ).all()
    if not matches:
        return None

    return min(
        matches,
        key=lambda record: (
            candidate_priority.get(
                str(getattr(record, 'tco_id', '') or '').strip().lower(),
                len(candidate_priority)
            ),
            getattr(record, 'id', 0) or 0,
        )
    )


def get_request_payload_by_id(request_id: int):
    """Return a legacy-shaped dictionary payload for either schema."""
    record = get_request_by_legacy_or_normalized_id(request_id)
    return record.to_dict() if record else None


def get_request_payload_by_tco_id(tco_id: str):
    """Return a legacy-shaped dictionary payload for either schema."""
    record = get_request_by_tco_id(tco_id)
    return record.to_dict() if record else None

