# Architecture

日本株スクリーニングツール。ユーザ定義の Python 戦略ファイルでフィルタリングを行う。財務データ・株価データは隣接プロジェクト `../stock_db` (`stock_db.paths`) の SQLite DB から取得する。スクレイピング機能は `stock_db` プロジェクトに移管済み。

## ディレクトリ構成

```
formula_screening/
├── src/formula_screening/      # メインパッケージ
│   ├── __main__.py             # python -m formula_screening のエントリポイント
│   ├── cli.py                  # argparse によるCLI定義 (screenサブコマンド) + --ticker 複数銘柄対応 (nargs="+") + マルチフォーマット解決 (all/range/csv) + --show-all + OSC 8 ハイパーリンク描画
│   ├── config.py               # config/*.toml の読み込み、パス定数の定義
│   ├── log.py                  # ロギング設定 (stderr + RotatingFileHandler)
│   ├── fmt.py                  # 全角文字対応のテーブル整形ユーティリティ
│   ├── screener.py             # 戦略ファイルの動的ロードとスクリーニング実行 (tickers / return_all パラメータ対応)
│   ├── metrics.py              # 財務指標の計算 (PER, PBR, ネットキャッシュ比率, 配当利回り 等)
│   ├── net_cash.py             # ネットキャッシュ・ネットキャッシュ比率の計算 (compute_net_cash_metrics)
│   ├── validation.py           # 検証用ヘルパー (対象選定・XBRL BS読込・ネットキャッシュ指標計算)
│   ├── screen_output.py        # 共有カラムヘルパー (LinkCell, 外部サイトURL生成, カラムマージ)
│   ├── indicators/
│   │   ├── __init__.py         # 共有指標関数の re-export
│   │   ├── fcf.py              # 平均FCFイールド (fcf_yield_avg)
│   │   └── croic.py            # CROIC (Cash Return on Invested Capital)
│   └── db/
│       ├── schema.py           # SQLite 接続管理 (stock_db.STOCKS_DB_PATH を使用)
│       └── repository.py       # データアクセス層 (stocks, financial_items, prices)
├── tests/                      # テストスイート
│   ├── test_net_cash.py        # compute_net_cash_metrics のテスト
│   └── test_validation.py      # validation.py のヘルパー関数テスト
├── data/
│   └── logs/                   # RotatingFileHandler のログ出力先
├── strategies/                 # スクリーニング戦略ファイル
│   └── net_cash_fcf.py         # ネットキャッシュ + 平均FCFイールド戦略
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
           │ repository.py                      │ validation.py
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
  │   CLI Output    │
  │  (table/CSV)    │
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

# ソートキー（オプション）
SORT: Callable[[dict], float | None] = fcf_yield_avg

# 追加カラム（オプション）
COLUMNS: list[tuple] = [
    ("FCF_Y%", fcf_yield_avg, "{:.2%}"),
    ("CROIC%", croic, "{:.2%}"),
]
```

すべての戦略に対し、`screener.py` が monex・四季報オンラインへのリンクカラムを自動付与する（`screen_output.build_common_link_columns`）。戦略側で同名ヘッダを定義した場合はそちらが優先される。`cli._print_table` は `LinkCell` を OSC 8 ハイパーリンクとして描画する（対応ターミナルのみ: kitty, iTerm2, WezTerm, VSCode 等）。

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

## stock_db との連携

- **DBパス**: `stock_db.paths.STOCKS_DB_PATH` (デフォルト: `var/db/stocks.db`)
- **依存関係**: `pyproject.toml` で `stock-db` をローカルパス参照
- **テーブル構造**:
  - `stocks`: 銘柄情報（ticker, edinet_code, name, sector, market, shares_outstanding, shares_updated_at, securities_report_url, updated_at）
  - `financial_items`: 財務データ（PL/BS/CF/dividend/ss/forecast のEAVモデル）
  - `prices`: 株価データ（ticker, date, close, volume, updated_at）

## CLI 使用例

```bash
# 基本的なスクリーニング
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py

# 結果をCSVに出力
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -o result.csv

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

# 上位5件を四季報オンラインで開く
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py --open 5

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
uv run python strategies/net_cash_fcf.py --open 5
```
