from __future__ import annotations

from pathlib import Path


def test_runtime_code_uses_stock_db_public_api() -> None:
    root = Path(__file__).resolve().parent.parent
    banned = (
        "STOCKS_DB_PATH",
        "stock_db.paths",
        "stock_db.storage",
        "sqlite3.connect",
        "var/db/stocks.db",
        "../stock_db/var/db/stocks.db",
        "financial_items",
    )
    checked_files = [
        *sorted((root / "src").rglob("*.py")),
        *sorted((root / "rust" / "src").rglob("*.rs")),
    ]

    violations: list[str] = []
    for path in checked_files:
        text = path.read_text(encoding="utf-8")
        for token in banned:
            if token in text:
                violations.append(f"{path.relative_to(root)}: {token}")

    assert violations == []
