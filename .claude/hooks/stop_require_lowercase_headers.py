#!/usr/bin/env python3
"""Stop hook: Block if src_ts/app.ts has non-lowercase-ASCII header values.

Column headers must match ^[a-z0-9_%]+$ to prevent Japanese text or
uppercase letters from creeping into the table headings.

Suppress with ``// noqa: header-case`` on the offending line.
"""

import os
import re
import sys

_HEADER_RE = re.compile(r'''header:\s*["']([^"']+)["']''')
_VALID_HEADER = re.compile(r"^[a-z0-9_%]+$")


def main() -> None:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    app_ts = os.path.join(project_dir, "src_ts", "app.ts")
    if not os.path.isfile(app_ts):
        return

    with open(app_ts) as f:
        lines = f.readlines()

    violations: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        if "# noqa: header-case" in line:
            continue
        for m in _HEADER_RE.finditer(line):
            header = m.group(1)
            if not _VALID_HEADER.match(header):
                violations.append((i, header))

    if not violations:
        return

    print(
        "src_ts/app.ts に header 値が小文字ASCIIではありません。\n"
        "header は ^[a-z0-9_%]+$ に一致する必要があります。\n"
        "(例外は行末に // noqa: header-case を付けてください)\n",
        file=sys.stderr,
    )
    for lineno, header in violations:
        print(f"  src_ts/app.ts:{lineno} — header: \"{header}\"", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
