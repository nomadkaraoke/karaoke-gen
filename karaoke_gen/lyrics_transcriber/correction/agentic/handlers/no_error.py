"""Handler for gaps where transcription matches at least one reference source."""

from typing import List, Dict, Any
from .base import BaseHandler
from ..models.schemas import CorrectionProposal, GapCategory


class NoErrorHandler(BaseHandler):
    """Handles gaps where the transcription is correct (matches a reference source)."""
    
    @property
    def category(self) -> GapCategory:
        return GapCategory.NO_ERROR
    
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Return NO_ACTION since transcription is correct."""
        
        if not gap_words:
            return []
        
        # Create a single NO_ACTION proposal
        proposal = CorrectionProposal(
            word_ids=[w['id'] for w in gap_words],
            action="NoAction",
            confidence=0.99,
            reason=f"Transcription matches at least one reference source. {classification_reasoning}",
            gap_category=self.category,
            requires_human_review=False,
            artist=self.artist,
            title=self.title
        )
        
        return [proposal]

