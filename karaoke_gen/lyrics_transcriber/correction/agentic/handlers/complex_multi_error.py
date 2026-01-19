"""Handler for complex gaps with multiple error types."""

from typing import List, Dict, Any
from .base import BaseHandler
from ..models.schemas import CorrectionProposal, GapCategory


class ComplexMultiErrorHandler(BaseHandler):
    """Handles large, complex gaps with multiple types of errors."""
    
    @property
    def category(self) -> GapCategory:
        return GapCategory.COMPLEX_MULTI_ERROR
    
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Flag complex gaps for human review."""
        
        if not gap_words:
            return []
        
        # Complex multi-error gaps are too difficult for automatic correction
        # Always flag for human review
        gap_text = ' '.join(w.get('text', '') for w in gap_words)
        word_count = len(gap_words)
        
        proposal = CorrectionProposal(
            word_ids=[w['id'] for w in gap_words],
            action="Flag",
            confidence=0.3,
            reason=f"Complex gap with {word_count} words and multiple error types: '{gap_text[:100]}...'. Too complex for automatic correction. {classification_reasoning}",
            gap_category=self.category,
            requires_human_review=True,
            artist=self.artist,
            title=self.title
        )
        
        return [proposal]

