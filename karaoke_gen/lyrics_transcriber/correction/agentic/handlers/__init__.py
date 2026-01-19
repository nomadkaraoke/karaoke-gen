"""Category-specific handlers for gap correction."""

from .base import BaseHandler
from .punctuation import PunctuationHandler
from .sound_alike import SoundAlikeHandler
from .background_vocals import BackgroundVocalsHandler
from .extra_words import ExtraWordsHandler
from .repeated_section import RepeatedSectionHandler
from .complex_multi_error import ComplexMultiErrorHandler
from .ambiguous import AmbiguousHandler
from .no_error import NoErrorHandler

__all__ = [
    'BaseHandler',
    'PunctuationHandler',
    'SoundAlikeHandler',
    'BackgroundVocalsHandler',
    'ExtraWordsHandler',
    'RepeatedSectionHandler',
    'ComplexMultiErrorHandler',
    'AmbiguousHandler',
    'NoErrorHandler',
]

