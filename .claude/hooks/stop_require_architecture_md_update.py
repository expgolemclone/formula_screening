#!/usr/bin/env python3
"""Stop hook: Block if code files changed but ARCHITECTURE.md was not updated.

Uses a hash state file to track changes across sessions.
"""

import hashlib
import json
import os
import sys

CODE_EXTENSIONS = {".py"}
CODE_DIRS = {"src", "scripts", "strategies"}


def _find_code_files(project_dir: str) -> list[str]:
    """Collect code files (sorted) relative to project_dir."""
    result = []
    for entry in sorted(os.listdir(project_dir)):
        full = os.path.join(project_dir, entry)
        if os.path.isfile(full) and os.path.splitext(entry)[1] in CODE_EXTENSIONS:
            result.append(entry)
    for d in sorted(CODE_DIRS):
        dir_path = os.path.join(project_dir, d)
        if not os.path.isdir(dir_path):
            continue
        for root, _dirs, files in os.walk(dir_path):
            for f in sorted(files):
                if os.path.splitext(f)[1] in CODE_EXTENSIONS:
                    rel = os.path.relpath(os.path.join(root, f), project_dir)
                    result.append(rel)
    return sorted(set(result))


def _hash_files(project_dir: str, paths: list[str]) -> str:
    """SHA256 digest of concatenated file contents."""
    h = hashlib.sha256()
    for p in paths:
        full = os.path.join(project_dir, p)
        if os.path.isfile(full):
            h.update(p.encode())
            with open(full, "rb") as f:
                h.update(f.read())
    return h.hexdigest()


def _hash_file(path: str) -> str:
    """SHA256 of a single file."""
    if not os.path.isfile(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def main() -> None:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )

    state_file = os.path.join(project_dir, ".claude", ".arch_check_state.json")
    arch_path = os.path.join(project_dir, "ARCHITECTURE.md")

    # Load previous state
    prev = {"code_hash": "", "arch_hash": ""}
    if os.path.isfile(state_file):
        try:
            with open(state_file) as f:
                prev = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"state file read error: {exc}", file=sys.stderr)

    # Compute current hashes
    code_files = _find_code_files(project_dir)
    code_hash = _hash_files(project_dir, code_files)
    arch_hash = _hash_file(arch_path)

    code_changed = code_hash != prev.get("code_hash", "")
    arch_changed = arch_hash != prev.get("arch_hash", "")

    if not code_changed:
        # No code change -- update state and pass
        _save_state(state_file, code_hash, arch_hash)
        return

    if arch_changed:
        # Both changed -- in sync, update state and pass
        _save_state(state_file, code_hash, arch_hash)
        return

    # Code changed but ARCHITECTURE.md did not -- block
    print(
        "ARCHITECTURE.md が更新されていません。\n"
        "コードファイルのハッシュが前回から変化しています。\n"
        "ARCHITECTURE.md の行数・シンボル・依存グラフを確認し更新してください。",
        file=sys.stderr,
    )
    sys.exit(2)


def _save_state(state_file: str, code_hash: str, arch_hash: str) -> None:
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump({"code_hash": code_hash, "arch_hash": arch_hash}, f)


if __name__ == "__main__":
    main()
