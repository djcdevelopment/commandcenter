def char_histogram(text: str) -> dict:
    """
    Returns a dictionary mapping each distinct character in text to its occurrence count.
    
    Args:
        text (str): The input string to analyze
        
    Returns:
        dict: A dictionary where keys are characters and values are their counts
    """
    histogram = {}
    for char in text:
        if char in histogram:
            histogram[char] += 1
        else:
            histogram[char] = 1
    return histogram