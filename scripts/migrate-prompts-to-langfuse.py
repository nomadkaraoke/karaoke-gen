#!/usr/bin/env python3
"""Migrate hardcoded prompts and examples to LangFuse.

This script uploads:
1. The gap-classifier prompt template
2. The few-shot examples as a dataset

Usage:
    # Dry run (preview what would be uploaded)
    python scripts/migrate-prompts-to-langfuse.py --dry-run

    # Actual migration
    python scripts/migrate-prompts-to-langfuse.py

    # Force overwrite existing prompts/datasets
    python scripts/migrate-prompts-to-langfuse.py --force

Environment Variables Required:
    LANGFUSE_PUBLIC_KEY
    LANGFUSE_SECRET_KEY
    LANGFUSE_HOST (optional, defaults to https://us.cloud.langfuse.com)
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "lyrics_transcriber_temp"))


def get_langfuse_client():
    """Initialize and return the Langfuse client."""
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_HOST", "https://us.cloud.langfuse.com")

    if not (public_key and secret_key):
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must be set")
        sys.exit(1)

    from langfuse import Langfuse

    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )


def get_prompt_template() -> str:
    """Get the prompt template with LangFuse variable placeholders.

    This template uses {{variable}} syntax for LangFuse variable substitution.
    """
    return """You are an expert at analyzing transcription errors in song lyrics. Your task is to classify gaps (mismatches between transcription and reference lyrics) into categories to determine the best correction approach.

{{song_context}}

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

{{examples_text}}

## Gap to Classify

**Gap ID:** {{gap_id}}

**Preceding Context:** {{preceding_words}}

**Gap Text:** {{gap_text}}

**Following Context:** {{following_words}}

{{references_text}}

## Important Guidelines

- If ANY reference source matches the gap text exactly (ignoring punctuation), classify as **NO_ERROR**
- Consider whether the song title/artist contains words that might appear in the gap
- Parentheses in transcription usually indicate background vocals
- Sound-alike errors are very common in song transcription
- Flag for human review when uncertain

## Your Task

Analyze this gap and respond with a JSON object matching this schema:

{
  "gap_id": "{{gap_id}}",
  "category": "<one of the 8 categories above>",
  "confidence": <float between 0 and 1>,
  "reasoning": "<detailed explanation for your classification>",
  "suggested_handler": "<name of handler or null>"
}

Provide ONLY the JSON response, no other text."""


def get_examples():
    """Get the few-shot examples from the hardcoded source."""
    from karaoke_gen.lyrics_transcriber.correction.agentic.prompts.classifier import get_hardcoded_examples

    examples = get_hardcoded_examples()

    # Flatten the examples by category into a list
    flattened = []
    for category, category_examples in examples.items():
        for ex in category_examples:
            flattened.append({
                "category": category,
                **ex
            })

    return flattened


def upload_prompt(client, dry_run: bool = False, force: bool = False) -> bool:
    """Upload the prompt template to LangFuse.

    Args:
        client: Langfuse client
        dry_run: If True, only print what would be done
        force: If True, overwrite existing prompt

    Returns:
        True if successful, False otherwise
    """
    prompt_name = "gap-classifier"
    template = get_prompt_template()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Uploading prompt: {prompt_name}")
    print(f"  Template length: {len(template)} characters")

    if dry_run:
        print("  Preview (first 500 chars):")
        print(f"  {template[:500]}...")
        return True

    try:
        # Check if prompt exists
        try:
            existing = client.get_prompt(prompt_name)
            if existing and not force:
                print(f"  Prompt '{prompt_name}' already exists. Use --force to overwrite.")
                return True
        except Exception:
            pass  # Prompt doesn't exist, which is fine

        # Create the prompt
        client.create_prompt(
            name=prompt_name,
            prompt=template,
        )
        print(f"  Successfully created prompt '{prompt_name}'")
        return True

    except Exception as e:
        print(f"  Error uploading prompt: {e}")
        return False


def upload_examples(client, dry_run: bool = False, force: bool = False) -> bool:
    """Upload the few-shot examples as a LangFuse dataset.

    Args:
        client: Langfuse client
        dry_run: If True, only print what would be done
        force: If True, overwrite existing dataset

    Returns:
        True if successful, False otherwise
    """
    dataset_name = "gap-classifier-examples"
    examples = get_examples()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Uploading dataset: {dataset_name}")
    print(f"  Total examples: {len(examples)}")

    # Count by category
    categories = {}
    for ex in examples:
        cat = ex.get("category", "unknown")
        categories[cat] = categories.get(cat, 0) + 1
    print("  Examples by category:")
    for cat, count in sorted(categories.items()):
        print(f"    {cat}: {count}")

    if dry_run:
        print("\n  Sample examples:")
        for ex in examples[:3]:
            print(f"    - {ex.get('category')}: {ex.get('gap_text', '')[:50]}...")
        return True

    try:
        # Try to get existing dataset
        dataset = None
        try:
            dataset = client.get_dataset(dataset_name)
            if dataset and not force:
                print(f"  Dataset '{dataset_name}' already exists. Use --force to overwrite.")
                return True
        except Exception:
            pass  # Dataset doesn't exist

        # Create dataset if it doesn't exist
        if dataset is None:
            dataset = client.create_dataset(
                name=dataset_name,
                description="Few-shot examples for gap classification in lyrics correction",
            )
            print(f"  Created dataset '{dataset_name}'")

        # Upload each example as a dataset item
        for i, ex in enumerate(examples):
            client.create_dataset_item(
                dataset_name=dataset_name,
                input=ex,
                metadata={
                    "category": ex.get("category"),
                    "index": i,
                },
            )

        print(f"  Successfully uploaded {len(examples)} examples to '{dataset_name}'")
        return True

    except Exception as e:
        print(f"  Error uploading dataset: {e}")
        return False


def verify_roundtrip(client) -> bool:
    """Verify that prompts and datasets can be fetched back.

    Args:
        client: Langfuse client

    Returns:
        True if verification passes, False otherwise
    """
    print("\nVerifying round-trip...")

    try:
        # Verify prompt
        prompt = client.get_prompt("gap-classifier")
        if not prompt:
            print("  Error: Could not fetch prompt 'gap-classifier'")
            return False
        print(f"  Prompt 'gap-classifier' verified (length: {len(prompt.prompt)} chars)")

        # Verify dataset
        dataset = client.get_dataset("gap-classifier-examples")
        if not dataset:
            print("  Error: Could not fetch dataset 'gap-classifier-examples'")
            return False

        item_count = len(list(dataset.items))
        print(f"  Dataset 'gap-classifier-examples' verified ({item_count} items)")

        return True

    except Exception as e:
        print(f"  Verification failed: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Migrate hardcoded prompts to LangFuse"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be uploaded without making changes",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing prompts and datasets",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing prompts/datasets can be fetched",
    )

    args = parser.parse_args()

    print("LangFuse Prompt Migration")
    print("=" * 50)

    if args.dry_run:
        print("Mode: DRY RUN (no changes will be made)")
    elif args.force:
        print("Mode: FORCE (will overwrite existing)")
    else:
        print("Mode: Normal (will skip existing)")

    # Get client
    client = get_langfuse_client()
    print(f"Connected to LangFuse at {os.getenv('LANGFUSE_HOST', 'https://us.cloud.langfuse.com')}")

    if args.verify_only:
        success = verify_roundtrip(client)
        sys.exit(0 if success else 1)

    # Upload prompt
    prompt_ok = upload_prompt(client, dry_run=args.dry_run, force=args.force)

    # Upload examples
    examples_ok = upload_examples(client, dry_run=args.dry_run, force=args.force)

    # Verify if not dry run
    if not args.dry_run and prompt_ok and examples_ok:
        verify_ok = verify_roundtrip(client)
    else:
        verify_ok = True

    # Summary
    print("\n" + "=" * 50)
    print("Summary:")
    print(f"  Prompt upload: {'OK' if prompt_ok else 'FAILED'}")
    print(f"  Examples upload: {'OK' if examples_ok else 'FAILED'}")
    if not args.dry_run:
        print(f"  Verification: {'OK' if verify_ok else 'FAILED'}")

    success = prompt_ok and examples_ok and (args.dry_run or verify_ok)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
