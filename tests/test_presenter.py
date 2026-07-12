import sys
import types
import unittest


def _install_ida_stubs():
    for module_name in (
        "ida_hexrays",
        "ida_funcs",
        "ida_name",
        "ida_nalt",
        "ida_typeinf",
        "idaapi",
    ):
        sys.modules.setdefault(module_name, types.ModuleType(module_name))

    kernwin = sys.modules.setdefault("ida_kernwin", types.ModuleType("ida_kernwin"))
    kernwin.simplecustviewer_t = getattr(
        kernwin, "simplecustviewer_t", type("simplecustviewer_t", (), {})
    )
    kernwin.jumpto = getattr(kernwin, "jumpto", lambda _address: True)

    lines = sys.modules.setdefault("ida_lines", types.ModuleType("ida_lines"))
    lines.SCOLOR_ON = "\x01"
    lines.SCOLOR_OFF = "\x02"
    lines.SCOLOR_DEFAULT = "\x00"
    lines.SCOLOR_KEYWORD = "K"
    lines.SCOLOR_STRING = "S"
    lines.SCOLOR_CHAR = "H"
    lines.SCOLOR_DSTR = "s"
    lines.SCOLOR_DCHAR = "h"
    lines.SCOLOR_NUMBER = "N"
    lines.SCOLOR_LOCNAME = "L"
    lines.SCOLOR_DNAME = "D"
    lines.SCOLOR_CNAME = "C"
    lines.SCOLOR_IMPNAME = "I"
    lines.SCOLOR_REGCMT = "R"
    lines.SCOLOR_AUTOCMT = "A"
    lines.COLOR_DEFAULT = 123
    lines.COLSTR = lambda text, color: (
        lines.SCOLOR_ON + color + text + lines.SCOLOR_OFF + color
    )
    lines.tag_remove = lambda text: text.replace(lines.SCOLOR_ON, "").replace(
        lines.SCOLOR_OFF, ""
    )

    idc = sys.modules.setdefault("idc", types.ModuleType("idc"))
    idc.BADADDR = -1


_install_ida_stubs()

from CHelper import presenter
from CHelper.extractor import CodeExtractor


class PresenterTests(unittest.TestCase):
    def setUp(self):
        self.jumps = []
        self.names = {"sub_401000": 0x401000, "target": 0x140001460}
        presenter.idc.get_name_ea_simple = self.names.get
        presenter.idc.jumpto = lambda address: self.jumps.append(address)

        loaded = types.ModuleType("ida_bytes")
        loaded.is_loaded = lambda address: address == 0x140001460
        sys.modules["ida_bytes"] = loaded
        segments = types.ModuleType("ida_segment")
        segments.getseg = lambda address: object() if address == 0x401000 else None
        sys.modules["ida_segment"] = segments

    def _viewer(self, word, context=None):
        viewer = object.__new__(presenter.OptimizedCodeViewer)
        viewer.context = context or {}
        viewer.GetCurrentWord = lambda mouse=0: word
        return viewer

    def test_double_click_reads_mouse_word_and_jumps_to_symbol(self):
        words = []
        viewer = self._viewer("sub_401000")
        viewer.GetCurrentWord = lambda mouse=0: words.append(mouse) or "sub_401000"

        self.assertTrue(viewer.OnDblClick(0))
        self.assertEqual(words, [1])
        self.assertEqual(self.jumps, [0x401000])

    def test_word_at_mouse_column_ignores_color_tags(self):
        line = "for ( i = &g_expected; *i; ++i )"

        self.assertEqual(
            presenter._word_at_column(line, line.index("g_expected") + 3),
            "g_expected",
        )

    def test_double_click_uses_mouse_line_when_current_word_is_empty(self):
        line = "for ( i = &target; *i; ++i )"
        viewer = self._viewer("")
        viewer.GetPos = lambda mouse=0: (0, line.index("target") + 2, 0)
        viewer.GetCurrentLine = lambda mouse=0, notags=0: line

        self.assertTrue(viewer.OnDblClick(0))
        self.assertEqual(self.jumps, [0x140001460])

    def test_double_click_does_not_jump_to_small_numeric_constant(self):
        viewer = self._viewer("0x52")

        self.assertFalse(viewer.OnDblClick(0))
        self.assertEqual(self.jumps, [])

    def test_double_click_jumps_to_loaded_numeric_address(self):
        viewer = self._viewer("0x140001460")

        self.assertTrue(viewer.OnDblClick(0))
        self.assertEqual(self.jumps, [0x140001460])

    def test_unknown_local_does_not_fall_back_to_function_start(self):
        viewer = self._viewer("input_buffer", {"function_name": "main", "address": "0x401000"})

        self.assertFalse(viewer.OnDblClick(0))
        self.assertEqual(self.jumps, [])

    def test_native_semantic_color_overrides_lexical_fallback(self):
        colors = {"sub_401000": presenter.ida_lines.SCOLOR_CNAME}
        rendered = presenter._colorize_c_line(
            "int sub_401000(void) { return 0; }", colors
        )

        self.assertIn(
            presenter.ida_lines.SCOLOR_ON
            + presenter.ida_lines.SCOLOR_CNAME
            + "sub_401000",
            rendered,
        )
        self.assertIn(
            presenter.ida_lines.SCOLOR_ON
            + presenter.ida_lines.SCOLOR_KEYWORD
            + "return",
            rendered,
        )

    def test_strings_are_colored_but_numbers_keep_default_dark_color(self):
        rendered = presenter._colorize_c_line('puts("Good!"); return 5 + 0x52;')

        self.assertIn(
            presenter.ida_lines.SCOLOR_ON
            + presenter.ida_lines.SCOLOR_DSTR,
            rendered,
        )
        self.assertNotIn(
            presenter.ida_lines.SCOLOR_ON
            + presenter.ida_lines.SCOLOR_NUMBER,
            rendered,
        )

    def test_semantic_colors_read_simpleline_line_attribute(self):
        class SimpleLine:
            def __init__(self, line):
                self.line = line

        class CFunc:
            def get_pseudocode(self):
                return [
                    SimpleLine(
                        presenter.ida_lines.COLSTR(
                            "sub_401000", presenter.ida_lines.SCOLOR_CNAME
                        )
                    )
                ]

        colors = CodeExtractor.get_semantic_colors(CFunc())

        self.assertEqual(
            colors["sub_401000"], presenter.ida_lines.SCOLOR_CNAME
        )

    def test_custom_viewer_wraps_plain_text_in_ida_default_color(self):
        viewer = object.__new__(presenter.OptimizedCodeViewer)
        viewer.context = {}
        viewer._lines = []
        added = []
        viewer.AddLine = lambda *args, **kwargs: added.append((args, kwargs))

        viewer._add_line("int value;")

        rendered = added[0][0][0]
        self.assertTrue(
            rendered.startswith(
                presenter.ida_lines.SCOLOR_ON
                + presenter.ida_lines.SCOLOR_DEFAULT
            )
        )


if __name__ == "__main__":
    unittest.main()
