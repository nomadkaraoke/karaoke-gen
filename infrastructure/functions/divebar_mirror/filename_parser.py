"""
Parse karaoke filenames into structured metadata.

Handles the various naming conventions found across Divebar karaoke brands:
  Pattern 1: "BRAND-001 - Artist - Title.ext" (disc ID prefix)
  Pattern 2: "Artist - Title - (Brand Tag).ext" (brand suffix)
  Pattern 3: "Artist - Title.ext" (brand from folder name)
"""

import os
import re
import unicodedata


# Common karaoke suffixes to strip from titles
_KARAOKE_SUFFIXES = re.compile(
    r"\s*[\(\[](karaoke|instrumental|backing track|no vocals?|kj version|"
    r"karaoke version|with vocals?|demo)[\)\]]\s*$",
    re.IGNORECASE,
)

# Brand code pattern at start of filename: "ABC-001" or "ABC001" or "ABC 001"
_DISC_ID_PATTERN = re.compile(
    r"^([A-Z][A-Z0-9]{1,10})[\s\-]?(\d{1,5}(?:-\d{1,3})?)\s*-\s*"
)

# Brand tag in parentheses at end of filename (before extension)
_BRAND_SUFFIX_PATTERN = re.compile(r"\s*-?\s*\(([^)]+)\)\s*$")

# Video and audio extensions we care about
KARAOKE_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".webm", ".mov",  # Video
    ".mp3", ".cdg",  # CDG+MP3 pairs
    ".zip",  # Zipped CDG+MP3
}

# Files to skip during indexing
IGNORED_FILES = {
    ".DS_Store", ".keep", ".gitkeep", "desktop.ini", "Thumbs.db",
    "kj-nomad.index.json",
}


def normalize_for_search(text: str) -> str:
    """Normalize text for fuzzy matching: lowercase, strip diacritics, remove special chars."""
    if not text:
        return ""
    # NFD decomposition then strip combining characters (diacritics)
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    # Strip common karaoke suffixes
    text = _KARAOKE_SUFFIXES.sub("", text)
    # Remove "the " prefix for matching
    if text.startswith("the "):
        text = text[4:]
    # Normalize whitespace and strip non-alphanumeric (keep spaces)
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_format(filename: str) -> str:
    """Detect the karaoke format from a filename."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".zip":
        return "zip"
    if ext == ".cdg":
        return "cdg"
    if ext == ".mp3":
        return "mp3"
    if ext in (".mp4", ".mkv", ".avi", ".webm", ".mov"):
        return ext[1:]  # Strip the dot
    return ext[1:] if ext else "unknown"


def should_index_file(filename: str) -> bool:
    """Check if a file should be included in the index."""
    if filename in IGNORED_FILES or filename.startswith("."):
        return False
    ext = os.path.splitext(filename)[1].lower()
    return ext in KARAOKE_EXTENSIONS


def parse_filename(filename: str, brand_folder: str = "") -> dict:
    """
    Parse a karaoke filename into structured metadata.

    Args:
        filename: The file name (not path), e.g. "NOMAD-0001 - Artist - Title.mp4"
        brand_folder: The parent brand folder name, used as fallback brand

    Returns:
        dict with keys: artist, title, disc_id, brand_code, brand_name, format
    """
    # Strip extension
    name, ext = os.path.splitext(filename)
    file_format = detect_format(filename)

    result = {
        "artist": None,
        "title": None,
        "disc_id": None,
        "brand_code": None,
        "brand_name": brand_folder or None,
        "format": file_format,
    }

    # Try Pattern 1: "BRAND-001 - Artist - Title"
    disc_match = _DISC_ID_PATTERN.match(name)
    if disc_match:
        result["brand_code"] = disc_match.group(1)
        result["disc_id"] = f"{disc_match.group(1)}-{disc_match.group(2)}"
        remainder = name[disc_match.end():]
        parts = [p.strip() for p in remainder.split(" - ")]
        if len(parts) >= 2:
            result["artist"] = parts[0]
            result["title"] = " - ".join(parts[1:])
        elif len(parts) == 1 and parts[0]:
            result["title"] = parts[0]
        # Strip brand suffix from title if present
        if result["title"]:
            result["title"] = _BRAND_SUFFIX_PATTERN.sub("", result["title"])
        return result

    # Try Pattern 2: "Artist - Title - (Brand Tag)"
    brand_suffix_match = _BRAND_SUFFIX_PATTERN.search(name)
    if brand_suffix_match:
        result["brand_name"] = brand_suffix_match.group(1)
        name_without_brand = name[:brand_suffix_match.start()]
        parts = [p.strip() for p in name_without_brand.split(" - ")]
        if len(parts) >= 2:
            result["artist"] = parts[0]
            result["title"] = " - ".join(parts[1:])
        elif len(parts) == 1 and parts[0]:
            result["title"] = parts[0]
        return result

    # Pattern 3: "Artist - Title" (brand from folder name)
    parts = [p.strip() for p in name.split(" - ")]
    if len(parts) >= 2:
        result["artist"] = parts[0]
        result["title"] = " - ".join(parts[1:])
    elif len(parts) == 1 and parts[0]:
        result["title"] = parts[0]

    return result
