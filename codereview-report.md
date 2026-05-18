# Code Review Report

## Findings

### High: 空結果時に出力 JSON が更新されず、静的サイトと `--json` が古い結果を残す

- 根拠:
  - [`src/formula_screening/cli.py`](./src/formula_screening/cli.py#L117-L137) は `payload` が空だと `return` し、`docs/assets/screening.json` と `--json` 指定先の保存処理まで到達しない。
  - [`ARCHITECTURE.md`](./ARCHITECTURE.md#L119-L122) は `screen` 実行後に `docs/assets/screening.json` を常に生成すると説明している。
- 影響:
  - 実際には該当銘柄が 0 件でも、GitHub Pages は前回の銘柄一覧を表示し続ける。
  - `--json` を使う自動処理でも、0 件の正しい結果ではなく未更新ファイルまたは古いファイルを読む可能性がある。
- 改善方針:
  - 空配列でも `_GH_PAGES_JSON` と `args.json` へ必ず保存してからメッセージ表示へ進む。
  - `payload=[]` のときに両方の保存先へ `[]` が書かれる CLI テストを追加する。

### High: 欠損診断が「ログ用途」のはずなのに、非該当銘柄の不正値で全体実行を中断する

- 根拠:
  - [`ARCHITECTURE.md`](./ARCHITECTURE.md#L194-L195) は diagnostics をログ用途で、結果生成自体は継続すると定義している。
  - [`rust/src/lib.rs`](./rust/src/lib.rs#L158-L175) は payload 生成前に全対象銘柄へ `collect_missing_metric_diagnostics()` を実行する。
  - [`rust/src/lib.rs`](./rust/src/lib.rs#L201-L245) は diagnostics 内で `preferred_share_flag(stock)?` を呼び、`has_preferred_shares=2.0` のような不正値を即エラーにしている。
- 影響:
  - フィルタで落ちる銘柄に不正な優先株フラグが 1 件あるだけで、本来表示できるヒット銘柄まで返せなくなる。
  - diagnostics 追加前より通常 CLI の失敗条件が広がっており、設計説明とも一致しない。
- 改善方針:
  - diagnostics は失敗を伝播させず、`invalid_fields` のような診断へ落とすか、少なくとも payload 生成と切り離す。
  - 「非ヒット銘柄に不正 `has_preferred_shares` があってもヒット payload は返る」回帰テストを追加する。

### Medium: Rust 主経路が設定ファイルを無視し、指標期間をハードコードしている

- 根拠:
  - [`ARCHITECTURE.md`](./ARCHITECTURE.md#L211-L220) は `magic_numbers.toml` で `fcf_years` / `peg_trailing_years` / `peg_blended_actual_years` を管理すると説明している。
  - [`config/magic_numbers.toml`](./config/magic_numbers.toml) もその値を持つ。
  - Python 比較経路は [`src/formula_screening/screener.py`](./src/formula_screening/screener.py#L287-L292) で設定値を使っている。
  - 一方、通常運用の Rust 経路は [`rust/src/lib.rs`](./rust/src/lib.rs#L143-L198) と [`rust/src/lib.rs`](./rust/src/lib.rs#L248-L289) で `10` / `6` / `5` を直接埋め込んでいる。
- 影響:
  - 設定値を変えても、実際にユーザーが使う CLI と公開 API の結果は変わらない。
  - Python 比較経路との乖離が静かに広がり、将来の検証が誤った前提で通る。
- 改善方針:
  - Rust 側へ typed config を渡すか、期間値を戦略/CLI の明示入力に寄せて、主経路と比較経路で同じ source of truth を使う。
  - 設定値を変更した fixture で Rust-backed 経路の期間が追随する契約テストを追加する。

### Medium: Python 比較 API が渡された DB 接続を使わず、常にグローバル DB を読む

- 根拠:
  - [`src/formula_screening/screener.py`](./src/formula_screening/screener.py#L341-L392) の `run_screening(conn, ...)` は ticker と name だけを受け取った `conn` から読み出す。
  - 実データ読み込みは [`src/formula_screening/screener.py`](./src/formula_screening/screener.py#L310-L338) の `_screen_chunk()` で行い、ここでは常に `get_connection(STOCKS_DB_PATH)` を開いている。
- 影響:
  - 呼び出し側が一時 DB や検証用 DB を渡しても、財務値と価格だけは本番 DB から読み込まれる。
  - names/tickers と metrics の出所が分かれ、比較経路の検証結果を信用できない。
- 改善方針:
  - `run_screening()` から DB path または connection factory を `_screen_chunk()` へ明示的に渡す。
  - 一時 DB を渡したときにその DB だけが使われるテストを追加する。

## Verification

- `formula_screening`
  - `uv run pytest -q` -> 50 passed
  - `cargo test --manifest-path rust/Cargo.toml` -> 4 passed
  - `npx tsc --noEmit` -> passed
- `stock_db` 関連契約
  - `uv run pytest -q tests/storage/test_prices.py tests/test_market_calendar.py` -> 22 passed
  - `cargo test --manifest-path rust/Cargo.toml screening` -> 0 tests matched
- `stock_web_ui` 関連契約
  - `uv run pytest -q tests/test_serve.py tests/test_page.py tests/test_handler.py` -> 12 passed
  - `npm run typecheck` -> passed
  - `npm run test:ui` -> 10 passed

## Residual Risk

- `stock_db/rust/src/screening.rs` には直接の unit test がなく、`cargo test ... screening` でも該当テストは走らない。Rust-backed 主経路のデータ組み立ては、現状ほぼ `formula_screening` 側の E2E 契約テストに依存している。
