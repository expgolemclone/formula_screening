# Architecture

## 概要

`formula_screening` は、`stock_db` が保持する日本株データを読み出し、戦略ファイルで定義した条件に基づいて銘柄を抽出し、`stock_web_ui` に結果を渡してブラウザ表示する薄いアプリケーション層です。

処理の主経路は次のとおりです。

1. CLI が戦略ファイルと対象銘柄を受け取る
2. スクリーナーが各銘柄の財務データと株価を読み込み、`stock` 辞書を構築する
3. `metrics.py` と `indicators/` が派生指標を計算する
4. 戦略が `screen(stock)` を評価し、通過銘柄を返す
5. `web.py` が `/api/screening` を配信し、`src_ts/app.ts` がテーブル表示する

## モジュール責務

### `src/formula_screening/cli.py`

- `screen` サブコマンドを提供する
- `--ticker` の単一値、複数値、範囲、`all`、`csv:path.csv` を解決する
- `run_screening()` を呼び出し、結果をソートして `serve_screening()` に渡す

### `src/formula_screening/screener.py`

- 戦略ファイルを `importlib` で動的ロードする
- 宣言的戦略の `FILTERS` / `SORT` / `COLUMNS` を実行可能な関数に変換する
- `stock_db` から EDINET XBRL 財務、四季報予想、価格、発行済株式数、履歴 CF / PL を読み出し、戦略評価用の `stock` 辞書を組み立てる
- 各 CF 期間に対応する過去株価を `prices` テーブルから取得し `price_at_period` に格納する
- 並列実行時はワーカーごとに DB 接続を開く

### `src/formula_screening/metrics.py`

- PL / BS / CF と現在株価から派生指標を計算する
- `market_cap`, `per_actual`, `per`, `per_next`, `pbr`, `dividend_yield`, `equity_ratio`, `free_cf`, `interest_bearing_debt`, `net_cash`, `net_cash_ratio` などを `metrics` に詰める
- `per_actual` は `market_cap / pl.net_income`、`per` は `market_cap / forecast.net_income_current`（四季報今期予想純利益）、`per_next` は `market_cap / forecast.net_income_next`（四季報来期予想純利益）。純利益予想の単一ソースは `japan_company_handbook`（`stock_db` の `source=shikiho`）
- BS / PL / CF / dividend の単一ソースは `stock_db` の `source=edinet_xbrl`
- `net_cash` は次の式で求める

```text
current_assets - inventories + investment_securities * 0.7
- current_liabilities - non_current_liabilities
```

### `src/formula_screening/indicators/`

- `fcf.py`: 過去 N 期の平均 FCF Yield を計算する。既定の N は `config/magic_numbers.toml` の `fcf_years = 10`。各期の FCF をその期の時価総額（`price_at_period[period] × shares_outstanding`）で割ることで先読みバイアスを回避する。価格が取得できない期は計算から除外する。
- `croic.py`: `free_cf / (stockholders_equity + interest_bearing_debt)` を計算する
- `peg.py`: Trailing PEG（`peg_trailing`）と独自ブレンドPEG（`peg_blended_2f`）を計算する。いずれもEPSベース（`stock_db` の `compute_eps` で計算済み）。
  - `peg_trailing(stock, years)`: 過去 `years` 期間の実績EPS CAGRを使い、`per_actual / CAGR%` を返す。5年CAGRには6データポイントが必要（`years+1`）。
  - `peg_blended_2f(stock, actual_years)`: 過去 `actual_years` 期間の実績EPS + 今期予想EPS + 来期予想EPS の独自ブレンドCAGRを使い、`per_next / CAGR%` を返す。標準Forward PEGではない。

### `src/formula_screening/web.py`

- `/api/screening` を返す API ルートを作る
- `stock_web_ui` の `serve()` に `docs/assets`、`IndexPage`、API ルートを渡す
- handbook 参照用に `../japan_company_handbook/data` を `yazi_base_dir` として渡す
- 外部利用向けに `compute_all_stock_metrics()` を公開する

### `src_ts/app.ts`

- `stock_web_ui` の `StockTable` ランタイムを読み込む
- `/api/screening` を fetch し、単一テーブルとして描画する
- 表示カラム、ソート、`peg_5` を含む追加列、閾値色分けをここで定義する

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
    "price_at_period": dict[str, float | None],
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

`app.ts` は既定ソートを `net_cash_ratio` 降順に設定し、実績PERを `per_a`、今期予想PERを `per_c`、来期予想PERを `per_n` として表示します。PEGカラムは `PEG実績5年`（Trailing PEG）と `PEG5年+2F`（独自ブレンドPEG）の2種類を表示します。これらのPER、PBR、配当利回り、自己資本比率、FCF Yield、CROIC に閾値ベースの色付けを行います。

## 設定

設定値は `config/` 配下の TOML で管理します。

- `magic_numbers.toml`: `fcf_years`, `workers`, `peg_trailing_years`, `peg_blended_actual_years`
- `cli_defaults.toml`: CLI 既定値
- `path.toml`: データ・ログ系パス

現行コードでは、DB パス自体は `path.toml` ではなく `stock_db.paths.STOCKS_DB_PATH` を使用します。  
`path.toml` は `formula_screening` 自身の `data/` と `logs/` の管理に使われます。

## 補助モジュール

`validation.py` は、`stock_db` 内の検証対象銘柄と `source=edinet_xbrl` の BS データを使って、`net_cash_ratio` 検証用のスナップショットを作る補助モジュールです。現時点では CLI から直接呼ばれていませんが、テストで振る舞いが固定されています。
