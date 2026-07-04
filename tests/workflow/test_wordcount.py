import unittest
from tools.text.wordcount import word_count

class TestWordCount(unittest.TestCase):

    def test_empty_string(self):
        result = word_count("")
        self.assertEqual(result, {'words': 0, 'lines': 0, 'chars': 0})

    def test_single_word(self):
        result = word_count("hello")
        self.assertEqual(result, {'words': 1, 'lines': 1, 'chars': 5})

    def test_multiple_words_mixed_whitespace(self):
        text = "hello\tworld\nfoo bar"
        result = word_count(text)
        self.assertEqual(result, {'words': 4, 'lines': 2, 'chars': 19})

    def test_multi_line_text(self):
        text = "line one\nline two\nline three"
        result = word_count(text)
        self.assertEqual(result, {'words': 6, 'lines': 3, 'chars': 28})

    def test_trailing_newline(self):
        text = "hello world\n"
        result = word_count(text)
        self.assertEqual(result, {'words': 2, 'lines': 2, 'chars': 12})

if __name__ == '__main__':
    unittest.main()