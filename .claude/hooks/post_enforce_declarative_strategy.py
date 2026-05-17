#!/usr/bin/env python3
"""PostToolUse hook (Edit|Write): strategies/*.toml の宣言的フォーマットを強制.

検証ルール:
  - filters が1件以上あること
  - filter / column が source を持つこと
"""

import json
import os
import sys
import tomllib


def _is_strategy_file(file_path: str) -> bool:
    project_dir: str = os.environ.get("CLAUDE_PROJECT_DIR", "")
    strategies_dir: str = os.path.join(project_dir, "strategies")
    if not file_path.endswith(".toml"):
        return False
    if os.path.basename(file_path).startswith("__"):
        return False
    try:
        return os.path.commonpath([file_path, strategies_dir]) == strategies_dir
    except ValueError as exc:
        print(f"path comparison error: {exc}", file=sys.stderr)
        return False


def _validate(source: bytes) -> list[str]:
    try:
        data = tomllib.loads(source.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return [f"TOML parse error: {exc}"]

    errors: list[str] = []
    filters = data.get("filters")
    if not isinstance(filters, list) or not filters:
        errors.append("filters が定義されていません")
    else:
        for index, item in enumerate(filters):
            if not isinstance(item, dict) or "source" not in item:
                errors.append(f"filters[{index}] に source がありません")

    columns = data.get("columns", [])
    if isinstance(columns, list):
        for index, item in enumerate(columns):
            if not isinstance(item, dict) or "source" not in item:
                errors.append(f"columns[{index}] に source がありません")

    return errors


def main() -> None:
    data: dict = json.load(sys.stdin)
    tool_input: dict[str, str] = data.get("tool_input", {})
    file_path: str = tool_input.get("file_path", "")

    if not _is_strategy_file(file_path):
        return

    if not os.path.isfile(file_path):
        return

    with open(file_path, "rb") as f:
        source: bytes = f.read()

    errors: list[str] = _validate(source)
    if not errors:
        return

    detail: str = "\n".join(f"  • {e}" for e in errors)
    json.dump(
        {
            "decision": "stop",
            "reason": (
                f"strategies/ は宣言的フォーマット必須です:\n{detail}\n"
                "TOML の filters / columns で定義してください。"
            ),
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
