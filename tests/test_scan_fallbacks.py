"""Unit tests for the fallback-pattern scanner.

Each test uses Arrange-Act-Assert and constructs minimal AST inputs
rather than writing fixture files.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".claude" / "hooks"))

from scan_fallbacks_core import (
    FallbackVisitor,
    Finding,
    collect_fallback_comments,
    config_to_params,
    format_text_report,
    handler_is_importerror,
    handler_is_swallow,
    is_fallback_literal,
    is_known_fallback_helper,
    is_none_check,
    is_none_constant,
    iter_python_files,
    load_toml_config,
    scan_file,
)


def _visit(src: str) -> list[Finding]:
    tree = ast.parse(src)
    visitor = FallbackVisitor(src.splitlines(), "sample.py")
    visitor.visit(tree)
    return visitor.findings


def _patterns(findings: list[Finding]) -> list[str]:
    return [f.pattern for f in findings]


def test_is_none_constant_recognizes_only_none() -> None:
    assert is_none_constant(ast.parse("None", mode="eval").body) is True
    assert is_none_constant(ast.parse("0", mode="eval").body) is False
    assert is_none_constant(ast.parse("x", mode="eval").body) is False


def test_is_fallback_literal_matches_defaults_and_empty_collections() -> None:
    assert is_fallback_literal(ast.parse("0", mode="eval").body) is True
    assert is_fallback_literal(ast.parse("''", mode="eval").body) is True
    assert is_fallback_literal(ast.parse("False", mode="eval").body) is True
    assert is_fallback_literal(ast.parse("None", mode="eval").body) is True
    assert is_fallback_literal(ast.parse("[]", mode="eval").body) is True
    assert is_fallback_literal(ast.parse("{}", mode="eval").body) is True
    assert is_fallback_literal(ast.parse("()", mode="eval").body) is True
    assert is_fallback_literal(ast.parse("[1]", mode="eval").body) is False
    assert is_fallback_literal(ast.parse("'x'", mode="eval").body) is False
    assert is_fallback_literal(ast.parse("name", mode="eval").body) is False


def test_is_none_check_handles_is_isnot_and_not() -> None:
    assert is_none_check(ast.parse("x is None", mode="eval").body) is True
    assert is_none_check(ast.parse("x is not None", mode="eval").body) is True
    assert is_none_check(ast.parse("not x", mode="eval").body) is True
    assert is_none_check(ast.parse("x == 0", mode="eval").body) is False


def test_is_known_fallback_helper_matches_names_and_prefixes() -> None:
    assert is_known_fallback_helper("_prefer") is True
    assert is_known_fallback_helper("fallback") is True
    assert is_known_fallback_helper("_safe_div") is True
    assert is_known_fallback_helper("safe_get") is True
    assert is_known_fallback_helper("compute") is False
    assert is_known_fallback_helper(None) is False


def test_or_default_detects_literal_operands() -> None:
    findings = _visit("value = (x or 0) + (y or [])")
    assert _patterns(findings) == ["or_default", "or_default"]


def test_or_default_ignores_two_names() -> None:
    findings = _visit("value = a or b")
    assert _patterns(findings) == []


def test_or_default_with_none_literal_is_hit() -> None:
    findings = _visit("value = x or None")
    assert _patterns(findings) == ["or_default"]


def test_ternary_none_else_detects_is_not_none() -> None:
    findings = _visit("r = v if v is not None else fallback")
    assert "ternary_none_else" in _patterns(findings)


def test_ternary_none_else_detects_bare_name_test() -> None:
    findings = _visit("r = v if not v else fallback")
    assert "ternary_none_else" in _patterns(findings)


def test_dict_get_default_with_literal_is_hit() -> None:
    findings = _visit("v = d.get('k', 0)")
    assert _patterns(findings) == ["dict_get_default"]


def test_dict_get_with_none_default_is_ignored() -> None:
    findings = _visit("v = d.get('k', None)")
    assert _patterns(findings) == []


def test_dict_get_without_default_is_ignored() -> None:
    findings = _visit("v = d.get('k')")
    assert _patterns(findings) == []


def test_getattr_with_none_default_is_still_hit() -> None:
    findings = _visit("v = getattr(mod, 'x', None)")
    assert _patterns(findings) == ["getattr_default"]


def test_getattr_with_non_none_default_is_hit() -> None:
    findings = _visit("v = getattr(mod, 'x', 0)")
    assert _patterns(findings) == ["getattr_default"]


def test_getattr_without_default_is_ignored() -> None:
    findings = _visit("v = getattr(mod, 'x')")
    assert _patterns(findings) == []


def test_handler_is_swallow_for_pass() -> None:
    handler = ast.parse("try:\n    f()\nexcept Exception:\n    pass\n").body[0].handlers[0]
    assert handler_is_swallow(handler) is True


def test_handler_is_not_swallow_when_raises() -> None:
    handler = ast.parse(
        "try:\n    f()\nexcept Exception:\n    raise\n"
    ).body[0].handlers[0]
    assert handler_is_swallow(handler) is False


def test_handler_is_swallow_for_return_none() -> None:
    src = inspect.cleandoc(
        """
        def g() -> None:
            try:
                return f()
            except Exception:
                return None
        """
    )
    handler = ast.parse(src).body[0].body[0].handlers[0]
    assert handler_is_swallow(handler) is True


def test_try_except_swallow_emits_finding() -> None:
    src = inspect.cleandoc(
        """
        try:
            do()
        except OSError:
            pass
        """
    )
    findings = _visit(src)
    assert "try_except_swallow" in _patterns(findings)


def test_handler_is_importerror_accepts_bare_and_named() -> None:
    src = inspect.cleandoc(
        """
        try:
            import foo
        except ImportError:
            import bar
        """
    )
    handler = ast.parse(src).body[0].handlers[0]
    assert handler_is_importerror(handler) is True


def test_import_fallback_is_emitted_for_importerror_handler() -> None:
    src = inspect.cleandoc(
        """
        try:
            import foo
        except ImportError:
            import bar
        """
    )
    findings = _visit(src)
    assert "import_fallback" in _patterns(findings)


def test_if_none_assign_detects_single_line_body() -> None:
    src = inspect.cleandoc(
        """
        if x is None:
            x = default
        """
    )
    findings = _visit(src)
    assert "if_none_assign" in _patterns(findings)


def test_if_none_assign_ignores_multi_line_body() -> None:
    src = inspect.cleandoc(
        """
        if x is None:
            log('missing')
            x = default
        """
    )
    findings = _visit(src)
    assert "if_none_assign" not in _patterns(findings)


def test_fallback_call_detects_prefer_helper() -> None:
    findings = _visit("r = _prefer(direct, fallback)")
    assert _patterns(findings) == ["fallback_call"]


def test_fallback_call_detects_safe_prefix() -> None:
    findings = _visit("r = _safe_div(a, b)")
    assert _patterns(findings) == ["fallback_call"]


def test_fallback_call_ignores_unrelated_functions() -> None:
    findings = _visit("r = compute(a, b)")
    assert _patterns(findings) == []


def test_collect_fallback_comments_picks_up_case_insensitive(tmp_path: Path) -> None:
    target: Path = tmp_path / "sample.py"
    target.write_text(
        "x = 1  # Fallback: use default\n"
        "y = 2  # normal comment\n"
        "z = 3  # FALLBACK path\n"
    )
    findings = collect_fallback_comments(target, target.read_text().splitlines(), "sample.py")
    assert [f.line for f in findings] == [1, 3]
    assert all(f.pattern == "fallback_comment" for f in findings)


def test_collect_fallback_comments_ignores_string_literals(tmp_path: Path) -> None:
    target: Path = tmp_path / "sample.py"
    target.write_text('msg = "fallback here"\n')
    findings = collect_fallback_comments(target, target.read_text().splitlines(), "sample.py")
    assert findings == []


def test_scan_file_aggregates_ast_and_comment_findings(tmp_path: Path) -> None:
    target: Path = tmp_path / "synthetic.py"
    target.write_text(
        inspect.cleandoc(
            """
            # Fallback: replace missing BS values
            def compute(value: int | None, default: int) -> int:
                result = value or 0
                other = d.get('k', 0)
                return _prefer(value, default)
            """
        )
        + "\n"
    )
    findings = scan_file(target)
    patterns = {f.pattern for f in findings}
    assert "or_default" in patterns
    assert "dict_get_default" in patterns
    assert "fallback_call" in patterns
    assert "fallback_comment" in patterns


def test_scan_file_handles_syntax_error(tmp_path: Path) -> None:
    target: Path = tmp_path / "broken.py"
    target.write_text("def (:\n")
    assert scan_file(target) == []


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_scan_params() -> tuple[tuple[str, ...], frozenset[str]]:
    cfg = load_toml_config(_PROJECT_ROOT)
    scan_roots, exclude_dirs, _fn_names, _fn_prefixes = config_to_params(cfg)
    return scan_roots, exclude_dirs


def test_iter_python_files_excludes_venv_and_tests() -> None:
    scan_roots, exclude_dirs = _load_scan_params()
    files = iter_python_files(_PROJECT_ROOT, scan_roots, exclude_dirs)
    for path in files:
        parts = set(path.parts)
        assert ".venv" not in parts
        assert "tests" not in parts
        assert "browser_service" not in parts
        assert "__pycache__" not in parts


def test_iter_python_files_includes_src_strategies_scripts() -> None:
    scan_roots, exclude_dirs = _load_scan_params()
    files = iter_python_files(_PROJECT_ROOT, scan_roots, exclude_dirs)
    joined: str = "|".join(str(p) for p in files)
    assert "src/formula_screening/metrics.py" in joined
    assert "strategies/net_cash.py" in joined
    assert "scripts/export_csv.py" in joined


def test_format_text_report_groups_by_pattern() -> None:
    findings: list[Finding] = [
        Finding(file="a.py", line=1, col=0, pattern="or_default", snippet="x = a or 0"),
        Finding(file="b.py", line=5, col=4, pattern="or_default", snippet="y = b or []"),
        Finding(file="c.py", line=2, col=0, pattern="fallback_call", snippet="_prefer(a, b)"),
    ]
    report = format_text_report(findings, file_count=3, scan_roots=("src",))
    assert "## or_default (2)" in report
    assert "## fallback_call (1)" in report
    assert "Total: 3 findings" in report
