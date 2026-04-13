"""Message normalizer for the production error monitoring service.

Replaces variable parts of error messages (IDs, timestamps, paths, etc.)
with stable placeholders so the same logical error always produces the same
pattern hash, regardless of which specific resource or timestamp was involved.

Normalizer rules are applied in order — more specific patterns first.
"""

import hashlib
import re

from backend.services.error_monitor.config import MAX_NORMALIZED_MESSAGE_LENGTH

# ---------------------------------------------------------------------------
# Compiled regex patterns (applied in order — more specific first)
# ---------------------------------------------------------------------------

_NORMALIZERS: list[tuple[re.Pattern, str]] = [
    # 1. GCS paths: gs://bucket/path/to/object  →  gs://<BUCKET>/<PATH>
    #    Must come before Firestore and URL normalization.
    (
        re.compile(r"gs://([^/\s]+)/([^\s]+)"),
        r"gs://<BUCKET>/<PATH>",
    ),
    # 1b. GCS bucket root (no object path): gs://bucket/  or  gs://bucket
    #     Uses a lookahead so the slash/end-of-string is not consumed, and uses
    #     a character class that excludes '<' to avoid re-matching placeholders.
    (
        re.compile(r"gs://([^/<\s]+)(?=[/\s]|$)"),
        r"gs://<BUCKET>",
    ),
    # 2. Firestore document paths  (collection/doc/…/doc  — at least 2 segments)
    #    Negative lookbehind for '.' prevents matching inside domain names (URLs).
    #    Negative lookbehind for '/' prevents matching inside already-replaced paths.
    (
        re.compile(
            r"(?<![./])\b([a-zA-Z_][a-zA-Z0-9_-]*/[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+){1,})\b"
        ),
        r"<DOC_PATH>",
    ),
    # 3. UUIDs  (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<ID>",
    ),
    # 4. ISO 8601 timestamps  (2024-01-15T10:30:45Z / …+05:30 / ….123Z)
    (
        re.compile(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})"
        ),
        "<TS>",
    ),
    # 5. Epoch timestamps: 10+ digit number optionally followed by fractional seconds
    (
        re.compile(r"\b\d{10,}(?:\.\d+)?\b"),
        "<EPOCH>",
    ),
    # 6. Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "<EMAIL>",
    ),
    # 7. IP addresses (IPv4)  — must come before large-number replacement
    (
        re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
        "<IP>",
    ),
    # 8. URLs — strip to domain only (https://host/path → https://host)
    #    Handles http and https; preserves the scheme+domain for context.
    (
        re.compile(r"https?://([A-Za-z0-9.\-]+(:\d+)?)/[^\s]*"),
        r"https://\1",
    ),
    # 9. Job IDs in paths  (/jobs/<alphanumeric>)
    (
        re.compile(r"/jobs/[A-Za-z0-9]+"),
        "/jobs/<ID>",
    ),
    # 10. Firebase UIDs (20–28 mixed-case alphanumeric characters)
    #     Firebase Auth UIDs are typically 28 chars; allow 20-28 for flexibility.
    #     Must contain both letters and digits (to avoid replacing plain words).
    (
        re.compile(r"\b(?=[A-Za-z0-9]{20,28}\b)(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{20,28}\b"),
        "<UID>",
    ),
    # 11. Hex/alphanumeric IDs: 8+ chars containing both letters and digits
    #     (e.g. git SHAs, Cloud Run revision IDs, job IDs)
    #     Uses a lookahead to require at least one digit AND one letter.
    (
        re.compile(r"\b(?=[a-fA-F0-9]*[a-fA-F][a-fA-F0-9]*\d|[a-fA-F0-9]*\d[a-fA-F0-9]*[a-fA-F])[a-fA-F0-9]{8,}\b"),
        "<ID>",
    ),
    # 12. Large numbers (4+ digits)  — must come after epoch/IP/UUID replacements
    (
        re.compile(r"\b\d{4,}\b"),
        "<NUM>",
    ),
]


def normalize_message(message: str) -> str:
    """Normalize a raw log message by replacing variable parts with placeholders.

    Applies each normalizer rule in order (more specific patterns first) and
    truncates the result to MAX_NORMALIZED_MESSAGE_LENGTH characters.

    Args:
        message: Raw log message string.

    Returns:
        Normalized message string, at most MAX_NORMALIZED_MESSAGE_LENGTH chars.
    """
    if not message:
        return ""

    result = message
    for pattern, replacement in _NORMALIZERS:
        result = pattern.sub(replacement, result)

    return result[:MAX_NORMALIZED_MESSAGE_LENGTH]


def compute_pattern_hash(service: str, normalized_message: str) -> str:
    """Compute a stable SHA-256 fingerprint for an error pattern.

    Args:
        service: The originating service name (e.g. "karaoke-backend").
        normalized_message: The output of normalize_message().

    Returns:
        64-character lowercase hex SHA-256 digest.
    """
    payload = f"{service}:{normalized_message}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
