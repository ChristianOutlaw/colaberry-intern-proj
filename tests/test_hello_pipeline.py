import unittest

from execution.hello_pipeline import transform_name


class TestTransformName(unittest.TestCase):

    def test_strips_whitespace(self):
        self.assertEqual(transform_name("  Alice  "), "hello_alice")

    def test_converts_to_lowercase(self):
        self.assertEqual(transform_name("INTERN"), "hello_intern")

    def test_combined_whitespace_and_case(self):
        self.assertEqual(transform_name("  Intern  "), "hello_intern")


if __name__ == "__main__":
    unittest.main()
