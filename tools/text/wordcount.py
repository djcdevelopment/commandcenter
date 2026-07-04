def word_count(text: str) -> dict:
    words = len(text.split()) if text else 0
    lines = text.count("\n")
    chars = len(text)
    return {"words": words, "lines": lines, "chars": chars}
