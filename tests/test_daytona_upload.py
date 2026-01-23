import base64
import os
import sys
import unittest
from unittest.mock import MagicMock

# Add parent directory to path to import daytona_orchestrator
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from daytona_orchestrator import DaytonaOrchestrator


class MockProcess:
    def __init__(self):
        self.commands = []

    def exec(self, cmd):
        self.commands.append(cmd)
        result = MagicMock()
        result.exit_code = 0
        result.result = ""
        return result


class MockSandbox:
    def __init__(self):
        self.process = MockProcess()
        self.id = "mock_sandbox_id"


class TestDaytonaUpload(unittest.TestCase):
    def test_upload_file_chunking(self):
        orchestrator = DaytonaOrchestrator()
        # Mock daytona to avoid init errors if any
        orchestrator.daytona = MagicMock()

        sandbox = MockSandbox()

        # Create a large content > 10KB (chunk size)
        content = "A" * 25000  # 25KB
        path = "subdir/test_file.txt"

        orchestrator.upload_file(sandbox, path, content)

        cmds = sandbox.process.commands

        # Verify commands
        # 1. mkdir
        self.assertTrue(any("os.makedirs" in cmd for cmd in cmds))

        # 2. rm temp file
        self.assertTrue(any(f"rm -f {path}.b64" in cmd for cmd in cmds))

        # 3. Chunks
        # 25000 bytes -> base64 is approx 33336 bytes.
        # Chunk size 10000. So 4 chunks (0-10000, 10000-20000, 20000-30000, 30000-33336)
        # We expect at least 4 exec calls with "write" and "b64" logic

        # Let's count how many times we call the chunk appending script
        append_calls = 0
        for cmd in cmds:
            decoded = self._decode_cmd(cmd)
            if decoded and f"open('{path}.b64', 'a')" in decoded:
                append_calls += 1

        self.assertEqual(append_calls, 4)

        # 4. Decode
        decode_calls = 0
        for cmd in cmds:
            decoded = self._decode_cmd(cmd)
            if f"open('{path}', 'wb')" in decoded:
                decode_calls += 1
        self.assertEqual(decode_calls, 1)

        # 5. Cleanup
        self.assertTrue(any(f"rm {path}.b64" in cmd for cmd in cmds))

    def test_cleanup_worker(self):
        orchestrator = DaytonaOrchestrator()
        orchestrator.daytona = MagicMock()
        sandbox = MockSandbox()

        orchestrator.cleanup_worker(sandbox)

        # Verify delete is called with sandbox object
        orchestrator.daytona.delete.assert_called_with(sandbox)

    def _decode_cmd(self, cmd):
        try:
            # cmd is: python -c "import base64; exec(base64.b64decode('...').decode('utf-8'))"
            # Extract the base64 string
            if "base64.b64decode('" in cmd:
                start = cmd.find("base64.b64decode('") + len("base64.b64decode('")
                end = cmd.find("').decode('utf-8'))")
                b64_str = cmd[start:end]
                return base64.b64decode(b64_str).decode("utf-8")
        except:
            pass
        return cmd


if __name__ == "__main__":
    unittest.main()
