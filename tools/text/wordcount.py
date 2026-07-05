def word_count(text: str) -> dict:
    """
    Count words, lines, and characters in text.
    
    Args:
        text (str): Input text to analyze
        
    Returns:
        dict: Dictionary with keys 'words', 'lines', 'chars' containing their respective counts
    """
    if not text:
        return {'words': 0, 'lines': 0, 'chars': 0}
    
    lines = text.count('\n') + (1 if text else 0)
    words = len(text.split())
    chars = len(text)
    
    return {'words': words, 'lines': lines, 'chars': chars}