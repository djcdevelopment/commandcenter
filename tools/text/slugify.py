def slugify(text: str) -> str:
    """
    Convert a string to a slug.
    - Lowercases the input
    - Replaces any run of non-alphanumeric characters with a single hyphen
    - Strips leading/trailing hyphens
    
    Examples:
        "Hello,  World!" -> "hello-world"
        "" -> ""
        "  --A_B--  " -> "a-b"
    """
    if not text:
        return ""
    
    # Lowercase the text
    text = text.lower()
    
    # Replace any run of non-alphanumeric characters with a single hyphen
    import re
    text = re.sub(r'[^a-z0-9]+', '-', text)
    
    # Strip leading/trailing hyphens
    return text.strip('-')
