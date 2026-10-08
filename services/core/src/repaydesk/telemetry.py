"""OpenTelemetry setup shared by every Python service.

Exports over OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set (it is, inside compose);
otherwise tracing is a no-op, so tests and scripts need nothing running.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_provider: TracerProvider | None = None


def setup(service_name: str) -> TracerProvider | None:
    global _provider
    if _provider or not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return _provider
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    resource = Resource.create({"service.name": os.getenv("OTEL_SERVICE_NAME", service_name)})
    _provider = TracerProvider(resource=resource)
    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(_provider)
    return _provider


def shutdown() -> None:
    if _provider:
        _provider.shutdown()


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else None
