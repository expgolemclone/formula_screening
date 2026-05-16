# Architecture

## 概要

`formula_screening` は、`stock_db` が保持する日本株データを読み出し、戦略ファイルで定義した条件に基づいて銘柄を抽出し、`stock_web_ui` に結果を渡してブラウザ表示する薄いアプリケーション層です。

処理の主経路は次のとおりです。

1. CLI が戦略ファイルと対象銘柄を受け取る
2. `stock_db` API で Stooq 株価の鮮度を確認し、JPX営業日ベースで古ければ更新する
3. スクリーナーが各銘柄の財務データと株価を読み込み、`stock` 辞書を構築する
4. `metrics.py` と `indicators/` が派生指標を計算する
5. 戦略が `screen(stock)` を評価し、通過銘柄を返す
6. `web.py` が `/api/screening` を配信し、`src_ts/app.ts` がテーブル表示する

## モジュール責務

### `src/formula_screening/cli.py`

- `screen` サブコマンドを提供する
- `--ticker` の単一値、複数値、範囲、`all`、`csv:path.csv` を解決する
- スクリーニング前に `stock_db.storage.prices.is_stooq_price_update_required()` で株価鮮度を確認し、必要なら `stock_db.sources.stooq.update_stooq_daily_prices()` を呼ぶ
- Stooq 更新失敗時は古い株価で続行せず、エラー終了する
- `run_screening()` を呼び出し、結果をソートして `serve_screening()` に渡す

### `src/formula_screening/screener.py`

- 戦略ファイルを `importlib` で動的ロードする
- 宣言的戦略の `FILTERS` / `SORT` / `COLUMNS` を実行可能な関数に変換する
- `stock_db` から EDINET XBRL 財務、四季報予想、価格、発行済株式数、履歴 CF / PL を読み出し、戦略評価用の `stock` 辞書を組み立てる
- 並列実行時はワーカーごとに DB 接続を開く

### `src/formula_screening/metrics.py`

- PL / BS / CF と現在株価から派生指標を計算する
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
- 外部利用向けに `compute_all_stock_metrics()` を公開する
- `save_screening_json(stocks, path)` でスクリーニング結果を静的 JSON ファイルとして保存する（GitHub Pages 用）

### `src/formula_screening/cli.py`

- `screen` サブコマンドはスクリーニング実行後、常に `docs/assets/screening.json`（GitHub Pages 用）を自動生成する
- `screen` サブコマンドは実行前に Stooq 株価の最新日付を確認する。最新価格日の翌日から実行日までに JPX 営業日が1日以上ある場合だけ Stooq 更新を実行する。JPX 休日定義は `stock_db` 側の `config/jpx_market_holidays.toml` を使う
- `--json <path>` オプションで追加の JSON 保存先を指定できる（Web サーバーを起動しない）
- `--json` 未指定時は従来どおり Web サーバーを起動する

### `src_ts/app.ts`

- `stock_web_ui` の `StockTable` ランタイムを読み込む
- ローカル時は `/api/screening`、GitHub Pages 時は `assets/screening.json` を fetch する
- 表示カラム、ソート、`peg_trailing_5` / `peg_blended_5y_actual_2f` を含む追加列、閾値色分けをここで定義する

## 戦略インターフェース

戦略ファイルは次のどちらかを満たす必要があります。

1. `FILTERS` を定義する
2. `screen(stock) -> bool` を定義する

宣言的戦略では、追加で以下を定義できます。

- `SORT`: 並び順に使う `metrics` キーまたは callable
- `COLUMNS`: 追加表示カラム定義
- `columns(stock)`: `COLUMNS` の代わりに直接カラム生成する関数

`FILTERS` は `(source, operator, threshold)` の配列です。

- `source`: `metrics` 内のキー名、または `stock` を受け取る callable
- `operator`: `>`, `>=`, `<`, `<=`, `between`
- `threshold`: 数値、または `between` 用の `(lo, hi)`

ロード後の戦略モジュールには必ず `screen(stock)` が生え、`columns` には共通リンク列が自動マージされます。

## `stock` データモデル

戦略に渡す辞書は、`build_stock_dict()` が構築します。

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

`screen_output.py` は共通リンク列として少なくとも次を追加します。

- `monex`
- `sikiho`

Web UI 側では会社名列に handbook 連携用の `yazi` リンクも使います。

## Web UI と API

`create_screening_api()` は、通過銘柄の `stock` 辞書をフロントエンド向け JSON に変換し、`/api/screening` で返します。返却形状は次のキーを中心に構成されます。

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
- `peg_blended_5y_actual_2f`

フロントエンド資産は次の分担です。

- `stock_web_ui.page.IndexPage`: ローカルサーバー起動時の HTML テンプレート入力
- `docs/index.html`: 静的配信用のページ骨格
- `docs/assets/app.js`: ビルド済みフロントエンド
- `src_ts/app.ts`: TypeScript ソース

`app.ts` は共通カラム定義を `stock_web_ui` の `columns.ts`（`globalThis.StockColumns`）から取得し、プロジェクト固有の `metrics` accessor と組み合わせてカラム配列を構築します。既定ソートは `net_cash_ratio` 降順です。ヘッダーは全て英語小文字に統一されています（`per_a`, `per_c`, `per_n`, `peg_5y`, `peg_5y2f`, `pbr`, `div%`, `equity%`, `fcf_10y%`, `croic%`）。PER、PBR、配当利回り、自己資本比率、FCF Yield、CROIC に閾値ベースの色付けを行います。共通閾値は `COMMON_THRESHOLDS` を利用し、`pbr` と `div%` のみプロジェクト固有で追加しています。

## 設定

設定値は `config/` 配下の TOML で管理します。

- `magic_numbers.toml`: `fcf_years`, `workers`, `peg_trailing_years`, `peg_blended_actual_years`
- `cli_defaults.toml`: CLI 既定値
- `path.toml`: データ・ログ系パス

現行コードでは、DB パス自体は `path.toml` ではなく `stock_db.paths.STOCKS_DB_PATH` を使用します。  
`path.toml` は `formula_screening` 自身の `data/` と `logs/` の管理に使われます。

## 補助モジュール

`validation.py` は、`stock_db` 内の検証対象銘柄と `source=edinet_xbrl` の BS データを使って、`net_cash_ratio` 検証用のスナップショットを作る補助モジュールです。現時点では CLI から直接呼ばれていませんが、テストで振る舞いが固定されています。
