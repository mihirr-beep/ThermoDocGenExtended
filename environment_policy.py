"""Application-environment policy helpers.

Keep environment resolution and production-only feature decisions independent
from Flask so they can be validated without starting the application or
connecting to the database.
"""

from __future__ import annotations

import os
from collections.abc import Mapping


PRODUCTION_ENVIRONMENT = "production"


def _normalize_environment_name(value: object) -> str:
    return str(value or "").strip().lower()


def resolve_application_environment(
    config_name: object = None,
    *,
    environ: Mapping[str, str] | None = None,
    default: str = PRODUCTION_ENVIRONMENT,
    valid_names: object = None,
) -> str:
    """Resolve the configured application environment.

    Environment variables take precedence over an explicitly supplied config
    name. When neither is present, ``default`` is used. Invalid names fall back
    to the configured default instead of silently enabling development-only
    behavior in production.
    """

    source = os.environ if environ is None else environ
    default_name = _normalize_environment_name(default) or PRODUCTION_ENVIRONMENT
    resolved_name = _normalize_environment_name(
        source.get("APP_ENV")
        or source.get("FLASK_ENV")
        or config_name
        or default_name
    )

    if valid_names is None:
        return resolved_name or default_name

    valid = {_normalize_environment_name(name) for name in valid_names}
    if resolved_name in valid:
        return resolved_name
    if default_name in valid:
        return default_name
    if "default" in valid:
        return "default"
    return next(iter(valid), default_name)


def datasheet_generation_enabled(environment_name: object) -> bool:
    """Return whether assigned-test datasheets may be generated in-app."""

    return _normalize_environment_name(environment_name) != PRODUCTION_ENVIRONMENT
