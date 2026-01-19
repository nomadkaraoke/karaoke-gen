"""Handler for repeated sections (chorus, verse repetitions)."""

from typing import List, Dict, Any
from .base import BaseHandler
from ..models.schemas import CorrectionProposal, GapCategory


class RepeatedSectionHandler(BaseHandler):
    """Handles gaps where transcription includes repeated sections not in condensed references."""
    
    @property
    def category(self) -> GapCategory:
        return GapCategory.REPEATED_SECTION
    
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Flag repeated sections for human review."""
        
        if not gap_words:
            return []
        
        # Repeated sections need audio verification - always flag for review
        gap_text = ' '.join(w.get('text', '') for w in gap_words)
        
        proposal = CorrectionProposal(
            word_ids=[w['id'] for w in gap_words],
            action="Flag",
            confidence=0.5,
            reason=f"Repeated section detected: '{gap_text[:100]}...'. Reference lyrics may be condensed. Requires audio verification. {classification_reasoning}",
            gap_category=self.category,
            requires_human_review=True,
            artist=self.artist,
            title=self.title
        )
        
        return [proposal]

