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
import threading
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

        # Lazy-initialized chat model with thread-safe initialization
        # Lock prevents race condition where multiple threads try to initialize simultaneously
        self._chat_model: Optional[Any] = None
        self._model_init_lock = threading.Lock()

    def warmup(self) -> bool:
        """Eagerly initialize the chat model.

        Call this after creating the bridge to avoid lazy initialization delays
        when multiple threads call generate_correction_proposals() simultaneously.

        Returns:
            True if model was initialized successfully, False otherwise
        """
        if self._chat_model is not None:
            logger.debug(f"🤖 Model {self._model} already initialized")
            return True

        logger.info(f"🤖 Warming up model {self._model}...")
        # Trigger initialization by calling the initialization logic directly
        try:
            self._ensure_model_initialized()
            return self._chat_model is not None
        except Exception as e:
            logger.error(f"🤖 Warmup failed for {self._model}: {e}")
            return False

    def _ensure_model_initialized(self) -> None:
        """Ensure the chat model is initialized (thread-safe).

        This method handles the lazy initialization with proper locking.
        It's separated out so it can be called from both warmup() and
        generate_correction_proposals().
        """
        if self._chat_model is not None:
            return

        with self._model_init_lock:
            # Double-check after acquiring lock
            if self._chat_model is not None:
                return

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
                logger.info(f"🤖 Model initialized in {init_elapsed:.2f}s")

            except InitializationTimeoutError:
                self._circuit_breaker.record_failure(self._model)
                raise
            except Exception as e:
                self._circuit_breaker.record_failure(self._model)
                logger.error(f"🤖 Failed to initialize chat model: {e}")
                raise

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
        
        # Step 2: Get or create chat model with thread-safe initialization
        # Use double-checked locking to avoid race condition where multiple threads
        # all try to initialize the model simultaneously (which caused job 2ccbdf6b
        # to have 5 concurrent model initializations and 6+ minute delays)
        #
        # NOTE: For best performance, call warmup() after creating the bridge to
        # eagerly initialize the model before parallel processing begins.
        try:
            self._ensure_model_initialized()
        except InitializationTimeoutError as e:
            logger.exception("🤖 Model initialization timeout")
            return [{
                "error": INIT_TIMEOUT_ERROR,
                "message": str(e),
                "timeout_seconds": self._config.initialization_timeout_seconds
            }]
        except Exception as e:
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


