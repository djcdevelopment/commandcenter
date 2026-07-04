import unittest
from tools.text.wordcount import word_count


class TestWordCount(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(word_count(""), {"words": 0, "lines": 0, "chars": 0})

    def test_single_word(self):
        self.assertEqual(word_count("hello"), {"words": 1, "lines": 0, "chars": 5})

    def test_multiple_words_mixed_whitespace(self):
        result = word_count("foo  bar\tbaz")
        self.assertEqual(result["words"], 3)
        self.assertEqual(result["lines"], 0)
        self.assertEqual(result["chars"], 12)

    def test_multi_line_text(self):
        result = word_count("one two\nthree four\nfive")
        self.assertEqual(result["words"], 5)
        self.assertEqual(result["lines"], 2)
        self.assertEqual(result["chars"], 23)

    def test_trailing_newline(self):
        result = word_count("hello world\n")
        self.assertEqual(result["words"], 2)
        self.assertEqual(result["lines"], 1)
        self.assertEqual(result["chars"], 12)


if __name__ == "__main__":
    unittest.main()
