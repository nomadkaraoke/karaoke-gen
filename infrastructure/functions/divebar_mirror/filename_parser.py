"""
Parse karaoke filenames into structured metadata.

Community karaoke brands use many naming conventions. Rather than a handful of
rigid patterns, this parser is *folder-brand-aware*: the top-level Drive folder
reliably names the brand, so we use it to recognise and strip the brand token
wherever it sits (prefix, suffix, bracket, disc-id) and then split the remainder
into artist / title. Brand stripping is gated on the known folder brand so we
never eat real song text like "(Live)", "(Banda Remix)" or "(G)I-DLE".

Returned dict keys: artist, title, disc_id, brand_code, brand_name, format.
"""

import os
import re
import unicodedata

# Generic karaoke/source tags to strip from the end of a title, in () or [].
# Matches any bracket whose content mentions "karaoke" ("(WTF Karaoke)",
# "[DJ Sauly Karaoke]", "(Karaoke HD)") or starts with another generic tag
# ("(instrumental)", "(backing track)").
_KARAOKE_TAG = re.compile(
    r"\s*[\(\[]\s*(?:[^)\]]*karaoke[^)\]]*|"
    r"(?:instrumental|backing track|no vocals?|kj version|with vocals?|demo)[^)\]]*)"
    r"[\)\]]\s*$",
    re.IGNORECASE,
)
# Back-compat alias (normalize_for_search historically referenced this name).
_KARAOKE_SUFFIXES = _KARAOKE_TAG

# disc-id PREFIX token followed by a " - " or plain-space separator. The token
# (e.g. "FATBIRD-163", "IK00-01", "KFNO101", "RABZ-0001", "Lopenash-002") must
# contain both a letter and a digit; acceptance is further gated in code (code
# all-uppercase, or the code matches the folder brand) so alphanumeric artists
# like "2Pac", "311" or "M83" are never eaten.
_DISC_ID_PREFIX = re.compile(r"^([A-Za-z0-9][A-Za-z0-9.\-]*?)(?:\s*-\s+|\s+)(?=\S)")
# disc-id SUFFIX at end: " - SOKC-0306", " ATG0072", " IMBK-0076". Captures the
# whole token (preserving any internal dash) so disc_id stays literal.
_DISC_ID_SUFFIX = re.compile(r"[\s\-]+([A-Z]{2,6}-?\d{2,5}(?:-\d{1,3})?)\s*$")
# Leading "(CODE) " brand prefix (Sandell "(SDK) ...").
_PAREN_CODE_PREFIX = re.compile(r"^\(\s*([A-Za-z0-9]{1,6})\s*\)\s+")
# A leading pure track-number segment, e.g. "02" or "08 (and a half)".
_LEADING_TRACK_NO = re.compile(r"^\d{1,3}(?:\s*\(.*?\))?$")

# Curated folder -> brand code map. Aligned to real KaraokeNerds community codes
# where confident (so the secondary brand_match xref can also fire). Overrides a
# prefix/suffix-derived code (disc_id keeps the literal). Folders with no
# confident code are omitted (artist/title fix already surfaces them via the
# primary exact xref). Keys are lowercased exact folder names.
FOLDER_CODE = {
    "funbox karaoke": "FBK",
    "sandell karaoke": "SDK",
    "bellysings karaoke": "BELLY",
    "dj sauly collection": "DJS",
    "playkaraoke": "PLAY",
    "reekies karaoke": "REEKIES",
    "cereal killer karaoke": "CKK",
    "wizzyoke": "WIZZY",
    "brilliant trash karaoke": "BTRASH",
    "lopenash": "LOPE",
    "monster tracks": "MTRAX",
    "popeoke": "POPE",
    "kaddaok": "KADDA",
    "potos power plant productions": "PPPP",
    "april's choice karaoke": "APRIL",
    "the nerdy singer karaoke": "TNS",
    "mobile karaoke unit": "MKU",
    "atg karaoke - zipped mp3+g": "ATG",
    "it might be karaoke": "IMBK",
    "mix tape karaoke": "MIXTAPE",
    "fortekaraoke discord": "EBK",
    "steve-o karaoke cult": "SOKC",
}

# Descriptor words dropped when fuzzy-matching a brand token against a folder name.
_BRAND_NOISE = {
    "karaoke", "collection", "cdg", "files", "file", "discord", "videos", "video",
    "mp4", "mp3", "zipped", "presents", "studio", "made", "the", "of", "and",
}

KARAOKE_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".webm", ".mov",  # Video
    ".mp3", ".cdg",  # CDG+MP3 pairs
    ".zip",  # Zipped CDG+MP3
}

IGNORED_FILES = {
    ".DS_Store", ".keep", ".gitkeep", "desktop.ini", "Thumbs.db",
    "kj-nomad.index.json",
}


def _strip_diacritics(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def normalize_for_search(text: str) -> str:
    """Normalize text for fuzzy matching: lowercase, strip diacritics/special chars."""
    if not text:
        return ""
    text = _strip_diacritics(text)
    text = text.lower().strip()
    text = _KARAOKE_TAG.sub("", text)
    if text.startswith("the "):
        text = text[4:]
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _brand_key(folder: str) -> str:
    """Significant-word signature of a folder/brand name for fuzzy comparison."""
    base = re.sub(r"[^\w\s]", " ", _strip_diacritics(folder or "").lower())
    words = [w for w in base.split() if w and w not in _BRAND_NOISE]
    return "".join(words)


def _brand_acronym(folder: str) -> str:
    # Initials of ALL words (brands often acronym the full name, e.g.
    # "The Nerdy Singer Karaoke" -> TNSK, "Steve-O Karaoke Cult" -> SOKC).
    words = re.findall(r"[A-Za-z0-9]+", _strip_diacritics(folder or "").lower())
    return "".join(w[0] for w in words).upper()


def _folder_code(folder: str) -> str | None:
    return FOLDER_CODE.get((folder or "").strip().lower())


def _matches_folder_brand(token: str, folder: str) -> bool:
    """True if a stripped token looks like this folder's brand (not song text)."""
    if not token or not folder:
        return False
    tk = _brand_key(token)
    fk = _brand_key(folder)
    if tk and fk and (tk == fk or (len(tk) >= 4 and (tk in fk or fk in tk))):
        return True
    up = token.strip().strip("()[]").upper()
    if up and (up == _brand_acronym(folder) or up == (_folder_code(folder) or "\0")):
        return True
    return False


def detect_format(filename: str) -> str:
    """Detect karaoke file format from extension."""
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".zip":
        return "zip"
    if ext == ".cdg":
        return "cdg"
    if ext == ".mp3":
        return "mp3"
    if ext in (".mp4", ".mkv", ".avi", ".webm", ".mov"):
        return ext[1:]
    return ext[1:] if ext else "unknown"


def should_index_file(filename: str) -> bool:
    """Whether a file should be included in the index."""
    if filename in IGNORED_FILES or filename.startswith("."):
        return False
    ext = os.path.splitext(filename)[1].lower()
    return ext in KARAOKE_EXTENSIONS


def _strip_trailing_noise(text: str) -> str:
    """Remove a trailing generic karaoke tag and dangling separators."""
    prev = None
    while prev != text:
        prev = text
        text = _KARAOKE_TAG.sub("", text).strip()
        text = re.sub(r"[\s\-]+$", "", text).strip()
    return text


def _disc_code(token: str) -> str:
    """Derive the brand code (stem) from a disc-id token.

    "IK00-01" -> "IK00", "FATBIRD-163" -> "FATBIRD", "C11K-0000A" -> "C11K",
    "KFNO101" -> "KFNO", "Lopenash-002" -> "Lopenash".
    """
    m = re.match(r"^(.*?)-\d[\w]*$", token)
    if m:
        return m.group(1)
    m = re.match(r"^([A-Za-z]+)\d+$", token)
    if m:
        return m.group(1)
    return token


def _try_prefix(name: str, folder: str) -> tuple[str, str | None, str | None, bool]:
    """Strip a leading brand token.

    Returns (remainder, brand_code, disc_id, disc_prefix_stripped).
    """
    # 1. disc-id prefix (token contains a letter and a digit; accepted only when
    #    the code is all-uppercase or matches the folder brand).
    m = _DISC_ID_PREFIX.match(name)
    if m:
        token = m.group(1)
        if re.search(r"[A-Za-z]", token) and re.search(r"\d", token):
            code = _disc_code(token)
            num_part = token[len(code):]
            # "strong" disc-id: code separated from number by a hyphen, or the
            # number has >=3 digits. Prevents alphanumeric artists like "UB40",
            # "D12", "AC3" (uppercase but not disc-shaped) from being eaten.
            strong = "-" in num_part or len(re.findall(r"\d", num_part)) >= 3
            if _matches_folder_brand(code, folder) or (
                code.isupper() and len(code) >= 2 and strong
            ):
                return name[m.end():].strip(), code, token, True
    # 2. "(CODE) " paren prefix
    m = _PAREN_CODE_PREFIX.match(name)
    if m:
        return name[m.end():].strip(), m.group(1).upper(), None, False
    # 3. "CODE - " / "Brand - " prefix (no number), gated on folder match
    m = re.match(r"^(.{1,30}?)\s+-\s+(?=\S)", name)
    if m:
        token = m.group(1).strip()
        if _matches_folder_brand(token, folder):
            code = token.upper() if re.fullmatch(r"[A-Za-z0-9]{1,8}", token) else None
            return name[m.end():].strip(), code, None, False
    return name, None, None, False


def _strip_brand_suffix(core: str, folder: str) -> str:
    """Strip a trailing [Brand]/(Brand)/ - Brand token when it matches the folder."""
    # bracket / paren suffix
    m = re.search(r"\s*[\(\[]([^)\]]+)[\)\]]\s*$", core)
    if m and _matches_folder_brand(m.group(1), folder):
        return core[: m.start()].strip()
    # " - Brand" dash suffix (only if there is something before it to keep)
    if " - " in core:
        head, _, last = core.rpartition(" - ")
        if head and _matches_folder_brand(last, folder):
            return head.strip()
    return core


def parse_filename(filename: str, brand_folder: str = "") -> dict:
    """Parse a karaoke filename into structured metadata (see module docstring)."""
    name, _ext = os.path.splitext(filename)
    # Normalise spaced en/em dashes to the standard " - " artist/title separator.
    name = name.replace(" – ", " - ").replace(" — ", " - ").strip()

    result = {
        "artist": None,
        "title": None,
        "disc_id": None,
        "brand_code": None,
        "brand_name": brand_folder or None,
        "format": detect_format(filename),
    }

    # 1. leading brand token (disc-id / paren-code / folder-name prefix)
    core, brand_code, disc_id, disc_prefix_stripped = _try_prefix(name, brand_folder)
    if disc_prefix_stripped:
        # Some brands write "CODE-0002 -Artist" (dash hugging the artist); drop the
        # leftover leading separator so the artist isn't "-Artist".
        core = core.lstrip(" -–").strip()

    # 2. trailing generic karaoke tag (so a disc-id suffix is reachable)
    core = _strip_trailing_noise(core)

    # 3. disc-id SUFFIX (strong signal; unconditional)
    if disc_id is None:
        m = _DISC_ID_SUFFIX.search(core)
        if m:
            token = m.group(1)
            core = core[: m.start()].strip()
            core = _strip_trailing_noise(core)
            brand_code = brand_code or _disc_code(token)
            disc_id = token

    # 4. trailing brand token in brackets/parens/dash, gated on folder
    core = _strip_brand_suffix(core, brand_folder)
    core = _strip_trailing_noise(core)

    # 5. split remainder into artist / title. Drop a leading track-number segment
    #    only when it followed a disc-id prefix (e.g. "MIX001 - 02 - Artist - T"),
    #    so numeric artists like "311" are never mistaken for a track number.
    parts = [p.strip() for p in core.split(" - ") if p.strip()]
    if (
        disc_prefix_stripped
        and len(parts) >= 3
        and _LEADING_TRACK_NO.match(parts[0])
    ):
        parts = parts[1:]
    if len(parts) >= 2:
        result["artist"] = parts[0]
        result["title"] = " - ".join(parts[1:])
    elif len(parts) == 1:
        result["title"] = parts[0]

    if result["title"]:
        result["title"] = _KARAOKE_TAG.sub("", result["title"]).strip()

    # 6. brand_code: curated folder map overrides; else captured code
    result["disc_id"] = disc_id
    result["brand_code"] = _folder_code(brand_folder) or brand_code
    return result
