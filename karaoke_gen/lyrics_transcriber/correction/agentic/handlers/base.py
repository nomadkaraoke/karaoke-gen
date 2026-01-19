"""Base handler interface for gap correction."""

from abc import ABC, abstractmethod
from typing import List, Dict, Any
from ..models.schemas import CorrectionProposal, GapCategory


class BaseHandler(ABC):
    """Base class for category-specific correction handlers."""
    
    def __init__(self, artist: str = None, title: str = None):
        """Initialize handler with song metadata.
        
        Args:
            artist: Song artist name
            title: Song title
        """
        self.artist = artist
        self.title = title
    
    @abstractmethod
    def handle(
        self,
        gap_id: str,
        gap_words: List[Dict[str, Any]],
        preceding_words: str,
        following_words: str,
        reference_contexts: Dict[str, str],
        classification_reasoning: str = ""
    ) -> List[CorrectionProposal]:
        """Process a gap and return correction proposals.
        
        Args:
            gap_id: Unique identifier for the gap
            gap_words: List of word dictionaries with id, text, start_time, end_time
            preceding_words: Context before the gap
            following_words: Context after the gap
            reference_contexts: Dictionary of reference lyrics by source
            classification_reasoning: Reasoning from the classifier
        
        Returns:
            List of CorrectionProposal objects
        """
        raise NotImplementedError
    
    @property
    @abstractmethod
    def category(self) -> GapCategory:
        """Return the gap category this handler processes."""
        raise NotImplementedError

