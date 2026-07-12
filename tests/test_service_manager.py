import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from CHelper.service_manager import LLMServiceManager


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class ServiceManagerTests(unittest.TestCase):
    def test_llama_command_limits_reasoning_budget(self):
        config = _Config({
            "llm.api_url": "http://localhost:8000/v1/chat/completions",
            "llm.reasoning": "off",
            "llm.reasoning_budget": 0,
        })
        manager = LLMServiceManager(config)

        command = manager._build_llama_server_command("model.gguf", "localhost", 8000)

        self.assertEqual(command[-4:], ["--reasoning", "off", "--reasoning-budget", "0"])

    def test_openai_backend_skips_local_service_management(self):
        config = _Config({
            "llm.api_url": "https://api.openai.com/v1/chat/completions",
            "llm.backend": "openai",
            "llm.auto_start": False,
        })
        manager = LLMServiceManager(config)

        manager.start_check_async()

        self.assertTrue(manager.is_ready())
        self.assertEqual(manager.get_status(), "ready")


if __name__ == "__main__":
    unittest.main()
