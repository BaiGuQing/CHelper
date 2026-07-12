import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from CHelper.processor import CodeProcessor


SOURCE = '''int __fastcall main(int argc, const char **argv, const char **envp)
{
  int v3;
  char *v4;
  _BYTE *i;
  char Str[30];
  char v8;

  _main();
  _mingw_printf("input your flag: ");
  v3 = _mingw_scanf("%511s", Str);
  if ( v3 == 1 )
  {
    if ( strlen(Str) == 30 )
    {
      v4 = Str;
      for ( i = &g_expected; *i == ((unsigned __int8)*v4 ^ 0x52) + 5; ++i )
      {
        if ( ++v4 == &v8 )
        {
          puts_0("Good!");
          return 0;
        }
      }
    }
    puts_0("Wrong!");
  }
  else
  {
    puts_0("Wrong!");
    return 1;
  }
  return v3;
}
'''


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class ProcessorTests(unittest.TestCase):
    def test_complete_source_passes_the_quality_guard(self):
        processed, success, message = CodeProcessor.process(SOURCE, SOURCE, _Config())

        self.assertTrue(success, message)
        self.assertEqual(processed, SOURCE.strip())

    def test_full_mode_can_remove_redundant_source_anchors(self):
        rewritten = SOURCE.replace('puts_0("Wrong!");', '')
        config = _Config({"llm.conservative_mode": "off"})

        processed, success, message = CodeProcessor.process(
            rewritten, SOURCE, config
        )

        self.assertTrue(success, message)
        self.assertNotIn('puts_0("Wrong!");', processed)

    def test_invalid_fragment_is_rejected_instead_of_returning_original(self):
        processed, success, message = CodeProcessor.process("return 0;", SOURCE, _Config())

        self.assertFalse(success)
        self.assertEqual(processed, "")
        self.assertIn("完整函数", message)

    def test_semantically_wrong_but_balanced_function_is_rejected(self):
        hallucinated = '''int __fastcall main(int argc, const char **argv, const char **envp)
{
  return 0;
}
'''

        processed, success, message = CodeProcessor.process(hallucinated, SOURCE, _Config())

        self.assertFalse(success)
        self.assertEqual(processed, "")
        self.assertIn("安全校验", message)
        self.assertIn("关键调用", message)

    def test_global_anchor_in_comment_does_not_satisfy_quality_guard(self):
        hallucinated = SOURCE.replace("&g_expected", "&wrong_global")
        hallucinated = hallucinated.replace(
            "{\n",
            "{\n  // g_expected must not satisfy the global-symbol check\n",
            1,
        )

        processed, success, message = CodeProcessor.process(
            hallucinated, SOURCE, _Config()
        )

        self.assertFalse(success)
        self.assertEqual(processed, "")
        self.assertIn("IDA 全局符号丢失: g_expected", message)

    def test_quality_rejection_has_a_stable_reason_for_one_shot_repair(self):
        hallucinated = '''int __fastcall main(int argc, const char **argv, const char **envp)
{
  return 0;
}
'''

        _processed, success, message = CodeProcessor.process(
            hallucinated, SOURCE, _Config()
        )

        self.assertFalse(success)
        self.assertTrue(CodeProcessor.is_quality_rejection(message))
        reason = CodeProcessor.get_quality_rejection_reason(message)
        self.assertIn("关键调用", reason)
        self.assertNotIn("结果未缓存", reason)
        self.assertFalse(CodeProcessor.is_quality_rejection("代码处理失败: 网络错误"))

    def test_required_semantic_anchor_summary_lists_globals_and_calls(self):
        anchors = CodeProcessor.get_required_semantic_anchors(SOURCE)

        self.assertIn("函数签名:", anchors)
        self.assertIn("外部调用: _main, _mingw_printf, _mingw_scanf, puts_0, strlen", anchors)
        self.assertIn("IDA 全局符号: g_expected", anchors)

    def test_safe_local_readability_renames_only_proven_locals(self):
        improved, metadata = CodeProcessor.apply_safe_local_readability(SOURCE)

        self.assertEqual(metadata["renamed_locals"], 5)
        self.assertIn("int scan_result;", improved)
        self.assertIn("char *input_cursor;", improved)
        self.assertIn("_BYTE *expected_cursor;", improved)
        self.assertIn("char input_buffer[30];", improved)
        self.assertIn("char input_end_marker;", improved)
        self.assertIn("&g_expected", improved)
        self.assertIn('_mingw_printf("input your flag: ")', improved)
        self.assertNotIn("scan_result", SOURCE)
        self.assertTrue(CodeProcessor.has_meaningful_change(SOURCE, improved))
        self.assertEqual(CodeProcessor.basic_syntax_check(improved), (True, ""))
        self.assertEqual(
            CodeProcessor.validate_semantic_preservation(SOURCE, improved),
            (True, ""),
        )
        valid, reason, mapping = CodeProcessor.validate_local_rename_only(
            SOURCE, improved
        )
        self.assertTrue(valid, reason)
        self.assertEqual(mapping, metadata["rename_mapping"])

    def test_local_readability_does_not_replace_comments_or_strings(self):
        source = SOURCE.replace(
            'int v3;',
            'int v3; // v3 must stay in this comment',
        ).replace(
            '"input your flag: "',
            '"v3 is a string literal"',
        )

        improved, _metadata = CodeProcessor.apply_safe_local_readability(source)

        self.assertIn("// v3 must stay in this comment", improved)
        self.assertIn('"v3 is a string literal"', improved)

    def test_local_rename_validation_rejects_inconsistent_references(self):
        candidate = SOURCE.replace("v3", "scan_result", 1)

        valid, reason, _mapping = CodeProcessor.validate_local_rename_only(
            SOURCE, candidate
        )

        self.assertFalse(valid)
        self.assertIn("不一致", reason)

    def test_trailing_explanation_is_not_shown_as_c_code(self):
        response = SOURCE + "\n优化说明：这里不应出现在代码窗口。"

        cleaned = CodeProcessor.clean_llm_artifacts(response)
        processed, success, message = CodeProcessor.process(response, SOURCE, _Config())

        self.assertEqual(cleaned, SOURCE.strip())
        self.assertTrue(success, message)
        self.assertEqual(processed, SOURCE.strip())

    def test_braces_inside_a_string_do_not_make_a_function_incomplete(self):
        code = '''int demo(void)
{
  puts("{");
  return 0;
}
'''

        self.assertFalse(CodeProcessor.is_incomplete_fragment(code, code))
        processed, success, message = CodeProcessor.process(code, code, _Config())
        self.assertTrue(success, message)
        self.assertEqual(processed, code.strip())

    def test_pointer_return_signature_is_recognized(self):
        code = '''char *__fastcall get_name(int index)
{
  return names[index];
}
'''

        self.assertEqual(
            CodeProcessor.extract_complete_function(code),
            code.strip(),
        )

    def test_signature_changes_are_rejected(self):
        renamed_signature = SOURCE.replace(
            "main(int argc, const char **argv, const char **envp)",
            "mymain(int argc, const char **argv, const char **envp)",
        )

        accepted, reason = CodeProcessor.validate_semantic_preservation(SOURCE, renamed_signature)

        self.assertFalse(accepted)
        self.assertIn("函数签名", reason)

    def test_unused_parameter_signature_is_safely_restored(self):
        candidate = SOURCE.replace(
            "int __fastcall main(int argc, const char **argv, const char **envp)",
            "int main(int argc, const char *argv, const char *envp)",
        )

        restored, did_restore = CodeProcessor.restore_unused_parameter_signature(
            SOURCE, candidate
        )
        processed, success, message = CodeProcessor.process(candidate, SOURCE, _Config())

        self.assertTrue(did_restore)
        self.assertTrue(restored.startswith("int __fastcall main("))
        self.assertTrue(success, message)
        self.assertEqual(processed, SOURCE.strip())

    def test_signature_is_not_restored_when_a_parameter_is_used(self):
        original = '''int __fastcall demo(int value)
{
  return 0;
}
'''
        candidate = '''int demo(char value)
{
  return value;
}
'''

        restored, did_restore = CodeProcessor.restore_unused_parameter_signature(
            original, candidate
        )

        self.assertFalse(did_restore)
        self.assertEqual(restored, candidate)


if __name__ == "__main__":
    unittest.main()
