"""Distributed Tracing with OpenTelemetry.

Provides OpenTelemetry spans linking agent invocations, tool executions,
and LLM model calls across the assessment workflow.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

from app.observability.pii_scrubber import scrub_pii

# Initialize TracerProvider if not already configured
_provider = trace.get_tracer_provider()
if not isinstance(_provider, TracerProvider):
    provider = TracerProvider()
    # In development/local, we can log or export spans
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)

tracer = trace.get_tracer("hunger_games_assessment_agent", "1.0.0")


def get_tracer() -> trace.Tracer:
    """Return the global OpenTelemetry tracer instance."""
    return tracer


@contextmanager
def trace_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    span_kind: trace.SpanKind = trace.SpanKind.INTERNAL,
) -> Iterator[trace.Span]:
    """Context manager to create, record, and link OpenTelemetry spans.
    
    Args:
        name: Name of the span (e.g. 'agent.evaluate_correctness', 'tool.mask_pii').
        attributes: Dictionary of attributes to attach to the span.
        span_kind: OpenTelemetry SpanKind.
        
    Yields:
        The active OpenTelemetry Span.
    """
    clean_attrs: Dict[str, Any] = {}
    if attributes:
        for k, v in attributes.items():
            if isinstance(v, str):
                clean_attrs[k] = scrub_pii(v)
            elif isinstance(v, (int, float, bool)):
                clean_attrs[k] = v
            else:
                clean_attrs[k] = scrub_pii(str(v))

    with tracer.start_as_current_span(name, attributes=clean_attrs, kind=span_kind) as span:
        try:
            yield span
            span.set_status(Status(StatusCode.OK))
        except Exception as exc:
            span.set_status(Status(StatusCode.ERROR, description=str(exc)))
            span.record_exception(exc)
            raise
