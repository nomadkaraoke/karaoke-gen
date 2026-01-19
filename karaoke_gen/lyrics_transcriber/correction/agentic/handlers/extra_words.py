"""Handler for extra filler words at sentence starts."""

from typing import List, Dict, Any
from .base import BaseHandler
from ..models.schemas import CorrectionProposal, GapCategory


class ExtraWordsHandler(BaseHandler):
    """Handles gaps with extra filler words like 'And', 'But', 'Well'."""
    
    # Common filler words that are often incorrectly added by transcription
    FILLER_WORDS = {'and', 'but', 'well', 'so', 'or', 'then', 'now'}
    
    @property
    def category(self) -> GapCategory:
        return GapCategory.EXTRA_WORDS
    
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Propose deletion of filler words."""
        
        if not gap_words:
            return []
        
        proposals = []
        
        # Look for filler words at the start of the gap
        for i, word in enumerate(gap_words):
            text = word.get('text', '').strip().lower().rstrip(',.!?;:')
            
            if text in self.FILLER_WORDS:
                # Check if this is likely at a sentence/line start
                # (either it's the first word or preceded by punctuation)
                is_sentence_start = (
                    i == 0 or 
                    gap_words[i-1].get('text', '').strip()[-1:] in '.!?'
                )
                
                if is_sentence_start:
                    proposal = CorrectionProposal(
                        word_id=word['id'],
                        action="DeleteWord",
                        confidence=0.80,
                        reason=f"Extra filler word '{word.get('text')}' at sentence start not in reference. {classification_reasoning}",
                        gap_category=self.category,
                        requires_human_review=False,
                        artist=self.artist,
                        title=self.title
                    )
                    proposals.append(proposal)
        
        # If no filler words found, flag for review
        if not proposals:
            proposal = CorrectionProposal(
                word_ids=[w['id'] for w in gap_words],
                action="Flag",
                confidence=0.5,
                reason=f"Classified as extra words but no obvious fillers found. {classification_reasoning}",
                gap_category=self.category,
                requires_human_review=True,
                artist=self.artist,
                title=self.title
            )
            proposals.append(proposal)
        
        return proposals

