# -*- coding: utf-8 -*-
"""
代码后处理器 - 验证和清理优化后的代码
"""

import re
from .constants import (
    DEGENERATION_THRESHOLD,
    DEFAULT_MINIMUM_OUTPUT_RATIO,
    FUNCTION_SIGNATURE_PATTERN,
)
from .logger import get_logger


class CodeProcessor:
    """处理和验证优化后的代码"""

    # Keep this prefix stable: the handler uses it to distinguish a real
    # semantic-preservation rejection (which can be retried with a stricter
    # prompt) from transport, syntax, or truncation failures.
    QUALITY_REJECTION_PREFIX = "模型输出未通过安全校验:"

    # A code model can produce a syntactically valid function while silently
    # dropping the actual check, an API call, or a magic value.  These tokens
    # are cheap, high-signal necessary conditions for accepting a rewrite.  A
    # match is not a proof of equivalence, but a mismatch catches the common
    # and dangerous small-model failure mode before it reaches the user.
    _CALL_KEYWORDS = frozenset({
        "if", "for", "while", "switch", "return", "sizeof", "case",
        "do", "else", "typeof", "__typeof__", "__declspec", "defined",
    })
    _STRING_LITERAL_PATTERN = re.compile(
        r'(?:u8|u|U|L)?"(?:\\.|[^"\\])*"', re.DOTALL
    )
    _NUMBER_PATTERN = re.compile(
        r'(?<![A-Za-z0-9_])(?:0[xX][0-9A-Fa-f]+|\d+)[uUlL]*'
    )
    _IDA_GLOBAL_PATTERN = re.compile(
        r'\b(?:g|byte|word|dword|qword|unk|off|asc|stru|flt|dbl)_[A-Za-z0-9_]+'
    )
    _LOCAL_TYPE_WORDS = frozenset({
        "void", "char", "short", "int", "long", "float", "double",
        "signed", "unsigned", "bool", "_Bool", "size_t", "ssize_t",
        "wchar_t", "ptrdiff_t", "BYTE", "WORD", "DWORD", "QWORD",
        "_BYTE", "_WORD", "_DWORD", "_QWORD", "__int8", "__int16",
        "__int32", "__int64", "int8_t", "int16_t", "int32_t", "int64_t",
        "uint8_t", "uint16_t", "uint32_t", "uint64_t", "const", "volatile",
        "restrict", "register", "static", "struct", "union", "enum",
    })
    _RESERVED_IDENTIFIERS = _CALL_KEYWORDS | _LOCAL_TYPE_WORDS | frozenset({
        "break", "continue", "default", "goto", "typedef", "auto",
        "inline", "extern", "asm", "NULL", "true", "false",
    })

    @staticmethod
    def _tokenize_c_code(code: str) -> list:
        """Return C-like tokens while excluding whitespace and comments."""
        tokens = []
        i = 0
        length = len(code or "")

        while i < length:
            char = code[i]
            next_char = code[i + 1] if i + 1 < length else ""

            if char.isspace():
                i += 1
                continue
            if char == "/" and next_char == "/":
                newline = code.find("\n", i + 2)
                i = length if newline == -1 else newline + 1
                continue
            if char == "/" and next_char == "*":
                close = code.find("*/", i + 2)
                i = length if close == -1 else close + 2
                continue
            if char in ('"', "'"):
                start = i
                quote = char
                i += 1
                escaped = False
                while i < length:
                    current = code[i]
                    if escaped:
                        escaped = False
                    elif current == "\\":
                        escaped = True
                    elif current == quote:
                        i += 1
                        break
                    i += 1
                tokens.append(("literal", code[start:i], start, i))
                continue
            if char.isalpha() or char == "_":
                start = i
                i += 1
                while i < length and (code[i].isalnum() or code[i] == "_"):
                    i += 1
                tokens.append(("identifier", code[start:i], start, i))
                continue
            if char.isdigit():
                start = i
                i += 1
                while i < length and (code[i].isalnum() or code[i] in "._"):
                    i += 1
                tokens.append(("number", code[start:i], start, i))
                continue
            if code.startswith("->", i) or code.startswith("::", i):
                tokens.append(("symbol", code[i:i + 2], i, i + 2))
                i += 2
                continue
            tokens.append(("symbol", char, i, i + 1))
            i += 1

        return tokens

    @staticmethod
    def _get_function_body_span(code: str):
        span = CodeProcessor._get_signature_span(code)
        if span is None:
            return None
        _start, _opening, signature_end = span
        body_start = CodeProcessor._skip_whitespace_and_comments(code, signature_end)
        if body_start >= len(code) or code[body_start] != "{":
            return None
        body_end = CodeProcessor._find_matching_delimiter(code, body_start, "{", "}")
        if body_end is None:
            return None
        return body_start, body_end

    @classmethod
    def _parse_simple_local_declaration(cls, statement: list):
        if not statement or statement[-1][1] != ";":
            return None

        values = [token[1] for token in statement[:-1]]
        if not values or any(value in ("=", ",", "(", ")", "{", "}") for value in values):
            return None

        identifier_indexes = [
            index for index, token in enumerate(statement[:-1])
            if token[0] == "identifier"
        ]
        if len(identifier_indexes) < 2:
            return None

        name_index = identifier_indexes[-1]
        name = statement[name_index][1]
        prefix = statement[:name_index]
        if not prefix or not any(
            token[0] == "identifier" and token[1] in cls._LOCAL_TYPE_WORDS
            for token in prefix
        ):
            return None
        first_identifier = next(
            (token[1] for token in prefix if token[0] == "identifier"), ""
        )
        if first_identifier not in cls._LOCAL_TYPE_WORDS:
            return None
        suffix = statement[name_index + 1:-1]
        if any(token[1] not in ("[", "]") and token[0] != "number" for token in suffix):
            return None
        return {
            "name": name,
            "pointer": any(token[1] == "*" for token in prefix),
            "array": any(token[1] == "[" for token in suffix),
            "type_words": {
                token[1] for token in prefix if token[0] == "identifier"
            },
        }

    @classmethod
    def _get_top_level_local_declarations(cls, code: str) -> dict:
        body_span = cls._get_function_body_span(code)
        if body_span is None:
            return {}
        body_start, body_end = body_span
        declarations = {}
        depth = 0
        statement = []

        for token in cls._tokenize_c_code(code):
            if token[2] < body_start or token[2] >= body_end:
                continue
            value = token[1]
            if value == "{":
                depth += 1
                if depth == 1:
                    statement = []
                continue
            if value == "}":
                if depth == 1:
                    statement = []
                depth -= 1
                continue
            if depth != 1:
                continue
            statement.append(token)
            if value == ";":
                declaration = cls._parse_simple_local_declaration(statement)
                if declaration and declaration["name"] not in declarations:
                    declarations[declaration["name"]] = declaration
                statement = []

        return declarations

    @staticmethod
    def _identifier_has_unsafe_context(tokens: list, index: int) -> bool:
        previous = tokens[index - 1][1] if index else ""
        before_previous = tokens[index - 2][1] if index > 1 else ""
        following = tokens[index + 1][1] if index + 1 < len(tokens) else ""
        return (
            previous in (".", "->", "goto", "#")
            or (previous == ">" and before_previous == "-")
            or following in (":", "(")
        )

    @classmethod
    def validate_local_rename_only(cls, original: str, candidate: str) -> tuple:
        """Require a conservative rewrite to change only local identifiers."""
        original_tokens = cls._tokenize_c_code(original)
        candidate_tokens = cls._tokenize_c_code(candidate)
        if len(original_tokens) != len(candidate_tokens):
            return False, "代码 token 数量发生变化", {}

        declarations = cls._get_top_level_local_declarations(original)
        local_names = set(declarations)
        original_identifiers = {
            token[1] for token in original_tokens if token[0] == "identifier"
        }
        unsafe_names = {
            token[1]
            for index, token in enumerate(original_tokens)
            if token[0] == "identifier"
            and token[1] in local_names
            and cls._identifier_has_unsafe_context(original_tokens, index)
        }
        mapping = {}
        targets = set()

        for index, (source, rewritten) in enumerate(zip(original_tokens, candidate_tokens)):
            if source[0] == rewritten[0] and source[1] == rewritten[1]:
                continue
            if source[0] != "identifier" or rewritten[0] != "identifier":
                return False, f"第 {index + 1} 个 token 不属于局部变量重命名", {}
            source_name = source[1]
            target_name = rewritten[1]
            if source_name not in local_names or source_name in unsafe_names:
                return False, f"非安全局部标识符被改写: {source_name}", {}
            if target_name in cls._RESERVED_IDENTIFIERS:
                return False, f"局部变量被改为保留字: {target_name}", {}
            previous = mapping.get(source_name)
            if previous is not None and previous != target_name:
                return False, f"局部变量重命名不一致: {source_name}", {}
            mapping[source_name] = target_name

        for source_name, target_name in mapping.items():
            if source_name == target_name:
                continue
            if target_name in targets:
                return False, f"多个局部变量使用同一个新名称: {target_name}", {}
            if target_name in original_identifiers:
                return False, f"局部变量新名称与原始标识符冲突: {target_name}", {}
            targets.add(target_name)

        for source, rewritten in zip(original_tokens, candidate_tokens):
            if source[0] != "identifier" or source[1] not in mapping:
                continue
            if rewritten[0] != "identifier" or rewritten[1] != mapping[source[1]]:
                return False, f"局部变量重命名不一致: {source[1]}", {}

        return True, "", mapping

    @staticmethod
    def _replace_identifier_tokens(code: str, mapping: dict) -> str:
        if not mapping:
            return code
        pieces = []
        cursor = 0
        for kind, value, start, end in CodeProcessor._tokenize_c_code(code):
            if kind != "identifier" or value not in mapping:
                continue
            pieces.append(code[cursor:start])
            pieces.append(mapping[value])
            cursor = end
        pieces.append(code[cursor:])
        return "".join(pieces)

    @classmethod
    def _next_available_local_name(cls, base: str, identifiers: set) -> str:
        candidate = base
        suffix = 2
        while candidate in identifiers or candidate in cls._RESERVED_IDENTIFIERS:
            candidate = f"{base}_{suffix}"
            suffix += 1
        identifiers.add(candidate)
        return candidate

    @staticmethod
    def _has_increment_and_dereference(source: str, name: str) -> bool:
        escaped = re.escape(name)
        incremented = re.search(rf"(?:\+\+\s*{escaped}\b|\b{escaped}\s*\+\+)", source)
        dereferenced = re.search(rf"\*\s*{escaped}\b", source)
        return bool(incremented and dereferenced)

    @classmethod
    def build_safe_local_rename_mapping(cls, code: str) -> dict:
        """Infer a small set of high-confidence readability names locally."""
        declarations = cls._get_top_level_local_declarations(code)
        if not declarations:
            return {}

        source = cls._strip_comments_and_literals(code)
        identifiers = {
            token[1] for token in cls._tokenize_c_code(code) if token[0] == "identifier"
        }
        mapping = {}
        input_buffers = []

        def add(name: str, base: str):
            if name not in declarations or name in mapping:
                return
            mapping[name] = cls._next_available_local_name(base, identifiers)

        for name, declaration in declarations.items():
            is_char_array = "char" in declaration["type_words"] and declaration["array"]
            reads_input = re.search(
                rf"\b(?:_mingw_scanf|scanf|fgets|gets)\s*\([^;]*\b{re.escape(name)}\b",
                source,
            )
            if is_char_array and reads_input:
                add(name, "input_buffer")
                input_buffers.append(name)

        for name in declarations:
            if re.search(
                rf"\b{re.escape(name)}\s*=\s*(?:_mingw_scanf|scanf)\s*\(", source
            ):
                add(name, "scan_result")

        for name, declaration in declarations.items():
            if not declaration["pointer"]:
                continue
            for input_buffer in input_buffers:
                assigned_from_input = re.search(
                    rf"\b{re.escape(name)}\s*=\s*{re.escape(input_buffer)}\s*;", source
                )
                if assigned_from_input and cls._has_increment_and_dereference(source, name):
                    add(name, "input_cursor")
                    break

        for name, declaration in declarations.items():
            if not declaration["pointer"] or not cls._has_increment_and_dereference(source, name):
                continue
            match = re.search(
                rf"\b{re.escape(name)}\s*=\s*&\s*g_([A-Za-z0-9_]+)", source
            )
            if match:
                stem = re.sub(r"[^A-Za-z0-9_]", "_", match.group(1)).strip("_")
                add(name, f"{stem or 'expected'}_cursor")

        input_cursors = [
            name for name, target in mapping.items() if target.startswith("input_cursor")
        ]
        for name, declaration in declarations.items():
            if name in mapping or declaration["pointer"] or declaration["array"]:
                continue
            if "char" not in declaration["type_words"]:
                continue
            for input_cursor in input_cursors:
                boundary_check = re.search(
                    rf"(?:\+\+\s*{re.escape(input_cursor)}\s*==\s*&\s*{re.escape(name)}|"
                    rf"&\s*{re.escape(name)}\s*==\s*\+\+\s*{re.escape(input_cursor)})",
                    source,
                )
                if boundary_check:
                    add(name, "input_end_marker")
                    break

        return mapping

    @classmethod
    def apply_safe_local_readability(cls, code: str) -> tuple:
        """Apply only verified local identifier substitutions to pseudocode."""
        mapping = cls.build_safe_local_rename_mapping(code)
        if not mapping:
            return code, {}
        candidate = cls._replace_identifier_tokens(code, mapping)
        valid, _reason, confirmed_mapping = cls.validate_local_rename_only(code, candidate)
        if not valid or not confirmed_mapping:
            return code, {}
        return candidate, {
            "result_kind": "local_readability",
            "renamed_locals": len(confirmed_mapping),
            "rename_mapping": confirmed_mapping,
        }

    @staticmethod
    def has_meaningful_change(original: str, candidate: str) -> bool:
        """Ignore whitespace-only changes when deciding whether to show output."""
        original_normalized = re.sub(r"\s+", "", original or "")
        candidate_normalized = re.sub(r"\s+", "", candidate or "")
        return original_normalized != candidate_normalized

    @staticmethod
    def basic_syntax_check(code: str) -> tuple:
        """基础语法检查

        Args:
            code: C代码

        Returns:
            (是否通过, 错误信息)
        """
        if not code or not code.strip():
            return False, "代码为空"

        # Scan delimiters while ignoring strings, character literals and
        # comments. Raw ``str.count`` treats a brace in puts("{") as code and
        # rejects otherwise valid output.
        opening = {"{": "}", "(": ")", "[": "]"}
        closing = set(opening.values())
        stack = []
        state = "normal"
        escaped = False
        i = 0

        while i < len(code):
            ch = code[i]
            nxt = code[i + 1] if i + 1 < len(code) else ""

            if state == "line_comment":
                if ch == "\n":
                    state = "normal"
                i += 1
                continue
            if state == "block_comment":
                if ch == "*" and nxt == "/":
                    state = "normal"
                    i += 2
                else:
                    i += 1
                continue
            if state in ("string", "char"):
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif (state == "string" and ch == '"') or (state == "char" and ch == "'"):
                    state = "normal"
                i += 1
                continue

            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue

            if ch in opening:
                stack.append(ch)
            elif ch in closing:
                if not stack or opening[stack[-1]] != ch:
                    return False, f"括号嵌套不匹配: {ch}"
                stack.pop()
            i += 1

        if state == "block_comment":
            return False, "块注释未闭合"
        if state in ("string", "char"):
            return False, "字符串或字符常量未闭合"
        if stack:
            expected = opening[stack[-1]]
            return False, f"括号不匹配，缺少: {expected}"

        return True, ""

    @staticmethod
    def _find_matching_delimiter(code: str, start: int, opening: str, closing: str):
        """Find a matching delimiter while ignoring comments and literals."""
        depth = 0
        state = "normal"
        escaped = False
        i = start

        while i < len(code):
            ch = code[i]
            nxt = code[i + 1] if i + 1 < len(code) else ""

            if state == "line_comment":
                if ch == "\n":
                    state = "normal"
                i += 1
                continue
            if state == "block_comment":
                if ch == "*" and nxt == "/":
                    state = "normal"
                    i += 2
                else:
                    i += 1
                continue
            if state in ("string", "char"):
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif (state == "string" and ch == '"') or (state == "char" and ch == "'"):
                    state = "normal"
                i += 1
                continue

            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                state = "string"
                i += 1
                continue
            if ch == "'":
                state = "char"
                i += 1
                continue

            if ch == opening:
                depth += 1
            elif ch == closing:
                depth -= 1
                if depth == 0:
                    return i + 1
                if depth < 0:
                    return None
            i += 1

        return None

    @staticmethod
    def _skip_whitespace_and_comments(code: str, start: int) -> int:
        """Advance over legal whitespace/comments between a signature and body."""
        i = start
        while i < len(code):
            while i < len(code) and code[i].isspace():
                i += 1
            if code.startswith("//", i):
                newline = code.find("\n", i + 2)
                i = len(code) if newline == -1 else newline + 1
                continue
            if code.startswith("/*", i):
                end = code.find("*/", i + 2)
                if end == -1:
                    return len(code)
                i = end + 2
                continue
            break
        return i

    @staticmethod
    def extract_complete_function(code: str) -> str:
        """Return the first complete C function from a model response.

        Models frequently append an explanation after a valid function.  The
        viewer must never treat that prose as C code.  Conversely, a truncated
        function returns an empty string so the caller can reject it.
        """
        if not code:
            return ""

        for match in re.finditer(FUNCTION_SIGNATURE_PATTERN, code, re.MULTILINE):
            # FUNCTION_SIGNATURE_PATTERN ends immediately after the function
            # parameter list's opening parenthesis.
            signature_open = match.end() - 1
            signature_end = CodeProcessor._find_matching_delimiter(
                code, signature_open, "(", ")"
            )
            if signature_end is None:
                continue

            body_open = CodeProcessor._skip_whitespace_and_comments(code, signature_end)
            if body_open >= len(code) or code[body_open] != "{":
                continue

            function_end = CodeProcessor._find_matching_delimiter(
                code, body_open, "{", "}"
            )
            if function_end is not None:
                return code[match.start():function_end].strip()

        return ""

    @staticmethod
    def _strip_comments_and_literals(code: str) -> str:
        """Replace comments and string/char literals with whitespace."""
        result = []
        state = "normal"
        escaped = False
        i = 0

        while i < len(code):
            ch = code[i]
            nxt = code[i + 1] if i + 1 < len(code) else ""

            if state == "line_comment":
                if ch == "\n":
                    state = "normal"
                    result.append("\n")
                else:
                    result.append(" ")
                i += 1
                continue
            if state == "block_comment":
                if ch == "*" and nxt == "/":
                    result.extend((" ", " "))
                    state = "normal"
                    i += 2
                else:
                    result.append("\n" if ch == "\n" else " ")
                    i += 1
                continue
            if state in ("string", "char"):
                result.append("\n" if ch == "\n" else " ")
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif (state == "string" and ch == '"') or (state == "char" and ch == "'"):
                    state = "normal"
                i += 1
                continue

            if ch == "/" and nxt == "/":
                result.extend((" ", " "))
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                result.extend((" ", " "))
                state = "block_comment"
                i += 2
                continue
            if ch == '"':
                result.append(" ")
                state = "string"
                i += 1
                continue
            if ch == "'":
                result.append(" ")
                state = "char"
                i += 1
                continue

            result.append(ch)
            i += 1

        return "".join(result)

    @staticmethod
    def _extract_signature(code: str) -> str:
        """Extract and normalize the first function declaration signature."""
        span = CodeProcessor._get_signature_span(code)
        if span is None:
            return ""

        start, _opening, end = span
        signature = code[start:end]
        signature = CodeProcessor._strip_comments_and_literals(signature)
        signature = re.sub(r"\s+", " ", signature).strip()
        return re.sub(r"\s*([*(),])\s*", r"\1", signature)

    @staticmethod
    def _get_signature_span(code: str):
        """Return (start, parameter-opening, end) for the first signature."""
        if not code:
            return None
        match = re.search(FUNCTION_SIGNATURE_PATTERN, code, re.MULTILINE)
        if not match:
            return None

        opening = match.end() - 1
        end = CodeProcessor._find_matching_delimiter(code, opening, "(", ")")
        if end is None:
            return None
        return match.start(), opening, end

    @staticmethod
    def _extract_parameter_names(code: str, span) -> tuple:
        """Extract simple parameter names; return None for complex declarators."""
        if span is None:
            return None
        _start, opening, end = span
        parameters = CodeProcessor._strip_comments_and_literals(code[opening + 1:end - 1]).strip()
        if not parameters or parameters == "void":
            return ()
        # Function-pointer parameters need a real C parser.  Do not attempt a
        # signature repair for them; rejecting is safer than guessing.
        if "(" in parameters or ")" in parameters:
            return None

        names = []
        for parameter in parameters.split(","):
            identifiers = re.findall(r"[A-Za-z_]\w*", parameter)
            if not identifiers:
                return None
            names.append(identifiers[-1])
        return tuple(names)

    @staticmethod
    def restore_unused_parameter_signature(original: str, candidate: str) -> tuple:
        """Restore an ABI signature only when parameter names are unused.

        Small models often turn ``__fastcall`` into a plain C signature or
        drop a pointer level while leaving an otherwise faithful body.  The
        original declaration is authoritative.  It is safe to restore it only
        when both signatures use the same names and none of those parameters
        occurs in the candidate body; otherwise a type/name change could alter
        the body's meaning and the candidate is left for the quality guard to
        reject.
        """
        original_span = CodeProcessor._get_signature_span(original)
        candidate_span = CodeProcessor._get_signature_span(candidate)
        if original_span is None or candidate_span is None:
            return candidate, False
        if CodeProcessor._extract_signature(original) == CodeProcessor._extract_signature(candidate):
            return candidate, False
        if CodeProcessor._extract_function_name(original) != CodeProcessor._extract_function_name(candidate):
            return candidate, False

        original_names = CodeProcessor._extract_parameter_names(original, original_span)
        candidate_names = CodeProcessor._extract_parameter_names(candidate, candidate_span)
        if original_names is None or original_names != candidate_names:
            return candidate, False

        _candidate_start, _candidate_opening, candidate_end = candidate_span
        candidate_body = CodeProcessor._strip_comments_and_literals(candidate[candidate_end:])
        for name in candidate_names:
            if re.search(r"\b" + re.escape(name) + r"\b", candidate_body):
                return candidate, False

        original_start, _original_opening, original_end = original_span
        candidate_start, _candidate_opening, _candidate_end = candidate_span
        restored = (
            candidate[:candidate_start]
            + original[original_start:original_end]
            + candidate[candidate_end:]
        )
        return restored, True

    @staticmethod
    def _extract_function_name(code: str) -> str:
        """Return the name from a function signature, if one is present."""
        signature = CodeProcessor._extract_signature(code)
        if not signature:
            return ""
        before_parameters = signature.rsplit("(", 1)[0]
        names = re.findall(r"[A-Za-z_]\w*", before_parameters)
        return names[-1] if names else ""

    @staticmethod
    def _extract_function_calls(code: str) -> set:
        """Extract direct C-style calls, excluding syntax and the definition."""
        source = CodeProcessor._strip_comments_and_literals(code)
        own_name = CodeProcessor._extract_function_name(code)
        non_calls = CodeProcessor._CALL_KEYWORDS | {
            "void", "char", "short", "int", "long", "float", "double",
            "signed", "unsigned", "struct", "union", "enum", "bool",
            "_Bool", "__int8", "__int16", "__int32", "__int64",
        }
        calls = set()
        for name in re.findall(r"\b([A-Za-z_]\w*)\s*\(", source):
            if name not in non_calls and name != own_name:
                calls.add(name)
        return calls

    @staticmethod
    def _extract_numeric_literals(code: str) -> set:
        """Extract integer values so 0x52 and 82 compare as the same anchor."""
        numbers = set()
        source = CodeProcessor._strip_comments_and_literals(code)
        for token in CodeProcessor._NUMBER_PATTERN.findall(source):
            numeric = re.sub(r"[uUlL]+$", "", token)
            try:
                numbers.add(int(numeric, 0))
            except ValueError:
                continue
        return numbers

    @classmethod
    def _extract_ida_global_symbols(cls, code: str) -> set:
        """Extract IDA globals from code, excluding comments and literals."""
        source = cls._strip_comments_and_literals(code)
        return set(cls._IDA_GLOBAL_PATTERN.findall(source))

    @staticmethod
    def validate_semantic_preservation(
        original: str,
        candidate: str,
        minimum_output_ratio: float = DEFAULT_MINIMUM_OUTPUT_RATIO,
        strict_anchors: bool = True,
    ) -> tuple:
        """Check structural and, optionally, semantic preservation.

        Strict mode requires all high-signal anchors from the source.  Full
        rewrite mode may remove redundant source calls/constants, but still
        requires a complete function with the original signature, a reasonable
        output size, and no unverified new function calls.
        """
        if not original:
            return True, ""

        original_function = CodeProcessor.extract_complete_function(original)
        candidate_function = CodeProcessor.extract_complete_function(candidate)
        if not original_function or not candidate_function:
            return False, "未找到完整函数"

        original_signature = CodeProcessor._extract_signature(original_function)
        candidate_signature = CodeProcessor._extract_signature(candidate_function)
        if original_signature and original_signature != candidate_signature:
            return False, "函数签名或参数类型被改写"

        if strict_anchors:
            anchors = (
                ("关键调用", CodeProcessor._extract_function_calls),
                ("字符串常量", lambda text: set(CodeProcessor._STRING_LITERAL_PATTERN.findall(text))),
                ("数值常量", CodeProcessor._extract_numeric_literals),
                ("IDA 全局符号", CodeProcessor._extract_ida_global_symbols),
            )
            for label, extractor in anchors:
                expected = extractor(original_function)
                actual = extractor(candidate_function)
                missing = expected - actual
                if missing:
                    examples = ", ".join(sorted(map(str, missing))[:4])
                    return False, f"{label}丢失: {examples}"

        original_calls = CodeProcessor._extract_function_calls(original_function)
        candidate_calls = CodeProcessor._extract_function_calls(candidate_function)
        unexpected_calls = candidate_calls - original_calls
        if unexpected_calls:
            examples = ", ".join(sorted(unexpected_calls)[:4])
            return False, f"引入未验证调用: {examples}"

        original_globals = CodeProcessor._extract_ida_global_symbols(original_function)
        candidate_globals = CodeProcessor._extract_ida_global_symbols(candidate_function)
        unexpected_globals = candidate_globals - original_globals
        if unexpected_globals:
            examples = ", ".join(sorted(unexpected_globals)[:4])
            return False, f"引入未验证 IDA 全局符号: {examples}"

        try:
            ratio = float(minimum_output_ratio)
        except (TypeError, ValueError):
            ratio = DEFAULT_MINIMUM_OUTPUT_RATIO
        ratio = min(max(ratio, 0.1), 1.0)
        original_length = len(re.sub(r"\s+", "", original_function))
        candidate_length = len(re.sub(r"\s+", "", candidate_function))
        if original_length and candidate_length < original_length * ratio:
            return False, f"输出过短 ({candidate_length}/{original_length} 字符)"

        return True, ""

    @classmethod
    def is_quality_rejection(cls, message: str) -> bool:
        """Return whether ``message`` is a semantic quality-guard failure."""
        return bool(message and message.lstrip().startswith(cls.QUALITY_REJECTION_PREFIX))

    @classmethod
    def get_quality_rejection_reason(cls, message: str) -> str:
        """Extract the concise invariant violation for a repair prompt."""
        if not cls.is_quality_rejection(message):
            return ""

        reason = message.lstrip()[len(cls.QUALITY_REJECTION_PREFIX):].strip()
        suffix = "。原始伪代码未被替换，结果未缓存"
        if reason.endswith(suffix):
            reason = reason[:-len(suffix)]
        return reason.rstrip("。")

    @staticmethod
    def get_required_semantic_anchors(original: str) -> str:
        """Summarize the minimum invariants for a model repair request.

        This is deliberately a readable checklist rather than a substitute for
        ``validate_semantic_preservation``.  The latter remains the authority
        when deciding whether an answer is safe to display.
        """
        source = CodeProcessor.extract_complete_function(original) or (original or "")
        if not source:
            return ""

        entries = []
        signature = CodeProcessor._extract_signature(source)
        if signature:
            entries.append(f"函数签名: {signature}")

        for label, values in (
            ("外部调用", CodeProcessor._extract_function_calls(source)),
            ("IDA 全局符号", CodeProcessor._extract_ida_global_symbols(source)),
            ("字符串常量", set(CodeProcessor._STRING_LITERAL_PATTERN.findall(source))),
            ("数值常量", CodeProcessor._extract_numeric_literals(source)),
        ):
            if values:
                rendered = ", ".join(sorted(map(str, values))[:16])
                entries.append(f"{label}: {rendered}")
        return "\n".join(entries)

    @staticmethod
    def remove_extra_whitespace(code: str) -> str:
        """移除多余的空白字符

        Args:
            code: C代码

        Returns:
            清理后的代码
        """
        # 移除行尾空白
        lines = code.split('\n')
        lines = [line.rstrip() for line in lines]

        # 移除连续的空行（最多保留一个）
        result = []
        prev_empty = False

        for line in lines:
            if line.strip():
                result.append(line)
                prev_empty = False
            else:
                if not prev_empty:
                    result.append(line)
                prev_empty = True

        # 去除首尾空行（首尾空行无意义）
        while result and not result[0].strip():
            result.pop(0)
        while result and not result[-1].strip():
            result.pop()

        return '\n'.join(result)

    @staticmethod
    def clean_llm_artifacts(code: str) -> str:
        """清理LLM生成的非代码内容

        Args:
            code: 可能包含解释文本的代码

        Returns:
            纯代码
        """
        # 先剥离推理模型的思考块（<think>...</think> 等）
        code = CodeProcessor.strip_reasoning_blocks(code)

        # 移除markdown代码块标记
        code = re.sub(r'^```[a-z]*\n?', '', code, flags=re.MULTILINE)
        code = re.sub(r'\n?```$', '', code)

        # 移除常见的解释性前缀和后缀
        lines = code.split('\n')
        cleaned = []

        skip_patterns = [
            r'^(这是|以下是|优化后的代码|最终代码|Here is|The optimized code|Final code)',
            r'^(解释|说明|注意|Explanation|Note|Summary|总结)[：:]',
            r'^(思考|分析|Analysis|Thinking)[：:]',
        ]

        in_code = False
        for line in lines:
            stripped = line.strip()

            # 检测到函数签名行，开始收集代码
            if not in_code and re.match(FUNCTION_SIGNATURE_PATTERN, stripped):
                in_code = True

            # 已进入代码块，收集所有行
            if in_code:
                cleaned.append(line)
                continue

            # 尚未进入代码，跳过解释性行
            if any(re.match(pattern, stripped, re.IGNORECASE) for pattern in skip_patterns):
                continue

            # 保留可能是代码的行
            if stripped and ('{' in stripped or '}' in stripped or ';' in stripped or '#include' in stripped):
                in_code = True
                cleaned.append(line)
            elif not stripped:
                # 空行保留（可能是代码中的分隔）
                cleaned.append(line)

        cleaned_code = '\n'.join(cleaned)
        # Keep only the first balanced top-level function.  This removes an
        # explanation (or a second accidental answer) appended by the model.
        complete_function = CodeProcessor.extract_complete_function(cleaned_code)
        return complete_function or cleaned_code

    @staticmethod
    def strip_reasoning_blocks(code: str, tags=None) -> str:
        """剥离推理模型的思考块（如 <think>...</think>）

        推理模型（VibeThinker、DeepSeek-R1 等）会先输出一段 <think> 思考过程
        再给出最终答案。这段思考内容对用户无用，且会污染后续的语法检查和
        对比展示，必须先剥离。

        处理两种情况：
        1. 完整闭合：<think>思考内容</think>最终代码 -> 只保留最终代码
        2. 未闭合（流式中断）：<think>思考内容（无结束标签）-> 整体丢弃，
           因为此时还没有进入答案部分

        Args:
            code: LLM原始输出
            tags: 需要剥离的标签名列表，默认 ["think", "thinking"]

        Returns:
            剥离思考块后的内容
        """
        if not code:
            return code

        if tags is None:
            tags = ["think", "thinking"]

        result = code
        for tag in tags:
            # 优先匹配完整闭合的块，非贪婪
            pattern = re.compile(
                r'<' + re.escape(tag) + r'>.*?</' + re.escape(tag) + r'>',
                re.DOTALL | re.IGNORECASE
            )
            result = pattern.sub('', result)

            # 处理未闭合的块（流式中断或模型未输出结束标签）
            # 如果存在开标签但无闭标签，删除开标签及其后的所有内容
            open_pattern = re.compile(
                r'<' + re.escape(tag) + r'>.*',
                re.DOTALL | re.IGNORECASE
            )
            result = open_pattern.sub('', result)

        return result.strip()

    @staticmethod
    def preserve_ida_annotations(original: str, optimized: str) -> str:
        """尝试保留IDA的特殊注释和标注

        Args:
            original: 原始IDA伪C代码
            optimized: 优化后的代码

        Returns:
            合并了IDA注释的优化代码
        """
        # 提取IDA特有的注释（如XREF、JUMPOUT等）
        ida_comments = re.findall(r'//.*?(?:XREF|JUMPOUT|DATA XREF).*', original)

        # 如果有IDA特殊注释，尝试添加到优化代码的开头
        # Do not prepend annotations that are already present in a fallback or
        # in a model output that preserved the original comments.
        missing_comments = [comment for comment in ida_comments if comment not in optimized]
        if missing_comments:
            header = '\n'.join(missing_comments) + '\n\n'
            return header + optimized

        return optimized

    @staticmethod
    def detect_and_truncate_degeneration(code: str, threshold: int = DEGENERATION_THRESHOLD) -> tuple:
        """检测并截断重复退化输出

        小模型在啃不动硬骨头时会进入重复退化，生成大量结构相同、仅编号递增的
        行（如 `unsigned __int16 vN = vN+1 ^ vN+2;` 重复上百行）。这是即使
        加了 repeat_penalty 仍可能发生的兜底场景。

        策略：
        - 把行归一化（去掉变量编号数字、空白）得到"骨架"
        - 若同一骨架连续出现 >= threshold 次，判定退化
        - 保留退化起点之前的全部内容 + 首个重复行，丢弃后续重复
        - 若退化点之后还有明显的新结构（如 `for`、`return`），尝试保留

        Args:
            code: LLM 生成的代码
            threshold: 连续重复触发阈值

        Returns:
            (截断后的代码, 是否检测到退化)
        """
        if not code:
            return code, False

        lines = code.split('\n')
        if len(lines) < threshold * 2:
            return code, False

        def skeleton(line: str) -> str:
            # 去掉变量编号数字（v123 -> v），压缩空白，转小写
            s = re.sub(r'\b[vV]\d+\b', 'v', line)
            s = re.sub(r'\b[a-zA-Z_]+\d+\b', 'x', s)  # 任意标识符带数字也归一
            s = re.sub(r'\d+', '0', s)
            s = re.sub(r'\s+', ' ', s).strip().lower()
            return s

        skel_prev = None
        run_len = 1
        truncate_at = None

        for idx, line in enumerate(lines):
            sk = skeleton(line)
            if not sk:
                run_len = 1
                skel_prev = None
                continue
            if sk == skel_prev:
                run_len += 1
                if run_len >= threshold and truncate_at is None:
                    # 退化起点：保留到首个重复行（含），截断后续
                    truncate_at = idx
                    break
            else:
                run_len = 1
                skel_prev = sk

        if truncate_at is None:
            return code, False

        # 保留退化起点行（首个重复行），丢弃其后所有重复
        kept = lines[:truncate_at + 1]
        truncated = '\n'.join(kept)

        # 尝试从被丢弃的部分里捞回真正的代码收尾（return/} 等）
        tail_patterns = [r'^\s*return\b', r'^\s*\}\s*$', r'^\s*for\b', r'^\s*printf']
        for line in lines[truncate_at + 1:]:
            if any(re.match(p, line) for p in tail_patterns):
                truncated += '\n' + line

        return truncated, True

    @staticmethod
    def is_incomplete_fragment(code: str, original: str = None) -> bool:
        """检测是否为不完整的代码片段（而非完整函数）

        判定依据：必须能从函数签名找到一个字符串/注释感知的完整函数体。
        这避免将 printf("{") 等合法代码误判为不完整。

        Args:
            code: 待检测的代码
            original: 原始代码（可选，用于长度对比）

        Returns:
            不完整返回 True
        """
        if not code or not code.strip():
            return True

        stripped = code.strip()

        # A raw brace count is incorrect for printf("{") and comments.  Reuse
        # the delimiter-aware extractor used by artifact cleanup instead.
        if CodeProcessor.extract_complete_function(stripped):
            return False
        return True

    @staticmethod
    def process(code: str, original: str = None, config=None) -> tuple:
        """完整的后处理流程

        Args:
            code: LLM生成的代码
            original: 原始IDA代码（可选，用于保留注释）
            config: Config实例（可选，用于读取 degeneration_guard 开关）

        Returns:
            (处理后的代码, 是否成功, 错误信息)
        """
        logger = get_logger()

        if not code:
            logger.error("处理失败：代码为空")
            return "", False, "代码为空"

        # 1. 清理LLM生成的非代码内容
        code = CodeProcessor.clean_llm_artifacts(code)
        logger.debug("已清理 LLM 生成的非代码内容")

        # Recover the authoritative IDA signature for a narrowly safe case:
        # a model changed only ABI/type spelling while the same parameters are
        # completely unused by its body.  This avoids rejecting harmless
        # formatting drift without weakening semantic checks for real changes.
        restore_signature = True
        if config is not None:
            restore_signature = config.get("llm.restore_unused_parameter_signature", True)
        if original and restore_signature:
            code, signature_restored = CodeProcessor.restore_unused_parameter_signature(
                original, code
            )
            if signature_restored:
                logger.info("模型改写了未使用参数的函数签名，已恢复 IDA 原始签名")

        # 2. 退化检测兜底（在语法检查前，避免重复行撑爆统计）
        guard = True
        if config is not None:
            guard = config.get("llm.degeneration_guard", True)
        degenerated = False
        if guard:
            code, degenerated = CodeProcessor.detect_and_truncate_degeneration(code)
            if degenerated:
                logger.warning("检测到重复退化输出，已截断")

        # 2b. Never present or cache a fallback as an "optimized" result.
        # The IDA pseudocode view already retains the original, and accepting a
        # fragment here used to create a misleading one-character cache entry.
        if CodeProcessor.is_incomplete_fragment(code, original):
            msg = "模型未输出完整函数，已保留原始伪代码（结果未缓存）"
            return "", False, msg

        # 3. 基础语法检查
        passed, error = CodeProcessor.basic_syntax_check(code)
        if not passed:
            msg = f"语法检查失败: {error}"
            if degenerated:
                msg += "（已截断重复退化输出）"
            logger.error(msg)
            return code, False, msg

        # 3b. A balanced function can still be a hallucinated rewrite.  The
        # quality guard compares high-signal anchors before it is shown or
        # cached.  Users who deliberately want aggressive transformations can
        # opt out through llm.quality_guard.
        quality_guard = True
        minimum_output_ratio = DEFAULT_MINIMUM_OUTPUT_RATIO
        strict_anchors = True
        if config is not None:
            quality_guard = config.get("llm.quality_guard", True)
            minimum_output_ratio = config.get(
                "llm.minimum_output_ratio", DEFAULT_MINIMUM_OUTPUT_RATIO
            )
            # ``off`` is the deliberate full-rewrite profile. Keep structural
            # checks enabled while allowing the user to remove redundant source
            # anchors such as dead calls, strings, and constants.
            conservative_mode = str(
                config.get("llm.conservative_mode", "auto") or "auto"
            ).lower()
            strict_anchors = conservative_mode != "off"
        if original and quality_guard:
            preserved, preservation_error = CodeProcessor.validate_semantic_preservation(
                original, code, minimum_output_ratio, strict_anchors=strict_anchors
            )
            if not preserved:
                msg = (
                    f"{CodeProcessor.QUALITY_REJECTION_PREFIX} {preservation_error}。"
                    "原始伪代码未被替换，结果未缓存"
                )
                return "", False, msg

        # 4. 清理空白字符
        code = CodeProcessor.remove_extra_whitespace(code)

        # 5. 保留IDA注释
        if original:
            code = CodeProcessor.preserve_ida_annotations(original, code)

        if degenerated:
            logger.info("处理成功（已截断重复退化输出）")
            return code, True, "已截断重复退化输出"

        logger.debug("代码处理完成")
        return code, True, ""

    @staticmethod
    def calculate_diff_stats(original: str, optimized: str) -> dict:
        """计算代码差异统计

        Args:
            original: 原始代码
            optimized: 优化后的代码

        Returns:
            差异统计字典
        """
        original_lines = original.split('\n')
        optimized_lines = optimized.split('\n')

        stats = {
            "original_lines": len(original_lines),
            "optimized_lines": len(optimized_lines),
            "original_chars": len(original),
            "optimized_chars": len(optimized),
            "line_diff": len(optimized_lines) - len(original_lines),
            "char_diff": len(optimized) - len(original)
        }

        return stats
