"""Handler for ambiguous gaps that need human review."""

from typing import List, Dict, Any
from .base import BaseHandler
from ..models.schemas import CorrectionProposal, GapCategory


class AmbiguousHandler(BaseHandler):
    """Handles ambiguous gaps where correct action is unclear without audio."""
    
    @property
    def category(self) -> GapCategory:
        return GapCategory.AMBIGUOUS
    
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Flag ambiguous gaps for human review."""
        
        if not gap_words:
            return []
        
        # Ambiguous cases always require human review with audio
        gap_text = ' '.join(w.get('text', '') for w in gap_words)
        
        proposal = CorrectionProposal(
            word_ids=[w['id'] for w in gap_words],
            action="Flag",
            confidence=0.4,
            reason=f"Ambiguous gap: '{gap_text[:100]}...'. Cannot determine correct action without listening to audio. {classification_reasoning}",
            gap_category=self.category,
            requires_human_review=True,
            artist=self.artist,
            title=self.title
        )
        
        return [proposal]

