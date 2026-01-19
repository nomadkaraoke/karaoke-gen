from __future__ import annotations

import logging
import os
import json
import time
from typing import Dict, Any, List, Optional

from .providers.base import BaseAIProvider
from .providers.langchain_bridge import LangChainBridge
from .providers.config import ProviderConfig
from .models.schemas import CorrectionProposal, GapClassification, GapCategory
from .workflows.correction_graph import build_correction_graph
from .prompts.classifier import build_classification_prompt
from .handlers.registry import HandlerRegistry
from karaoke_gen.lyrics_transcriber.utils.tracing import create_span, add_span_attribute

logger = logging.getLogger(__name__)


class AgenticCorrector:
    """Main entry for agentic AI correction using LangChain + LangGraph.

    This orchestrates correction workflows using LangGraph for state management
    and LangChain ChatModels for provider integration. Langfuse tracing is
    automatic via LangChain callbacks.
    
    Uses dependency injection for better testability - you can inject a
    mock provider for testing.
    """

    def __init__(
        self, 
        provider: BaseAIProvider,
        graph: Optional[Any] = None,
        langfuse_handler: Optional[Any] = None,
        session_id: Optional[str] = None
    ):
        """Initialize with injected dependencies.
        
        Args:
            provider: AI provider implementation (e.g., LangChainBridge)
            graph: Optional LangGraph workflow (builds default if None)
            langfuse_handler: Optional Langfuse callback handler (if None, will try to get from provider)
            session_id: Optional Langfuse session ID to group related traces
        """
        self._provider = provider
        self._session_id = session_id
        
        # Get Langfuse handler from provider if available (avoids duplication)
        self._langfuse_handler = langfuse_handler or self._get_provider_handler()
        
        # Build graph with Langfuse callback if available
        self._graph = graph if graph is not None else build_correction_graph(
            callbacks=[self._langfuse_handler] if self._langfuse_handler else None
        )
    
    def _get_provider_handler(self) -> Optional[Any]:
        """Get Langfuse handler from provider if it has one.
        
        This avoids duplicating Langfuse initialization - if the provider
        (e.g., LangChainBridge) already has a handler, we reuse it.
        
        Returns:
            CallbackHandler instance from provider, or None
        """
        # Check if provider is LangChainBridge and has a factory
        if hasattr(self._provider, '_factory'):
            factory = self._provider._factory
            
            # Force initialization of Langfuse if keys are present
            # This ensures the handler is available when we need it
            if hasattr(factory, '_langfuse_initialized'):
                if not factory._langfuse_initialized:
                    # Initialize by calling _create_callbacks (which triggers _initialize_langfuse)
                    factory._create_callbacks(self._provider._model)
            
            # Now check if handler is available
            if hasattr(factory, '_langfuse_handler'):
                handler = factory._langfuse_handler
                if handler:
                    logger.debug("🤖 Reusing Langfuse handler from ModelFactory")
                    return handler
        
        logger.debug("🤖 No Langfuse handler from provider")
        return None
    
    @classmethod
    def from_model(
        cls,
        model: str,
        config: ProviderConfig | None = None,
        session_id: Optional[str] = None,
        cache_dir: Optional[str] = None,
        warmup: bool = True
    ) -> "AgenticCorrector":
        """Factory method to create corrector from model specification.

        This is a convenience method for the common case where you want
        to use LangChainBridge with a model spec string.

        Args:
            model: Model identifier in format "provider/model"
            config: Optional provider configuration
            session_id: Optional Langfuse session ID to group related traces
            cache_dir: Optional cache directory (uses default if not provided)
            warmup: If True, eagerly initialize the model to avoid delays when
                    multiple threads call classify_gap() simultaneously (default: True)

        Returns:
            AgenticCorrector instance with LangChainBridge provider
        """
        config = config or ProviderConfig.from_env(cache_dir=cache_dir)
        provider = LangChainBridge(model=model, config=config)

        # Eagerly initialize the model to avoid lazy initialization delays
        # when multiple threads call classify_gap() simultaneously
        if warmup:
            logger.info(f"🤖 Warming up model {model} before parallel processing...")
            if not provider.warmup():
                logger.warning(f"🤖 Model warmup failed for {model}, will retry on first use")

        return cls(provider=provider, session_id=session_id)

    def classify_gap(
        self,
        gap_id: str,
        gap_text: str,
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        artist: Optional[str] = None,
        title: Optional[str] = None
    ) -> Optional[GapClassification]:
        """Classify a gap using the AI provider.

        Args:
            gap_id: Unique identifier for the gap
            gap_text: The text of the gap
            preceding_words: Text immediately before the gap
            following_words: Text immediately after the gap
            reference_contexts: Dictionary of reference lyrics from each source
            artist: Song artist name
            title: Song title

        Returns:
            GapClassification object or None if classification fails
        """
        logger.info(f"🤖 Classifying gap {gap_id} ({len(gap_text)} chars)")
        start_time = time.time()

        # Build classification prompt
        prompt = build_classification_prompt(
            gap_text=gap_text,
            preceding_words=preceding_words,
            following_words=following_words,
            reference_contexts=reference_contexts,
            artist=artist,
            title=title,
            gap_id=gap_id
        )

        # Call AI provider to get classification
        try:
            data = self._provider.generate_correction_proposals(
                prompt,
                schema=GapClassification.model_json_schema(),
                session_id=self._session_id
            )

            elapsed = time.time() - start_time

            # Extract first result
            if data and len(data) > 0:
                item = data[0]
                if isinstance(item, dict) and "error" not in item:
                    classification = GapClassification.model_validate(item)
                    logger.info(
                        f"🤖 Classified gap {gap_id} as {classification.category} "
                        f"(confidence: {classification.confidence:.2f}) in {elapsed:.2f}s"
                    )
                    return classification
                else:
                    logger.warning(f"🤖 Classification returned error for gap {gap_id}: {item}")
        except Exception as e:
            elapsed = time.time() - start_time
            logger.warning(f"🤖 Failed to classify gap {gap_id} after {elapsed:.2f}s: {e}")

        return None
    
    def propose_for_gap(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        artist: Optional[str] = None,
        title: Optional[str] = None
    ) -> List[CorrectionProposal]:
        """Generate correction proposals for a gap using two-step classification workflow.
        
        Args:
            gap_id: Unique identifier for the gap
            gap_words: List of word dictionaries with id, text, start_time, end_time
            preceding_words: Text immediately before the gap
            following_words: Text immediately after the gap
            reference_contexts: Dictionary of reference lyrics from each source
            artist: Song artist name
            title: Song title
        
        Returns:
            List of CorrectionProposal objects
        """
        with create_span("agentic.propose_for_gap", {
            "gap_id": gap_id,
            "word_count": len(gap_words),
        }) as span:
            # Step 1: Classify the gap
            gap_text = ' '.join(w.get('text', '') for w in gap_words)
            classification = self.classify_gap(
                gap_id=gap_id,
                gap_text=gap_text,
                preceding_words=preceding_words,
                following_words=following_words,
                reference_contexts=reference_contexts,
                artist=artist,
                title=title
            )

            if not classification:
                # Classification failed, flag for human review
                logger.warning(f"🤖 Classification failed for gap {gap_id}, flagging for review")
                if span:
                    span.set_attribute("classification_failed", True)
                # Safely extract word IDs, filtering out any missing/None values
                word_ids = [w.get('id') for w in gap_words if w.get('id') is not None]
                return [CorrectionProposal(
                    word_ids=word_ids,
                    action="Flag",
                    confidence=0.0,
                    reason="Classification failed - unable to categorize gap",
                    requires_human_review=True,
                    artist=artist,
                    title=title
                )]

            if span:
                span.set_attribute("gap_category", classification.category.value if hasattr(classification.category, 'value') else str(classification.category))

            # Step 2: Route to appropriate handler based on category
            try:
                handler = HandlerRegistry.get_handler(
                    category=classification.category,
                    artist=artist,
                    title=title
                )

                proposals = handler.handle(
                    gap_id=gap_id,
                    gap_words=gap_words,
                    preceding_words=preceding_words,
                    following_words=following_words,
                    reference_contexts=reference_contexts,
                    classification_reasoning=classification.reasoning
                )

                # Add classification metadata to proposals
                for proposal in proposals:
                    if not proposal.gap_category:
                        proposal.gap_category = classification.category
                    if not proposal.artist:
                        proposal.artist = artist
                    if not proposal.title:
                        proposal.title = title

                if span:
                    span.set_attribute("proposal_count", len(proposals))

                return proposals

            except Exception as e:
                # Sanitize error message - use exception class name and truncated message
                error_type = type(e).__name__
                error_msg_truncated = str(e)[:50] if str(e) else "Unknown error"
                sanitized_error = f"{error_type}: {error_msg_truncated}"
                logger.error(f"🤖 Handler failed for gap {gap_id} (category: {classification.category}): {sanitized_error}")
                if span:
                    span.set_attribute("handler_error", sanitized_error)
                # Safely extract word IDs, filtering out any missing/None values
                word_ids = [w.get('id') for w in gap_words if w.get('id') is not None]
                # Handler failed, flag for human review
                return [CorrectionProposal(
                    word_ids=word_ids,
                    action="Flag",
                    confidence=0.0,
                    reason=f"Handler error for category {classification.category} ({error_type})",
                    gap_category=classification.category,
                    requires_human_review=True,
                    artist=artist,
                    title=title
                )]

    def propose(self, prompt: str) -> List[CorrectionProposal]:
        """Generate correction proposals using LangGraph + LangChain.
        
        DEPRECATED: This method uses the old single-step approach.
        Use propose_for_gap() for the new two-step classification workflow.
        
        Args:
            prompt: The correction prompt with gap text and reference context
            
        Returns:
            List of validated CorrectionProposal objects
        """
        # Prepare config with session_id in metadata (Langfuse format)
        config = {}
        if self._langfuse_handler:
            config["callbacks"] = [self._langfuse_handler]
            if self._session_id:
                config["metadata"] = {"langfuse_session_id": self._session_id}
                logger.debug(f"🤖 Set Langfuse session_id in metadata: {self._session_id}")
        
        # Run LangGraph workflow (with Langfuse tracing if configured)
        if self._graph:
            try:
                self._graph.invoke(
                    {"prompt": prompt, "proposals": []},
                    config=config
                )
            except Exception as e:
                logger.debug(f"🤖 LangGraph workflow invocation failed: {e}")

        # Get proposals from LangChain ChatModel
        # Pass the session_id via metadata to the provider
        data = self._provider.generate_correction_proposals(
            prompt, 
            schema=CorrectionProposal.model_json_schema(),
            session_id=self._session_id
        )
        
        # Validate via Pydantic; invalid entries are dropped
        proposals: List[CorrectionProposal] = []
        for item in data:
            # Check if this is an error response from the provider
            if isinstance(item, dict) and "error" in item:
                logger.warning(f"🤖 Provider returned error: {item}")
                continue
            
            try:
                proposals.append(CorrectionProposal.model_validate(item))
            except Exception as e:
                # Log validation errors for debugging
                logger.debug(f"🤖 Failed to validate proposal: {e}, item: {item}")
                continue
                
        return proposals


