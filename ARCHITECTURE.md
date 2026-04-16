# Architecture

日本株スクリーニングツール。ユーザ定義の Python 戦略ファイルでフィルタリングを行う。財務データ・株価データは隣接プロジェクト `../stock_db` (`stock_db.paths`) の SQLite DB から取得する。スクレイピング機能は `stock_db` プロジェクトに移管済み。

## ディレクトリ構成

```
formula_screening/
├── src/formula_screening/      # メインパッケージ
│   ├── __main__.py             # python -m formula_screening のエントリポイント
│   ├── cli.py                  # argparse によるCLI定義 (screenサブコマンドのみ)
│   ├── config.py               # config/*.toml の読み込み、パス定数の定義
│   ├── log.py                  # ロギング設定 (stderr + RotatingFileHandler)
│   ├── fmt.py                  # 全角文字対応のテーブル整形ユーティリティ
│   ├── screener.py             # 戦略ファイルの動的ロードとスクリーニング実行
│   ├── metrics.py              # 財務指標の計算 (PER, PBR, ネットキャッシュ比率, 配当利回り 等)
│   ├── screen_output.py        # スクリーニング結果の出力フォーマット
│   ├── indicators/
│   │   ├── __init__.py         # 共有指標関数の re-export
│   │   ├── fcf.py              # 平均FCFイールド (fcf_yield_avg)
│   │   └── croic.py            # CROIC (Cash Return on Invested Capital)
│   └── db/
│       ├── schema.py           # SQLite 接続管理 (stock_db.STOCKS_DB_PATH を使用)
│       └── repository.py       # データアクセス層 (stocks, financial_items, prices)
├── strategies/                 # スクリーニング戦略ファイル
│   ├── net_cash.py             # ネットキャッシュ比率 (net_cash / 時価総額) > 1.0 戦略
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
                             │ repository.py
                             v
                    ┌─────────────────┐
                    │  screener.py    │
                    │                 │
                    │ - load_strategy()│
                    │ - build_stock_dict()│
                    │ - run_screening()│
                    └────────┬────────┘
                             │
                             v
                    ┌─────────────────┐
                    │  strategy.py    │
                    │  (user-defined) │
                    │                 │
                    │ - FILTERS       │
                    │ - SORT          │
                    │ - COLUMNS       │
                    └────────┬────────┘
                             │
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
SORT: Callable[[dict], float] = fcf_yield_avg

# 追加カラム（オプション）
COLUMNS: list[tuple] = [
    ("FCF_Y%", fcf_yield_avg, "{:.2%}"),
    ("CROIC%", croic, "{:.2%}"),
]
```

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

def columns(stock: dict) -> list[tuple[str, str]]:
    return [("custom", "value")]
```

## stock_db との連携

- **DBパス**: `stock_db.paths.STOCKS_DB_PATH` (デフォルト: `var/db/stocks.db`)
- **依存関係**: `pyproject.toml` で `stock-db` をローカルパス参照
- **テーブル構造**:
  - `stocks`: 銘柄情報（ticker, name, sector, market, shares_outstanding）
  - `financial_items`: 財務データ（PL/BS/CF/forecast のEAVモデル）
  - `prices`: 株価データ（ticker, date, close, volume）

## CLI 使用例

```bash
# 基本的なスクリーニング
uv run python -m formula_screening screen -s strategies/net_cash.py

# 結果をCSVに出力
uv run python -m formula_screening screen -s strategies/net_cash.py -o result.csv

# 上位5件を四季報オンラインで開く
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py --open 5

# ワーカー数を指定（デフォルト: 4）
uv run python -m formula_screening screen -s strategies/net_cash.py --workers 8
```
