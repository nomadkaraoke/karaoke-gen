"""Handler for sound-alike transcription errors."""

from typing import List, Dict, Any, Optional
from .base import BaseHandler
from ..models.schemas import CorrectionProposal, GapCategory
import re


class SoundAlikeHandler(BaseHandler):
    """Handles gaps with sound-alike errors (homophones, similar-sounding phrases)."""
    
    @property
    def category(self) -> GapCategory:
        return GapCategory.SOUND_ALIKE
    
    def _extract_replacement_from_references(
        self,
        gap_words: List[Dict[str, Any]],
        reference_contexts: Dict[str, str],
        preceding_words: str,
        following_words: str
    ) -> Optional[str]:
        """Try to extract the correct text from reference lyrics.
        
        Args:
            gap_words: Words in the gap
            reference_contexts: Reference lyrics from each source
            preceding_words: Words before gap
            following_words: Words after gap
        
        Returns:
            Replacement text if found, None otherwise
        """
        if not reference_contexts:
            return None
        
        # Normalize preceding and following for matching
        preceding_norm = self._normalize_text(preceding_words)
        following_norm = self._normalize_text(following_words)
        
        # Take last few words of preceding and first few words of following
        preceding_tokens = preceding_norm.split()[-5:] if preceding_norm else []
        following_tokens = following_norm.split()[:5] if following_norm else []
        
        # Try to find the context in each reference
        for source, ref_text in reference_contexts.items():
            ref_norm = self._normalize_text(ref_text)
            
            # Try to find the preceding context
            if preceding_tokens:
                preceding_pattern = ' '.join(preceding_tokens)
                if preceding_pattern in ref_norm:
                    # Found the context, now extract what comes after
                    start_idx = ref_norm.index(preceding_pattern) + len(preceding_pattern)
                    remaining = ref_norm[start_idx:].strip()
                    
                    # Find where following context starts
                    if following_tokens:
                        following_pattern = ' '.join(following_tokens)
                        if following_pattern in remaining:
                            end_idx = remaining.index(following_pattern)
                            replacement = remaining[:end_idx].strip()
                            if replacement:
                                return replacement
        
        return None
    
    def _normalize_text(self, text: str) -> str:
        """Normalize text for comparison (lowercase, remove punctuation)."""
        # Remove punctuation except apostrophes in contractions
        text = re.sub(r'[^\w\s\']', ' ', text.lower())
        # Normalize whitespace
        text = ' '.join(text.split())
        return text
    
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Propose replacement based on reference lyrics."""
        
        if not gap_words:
            return []
        
        # Try to extract the correct replacement from references
        replacement_text = self._extract_replacement_from_references(
            gap_words,
            reference_contexts,
            preceding_words,
            following_words
        )
        
        if replacement_text:
            # Found a replacement in reference lyrics
            proposal = CorrectionProposal(
                word_ids=[w['id'] for w in gap_words],
                action="ReplaceWord",
                replacement_text=replacement_text,
                confidence=0.75,
                reason=f"Sound-alike error. Reference suggests: '{replacement_text}'. {classification_reasoning}",
                gap_category=self.category,
                requires_human_review=False,
                artist=self.artist,
                title=self.title
            )
            return [proposal]
        else:
            # Could not extract replacement, flag for human review
            gap_text = ' '.join(w.get('text', '') for w in gap_words)
            proposal = CorrectionProposal(
                word_ids=[w['id'] for w in gap_words],
                action="Flag",
                confidence=0.6,
                reason=f"Sound-alike error detected for '{gap_text}' but could not extract replacement from references. {classification_reasoning}",
                gap_category=self.category,
                requires_human_review=True,
                artist=self.artist,
                title=self.title
            )
            return [proposal]

