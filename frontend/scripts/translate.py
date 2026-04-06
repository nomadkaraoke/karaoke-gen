#!/usr/bin/env python3
"""
LLM Translation Pipeline for Nomad Karaoke i18n.

Two-pass translation using Gemini via Vertex AI:
  1. Translate English JSON to target language
  2. Review and polish translations for fluency

Supports incremental (delta) translation, parallel execution,
and retries with exponential backoff.

Usage:
  # Translate one locale (incremental by default)
  python scripts/translate.py --messages-dir ./messages --target pt

  # Translate all supported locales
  python scripts/translate.py --messages-dir ./messages --target all

  # Force full retranslation
  python scripts/translate.py --messages-dir ./messages --target es --full

  # Skip review pass (faster, lower quality)
  python scripts/translate.py --messages-dir ./messages --target es --skip-review

Requires:
  - google-genai SDK (pip install google-genai)
  - GCP Application Default Credentials (gcloud auth application-default login)
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from translation_cache import TranslationCache

from google import genai
from google.genai import types

MODEL = "gemini-3.1-pro-preview"
PROJECT = "nomadkaraoke"
LOCATION = "global"

MAX_CONCURRENT = 5
MAX_RETRIES = 3

# All supported locales and their display names (33 total, excluding 'en')
LOCALE_NAMES = {
    "ar": "Arabic",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "es": "Spanish",
    "fi": "Finnish",
    "fr": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "nb": "Norwegian",
    "nl": "Dutch",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sv": "Swedish",
    "th": "Thai",
    "tl": "Filipino",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh": "Chinese (Simplified)",
}

# RTL languages
RTL_LOCALES = {"ar", "he"}

# Informal "you" forms per language (where it matters)
INFORMAL_YOU = {
    "es": 'tú',
    "de": 'du',
    "fr": 'tu',
    "it": 'tu',
    "pt": 'você',
    "nl": 'je',
    "pl": 'ty',
    "ru": 'ты',
    "uk": 'ти',
    "cs": 'ty',
    "sk": 'ty',
    "hr": 'ti',
    "ro": 'tu',
    "hu": 'te',
    "el": 'εσύ',
    "tr": 'sen',
    "hi": 'तुम',
    "vi": 'bạn',
    "sv": 'du',
    "nb": 'du',
    "da": 'du',
    "fi": 'sinä',
    "ca": 'tu',
}


def load_glossary(script_dir: Path) -> dict:
    glossary_path = script_dir / "glossary.json"
    if glossary_path.exists():
        with open(glossary_path) as f:
            return json.load(f)
    return {"terms": {}}


def build_glossary_instructions(glossary: dict, target_locale: str) -> str:
    lines = []
    for term, translations in glossary.get("terms", {}).items():
        if target_locale in translations:
            val = translations[target_locale]
            if val is None:
                lines.append(f'- "{term}" → DO NOT translate, keep as "{term}"')
            else:
                lines.append(f'- "{term}" → "{val}"')
    if not lines:
        # Fallback: at minimum, brand names should not be translated
        lines.append('- "Nomad Karaoke" → DO NOT translate, keep as "Nomad Karaoke"')
        lines.append('- "Generator" → DO NOT translate, keep as "Generator"')
        lines.append('- "YouTube" → DO NOT translate, keep as "YouTube"')
    return "\n".join(lines)


def _you_form_instruction(target_locale: str) -> str:
    """Build the form-of-address instruction for a given locale."""
    if target_locale in INFORMAL_YOU:
        return f'Use informal "{INFORMAL_YOU[target_locale]}" form when addressing the user.'
    return ""


# --- Delta / incremental helpers ---

def flatten_keys(obj: dict, prefix: str = "") -> dict:
    """Flatten nested dict to dot-separated keys with leaf values."""
    result = {}
    for key, value in obj.items():
        full_key = f"{prefix}{key}" if prefix else key
        if isinstance(value, dict):
            result.update(flatten_keys(value, f"{full_key}."))
        else:
            result[full_key] = value
    return result


def extract_subset(obj: dict, dot_keys: set) -> dict:
    """Extract a subset of a nested dict by dot-separated key paths."""
    result = {}
    for dot_key in sorted(dot_keys):
        parts = dot_key.split(".")
        # Navigate source
        src = obj
        for part in parts:
            if isinstance(src, dict) and part in src:
                src = src[part]
            else:
                src = None
                break
        if src is None:
            continue
        # Build nested structure in result
        dst = result
        for part in parts[:-1]:
            dst = dst.setdefault(part, {})
        dst[parts[-1]] = src
    return result


def merge_deep(base: dict, overlay: dict) -> dict:
    """Deep merge overlay into base, returning a new dict."""
    result = dict(base)
    for key, value in overlay.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_deep(result[key], value)
        else:
            result[key] = value
    return result


def compute_changed_keys(current: dict, snapshot: dict) -> set:
    """Return set of dot-separated keys that are new or changed."""
    current_flat = flatten_keys(current)
    snapshot_flat = flatten_keys(snapshot)
    changed = set()
    for key, value in current_flat.items():
        if key not in snapshot_flat or snapshot_flat[key] != value:
            changed.add(key)
    return changed


# --- Clean / validate helpers ---

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


# --- Async translation with retries ---

async def translate_pass_async(
    client: genai.Client,
    english_json: str,
    target_locale: str,
    glossary_instructions: str,
) -> str:
    """Pass 1: Translate English to target language (async with retry)."""
    target_name = LOCALE_NAMES.get(target_locale, target_locale)
    you_form = _you_form_instruction(target_locale)

    prompt = f"""Translate the following JSON message file from English to {target_name}.

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
5. {you_form if you_form else "Use a natural, friendly tone appropriate for the language."}

## Glossary — follow these translations exactly:
{glossary_instructions}

## JSON to translate:
```json
{english_json}
```

Return ONLY the translated JSON, no explanation or markdown code fences."""

    return await _call_with_retry(client, prompt)


async def review_pass_async(
    client: genai.Client,
    english_json: str,
    translated_json: str,
    target_locale: str,
    glossary_instructions: str,
) -> str:
    """Pass 2: Review and polish translations (async with retry)."""
    target_name = LOCALE_NAMES.get(target_locale, target_locale)
    you_form = _you_form_instruction(target_locale)

    prompt = f"""Review and polish this {target_name} translation of a UI message file for Nomad Karaoke (a karaoke video generation platform).

## Your task
1. Read the original English and the {target_name} translation side by side
2. Check every string for:
   - Naturalness: Would a native {target_name} speaker find this awkward or unnatural?
   - Accuracy: Does the translation convey the same meaning as the English?
   - Consistency: Are the same terms translated the same way throughout?
   - Tone: The tone should be friendly, approachable, and professional — like a small business talking to customers
   - Form of address: {you_form if you_form else "Use a natural, friendly tone."}
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

Return ONLY the improved JSON, no explanation or markdown code fences."""

    return await _call_with_retry(client, prompt)


async def _call_with_retry(client: genai.Client, prompt: str) -> str:
    """Call Gemini with exponential backoff retry."""
    for attempt in range(MAX_RETRIES):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="medium"),
                    temperature=0.3,
                ),
            )
            return response.text
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                raise
            wait = 2 ** (attempt + 1)
            print(f"  Retry {attempt + 1}/{MAX_RETRIES} after error: {e}")
            print(f"  Waiting {wait}s...")
            await asyncio.sleep(wait)


async def translate_locale(
    client: genai.Client,
    semaphore: asyncio.Semaphore,
    english_json_str: str,
    english_data: dict,
    target_locale: str,
    messages_dir: Path,
    glossary: dict,
    skip_review: bool,
    full_mode: bool,
    snapshot_data: dict | None,
    completed: list,
    total: int,
    cache: TranslationCache | None = None,
    dry_run: bool = False,
) -> bool:
    """Translate a single locale. Returns True on success."""
    async with semaphore:
        target_name = LOCALE_NAMES.get(target_locale, target_locale)
        print(f"\n[{target_locale}] Starting translation to {target_name}...")

        glossary_instructions = build_glossary_instructions(glossary, target_locale)

        # Determine what to translate (full vs delta)
        json_to_translate = english_json_str
        delta_keys = None
        existing_data = None

        if not full_mode and snapshot_data is not None:
            delta_keys = compute_changed_keys(english_data, snapshot_data)
            if not delta_keys:
                completed.append(target_locale)
                print(f"[{target_locale}] No changes detected, skipping. ({len(completed)}/{total})")
                return True

            # Load existing translation if it exists
            locale_path = messages_dir / f"{target_locale}.json"
            if locale_path.exists():
                with open(locale_path) as f:
                    existing_data = json.load(f)

                # Extract only changed keys from English
                subset = extract_subset(english_data, delta_keys)
                json_to_translate = json.dumps(subset, ensure_ascii=False, indent=2)
                print(f"[{target_locale}] Delta mode: {len(delta_keys)} changed keys (of {count_keys(english_data)} total)")
            else:
                print(f"[{target_locale}] No existing translation, doing full translation")

        # --- Cache lookup ---
        cached_translations: dict[str, str] = {}  # dot_key -> translated string
        uncached_english: dict[str, str] = {}  # dot_key -> english string
        if cache is not None:
            cache.download(target_locale)
            # Flatten the strings we need to translate
            flat_to_translate = flatten_keys(json.loads(json_to_translate))
            for dot_key, en_value in flat_to_translate.items():
                if not isinstance(en_value, str):
                    continue
                hit = cache.lookup(en_value, target_locale)
                if hit is not None:
                    cached_translations[dot_key] = hit
                else:
                    uncached_english[dot_key] = en_value

            stats = cache.stats(target_locale)
            total_strings = len(cached_translations) + len(uncached_english)
            print(f"[{target_locale}] Cache: {stats['hits']} hits, {stats['misses']} misses out of {total_strings} strings")

            if dry_run:
                completed.append(target_locale)
                print(f"[{target_locale}] DRY RUN — would translate {len(uncached_english)} strings via Gemini ({len(completed)}/{total})")
                return True

            if uncached_english:
                # Build subset JSON with only uncached strings for Gemini
                uncached_keys = set(uncached_english.keys())
                data_to_translate = json.loads(json_to_translate)
                subset = extract_subset(data_to_translate, uncached_keys)
                json_to_translate = json.dumps(subset, ensure_ascii=False, indent=2)
                print(f"[{target_locale}] Sending {len(uncached_english)} uncached strings to Gemini (skipping {len(cached_translations)} cached)")
            elif cached_translations:
                # All strings cached — reconstruct from cache, skip Gemini entirely
                print(f"[{target_locale}] All {len(cached_translations)} strings found in cache, skipping Gemini")
                # Reconstruct nested dict from cached flat translations
                parsed = {}
                for dot_key, translated_value in cached_translations.items():
                    parts = dot_key.split(".")
                    dst = parsed
                    for part in parts[:-1]:
                        dst = dst.setdefault(part, {})
                    dst[parts[-1]] = translated_value

                # Merge with existing data if in delta mode
                if existing_data is not None and delta_keys is not None:
                    parsed = merge_deep(existing_data, parsed)

                # Write output
                out_path = messages_dir / f"{target_locale}.json"
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(parsed, f, ensure_ascii=False, indent=2)
                    f.write("\n")

                completed.append(target_locale)
                en_keys = count_keys(english_data)
                tr_keys = count_keys(parsed)
                pct = (tr_keys / en_keys * 100) if en_keys else 0
                print(f"[{target_locale}] Saved (from cache): {out_path} — Keys: {tr_keys}/{en_keys} ({pct:.0f}%) ({len(completed)}/{total})")
                return True
        elif dry_run:
            flat_to_translate = flatten_keys(json.loads(json_to_translate))
            completed.append(target_locale)
            print(f"[{target_locale}] DRY RUN — would translate {len(flat_to_translate)} strings via Gemini ({len(completed)}/{total})")
            return True

        # Pass 1: Translate
        print(f"[{target_locale}] Pass 1: Translating...")
        t0 = time.time()
        translated = await translate_pass_async(client, json_to_translate, target_locale, glossary_instructions)
        translated = clean_json_response(translated)
        t1 = time.time()
        print(f"[{target_locale}] Pass 1 done ({t1 - t0:.1f}s)")

        # Validate JSON
        try:
            parsed = json.loads(translated)
        except json.JSONDecodeError as e:
            print(f"[{target_locale}] ERROR: Pass 1 produced invalid JSON: {e}")
            out_path = messages_dir / f"{target_locale}.raw.json"
            with open(out_path, "w") as f:
                f.write(translated)
            print(f"[{target_locale}] Saved raw output to {out_path}")
            completed.append(target_locale)
            print(f"[{target_locale}] FAILED ({len(completed)}/{total})")
            return False

        if not skip_review:
            # Pass 2: Review
            print(f"[{target_locale}] Pass 2: Reviewing and polishing...")
            t0 = time.time()
            reviewed = await review_pass_async(
                client, json_to_translate, translated, target_locale, glossary_instructions
            )
            reviewed = clean_json_response(reviewed)
            t1 = time.time()
            print(f"[{target_locale}] Pass 2 done ({t1 - t0:.1f}s)")

            try:
                parsed = json.loads(reviewed)
            except json.JSONDecodeError as e:
                print(f"[{target_locale}] Warning: Pass 2 produced invalid JSON: {e}")
                print(f"[{target_locale}] Using Pass 1 output instead.")
                parsed = json.loads(translated)

        # Store new translations in cache and merge cached translations back
        if cache is not None:
            # Store Gemini-translated strings in cache (keyed by English text)
            flat_parsed = flatten_keys(parsed)
            # We need the English source for these keys to use as cache key
            flat_english_source = flatten_keys(json.loads(json_to_translate))
            for dot_key, translated_value in flat_parsed.items():
                if isinstance(translated_value, str) and dot_key in flat_english_source:
                    en_text = flat_english_source[dot_key]
                    if isinstance(en_text, str):
                        cache.store(en_text, target_locale, translated_value)

            # Merge cached translations back into parsed result
            if cached_translations:
                for dot_key, translated_value in cached_translations.items():
                    parts = dot_key.split(".")
                    dst = parsed
                    for part in parts[:-1]:
                        dst = dst.setdefault(part, {})
                    dst[parts[-1]] = translated_value

            cache.upload(target_locale)

        # Merge delta into existing if applicable
        if existing_data is not None and delta_keys is not None:
            parsed = merge_deep(existing_data, parsed)

        # Write output
        out_path = messages_dir / f"{target_locale}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False, indent=2)
            f.write("\n")

        completed.append(target_locale)
        en_keys = count_keys(english_data)
        tr_keys = count_keys(parsed)
        pct = (tr_keys / en_keys * 100) if en_keys else 0
        print(f"[{target_locale}] Saved: {out_path} — Keys: {tr_keys}/{en_keys} ({pct:.0f}%) ({len(completed)}/{total})")
        return True


async def async_main(args):
    # Validate
    en_path = args.messages_dir / "en.json"
    if not en_path.exists():
        print(f"Error: {en_path} not found")
        sys.exit(1)

    # Load English source
    with open(en_path) as f:
        english_json_str = f.read()
    english_data = json.loads(english_json_str)

    script_dir = Path(__file__).parent
    glossary = load_glossary(script_dir)

    # Resolve target locales
    if "all" in args.target:
        locales = sorted(LOCALE_NAMES.keys())
    else:
        locales = args.target
        # Validate locale codes
        for loc in locales:
            if loc not in LOCALE_NAMES:
                print(f"Warning: '{loc}' not in LOCALE_NAMES dict, will use code as name")

    # Load snapshot for delta mode
    snapshot_path = args.messages_dir / ".en-snapshot.json"
    snapshot_data = None
    if not args.full and snapshot_path.exists():
        with open(snapshot_path) as f:
            snapshot_data = json.load(f)
        print(f"Loaded snapshot for delta comparison ({count_keys(snapshot_data)} keys)")
    elif not args.full:
        print("No snapshot found — will do full translation (use --full to force)")

    # Initialize Vertex AI client
    client = genai.Client(
        vertexai=True,
        project=PROJECT,
        location=LOCATION,
    )

    cache = TranslationCache(
        bucket_name=args.cache_bucket,
        enabled=not args.no_cache,
    )

    print(f"Using model: {MODEL}")
    print(f"Project: {PROJECT}, Location: {LOCATION}")
    print(f"Locales: {', '.join(locales)} ({len(locales)} total)")
    print(f"Mode: {'full' if args.full or snapshot_data is None else 'incremental (delta)'}")
    print(f"Review: {'skip' if args.skip_review else 'enabled'}")
    print(f"Cache: {'disabled' if args.no_cache else 'enabled'} (bucket: {args.cache_bucket})")
    if args.dry_run:
        print(f"DRY RUN: will report stats without translating")
    print(f"Max concurrent: {MAX_CONCURRENT}")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    completed: list[str] = []
    total = len(locales)

    tasks = [
        translate_locale(
            client=client,
            semaphore=semaphore,
            english_json_str=english_json_str,
            english_data=english_data,
            target_locale=locale,
            messages_dir=args.messages_dir,
            glossary=glossary,
            skip_review=args.skip_review,
            full_mode=args.full or snapshot_data is None,
            snapshot_data=snapshot_data,
            completed=completed,
            total=total,
            cache=cache,
            dry_run=args.dry_run,
        )
        for locale in locales
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Summary
    succeeded = sum(1 for r in results if r is True)
    failed = sum(1 for r in results if r is not True)

    # Only save snapshot if all translations succeeded
    if failed == 0:
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(english_data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"\nSnapshot saved: {snapshot_path}")
    else:
        print(f"\nSnapshot NOT saved (some translations failed — will retry on next run)")
    print(f"\nDone! {succeeded}/{total} locales succeeded", end="")
    if failed:
        print(f", {failed} failed")
        for locale, result in zip(locales, results):
            if result is not True:
                if isinstance(result, Exception):
                    print(f"  {locale}: {result}")
                else:
                    print(f"  {locale}: failed")
    else:
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Translate i18n message files using Gemini via Vertex AI"
    )
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
        help='Target locale codes (e.g., es de pt) or "all" for all supported locales',
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip the review pass (faster, lower quality)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force full retranslation (ignore snapshot/delta)",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass GCS translation cache (force fresh Gemini translation)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be translated without calling Gemini or writing files",
    )
    parser.add_argument(
        "--cache-bucket",
        default="nomadkaraoke-translation-cache",
        help="GCS bucket name for translation cache (default: nomadkaraoke-translation-cache)",
    )
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
