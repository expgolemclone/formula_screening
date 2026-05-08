# formula-screening

日本株を、Pythonで書いた戦略ファイルに沿ってスクリーニングするツールです。

財務データと株価データは隣接プロジェクト `../stock_db` のSQLite DBから読み取り、結果は `../stock_web_ui` のWeb UIでブラウザ表示します。このリポジトリはスクリーニングロジック、指標計算、戦略ファイル、Web UIへの橋渡しに集中しています。

## できること

- Pythonモジュールとして書いた戦略を動的に読み込み、銘柄ごとにフィルタリングする
- PER、PBR、自己資本比率、ネットキャッシュ比率、平均FCFイールド、CROICなどの指標を計算する
- 銘柄コード、範囲、CSV、全銘柄指定でスクリーニング対象を切り替える
- 通過銘柄だけでなく、`--show-all` で非通過銘柄も含めて確認する
- 結果をローカルWeb UIで表示し、Monex、四季報オンライン、会社四季報PDFへのリンクを付ける

詳しい内部設計は [ARCHITECTURE.md](ARCHITECTURE.md) を参照してください。

## 前提

- Python `>= 3.13`
- `uv`
- 財務・株価データを持つ `../stock_db`
- Web UIを提供する `../stock_web_ui`

`pyproject.toml` では `stock-db` と `stock-web-ui` をローカルパス依存として参照しています。初回セットアップ前に、同じ親ディレクトリ配下へ次のように配置してください。

```text
projects/
├── formula_screening/
├── stock_db/
└── stock_web_ui/
```

Nix環境を使う場合は `flake.nix` のdev shellから `uv` などを利用できます。

## セットアップ

```bash
uv sync --extra dev
```

データベースの作成、更新、スクレイピングは `stock_db` 側の責務です。スクリーニング実行前に、`stock_db` から参照されるDBに銘柄情報、財務データ、株価データが入っている状態にしてください。

## 使い方

同梱のネットキャッシュ + FCFイールド戦略を実行します。

```bash
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py
```

実行後、条件に合致した銘柄数が表示され、ローカルWeb UIが起動します。

対象銘柄を絞る場合:

```bash
# 単一銘柄
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 7203

# 複数銘柄
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 7203 6758 9984

# DB内の銘柄コード範囲
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 7200-7210

# CSVの1列目から銘柄コードを読み込む
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t csv:tickers.csv

# フィルタ非通過銘柄も含めて表示
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 7203 6758 --show-all
```

戦略ファイルは直接実行することもできます。

```bash
uv run python strategies/net_cash_fcf.py -t 7203 --workers 8
```

## 同梱戦略

`strategies/net_cash_fcf.py` は、清原達郎式のネットキャッシュ比率とFCFイールドを軸にしたサンプル戦略です。

通過条件:

- `net_cash_ratio >= -1.0`
- `0 < per < 10`
- `equity_ratio > 50`
- 過去N年の平均FCFイールドがプラス

N年の値やワーカー数は `config/magic_numbers.toml` で調整できます。

## 戦略ファイルの書き方

推奨形式は、モジュール変数で条件を宣言する形式です。

```python
from formula_screening.indicators import croic, fcf_yield_avg

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
]
```

`FILTERS` の左辺には、`stock["metrics"]` のキー名、または `stock` を受け取る関数を指定できます。独自の処理が必要な場合は、`screen(stock)`、`sort_key(stock)`、`columns(stock)` を定義する関数ベース形式も使えます。

`stock` には主に次の値が入ります。

- `ticker`, `name`, `price`, `shares_outstanding`
- `pl`, `bs`, `cf`, `dividend`, `forecast`
- `metrics`
- `cf_history`

## 主なディレクトリ

```text
src/formula_screening/     # CLI、スクリーニングエンジン、指標計算、Web連携
strategies/                # ユーザ定義のスクリーニング戦略
config/                    # ワーカー数、FCF年数、ログ出力先などの設定
docs/                      # Web UI用の静的ファイル
tests/                     # pytestテスト
```

## テスト

```bash
uv run pytest
```

フロントエンドTypeScriptを確認する場合:

```bash
npm install
npm exec -- tsc --noEmit
```

## トラブルシュート

- `stock-db` または `stock-web-ui` が見つからない場合は、隣接プロジェクトの配置と `uv sync --extra dev` の結果を確認してください。
- スクリーニング結果が0件の場合は、DBに対象銘柄の財務データ、株価、発行済株式数が揃っているか確認してください。
- Web UIは `stock_web_ui` のサーバー設定を利用します。ポートやホストを変えたい場合は `stock_web_ui` 側の設定も確認してください。
