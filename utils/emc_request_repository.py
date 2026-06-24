"""Helpers for reading normalized IEC/EMC requests by legacy-facing ids."""

from typing import Optional

from sqlalchemy import func

from models import EMCRequest


RequestRecord = EMCRequest


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
    normalized_tco_id = (tco_id or '').strip()
    if not normalized_tco_id:
        return None

    return EMCRequest.query.filter(
        func.lower(func.trim(EMCRequest.tco_id)) == normalized_tco_id.lower()
    ).first()


def get_request_payload_by_id(request_id: int):
    """Return a legacy-shaped dictionary payload for either schema."""
    record = get_request_by_legacy_or_normalized_id(request_id)
    return record.to_dict() if record else None


def get_request_payload_by_tco_id(tco_id: str):
    """Return a legacy-shaped dictionary payload for either schema."""
    record = get_request_by_tco_id(tco_id)
    return record.to_dict() if record else None
