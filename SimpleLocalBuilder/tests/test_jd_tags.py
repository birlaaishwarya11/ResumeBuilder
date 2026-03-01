"""
Tests for tag-based JD version matching.

Run with:
    pytest tests/test_jd_tags.py -v
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import patch

from jd_service import _score_version_for_jd, find_best_version_for_jd


# ---------------------------------------------------------------------------
# _score_version_for_jd
# ---------------------------------------------------------------------------

class TestScoreVersionForJD:

    def test_all_tags_match(self):
        tags = ["python", "machine learning", "aws"]
        jd = "We need a Python engineer with machine learning and AWS experience."
        assert _score_version_for_jd(tags, jd) == 1.0

    def test_partial_match(self):
        tags = ["python", "go", "rust"]
        jd = "Looking for a Python developer."
        score = _score_version_for_jd(tags, jd)
        assert abs(score - 1/3) < 1e-9

    def test_no_match(self):
        tags = ["machine learning", "tensorflow"]
        jd = "Frontend React developer wanted."
        assert _score_version_for_jd(tags, jd) == 0.0

    def test_case_insensitive(self):
        tags = ["Python", "AWS"]
        jd = "experience with python and aws is required"
        assert _score_version_for_jd(tags, jd) == 1.0

    def test_empty_tags_returns_zero(self):
        assert _score_version_for_jd([], "any JD text") == 0.0

    def test_multiword_tag_match(self):
        tags = ["product manager"]
        jd = "We are hiring a Senior Product Manager for our team."
        assert _score_version_for_jd(tags, jd) == 1.0

    def test_multiword_tag_no_partial(self):
        # "product" is in JD but "product manager" as a phrase is not
        tags = ["product manager"]
        jd = "We need someone who can manage product development."
        # "product manager" substring not present
        assert _score_version_for_jd(tags, jd) == 0.0


# ---------------------------------------------------------------------------
# find_best_version_for_jd
# ---------------------------------------------------------------------------

def _make_version(id_, label, tags):
    return {
        "id": id_,
        "label": label,
        "source": "manual_edit",
        "created_at": f"2026-01-{id_:02d}T00:00:00",
        "tags": json.dumps(tags) if tags else None,
    }


class TestFindBestVersion:

    JD_PYTHON = "We need a Python backend engineer with AWS and SQL experience."

    def test_no_versions_returns_none(self):
        with patch("jd_service.list_resume_versions", return_value=[]):
            result = find_best_version_for_jd(1, self.JD_PYTHON)
        assert result is None

    def test_no_tagged_versions_returns_none(self):
        versions = [
            _make_version(1, "general", None),
            _make_version(2, "no tags", []),
        ]
        with patch("jd_service.list_resume_versions", return_value=versions):
            result = find_best_version_for_jd(1, self.JD_PYTHON)
        assert result is None

    def test_single_matching_version(self):
        versions = [
            _make_version(1, "python backend", ["python", "aws", "sql"]),
        ]
        with patch("jd_service.list_resume_versions", return_value=versions):
            result = find_best_version_for_jd(1, self.JD_PYTHON)
        assert result is not None
        assert result["id"] == 1
        assert result["_matched_tags"] == 3

    def test_zero_score_returns_none(self):
        versions = [
            _make_version(1, "frontend", ["react", "typescript", "css"]),
        ]
        with patch("jd_service.list_resume_versions", return_value=versions):
            result = find_best_version_for_jd(1, self.JD_PYTHON)
        assert result is None

    def test_selects_best_of_multiple(self):
        versions = [
            _make_version(1, "frontend", ["react", "typescript"]),          # 0 matches
            _make_version(2, "backend lite", ["python"]),                   # 1/1 = 1.0
            _make_version(3, "backend full", ["python", "aws", "docker"]),  # 2/3 ≈ 0.67
        ]
        with patch("jd_service.list_resume_versions", return_value=versions):
            result = find_best_version_for_jd(1, self.JD_PYTHON)
        # version 2 has 1/1 = 100%, version 3 has 2/3 ≈ 67%
        assert result["id"] == 2

    def test_mixed_tagged_and_untagged(self):
        versions = [
            _make_version(1, "no tags", None),
            _make_version(2, "ml role", ["tensorflow", "nlp"]),   # 0 matches for python JD
            _make_version(3, "backend", ["python", "sql"]),       # 2/2 = 1.0
        ]
        with patch("jd_service.list_resume_versions", return_value=versions):
            result = find_best_version_for_jd(1, self.JD_PYTHON)
        assert result["id"] == 3
        assert result["_matched_tags"] == 2

    def test_matched_tags_count_correct(self):
        versions = [
            _make_version(1, "partial", ["python", "kubernetes", "gcp"]),  # only python matches
        ]
        with patch("jd_service.list_resume_versions", return_value=versions):
            result = find_best_version_for_jd(1, self.JD_PYTHON)
        assert result is not None
        assert result["_matched_tags"] == 1

    def test_tags_stored_as_json_string(self):
        """Tags stored as JSON strings (from DB) should be parsed correctly."""
        versions = [
            {
                "id": 5,
                "label": "data science",
                "source": "manual_edit",
                "created_at": "2026-01-05T00:00:00",
                "tags": '["python", "sql"]',   # JSON string as stored in DB
            }
        ]
        with patch("jd_service.list_resume_versions", return_value=versions):
            result = find_best_version_for_jd(1, self.JD_PYTHON)
        assert result is not None
        assert result["id"] == 5
