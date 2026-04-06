#!/usr/bin/env python3
"""
Translation Validation Script for Nomad Karaoke i18n.

Checks all translation files against en.json for:
  - 100% key parity (no missing or extra keys)
  - No missing {placeholder} variables
  - No empty string values
  - Valid JSON

Usage:
  python scripts/validate-translations.py --messages-dir ./messages
  python scripts/validate-translations.py --messages-dir ./messages --locale es pt

Exit code 0 = all checks pass, 1 = issues found.
"""

import argparse
import json
import re
import sys
from pathlib import Path


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


def extract_placeholders(text: str) -> set:
    """Extract {placeholder} names from a string."""
    return set(re.findall(r"\{(\w+)\}", str(text)))


def validate_locale(en_flat: dict, locale_path: Path, keys_only: bool = False) -> list[str]:
    """Validate a single locale file against English. Returns list of issues."""
    issues = []
    locale = locale_path.stem

    # Check valid JSON
    try:
        with open(locale_path) as f:
            raw = f.read()
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return [f"[{locale}] Invalid JSON: {e}"]

    tr_flat = flatten_keys(data)

    # Check key parity
    en_keys = set(en_flat.keys())
    tr_keys = set(tr_flat.keys())

    missing = en_keys - tr_keys
    extra = tr_keys - en_keys

    if missing:
        for key in sorted(missing):
            issues.append(f"[{locale}] Missing key: {key}")

    if extra:
        for key in sorted(extra):
            issues.append(f"[{locale}] Extra key: {key}")

    if keys_only:
        return issues

    # Check placeholders and empty values for shared keys
    for key in sorted(en_keys & tr_keys):
        en_val = en_flat[key]
        tr_val = tr_flat[key]

        # Empty string check
        if isinstance(tr_val, str) and tr_val.strip() == "":
            issues.append(f"[{locale}] Empty value: {key}")

        # Placeholder check
        if isinstance(en_val, str) and isinstance(tr_val, str):
            en_placeholders = extract_placeholders(en_val)
            tr_placeholders = extract_placeholders(tr_val)

            missing_ph = en_placeholders - tr_placeholders
            extra_ph = tr_placeholders - en_placeholders

            if missing_ph:
                issues.append(
                    f"[{locale}] Missing placeholder(s) in '{key}': {', '.join(sorted(missing_ph))}"
                )
            if extra_ph:
                issues.append(
                    f"[{locale}] Extra placeholder(s) in '{key}': {', '.join(sorted(extra_ph))}"
                )

    return issues


def main():
    parser = argparse.ArgumentParser(
        description="Validate translation files against en.json"
    )
    parser.add_argument(
        "--messages-dir",
        type=Path,
        required=True,
        help="Path to messages directory containing en.json",
    )
    parser.add_argument(
        "--locale",
        nargs="*",
        help="Specific locale(s) to validate (default: all *.json except en.json)",
    )
    parser.add_argument(
        "--keys-only",
        action="store_true",
        help="Only check key parity (skip placeholder and empty value checks)",
    )
    args = parser.parse_args()

    en_path = args.messages_dir / "en.json"
    if not en_path.exists():
        print(f"Error: {en_path} not found")
        sys.exit(1)

    with open(en_path) as f:
        en_data = json.load(f)
    en_flat = flatten_keys(en_data)

    print(f"English source: {len(en_flat)} keys")

    # Find locale files to validate
    if args.locale:
        locale_paths = [args.messages_dir / f"{loc}.json" for loc in args.locale]
        locale_paths = [p for p in locale_paths if p.exists()]
        missing = [loc for loc in args.locale if not (args.messages_dir / f"{loc}.json").exists()]
        if missing:
            print(f"Warning: locale files not found: {', '.join(missing)}")
    else:
        locale_paths = sorted(
            p for p in args.messages_dir.glob("*.json")
            if p.name != "en.json" and not p.name.startswith(".")
        )

    if not locale_paths:
        print("No translation files found to validate.")
        sys.exit(0)

    print(f"Validating {len(locale_paths)} locale(s): {', '.join(p.stem for p in locale_paths)}\n")

    all_issues = []
    for locale_path in locale_paths:
        issues = validate_locale(en_flat, locale_path, keys_only=args.keys_only)
        if issues:
            all_issues.extend(issues)
            print(f"  {locale_path.stem}: {len(issues)} issue(s)")
        else:
            print(f"  {locale_path.stem}: OK")

    if all_issues:
        print(f"\n{len(all_issues)} issue(s) found:\n")
        for issue in all_issues:
            print(f"  {issue}")
        sys.exit(1)
    else:
        print(f"\nAll {len(locale_paths)} locale(s) passed validation.")
        sys.exit(0)


if __name__ == "__main__":
    main()
