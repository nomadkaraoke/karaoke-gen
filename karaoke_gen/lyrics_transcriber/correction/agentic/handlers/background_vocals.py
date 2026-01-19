"""Handler for background vocals that should be removed."""

from typing import List, Dict, Any
from .base import BaseHandler
from ..models.schemas import CorrectionProposal, GapCategory


class BackgroundVocalsHandler(BaseHandler):
    """Handles gaps containing background vocals (usually in parentheses)."""
    
    @property
    def category(self) -> GapCategory:
        return GapCategory.BACKGROUND_VOCALS
    
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Propose deletion of words in parentheses."""
        
        if not gap_words:
            return []
        
        proposals = []
        
        # Find words that are in parentheses or are parentheses themselves
        words_to_delete = []
        for word in gap_words:
            text = word.get('text', '')
            # Check if word has parentheses or is just parentheses
            if '(' in text or ')' in text:
                words_to_delete.append(word)
        
        if words_to_delete:
            # Create delete proposals for parenthesized content
            proposal = CorrectionProposal(
                word_ids=[w['id'] for w in words_to_delete],
                action="DeleteWord",
                confidence=0.85,
                reason=f"Background vocals in parentheses, not in reference lyrics. {classification_reasoning}",
                gap_category=self.category,
                requires_human_review=False,
                artist=self.artist,
                title=self.title
            )
            proposals.append(proposal)
        else:
            # If no parentheses found but classified as background vocals,
            # flag for review as classifier may have other reasoning
            proposal = CorrectionProposal(
                word_ids=[w['id'] for w in gap_words],
                action="Flag",
                confidence=0.6,
                reason=f"Classified as background vocals but no parentheses found. {classification_reasoning}",
                gap_category=self.category,
                requires_human_review=True,
                artist=self.artist,
                title=self.title
            )
            proposals.append(proposal)
        
        return proposals

