def word_count(text: str) -> dict:
    """
    Count words, lines, and characters in a text string.

    Args:
        text (str): Input text to analyze.

    Returns:
        dict: Dictionary with keys 'words', 'lines', 'chars' representing counts.
              Empty string returns all zeros.
    """
    if not text:
        return {'words': 0, 'lines': 0, 'chars': 0}

    words = len(text.split())
    lines = text.count('\n') + 1  # +1 because last line doesn't end with newline
    chars = len(text)

    return {'words': words, 'lines': lines, 'chars': chars}