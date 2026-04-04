# Architecture

日本株スクリーニングツール。IR BANK の財務データと yfinance の株価を SQLite に集約し、ユーザ定義の Python 戦略ファイルでフィルタリングする。

## ディレクトリ構成

```
formula_screening/
├── src/formula_screening/      # メインパッケージ
│   ├── __main__.py             # python -m formula_screening のエントリポイント
│   ├── cli.py                  # argparse によるサブコマンド定義・ディスパッチ
│   ├── config.py               # config/*.toml の読み込み、パス定数の定義
│   ├── log.py                  # ロギング設定 (stderr + RotatingFileHandler)
│   ├── fmt.py                  # 全角文字対応のテーブル整形ユーティリティ
│   ├── stealth.py              # プロキシ取得・検証・ローテーション、TLS指紋偽装
│   ├── cache_invalidation.py   # datasource ファイルのハッシュ比較によるキャッシュ管理
│   ├── screener.py             # 戦略ファイルの動的ロードとスクリーニング実行
│   ├── metrics.py              # 財務指標の計算 (PER, PBR, ネットキャッシュ比率 等)
│   ├── db/
│   │   ├── schema.py           # SQLite スキーマ定義・マイグレーション・接続管理
│   │   └── repository.py       # データアクセス層 (stocks, financial_items, prices)
│   └── datasources/
│       ├── irbank.py           # IR BANK JSON ファイルのインポート (PL/BS/CF/配当/四半期)
│       ├── irbank_bs.py        # IR BANK /bs ページのスクレイピング・パース
│       ├── irbank_forecast.py  # IR BANK /results ページから会社予想をスクレイピング
│       ├── irbank_common.py    # irbank_bs / irbank_forecast 共通の HTTP取得・ワーカー
│       └── yfinance_price.py   # yfinance による株価・発行済株式数の取得
├── scripts/                    # スタンドアロンスクリプト (uv run python scripts/... で実行)
│   ├── download_irbank.py      # IR BANK JSON ファイルのダウンロード
│   ├── scrape_irbank_bs.py     # BS スクレイピングのスクリプト版
│   ├── fetch_prices.py         # 株価取得のスクリプト版
│   └── export_csv.py           # 全銘柄の財務データ + 指標を CSV エクスポート
├── strategies/                 # スクリーニング戦略ファイル (screen(stock) -> bool)
│   ├── net_cash.py             # ネットキャッシュ比率戦略
│   └── net_cash_fcf.py         # ネットキャッシュ + FCFイールド + CROIC戦略
├── config/
│   ├── path.toml               # データディレクトリ・DB パス等
│   ├── magic_numbers.toml      # スクレイピング間隔、バッチサイズ等の定数
│   └── cli_defaults.toml       # CLIオプションのデフォルト値
├── data/
│   ├── irbank/                 # IR BANK JSON ファイル (年度コード別サブディレクトリ)
│   ├── screening.db            # SQLite データベース
│   ├── logs/                   # ローテーションログ
│   └── .scraper_hashes.json    # datasource ファイルのハッシュ (キャッシュ無効化用)
└── tests/
    ├── conftest.py
    ├── test_cache_invalidation.py
    ├── test_fmt.py
    ├── test_metrics.py
    ├── test_screener.py
    ├── test_irbank.py
    ├── test_irbank_bs.py
    ├── test_irbank_forecast.py
    └── test_repository.py
```

## データフロー

```
                          ┌──────────────────┐
                          │  IR BANK (Web)   │
                          └────────┬─────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              v                    v                    v
   download_irbank.py     irbank_bs.py         irbank_forecast.py
   (JSON ダウンロード)    (/bs スクレイピング)   (/results スクレイピング)
              │                    │                    │
              v                    │                    │
   data/irbank/*.json              │                    │
              │                    │                    │
              v                    v                    v
         irbank.py          irbank_common.py ◄─────────┘
     (JSON インポート)     (共通 HTTP/ワーカー)
              │                    │
              v                    v
        ┌─────────────────────────────────────┐
        │         screening.db (SQLite)       │
        │  ┌─────────┬────────────┬────────┐  │
        │  │ stocks  │ financial  │ prices │  │
        │  │         │  _items    │        │  │
        │  └─────────┴────────────┴────────┘  │
        └──────────────┬──────────────────────┘
                       │              ^
                       │              │
                       v              │
                  repository.py    yfinance_price.py
                 (データアクセス)   (株価取得)
                       │              ^
                       v              │
                  screener.py ────────┘
                 (スクリーニング実行)
                       │
           ┌───────────┼───────────┐
           v           v           v
      metrics.py   strategies/   fmt.py
    (指標計算)    (戦略ファイル)  (テーブル表示)
```

## モジュール依存関係

### エントリポイント

| モジュール       | 呼び出し先                                                         |
| :--------------- | :----------------------------------------------------------------- |
| `__main__.py`    | `cli.main()`                                                       |
| `cli.py`         | `config`, `db.schema`, `fmt`, `log`, `stealth`                     |
|                  | サブコマンド経由: `irbank`, `irbank_bs`, `irbank_forecast`         |
|                  | `yfinance_price`, `cache_invalidation`, `screener`, `repository`   |

### データ取得層 (`datasources/`)

| モジュール             | 依存先                                 | 役割                              |
| :--------------------- | :------------------------------------- | :-------------------------------- |
| `irbank.py`            | `repository`                           | JSON -> DB インポート             |
| `irbank_bs.py`         | `irbank_common`, `config`, `repository`| /bs ページのパース・行生成        |
| `irbank_forecast.py`   | `irbank_common`, `config`              | /results ページのパース・行生成   |
| `irbank_common.py`     | `config`, `stealth` (`create_session`), `repository`, `db.schema` | 共通 HTTP 取得 (TLS 偽装)・並列ワーカー |
| `yfinance_price.py`    | `config`, `repository`, `stealth`      | yfinance 経由の株価バッチ取得     |

### コア層

| モジュール               | 依存先                           | 役割                                |
| :----------------------- | :------------------------------- | :---------------------------------- |
| `screener.py`            | `config`, `repository`, `metrics`, `db.schema` | 戦略ファイルの動的ロード・全銘柄並列適用 |
| `metrics.py`             | (なし)                           | 財務データ + 株価 -> 派生指標の計算  |
| `cache_invalidation.py`  | `config`, `repository`, `db.schema`, `cli` | ハッシュ比較によるキャッシュ管理 |

### インフラ層

| モジュール      | 依存先     | 役割                                          |
| :-------------- | :--------- | :-------------------------------------------- |
| `config.py`     | (なし)     | TOML 読み込み、パス定数                       |
| `db/schema.py`  | `config`   | DDL、マイグレーション、接続生成               |
| `db/repository.py` | (なし)  | CRUD 操作 (stocks, financial_items, prices)   |
| `stealth.py`    | `config`   | プロキシプール、TLS 偽装、リクエスト遅延      |
| `log.py`        | `config`   | ロギング設定                                  |
| `fmt.py`        | (なし)     | 全角対応の文字列整形                          |

### スタンドアロンスクリプト (`scripts/`)

| スクリプト              | 使用モジュール                                                | 用途                     |
| :---------------------- | :------------------------------------------------------------ | :----------------------- |
| `download_irbank.py`    | `config`, `stealth.fetch_live_proxies`                        | JSON ダウンロード        |
| `scrape_irbank_bs.py`   | `cli.dispatch_scrape_workers`, `irbank_bs`, `repository`, `db.schema`, `stealth` | BS スクレイピング |
| `fetch_prices.py`       | `yfinance_price`, `repository`, `db.schema`                   | 株価取得                 |
| `export_csv.py`         | `config`, `db.schema`, `screener.build_stock_dict`            | CSV エクスポート         |

## データベーススキーマ

3 テーブル構成。すべて SQLite WAL モードで運用。

### stocks

銘柄マスタ。IR BANK JSON インポート時に自動登録、BS スクレイピング時に企業名を更新。

| カラム       | 型   | 備考               |
| :----------- | :--- | :----------------- |
| ticker       | TEXT | PK                 |
| edinet_code  | TEXT | UNIQUE (nullable)  |
| name         | TEXT | 企業名             |
| sector       | TEXT | セクター           |
| market       | TEXT | 市場               |
| updated_at   | TEXT | ISO8601 タイムスタンプ |

### financial_items

EAV (Entity-Attribute-Value) 形式の財務データ。`source` カラムでデータ出所を区別し、キャッシュ無効化の単位となる。

| カラム     | 型   | 備考                                        |
| :--------- | :--- | :------------------------------------------ |
| ticker     | TEXT | 銘柄コード                                  |
| period     | TEXT | 決算期 (例: `2025-03`)                      |
| statement  | TEXT | `pl`, `bs`, `cf`, `dividend`, `forecast`, `qy` |
| item_name  | TEXT | 項目名 (例: `revenue`, `net_cash`)          |
| value      | REAL | 値                                          |
| source     | TEXT | `irbank`, `irbank_bs`, `irbank_forecast`    |
| updated_at | TEXT | ISO8601 タイムスタンプ                      |

PK: `(ticker, period, statement, item_name)`

### prices

yfinance から取得した株価キャッシュ。`updated_at` が 1 日以上古い場合に再取得。

| カラム              | 型      | 備考               |
| :------------------ | :------ | :----------------- |
| ticker              | TEXT    | 銘柄コード         |
| date                | TEXT    | 日付               |
| close               | REAL    | 終値               |
| volume              | INTEGER | 出来高             |
| shares_outstanding  | INTEGER | 発行済株式数       |
| updated_at          | TEXT    | ISO8601 タイムスタンプ |

PK: `(ticker, date)`

## 設定ファイル

| ファイル               | 内容                                                     |
| :--------------------- | :------------------------------------------------------- |
| `config/path.toml`     | データディレクトリ、DB パス、ログディレクトリ等の相対パス |
| `config/magic_numbers.toml` | スクレイピング間隔・ワーカー数・バッチサイズ等の定数 |
| `config/cli_defaults.toml`  | CLI オプションのデフォルト値 (ダウンロード年数等)   |

すべて `config.py` が起動時に読み込み、`MAGIC`, `PATHS`, `CLI_DEFAULTS` として公開する。

## CLI サブコマンド

`uv run python -m formula_screening <command>` で実行。

| コマンド            | 処理内容                                            |
| :------------------ | :-------------------------------------------------- |
| `import-irbank`     | `data/irbank/` の JSON を DB にインポート           |
| `fetch-prices`      | yfinance で全銘柄の株価・発行済株式数を取得         |
| `scrape-bs`         | IR BANK /bs ページから詳細 BS データをスクレイピング |
| `scrape-forecast`   | IR BANK /results ページから会社予想をスクレイピング  |
| `refresh`           | datasource ハッシュ変更を検知し、キャッシュを再構築  |
| `screen`            | 戦略ファイルを適用してスクリーニング実行 (`--workers` で並列化、`--open [N]` で上位N件を四季報オンラインで開く) |

全コマンド実行前に `cache_invalidation.check_and_invalidate()` が自動実行され、datasource ファイルの変更があれば対応キャッシュが破棄される (`refresh` コマンド自身は除く)。

スクレイピング系コマンド (`scrape-bs`, `scrape-forecast`) および `screen` の自動データ取得では、`dispatch_scrape_workers` がワーカー数をプロキシプールのサイズ以下に制限する。これにより空プールへの分割（直接接続フォールバック）を防ぎ、全ワーカーがプロキシ経由で通信する。

## 戦略ファイルの仕組み

`strategies/` に配置した `.py` ファイルが戦略となる。`screener.py` が `importlib` で動的にロードし、全銘柄に対して `screen(stock: dict) -> bool` を呼び出す。

オプションで以下の関数を定義できる:

- `columns(stock: dict) -> list[tuple[str, str]]` — CLI の出力テーブル・CSV に戦略固有のカラムを追加。タプルは `(ヘッダー名, フォーマット済み値)` のペア。
- `sort_key(stock: dict) -> float` — 結果のソートキーを返す（降順）。未定義の場合は `net_cash_ratio` 降順。

`stock` dict の構造:

```python
{
    "ticker": str,
    "name": str,
    "price": float | None,
    "shares_outstanding": int | None,
    "pl": {"revenue": float, "net_income": float, ...},
    "bs": {"total_assets": float, "current_assets": float, ...},
    "cf": {"operating_cf": float, "free_cf": float, ...},
    "dividend": {"dps": float, ...},
    "forecast": {"basic_eps": float, ...},
    "metrics": {"per": float, "net_cash_ratio": float, ...},
    "cf_history": [("2025-03", {"operating_cf": float, ...}), ...],
}
```

## キャッシュ無効化の仕組み

`cache_invalidation.py` が `datasources/` 内の各ファイルの SHA256 ハッシュを `data/.scraper_hashes.json` に保存する。CLI 実行時にハッシュを再計算し、差分があれば対応する `financial_items.source` の行を DELETE して再取得する。

ファイルと DB source の対応:

| ファイル               | 無効化される source                  |
| :--------------------- | :----------------------------------- |
| `irbank.py`            | `irbank`                             |
| `irbank_bs.py`         | `irbank_bs`                          |
| `irbank_forecast.py`   | `irbank_forecast`                    |
| `irbank_common.py`     | `irbank_bs`, `irbank_forecast`       |
| `yfinance_price.py`    | `prices` テーブル全体                |
