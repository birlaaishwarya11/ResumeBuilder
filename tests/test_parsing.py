import unittest

from resume_parser import parse_text, to_text


class TestResumeParser(unittest.TestCase):
    def test_parse_name(self):
        text = "# Name: John Doe"
        data = parse_text(text)
        self.assertEqual(data.get("name"), "John Doe")

    def test_parse_contact(self):
        text = """
## Contact
Email: john@example.com
Phone: 123-456-7890
"""
        data = parse_text(text)
        self.assertEqual(data["contact"]["email"], "john@example.com")
        self.assertEqual(data["contact"]["phone"], "123-456-7890")

    def test_parse_education(self):
        text = """
## Education
### University of Test
Degree: BS CS
Year: 2024
"""
        data = parse_text(text)
        self.assertIsInstance(data["education"], list)
        self.assertEqual(data["education"][0]["institution"], "University of Test")
        self.assertEqual(data["education"][0]["degree"], "BS CS")

    def test_round_trip(self):
        # Basic sanity check that data -> text -> data preserves info
        original_data = {
            "name": "Jane Doe",
            "contact": {"email": "jane@example.com"},
            "education": [{"institution": "Test Univ", "degree": "BS"}],
        }
        text = to_text(original_data)
        parsed_data = parse_text(text)

        self.assertEqual(parsed_data["name"], original_data["name"])
        self.assertEqual(parsed_data["contact"]["email"], original_data["contact"]["email"])
        self.assertEqual(parsed_data["education"][0]["institution"], original_data["education"][0]["institution"])


if __name__ == "__main__":
    unittest.main()
