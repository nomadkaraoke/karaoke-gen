"""Circuit breaker pattern implementation for AI provider reliability."""
from __future__ import annotations

import time
import logging
from typing import Dict

from .config import ProviderConfig

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit breaker for protecting against cascading failures.
    
    Tracks failures per model and temporarily stops requests when
    failure threshold is exceeded. Automatically resets after a timeout.
    
    Single Responsibility: Failure tracking and circuit state management only.
    """
    
    def __init__(self, config: ProviderConfig):
        """Initialize circuit breaker with configuration.
        
        Args:
            config: Provider configuration with thresholds and timeouts
        """
        self._config = config
        self._failures: Dict[str, int] = {}
        self._open_until: Dict[str, float] = {}
    
    def is_open(self, model: str) -> bool:
        """Check if circuit breaker is open for this model.
        
        An open circuit means requests should be rejected immediately
        to prevent cascading failures.
        
        Args:
            model: Model identifier to check
            
        Returns:
            True if circuit is open (reject requests), False if closed (allow)
        """
        now = time.time()
        open_until = self._open_until.get(model, 0)
        
        if now < open_until:
            remaining = int(open_until - now)
            logger.debug(
                f"🤖 Circuit breaker open for {model}, "
                f"retry in {remaining}s"
            )
            return True
        
        # Circuit was open but timeout expired - close it
        if model in self._open_until:
            logger.info(f"🤖 Circuit breaker closed for {model} (timeout expired)")
            del self._open_until[model]
            self._failures[model] = 0
        
        return False
    
    def get_open_until(self, model: str) -> float:
        """Get timestamp when circuit will close for this model.
        
        Args:
            model: Model identifier
            
        Returns:
            Unix timestamp when circuit will close, or 0 if not open
        """
        return self._open_until.get(model, 0)
    
    def record_failure(self, model: str) -> None:
        """Record a failure for this model and maybe open the circuit.
        
        Args:
            model: Model identifier that failed
        """
        self._failures[model] = self._failures.get(model, 0) + 1
        failure_count = self._failures[model]
        
        logger.debug(
            f"🤖 Recorded failure for {model}, "
            f"total: {failure_count}"
        )
        
        # Check if we should open the circuit
        threshold = self._config.circuit_breaker_failure_threshold
        if failure_count >= threshold:
            self._open_circuit(model)
    
    def record_success(self, model: str) -> None:
        """Record a successful call and reset failure count.
        
        Args:
            model: Model identifier that succeeded
        """
        if model in self._failures and self._failures[model] > 0:
            logger.debug(
                f"🤖 Reset failure count for {model} "
                f"(was {self._failures[model]})"
            )
        self._failures[model] = 0
    
    def _open_circuit(self, model: str) -> None:
        """Open the circuit breaker for this model.
        
        Args:
            model: Model identifier to open circuit for
        """
        open_seconds = self._config.circuit_breaker_open_seconds
        self._open_until[model] = time.time() + open_seconds
        
        logger.warning(
            f"🤖 Circuit breaker opened for {model} "
            f"({self._failures[model]} failures >= "
            f"{self._config.circuit_breaker_failure_threshold} threshold), "
            f"will retry in {open_seconds}s"
        )
    
    def reset(self, model: str) -> None:
        """Manually reset circuit breaker for a model.
        
        Useful for testing or administrative reset.
        
        Args:
            model: Model identifier to reset
        """
        self._failures[model] = 0
        if model in self._open_until:
            del self._open_until[model]
        logger.info(f"🤖 Circuit breaker manually reset for {model}")
    
    def get_failure_count(self, model: str) -> int:
        """Get current failure count for a model.
        
        Args:
            model: Model identifier
            
        Returns:
            Number of consecutive failures
        """
        return self._failures.get(model, 0)

