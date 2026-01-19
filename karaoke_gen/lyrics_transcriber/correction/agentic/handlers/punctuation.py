"""Handler for punctuation-only differences."""

from typing import List, Dict, Any
from .base import BaseHandler
from ..models.schemas import CorrectionProposal, GapCategory


class PunctuationHandler(BaseHandler):
    """Handles gaps where only punctuation/capitalization differs."""
    
    @property
    def category(self) -> GapCategory:
        return GapCategory.PUNCTUATION_ONLY
    
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Return NO_ACTION for punctuation-only differences."""
        # For punctuation differences, we don't need to make any changes
        # The transcription is correct, just styled differently
        
        if not gap_words:
            return []
        
        # Create a single NO_ACTION proposal for the entire gap
        proposal = CorrectionProposal(
            word_ids=[w['id'] for w in gap_words],
            action="NoAction",
            confidence=0.95,
            reason=f"Punctuation/style difference only. {classification_reasoning}",
            gap_category=self.category,
            requires_human_review=False,
            artist=self.artist,
            title=self.title
        )
        
        return [proposal]

