# formula_screening

日本株の財務データに対して、Python で書いた条件式を使ってスクリーニングを行うツールです。  
データ取得と保存は `stock_db`、結果表示は `stock_web_ui` に委譲し、このリポジトリは「戦略の読み込み」「指標計算」「絞り込み」「Web UI への受け渡し」を担当します。

## 前提

- Python 3.13 以上
- `uv`
- このリポジトリと、依存先の `stock_db` / `stock_web_ui` が同じ親ディレクトリ配下にあること

`pyproject.toml` では次のようにローカル依存を参照しています。

- `../stock_db`
- `../stock_web_ui`

また、スクリーニング対象の DB は `formula_screening` 配下ではなく、`stock_db` 側の `STOCKS_DB_PATH` を使います。現行環境では `/home/exp/projects/stock_db/var/db/stocks.db` が参照先です。

## セットアップ

```bash
uv sync
```

`stock_db` 側で価格・財務データが投入済みであることを確認してください。  
BS / PL / CF / dividend は `source=edinet_xbrl`、四季報の今期・来期純利益予想は `source=shikiho` を参照します。
このリポジトリ単体では DB を作成しません。

## クイックスタート

同梱の戦略 `strategies/net_cash_fcf.py` を 1 銘柄に対して実行する例です。

```bash
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 1867
```

`1867` は現行 DB でこの戦略にヒットすることを確認済みの銘柄コードです。  
ヒット銘柄が 1 件以上あれば、スクリーニング結果を返すローカル Web サーバーが起動し、`/api/screening` を配信します。画面本体は `stock_web_ui` のテンプレートから生成され、アプリ固有の表示ロジックは `docs/assets/app.js` が担当します。

## CLI

実行入口:

```bash
uv run python -m formula_screening screen --strategy <strategy.py>
```

主要オプション:

- `--strategy`, `-s`: 実行する戦略ファイル
- `--ticker`, `-t`: 対象銘柄
- `--show-all`: 条件に通らなかった銘柄も含めて返す
- `--workers`: 並列スクリーニング数。既定値は `4`
- `--verbose`, `-v`: 詳細ログ
- `--quiet`, `-q`: ログ抑制

`--ticker` は次の形式を受け付けます。

- `7203`: 単一銘柄
- `7203 6758`: 複数銘柄
- `all`: DB 内の全銘柄
- `1000-2000`: 数値コードの範囲指定
- `csv:path.csv`: CSV 1 列目から銘柄コードを読み込む

例:

```bash
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 1867 7203
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t all --workers 8
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t csv:data/tickers.csv
```

## 戦略ファイルの書き方

戦略は Python ファイルとして読み込まれます。2 通りの書き方があります。

1. 宣言的に `FILTERS` を定義する
2. `screen(stock) -> bool` を自前で実装する

宣言的な戦略では、必要に応じて `SORT` と `COLUMNS` も定義できます。

```python
from formula_screening.indicators import croic, fcf_yield_avg, peg_5

FILTERS = [
    ("net_cash_ratio", ">=", -1.0),
    ("per", "between", (0, 10)),
    ("equity_ratio", ">", 50),
    (fcf_yield_avg, ">", 0),
]

SORT = "net_cash_ratio"

COLUMNS = [
    ("FCF_Y%", fcf_yield_avg, "{:.2%}"),
    ("CROIC%", croic, "{:.2%}"),
    ("peg_5", peg_5, "{:.2f}"),
]
```

`FILTERS` で使える比較演算子は `>`, `>=`, `<`, `<=`, `between` です。  
`source` には `metrics` キー名か、`stock` を受け取る callable を指定できます。

## `stock` 引数の形

戦略に渡される `stock` は概ね次の形です。

```python
{
    "ticker": "1867",
    "name": "植木組",
    "price": 0.0,
    "shares_outstanding": 0,
    "pl": {...},
    "bs": {...},
    "cf": {...},
    "dividend": {...},
    "forecast": {...},
    "metrics": {...},
    "cf_history": [("2025-03", {...}), ...],
    "pl_history": [("2025-03", {...}), ...],
}
```

`metrics` には少なくとも次のような派生指標が入ります。

- `market_cap`
- `per_actual`, `per`, `per_next`, `pbr`
- `dividend_yield`
- `equity_ratio`
- `free_cf`
- `interest_bearing_debt`
- `net_cash`
- `net_cash_ratio`

詳細な流れとモジュール分割は [ARCHITECTURE.md](./ARCHITECTURE.md) を参照してください。
