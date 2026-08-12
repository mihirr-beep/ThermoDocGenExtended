# -*- coding: utf-8 -*-
"""Langfuse observability for the NL search (OpenTelemetry via logfire).

Langfuse Cloud exposes an OTEL trace endpoint, so we export the OpenAI Agents
SDK spans (agent runs, LLM generations, tool calls) and the raw OpenAI calls
to Langfuse over OTLP. logfire is used purely as the OTEL SDK /
instrumentation layer — nothing is sent to Logfire's own cloud
(``send_to_logfire=False``).

Enabled only when LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY are present;
otherwise this is a no-op and the feature runs untraced. Fully best-effort:
a tracing failure never affects search.

Env:
    LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY   (required to enable)
    LANGFUSE_BASE_URL   (or LANGFUSE_HOST; default https://cloud.langfuse.com)
    LANGFUSE_SERVICE_NAME  (optional span service name)
"""
import base64
import importlib.util
import sys
import logging
import os

_log = logging.getLogger("nlp_search.tracing")
_configured = None  # None = not attempted; True/False = result


def _has_logfire():
    return importlib.util.find_spec("logfire") is not None


def available():
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def is_active():
    return bool(_configured)


def _base_url():
    return (os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST")
            or "https://cloud.langfuse.com").rstrip("/")


def setup_tracing():
    """Configure Langfuse OTEL export + instrument the Agents SDK and OpenAI.
    Idempotent; returns True if tracing is active. Never raises."""
    global _configured
    if _configured is not None:
        return _configured
    if not available():
        _configured = False
        return False
    # logfire is the OTEL SDK this exports through, and it is optional: say so once and
    # carry on untraced rather than failing the search.
    if not _has_logfire():
        _log.info(
            "Langfuse tracing disabled: optional dependency 'logfire' is not installed "
            "for the active interpreter (%s). Continuing untraced.",
            sys.executable,
        )
        _configured = False
        return False
    try:
        pk = os.environ["LANGFUSE_PUBLIC_KEY"]
        sk = os.environ["LANGFUSE_SECRET_KEY"]
        auth = base64.b64encode(("%s:%s" % (pk, sk)).encode()).decode()
        # Point OTLP at Langfuse's OTEL endpoint (HTTP/protobuf). setdefault so an
        # explicit OTEL_* env set by the operator wins.
        os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", _base_url() + "/api/public/otel")
        os.environ.setdefault("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Basic " + auth)
        os.environ.setdefault("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")

        import logfire
        logfire.configure(
            service_name=os.environ.get("LANGFUSE_SERVICE_NAME", "emc-nl-search"),
            send_to_logfire=False,   # export to Langfuse only, not Logfire cloud
            console=False,
            inspect_arguments=False,  # we pass attributes explicitly; skip f-string introspection
        )

        # The Agents SDK's own tracing must be ENABLED for logfire's provider
        # wrapper to emit spans; but clear its default processors first so lab
        # data is NOT also shipped to OpenAI's trace backend — Langfuse only.
        try:
            from agents import set_trace_processors, set_tracing_disabled
            set_trace_processors([])
            set_tracing_disabled(False)
        except Exception as exc:  # noqa: BLE001
            _log.warning("Agents SDK trace toggle failed: %s", exc)

        logfire.instrument_openai_agents()
        try:
            logfire.instrument_openai()   # capture the raw OpenAI calls too
        except Exception:  # noqa: BLE001
            pass

        _configured = True
        _log.info("Langfuse tracing enabled -> %s", _base_url())
    except Exception as exc:  # noqa: BLE001 - tracing must never break search
        _log.warning("Langfuse tracing setup failed (%s); continuing untraced", exc)
        _configured = False
    return _configured


def flush():
    """Force-flush pending spans (for short-lived processes: CLI, bg threads)."""
    if not _configured:
        return
    try:
        import logfire
        logfire.force_flush()
    except Exception:  # noqa: BLE001
        pass
