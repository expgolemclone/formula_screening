#!/usr/bin/env python3
"""Stop hook: Block if cli.py has add_argument(default=<numeric literal>).

Numeric defaults must come from MAGIC[...] or CLI_DEFAULTS[...] so that
changing config/magic_numbers.toml or config/cli_defaults.toml is the
single source of truth.

Suppress with ``# noqa: literal-default`` on the offending line.
"""

import ast
import os
import sys


def _find_violations(source: str, lines: list[str]) -> list[tuple[int, object]]:
    """Return (lineno, value) for each add_argument default=<int|float>."""
    tree = ast.parse(source)
    violations: list[tuple[int, object]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            if not isinstance(kw.value, ast.Constant):
                continue
            if not isinstance(kw.value.value, (int, float)):
                continue
            # Check for noqa comment on the same line
            line = lines[kw.value.lineno - 1]
            if "# noqa: literal-default" in line:
                continue
            violations.append((kw.value.lineno, kw.value.value))
    return violations


def main() -> None:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    cli_path = os.path.join(
        project_dir, "src", "formula_screening", "cli.py"
    )
    if not os.path.isfile(cli_path):
        return

    with open(cli_path) as f:
        source = f.read()
    lines = source.splitlines()

    violations = _find_violations(source, lines)
    if not violations:
        return

    print(
        "cli.py に add_argument(default=<数値リテラル>) があります。\n"
        "MAGIC[...] または CLI_DEFAULTS[...] を使ってください。\n"
        "(例外は行末に # noqa: literal-default を付けてください)\n",
        file=sys.stderr,
    )
    for lineno, value in violations:
        print(f"  cli.py:{lineno} — default={value}", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
