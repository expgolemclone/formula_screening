"""Stock list management: load from file or fetch from external sources."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from formula_screening.db.repository import upsert_stock

logger = logging.getLogger("formula_screening.stocklist")


def load_manual_stocklist(conn: sqlite3.Connection, path: Path) -> int:
    """Load stock tickers from a text file (one ticker per line).

    Lines starting with # are comments. Each line can be:
    - Just a ticker: "7203"
    - Ticker and name separated by comma: "7203,トヨタ自動車"
    """
    count = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(",", 1)
            ticker = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else ""
            upsert_stock(conn, ticker, name, "", "")
            count += 1
    conn.commit()
    logger.info("Loaded %d stocks from %s", count, path)
    return count


def fetch_edinetdb_companies(conn: sqlite3.Connection) -> int:
    """Fetch stock list from EDINET DB API.

    Requires EDINETDB_API_KEY environment variable.
    """
    raise NotImplementedError(
        "EDINET DB API integration not yet implemented. "
        "Use --file option to load a manual stock list instead."
    )
