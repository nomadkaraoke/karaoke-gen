"""
OpenTelemetry tracing utilities for lyrics_transcriber library.

This module provides tracing utilities that integrate with the backend's
OpenTelemetry setup. When running in the backend context (Cloud Run),
traces will export to Google Cloud Trace. When running standalone (CLI),
tracing is no-op unless explicitly configured.

Usage:
    from karaoke_gen.lyrics_transcriber.utils.tracing import create_span, traced

    # Context manager for spans
    with create_span("operation_name", {"key": "value"}) as span:
        span.set_attribute("result_count", 42)
        # ... do work ...

    # Decorator for functions
    @traced("custom_name")
    def my_function():
        pass
"""
import logging
from typing import Any, Dict, Optional
from contextlib import contextmanager
from functools import wraps

logger = logging.getLogger(__name__)

# Try to import OpenTelemetry - gracefully handle missing dependency
try:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode, Span
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None
    Status = None
    StatusCode = None
    Span = None


def get_tracer(name: str = "lyrics_transcriber") -> Any:
    """
    Get a tracer instance.

    If OpenTelemetry is available and configured (by the backend),
    returns a real tracer. Otherwise returns a no-op tracer.

    Args:
        name: Tracer name (typically module name)

    Returns:
        Tracer instance
    """
    if OTEL_AVAILABLE and trace:
        return trace.get_tracer(name)
    return None


@contextmanager
def create_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    tracer_name: str = "lyrics_transcriber",
):
    """
    Create a traced span as a context manager.

    If OpenTelemetry is not available or not configured, this is a no-op.

    Usage:
        with create_span("find_anchors", {"word_count": 500}) as span:
            # ... do work ...
            if span:
                span.set_attribute("anchors_found", 42)

    Args:
        name: Span name (appears in Cloud Trace)
        attributes: Initial span attributes
        tracer_name: Tracer name to use

    Yields:
        The active span, or None if tracing is not available
    """
    tracer = get_tracer(tracer_name)

    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                # Only set if value is a valid type
                if isinstance(value, (str, int, float, bool)):
                    span.set_attribute(key, value)
                elif isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float, bool)) for v in value):
                    span.set_attribute(key, list(value))
        try:
            yield span
        except Exception as e:
            if OTEL_AVAILABLE and Status and StatusCode:
                span.set_status(Status(StatusCode.ERROR, str(e)))
                span.record_exception(e)
            raise


def traced(
    name: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None,
    tracer_name: str = "lyrics_transcriber",
):
    """
    Decorator to trace a function.

    If OpenTelemetry is not available or not configured, this is a no-op decorator.

    Usage:
        @traced("process_correction")
        def process_correction(job_id: str):
            ...

        @traced(attributes={"operation": "add-lyrics"})
        async def add_lyrics(job_id: str, source: str):
            ...

    Args:
        name: Span name (defaults to function name)
        attributes: Static attributes to add to span
        tracer_name: Tracer name to use
    """
    def decorator(func):
        span_name = name or func.__name__

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            with create_span(span_name, attributes, tracer_name) as span:
                if span:
                    span.set_attribute("function", func.__name__)
                return func(*args, **kwargs)

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            with create_span(span_name, attributes, tracer_name) as span:
                if span:
                    span.set_attribute("function", func.__name__)
                return await func(*args, **kwargs)

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def add_span_attribute(key: str, value: Any) -> None:
    """
    Add an attribute to the current span.

    If no span is active or tracing is not available, this is a no-op.

    Args:
        key: Attribute name
        value: Attribute value (must be string, int, float, bool, or list thereof)
    """
    if not OTEL_AVAILABLE or not trace:
        return

    span = trace.get_current_span()
    if span and span.is_recording():
        # Validate type
        if isinstance(value, (str, int, float, bool)):
            span.set_attribute(key, value)
        elif isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float, bool)) for v in value):
            span.set_attribute(key, list(value))


def add_span_event(name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
    """
    Add an event to the current span.

    Events are timestamped annotations that appear in the trace timeline.
    If no span is active or tracing is not available, this is a no-op.

    Args:
        name: Event name
        attributes: Event attributes
    """
    if not OTEL_AVAILABLE or not trace:
        return

    span = trace.get_current_span()
    if span and span.is_recording():
        span.add_event(name, attributes=attributes or {})


def set_span_error(error: Exception) -> None:
    """
    Mark the current span as errored.

    If no span is active or tracing is not available, this is a no-op.

    Args:
        error: The exception that occurred
    """
    if not OTEL_AVAILABLE or not trace or not Status or not StatusCode:
        return

    span = trace.get_current_span()
    if span and span.is_recording():
        span.set_status(Status(StatusCode.ERROR, str(error)))
        span.record_exception(error)
