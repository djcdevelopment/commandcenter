import unittest
from tools.text.wordcount import word_count

class TestWordCount(unittest.TestCase):
    
    def test_empty_string(self):
        result = word_count('')
        self.assertEqual(result, {'words': 0, 'lines': 0, 'chars': 0})
    
    def test_single_word(self):
        result = word_count('hello')
        self.assertEqual(result, {'words': 1, 'lines': 1, 'chars': 5})
    
    def test_multiple_words_mixed_whitespace(self):
        result = word_count('hello   world\t\nfoo')
        self.assertEqual(result, {'words': 3, 'lines': 2, 'chars': 18})
    
    def test_multiline_text(self):
        result = word_count('line1\nline2\nline3')
        self.assertEqual(result, {'words': 3, 'lines': 3, 'chars': 17})
    
    def test_trailing_newline_handling(self):
        result = word_count('hello\n')
        self.assertEqual(result, {'words': 1, 'lines': 2, 'chars': 6})

if __name__ == '__main__':
    unittest.main()