# Let's debug what the actual test strings are
import os

# Read the test file to see exact content
with open('tests/workflow/test_wordcount.py', 'r') as f:
    content = f.read()
    
print("=== TEST FILE CONTENT ===")
print(content)

# Let's also manually count what we think should happen
multiline_text = '''line one\nline two\nline three'''
print(f"\nMultiline text: {repr(multiline_text)}")
print(f"Length: {len(multiline_text)}")
print(f"Newlines: {multiline_text.count(chr(10))}")

mixed_whitespace = 'hello   world\t\nfoo'
print(f"\nMixed whitespace text: {repr(mixed_whitespace)}")
print(f"Length: {len(mixed_whitespace)}")
print(f"Newlines: {mixed_whitespace.count(chr(10))}")

trailing_newline = 'hello\n'
print(f"\nTrailing newline text: {repr(trailing_newline)}")
print(f"Length: {len(trailing_newline)}")
print(f"Newlines: {trailing_newline.count(chr(10))}")