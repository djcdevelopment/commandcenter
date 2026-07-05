def word_count(text: str) -> dict:
    """
    Count words, lines, and characters in text.
    
    Args:
        text (str): The input text to analyze
        
    Returns:
        dict: A dictionary with keys 'words', 'lines', and 'chars'
              containing their respective counts
    """
    if not text:
        return {'words': 0, 'lines': 0, 'chars': 0}
    
    # Count words using split() which handles all whitespace
    words = len(text.split())
    
    # Count lines - number of newline characters + 1 if doesn't end with newline
    lines = text.count('\n')
    if not text.endswith('\n'):
        lines += 1
    
    # Count characters - just the length of string
    chars = len(text)
    
    return {'words': words, 'lines': lines, 'chars': chars}