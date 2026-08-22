"""LLM access layer.

Wapas is provider-agnostic by construction. The diagnosis and planning steps
speak to an :class:`~wapas.llm.base.LLMProvider`; whether that is NVIDIA NIM
serving an open model, Anthropic serving Claude, or a deterministic fake used
by the test suite is one line of configuration.

Why the indirection is not over-engineering
-------------------------------------------
Structured-output support varies sharply across open models. Measured on the
NVIDIA catalogue on 2026-08-22 with an identical diagnosis prompt:

===============================================  ==============  ============
model                                            json_schema     json_object
===============================================  ==============  ============
``openai/gpt-oss-120b``                          hangs           works, correct
``nvidia/nemotron-3-super-120b-a12b``            works, *wrong*  —
``meta/llama-3.3-70b-instruct``                  hangs           hangs
===============================================  ==============  ============

So the layer negotiates capability per model and degrades through a ladder —
strict schema, then JSON mode, then prompted JSON with extraction — and
validates every response against the Pydantic model regardless of which rung
produced it. A response that fails validation is retried with the validation
error fed back to the model.

This is also why the model choice is an *evaluated* decision rather than a
preference: ``eval/model_bakeoff.py`` scores candidates on a labelled diagnosis
set and publishes the confusion matrix.
"""

from .base import LLMProvider, LLMResponse, StructuredMode, Usage
from .costs import CostBook, cost_paise
from .fake import FakeProvider
from .openai_compat import OpenAICompatProvider
from .structured import StructuredCallError, ask_structured

__all__ = [
    "CostBook",
    "FakeProvider",
    "LLMProvider",
    "LLMResponse",
    "OpenAICompatProvider",
    "StructuredCallError",
    "StructuredMode",
    "Usage",
    "ask_structured",
    "cost_paise",
]
