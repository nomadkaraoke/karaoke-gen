"""Gap classification prompt builder for agentic correction.

This module provides two modes of operation:
1. LangFuse mode: Prompts and examples fetched from LangFuse for dynamic iteration
2. Hardcoded mode: Fallback for local development when LangFuse is not configured

The main entry point is `build_classification_prompt()` which automatically
selects the appropriate mode based on LangFuse configuration.
"""

from typing import Dict, List, Optional
import yaml
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_few_shot_examples() -> Dict[str, List[Dict]]:
    """Load few-shot examples from examples.yaml if it exists."""
    examples_path = Path(__file__).parent / "examples.yaml"

    if not examples_path.exists():
        return get_hardcoded_examples()

    try:
        with open(examples_path, 'r') as f:
            data = yaml.safe_load(f)
            return data.get('examples_by_category', {})
    except Exception as e:
        logger.warning(f"Failed to load examples.yaml, using hardcoded examples: {e}")
        return get_hardcoded_examples()


def get_hardcoded_examples() -> Dict[str, List[Dict]]:
    """Hardcoded examples from gaps_review.yaml for initial training."""
    return {
        "sound_alike": [
            {
                "gap_text": "out, I'm starting over",
                "preceding": "Oh no, was it worth it? Starting",
                "following": "gonna sleep With the next person",
                "reference": "Starting now I'm starting over",
                "reasoning": "Transcription heard 'out' but reference lyrics show 'now' - common sound-alike error",
                "action": "REPLACE 'out' with 'now'"
            },
            {
                "gap_text": "And you said to watch it",
                "preceding": "You're a time, uh, uh, uh",
                "following": "just in time But to wreck",
                "reference": "You set the watch You're just in time",
                "reasoning": "Transcription heard 'And you said to watch it' but reference shows 'You set the watch You're' - sound-alike with extra word 'And'",
                "action": "REPLACE with reference text"
            }
        ],
        "background_vocals": [
            {
                "gap_text": "it? (Big business)",
                "preceding": "Oh no, was it worth it? Was it worth",
                "following": "Was it worth it? (Was it worth",
                "reference": "was it worth what you did to big business?",
                "reasoning": "Words in parentheses are background vocals not in reference lyrics",
                "action": "DELETE words in parentheses"
            },
            {
                "gap_text": "(Was it worth it?) Was",
                "preceding": "it? (Big business) Was it worth it?",
                "following": "it worth it? (Your friends)",
                "reference": "Was it worth what you did to big business?",
                "reasoning": "Parenthesized phrase is backing vocal repetition",
                "action": "DELETE parenthesized words"
            }
        ],
        "extra_words": [
            {
                "gap_text": "But to wreck my life",
                "preceding": "said to watch it just in time",
                "following": "To bring back what I left",
                "reference": "You're just in time To wreck my life",
                "reasoning": "Transcription adds filler word 'But' not in reference lyrics",
                "action": "DELETE 'But'"
            }
        ],
        "punctuation_only": [
            {
                "gap_text": "Tick- tock, you're",
                "preceding": "They got no, they got no concept of time",
                "following": "not a clock You're a time bomb",
                "reference": "Tick tock, you're not a clock",
                "reasoning": "Only difference is hyphen in 'Tick-tock' vs 'Tick tock' - stylistic",
                "action": "NO_ACTION"
            }
        ],
        "no_error": [
            {
                "gap_text": "you're telling lies Well,",
                "preceding": "You swore together forever Now",
                "following": "tell me your words They got",
                "reference_genius": "Now you're telling lies",
                "reference_lrclib": "Now you're telling me lies",
                "reasoning": "Genius reference matches transcription exactly (without 'me'), so transcription is correct",
                "action": "NO_ACTION"
            }
        ],
        "repeated_section": [
            {
                "gap_text": "You're a time bomb, baby You're",
                "preceding": "Tick-tock, you're not a clock",
                "following": "a time bomb, baby, oh",
                "reference": "You're a time bomb baby",
                "reasoning": "Reference lyrics don't show repetition, but cannot confirm without audio",
                "action": "FLAG for human review"
            }
        ],
        "complex_multi_error": [
            {
                "gap_text": "Right here, did you dance for later? That's what you said? Well, here's an answer You're out in life You have to try",
                "reference": "Five years and you fell for a waiter I'm sure he says he's an actor So you're acting like",
                "reasoning": "50-word gap with multiple sound-alike errors throughout, too complex for automatic correction",
                "action": "FLAG for human review"
            }
        ]
    }


def build_classification_prompt(
    gap_text: str,
    preceding_words: str,
    following_words: str,
    reference_contexts: Dict[str, str],
    artist: Optional[str] = None,
    title: Optional[str] = None,
    gap_id: Optional[str] = None
) -> str:
    """Build a prompt for classifying a gap in the transcription.

    This function automatically selects between LangFuse and hardcoded prompts:
    - If LangFuse is configured (LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY set),
      fetches the prompt template and examples from LangFuse.
    - Otherwise, uses hardcoded prompts for local development.

    Args:
        gap_text: The text of the gap that needs classification
        preceding_words: Text immediately before the gap
        following_words: Text immediately after the gap
        reference_contexts: Dictionary of reference lyrics from each source
        artist: Song artist name for context
        title: Song title for context
        gap_id: Identifier for the gap

    Returns:
        Formatted prompt string for the LLM

    Raises:
        LangFusePromptError: If LangFuse is configured but prompt fetch fails
    """
    from .langfuse_prompts import get_prompt_service

    service = get_prompt_service()
    return service.get_classification_prompt(
        gap_text=gap_text,
        preceding_words=preceding_words,
        following_words=following_words,
        reference_contexts=reference_contexts,
        artist=artist,
        title=title,
        gap_id=gap_id
    )


def build_classification_prompt_hardcoded(
    gap_text: str,
    preceding_words: str,
    following_words: str,
    reference_contexts: Dict[str, str],
    artist: Optional[str] = None,
    title: Optional[str] = None,
    gap_id: Optional[str] = None
) -> str:
    """Build a prompt for classifying a gap using hardcoded templates.

    This is the fallback implementation used when LangFuse is not configured.
    It is also used as the source of truth for migrating prompts to LangFuse.

    Args:
        gap_text: The text of the gap that needs classification
        preceding_words: Text immediately before the gap
        following_words: Text immediately after the gap
        reference_contexts: Dictionary of reference lyrics from each source
        artist: Song artist name for context
        title: Song title for context
        gap_id: Identifier for the gap

    Returns:
        Formatted prompt string for the LLM
    """
    examples = load_few_shot_examples()
    
    # Build few-shot examples section
    examples_text = "## Example Classifications\n\n"
    for category, category_examples in examples.items():
        if category_examples:
            examples_text += f"### {category.upper().replace('_', ' ')}\n\n"
            for ex in category_examples[:2]:  # Limit to 2 examples per category
                examples_text += f"**Gap:** {ex['gap_text']}\n"
                examples_text += f"**Context:** ...{ex.get('preceding', '')}... [GAP] ...{ex.get('following', '')}...\n"
                if 'reference' in ex:
                    examples_text += f"**Reference:** {ex['reference']}\n"
                examples_text += f"**Reasoning:** {ex['reasoning']}\n"
                examples_text += f"**Action:** {ex['action']}\n\n"
    
    # Build reference lyrics section
    references_text = ""
    if reference_contexts:
        references_text = "## Available Reference Lyrics\n\n"
        for source, context in reference_contexts.items():
            references_text += f"**{source.upper()}:** {context}\n\n"
    
    # Build song context
    song_context = ""
    if artist and title:
        song_context = f"\n## Song Context\n\n**Artist:** {artist}\n**Title:** {title}\n\nNote: The song title and artist name may help identify proper nouns or unusual words that could be mis-heard.\n"
    
    prompt = f"""You are an expert at analyzing transcription errors in song lyrics. Your task is to classify gaps (mismatches between transcription and reference lyrics) into categories to determine the best correction approach.

{song_context}

## Categories

Use these EXACT category names in your response:

1. **PUNCTUATION_ONLY**: Only difference is punctuation, capitalization, or symbols (hyphens, quotes). No text changes needed.

2. **SOUND_ALIKE**: Transcription mis-heard words that sound similar (e.g., "out" vs "now", "said to watch" vs "set the watch"). Common for homophones or similar-sounding phrases.

3. **BACKGROUND_VOCALS**: Transcription includes backing vocals (usually in parentheses) that aren't in the main reference lyrics. Should typically be removed for karaoke.

4. **EXTRA_WORDS**: Transcription adds common filler words like "And", "But", "Well" at sentence starts that aren't in reference lyrics.

5. **REPEATED_SECTION**: Transcription shows repeated chorus/lyrics that may or may not appear in condensed reference lyrics. Often needs human verification via audio.

6. **COMPLEX_MULTI_ERROR**: Large gaps (many words) with multiple different error types. Too complex for automatic correction.

7. **NO_ERROR**: At least one reference source matches the transcription exactly, indicating the transcription is correct and other references are incomplete/wrong.

8. **AMBIGUOUS**: Cannot determine correct action without listening to audio. Similar to repeated sections but less clear.

{examples_text}

## Gap to Classify

**Gap ID:** {gap_id or 'unknown'}

**Preceding Context:** {preceding_words}

**Gap Text:** {gap_text}

**Following Context:** {following_words}

{references_text}

## Important Guidelines

- If ANY reference source matches the gap text exactly (ignoring punctuation), classify as **NO_ERROR**
- Consider whether the song title/artist contains words that might appear in the gap
- Parentheses in transcription usually indicate background vocals
- Sound-alike errors are very common in song transcription
- Flag for human review when uncertain

## Your Task

Analyze this gap and respond with a JSON object matching this schema:

{{
  "gap_id": "{gap_id or 'unknown'}",
  "category": "<one of the 8 categories above>",
  "confidence": <float between 0 and 1>,
  "reasoning": "<detailed explanation for your classification>",
  "suggested_handler": "<name of handler or null>"
}}

Provide ONLY the JSON response, no other text.
"""
    
    return prompt

