import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rag_agent import AgentState, RagAgent


class TestRagSanity(unittest.TestCase):
    def setUp(self):
        self.agent = RagAgent()
        # Mock KnowledgeBase
        self.agent.kb = MagicMock()

    @patch("rag_agent.completion")
    def test_find_lost_bullets_sanity(self, mock_completion):
        """Test that RAG agent returns grounded suggestions from context."""

        # 1. Setup Mock Context
        mock_doc = MagicMock()
        mock_doc.page_content = "Worked at TechCorp on Project Apollo. Used Python and AWS."
        self.agent.kb.search.return_value = [mock_doc]

        # 2. Setup Mock LLM Response
        expected_suggestions = ["Lead developer for Project Apollo at TechCorp", " utilized Python for AWS integration"]
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(expected_suggestions)
        mock_completion.return_value = mock_response

        # 3. Run Agent
        query = "Python experience"
        result_json = self.agent.find_lost_bullets(query)
        result = json.loads(result_json)

        # 4. Assertions
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIn("Project Apollo", result[0])

        # Verify context was retrieved
        self.agent.kb.search.assert_called_with(query)

        # Verify LLM was called with context
        call_args = mock_completion.call_args
        prompt = call_args[1]["messages"][0]["content"]
        self.assertIn("Worked at TechCorp", prompt)
        self.assertIn("Project Apollo", prompt)

    @patch("rag_agent.completion")
    def test_find_lost_bullets_invalid_json(self, mock_completion):
        """Test graceful handling of invalid JSON from LLM."""

        self.agent.kb.search.return_value = []

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Not a JSON string"
        mock_completion.return_value = mock_response

        # Should handle the error gracefully (RagAgent usually returns the raw string if not JSON, or catches exception)
        # Looking at code: it returns the content.strip() if it fails to split by markdown code blocks
        # But find_lost_bullets expects JSON string.
        # The app.py tries to parse it.

        result = self.agent.find_lost_bullets("query")
        self.assertEqual(result, "Not a JSON string")

    @patch("rag_agent.completion")
    def test_interview_coach_sanity(self, mock_completion):
        """Test interview coach logic."""

        mock_doc = MagicMock()
        mock_doc.page_content = "STAR Method: Situation - Server crash. Action - Restarted. Result - Uptime."
        self.agent.kb.search.return_value = [mock_doc]

        mock_response = MagicMock()
        mock_response.choices[0].message.content = "Here is a sample answer based on the server crash..."
        mock_completion.return_value = mock_response

        answer = self.agent.prep_for_interview("Tell me about a time you fixed a bug")

        self.assertIn("sample answer", answer)
        self.agent.kb.search.assert_called()


if __name__ == "__main__":
    unittest.main()
