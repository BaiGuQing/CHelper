import sys
import types
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def _install_ida_import_stubs():
    """Import only enough IDA surface for handler's pure-Python helper tests."""
    kernwin = types.ModuleType("ida_kernwin")
    kernwin.action_handler_t = type("action_handler_t", (), {})
    kernwin.simplecustviewer_t = type("simplecustviewer_t", (), {})
    sys.modules.setdefault("ida_kernwin", kernwin)
    sys.modules.setdefault("ida_hexrays", types.ModuleType("ida_hexrays"))
    sys.modules.setdefault("ida_funcs", types.ModuleType("ida_funcs"))
    sys.modules.setdefault("ida_name", types.ModuleType("ida_name"))
    sys.modules.setdefault("ida_nalt", types.ModuleType("ida_nalt"))
    sys.modules.setdefault("ida_typeinf", types.ModuleType("ida_typeinf"))

    idaapi = types.ModuleType("idaapi")
    idaapi.BADADDR = -1
    sys.modules.setdefault("idaapi", idaapi)

    lines = types.ModuleType("ida_lines")
    lines.SCOLOR_REGCMT = 0
    lines.SCOLOR_KEYWORD = 0
    lines.SCOLOR_STRING = 0
    lines.SCOLOR_NUMBER = 0
    lines.SCOLOR_LOCNAME = 0
    lines.COLSTR = lambda text, _color: text
    lines.tag_remove = lambda text: text
    sys.modules.setdefault("ida_lines", lines)

    idc = types.ModuleType("idc")
    idc.BADADDR = -1
    sys.modules.setdefault("idc", idc)


_install_ida_import_stubs()

from CHelper.handler import OptimizationHandler


SOURCE = '''int main(void)
{
  _BYTE *i;

  _mingw_printf("input: ");
  for ( i = &g_expected; *i; ++i )
    puts_0("ok");
  return 0;
}
'''


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Logger:
    def __init__(self):
        self.messages = []

    def info(self, message):
        self.messages.append(message)

    def warning(self, message):
        self.messages.append(message)


class _LLM:
    def __init__(self, repaired_code):
        self.repaired_code = repaired_code
        self.calls = []

    def repair_code(self, pseudocode, context, violation, required_anchors):
        self.calls.append((pseudocode, context, violation, required_anchors))
        return self.repaired_code

    def get_last_error(self):
        return "mock model failure"


class HandlerQualityRepairTests(unittest.TestCase):
    def _handler(self, repaired_code, attempts=1, conservative_mode="on"):
        handler = object.__new__(OptimizationHandler)
        handler.config = _Config({
            "llm.quality_repair_attempts": attempts,
            "llm.local_readability_fallback": True,
            "llm.conservative_mode": conservative_mode,
        })
        handler.logger = _Logger()
        handler.llm_client = _LLM(repaired_code)
        return handler

    def test_quality_rejection_gets_one_repair_and_revalidates(self):
        bad_candidate = SOURCE.replace("g_expected", "wrong_global")
        handler = self._handler(SOURCE)

        processed, success, error = handler._process_model_candidate(
            SOURCE, {"function_name": "main"}, bad_candidate
        )

        self.assertTrue(success, error)
        self.assertIn("expected_cursor", processed)
        self.assertEqual(error["result_kind"], "local_readability_fallback")
        self.assertEqual(len(handler.llm_client.calls), 1)
        _source, _context, violation, anchors = handler.llm_client.calls[0]
        self.assertIn("g_expected", violation)
        self.assertIn("IDA 全局符号: g_expected", anchors)

    def test_full_mode_accepts_structural_rewrite(self):
        full_rewrite = '''int main(void)
{
  _BYTE *cursor = &g_expected;
  while ( *cursor )
  {
    puts_0("ok");
    ++cursor;
  }
  return 0;
}
'''
        handler = self._handler(SOURCE, conservative_mode="off")

        processed, success, metadata = handler._process_model_candidate(
            SOURCE, {"function_name": "main"}, full_rewrite
        )

        self.assertTrue(success, metadata)
        self.assertEqual(metadata["result_kind"], "model_full_rewrite")
        self.assertIn("while", processed)

    def test_non_quality_fragment_does_not_trigger_repair(self):
        handler = self._handler(SOURCE)

        processed, success, error = handler._process_model_candidate(
            SOURCE, {}, "return 0;"
        )

        self.assertTrue(success, error)
        self.assertIn("expected_cursor", processed)
        self.assertEqual(error["result_kind"], "local_readability_fallback")
        self.assertEqual(handler.llm_client.calls, [])

    def test_second_quality_failure_is_not_retried_indefinitely(self):
        bad_candidate = SOURCE.replace("g_expected", "wrong_global")
        handler = self._handler(bad_candidate)

        processed, success, error = handler._process_model_candidate(
            SOURCE, {}, bad_candidate
        )

        self.assertTrue(success, error)
        self.assertIn("expected_cursor", processed)
        self.assertEqual(error["result_kind"], "local_readability_fallback")
        self.assertEqual(len(handler.llm_client.calls), 1)

    def test_model_noop_uses_local_readability_fallback(self):
        handler = self._handler(SOURCE)

        processed, success, metadata = handler._process_model_candidate(
            SOURCE, {}, SOURCE
        )

        self.assertTrue(success, metadata)
        self.assertIn("expected_cursor", processed)
        self.assertEqual(metadata["result_kind"], "local_readability_fallback")

    def test_direct_local_fallback_preserves_semantic_anchors(self):
        handler = self._handler(SOURCE)

        processed, success, metadata = handler._fallback_to_local_readability(
            SOURCE, "模型调用失败"
        )

        self.assertTrue(success, metadata)
        self.assertIn("expected_cursor = &g_expected", processed)
        self.assertIn('_mingw_printf("input: ")', processed)
        self.assertEqual(metadata["renamed_locals"], 1)


if __name__ == "__main__":
    unittest.main()
