import unittest
from tools.text.slugify import slugify

class TestSlugify(unittest.TestCase):
    def test_empty_string(self):
        self.assertEqual(slugify(""), "")
    
    def test_already_slug_text(self):
        self.assertEqual(slugify("hello-world"), "hello-world")
        
    def test_mixed_case_with_punctuation(self):
        self.assertEqual(slugify("Hello, World!"), "hello-world")
        
    def test_multiple_consecutive_spaces(self):
        self.assertEqual(slugify("hello   world"), "hello-world")
        
    def test_leading_trailing_whitespace(self):
        self.assertEqual(slugify("  hello world  "), "hello-world")

if __name__ == '__main__':
    unittest.main()