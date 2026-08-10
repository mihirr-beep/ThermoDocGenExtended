# -*- coding: utf-8 -*-
"""Per-model output controls: reasoning effort, verbosity, and a hard cap.

WHY THIS EXISTS
---------------
The agent loop had no output limit at all, which did not matter while the
models were not reasoning models. Measured on gpt-5-nano, same three questions
as gpt-4o-mini, same prompts:

    question              gpt-4o-mini out   gpt-5-nano out
    instruments                      569             2,702
    unfilled + who                   837            23,712
    never scheduled                  879             5,511

Reasoning tokens bill as output, so on nano output went from 10-46% of the
cost to 71-92% of it, and the cheaper input could not cover that: nano was
72% MORE expensive overall despite input being a third of the price. Q2 also
took 129 seconds against 16.

So the knobs the SDK offers are used rather than left at their defaults:

  reasoning.effort  how much thinking to do. 'none' and 'minimal' exist, and
                    on a pipeline where the SQL is already written down for
                    the model - reviewed metrics, declared joins, a lab-rules
                    block - most of that thinking is re-deriving what it was
                    handed.
  verbosity         how much prose to emit for the same answer.
  max_tokens        the cap that was simply missing. A backstop, not a tuning
                    knob: one question emitting 23k tokens is a bug, not a
                    long answer.

Reasoning settings are only sent to models that accept them. gpt-4o rejects
`reasoning` outright, so it is gated on the model name.
"""
import os

# Only these take a reasoning parameter. Prefix match, so gpt-5-nano and any
# dated snapshot are covered.
_REASONING_MODELS = ("gpt-5", "o1", "o3", "o4")

# Effort and verbosity are UNSET by default, on purpose. Measured on the hard
# question: default effort answered 78 (correct), 'low' answered 82, and
# 'minimal' stopped acting altogether - it ran the SQL then asked "do you want
# me to proceed?". The reasoning tokens are what buys the right answer, so the
# model's own default is left alone and these exist only to re-measure.
DEFAULT_EFFORT = ""
DEFAULT_VERBOSITY = ""

# A runaway backstop, not a tuning knob - and it has to clear the real ceiling.
# On a reasoning model this limit counts REASONING tokens, and the question
# this pipeline switched models for emitted 23,712. A 4,000 cap would have cut
# it off mid-thought and returned a truncated answer, which is worse than
# either the cost or the wait.
DEFAULT_MAX_TOKENS = 32000


def supports_reasoning(model):
    m = (model or "").lower()
    return any(m.startswith(p) for p in _REASONING_MODELS)


def for_model(model):
    """ModelSettings for this model, or None to leave the SDK's defaults.

    Env overrides, all optional:
        NLP_REASONING_EFFORT   none | minimal | low | medium | high
        NLP_VERBOSITY          low | medium | high
        NLP_MAX_OUTPUT_TOKENS  integer, or 0 to remove the cap
    """
    try:
        from agents import ModelSettings
    except Exception:  # noqa: BLE001 - SDK absent; caller falls back
        return None

    kwargs = {}

    raw_cap = os.environ.get("NLP_MAX_OUTPUT_TOKENS")
    cap = DEFAULT_MAX_TOKENS if raw_cap is None else int(raw_cap or 0)
    if cap > 0:
        kwargs["max_tokens"] = cap

    if supports_reasoning(model):
        effort = os.environ.get("NLP_REASONING_EFFORT", DEFAULT_EFFORT)
        if effort:
            try:
                from openai.types.shared import Reasoning
                kwargs["reasoning"] = Reasoning(effort=effort)
            except Exception:  # noqa: BLE001 - older SDK; skip rather than fail
                pass
        verbosity = os.environ.get("NLP_VERBOSITY", DEFAULT_VERBOSITY)
        if verbosity:
            kwargs["verbosity"] = verbosity

    if not kwargs:
        return None
    try:
        return ModelSettings(**kwargs)
    except Exception:  # noqa: BLE001 - unknown field on an older SDK
        try:
            return ModelSettings(max_tokens=kwargs.get("max_tokens"))
        except Exception:  # noqa: BLE001
            return None
