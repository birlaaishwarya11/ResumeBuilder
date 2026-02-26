"""
Tests for _clean_parsed_resume() and _clean_flat_list().

Run with:
    pytest tests/test_clean_parsed.py -v
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import only the two functions under test — no Flask app startup needed
from local_app import _clean_parsed_resume, _clean_flat_list


# ---------------------------------------------------------------------------
# _clean_flat_list
# ---------------------------------------------------------------------------

class TestCleanFlatList:

    def test_strips_bullet_prefix(self):
        items = ["• Google PM Cert.", "• SQL for Data Science"]
        assert _clean_flat_list(items) == [
            "Google PM Cert.",
            "SQL for Data Science",
        ]

    def test_merges_continuation_lines(self):
        items = [
            "• Led a team of 100+ people as Committee Head for",
            "Techno-Management fest AARUUSH.",
            "• Organized multiple events with student led NGO",
            "Blooming Beacon",
        ]
        result = _clean_flat_list(items)
        assert result == [
            "Led a team of 100+ people as Committee Head for Techno-Management fest AARUUSH.",
            "Organized multiple events with student led NGO Blooming Beacon",
        ]

    def test_non_bullet_list_unchanged(self):
        items = ["Attended court hearings.", "Drafted bail documents."]
        # Majority do NOT start with bullet, so list is left as-is
        assert _clean_flat_list(items) == items

    def test_mixed_dash_bullet(self):
        items = ["- Python", "- JavaScript", "- Go"]
        result = _clean_flat_list(items)
        assert result == ["Python", "JavaScript", "Go"]

    def test_empty_items_dropped(self):
        items = ["• Item one", "", "• Item two"]
        result = _clean_flat_list(items)
        assert result == ["Item one", "Item two"]

    def test_non_string_list_unchanged(self):
        items = [{"title": "foo"}, {"title": "bar"}]
        assert _clean_flat_list(items) == items

    def test_empty_list(self):
        assert _clean_flat_list([]) == []


# ---------------------------------------------------------------------------
# _clean_parsed_resume — education cleanup
# ---------------------------------------------------------------------------

class TestCleanEducation:

    def _parsed(self, edu_entries):
        return {"name": "Test", "education": edu_entries}

    def test_removes_contact_block_as_edu(self):
        parsed = self._parsed([
            {
                "institution": "TANAMAY KOTHARI",
                "degree": "",
                "end_date": None,
                "description": [
                    "| kotharitanamay02@gmail.com | (765) 476-6985 |",
                    "https://www.linkedin.com/in/tanamay-kothari/",
                ],
            },
            {
                "institution": "Purdue University",
                "degree": "Master's in Engineering Management",
                "end_date": "Dec 2023",
                "description": [],
            },
        ])
        result = _clean_parsed_resume(parsed)
        edu = result["education"]
        assert len(edu) == 1
        assert edu[0]["institution"] == "Purdue University"

    def test_keeps_real_edu_with_url_in_institution(self):
        # Should NOT be dropped — institution IS a university keyword
        parsed = self._parsed([
            {
                "institution": "MIT - Massachusetts Institute of Technology",
                "degree": "B.Sc Computer Science",
                "description": ["https://mit.edu"],
            }
        ])
        result = _clean_parsed_resume(parsed)
        assert len(result["education"]) == 1

    def test_keeps_edu_with_no_contact_info(self):
        parsed = self._parsed([
            {
                "institution": "SRM University",
                "degree": "Bachelor's in Aerospace Engineering",
                "end_date": "May 2020",
                "description": ["CAD, CAM, Aviation Management"],
            }
        ])
        result = _clean_parsed_resume(parsed)
        assert len(result["education"]) == 1

    def test_removes_empty_degree_non_university(self):
        parsed = self._parsed([
            {"institution": "JOHN DOE", "degree": "", "description": []},
            {"institution": "Harvard University", "degree": "MBA", "description": []},
        ])
        result = _clean_parsed_resume(parsed)
        assert len(result["education"]) == 1
        assert result["education"][0]["institution"] == "Harvard University"


# ---------------------------------------------------------------------------
# _clean_parsed_resume — bullet cleanup in various section shapes
# ---------------------------------------------------------------------------

class TestCleanBullets:

    def test_flat_list_section(self):
        parsed = {
            "certifications": [
                "• Google PM Cert.",
                "• SQL for Data Science",
                "Continuation line",
            ]
        }
        # Continuation: "Continuation line" has no bullet but follows a bullet list
        result = _clean_parsed_resume(parsed)
        certs = result["certifications"]
        # Last item should be merged onto previous
        assert "SQL for Data Science Continuation line" in certs[-1] or \
               "SQL for Data Science" in certs[-1]

    def test_dict_with_bullets_key(self):
        """certifications_and_leadership_experience: {bullets: [...]}"""
        parsed = {
            "certifications_and_leadership_experience": {
                "bullets": [
                    "•  Google PM Cert.",
                    "•  Led a team of 100+ for",
                    "Techno-Management fest AARUUSH.",
                ]
            }
        }
        result = _clean_parsed_resume(parsed)
        bullets = result["certifications_and_leadership_experience"]["bullets"]
        assert bullets[0] == "Google PM Cert."
        assert "Techno-Management fest AARUUSH." in bullets[1]
        assert len(bullets) == 2

    def test_list_of_dicts_sub_bullets(self):
        parsed = {
            "experience": [
                {
                    "company": "ACME",
                    "bullets": [
                        "• Did the thing",
                        "• Also this",
                        "continuation here",
                    ],
                }
            ]
        }
        result = _clean_parsed_resume(parsed)
        bullets = result["experience"][0]["bullets"]
        assert bullets[0] == "Did the thing"
        assert "Also this continuation here" in bullets[1]

    def test_list_of_dicts_description_sub_list(self):
        parsed = {
            "experience": [
                {
                    "organization": "Law Firm",
                    "description": [
                        "• Drafted legal documents",
                        "• Attended hearings",
                    ],
                }
            ]
        }
        result = _clean_parsed_resume(parsed)
        desc = result["experience"][0]["description"]
        assert desc == ["Drafted legal documents", "Attended hearings"]

    def test_non_bullet_list_not_merged(self):
        """A plain list without bullet markers should not have items merged."""
        parsed = {
            "workshops": [
                "Attended workshop on Trademark Filing",
                "Attended lecture by SHREE RAJEEV PANDE",
            ]
        }
        result = _clean_parsed_resume(parsed)
        assert len(result["workshops"]) == 2


# ---------------------------------------------------------------------------
# End-to-end: law student resume shape
# ---------------------------------------------------------------------------

class TestLawStudentResume:

    def test_full_law_resume(self):
        parsed = {
            "name": "DIYA ABHAY KOTHARI",
            "contact": {"email": "diya@example.com", "phone": "+91 99212 61312"},
            "education": [
                {
                    "degree": "IV Semester",
                    "institution": "National Law University, Nagpur",
                    "dates": "2024-29",
                    "score": "7.5/10",
                }
            ],
            "experience": [
                {
                    "organization": "ADV. JAYMALA, HIGH COURT, MUMBAI",
                    "dates": "2025",
                    "description": [
                        "• Gained exposure to Civil Court procedures.",
                        "• Observed hearings and arbitrations.",
                    ],
                }
            ],
            "co_curricular_activities": [
                {"title": "Moot Courts", "bullets": ["• Speaker, 3rd NFSU Competition"]},
            ],
            "certifications_and_leadership_experience": {
                "bullets": [
                    "•  Google PM Cert.",
                    "•  Led a team of 100+ for",
                    "Techno-Management fest AARUUSH.",
                ]
            },
        }
        result = _clean_parsed_resume(parsed)

        # Education untouched (has real institution)
        assert len(result["education"]) == 1

        # Experience description cleaned
        desc = result["experience"][0]["description"]
        assert desc[0] == "Gained exposure to Civil Court procedures."

        # Co-curricular bullets cleaned
        bullets = result["co_curricular_activities"][0]["bullets"]
        assert bullets[0] == "Speaker, 3rd NFSU Competition"

        # Certifications dict bullets cleaned + continuation merged
        cert_bullets = result["certifications_and_leadership_experience"]["bullets"]
        assert cert_bullets[0] == "Google PM Cert."
        assert "Techno-Management fest AARUUSH." in cert_bullets[1]
        assert len(cert_bullets) == 2


# ---------------------------------------------------------------------------
# End-to-end: engineering resume shape
# ---------------------------------------------------------------------------

class TestEngineeringResume:

    def test_contact_block_removed_from_education(self):
        parsed = {
            "name": "TANAMAY KOTHARI",
            "education": [
                {
                    "institution": "TANAMAY KOTHARI",
                    "degree": "",
                    "end_date": None,
                    "description": [
                        "| kotharitanamay02@gmail.com | (765) 476-6985 |",
                        "https://www.linkedin.com/in/tanamay-kothari/",
                    ],
                },
                {
                    "institution": "Purdue University",
                    "degree": "Master's in Engineering Management",
                    "end_date": "Dec 2023",
                    "description": [
                        "Operations and Supply Chain Management",
                        "Project Management",
                    ],
                },
                {
                    "institution": "SRM University",
                    "degree": "Bachelor's in Aerospace Engineering",
                    "end_date": "May 2020",
                    "description": ["CAD, CAM, Aviation Management"],
                },
            ],
        }
        result = _clean_parsed_resume(parsed)
        edu = result["education"]
        assert len(edu) == 2
        assert edu[0]["institution"] == "Purdue University"
        assert edu[1]["institution"] == "SRM University"
