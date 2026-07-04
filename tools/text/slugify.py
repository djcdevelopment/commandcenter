import re

def slugify(text: str) -> str:
    """
    Convert text to a URL-friendly slug.
    
    Lowercases the input, replaces any run of non-alphanumeric characters 
    with a single hyphen, and strips leading/trailing hyphens.
    
    Args:
        text (str): The input string to slugify
        
    Returns:
        str: The slugified string
    """
    # Lowercase the text
    text = text.lower()
    
    # Replace any run of non-alphanumeric characters with a single hyphen
    text = re.sub(r'[^a-z0-9]+', '-', text)
    
    # Strip leading and trailing hyphens
    text = text.strip('-')
    
    return text