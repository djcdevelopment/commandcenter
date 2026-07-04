import unittest
from tools.text.slugify import slugify

class TestSlugify(unittest.TestCase):
    
    def test_empty_string(self):
        self.assertEqual(slugify(''), '')
    
    def test_single_word(self):
        self.assertEqual(slugify('hello'), 'hello')
    
    def test_spaces_and_punctuation_collapsing_to_one_hyphen(self):
        self.assertEqual(slugify('Hello,  World!'), 'hello-world')
        self.assertEqual(slugify('  --A_B--  '), 'a-b')
    
    def test_leading_trailing_separators_stripped(self):
        self.assertEqual(slugify('--hello-world--'), 'hello-world')
        self.assertEqual(slugify('---test---'), 'test')
    
    def test_already_clean_input_unchanged(self):
        self.assertEqual(slugify('hello-world'), 'hello-world')
        self.assertEqual(slugify('test123'), 'test123')

if __name__ == '__main__':
    unittest.main()