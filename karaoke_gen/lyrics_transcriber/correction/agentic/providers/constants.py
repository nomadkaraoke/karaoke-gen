"""Constants for the agentic correction providers module."""

# Logging constants
PROMPT_LOG_LENGTH = 200  # Characters to log from prompts
RESPONSE_LOG_LENGTH = 500  # Characters to log from responses

# Model specification format
MODEL_SPEC_FORMAT = "provider/model"  # Expected format for model identifiers

# Default Langfuse host
DEFAULT_LANGFUSE_HOST = "https://us.cloud.langfuse.com"

# Raw response indicator
RAW_RESPONSE_KEY = "raw"  # Key used to wrap unparsed responses

# Error response keys
ERROR_KEY = "error"
ERROR_MESSAGE_KEY = "message"

# Circuit breaker error types
CIRCUIT_OPEN_ERROR = "circuit_open"
MODEL_INIT_ERROR = "model_init_failed"
PROVIDER_ERROR = "provider_error"

