"""
Worker logging utilities.

Provides logging that sends to both standard output AND Firestore
for remote debugging via the CLI.

Two approaches are provided:
1. JobLogger - A custom logger class for explicit logging in workers
2. JobLogHandler - A logging.Handler that captures logs from any logger (including dependencies)

IMPORTANT: Uses contextvars to ensure log isolation between concurrent jobs.
When multiple jobs run in parallel on the same Cloud Run instance, each job's
worker logs are correctly filtered to only include logs from that job.
"""
import contextvars
import logging
from contextlib import contextmanager
from typing import Optional, Set


# Context variable to track the current job being processed
# This is used to filter logs when multiple jobs run concurrently
_current_job_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    'current_job_id', default=None
)


@contextmanager
def job_logging_context(job_id: str):
    """
    Context manager to set the current job ID for log filtering.
    
    When multiple jobs run concurrently on the same Cloud Run instance,
    this ensures that each job's handler only captures logs from its own
    processing thread/context.
    
    Usage:
        with job_logging_context(job_id):
            # All logs emitted here will be associated with this job_id
            process_job(job_id)
    
    Args:
        job_id: The job ID to set as the current context
    """
    token = _current_job_id.set(job_id)
    try:
        yield
    finally:
        _current_job_id.reset(token)


class JobLogHandler(logging.Handler):
    """
    A logging handler that captures log records and stores them in Firestore.
    
    This handler can be added to any logger (including dependency loggers like
    LyricsTranscriber) to capture their output for remote debugging.
    
    Usage:
        # Capture logs from LyricsTranscriber and its sub-components
        handler = JobLogHandler(job_id, "lyrics", job_manager)
        
        # Add to the logger that will be passed to LyricsTranscriber
        lyrics_logger = logging.getLogger("karaoke_gen.lyrics_processor")
        lyrics_logger.addHandler(handler)
        
        # Also capture logs from lyrics_transcriber package itself
        lt_logger = logging.getLogger("karaoke_gen.lyrics_transcriber")
        lt_logger.addHandler(handler)
    """
    
    def __init__(self, job_id: str, worker_name: str, job_manager, level: int = logging.INFO):
        """
        Initialize the job log handler.
        
        Args:
            job_id: Job ID to log to
            worker_name: Worker name for log entries
            job_manager: JobManager instance for Firestore access
            level: Minimum log level to capture (default INFO)
        """
        super().__init__(level)
        self.job_id = job_id
        self.worker_name = worker_name
        self.job_manager = job_manager
        
        # Track which messages we've already logged to avoid duplicates
        self._logged_messages: Set[str] = set()
    
    def emit(self, record: logging.LogRecord) -> None:
        """Process a log record."""
        try:
            # CRITICAL: Filter by current job context to prevent log mixing
            # When multiple jobs run concurrently, each attaches handlers to
            # the same global loggers. Without this check, logs from Job A
            # would be captured by Job B's handler and vice versa.
            current_job = _current_job_id.get()
            if current_job is not None and current_job != self.job_id:
                # This log is from a different job's context, skip it
                return
            
            # Format the log message
            message = self.format(record)
            
            # Create a dedup key (to avoid duplicate messages from multiple handlers)
            dedup_key = f"{record.created}:{record.levelname}:{message[:100]}"
            if dedup_key in self._logged_messages:
                return
            self._logged_messages.add(dedup_key)
            
            # Keep the set from growing unbounded
            if len(self._logged_messages) > 1000:
                self._logged_messages = set(list(self._logged_messages)[-500:])
            
            # Store in Firestore
            self.job_manager.append_worker_log(
                job_id=self.job_id,
                worker=self.worker_name,
                level=record.levelname,
                message=message
            )
        except Exception:
            # Don't let logging errors break the worker
            self.handleError(record)


class JobLogger:
    """
    Logger that writes to both standard logging and Firestore job logs.
    
    This allows worker logs to be viewed remotely via the CLI.
    
    Usage:
        job_logger = JobLogger(job_id, "audio", job_manager)
        job_logger.info("Processing audio...")
        job_logger.error("Failed to process", exc_info=True)
    """
    
    def __init__(self, job_id: str, worker_name: str, job_manager):
        """
        Initialize job logger.
        
        Args:
            job_id: Job ID to log to
            worker_name: Worker name (audio, lyrics, screens, video, render)
            job_manager: JobManager instance for Firestore access
        """
        self.job_id = job_id
        self.worker_name = worker_name
        self.job_manager = job_manager
        self._logger = logging.getLogger(f"worker.{worker_name}.{job_id}")
    
    def _log(self, level: str, message: str, *args, **kwargs):
        """Internal logging method."""
        # Format message with args if provided
        if args:
            try:
                formatted_message = message % args
            except (TypeError, ValueError):
                formatted_message = f"{message} {args}"
        else:
            formatted_message = message
        
        # Log to standard logging
        log_method = getattr(self._logger, level.lower())
        log_method(formatted_message)
        
        # Also append to Firestore job logs (async-safe)
        try:
            self.job_manager.append_worker_log(
                job_id=self.job_id,
                worker=self.worker_name,
                level=level.upper(),
                message=formatted_message
            )
        except Exception as e:
            # Don't let Firestore errors break worker processing
            self._logger.warning(f"Failed to append job log: {e}")
    
    def debug(self, message: str, *args, **kwargs):
        """Log debug message."""
        self._log("DEBUG", message, *args, **kwargs)
    
    def info(self, message: str, *args, **kwargs):
        """Log info message."""
        self._log("INFO", message, *args, **kwargs)
    
    def warning(self, message: str, *args, **kwargs):
        """Log warning message."""
        self._log("WARNING", message, *args, **kwargs)
    
    def error(self, message: str, *args, exc_info: bool = False, **kwargs):
        """Log error message."""
        self._log("ERROR", message, *args, **kwargs)
        if exc_info:
            import traceback
            tb = traceback.format_exc()
            if tb and tb != "NoneType: None\n":
                self._log("ERROR", f"Traceback:\n{tb}")
    
    def exception(self, message: str, *args, **kwargs):
        """Log exception with traceback."""
        self.error(message, *args, exc_info=True, **kwargs)


def create_job_logger(job_id: str, worker_name: str) -> JobLogger:
    """
    Create a JobLogger for a worker.
    
    This is a convenience function that creates the JobManager internally.
    
    Args:
        job_id: Job ID
        worker_name: Worker name
        
    Returns:
        JobLogger instance
    """
    from backend.services.job_manager import JobManager
    job_manager = JobManager()
    return JobLogger(job_id, worker_name, job_manager)


def setup_job_logging(job_id: str, worker_name: str, *logger_names: str) -> JobLogHandler:
    """
    Set up job logging for a worker and its dependencies.
    
    This adds a JobLogHandler to capture logs from specified loggers
    (including dependency loggers like lyrics_transcriber).
    
    IMPORTANT: When using this function, wrap your job processing code in
    `job_logging_context(job_id)` to ensure proper log isolation when multiple
    jobs run concurrently on the same Cloud Run instance.
    
    Args:
        job_id: Job ID
        worker_name: Worker name
        *logger_names: Names of loggers to capture (e.g., "karaoke_gen.lyrics_transcriber", "karaoke_gen")
        
    Returns:
        The JobLogHandler (can be removed later if needed)
        
    Example:
        # In lyrics_worker.py:
        handler = setup_job_logging(
            job_id, 
            "lyrics",
            "karaoke_gen.lyrics_processor",
            "karaoke_gen.lyrics_transcriber",  # Capture LyricsTranscriber logs
        )
        
        # IMPORTANT: Use job_logging_context for proper isolation
        with job_logging_context(job_id):
            # ... do work ...
            pass
        
        # Optional: remove handler when done
        for name in logger_names:
            logging.getLogger(name).removeHandler(handler)
    """
    from backend.services.job_manager import JobManager
    
    job_manager = JobManager()
    handler = JobLogHandler(job_id, worker_name, job_manager, level=logging.INFO)
    
    # Simple formatter that just shows the message
    formatter = logging.Formatter('%(message)s')
    handler.setFormatter(formatter)
    
    # Add handler to all specified loggers
    for logger_name in logger_names:
        logger = logging.getLogger(logger_name)
        logger.addHandler(handler)
        # Ensure logger level allows INFO messages through
        if logger.level > logging.INFO or logger.level == logging.NOTSET:
            logger.setLevel(logging.INFO)
    
    return handler
