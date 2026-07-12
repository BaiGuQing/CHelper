import sys
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from CHelper.llm_client import LLMClient


SOURCE = '''int demo(void)
{
  puts("ok");
  return 0;
}
'''


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Response:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self.data


class LLMClientTests(unittest.TestCase):
    def _client(self, **overrides):
        values = {
            "llm.api_url": "http://unit.test/v1/chat/completions",
            "llm.model": "unit-model",
            "llm.max_retry_attempts": 1,
            "llm.seed": 123,
        }
        values.update(overrides)
        return LLMClient(_Config(values))

    def test_length_finish_reason_is_rejected_before_post_processing(self):
        client = self._client()
        response = {
            "choices": [{
                "finish_reason": "length",
                "message": {"content": "int demo(void) {"},
            }],
            "model": "unit-model",
            "usage": {"completion_tokens": 42},
        }

        with patch("CHelper.llm_client.requests.post", return_value=_Response(response)):
            result = client.optimize_code(SOURCE)

        self.assertIsNone(result)
        self.assertIn("max_tokens", client.get_last_error())
        self.assertEqual(client.last_response_info["finish_reason"], "length")

    def test_request_uses_system_message_and_optional_seed(self):
        client = self._client()
        response = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": SOURCE},
            }],
            "model": "unit-model",
            "usage": {"completion_tokens": 12},
        }

        with patch("CHelper.llm_client.requests.post", return_value=_Response(response)) as post:
            result = client.optimize_code(SOURCE, {"function_name": "demo"})

        self.assertEqual(result, SOURCE.strip())
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["seed"], 123)
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][1]["role"], "user")

    def test_base_v1_url_is_normalized_to_chat_completions(self):
        client = self._client(**{"llm.api_url": "https://api.example.test/v1"})
        response = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": SOURCE},
            }],
        }

        with patch("CHelper.llm_client.requests.post", return_value=_Response(response)) as post:
            self.assertEqual(client.optimize_code(SOURCE), SOURCE.strip())

        self.assertEqual(
            post.call_args.args[0],
            "https://api.example.test/v1/chat/completions",
        )

    def test_prompt_treats_pseudocode_signature_as_authoritative(self):
        client = self._client()
        prompt = client._build_prompt(
            SOURCE,
            {"function_type": "int __fastcall(int wrong_type)"},
        )

        self.assertIn("不可变函数签名", prompt)
        self.assertIn("int demo(void)", prompt)
        self.assertNotIn("int wrong_type", prompt)

    def test_conservative_prompt_includes_verified_local_rename_map(self):
        source = '''int demo(void)
{
  int status;
  char input[8];
  status = scanf("%7s", input);
  return status;
}
'''
        client = self._client(**{"llm.conservative_mode": "on"})

        prompt = client._build_prompt(source, {"function_name": "demo"})

        self.assertIn("必须在有明确用途时", prompt)
        self.assertIn("已验证的局部变量映射", prompt)
        self.assertIn("input -> input_buffer", prompt)
        self.assertIn("status -> scan_result", prompt)

    def test_full_prompt_allows_structural_readability_rewrite(self):
        client = self._client(**{"llm.conservative_mode": "off"})

        prompt = client._build_prompt(SOURCE, {"function_name": "demo"})

        self.assertIn("全量可读性重写", prompt)
        self.assertIn("重写表达式、循环、分支和局部变量", prompt)
        self.assertIn("不要将指针循环改写成索引循环", prompt)
        self.assertNotIn("只可做可验证的局部变量命名", prompt)

    def test_full_prompt_can_enable_control_flow_rewrite(self):
        client = self._client(**{
            "llm.conservative_mode": "off",
            "optimization": {"rewrite_control_flow": True},
        })

        prompt = client._build_prompt(SOURCE, {"function_name": "demo"})

        self.assertIn("主动重写循环和分支", prompt)
        self.assertIn("可将指针循环改成索引循环", prompt)

    def test_quality_repair_uses_original_source_and_temperature_zero(self):
        client = self._client(**{
            "llm.temperature": 0.7,
            "llm.conservative_mode": "on",
        })
        response = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": SOURCE},
            }],
            "model": "unit-model",
            "usage": {"completion_tokens": 12},
        }

        with patch("CHelper.llm_client.requests.post", return_value=_Response(response)) as post:
            result = client.repair_code(
                SOURCE,
                {"function_name": "demo"},
                "IDA 全局符号丢失: g_expected",
                "外部调用: puts\nIDA 全局符号: g_expected",
            )

        self.assertEqual(result, SOURCE.strip())
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["temperature"], 0.0)
        prompt = payload["messages"][1]["content"]
        self.assertIn("IDA 全局符号丢失: g_expected", prompt)
        self.assertIn("外部调用: puts", prompt)
        self.assertIn("不要用注释、字符串或新变量名代替原始 IDA 全局符号", prompt)
        self.assertIn(SOURCE, prompt)
        self.assertEqual(client.last_response_info["request_kind"], "quality_repair")

    def test_connection_uses_models_endpoint_without_generating_tokens(self):
        client = self._client()

        with patch("CHelper.llm_client.requests.get") as get:
            get.return_value.status_code = 200
            self.assertTrue(client.test_connection())

        self.assertEqual(get.call_args.args[0], "http://unit.test/v1/models")


if __name__ == "__main__":
    unittest.main()
