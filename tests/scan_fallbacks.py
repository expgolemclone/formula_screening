#!/usr/bin/env python3
"""Inventory fallback-style code paths across the project.

Exits non-zero when any findings are present so it can act as a CI gate:
the project prohibits fallback patterns (see feedback_no_fallbacks).

Run: uv run python tests/scan_fallbacks.py [--pattern NAME] [--json] [--quiet]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import tokenize
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
SCAN_ROOTS: tuple[str, ...] = ("src", "strategies", "scripts")
EXCLUDE_DIRS: frozenset[str] = frozenset(
    {"__pycache__", ".venv", "browser_service", "tests", "data"}
)

_FALLBACK_FN_NAMES: frozenset[str] = frozenset(
    {"_prefer", "fallback", "coalesce", "_default"}
)
_FALLBACK_FN_PREFIXES: tuple[str, ...] = ("safe_", "_safe_")
_FALLBACK_LITERAL_VALUES: frozenset[object] = frozenset({0, 0.0, False, "", None})


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    col: int
    pattern: str
    snippet: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col} [{self.pattern}] {self.snippet}"


def is_none_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def is_fallback_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return node.value in _FALLBACK_LITERAL_VALUES
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    return isinstance(node, ast.Dict) and not node.keys


def is_none_check(test: ast.AST) -> bool:
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        op = test.ops[0]
        if isinstance(op, (ast.Is, ast.IsNot)) and is_none_constant(test.comparators[0]):
            return True
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return isinstance(test.operand, ast.Name)
    return False


def handler_is_importerror(handler: ast.ExceptHandler) -> bool:
    exc = handler.type
    if exc is None:
        return True
    return isinstance(exc, ast.Name) and exc.id in {"ImportError", "ModuleNotFoundError"}


def handler_is_swallow(handler: ast.ExceptHandler) -> bool:
    body = handler.body
    if not body or any(isinstance(stmt, ast.Raise) for stmt in body):
        return False
    if len(body) == 1 and isinstance(body[0], ast.Pass):
        return True
    last = body[-1]
    if isinstance(last, ast.Return) and (last.value is None or isinstance(last.value, ast.Constant)):
        return True
    return isinstance(last, ast.Assign)


def extract_callable_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def is_known_fallback_helper(name: str | None) -> bool:
    if name is None:
        return False
    return name in _FALLBACK_FN_NAMES or any(name.startswith(p) for p in _FALLBACK_FN_PREFIXES)


class FallbackVisitor(ast.NodeVisitor):
    def __init__(self, source_lines: list[str], rel_path: str) -> None:
        self.findings: list[Finding] = []
        self._lines = source_lines
        self._rel = rel_path

    def _snippet(self, node: ast.AST) -> str:
        idx = node.lineno - 1
        return self._lines[idx].strip() if 0 <= idx < len(self._lines) else ""

    def _record(self, node: ast.AST, pattern: str) -> None:
        self.findings.append(
            Finding(self._rel, node.lineno, node.col_offset, pattern, self._snippet(node))
        )

    def visit_Try(self, node: ast.Try) -> None:
        body_is_imports = bool(node.body) and all(
            isinstance(stmt, (ast.Import, ast.ImportFrom)) for stmt in node.body
        )
        for handler in node.handlers:
            if body_is_imports and handler_is_importerror(handler):
                self._record(handler, "import_fallback")
            if handler_is_swallow(handler):
                self._record(handler, "try_except_swallow")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if isinstance(node.op, ast.Or) and is_fallback_literal(node.values[-1]):
            self._record(node, "or_default")
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        if is_none_check(node.test):
            self._record(node, "ternary_none_else")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and len(node.args) == 2
            and not is_none_constant(node.args[1])
        ):
            self._record(node, "dict_get_default")
        if isinstance(func, ast.Name) and func.id == "getattr" and len(node.args) == 3:
            self._record(node, "getattr_default")
        if is_known_fallback_helper(extract_callable_name(func)):
            self._record(node, "fallback_call")
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        if (
            is_none_check(node.test)
            and len(node.body) == 1
            and isinstance(node.body[0], (ast.Assign, ast.AugAssign))
        ):
            self._record(node, "if_none_assign")
        self.generic_visit(node)


def collect_fallback_comments(
    path: Path, source_lines: list[str], rel_path: str
) -> list[Finding]:
    findings: list[Finding] = []
    with path.open("rb") as fh:
        try:
            tokens = list(tokenize.tokenize(fh.readline))
        except tokenize.TokenError:
            return findings
    for tok in tokens:
        if tok.type != tokenize.COMMENT or "fallback" not in tok.string.lower():
            continue
        line_idx = tok.start[0] - 1
        snippet = source_lines[line_idx].strip() if 0 <= line_idx < len(source_lines) else ""
        findings.append(
            Finding(rel_path, tok.start[0], tok.start[1], "fallback_comment", snippet)
        )
    return findings


def display_path(path: Path) -> str:
    if path.is_relative_to(PROJECT_ROOT):
        return str(path.relative_to(PROJECT_ROOT))
    return str(path)


def scan_file(path: Path) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    rel = display_path(path)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []
    lines = source.splitlines()
    visitor = FallbackVisitor(lines, rel)
    visitor.visit(tree)
    return visitor.findings + collect_fallback_comments(path, lines, rel)


def iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        base = PROJECT_ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            files.append(path)
    return sorted(files)


def format_text_report(findings: list[Finding], file_count: int) -> str:
    grouped: dict[str, list[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.pattern, []).append(finding)
    out: list[str] = [
        "=" * 72,
        "  Fallback logic detection report",
        "=" * 72,
        f"Scanned {file_count} files under {', '.join(SCAN_ROOTS)}/",
        f"Found {len(findings)} occurrences across {len(grouped)} pattern categories:",
        "",
    ]
    for pattern in sorted(grouped):
        items = sorted(grouped[pattern], key=lambda f: (f.file, f.line))
        out.append(f"## {pattern} ({len(items)})")
        out.extend(f"  {f.file}:{f.line}:{f.col}  {f.snippet}" for f in items)
        out.append("")
    out.append(f"Total: {len(findings)} findings")
    return "\n".join(out)


def format_json_report(findings: list[Finding]) -> str:
    return json.dumps([asdict(f) for f in findings], ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan project for fallback patterns")
    parser.add_argument("--pattern", help="Filter output to a single pattern name")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a report")
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress output; use exit code only"
    )
    parser.add_argument(
        "--allow-findings",
        action="store_true",
        help="Return 0 even when findings exist (inventory mode)",
    )
    args = parser.parse_args(argv)

    files = iter_python_files()
    findings: list[Finding] = []
    for path in files:
        findings.extend(scan_file(path))
    if args.pattern:
        findings = [f for f in findings if f.pattern == args.pattern]

    if not args.quiet:
        if args.json:
            print(format_json_report(findings))
        else:
            print(format_text_report(findings, len(files)))

    return 0 if (args.allow_findings or not findings) else 1


if __name__ == "__main__":
    sys.exit(main())
