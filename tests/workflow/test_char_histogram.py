import unittest
from tools.text.char_histogram import char_histogram

class TestCharHistogram(unittest.TestCase):
    
    def test_empty_string(self):
        self.assertEqual(char_histogram(""), {})
        
    def test_single_character(self):
        self.assertEqual(char_histogram("a"), {"a": 1})
        
    def test_repeated_characters(self):
        self.assertEqual(char_histogram("aaa"), {"a": 3})
        
    def test_mixed_case_letters(self):
        self.assertEqual(char_histogram("AbC"), {"A": 1, "b": 1, "C": 1})
        
    def test_string_with_spaces_newlines(self):
        self.assertEqual(char_histogram("a b\nc"), {"a": 1, " ": 1, "b": 1, "\n": 1, "c": 1})

if __name__ == '__main__':
    unittest.main()