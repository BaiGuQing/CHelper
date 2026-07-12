import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from CHelper.config_validator import ConfigValidator


def _config(backend="openai", auto_start=False):
    return {
        "llm": {
            "api_url": "https://api.openai.com/v1/chat/completions",
            "model": "gpt-4o-mini",
            "backend": backend,
            "auto_start": auto_start,
        },
        "plugin": {},
        "optimization": {},
    }


class ConfigValidatorTests(unittest.TestCase):
    def test_openai_backend_is_valid_without_local_autostart(self):
        valid, errors = ConfigValidator.validate(_config())

        self.assertTrue(valid, errors)

    def test_openai_backend_rejects_local_autostart(self):
        valid, errors = ConfigValidator.validate(_config(auto_start=True))

        self.assertFalse(valid)
        self.assertIn("llm.auto_start", " ".join(errors))


if __name__ == "__main__":
    unittest.main()
