# Let's examine character by character
import os

text1 = '''line one
line two
line three'''
print(f"Text 1: {repr(text1)}")
print(f"Length: {len(text1)}")
for i, char in enumerate(text1):
    print(f"{i}: {repr(char)} (ord={ord(char)})")

print("\n" + "="*50)

text2 = 'hello   world\t\nfoo'
print(f"Text 2: {repr(text2)}")
print(f"Length: {len(text2)}")
for i, char in enumerate(text2):
    print(f"{i}: {repr(char)} (ord={ord(char)})")

print("\n" + "="*50)

# Let's also test what the actual expected values are by calculating them manually
print("Manual calculation for text1:")
print("'line one' = 8 chars")
print("'\n' = 1 char")
print("'line two' = 8 chars")
print("'\n' = 1 char")
print("'line three' = 10 chars")
print("Total: 28 chars")

print("\nExpected is 27, so one character is missing from my count.")
print("Let me check if there's a trailing newline that shouldn't be counted?")

# Let's also test the exact string from the test file
print("\nTesting with exact string from test file:")
exact_string = 'line one\nline two\nline three'
print(f"Exact string: {repr(exact_string)}")
print(f"Length: {len(exact_string)}")