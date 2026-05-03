"""Shared 4-method syllable counter.

Extracted from SyllablesMatchHandler so multiple components can reuse it
without duplicating the heavy spacy / NLTK initialisation.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import nltk
import pyphen
import spacy
import syllables
from nltk.corpus import cmudict
from spacy_syllables import SpacySyllables

try:
    from backend.services.spacy_preloader import get_preloaded_model
    from backend.services.nltk_preloader import get_preloaded_cmudict

    _HAS_PRELOADER = True
except ImportError:
    _HAS_PRELOADER = False


_TOKEN_RE = re.compile(r"[A-Za-z']+")


class SyllableCounter:
    """Counts syllables using 4 independent methods. Returns lists, never single ints."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger(__name__)
        init_start = time.time()

        _ = SpacySyllables  # silence unused-import warning

        if _HAS_PRELOADER:
            preloaded = get_preloaded_model("en_core_web_sm")
            if preloaded is not None:
                self.nlp = preloaded
                if "syllables" not in self.nlp.pipe_names:
                    self.nlp.add_pipe("syllables", after="tagger")
                self._init_nltk_resources()
                self.logger.info(
                    "Initialised SyllableCounter in %.2fs (preloaded)",
                    time.time() - init_start,
                )
                return

        try:
            self.nlp = spacy.load("en_core_web_sm")
        except OSError as exc:
            raise OSError(
                "spacy model 'en_core_web_sm' not installed. "
                "Run: python -m spacy download en_core_web_sm"
            ) from exc

        if "syllables" not in self.nlp.pipe_names:
            self.nlp.add_pipe("syllables", after="tagger")

        self._init_nltk_resources()
        self.logger.info(
            "Initialised SyllableCounter in %.2fs (lazy)",
            time.time() - init_start,
        )

    def _init_nltk_resources(self) -> None:
        self.dic = pyphen.Pyphen(lang="en_US")

        if _HAS_PRELOADER:
            preloaded = get_preloaded_cmudict()
            if preloaded is not None:
                self.cmudict = preloaded
                return

        try:
            self.cmudict = cmudict.dict()
        except LookupError:
            nltk.download("cmudict")
            self.cmudict = cmudict.dict()

    @staticmethod
    def _tokenise(line: str) -> list[str]:
        return _TOKEN_RE.findall(line)

    def _count_spacy(self, words: list[str]) -> int:
        if not words:
            return 0
        text = " ".join(words)
        doc = self.nlp(text)
        return sum(token._.syllables_count or 1 for token in doc)

    def _count_pyphen(self, words: list[str]) -> int:
        total = 0
        for word in words:
            hyphenated = self.dic.inserted(word)
            total += len(hyphenated.split("-")) if hyphenated else 1
        return total

    def _count_nltk(self, words: list[str]) -> int:
        total = 0
        for word in words:
            w = word.lower()
            if w in self.cmudict:
                total += len([ph for ph in self.cmudict[w][0] if ph[-1].isdigit()])
            else:
                total += 1
        return total

    def _count_lib(self, words: list[str]) -> int:
        return sum(syllables.estimate(word) for word in words)

    def count_per_word(self, words: list[str]) -> list[int]:
        if not words:
            return [0, 0, 0, 0]
        return [
            self._count_spacy(words),
            self._count_pyphen(words),
            self._count_nltk(words),
            self._count_lib(words),
        ]

    def count_per_line(self, line: str) -> list[int]:
        return self.count_per_word(self._tokenise(line))

    @staticmethod
    def any_method_within(
        candidate_counts: list[int],
        target_counts: list[int],
        tolerance: int,
    ) -> bool:
        """True iff some pair of counters across candidate x target agrees within tolerance."""
        if not candidate_counts or not target_counts:
            return False
        return any(
            abs(c - t) <= tolerance
            for c in candidate_counts
            for t in target_counts
        )

    @staticmethod
    def min_delta(
        candidate_counts: list[int],
        target_counts: list[int],
    ) -> int:
        """Minimum |c - t| across all 4x4 method pairs."""
        if not candidate_counts or not target_counts:
            return 0
        return min(
            abs(c - t)
            for c in candidate_counts
            for t in target_counts
        )
