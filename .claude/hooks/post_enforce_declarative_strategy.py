#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write): strategies/*.py の宣言的フォーマットを強制.

検証ルール:
  - FILTERS がモジュールレベルに定義されていること
  - def screen() / sort_key() / columns() が定義されていないこと
"""

import ast
import json
import os
import sys

_FORBIDDEN_FNS: frozenset[str] = frozenset({"screen", "sort_key", "columns"})


def _is_strategy_file(file_path: str) -> bool:
    project_dir: str = os.environ.get("CLAUDE_PROJECT_DIR", "")
    strategies_dir: str = os.path.join(project_dir, "strategies")
    if not file_path.endswith(".py"):
        return False
    if os.path.basename(file_path).startswith("__"):
        return False
    try:
        return os.path.commonpath([file_path, strategies_dir]) == strategies_dir
    except ValueError as exc:
        print(f"path comparison error: {exc}", file=sys.stderr)
        return False


def _validate(source: str) -> list[str]:
    try:
        tree: ast.Module = ast.parse(source)
    except SyntaxError as exc:
        print(f"syntax error in source: {exc}", file=sys.stderr)
        return []

    errors: list[str] = []
    has_filters: bool = False

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "FILTERS":
                    has_filters = True
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "FILTERS":
                has_filters = True
        elif isinstance(node, ast.FunctionDef) and node.name in _FORBIDDEN_FNS:
            errors.append(
                f"L{node.lineno}: def {node.name}() は禁止。"
                "FILTERS/SORT/COLUMNS を使ってください"
            )

    if not has_filters:
        errors.insert(0, "FILTERS が定義されていません")

    return errors


def main() -> None:
    data: dict = json.load(sys.stdin)
    tool_input: dict[str, str] = data.get("tool_input", {})
    file_path: str = tool_input.get("file_path", "")

    if not _is_strategy_file(file_path):
        return

    if not os.path.isfile(file_path):
        return

    with open(file_path, encoding="utf-8") as f:
        source: str = f.read()

    errors: list[str] = _validate(source)
    if not errors:
        return

    detail: str = "\n".join(f"  • {e}" for e in errors)
    json.dump(
        {
            "decision": "stop",
            "reason": (
                f"strategies/ は宣言的フォーマット必須です:\n{detail}\n"
                "FILTERS リストを定義し、screen()/sort_key()/columns() は使わないでください。"
            ),
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
