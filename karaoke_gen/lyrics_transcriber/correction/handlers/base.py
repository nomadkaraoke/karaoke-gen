from abc import ABC, abstractmethod
from typing import List, Optional, Tuple, Dict, Any
import logging

from karaoke_gen.lyrics_transcriber.types import GapSequence, WordCorrection


class GapCorrectionHandler(ABC):
    """Base class for gap correction handlers."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)

    @abstractmethod
    def can_handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
        """Check if this handler can process the given gap.
        
        Args:
            gap: The gap sequence to check
            data: Optional dictionary containing additional data like word_map
            
        Returns:
            Tuple of (can_handle, handler_data)
        """
        pass

    @abstractmethod
    def handle(self, gap: GapSequence, data: Optional[Dict[str, Any]] = None) -> List[WordCorrection]:
        """Process the gap and return any corrections.
        
        Args:
            gap: The gap sequence to process
            data: Optional dictionary containing additional data like word_map
            
        Returns:
            List of corrections to apply
        """
        pass

    def _validate_data(self, data: Optional[Dict[str, Any]]) -> bool:
        """Validate that required data is present.
        
        Args:
            data: The data dictionary to validate
            
        Returns:
            True if data is valid, False otherwise
        """
        if not data or "word_map" not in data:
            self.logger.error("No word_map provided in data")
            return False
        return True
