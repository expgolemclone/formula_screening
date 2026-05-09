# Architecture

日本株スクリーニングツール。ユーザ定義の Python 戦略ファイルでフィルタリングを行う。財務データ・株価データは隣接プロジェクト `../stock_db` (`stock_db.paths`) の SQLite DB から取得する。スクレイピング機能は `stock_db` プロジェクトに移管済み。スクリーニング結果は隣接プロジェクト `../stock_web_ui` (`stock_web_ui`) の Web UI でブラウザ表示する（ターミナル出力は廃止）。

## ディレクトリ構成

```
formula_screening/
├── src/formula_screening/      # メインパッケージ
│   ├── __main__.py             # python -m formula_screening のエントリポイント
│   ├── cli.py                  # argparse によるCLI定義 (screenサブコマンド) + --ticker 複数銘柄対応 (nargs="+") + マルチフォーマット解決 (all/range/csv) + --show-all
│   ├── config.py               # config/*.toml の読み込み、パス定数の定義 (DATA_DIR, LOG_DIR)
│   ├── log.py                  # ロギング設定 (stderr + RotatingFileHandler)
│   ├── screener.py             # 戦略ファイルの動的ロードとスクリーニング実行 (tickers / return_all パラメータ対応)
│   ├── metrics.py              # 財務指標の計算 (PER, PBR, ネットキャッシュ比率, 配当利回り 等)
│   ├── net_cash.py             # ネットキャッシュ・ネットキャッシュ比率の計算 (compute_net_cash_metrics)
│   ├── validation.py           # 検証用ヘルパー (対象選定・XBRL BS読込・ネットキャッシュ指標計算) — stock_db.storage API 経由
│   ├── screen_output.py        # 共有カラムヘルパー (LinkCell, 外部サイトURL生成, カラムマージ)
│   ├── web.py                  # Web UI 統合 (stock_web_ui へのブリッジ, /api/screening で JSON 配信)
│   ├── indicators/
│   │   ├── __init__.py         # 共有指標関数の re-export
│   │   ├── fcf.py              # 平均FCFイールド (fcf_yield_avg)
│   │   └── croic.py            # CROIC (Cash Return on Invested Capital)
├── docs/                       # Web UI 静的ファイル
│   ├── index.html              # stock_web_ui テンプレートから生成した HTML
│   └── assets/
│       └── app.js              # formula_screening 用テーブル設定 (フラットモード)
├── tests/                      # テストスイート
│   ├── test_net_cash.py        # compute_net_cash_metrics のテスト
│   ├── test_validation.py      # validation.py のヘルパー関数テスト
│   └── test_net_cash_fcf_strategy.py # net_cash_fcf 戦略の境界条件テスト
├── data/
│   └── logs/                   # RotatingFileHandler のログ出力先
├── strategies/                 # スクリーニング戦略ファイル
│   └── net_cash_fcf.py         # net_cash_ratio >= -1.0 のネットキャッシュ + 平均FCFイールド戦略
└── config/
    ├── path.toml               # データディレクトリ・DB パス等
    ├── magic_numbers.toml      # スクリーニング設定 (fcf_years, workers)
    └── cli_defaults.toml       # CLIオプションのデフォルト値
```

## データフロー

```
                    ┌─────────────────┐
                    │   stock_db      │
                    │ (SQLite DB)     │
                    │                 │
                    │ - stocks        │
                    │ - prices        │
                    │ - financial_items│
                    └────────┬────────┘
                             │
           ┌─────────────────┴──────────────────┐
           │ stock_db.storage.*                 │ validation.py
           │ (直接参照)                          │ (stock_db.storage API)
           v                                    v
  ┌─────────────────┐              ┌──────────────────────┐
  │  screener.py    │              │ select_validation_   │
  │                 │              │   targets()          │
  │ - load_strategy()│              └──────────┬───────────┘
  │ - build_stock_  │                         │
  │   dict()        │              ┌──────────v───────────┐
  │ - screen_single()│              │ load_latest_bs()     │
  │ - run_screening()│              │ (XBRL BS読込)        │
  └────────┬────────┘              └──────────┬───────────┘
           │                                  │
           v                       ┌──────────v───────────┐
  ┌─────────────────┐              │ net_cash.py           │
  │  strategy.py    │              │ compute_net_cash_     │
  │  (user-defined) │              │   metrics()           │
  │                 │              └──────────┬───────────┘
  │ - FILTERS       │                         │
  │ - SORT          │              ┌──────────v───────────┐
  │ - COLUMNS       │              │ build_net_cash_      │
  └────────┬────────┘              │   snapshot()         │
           │                       └──────────────────────┘
           v
  ┌─────────────────┐
  │    web.py       │
  │ /api/screening  │
  │  (JSON配信)     │
  └────────┬────────┘
           │
           v
  ┌─────────────────┐
  │  stock_web_ui   │
  │ (ブラウザ表示)   │
  └─────────────────┘
```

## 戦略ファイルフォーマット

戦略ファイルはPythonモジュールとして動的にロードされ、以下の属性を定義できます：

### 宣言的フィルタ形式（推奨）

```python
"""戦略の説明文"""

REQUIRED_SOURCES: list[str] = ["irbank", "prices"]  # 必要なデータソース

# フィルタ定義: (フィルタ対象, 演算子, 閾値)
FILTERS: list[tuple] = [
    ("net_cash_ratio", ">", 1.0),           # ネットキャッシュ比率 > 1.0
    ("per", "between", (0, 10)),            # 0 < PER < 10
    ("equity_ratio", ">", 50),              # 自己資本比率 > 50%
    (fcf_yield_avg, ">", 0),                # カスタム関数も可
]

# ソートキー（オプション）— 文字列キー または 呼び出し可能関数
SORT: str | Callable[[dict], float | None] = "net_cash_ratio"

# 追加カラム（オプション）
COLUMNS: list[tuple] = [
    ("FCF_Y%", fcf_yield_avg, "{:.2%}"),
    ("CROIC%", croic, "{:.2%}"),
]
```

すべての戦略に対し、`screener.py` が monex・四季報オンラインへのリンクカラムを自動付与する（`screen_output.build_common_link_columns`）。戦略側で同名ヘッダを定義した場合はそちらが優先される。

### 同梱戦略 `net_cash_fcf.py`

`strategies/net_cash_fcf.py` は次の条件を満たす銘柄を通す。Web UI 上の表示名は `ncr` だが、戦略条件・ソートキー・JSON の内部キーは `net_cash_ratio` を使う。

- `net_cash_ratio >= -1.0`
- `0 < per < 10`
- `equity_ratio > 50`
- `fcf_yield_avg > 0`

### 関数ベース形式

```python
def screen(stock: dict) -> bool:
    """stock は build_stock_dict() で構築された辞書"""
    m = stock["metrics"]
    return (
        m.get("net_cash_ratio", 0) > 1.0
        and 0 < m.get("per", 999) < 10
    )

def sort_key(stock: dict) -> float:
    return stock["metrics"].get("net_cash_ratio") or 0

def columns(stock: dict) -> list[tuple[str, str | LinkCell]]:
    return [("custom", "value")]
```

## stock_web_ui との連携

- **依存関係**: `pyproject.toml` で `stock-web-ui` をローカルパス参照
- **Web UI**: `web.py` がスクリーニング結果を JSON に変換し、`stock_web_ui.page.IndexPage` と `stock_web_ui.serve.serve()` で HTTP サーバーを起動
- **API**: `/api/screening` は `stock_web_ui.handler.json_route()` で組み立てる
- **静的資産**: ローカルサーバーは `docs/assets/` を優先し、不足する共有資産は `stock_web_ui.ASSETS_DIR` から配信する
- **フロントエンド**: `docs/assets/app.js` がカラム定義・閾値・ソート設定を注入し、ブラウザでは先に読み込まれた共有 `StockTable` API を使う。`code` は `stockLink: "monex"`、`name` は `stockLink: "yazi"`、`price` は `stockLink: "shikiho"` を使う。`name` はローカルで yazi、静的環境では非リンクになる。ネットキャッシュ比率列の表示ヘッダは `ncr`、内部指標キーは `metrics.net_cash_ratio`
- **共有ファイル**: `index.html` は `python -m stock_web_ui.render_index --shared-asset-base-url https://expgolemclone.github.io/stock_web_ui/assets ...` で生成し、`stock-table.js` / `style.css` は `stock_web_ui` GitHub Pages を直接参照する
- **ブラウザ**: サーバー起動時に `xdg-open` で自動表示する。銘柄コードは Monex 財務ページ、会社名は `stock_web_ui` の `/open-yazi/{code}` 経由で `japan_company_handbook/data/{YYYY_Q}/{code}.pdf` を yazi で開き、株価列は四季報オンラインを `stock_web_ui` の `/open` 経由で開く

## stock_db との連携

- **DBパス**: `stock_db.paths.STOCKS_DB_PATH` (デフォルト: `var/db/stocks.db`)
- **依存関係**: `pyproject.toml` で `stock-db` をローカルパス参照
- **API参照**: 各モジュールは `stock_db.storage.*` の公開APIを直接参照する:
  - `stock_db.storage.connection.get_connection()`
  - `stock_db.storage.stocks.get_all_tickers()`, `get_stock_names()`
  - `stock_db.storage.financials.get_financial_dict()`, `get_historical_items()`
  - `stock_db.storage.prices.get_latest_price_with_shares()`
- **API参照 (validation.py)**: 生SQLを直書きせず、`stock_db.storage.*` の公開APIを経由する:
  - `validation.select_validation_targets()` → `stock_db.storage.stocks.get_validation_targets()` + ValidationTarget変換
  - `validation.load_latest_bs()` → `stock_db.storage.financials.get_items_by_source()` + Python側でperiod/statement判定
- **テーブル構造**:
  - `stocks`: 銘柄情報（ticker, edinet_code, name, sector, market, shares_outstanding, shares_updated_at, securities_report_url, updated_at）
  - `financial_items`: 財務データ（PL/BS/CF/dividend/ss/forecast のEAVモデル）
  - `prices`: 株価データ（ticker, date, close, volume, updated_at）

## CLI 使用例

```bash
# 基本的なスクリーニング（ブラウザで表示）
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py

# 単一銘柄のスクリーニング
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 7203

# 複数銘柄のスクリーニング（スペース区切りで指定）
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 7203 6758 9984

# 全銘柄をスクリーニング（--ticker省略と等価）
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t all

# 範囲指定でスクリーニング（DB内の7200〜7210銘柄のみ）
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 7200-7210

# CSVファイルから銘柄一覧を指定してスクリーニング
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t csv:tickers.csv

# フィルタ非通過銘柄も含めて全銘柄を表示
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 7203 6758 --show-all

# ワーカー数を指定（デフォルト: 4）
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py --workers 8
```

### 戦略ファイルを直接実行

各戦略ファイルは `__main__` ブロックを持ち、`screen` サブコマンドのショートカットとして直接実行できる。追加引数はそのまま `screen` に渡る。パス区切りはフォワードスラッシュで Windows bash / PowerShell / Linux / macOS 共通に動作する。

```bash
# 上記 CLI 例と等価
uv run python strategies/net_cash_fcf.py -t 7203
uv run python strategies/net_cash_fcf.py --workers 8
```
