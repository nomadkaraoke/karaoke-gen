"""LangFuse prompt management for agentic correction.

This module provides prompt fetching from LangFuse, enabling dynamic prompt
iteration without code redeployment.
"""

from typing import Dict, List, Optional, Any
import logging
import os

logger = logging.getLogger(__name__)


class LangFusePromptError(Exception):
    """Raised when LangFuse prompt fetching fails."""
    pass


class LangFuseDatasetError(Exception):
    """Raised when LangFuse dataset fetching fails."""
    pass


class LangFusePromptService:
    """Fetches prompts and datasets from LangFuse for agentic correction.

    This service handles:
    - Fetching prompt templates from LangFuse
    - Fetching few-shot examples from LangFuse datasets
    - Compiling prompts with dynamic variables
    - Fail-fast behavior when LangFuse is configured but unavailable

    When LangFuse keys are not configured, falls back to hardcoded prompts
    for local development.
    """

    # Prompt and dataset names in LangFuse
    CLASSIFIER_PROMPT_NAME = "gap-classifier"
    EXAMPLES_DATASET_NAME = "gap-classifier-examples"

    def __init__(self, client: Optional[Any] = None):
        """Initialize the prompt service.

        Args:
            client: Optional pre-initialized Langfuse client (for testing).
                   If None, will initialize from environment variables.
        """
        self._client = client
        self._initialized = False
        self._use_langfuse = self._should_use_langfuse()

        if self._use_langfuse and client is None:
            self._init_client()

    def _should_use_langfuse(self) -> bool:
        """Check if LangFuse credentials are configured."""
        public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
        secret_key = os.getenv("LANGFUSE_SECRET_KEY")
        return bool(public_key and secret_key)

    def _init_client(self) -> None:
        """Initialize the Langfuse client using the shared singleton."""
        from ..observability.langfuse_integration import get_langfuse_client, LangFuseConfigError

        try:
            self._client = get_langfuse_client()
            if self._client:
                self._initialized = True
                logger.info("LangFuse prompt service initialized")
            else:
                logger.debug("LangFuse keys not configured, will use hardcoded prompts")
        except LangFuseConfigError as e:
            # Re-raise as RuntimeError for consistent error handling
            raise RuntimeError(str(e)) from e

    def get_classification_prompt(
        self,
        gap_text: str,
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        artist: Optional[str] = None,
        title: Optional[str] = None,
        gap_id: Optional[str] = None
    ) -> str:
        """Fetch and compile the gap classification prompt.

        If LangFuse is configured, fetches the prompt template and examples
        from LangFuse. Otherwise, falls back to hardcoded prompts.

        Args:
            gap_text: The text of the gap that needs classification
            preceding_words: Text immediately before the gap
            following_words: Text immediately after the gap
            reference_contexts: Dictionary of reference lyrics from each source
            artist: Song artist name for context
            title: Song title for context
            gap_id: Identifier for the gap

        Returns:
            Compiled prompt string ready for LLM

        Raises:
            LangFusePromptError: If LangFuse is configured but prompt fetch fails
        """
        if not self._use_langfuse:
            # Fall back to hardcoded prompt for development
            from .classifier import build_classification_prompt_hardcoded
            return build_classification_prompt_hardcoded(
                gap_text=gap_text,
                preceding_words=preceding_words,
                following_words=following_words,
                reference_contexts=reference_contexts,
                artist=artist,
                title=title,
                gap_id=gap_id
            )

        # Fetch from LangFuse
        try:
            prompt_template = self._fetch_prompt(self.CLASSIFIER_PROMPT_NAME)
            examples = self._fetch_examples()

            # Build component strings
            song_context = self._build_song_context(artist, title)
            examples_text = self._format_examples(examples)
            references_text = self._format_references(reference_contexts)

            # Compile the prompt with variables
            compiled = prompt_template.compile(
                song_context=song_context,
                examples_text=examples_text,
                gap_id=gap_id or "unknown",
                preceding_words=preceding_words,
                gap_text=gap_text,
                following_words=following_words,
                references_text=references_text
            )

            logger.debug(f"Compiled LangFuse prompt for gap {gap_id}")
            return compiled

        except Exception as e:
            raise LangFusePromptError(
                f"Failed to fetch/compile prompt from LangFuse: {e}"
            ) from e

    def _fetch_prompt(self, name: str, label: str = "production") -> Any:
        """Fetch a prompt template from LangFuse.

        Args:
            name: The prompt name in LangFuse
            label: Prompt label to fetch (default: "production"). Falls back to
                   version 1 if labeled version not found.

        Returns:
            LangFuse prompt object

        Raises:
            LangFusePromptError: If fetch fails
        """
        if not self._client:
            raise LangFusePromptError("LangFuse client not initialized")

        try:
            # Try to fetch with the specified label (default: production)
            prompt = self._client.get_prompt(name, label=label)
            logger.debug(f"Fetched prompt '{name}' (label={label}) from LangFuse")
            return prompt
        except Exception as label_error:
            # If labeled version not found, try fetching version 1 as fallback
            # This handles newly created prompts that haven't been promoted yet
            try:
                prompt = self._client.get_prompt(name, version=1)
                logger.warning(
                    f"Prompt '{name}' label '{label}' not found, using version 1. "
                    f"Consider promoting this prompt in LangFuse UI."
                )
                return prompt
            except Exception as version_error:
                raise LangFusePromptError(
                    f"Failed to fetch prompt '{name}' from LangFuse: "
                    f"Label '{label}' error: {label_error}, "
                    f"Version 1 fallback error: {version_error}"
                ) from version_error

    def _fetch_examples(self) -> List[Dict[str, Any]]:
        """Fetch few-shot examples from LangFuse dataset.

        Returns:
            List of example dictionaries

        Raises:
            LangFuseDatasetError: If fetch fails
        """
        if not self._client:
            raise LangFuseDatasetError("LangFuse client not initialized")

        try:
            dataset = self._client.get_dataset(self.EXAMPLES_DATASET_NAME)
            examples = []
            for item in dataset.items:
                # Dataset items have 'input' field with the example data
                if hasattr(item, 'input') and item.input:
                    examples.append(item.input)

            logger.debug(f"Fetched {len(examples)} examples from LangFuse dataset")
            return examples
        except Exception as e:
            raise LangFuseDatasetError(
                f"Failed to fetch dataset '{self.EXAMPLES_DATASET_NAME}' from LangFuse: {e}"
            ) from e

    def _build_song_context(self, artist: Optional[str], title: Optional[str]) -> str:
        """Build song context section for the prompt."""
        if artist and title:
            return (
                f"\n## Song Context\n\n"
                f"**Artist:** {artist}\n"
                f"**Title:** {title}\n\n"
                f"Note: The song title and artist name may help identify proper nouns "
                f"or unusual words that could be mis-heard.\n"
            )
        return ""

    def _format_examples(self, examples: List[Dict[str, Any]]) -> str:
        """Format few-shot examples for inclusion in prompt.

        Args:
            examples: List of example dictionaries from LangFuse dataset

        Returns:
            Formatted examples string
        """
        if not examples:
            return ""

        # Group examples by category
        examples_by_category: Dict[str, List[Dict]] = {}
        for ex in examples:
            category = ex.get("category", "unknown")
            if category not in examples_by_category:
                examples_by_category[category] = []
            examples_by_category[category].append(ex)

        # Build formatted text
        text = "## Example Classifications\n\n"
        for category, category_examples in examples_by_category.items():
            text += f"### {category.upper().replace('_', ' ')}\n\n"
            for ex in category_examples[:2]:  # Limit to 2 examples per category
                text += f"**Gap:** {ex.get('gap_text', '')}\n"
                text += f"**Context:** ...{ex.get('preceding', '')}... [GAP] ...{ex.get('following', '')}...\n"
                if 'reference' in ex:
                    text += f"**Reference:** {ex['reference']}\n"
                text += f"**Reasoning:** {ex.get('reasoning', '')}\n"
                text += f"**Action:** {ex.get('action', '')}\n\n"

        return text

    def _format_references(self, reference_contexts: Dict[str, str]) -> str:
        """Format reference lyrics for inclusion in prompt.

        Args:
            reference_contexts: Dictionary of reference lyrics from each source

        Returns:
            Formatted references string
        """
        if not reference_contexts:
            return ""

        text = "## Available Reference Lyrics\n\n"
        for source, context in reference_contexts.items():
            text += f"**{source.upper()}:** {context}\n\n"

        return text


# Module-level singleton for convenience
_prompt_service: Optional[LangFusePromptService] = None


def get_prompt_service() -> LangFusePromptService:
    """Get or create the global prompt service instance.

    Returns:
        LangFusePromptService singleton instance
    """
    global _prompt_service
    if _prompt_service is None:
        _prompt_service = LangFusePromptService()
    return _prompt_service


def reset_prompt_service() -> None:
    """Reset the global prompt service instance (for testing)."""
    global _prompt_service
    _prompt_service = None
