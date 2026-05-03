"""
Structured logging with trace correlation for Cloud Logging.

This module provides JSON-formatted logging that integrates with Google Cloud Logging
and correlates logs with OpenTelemetry traces in Cloud Trace.

When running in Cloud Run:
- Logs are output as JSON for Cloud Logging to parse
- Each log entry includes trace ID and span ID for correlation
- Custom fields (job_id, worker) are preserved in the log structure

Usage:
    # In main.py, before any logging:
    from backend.services.structured_logging import setup_structured_logging
    setup_structured_logging()
    
    # Then use standard logging:
    logger = logging.getLogger(__name__)
    logger.info("Processing started", extra={"job_id": "abc123"})
"""
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, Optional


# Cloud Logging severity mapping (compatible with GCP)
# https://cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry#LogSeverity
SEVERITY_MAP = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}


# Standard LogRecord attributes set by the logging module itself. Anything
# else on a record came from the caller's `extra=` dict and should be
# forwarded to Cloud Logging.
_RESERVED_LOG_RECORD_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
})


class StructuredFormatter(logging.Formatter):
    """
    JSON formatter with trace correlation for Google Cloud Logging.
    
    Produces log entries in the format expected by Cloud Logging, including:
    - logging.googleapis.com/trace: Links to Cloud Trace
    - logging.googleapis.com/spanId: Current span ID
    - severity: Cloud Logging severity level
    - Custom fields passed via 'extra' dict
    """
    
    def __init__(self, project_id: Optional[str] = None):
        """
        Initialize the structured formatter.
        
        Args:
            project_id: GCP project ID for trace URLs (auto-detected if not provided)
        """
        super().__init__()
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
    
    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record as JSON.
        
        Args:
            record: The log record to format
            
        Returns:
            JSON string for Cloud Logging
        """
        from backend.services.tracing import get_current_trace_id, get_current_span_id
        
        # Build base log entry
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "severity": SEVERITY_MAP.get(record.levelname, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
        }
        
        # Add trace correlation if available
        trace_id = get_current_trace_id()
        span_id = get_current_span_id()
        
        if trace_id and self.project_id:
            # Format for Cloud Logging trace correlation
            log_entry["logging.googleapis.com/trace"] = f"projects/{self.project_id}/traces/{trace_id}"
        
        if span_id:
            log_entry["logging.googleapis.com/spanId"] = span_id
        
        # Forward any custom field passed via `extra=` on the log call.
        # Use a denylist of standard LogRecord attributes rather than an
        # allowlist — otherwise new diagnostic fields silently drop on the
        # floor and feature owners discover this only when debugging prod.
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS or key.startswith("_"):
                continue
            if value is None:
                continue
            log_entry[key] = value
        
        # Add source location for debugging
        if record.pathname and record.lineno:
            log_entry["logging.googleapis.com/sourceLocation"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Remove None values for cleaner output
        return json.dumps({k: v for k, v in log_entry.items() if v is not None})


class HumanReadableFormatter(logging.Formatter):
    """
    Human-readable formatter for local development.
    
    Includes trace context when available but outputs in traditional format.
    """
    
    def format(self, record: logging.LogRecord) -> str:
        """Format a log record for human readability."""
        from backend.services.tracing import get_current_trace_id
        
        # Build base message
        timestamp = datetime.utcfromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        
        # Add job_id prefix if available
        job_id = getattr(record, "job_id", None)
        job_prefix = f"[job:{job_id[:8]}] " if job_id else ""
        
        # Add trace ID suffix if available
        trace_id = get_current_trace_id()
        trace_suffix = f" [trace:{trace_id[:8]}]" if trace_id else ""
        
        message = f"{timestamp} - {record.name} - {record.levelname} - {job_prefix}{record.getMessage()}{trace_suffix}"
        
        # Add exception info if present
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        
        return message


def is_cloud_run() -> bool:
    """Check if we're running in Cloud Run."""
    return os.environ.get("K_SERVICE") is not None


def setup_structured_logging(force_json: bool = False, log_level: Optional[str] = None) -> None:
    """
    Configure structured logging for the application.
    
    In Cloud Run: Uses JSON format with trace correlation for Cloud Logging
    In development: Uses human-readable format with optional trace context
    
    Args:
        force_json: Force JSON output even in development
        log_level: Override log level (default from settings or INFO)
    """
    from backend.config import settings
    
    # Determine log level
    level = log_level or getattr(settings, "log_level", "INFO")
    level = getattr(logging, level.upper(), logging.INFO)
    
    # Choose formatter based on environment
    if is_cloud_run() or force_json:
        formatter = StructuredFormatter()
    else:
        formatter = HumanReadableFormatter()
    
    # Create handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    handler.setLevel(level)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Remove existing handlers and add our handler
    root_logger.handlers = [handler]
    
    # Also configure uvicorn loggers to use our format
    for logger_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        logger = logging.getLogger(logger_name)
        logger.handlers = [handler]
        logger.propagate = False


class JobLogAdapter(logging.LoggerAdapter):
    """
    Logger adapter that automatically adds job context to log records.
    
    Usage:
        logger = logging.getLogger(__name__)
        job_logger = JobLogAdapter(logger, job_id="abc123", worker="audio")
        job_logger.info("Processing started")  # Automatically includes job_id and worker
    """
    
    def __init__(self, logger: logging.Logger, job_id: str, worker: Optional[str] = None, **extra):
        """
        Initialize the adapter.
        
        Args:
            logger: Base logger instance
            job_id: Job ID to include in all log records
            worker: Optional worker name
            **extra: Additional fields to include in all log records
        """
        super().__init__(logger, {"job_id": job_id, "worker": worker, **extra})
    
    def process(self, msg: str, kwargs: Dict[str, Any]) -> tuple:
        """Process a logging call to add extra context."""
        # Merge extra dict from adapter with any extra passed to the log call
        extra = kwargs.get("extra", {})
        extra.update(self.extra)
        kwargs["extra"] = extra
        return msg, kwargs


def get_job_logger(job_id: str, worker: Optional[str] = None, name: Optional[str] = None) -> JobLogAdapter:
    """
    Get a logger adapter configured for a specific job.
    
    This is a convenience function for creating job-specific loggers.
    
    Args:
        job_id: Job ID to include in all log records
        worker: Optional worker name
        name: Logger name (defaults to "job")
        
    Returns:
        JobLogAdapter configured for the job
        
    Usage:
        logger = get_job_logger("abc123", "audio")
        logger.info("Starting audio separation")
        logger.error("Failed to process", extra={"error": str(e)})
    """
    base_logger = logging.getLogger(name or "job")
    return JobLogAdapter(base_logger, job_id=job_id, worker=worker)

