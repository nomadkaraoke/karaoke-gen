"""Registry for mapping gap categories to handlers."""

from typing import Dict, Type
from .base import BaseHandler
from .punctuation import PunctuationHandler
from .sound_alike import SoundAlikeHandler
from .background_vocals import BackgroundVocalsHandler
from .extra_words import ExtraWordsHandler
from .repeated_section import RepeatedSectionHandler
from .complex_multi_error import ComplexMultiErrorHandler
from .ambiguous import AmbiguousHandler
from .no_error import NoErrorHandler
from ..models.schemas import GapCategory


class HandlerRegistry:
    """Registry for mapping gap categories to their handler classes."""
    
    _handlers: Dict[GapCategory, Type[BaseHandler]] = {
        GapCategory.PUNCTUATION_ONLY: PunctuationHandler,
        GapCategory.SOUND_ALIKE: SoundAlikeHandler,
        GapCategory.BACKGROUND_VOCALS: BackgroundVocalsHandler,
        GapCategory.EXTRA_WORDS: ExtraWordsHandler,
        GapCategory.REPEATED_SECTION: RepeatedSectionHandler,
        GapCategory.COMPLEX_MULTI_ERROR: ComplexMultiErrorHandler,
        GapCategory.AMBIGUOUS: AmbiguousHandler,
        GapCategory.NO_ERROR: NoErrorHandler,
    }
    
    @classmethod
    def get_handler(cls, category: GapCategory, artist: str = None, title: str = None) -> BaseHandler:
        """Get a handler instance for the given category.
        
        Args:
            category: Gap category
            artist: Song artist name
            title: Song title
        
        Returns:
            Handler instance for the category
        
        Raises:
            ValueError: If category is not registered
        """
        handler_class = cls._handlers.get(category)
        if not handler_class:
            raise ValueError(f"No handler registered for category: {category}")
        
        return handler_class(artist=artist, title=title)
    
    @classmethod
    def register_handler(cls, category: GapCategory, handler_class: Type[BaseHandler]):
        """Register a custom handler for a category.
        
        Args:
            category: Gap category
            handler_class: Handler class to register
        """
        cls._handlers[category] = handler_class

