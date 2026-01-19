"""Retry execution logic with exponential backoff."""
from __future__ import annotations

import time
import random
import logging
from typing import Callable, TypeVar, Generic
from dataclasses import dataclass

from .config import ProviderConfig

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class ExecutionResult(Generic[T]):
    """Result of a retry execution attempt.
    
    Attributes:
        success: Whether execution succeeded
        value: The return value if successful
        error: Error message if failed
        attempts: Number of attempts made
    """
    success: bool
    value: T | None = None
    error: str | None = None
    attempts: int = 0


class RetryExecutor:
    """Executes operations with retry logic and exponential backoff.
    
    Implements exponential backoff with jitter to prevent thundering herd.
    
    Single Responsibility: Retry logic only, no model-specific behavior.
    """
    
    def __init__(self, config: ProviderConfig):
        """Initialize retry executor with configuration.
        
        Args:
            config: Provider configuration with retry parameters
        """
        self._config = config
    
    def execute_with_retry(
        self,
        operation: Callable[[], T],
        operation_name: str = "operation"
    ) -> ExecutionResult[T]:
        """Execute operation with retry logic.
        
        Args:
            operation: Callable that performs the operation
            operation_name: Name for logging purposes
            
        Returns:
            ExecutionResult with success/failure status and value/error
        """
        max_attempts = max(1, self._config.max_retries + 1)
        last_error: Exception | None = None
        
        for attempt in range(max_attempts):
            try:
                logger.debug(
                    f"🤖 Executing {operation_name} "
                    f"(attempt {attempt + 1}/{max_attempts})"
                )
                
                result = operation()
                
                logger.debug(f"🤖 {operation_name} succeeded on attempt {attempt + 1}")
                return ExecutionResult(
                    success=True,
                    value=result,
                    attempts=attempt + 1
                )
                
            except Exception as e:
                last_error = e
                logger.warning(
                    f"🤖 {operation_name} failed on attempt {attempt + 1}: {e}"
                )
                
                # Don't sleep after the last attempt
                if attempt < max_attempts - 1:
                    sleep_duration = self._calculate_backoff(attempt)
                    logger.debug(f"🤖 Backing off for {sleep_duration:.2f}s")
                    time.sleep(sleep_duration)
        
        # All attempts failed
        error_msg = str(last_error) if last_error else "unknown error"
        logger.error(
            f"🤖 {operation_name} failed after {max_attempts} attempts: {error_msg}"
        )
        
        return ExecutionResult(
            success=False,
            error=error_msg,
            attempts=max_attempts
        )
    
    def _calculate_backoff(self, attempt: int) -> float:
        """Calculate backoff duration with exponential backoff and jitter.
        
        Formula: base * (factor ^ attempt) + random_jitter
        
        Args:
            attempt: Current attempt number (0-indexed)
            
        Returns:
            Sleep duration in seconds
        """
        base = self._config.retry_backoff_base_seconds
        factor = self._config.retry_backoff_factor
        
        # Exponential backoff
        backoff = base * (factor ** attempt)
        
        # Add jitter (0-50ms) to prevent thundering herd
        jitter = random.uniform(0, 0.05)
        
        return backoff + jitter

