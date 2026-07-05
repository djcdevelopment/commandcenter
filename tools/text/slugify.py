import re

def slugify(text: str) -> str:
    if not text:
        return ""
    # Convert to lowercase
    text = text.lower()
    # Replace runs of whitespace and punctuation with a single hyphen
    text = re.sub(r'[^a-z0-9]+', '-', text)
    # Strip leading/trailing hyphens
    text = text.strip('-')
    # Collapse repeated hyphens into one
    text = re.sub(r'-+', '-', text)
    return text