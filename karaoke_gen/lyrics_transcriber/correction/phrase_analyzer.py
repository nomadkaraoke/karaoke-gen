from typing import List
import spacy
from spacy.tokens import Doc
import logging
from karaoke_gen.lyrics_transcriber.correction.text_utils import clean_text
from karaoke_gen.lyrics_transcriber.types import PhraseType, PhraseScore

# Try to import preloader (may not exist in standalone library usage)
try:
    from backend.services.spacy_preloader import get_preloaded_model

    _HAS_PRELOADER = True
except ImportError:
    _HAS_PRELOADER = False


class PhraseAnalyzer:
    """Language-agnostic phrase analyzer using spaCy"""

    def __init__(self, logger: logging.Logger, language_code: str = "en_core_web_sm"):
        """Initialize with specific language model and logger

        Args:
            logger: Logger instance to use for this analyzer
            language_code: spaCy language model to use
        """
        self.logger = logger

        # Try to use preloaded model first (avoids 60+ second load on Cloud Run)
        if _HAS_PRELOADER:
            preloaded = get_preloaded_model(language_code)
            if preloaded is not None:
                self.logger.info(f"Using preloaded SpaCy model: {language_code}")
                self.nlp = preloaded
                return

        # Fall back to loading model directly
        self.logger.info(f"Initializing PhraseAnalyzer with language model: {language_code}")
        try:
            self.nlp = spacy.load(language_code)
        except OSError:
            self.logger.info(f"Language model {language_code} not found. Attempting to download...")
            import subprocess

            try:
                subprocess.check_call(["python", "-m", "spacy", "download", language_code])
                self.nlp = spacy.load(language_code)
                self.logger.info(f"Successfully downloaded and loaded {language_code}")
            except subprocess.CalledProcessError as e:
                self.logger.error(f"Failed to download language model: {language_code}")
                raise OSError(
                    f"Language model '{language_code}' could not be downloaded. "
                    f"Please install it manually with: python -m spacy download {language_code}"
                ) from e

    def score_phrase(self, words: List[str], context: str) -> PhraseScore:
        """Score a phrase based on grammatical completeness and natural breaks.

        Args:
            words: List of words in the phrase
            context: Full text containing the phrase

        Returns:
            PhraseScore with phrase_type, natural_break_score, and length_score
        """
        # self.logger.info(f"Scoring phrase with context length {len(context)}: {' '.join(words)}")

        phrase = " ".join(words)
        phrase_doc = self.nlp(phrase)
        context_doc = self.nlp(context)

        # Get initial phrase type based on grammar
        phrase_type = self._determine_phrase_type(phrase_doc)

        # Calculate scores
        break_score = self._calculate_break_score(phrase_doc, context_doc)
        length_score = self._calculate_length_score(phrase_doc)

        # If break score is 0 (crosses boundary), override to CROSS_BOUNDARY
        if break_score == 0.0:
            phrase_type = PhraseType.CROSS_BOUNDARY

        return PhraseScore(phrase_type=phrase_type, natural_break_score=break_score, length_score=length_score)

    def _determine_phrase_type(self, doc: Doc) -> PhraseType:
        """Determine the grammatical type of a phrase using SpaCy's linguistic analysis.

        This method categorizes text into three types:
        1. COMPLETE: A grammatically complete clause with subject and predicate
           Examples: "I love you", "the cat sleeps"
           - Subject (I, the cat) + Predicate (love you, sleeps)

        2. PARTIAL: A valid but incomplete grammatical unit, which can be:
           a) Noun phrase: A group of words with a noun as the head
              Example: "the big cat"
              - Determiner (the) + Adjective (big) + Noun (cat)

           b) Verb phrase: A group of words with a verb as the head
              Example: "running fast"
              - Verb (running) + Adverb (fast)

           c) Prepositional phrase: Starting with a preposition
              Example: "in my heart"
              - Preposition (in) + Noun phrase (my heart)

           d) Adverb phrase: A group of words with an adverb as the head
              Example: "très rapidement" (French: "very quickly")
              - Adverb (très) + Adverb (rapidement)

        3. CROSS_BOUNDARY: Invalid grammatical structure
           Examples: "cat the big", "love but the"
           - Words in unnatural order or incomplete structures

        Args:
            doc: SpaCy Doc object containing the parsed text

        Returns:
            PhraseType: COMPLETE, PARTIAL, or CROSS_BOUNDARY
        """
        # self.logger.debug(f"Determining phrase type for: {doc.text}")

        # First check if it's a complete clause
        if self.is_complete_clause(doc):
            return PhraseType.COMPLETE

        # Check if it's a valid partial phrase
        if (
            self.is_valid_noun_phrase(doc)
            or self.is_valid_verb_phrase(doc)
            or self.is_valid_prep_phrase(doc)
            or self.is_valid_adverb_phrase(doc)
        ):
            # Additional check: if the phrase crosses sentence boundaries,
            # it should be CROSS_BOUNDARY even if it's grammatically valid
            if "." in doc.text:  # Simple check for sentence boundary within phrase
                return PhraseType.CROSS_BOUNDARY
            return PhraseType.PARTIAL

        return PhraseType.CROSS_BOUNDARY

    def _calculate_break_score(self, phrase_doc: Doc, context_doc: Doc) -> float:
        """Calculate how well the phrase respects natural breaks in the text.

        Scores are based on alignment with line breaks and sentence boundaries:
        1.0 - Perfect alignment (matches full line or sentence)
        0.8-0.9 - Strong alignment (matches most of a natural unit)
        0.5-0.7 - Partial alignment (matches start or end of unit)
        0.0 - Poor alignment (crosses line/sentence boundary)

        Examples from tests:
        "my heart will go on" -> 1.0 (matches full line)
        "go on and" -> 0.0 (crosses line break)
        "Hello world" -> 1.0 (matches complete sentence)
        "world How" -> 0.0 (crosses sentence boundary)
        "I wake up" -> 0.85 (strong alignment with verb phrase)
        """
        # Clean both texts while preserving structure
        phrase_text = clean_text(phrase_doc.text)
        context_text = clean_text(context_doc.text)

        # Find position in cleaned text
        phrase_start = context_text.find(phrase_text)

        if phrase_start == -1:
            return 0.0

        phrase_end = phrase_start + len(phrase_text)

        # Check line breaks first
        line_score = self.calculate_line_break_score(phrase_start, phrase_end, context_doc.text)
        if line_score in {0.0, 1.0}:  # Perfect match or crossing boundary
            return line_score

        # Then check sentence boundaries
        sentence_score = self.calculate_sentence_break_score(phrase_doc, phrase_start, phrase_end, context_doc)
        if sentence_score in {0.0, 1.0}:  # Perfect match or crossing boundary
            return sentence_score

        # Return the higher of the two scores
        return max(line_score, sentence_score)

    def _calculate_length_score(self, doc: Doc) -> float:
        """Calculate score based on phrase length and complexity.

        Scores are based on the number of meaningful linguistic units:
        - Noun chunks ("the big cat", "the mat")
        - Verbs ("sleeps")
        - Adverbial modifiers ("soundly")
        - Prepositional phrases ("on the mat")

        Scoring scale:
        0.0 - No meaningful units
        0.9 - One unit (e.g., "the cat")
        1.0 - Two units (e.g., "the cat sleeps")
        0.8 - Three units (e.g., "the big cat sleeps quickly")
        0.6 - Four or more units (e.g., "the big cat sleeps soundly on the mat")

        Examples from tests:
        "the cat" -> 1 unit (noun chunk) -> 0.9
        "the cat sleeps" -> 2 units (noun chunk + verb) -> 1.0
        "the big cat sleeps soundly on the mat" -> 4 units (noun chunk + verb + adverb + prep phrase) -> 0.6
        """
        # self.logger.debug(f"Calculating length score for: {doc.text}")
        # Count meaningful linguistic units
        units = 0

        # Count noun chunks
        units += len(list(doc.noun_chunks))

        # Count verbs
        units += len([token for token in doc if token.pos_ == "VERB"])

        # Count adverbial modifiers
        units += len([token for token in doc if token.dep_ == "advmod"])

        # Count prepositional phrases
        units += len([token for token in doc if token.dep_ == "prep"])

        # Score based on complexity
        if units == 0:
            return 0.0
        elif units == 1:
            return 0.9  # Simple phrase
        elif units == 2:
            return 1.0  # Optimal complexity
        elif units == 3:
            return 0.8  # Slightly complex
        return 0.6  # Too complex

    def is_complete_clause(self, doc: Doc) -> bool:
        """Check if the text forms a complete clause.

        Different languages mark subject-verb relationships differently:
        English/French:
        - Subject has nsubj/nsubjpass dependency
        - Verb is ROOT

        Spanish:
        - Sometimes marks pronoun as ROOT
        - Verb can be marked as flat/aux
        """
        # self.logger.debug(f"Checking if complete clause: {doc.text}")
        # Standard subject-verb pattern (English/French)
        standard_pattern = any(token.dep_ in {"nsubj", "nsubjpass"} for token in doc) and any(
            token.dep_ == "ROOT" and token.pos_ == "VERB" for token in doc
        )

        # Spanish pronoun-verb pattern
        spanish_pattern = (
            len(doc) == 2  # Two-word phrase
            and doc[0].pos_ == "PRON"  # First word is pronoun
            and doc[1].pos_ in {"VERB", "AUX", "ADJ"}  # Second word is verb-like
            and doc[1].dep_ in {"flat", "aux"}  # Common Spanish dependencies
        )

        return standard_pattern or spanish_pattern

    def is_valid_noun_phrase(self, doc: Doc) -> bool:
        """Check if the text is a valid noun phrase like "the big cat".

        Valid noun phrases:
        - "the cat" (determiner + noun)
        - "the big cat" (determiner + adjective + noun)
        - "my heart" (possessive + noun)
        """
        # self.logger.debug(f"Checking if valid noun phrase: {doc.text}")
        chunks = list(doc.noun_chunks)
        if not chunks:
            return False

        # The noun phrase should be the entire text
        chunk = chunks[0]
        if not (chunk.start == 0 and chunk.end == len(doc)):
            return False

        # Check for valid noun phrase structure
        root_nouns = [t for t in doc if t.dep_ == "ROOT" and t.pos_ in {"NOUN", "PROPN"}]
        compounds = [t for t in doc if t.dep_ == "compound"]

        return len(root_nouns) == 1 and len(compounds) == 0

    def is_valid_verb_phrase(self, doc: Doc) -> bool:
        """Check if the text is a valid verb phrase like "running fast".

        A verb phrase must:
        1. Contain a verb as the first content word
        2. Only use valid verb phrase dependencies
        3. Have correct word order (verb before modifiers)
        """
        # self.logger.debug(f"Checking if valid verb phrase: {doc.text}")
        VALID_DEPS = {
            "ROOT",  # Main verb
            "advmod",  # Adverbial modifier
            "dobj",  # Direct object
            "prt",  # Verb particle
            "prep",  # Preposition
            "pobj",  # Object of preposition
            "compound:prt",  # Phrasal verb particle
        }

        # Find all verbs
        verbs = [token for token in doc if token.pos_ == "VERB"]
        if not verbs:
            return False

        # Check if first content word is a verb
        content_words = [token for token in doc if token.pos_ not in {"DET", "PUNCT"}]
        if not content_words or content_words[0].pos_ != "VERB":
            return False

        # Check dependencies
        has_valid_deps = all(token.dep_ in VALID_DEPS for token in doc)
        return has_valid_deps

    def is_valid_prep_phrase(self, doc: Doc) -> bool:
        """Check if the text is a valid prepositional phrase.

        Examples:
        - "in my heart" (English)
        - "dans la maison" (French: "in the house")
        - "en la casa" (Spanish: "in the house")
        """
        # self.logger.debug(f"Checking if valid prep phrase: {doc.text}")
        starts_with_prep = doc[0].pos_ == "ADP"
        has_content = len(doc) > 1
        has_valid_structure = any(t.dep_ == "pobj" for t in doc) or (  # English style
            doc[0].dep_ == "case" and any(t.dep_ == "ROOT" for t in doc)
        )  # French/Spanish style

        return starts_with_prep and has_content and has_valid_structure

    def is_valid_adverb_phrase(self, doc: Doc) -> bool:
        """Check if the text is a valid adverbial phrase.

        Examples:
        - "très rapidement" (French: "very quickly")
        - "muy rápido" (Spanish: "very fast")
        - "very quickly" (English)

        Valid patterns:
        - ADV + ADV/ADJ (modifier + main adverb/adjective)
        - First word must modify second word
        - Second word must be the root
        """
        # self.logger.debug(f"Checking if valid adverb phrase: {doc.text}")
        # Check basic structure
        if len(doc) != 2:  # Only handle two-word phrases for now
            return False

        # Check parts of speech
        has_valid_pos = all(token.pos_ in {"ADV", "ADJ"} for token in doc)
        if not has_valid_pos:
            return False

        first_word = doc[0]
        second_word = doc[1]

        # The first word must be a modifier
        if first_word.dep_ != "advmod":
            return False

        # The second word must be the root
        if second_word.dep_ != "ROOT":
            return False

        # Check that the first word modifies the second
        if first_word.head != second_word:
            return False

        return True

    def calculate_line_break_score(self, phrase_start: int, phrase_end: int, context_text: str) -> float:
        """Calculate score based on line break alignment."""
        # Clean the context text while preserving line breaks
        cleaned_lines = [clean_text(line) for line in context_text.split("\n")]
        cleaned_context = "\n".join(cleaned_lines)

        # Track current position in cleaned context
        current_pos = 0

        # Recalculate positions using cleaned text
        for line in cleaned_lines:
            if not line:  # Skip empty lines
                current_pos += 1  # Account for newline
                continue

            line_start = current_pos
            line_end = line_start + len(line)

            # Perfect match with a full line
            if phrase_start == line_start and phrase_end == line_end:
                return 1.0

            # Strong alignment with start of line
            if phrase_start == line_start:
                coverage = (phrase_end - phrase_start) / len(line)
                if coverage >= 0.7:
                    return 0.9
                elif coverage >= 0.3:
                    return 0.8

            # Strong alignment with end of line
            if phrase_end == line_end:
                coverage = (phrase_end - phrase_start) / len(line)
                if coverage >= 0.7:
                    return 0.9
                elif coverage >= 0.3:
                    return 0.8

            # Update position for next line
            current_pos = line_end + 1  # +1 for newline

        # Check if phrase crosses any line boundary
        if any(
            phrase_start < cleaned_context.find("\n", i) < phrase_end for i in range(len(cleaned_context)) if "\n" in cleaned_context[i:]
        ):
            return 0.0

        return 0.5

    def calculate_sentence_break_score(self, phrase_doc: Doc, phrase_start: int, phrase_end: int, context_doc: Doc) -> float:
        """Calculate score based on sentence boundary alignment."""
        # self.logger.debug(f"Calculating sentence break score for: {phrase_doc.text}")
        for sent in context_doc.sents:
            sent_start = sent.start_char
            sent_end = sent.end_char

            # Perfect match with a full sentence
            if phrase_start == sent_start and phrase_end == sent_end:
                return 1.0

            # Strong alignment with most of a sentence
            if phrase_start >= sent_start and phrase_end <= sent_end:
                has_verb = any(token.pos_ == "VERB" for token in phrase_doc)
                has_subject = any(token.dep_ in {"nsubj", "nsubjpass"} for token in phrase_doc)

                phrase_len = phrase_end - phrase_start
                sent_len = sent_end - sent_start
                coverage = phrase_len / sent_len

                if has_verb and has_subject:
                    return 0.85
                elif has_verb and coverage > 0.3:
                    return 0.8
                elif coverage > 0.5:
                    return 0.8
                return 0.7

            # Crosses sentence boundary
            if any(phrase_start < s.start_char < phrase_end for s in context_doc.sents):
                return 0.0

        return 0.5
