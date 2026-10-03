import re

# =============================================================================
# Text Normalization
# =============================================================================
# These mappings normalize visually-similar Unicode characters to their standard
# ASCII equivalents. This ensures consistency in stored data and reliable search.

# Apostrophe-like characters -> standard apostrophe (U+0027)
APOSTROPHE_REPLACEMENTS = {
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK (')
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK (') - common from Word/macOS
    "\u201A": "'",  # SINGLE LOW-9 QUOTATION MARK (‚)
    "\u201B": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK (‛)
    "\u0060": "'",  # GRAVE ACCENT (`) - backtick
    "\u00B4": "'",  # ACUTE ACCENT (´)
    "\u2032": "'",  # PRIME (′)
    "\u02B9": "'",  # MODIFIER LETTER PRIME (ʹ)
    "\u02BC": "'",  # MODIFIER LETTER APOSTROPHE (ʼ)
    "\u02C8": "'",  # MODIFIER LETTER VERTICAL LINE (ˈ)
    "\u0301": "'",  # COMBINING ACUTE ACCENT (standalone, rare)
}

# Double quote-like characters -> standard double quote (U+0022)
DOUBLE_QUOTE_REPLACEMENTS = {
    "\u201C": '"',  # LEFT DOUBLE QUOTATION MARK (")
    "\u201D": '"',  # RIGHT DOUBLE QUOTATION MARK (")
    "\u201E": '"',  # DOUBLE LOW-9 QUOTATION MARK („)
    "\u201F": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK (‟)
    "\u2033": '"',  # DOUBLE PRIME (″)
    "\u02DD": '"',  # DOUBLE ACUTE ACCENT (˝)
    "\u3003": '"',  # DITTO MARK (〃) - CJK
}

# Dash-like characters -> standard hyphen-minus (U+002D)
DASH_REPLACEMENTS = {
    "\u2013": "-",  # EN DASH (–)
    "\u2014": "-",  # EM DASH (—)
    "\u2015": "-",  # HORIZONTAL BAR (―)
    "\u2212": "-",  # MINUS SIGN (−)
    "\u2010": "-",  # HYPHEN (‐)
    "\u2011": "-",  # NON-BREAKING HYPHEN (‑)
    "\u2012": "-",  # FIGURE DASH (‒)
    "\u00AD": "-",  # SOFT HYPHEN (invisible, but normalize anyway)
}

# Whitespace characters -> standard space (U+0020)
WHITESPACE_REPLACEMENTS = {
    "\u00A0": " ",  # NO-BREAK SPACE
    "\u2002": " ",  # EN SPACE
    "\u2003": " ",  # EM SPACE
    "\u2004": " ",  # THREE-PER-EM SPACE
    "\u2005": " ",  # FOUR-PER-EM SPACE
    "\u2006": " ",  # SIX-PER-EM SPACE
    "\u2007": " ",  # FIGURE SPACE
    "\u2008": " ",  # PUNCTUATION SPACE
    "\u2009": " ",  # THIN SPACE
    "\u200A": " ",  # HAIR SPACE
    "\u200B": "",   # ZERO WIDTH SPACE (remove entirely)
    "\u202F": " ",  # NARROW NO-BREAK SPACE
    "\u205F": " ",  # MEDIUM MATHEMATICAL SPACE
    "\u3000": " ",  # IDEOGRAPHIC SPACE (CJK full-width space)
    "\uFEFF": "",   # ZERO WIDTH NO-BREAK SPACE / BOM (remove entirely)
}

# Other replacements
OTHER_REPLACEMENTS = {
    "\u2026": "...",  # HORIZONTAL ELLIPSIS (…)
    "\u22EF": "...",  # MIDLINE HORIZONTAL ELLIPSIS (⋯)
}

# Combined replacement dict for normalize_text()
TEXT_NORMALIZATIONS = {
    **APOSTROPHE_REPLACEMENTS,
    **DOUBLE_QUOTE_REPLACEMENTS,
    **DASH_REPLACEMENTS,
    **WHITESPACE_REPLACEMENTS,
    **OTHER_REPLACEMENTS,
}

# Legacy dict for backwards compatibility (used by sanitize_filename)
# This is a subset focused on HTTP header safety
UNICODE_REPLACEMENTS = {
    # Curly/smart quotes -> straight quotes
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK (the one causing the bug)
    "\u201A": "'",  # SINGLE LOW-9 QUOTATION MARK
    "\u201B": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    "\u201C": '"',  # LEFT DOUBLE QUOTATION MARK
    "\u201D": '"',  # RIGHT DOUBLE QUOTATION MARK
    "\u201E": '"',  # DOUBLE LOW-9 QUOTATION MARK
    "\u201F": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
    # Other common problematic characters
    "\u2013": "-",  # EN DASH
    "\u2014": "-",  # EM DASH
    "\u2026": "...",  # HORIZONTAL ELLIPSIS
    "\u00A0": " ",  # NON-BREAKING SPACE
}


def normalize_text(text: str) -> str:
    """
    Normalize visually-similar Unicode characters to standard ASCII equivalents.

    This function standardizes text for consistency in stored data and reliable
    search/matching. It converts:
    - Curly quotes and backticks -> straight quotes
    - Various dashes (en dash, em dash, minus) -> hyphen
    - Various whitespace characters -> regular space
    - Ellipsis character -> three dots

    This function also:
    - Collapses multiple spaces to a single space
    - Strips leading/trailing whitespace

    Unlike sanitize_filename(), this does NOT:
    - Remove filesystem-unsafe characters (/, \\, :, *, ?, ", <, >, |)
    - Collapse multiple underscores
    - Strip leading/trailing periods

    This should be applied to user-facing text like artist names and song titles
    at input time to ensure data consistency.

    Args:
        text: The text to normalize

    Returns:
        Normalized text with standard ASCII equivalents, or None if input is None

    Examples:
        >>> normalize_text("Don't Stop")  # curly apostrophe
        "Don't Stop"
        >>> normalize_text("Song — Title")  # em dash
        "Song - Title"
        >>> normalize_text("Hello\u00A0World")  # non-breaking space
        "Hello World"
    """
    if text is None:
        return None

    if not isinstance(text, str):
        return text

    # Apply all normalizations
    for unicode_char, replacement in TEXT_NORMALIZATIONS.items():
        text = text.replace(unicode_char, replacement)

    # Collapse multiple spaces (but preserve intentional spacing structure)
    text = re.sub(r' {2,}', ' ', text)

    # Strip leading/trailing whitespace
    text = text.strip()

    return text


def sanitize_filename(filename):
    """
    Replace or remove characters that are unsafe for filenames.

    This function makes text safe for use in:
    - Filesystem paths
    - HTTP headers (Content-Disposition)
    - API requests (Modal, Google Drive, Dropbox)

    It applies normalize_text() first, then additionally:
    - Replaces filesystem-unsafe characters with underscores
    - Strips leading/trailing periods and spaces
    - Collapses multiple underscores/spaces

    Args:
        filename: The filename to sanitize

    Returns:
        Sanitized filename safe for filesystems and HTTP headers
    """
    if filename is None:
        return None

    # First, normalize Unicode characters
    filename = normalize_text(filename)

    if filename is None:
        return None

    # Replace problematic characters with underscores
    for char in ["\\", "/", ":", "*", "?", '"', "<", ">", "|"]:
        filename = filename.replace(char, "_")

    # Remove any trailing periods or spaces
    filename = filename.rstrip(". ")
    # Remove any leading periods or spaces
    filename = filename.lstrip(". ")
    # Replace multiple underscores with a single one
    filename = re.sub(r'_+', '_', filename)
    # Replace multiple spaces with a single one
    filename = re.sub(r' +', ' ', filename)

    return filename
