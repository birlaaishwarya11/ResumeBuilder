import unittest

from ats_analyzer import analyze_keywords


class TestATSAnalyzer(unittest.TestCase):
    def test_exact_match(self):
        resume = "Python Java SQL"
        jd = "Python Java SQL"
        result = analyze_keywords(resume, jd)
        self.assertEqual(result["match_score"], 100.0)
        self.assertEqual(result["matched_count"], 3)
        self.assertEqual(len(result["missing_keywords"]), 0)

    def test_partial_match(self):
        resume = "Python"
        jd = "Python Java"
        result = analyze_keywords(resume, jd)
        self.assertEqual(result["match_score"], 50.0)
        self.assertEqual(result["matched_count"], 1)
        self.assertIn("java", result["missing_keywords"])

    def test_case_insensitivity(self):
        resume = "python"
        jd = "PYTHON"
        result = analyze_keywords(resume, jd)
        self.assertEqual(result["match_score"], 100.0)

    def test_stopwords_removal(self):
        # "and", "the" are stopwords
        resume = "Python and Java"
        jd = "Python Java"
        result = analyze_keywords(resume, jd)
        self.assertEqual(result["match_score"], 100.0)


if __name__ == "__main__":
    unittest.main()
