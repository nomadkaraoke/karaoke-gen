"""Refactored LangChain-based provider bridge using composition.

This is a much cleaner version that delegates to specialized components:
- ModelFactory: Creates ChatModels
- CircuitBreaker: Manages failure state
- ResponseParser: Parses responses
- RetryExecutor: Handles retry logic
- ResponseCache: Caches LLM responses to avoid redundant calls

Each component has a single responsibility and is independently testable.
"""
from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import List, Dict, Any, Optional
from datetime import datetime

from .base import BaseAIProvider
from .config import ProviderConfig
from .model_factory import ModelFactory
from .circuit_breaker import CircuitBreaker
from .response_parser import ResponseParser
from .retry_executor import RetryExecutor
from .response_cache import ResponseCache
from .constants import (
    PROMPT_LOG_LENGTH,
    RESPONSE_LOG_LENGTH,
    CIRCUIT_OPEN_ERROR,
    MODEL_INIT_ERROR,
    PROVIDER_ERROR,
)

logger = logging.getLogger(__name__)

# Error constant for initialization timeout
INIT_TIMEOUT_ERROR = "initialization_timeout"


class InitializationTimeoutError(Exception):
    """Raised when model initialization exceeds the configured timeout."""
    pass


class LangChainBridge(BaseAIProvider):
    """Provider bridge using LangChain ChatModels with reliability patterns.
    
    This bridge is now much simpler - it delegates to specialized components
    rather than handling everything itself. This follows the Single 
    Responsibility Principle and makes the code more testable.
    
    Components:
        - ModelFactory: Creates and configures ChatModels
        - CircuitBreaker: Protects against cascading failures
        - ResponseParser: Handles JSON/raw response parsing
        - RetryExecutor: Implements exponential backoff
    """
    
    def __init__(
        self,
        model: str,
        config: ProviderConfig | None = None,
        model_factory: ModelFactory | None = None,
        circuit_breaker: CircuitBreaker | None = None,
        response_parser: ResponseParser | None = None,
        retry_executor: RetryExecutor | None = None,
        response_cache: ResponseCache | None = None,
    ):
        """Initialize the bridge with components (dependency injection).
        
        Args:
            model: Model identifier in format "provider/model"
            config: Provider configuration (creates default if None)
            model_factory: Factory for creating ChatModels (creates default if None)
            circuit_breaker: Circuit breaker instance (creates default if None)
            response_parser: Response parser instance (creates default if None)
            retry_executor: Retry executor instance (creates default if None)
            response_cache: Response cache instance (creates default if None)
        """
        self._model = model
        self._config = config or ProviderConfig.from_env()
        
        # Dependency injection with sensible defaults
        self._factory = model_factory or ModelFactory()
        self._circuit_breaker = circuit_breaker or CircuitBreaker(self._config)
        self._parser = response_parser or ResponseParser()
        self._executor = retry_executor or RetryExecutor(self._config)
        
        # Initialize cache (enabled by default, can be disabled via DISABLE_LLM_CACHE=1)
        cache_enabled = os.getenv("DISABLE_LLM_CACHE", "0").lower() not in {"1", "true", "yes"}
        self._cache = response_cache or ResponseCache(
            cache_dir=self._config.cache_dir,
            enabled=cache_enabled
        )
        
        # Lazy-initialized chat model
        self._chat_model: Optional[Any] = None
        self._warmed_up: bool = False
    
    def name(self) -> str:
        """Return provider name for logging."""
        return f"langchain:{self._model}"
    
    def generate_correction_proposals(
        self, 
        prompt: str, 
        schema: Dict[str, Any],
        session_id: str | None = None
    ) -> List[Dict[str, Any]]:
        """Generate correction proposals using LangChain ChatModel.
        
        This method is now much simpler - it orchestrates the components
        rather than implementing all the logic itself.
        
        Args:
            prompt: The correction prompt
            schema: Pydantic schema for structured output (for future use)
            session_id: Optional Langfuse session ID for grouping traces
            
        Returns:
            List of correction proposal dictionaries, or error dicts on failure
        """
        # Store session_id for use in _invoke_model
        self._session_id = session_id
        
        # Step 0: Check cache first
        cached_response = self._cache.get(prompt, self._model)
        if cached_response:
            # Parse cached response and return
            parsed = self._parser.parse(cached_response)
            logger.debug(f"🎯 Using cached response ({len(parsed)} items)")
            return parsed
        
        # Step 1: Check circuit breaker
        if self._circuit_breaker.is_open(self._model):
            open_until = self._circuit_breaker.get_open_until(self._model)
            return [{
                "error": CIRCUIT_OPEN_ERROR,
                "until": open_until
            }]
        
        # Step 2: Get or create chat model with initialization timeout
        if not self._chat_model:
            timeout = self._config.initialization_timeout_seconds
            logger.info(f"🤖 Initializing model {self._model} with {timeout}s timeout...")
            init_start = time.time()

            try:
                # Use ThreadPoolExecutor for cross-platform timeout
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        self._factory.create_chat_model,
                        self._model,
                        self._config
                    )
                    try:
                        self._chat_model = future.result(timeout=timeout)
                    except FuturesTimeoutError:
                        raise InitializationTimeoutError(
                            f"Model initialization timed out after {timeout}s. "
                            f"This may indicate network issues or service unavailability."
                        ) from None

                init_elapsed = time.time() - init_start
                logger.info(f"🤖 Model created in {init_elapsed:.2f}s, starting warm-up...")

                # Warm up the model to establish connection before real work
                self._warm_up_model()

                total_elapsed = time.time() - init_start
                logger.info(f"🤖 Model initialization complete in {total_elapsed:.2f}s")

            except InitializationTimeoutError as e:
                self._circuit_breaker.record_failure(self._model)
                logger.exception("🤖 Model initialization timeout")
                return [{
                    "error": INIT_TIMEOUT_ERROR,
                    "message": str(e),
                    "timeout_seconds": timeout
                }]
            except Exception as e:
                self._circuit_breaker.record_failure(self._model)
                logger.error(f"🤖 Failed to initialize chat model: {e}")
                return [{
                    "error": MODEL_INIT_ERROR,
                    "message": str(e)
                }]
        
        # Step 3: Execute with retry logic
        logger.info(
            f"🤖 [LangChain] Sending prompt to {self._model} ({len(prompt)} chars)"
        )
        logger.debug(f"🤖 [LangChain] Prompt preview: {prompt[:PROMPT_LOG_LENGTH]}...")

        invoke_start = time.time()
        result = self._executor.execute_with_retry(
            operation=lambda: self._invoke_model(prompt),
            operation_name=f"invoke_{self._model}"
        )
        invoke_elapsed = time.time() - invoke_start

        # Step 4: Handle result and update circuit breaker
        if result.success:
            self._circuit_breaker.record_success(self._model)

            logger.info(
                f"🤖 [LangChain] Got response from {self._model} in {invoke_elapsed:.2f}s "
                f"({len(result.value)} chars)"
            )
            logger.debug(f"🤖 [LangChain] Response preview: {result.value[:RESPONSE_LOG_LENGTH]}...")
            
            # Step 5: Cache the raw response for future use
            self._cache.set(
                prompt=prompt,
                model=self._model,
                response=result.value,
                metadata={
                    "session_id": session_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            # Step 6: Parse response
            return self._parser.parse(result.value)
        else:
            self._circuit_breaker.record_failure(self._model)
            return [{
                "error": PROVIDER_ERROR,
                "message": result.error or "unknown"
            }]
    
    def _invoke_model(self, prompt: str) -> str:
        """Invoke the chat model with a prompt.

        This is a simple wrapper that can be passed to the retry executor.

        Args:
            prompt: The prompt to send

        Returns:
            Response content as string

        Raises:
            Exception: Any error from the model invocation
        """
        from langchain_core.messages import HumanMessage

        # Prepare config with session_id in metadata (Langfuse format)
        config = {}
        if hasattr(self, '_session_id') and self._session_id:
            config["metadata"] = {"langfuse_session_id": self._session_id}
            logger.debug(f"🤖 [LangChain] Invoking with session_id: {self._session_id}")

        response = self._chat_model.invoke([HumanMessage(content=prompt)], config=config)
        content = response.content

        # Handle multimodal response format from Gemini 3+ models
        # Response can be a list of content parts: [{'type': 'text', 'text': '...'}]
        if isinstance(content, list):
            # Extract text from the first text content part
            for part in content:
                if isinstance(part, dict) and part.get('type') == 'text':
                    return part.get('text', '')
            # Fallback: concatenate all text parts
            return ''.join(
                part.get('text', '') if isinstance(part, dict) else str(part)
                for part in content
            )

        return content

    def _warm_up_model(self) -> None:
        """Send a lightweight request to warm up the model connection.

        This helps establish the REST connection and potentially warm up any
        server-side resources before processing real correction requests.
        The warm-up uses a timeout to fail fast if the service is unresponsive.
        """
        if self._warmed_up:
            return

        timeout = self._config.warmup_timeout_seconds
        # Use print with flush=True for visibility when output is redirected
        print(f"🔥 Warming up {self._model} connection (timeout: {timeout}s)...", flush=True)
        logger.info(f"🔥 Warming up {self._model} connection (timeout: {timeout}s)...")

        warmup_start = time.time()
        try:
            from langchain_core.messages import HumanMessage

            # Minimal prompt that requires almost no processing
            warm_up_prompt = 'Respond with exactly: {"status":"ready"}'

            # Use ThreadPoolExecutor for timeout on warm-up call
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    self._chat_model.invoke,
                    [HumanMessage(content=warm_up_prompt)]
                )
                try:
                    future.result(timeout=timeout)
                except FuturesTimeoutError:
                    raise TimeoutError(f"Warm-up timed out after {timeout}s") from None

            elapsed = time.time() - warmup_start
            self._warmed_up = True
            print(f"🔥 Warm-up complete for {self._model} in {elapsed:.2f}s", flush=True)
            logger.info(f"🔥 Warm-up complete for {self._model} in {elapsed:.2f}s")
        except Exception as e:
            elapsed = time.time() - warmup_start
            # Don't fail the actual request if warm-up fails
            # Just log and continue - the real request might still work
            print(f"🔥 Warm-up failed for {self._model} after {elapsed:.2f}s: {e} (continuing anyway)", flush=True)
            logger.warning(f"🔥 Warm-up failed for {self._model} after {elapsed:.2f}s: {e} (continuing anyway)")

