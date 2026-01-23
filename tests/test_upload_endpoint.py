import io
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add parent directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from user_manager import UserManager


class TestUploadEndpoint(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test"
        self.client = app.test_client()

        # Mock user manager to bypass login or auto-login
        # We'll use a patch for get_current_user
        self.user_patcher = patch("app.get_current_user")
        self.mock_get_user = self.user_patcher.start()
        self.mock_get_user.return_value = "test_user"

        self.manager_patcher = patch("app.user_manager")
        self.mock_manager = self.manager_patcher.start()
        self.mock_manager.get_user_dir.return_value = "/tmp/test_user_dir"
        os.makedirs("/tmp/test_user_dir", exist_ok=True)

        # Mock orchestrator
        self.orch_patcher = patch("app.orchestrator")
        self.mock_orch = self.orch_patcher.start()

        # Mock KnowledgeBase to simulate Ollama failure
        self.kb_patcher = patch("app.kb")
        self.mock_kb = self.kb_patcher.start()
        self.mock_kb.add_text.side_effect = Exception("Failed to connect to Ollama")

    def tearDown(self):
        self.user_patcher.stop()
        self.manager_patcher.stop()
        self.orch_patcher.stop()
        self.kb_patcher.stop()

    @patch("app.extract_resume_content")
    def test_upload_fallback_when_daytona_fails(self, mock_local_extract):
        # Setup: Daytona connected but fails
        self.mock_orch.daytona = MagicMock()
        self.mock_orch.parse_resume.side_effect = Exception("Daytona Upload Failed")

        mock_local_extract.return_value = "Extracted Text Content"

        # File to upload
        data = {"file": (io.BytesIO(b"dummy pdf content"), "resume.pdf")}

        # Mock login_required?
        # In testing, we can bypass or use session.
        # But patching get_current_user should be enough if login_required uses it or session.
        # Actually app.py uses @login_required decorator which checks session.
        with self.client.session_transaction() as sess:
            sess["user"] = "test_user"

        response = self.client.post("/api/upload_resume", data=data, content_type="multipart/form-data")

        # Verify
        self.assertEqual(response.status_code, 200)
        self.assertIn("Extracted Text Content", response.json["text"])

        # Check that Daytona was tried
        self.mock_orch.parse_resume.assert_called_once()
        # Check that fallback was used
        mock_local_extract.assert_called_once()

    @patch("app.extract_resume_content")
    def test_upload_local_when_daytona_disconnected(self, mock_local_extract):
        # Setup: Daytona disconnected
        self.mock_orch.daytona = None

        mock_local_extract.return_value = "Local Text Only"

        data = {"file": (io.BytesIO(b"dummy pdf content"), "resume.pdf")}

        with self.client.session_transaction() as sess:
            sess["user"] = "test_user"

        response = self.client.post("/api/upload_resume", data=data, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Local Text Only", response.json["text"])

        # Daytona not called
        self.mock_orch.parse_resume.assert_not_called()
        mock_local_extract.assert_called_once()


if __name__ == "__main__":
    unittest.main()
