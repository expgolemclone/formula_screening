## RULES

## 1. fall backは問題が発覚しづらくなるから禁止.

- どうしても実装すべきだと思う場合はuserの許可を取ること.

## 2. `../stock_web_ui/`, `../stock_db/` の公開境界を呼び出して利用すること.

- 機能分離すること.
- `../stock_db/` は Rust crate `edinet-xbrl` と `edinet-xbrl downstream-*` JSON CLI を api とすること.
- `stock_db.api` は使わないこと.
- github pages以外での静的json依存は避けること.
