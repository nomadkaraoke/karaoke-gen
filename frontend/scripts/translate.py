#!/usr/bin/env python3
"""
LLM Translation Pipeline for Nomad Karaoke i18n.

Two-pass translation using Gemini via Vertex AI:
  1. Translate English JSON to target language
  2. Review and polish translations for fluency

Usage:
  python scripts/translate.py --messages-dir ./messages --target es de
  python scripts/translate.py --messages-dir ../karaoke-decide-i18n/frontend/messages --target es
  python scripts/translate.py --messages-dir ./messages --target es --skip-review

Requires:
  - google-genai SDK (pip install google-genai)
  - GCP Application Default Credentials (gcloud auth application-default login)
"""

import argparse
import json
import sys
from pathlib import Path

from google import genai
from google.genai import types

MODEL = "gemini-3.1-pro-preview"
PROJECT = "nomadkaraoke"
LOCATION = "global"


def load_glossary(script_dir: Path) -> dict:
    glossary_path = script_dir / "glossary.json"
    if glossary_path.exists():
        with open(glossary_path) as f:
            return json.load(f)
    return {"terms": {}}


def build_glossary_instructions(glossary: dict, target_locale: str) -> str:
    lines = []
    for term, translations in glossary["terms"].items():
        if target_locale in translations:
            val = translations[target_locale]
            if val is None:
                lines.append(f'- "{term}" → DO NOT translate, keep as "{term}"')
            else:
                lines.append(f'- "{term}" → "{val}"')
    return "\n".join(lines)


def translate_pass(
    client: genai.Client,
    english_json: str,
    target_locale: str,
    glossary_instructions: str,
) -> str:
    """Pass 1: Translate English to target language."""

    locale_names = {"es": "Spanish", "de": "German"}
    target_name = locale_names.get(target_locale, target_locale)

    response = client.models.generate_content(
        model=MODEL,
        contents=f"""Translate the following JSON message file from English to {target_name}.

## Context
This is for Nomad Karaoke (nomadkaraoke.com), a karaoke platform with two products:
- "Decide": a song discovery app that helps users find karaoke songs to sing
- "Generator": a karaoke video creation tool that generates professional karaoke videos from any song

The strings are used in the website UI, including navigation, headings, descriptions, policy pages, and a tipping page.

## Rules
1. Preserve ALL JSON keys exactly as they are — only translate the string values
2. Preserve all placeholders like {{year}}, {{count}} etc. exactly as they appear
3. Preserve all URLs, email addresses, phone numbers, and physical addresses exactly
4. Preserve all currency amounts ($5, $10, etc.) in their original format
5. Use informal "{"tú" if target_locale == "es" else "du"}" form when addressing the user

## Glossary — follow these translations exactly:
{glossary_instructions}

## JSON to translate:
```json
{english_json}
```

Return ONLY the translated JSON, no explanation or markdown code fences.""",
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="medium"),
            temperature=0.3,
        ),
    )

    return response.text


def review_pass(
    client: genai.Client,
    english_json: str,
    translated_json: str,
    target_locale: str,
    glossary_instructions: str,
) -> str:
    """Pass 2: Review and polish translations."""

    locale_names = {"es": "Spanish", "de": "German"}
    target_name = locale_names.get(target_locale, target_locale)

    response = client.models.generate_content(
        model=MODEL,
        contents=f"""Review and polish this {target_name} translation of a UI message file for Nomad Karaoke (a karaoke video generation platform).

## Your task
1. Read the original English and the {target_name} translation side by side
2. Check every string for:
   - Naturalness: Would a native {target_name} speaker find this awkward or unnatural?
   - Accuracy: Does the translation convey the same meaning as the English?
   - Consistency: Are the same terms translated the same way throughout?
   - Tone: The tone should be friendly, approachable, and professional — like a small business talking to customers
   - Form of address: Should use informal "{"tú" if target_locale == "es" else "du"}" form
3. Fix any issues you find
4. Ensure all JSON keys, placeholders, URLs, emails, phone numbers, and addresses are preserved exactly

## Glossary — these translations are mandatory:
{glossary_instructions}

## Original English:
```json
{english_json}
```

## {target_name} translation to review:
```json
{translated_json}
```

Return ONLY the improved JSON, no explanation or markdown code fences.""",
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level="medium"),
            temperature=0.3,
        ),
    )

    return response.text


def clean_json_response(text: str) -> str:
    """Strip markdown code fences if the model included them."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        text = "\n".join(lines)
    return text


def count_keys(obj: dict, prefix: str = "") -> int:
    """Count leaf keys in nested dict."""
    count = 0
    for key, value in obj.items():
        if isinstance(value, dict):
            count += count_keys(value, f"{prefix}{key}.")
        else:
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description="Translate i18n message files using Gemini via Vertex AI")
    parser.add_argument(
        "--messages-dir",
        type=Path,
        required=True,
        help="Path to messages directory containing en.json",
    )
    parser.add_argument(
        "--target",
        nargs="+",
        required=True,
        help="Target locale codes (e.g., es de)",
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip the review pass (faster, lower quality)",
    )
    args = parser.parse_args()

    # Validate
    en_path = args.messages_dir / "en.json"
    if not en_path.exists():
        print(f"Error: {en_path} not found")
        sys.exit(1)

    # Load English source
    with open(en_path) as f:
        english_json = f.read()

    # Validate it's valid JSON
    json.loads(english_json)

    script_dir = Path(__file__).parent
    glossary = load_glossary(script_dir)

    # Initialize Vertex AI client using Application Default Credentials
    client = genai.Client(
        vertexai=True,
        project=PROJECT,
        location=LOCATION,
    )

    print(f"Using model: {MODEL}")
    print(f"Project: {PROJECT}, Location: {LOCATION}")

    for locale in args.target:
        print(f"\n{'='*60}")
        print(f"Translating to: {locale}")
        print(f"{'='*60}")

        glossary_instructions = build_glossary_instructions(glossary, locale)

        # Pass 1: Translate
        print("Pass 1: Translating...")
        translated = translate_pass(client, english_json, locale, glossary_instructions)
        translated = clean_json_response(translated)

        # Validate JSON
        try:
            json.loads(translated)
        except json.JSONDecodeError as e:
            print(f"Warning: Pass 1 produced invalid JSON: {e}")
            print("Saving raw output for inspection...")
            out_path = args.messages_dir / f"{locale}.raw.json"
            with open(out_path, "w") as f:
                f.write(translated)
            print(f"Saved to {out_path}")
            continue

        if not args.skip_review:
            # Pass 2: Review
            print("Pass 2: Reviewing and polishing...")
            reviewed = review_pass(client, english_json, translated, locale, glossary_instructions)
            reviewed = clean_json_response(reviewed)

            try:
                parsed = json.loads(reviewed)
            except json.JSONDecodeError as e:
                print(f"Warning: Pass 2 produced invalid JSON: {e}")
                print("Using Pass 1 output instead.")
                parsed = json.loads(translated)
        else:
            parsed = json.loads(translated)

        # Write output
        out_path = args.messages_dir / f"{locale}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
            f.write("\n")

        print(f"Saved: {out_path}")

        # Quick stats
        en_keys = count_keys(json.loads(english_json))
        tr_keys = count_keys(parsed)
        pct = (tr_keys / en_keys * 100) if en_keys else 0
        print(f"Keys: {tr_keys}/{en_keys} ({pct:.0f}%)")

    print("\nDone!")


if __name__ == "__main__":
    main()
