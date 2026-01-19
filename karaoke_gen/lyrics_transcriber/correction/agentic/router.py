from __future__ import annotations

import os
from typing import Dict, Any

from .providers.config import ProviderConfig

# Default model for cloud deployments - Gemini 3 Flash via Vertex AI
# Note: Gemini 3 models require 'global' location (not regional like us-central1)
DEFAULT_CLOUD_MODEL = "vertexai/gemini-3-flash-preview"


class ModelRouter:
    """Rules-based routing by gap type/length/uncertainty (scaffold)."""

    def __init__(self, config: ProviderConfig | None = None):
        self._config = config or ProviderConfig.from_env()

    def choose_model(self, gap_type: str, uncertainty: float) -> str:
        """Choose appropriate model based on gap characteristics.

        Returns model identifier in format "provider/model" for LangChain:
        - "vertexai/gemini-3-flash-preview" for Gemini via Vertex AI (default)
        - "ollama/llama3.2:latest" for local Ollama models
        - "openai/gpt-4" for OpenAI models
        - "anthropic/claude-3-sonnet-20240229" for Anthropic models
        """
        # Check for explicit model override from environment
        env_model = os.getenv("AGENTIC_AI_MODEL")
        if env_model:
            return env_model

        # Privacy mode: use local Ollama
        if self._config.privacy_mode:
            return "ollama/llama3.2:latest"

        # Default to Gemini 3 Flash for all cases (fast, cost-effective, latest capabilities)
        return DEFAULT_CLOUD_MODEL


