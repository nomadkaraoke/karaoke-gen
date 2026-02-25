"""
Custom exceptions for the karaoke generation backend.

These exceptions are used for structured error handling across the application,
particularly for rate limiting and validation errors.
"""


class RateLimitExceededError(Exception):
    """
    Raised when a rate limit is exceeded.

    Includes information about when the limit will reset for client retry logic.

    Attributes:
        message: Human-readable error message
        limit_type: Type of limit exceeded (e.g., "jobs_per_day", "youtube_uploads")
        remaining_seconds: Seconds until the limit resets (for Retry-After header)
        current_count: Current usage count
        limit_value: The limit that was exceeded
    """

    def __init__(
        self,
        message: str,
        limit_type: str = "unknown",
        remaining_seconds: int = 0,
        current_count: int = 0,
        limit_value: int = 0
    ):
        self.message = message
        self.limit_type = limit_type
        self.remaining_seconds = remaining_seconds
        self.current_count = current_count
        self.limit_value = limit_value
        super().__init__(message)


class EmailValidationError(Exception):
    """
    Raised when email validation fails.

    Attributes:
        message: Human-readable error message
        reason: Specific reason for failure (e.g., "disposable", "blocked", "invalid")
    """

    def __init__(self, message: str, reason: str = "invalid"):
        self.message = message
        self.reason = reason
        super().__init__(message)


class IPBlockedError(Exception):
    """
    Raised when a request comes from a blocked IP address.

    Attributes:
        message: Human-readable error message
        ip_address: The blocked IP address (may be partially masked)
    """

    def __init__(self, message: str, ip_address: str = ""):
        self.message = message
        self.ip_address = ip_address
        super().__init__(message)


class InsufficientCreditsError(Exception):
    """
    Raised when a user attempts to create a job without sufficient credits.

    Attributes:
        message: Human-readable error message
        credits_available: Number of credits the user currently has
        credits_required: Number of credits required (always 1 for now)
    """

    def __init__(
        self,
        message: str,
        credits_available: int = 0,
        credits_required: int = 1
    ):
        self.message = message
        self.credits_available = credits_available
        self.credits_required = credits_required
        super().__init__(message)


class InvalidStateTransitionError(Exception):
    """
    Raised when an invalid job state transition is attempted.

    This exception is raised by JobManager.transition_to_state() when the
    requested transition is not allowed by the state machine defined in
    STATE_TRANSITIONS.

    Example: Trying to transition from PENDING to GENERATING_SCREENS is invalid
    because PENDING can only transition to DOWNLOADING, SEARCHING_AUDIO, FAILED,
    or CANCELLED.

    Attributes:
        message: Human-readable error message
        job_id: The job ID that failed to transition
        from_status: Current status of the job
        to_status: Attempted target status
        valid_transitions: List of valid transitions from current status
    """

    def __init__(
        self,
        message: str,
        job_id: str = "",
        from_status: str = "",
        to_status: str = "",
        valid_transitions: list = None
    ):
        self.message = message
        self.job_id = job_id
        self.from_status = from_status
        self.to_status = to_status
        self.valid_transitions = valid_transitions or []
        super().__init__(message)
