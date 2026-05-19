# Architecture

## 概要

`formula_screening` は、`stock_db` が保持する日本株データを読み出し、戦略ファイルで定義した条件に基づいて銘柄を抽出し、`stock_web_ui` に結果を渡してブラウザ表示する薄いアプリケーション層です。

`rust/` には現在のスクリーニング中核を置く。TOML 戦略の解釈、指標計算、スクリーニング、JSON payload 生成を Rust で実装し、DB 読み取りは `stock_db/rust::screening` を使う。通常の運用入口は `python -m formula_screening screen` で、Python CLI が Stooq 株価の鮮度確認と `stock_web_ui` 連携を担当し、Rust binding `formula_screening._core` が payload を生成する。Rust バイナリ単体でも実行できるが、Stooq 鮮度確認は Python CLI 側の責務である。

処理の主経路は次のとおりです。

1. Python CLI が TOML 戦略ファイルと対象銘柄を受け取る
2. `stock_db` API で Stooq 株価の鮮度を確認し、JPX営業日ベースで古ければ更新する
3. `formula_screening._core.run_screening_payload_py()` が `stock_db/rust::screening` から財務データ・株価・履歴を読み込む
4. Rust core が派生指標を計算し、TOML 戦略の条件を評価して JSON payload を返す
5. CLI が `docs/assets/screening.json` と任意の `--json` 出力を書き出す
6. `--json` 未指定時は `web.py` が `/api/screening` を配信し、`src_ts/app.ts` がテーブル表示する

## 前提

- Python 3.13 以上
- `uv`
- `../stock_db` と `../stock_web_ui` が同じ親ディレクトリ配下にあること
- `stock_db` 側に価格・財務データが投入済みであること
- Python package は `src/formula_screening` に置き、`pyproject.toml` の `tool.maturin.python-source = "src"` で editable install からも import できるようにする

## 実行

```bash
uv run python -m formula_screening screen \
  -s strategies/net_cash_fcf.toml -t 1867 --json /tmp/screening.json
```

Rust バイナリ単体でも同じスクリーニング core を実行できます。ただし、この経路は Stooq 株価の鮮度確認を行いません。

```bash
cargo run --manifest-path rust/Cargo.toml --bin formula-screening -- \
  screen -s strategies/net_cash_fcf.toml -t 1867 --json /tmp/screening.json
```

複数銘柄、全銘柄、範囲、CSV も指定できます。

```bash
uv run python -m formula_screening screen \
  -s strategies/net_cash_fcf.toml -t 1867 7203 --json /tmp/screening.json
uv run python -m formula_screening screen \
  -s strategies/net_cash_fcf.toml -t all --json /tmp/screening.json
uv run python -m formula_screening screen \
  -s strategies/net_cash_fcf.toml -t 1000-2000 --json /tmp/screening.json
printf "1867\n7203\n" > /tmp/formula_tickers.csv
uv run python -m formula_screening screen \
  -s strategies/net_cash_fcf.toml -t csv:/tmp/formula_tickers.csv --json /tmp/screening.json
```

静的配信用 JSON を更新する場合:

```bash
uv run python -m formula_screening screen \
  -s strategies/net_cash_fcf.toml -t all --json docs/assets/screening.json
```

この出力は DB と最新株価に依存するスナップショットであり、再生成時は通過銘柄数、並び順、各指標値の差分が `docs/assets/screening.json` に集約されます。

## モジュール責務

### `src/formula_screening/cli.py`

- 通常運用の `screen` サブコマンドを提供する Python facade
- `--ticker` の単一値、複数値、範囲、`all`、`csv:path.csv` を解決する
- スクリーニング前に `stock_db.storage.prices.is_stooq_price_update_required()` で株価鮮度を確認し、必要なら `stock_db.sources.stooq.run_stooq_price_update_command()` を呼ぶ
- Stooq 更新失敗時は古い株価で続行せず、エラー終了する
- `formula_screening._core.run_screening_payload_with_diagnostics_py()` を呼び出し、Rust が生成した payload を JSON 保存または Web 配信へ渡す
- 全スクリーニング対象銘柄について UI 上で `-` 表示になる公開項目を診断し、欠損がある銘柄は欠損フィールド一覧付きの `ERROR` ログを出す
- `--workers` は互換用に残っているが、現在の Rust-backed 経路では並列数の制御には使っていない

### `rust/`

- `formula-screening` バイナリが TOML 戦略を読み、`stock_db` の Rust screening API から財務データを取得する
- `lib.rs` が metrics / indicators / preferred-share 判定 / JSON payload / 公開項目の欠損診断を担当する
- PyO3 モジュール `formula_screening._core` が `compute_all_stock_metrics()` を公開し、下流 Python repo からも Rust 実装を利用する
- `main.rs` が Rust 単体実行用の `screen` サブコマンド、静的 JSON 保存、`stock_web_ui_core::serve()` 連携を担当する
- Rust 単体実行は Stooq 鮮度確認を行わず、server host/port なども `main.rs` 内の固定値を使う。`--json` 未指定時は共有 Rust サーバーが既存の待受ポートを解放してから起動し、既定ブラウザを開く

### `src/formula_screening/screener.py`

- Python 側の比較用実装として TOML 戦略ファイルを読み込む
- TOML 戦略の `filters` / `sort` / `columns` を実行可能な関数に変換する
- `stock_db` から EDINET XBRL 財務、四季報予想、価格、発行済株式数、履歴 CF / PL を読み出し、戦略評価用の `stock` 辞書を組み立てる
- 並列実行時はワーカーごとに DB 接続を開く

### `src/formula_screening/metrics.py`

- Python 比較経路で PL / BS / CF と現在株価から派生指標を計算する。通常の CLI と `compute_all_stock_metrics()` は同等ロジックの Rust 実装を使う
- `market_cap`, `per_actual`, `per`, `per_next`, `pbr`, `dividend_yield`, `equity_ratio`, `free_cf`, `interest_bearing_debt`, `net_cash`, `net_cash_ratio` などを `metrics` に詰める
- `interest_bearing_debt` は `short_term_debt + long_term_debt` で計算し、欠損項目は0として扱う（XBRLに概念が存在しない=債務ゼロ）
- `per_actual` は `market_cap / pl.net_income`、`per` は `market_cap / forecast.net_income_current`（四季報今期予想純利益）、`per_next` は `market_cap / forecast.net_income_next`（四季報来期予想純利益）。純利益予想の単一ソースは `japan_company_handbook`（`stock_db` の `source=shikiho`）
- BS / PL / CF の単一ソースは `stock_db` の `source=edinet_xbrl`、dividend の単一ソースは `source=shikiho`（四季報）
- `net_cash` は次の式で求める

```text
current_assets - inventories + investment_securities * 0.7
- current_liabilities - non_current_liabilities
```

### `src/formula_screening/indicators/`

- `fcf.py`: 過去 N 期の平均 FCF Yield を計算する。既定の N は `config/magic_numbers.toml` の `fcf_years = 10`。各期の FCF を現在の時価総額で割る。ライブスクリーニング向けであり、バックテスト用途には先読みバイアスがある。上場年数が N 年未満で有効期間数が不足する銘柄では警告ログを出力し `None` を返す（スクリプト全体は継続する）。
- `croic.py`: `free_cf / (stockholders_equity + interest_bearing_debt)` を計算する。`interest_bearing_debt` は `metrics.py` が `short_term_debt + long_term_debt` から導出する。これらのBS項目は `stock_db` の XBRL パーサーが JPPFS（`ShortTermLoansPayable` / `LongTermLoansPayable` 等）と IFRS（`BorrowingsNCLIFRS` / `BondsAndBorrowingsCLIFRS` 等）の両概念名を候補としてパースする。
- `peg.py`: Trailing PEG（`peg_trailing`）と独自ブレンドPEG（`peg_blended_2f`）を計算する。いずれもEPSベース（`stock_db` の `compute_eps` で計算済み）。
  - `peg_trailing(stock, years)`: 過去 `years` 期間の実績EPS CAGRを使い、`per_actual / CAGR%` を返す。5年CAGRには6データポイントが必要（`years+1`）。
  - `peg_blended_2f(stock, actual_years)`: 過去 `actual_years` 期間の実績EPS + 今期予想EPS + 来期予想EPS の独自ブレンドCAGRを使い、`per_next / CAGR%` を返す。標準Forward PEGではない。

### `src/formula_screening/web.py`

- `/api/screening` を返す API ルートを作る
- `stock_web_ui` の `serve()` に `docs/assets`、`IndexPage`、API ルートを渡す
- handbook 参照用に `../japan_company_handbook/data` を `yazi_base_dir` として渡す
- 外部利用向けの `compute_all_stock_metrics()` は Rust binding `formula_screening._core` を呼び、`has_preferred_shares` も返す
- Python `stock` 辞書向けの `create_screening_api()` / `save_screening_json()` と、Rust payload 向けの `create_screening_payload_api()` / `save_screening_payload_json()` を持つ

### CLI 出力挙動

- `screen` サブコマンドはスクリーニング実行後、常に `docs/assets/screening.json`（GitHub Pages 用）を自動生成する
- `screen` サブコマンドは実行前に Stooq 株価の最新日付を確認する。最新価格日の翌日から実行日までに JPX 営業日が1日以上ある場合だけ、`stock_db.sources.stooq.run_stooq_price_update_command(db_path=STOCKS_DB_PATH)` 経由で Stooq 更新を実行する。JPX 休日定義は `stock_db` 側の `config/jpx_market_holidays.toml` を使う
- `--json <path>` オプションで追加の JSON 保存先を指定できる（Web サーバーを起動しない）
- `--json` 未指定時は従来どおり Web サーバーを起動する

### `src_ts/app.ts`

- `stock_web_ui` の `StockTable` ランタイムを読み込む
- ローカル時は `/api/screening`、GitHub Pages 時は `assets/screening.json` を fetch する
- 表示カラム、ソート、`peg_trailing_5` / `peg_blended_5y_actual_2f` を含む追加列、閾値色分けをここで定義する

## 戦略インターフェース

戦略ファイルは TOML で定義します。具体例は
[`strategies/net_cash_fcf.toml`](./strategies/net_cash_fcf.toml) を参照してください。

- `required_sources`: 戦略が前提とするデータソース名。現在は strategy metadata として保持し、runtime のデータ存在チェックには使っていない
- `sort`: 並び順に使う登録済み指標キー
- `[[filters]]`: `source`, `operator`, `threshold`
- `[[columns]]`: `header`, `source`, `format`

`source` は登録済み指標キーだけを受け付けます。Python callable は使いません。
`operator` は `>`, `>=`, `<`, `<=`, `between` を使えます。
`between` の `threshold` は `[lo, hi]` です。Python 比較経路ではロード後の戦略に
`screen(stock)` / `columns(stock)` が組み立てられ、`columns` には共通リンク列が自動マージされます。Rust-backed CLI の Web/API payload は現在固定形状で、TOML の `columns` は validation 対象ですが表示列の生成には使っていません。

## `stock` データモデル

Python 比較経路で戦略に渡す辞書は、`build_stock_dict()` が構築します。Rust-backed 経路では `stock_db_core::screening::ScreeningStock` から Rust 側の `Stock` を構築します。

```python
{
    "ticker": str,
    "name": str,
    "price": float | None,
    "shares_outstanding": int | None,
    "pl": dict[str, float | None],
    "bs": dict[str, float | None],
    "cf": dict[str, float | None],
    "dividend": dict[str, float | None],
    "forecast": dict[str, float | None],
    "metrics": dict[str, float | None],
    "cf_history": list[tuple[str, dict[str, float | None]]],
    "pl_history": list[tuple[str, dict[str, float | None]]],
}
```

Python 比較経路の `screen_output.py` は共通リンク列として少なくとも次を追加します。

- `monex`
- `sikiho`

Web UI 側では会社名列に handbook 連携用の `yazi` リンクも使います。

## Web UI と API

Python 比較経路では `create_screening_api()` が通過銘柄の `stock` 辞書をフロントエンド向け JSON に変換する。通常の CLI 経路では Rust core が同じ形状の payload を作り、`create_screening_payload_api()` が `/api/screening` で返します。返却形状は次のキーを中心に構成されます。

- `code`
- `name`
- `price`
- `metrics.net_cash_ratio`
- `metrics.per_actual`
- `metrics.per`
- `metrics.per_next`
- `metrics.pbr`
- `metrics.dividend_yield`
- `metrics.equity_ratio`
- `metrics.market_cap`
- `fcf_yield_avg`
- `croic`
- `peg_trailing_5`
- `peg_trailing_5_status`
- `peg_blended_5y_actual_2f`
- `peg_blended_5y_actual_2f_status`
- `has_preferred_shares`

下流プロジェクト向けには `formula_screening.web.run_screening_strategy_payload(strategy_path, tickers=None, return_all=False)`
を公開する。この関数は Rust-backed な `run_screening_payload_py()` を呼び、TOML戦略の通過銘柄 payload を返す。
下流側はこの payload を自分のドメインデータへ合流し、`formula_screening` 側には下流固有データへの依存を追加しない。

PyO3 の互換 API として `run_screening_payload_py()` は従来どおり payload 配列だけを返します。通常 CLI は `run_screening_payload_with_diagnostics_py()` を使い、`payload` に加えて `diagnostics` を受け取ります。`diagnostics` は全スクリーニング対象銘柄を対象に、公開 payload で `None` になり UI 上 `-` 表示になる項目を `code`, `name`, `missing_fields` で返します。欠損診断はログ用途であり、結果生成自体は継続します。

フロントエンド資産は次の分担です。

- `stock_web_ui.page.IndexPage`: ローカルサーバー起動時の HTML テンプレート入力
- `docs/index.html`: 静的配信用のページ骨格
- `docs/assets/app.js`: ビルド済みフロントエンド
- `src_ts/app.ts`: TypeScript ソース

`app.ts` は共通カラム定義を `stock_web_ui` の `columns.ts`（`globalThis.StockColumns`）から取得し、プロジェクト固有の `metrics` accessor と組み合わせてカラム配列を構築します。既定ソートは `net_cash_ratio` 降順です。ヘッダーは全て英語小文字に統一されています（`per_a`, `per_c`, `per_n`, `peg_5y`, `peg_5y2f`, `pbr`, `div%`, `pref`, `equity%`, `fcf_10y%`, `croic%`）。PEG 列は status が `non_positive_growth` のとき `neg`、それ以外の未算出は `-` を表示します。PER、PBR、配当利回り、自己資本比率、FCF Yield、CROIC に閾値ベースの色付けを行います。共通閾値は `COMMON_THRESHOLDS` を利用し、`pbr` と `div%` のみプロジェクト固有で追加しています。

## 移行漏れ検知

Rust-backed 経路への移行漏れは、`tests/test_rust_migration_contract.py` の一時 SQLite DB E2E で検知します。このテストは `formula_screening._core.run_screening_payload_py()` を実行し、旧 Python 経路で UI/API に必要だった payload キー、派生指標、`return_all` の挙動が欠落していないことを固定します。

XBRL タグから canonical financial item への取り込み漏れは `stock_db` 側の責務です。`stock_db/rust/src/financials.rs` の unit test は、`main` 時点の BS / PL / CF / dividend / shares / forecast 候補タグ一覧を静的スナップショットとして持ち、現在実装がそれらを最低条件として包含していることを確認します。テスト実行時に `main` ブランチを読み取らず、7203 型の IFRS 負債タグなど追加タグは許容します。DB 投入前の具体的な parse 回帰は `../stock_db/tests/sources/test_xbrl_financials_parser.py` で固定します。

## 設定

設定値は `config/` 配下の TOML で管理します。

- `magic_numbers.toml`: `fcf_years`, `workers`, `peg_trailing_years`, `peg_blended_actual_years`
- `cli_defaults.toml`: CLI 既定値
- `path.toml`: データ・ログ系パス

現行コードでは、DB パス自体は `path.toml` ではなく `stock_db.paths.STOCKS_DB_PATH` を使用します。  
`path.toml` は `formula_screening` 自身の `data/` と `logs/` の管理に使われます。

## 補助モジュール

`validation.py` は、`stock_db` 内の検証対象銘柄と `source=edinet_xbrl` の BS データを使って、`net_cash_ratio` 検証用のスナップショットを作る補助モジュールです。現時点では CLI から直接呼ばれていませんが、テストで振る舞いが固定されています。
