import re


def clean_text(text: str) -> str:
    """Clean text by removing punctuation and normalizing whitespace.

    Args:
        text: Text to clean

    Returns:
        Cleaned text with:
        - All text converted to lowercase
        - Multiple spaces/whitespace collapsed to single space
        - Leading/trailing whitespace removed
        - Hyphens and forward slashes replaced with spaces
        - Apostrophes and other punctuation removed
    """
    # Convert to lowercase
    text = text.lower()

    # Replace hyphens and forward slashes with spaces
    text = re.sub(r"[-/]", " ", text)
    
    # Remove apostrophes and other punctuation
    text = re.sub(r"[^\w\s]", "", text)

    # Normalize whitespace (collapse multiple spaces, remove leading/trailing)
    text = " ".join(text.split())

    return text
