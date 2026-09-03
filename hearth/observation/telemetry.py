"""OpenTelemetry tracing for Hearth — official SDK backed by the fleet AM4 Jaeger sink.

Context propagation uses the standard W3C TraceContextTextMapPropagator so
traceparent headers injected into sidecar job manifests are understood natively
by the .NET agent (System.Diagnostics.ActivityContext.TryParse).

Never raises from span emission.  The BatchSpanProcessor handles retries and
backpressure internally; if AM4 is unreachable, spans are silently dropped
rather than blocking the control plane (ADR-0002 pattern).
"""

from __future__ import annotations

import atexit
import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_OTLP_ENDPOINT = os.environ.get(
    "OTEL_EXPORTER_OTLP_ENDPOINT", "http://192.168.12.233:4318"
)
_SERVICE_NAME = os.environ.get("OTEL_SERVICE_NAME", "hearth")

# ---------------------------------------------------------------------------
# Provider bootstrap (module-level singleton, idempotent)
# ---------------------------------------------------------------------------

_resource = Resource.create({
    "service.name": _SERVICE_NAME,
    "host.name": os.environ.get("COMPUTERNAME", "unknown"),
})

_provider = TracerProvider(resource=_resource)
_exporter = OTLPSpanExporter(endpoint=_OTLP_ENDPOINT + "/v1/traces")
_provider.add_span_processor(BatchSpanProcessor(_exporter))

# Register as the global TracerProvider so any library-level instrumentation
# also routes to AM4.
trace.set_tracer_provider(_provider)
atexit.register(_provider.shutdown)

_tracer = trace.get_tracer("hearth.observation", "1.0.0")
_propagator = TraceContextTextMapPropagator()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_tracer() -> trace.Tracer:
    """Return the shared Hearth tracer instance."""
    return _tracer


@contextmanager
def trace_span(
    name: str,
    *,
    attributes: Optional[Dict[str, Any]] = None,
    parent_traceparent: Optional[str] = None,
) -> Iterator[trace.Span]:
    """Context manager that opens a span and yields it.

    If *parent_traceparent* is supplied (a W3C ``traceparent`` header value),
    the span is created as a child of that remote context — even if the
    current in-process context has no active span.  This is used by the
    ingestion layer to continue a trace that started in a different service.

    The span is automatically ended and exported on exit.  Exceptions set the
    span status to ERROR and are re-raised.

    Usage::

        with trace_span("hearth.arcserve.drain", attributes={"arc.slots": 4}) as span:
            drain_slots()
            span.set_attribute("drain.result", "idle")
    """
    ctx: Optional[Context] = None
    if parent_traceparent:
        carrier = {"traceparent": parent_traceparent}
        ctx = _propagator.extract(carrier)

    with _tracer.start_as_current_span(
        name,
        context=ctx,
        attributes=attributes or {},
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(trace.StatusCode.ERROR, str(exc))
            span.record_exception(exc)
            raise


def get_current_traceparent() -> Optional[str]:
    """Extract W3C ``traceparent`` from the current in-process context.

    Returns ``None`` when no span is active.  The returned string is suitable
    for injecting into sidecar job manifests so the .NET agent can continue
    the trace.
    """
    carrier: Dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent")
