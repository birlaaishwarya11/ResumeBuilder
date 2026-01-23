import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app import app


class TestAppIntegration(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        app.config["SECRET_KEY"] = "test-key"

        # Create a temp dir for sessions if needed, though flask-session might use default
        self.test_dir = tempfile.mkdtemp()
        app.config["SESSION_FILE_DIR"] = self.test_dir

        self.client = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()
        shutil.rmtree(self.test_dir)

    @patch("app.orchestrator")
    @patch("app.kb")
    @patch("app.doc_server")
    @patch("app.ats_server")
    def test_health_check(self, mock_ats, mock_doc, mock_kb, mock_orchestrator):
        # Setup mocks
        mock_kb.health_check.return_value = True
        mock_orchestrator.daytona = True  # Simulate connection
        # Mock MCP servers as existing objects
        mock_doc.__bool__ = MagicMock(return_value=True)
        mock_ats.__bool__ = MagicMock(return_value=True)

        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["services"]["knowledge_base"], "healthy")
        self.assertEqual(data["services"]["daytona"], "connected")

    @patch("app.user_manager")
    @patch("app.AIATSAnalyzer")
    @patch("app.rag_agent")
    def test_analyze_ats(self, mock_rag, mock_analyzer_cls, mock_user_manager):
        # 1. Login
        with self.client.session_transaction() as sess:
            sess["user"] = "testuser"

        user_dir = os.path.join(self.test_dir, "testuser")
        os.makedirs(user_dir, exist_ok=True)
        mock_user_manager.get_user_dir.return_value = user_dir

        # Create dummy resume.yaml
        with open(os.path.join(user_dir, "resume.yaml"), "w") as f:
            f.write("name: Test User\nskills: [Java]")

        # 2. Mock Logic
        mock_instance = mock_analyzer_cls.return_value
        mock_instance.analyze.return_value = {"match_score": 75, "missing_keywords": ["Python", "Docker"]}

        # Mock RAG response
        mock_rag.find_lost_bullets.return_value = [
            {"content": "Used Python for backend", "source": "Project A"},
            {"content": "Deployed with Docker", "source": "Project B"},
        ]

        # 3. Call API
        payload = {
            "jd_text": "Need Python and Docker skills",
            "model": "ollama/llama3",
            "resume_source": "current",
            "enable_rag": True,
        }

        response = self.client.post("/api/analyze", data=json.dumps(payload), content_type="application/json")

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.data)

        # analyze endpoint returns the result dict directly, without wrapper
        self.assertEqual(data["match_score"], 75)
        self.assertIn("rag_suggestions", data)
        self.assertEqual(len(data["rag_suggestions"]), 2)

    def test_index_landing(self):
        # Without login, should show landing page
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        # Check for landing page content
        self.assertIn(b"ResumeBuilder", response.data)
        self.assertIn(b"Craft the Perfect Resume", response.data)

    @patch("app.user_manager")
    def test_login_flow(self, mock_user_manager):
        # Setup mock user manager
        mock_user_manager.verify_user.return_value = True

        # Setup a real directory for get_user_dir so os.listdir works
        user_dir = os.path.join(self.test_dir, "testuser")
        os.makedirs(user_dir, exist_ok=True)
        mock_user_manager.get_user_dir.return_value = user_dir

        # Login
        response = self.client.post(
            "/login", data={"username": "testuser", "password": "password123"}, follow_redirects=True
        )

        self.assertEqual(response.status_code, 200)
        # Should redirect to dashboard, check for dashboard content
        self.assertIn(b"Editor", response.data)


if __name__ == "__main__":
    unittest.main()
