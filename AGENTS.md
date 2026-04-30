## rules

1. fall backは問題が発覚しづらくなるから禁止.
   どうしても実装すべきだと思う場合はuserの許可を取ること.

## verification

- 2026-04-30: 棚卸資産 (`inventories`) のDB格納検証は `xbrl_bs` を対象にすること。このrepoの検証コードは `xbrl_bs` を読む。
- テスト:
  `uv run pytest tests/test_validation.py ../stock_db/tests/sources/test_xbrl_bs_parser.py`
- E2E確認:
  一時DBを `STOCK_DB_VAR_DIR=$(mktemp -d)` で分離し、`sec_reports` にXBRL fixtureへの `xbrl_path` を登録してから `uv run python -m stock_db.cli.parse_xbrl_bs --ticker 5280` を実行すること。
- 確認値:
  `financial_items` に `('2025-03', 'bs', 'inventories', 32974467000.0, 'xbrl_bs')`、
  `('2024-03', 'bs', 'inventories', 28448283000.0, 'xbrl_bs')`、
  `('2023-03', 'bs', 'inventories', 0.0, 'xbrl_bs')` が入ることを確認済み。
- `irbank_bs` は今回の検証対象外。
