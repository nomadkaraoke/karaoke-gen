"""Factory for creating LangChain ChatModels with Langfuse callbacks."""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional, List

from .config import ProviderConfig

logger = logging.getLogger(__name__)

# Try to import Langfuse preloader (may not exist in standalone library usage)
try:
    from backend.services.langfuse_preloader import get_preloaded_langfuse_handler

    _HAS_LANGFUSE_PRELOADER = True
except ImportError:
    _HAS_LANGFUSE_PRELOADER = False

# Error message constant for TRY003 compliance
GOOGLE_API_KEY_MISSING_ERROR = (
    "GOOGLE_API_KEY environment variable is required for Google/Gemini models. "
    "Get an API key from https://aistudio.google.com/app/apikey"
)


class ModelFactory:
    """Creates and configures LangChain ChatModels with observability.
    
    This factory handles:
    - Parsing model specifications ("provider/model" format)
    - Creating Langfuse callbacks when configured
    - Instantiating the appropriate ChatModel for each provider
    
    Single Responsibility: Model creation only, no execution or state management.
    """
    
    def __init__(self):
        self._langfuse_handler: Optional[Any] = None
        self._langfuse_initialized = False
    
    def create_chat_model(self, model_spec: str, config: ProviderConfig) -> Any:
        """Create a ChatModel from a model specification.
        
        Args:
            model_spec: Model identifier in format "provider/model" 
                       e.g. "ollama/gpt-oss:latest", "openai/gpt-4"
            config: Provider configuration with timeouts, retries, etc.
            
        Returns:
            Configured LangChain ChatModel instance
            
        Raises:
            ValueError: If model_spec format is invalid or provider unsupported
        """
        provider, model_name = self._parse_model_spec(model_spec)
        callbacks = self._create_callbacks(model_spec)
        return self._instantiate_model(provider, model_name, callbacks, config)
    
    def _parse_model_spec(self, spec: str) -> tuple[str, str]:
        """Parse model specification into provider and model name.
        
        Args:
            spec: Model spec in format "provider/model"
            
        Returns:
            Tuple of (provider, model_name)
            
        Raises:
            ValueError: If format is invalid
        """
        parts = spec.split("/", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Model spec must be in format 'provider/model', got: {spec}"
            )
        return parts[0], parts[1]
    
    def _create_callbacks(self, model_spec: str) -> List[Any]:
        """Create Langfuse callback handlers if configured.
        
        Args:
            model_spec: Model specification for logging
            
        Returns:
            List of callback handlers (may be empty)
        """
        # Only initialize Langfuse once
        if not self._langfuse_initialized:
            self._initialize_langfuse(model_spec)
            self._langfuse_initialized = True
        
        return [self._langfuse_handler] if self._langfuse_handler else []
    
    def _initialize_langfuse(self, model_spec: str) -> None:
        """Initialize Langfuse callback handler if keys are present.

        First tries to use a preloaded handler (to avoid 200+ second init delay
        on Cloud Run cold starts), then falls back to creating a new one.

        Langfuse reads credentials from environment variables automatically:
        - LANGFUSE_PUBLIC_KEY
        - LANGFUSE_SECRET_KEY
        - LANGFUSE_HOST (optional)

        Args:
            model_spec: Model specification for logging

        Raises:
            RuntimeError: If Langfuse keys are set but initialization fails
        """
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")

        if not (public_key and secret_key):
            logger.debug("🤖 Langfuse keys not found, tracing disabled")
            return

        # Try to use preloaded handler first (avoids 200+ second delay on Cloud Run)
        if _HAS_LANGFUSE_PRELOADER:
            preloaded = get_preloaded_langfuse_handler()
            if preloaded is not None:
                logger.info(f"🤖 Using preloaded Langfuse handler for {model_spec}")
                self._langfuse_handler = preloaded
                return

        # Fall back to creating new handler
        logger.info(f"🤖 Initializing Langfuse handler (not preloaded) for {model_spec}...")
        try:
            from langfuse.langchain import CallbackHandler

            # CallbackHandler auto-discovers credentials from environment variables
            self._langfuse_handler = CallbackHandler()
            logger.info(f"🤖 Langfuse callback handler initialized for {model_spec}")
        except Exception as e:
            # If Langfuse keys are set, we MUST fail fast
            raise RuntimeError(
                f"Langfuse keys are set but initialization failed: {e}\n"
                f"This indicates a configuration or dependency problem.\n"
                f"Check:\n"
                f"  - LANGFUSE_PUBLIC_KEY: {public_key[:10]}...\n"
                f"  - LANGFUSE_SECRET_KEY: {'set' if secret_key else 'not set'}\n"
                f"  - LANGFUSE_HOST: {os.getenv('LANGFUSE_HOST', 'default')}\n"
                f"  - langfuse package version: pip show langfuse"
            ) from e
    
    def _instantiate_model(
        self, 
        provider: str, 
        model_name: str, 
        callbacks: List[Any], 
        config: ProviderConfig
    ) -> Any:
        """Instantiate the appropriate ChatModel for the provider.
        
        Args:
            provider: Provider name (ollama, openai, anthropic)
            model_name: Model name within that provider
            callbacks: List of callback handlers
            config: Provider configuration
            
        Returns:
            Configured ChatModel instance
            
        Raises:
            ValueError: If provider is not supported
            ImportError: If provider package is not installed
        """
        try:
            if provider == "ollama":
                return self._create_ollama_model(model_name, callbacks, config)
            elif provider == "openai":
                return self._create_openai_model(model_name, callbacks, config)
            elif provider == "anthropic":
                return self._create_anthropic_model(model_name, callbacks, config)
            elif provider in ("vertexai", "google"):
                return self._create_vertexai_model(model_name, callbacks, config)
            else:
                raise ValueError(f"Unsupported provider: {provider}")
        except ImportError as e:
            raise ImportError(
                f"Failed to import {provider} provider. "
                f"Install with: pip install langchain-{provider}"
            ) from e
    
    def _create_ollama_model(
        self, model_name: str, callbacks: List[Any], config: ProviderConfig
    ) -> Any:
        """Create ChatOllama model."""
        from langchain_ollama import ChatOllama
        
        model = ChatOllama(
            model=model_name,
            timeout=config.request_timeout_seconds,
            callbacks=callbacks,
        )
        logger.debug(f"🤖 Created Ollama model: {model_name}")
        return model
    
    def _create_openai_model(
        self, model_name: str, callbacks: List[Any], config: ProviderConfig
    ) -> Any:
        """Create ChatOpenAI model."""
        from langchain_openai import ChatOpenAI
        
        model = ChatOpenAI(
            model=model_name,
            timeout=config.request_timeout_seconds,
            max_retries=config.max_retries,
            callbacks=callbacks,
        )
        logger.debug(f"🤖 Created OpenAI model: {model_name}")
        return model
    
    def _create_anthropic_model(
        self, model_name: str, callbacks: List[Any], config: ProviderConfig
    ) -> Any:
        """Create ChatAnthropic model."""
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(
            model=model_name,
            timeout=config.request_timeout_seconds,
            max_retries=config.max_retries,
            callbacks=callbacks,
        )
        logger.debug(f"🤖 Created Anthropic model: {model_name}")
        return model

    def _create_vertexai_model(
        self, model_name: str, callbacks: List[Any], config: ProviderConfig
    ) -> Any:
        """Create ChatGoogleGenerativeAI model for Google Gemini.

        Uses the unified langchain-google-genai package which supports both:
        - Vertex AI backend (service account / ADC auth) - when project is set
        - Google AI Studio backend (API key auth) - when only api_key is set

        On Cloud Run, ADC (Application Default Credentials) are used automatically
        when the project parameter is provided, using the service account attached
        to the Cloud Run service.

        This is a REST-based API that avoids the gRPC connection issues
        seen with the deprecated langchain-google-vertexai package.
        """
        from langchain_google_genai import ChatGoogleGenerativeAI

        start_time = time.time()

        # Determine authentication method
        api_key = config.google_api_key
        project = config.gcp_project_id

        # Prefer Vertex AI (service account) if project is set, otherwise require API key
        if not project and not api_key:
            raise ValueError(GOOGLE_API_KEY_MISSING_ERROR)

        if project:
            logger.info(f"🤖 Creating Google Gemini model via Vertex AI (project={project}): {model_name}")
        else:
            logger.info(f"🤖 Creating Google Gemini model via AI Studio API: {model_name}")

        # Build kwargs - only include api_key if set (otherwise ADC is used)
        model_kwargs = {
            "model": model_name,
            "convert_system_message_to_human": True,  # Gemini doesn't support system messages
            "max_retries": config.max_retries,
            "timeout": config.request_timeout_seconds,
            "callbacks": callbacks,
        }

        # Add project to trigger Vertex AI backend with ADC
        if project:
            model_kwargs["project"] = project

        # Add API key if available (can be used with or without project)
        if api_key:
            model_kwargs["google_api_key"] = api_key

        model = ChatGoogleGenerativeAI(**model_kwargs)

        elapsed = time.time() - start_time
        logger.info(f"🤖 Google Gemini model created in {elapsed:.2f}s: {model_name}")
        return model

