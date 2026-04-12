# Architecture

日本株スクリーニングツール。IR BANK の財務データと Stooq の株価を SQLite に集約し、ユーザ定義の Python 戦略ファイルでフィルタリングする。IR BANK へのスクレイピングは Node.js puppeteer-real-browser サービス経由で行う。Stooq の日次テキストファイルはローカル配置済みファイルを優先し、なければブラウザ経由で自動ダウンロードする。

## ディレクトリ構成

```
formula_screening/
├── src/formula_screening/      # メインパッケージ
│   ├── __main__.py             # python -m formula_screening のエントリポイント
│   ├── cli.py                  # argparse によるサブコマンド定義・ディスパッチ
│   ├── config.py               # config/*.toml の読み込み、パス定数の定義
│   ├── log.py                  # ロギング設定 (stderr + RotatingFileHandler)
│   ├── fmt.py                  # 全角文字対応のテーブル整形ユーティリティ
│   ├── stealth.py              # プロキシ取得・検証・ローテーション
│   ├── browser.py              # Node.js puppeteer-real-browser サービスのクライアント
│   ├── worker.py               # スクレイピング・株価取得の並列ワーカー制御
│   ├── bootstrap.py            # empty DB からの自動ブートストラップ
│   ├── screener.py             # 戦略ファイルの動的ロードとスクリーニング実行
│   ├── metrics.py              # 財務指標の計算 (PER, PBR, ネットキャッシュ比率 等)
│   ├── indicators/
│   │   ├── __init__.py         # 共有指標関数の re-export
│   │   ├── fcf.py              # 平均FCFイールド (fcf_yield_avg)
│   │   └── croic.py            # CROIC (Cash Return on Invested Capital)
│   ├── db/
│   │   ├── schema.py           # SQLite スキーマ定義・マイグレーション・接続管理
│   │   └── repository.py       # データアクセス層 (stocks, financial_items, prices)
│   └── scrape/
│       ├── http_fetch.py       # ブラウザ経由HTML取得の共通リトライ・プロキシローテーション
│       ├── irbank.py           # IR BANK JSON ファイルのインポート (PL/BS/CF/配当/四半期)
│       ├── irbank_bs.py        # IR BANK /bs ページのパース・行生成
│       ├── irbank_forecast.py  # IR BANK /results ページのパース・行生成
│       ├── irbank_common.py    # IR BANK URL ビルダー (http_fetch に委譲)
│       ├── kabutan_shares.py   # kabutan 発行済株式数スクレイピング (plain HTTPS)
│       └── stooq_price.py      # Stooq 日次テキストファイルによる株価取得
├── scripts/                    # スタンドアロンスクリプト (uv run python scripts/... で実行)
│   ├── download_irbank.py      # IR BANK JSON ファイルのダウンロード
│   ├── scrape_irbank_bs.py     # BS スクレイピングのスクリプト版
│   ├── fetch_prices.py         # 株価取得のスクリプト版
│   ├── fetch_shares.py         # 発行済株式数取得のスクリプト版
│   ├── export_csv.py           # 全銘柄の財務データ + 指標を CSV エクスポート
│   └── generate_check_sites.py # Tranco リストからプロキシ検証用サイトを生成
├── strategies/                 # スクリーニング戦略ファイル (screen(stock) -> bool)
│   ├── net_cash.py             # ネットキャッシュ比率 (net_cash / 時価総額) > 1.0 戦略
│   └── net_cash_fcf.py         # ネットキャッシュ + 平均FCFイールド戦略 (CROIC は表示カラム)
├── config/
│   ├── path.toml               # データディレクトリ・DB パス等
│   ├── magic_numbers.toml      # スクレイピング間隔、バッチサイズ等の定数
│   ├── cli_defaults.toml       # CLIオプションのデフォルト値
│   └── validation_sites.txt    # プロキシ品質検証用ドメインリスト (Tranco由来)
├── data/
│   ├── irbank/                 # IR BANK JSON ファイル (年度コード別サブディレクトリ)
│   ├── stooq/                  # Stooq 日次テキストファイル (YYYYMMDD_d.txt)
│   ├── screening.db            # SQLite データベース
│   ├── logs/                   # ローテーションログ
│   └── .proxy_failures.json    # 検証失敗プロキシの reason 付きキャッシュ (TTL付き)
└── tests/
    ├── conftest.py
    ├── test_bootstrap.py
    ├── test_browser.py
    ├── test_cli.py
    ├── test_fmt.py
    ├── test_indicators.py
    ├── test_irbank.py
    ├── test_irbank_bs.py
    ├── test_irbank_forecast.py
    ├── test_metrics.py
    ├── test_proxy_runtime.py
    ├── test_repository.py
    ├── test_scan_fallbacks.py      # scan_fallbacks_core (hook) の単体テスト
    ├── test_kabutan_shares.py
    ├── test_screener.py
    ├── test_stealth.py
    ├── test_stooq_price.py
    ├── test_worker.py
    └── fixtures/
        ├── kabutan_8046.html       # kabutan 発行済株式数パーサのテスト用 HTML スナップショット
        └── kabutan_7203.html
```

fallback パターン検出は `~/.claude/hooks/scan_fallbacks_core.py` (汎用 AST スキャナ) + `config/scan_fallbacks.toml` (プロジェクト固有設定) に分離されている。`post-scan-fallbacks.py` hook が Edit/Write 時に自動検出し、fallback パターンがあれば停止を指示する。スタンドアロン実行: `python3 ~/.claude/hooks/scan_fallbacks_core.py .` (`--allow-findings` でインベントリモード)。

## データフロー

```
                          ┌──────────────────┐     ┌─────────────────┐
                          │  IR BANK (Web)   │     │ kabutan (Web)   │
                          └────────┬─────────┘     └───────┬─────────┘
                                   │                       │
              ┌────────────────────┼────────────────────┐  │
              v                    v                    v  v
   download_irbank.py     irbank_bs.py         irbank_forecast.py
   (JSON ダウンロード)    (/bs パース)          (/results パース)
              │                    │                    │
              v                    │                    │  kabutan_shares.py
   data/irbank/*.json              │                    │  (発行済株式数取得)
              │                    │                    │       │
              v                    v                    v       │
         irbank.py          irbank_common.py ◄─────────┘       │
     (JSON インポート)       (IR BANK URL)                      │
              │                    │                            │
              │                    v                            │
              │              http_fetch.py ──► browser.py       │
              │             (共通リトライ)   (Node.js経由)      │
              │                    │                            │
              │                    v                            │
              │              worker.py ◄────────────────────────┘
              │             (並列ワーカー制御)
              │                    │
              v                    v
        ┌─────────────────────────────────────┐
        │         screening.db (SQLite)       │
        │  ┌─────────┬────────────┬────────┐  │
        │  │ stocks  │ financial  │ prices │  │
        │  │ (shares)│  _items    │        │  │
        │  └─────────┴────────────┴────────┘  │
        └──────────────┬──────────────────────┘
                       │              ^
                       │              │
                       v              │
                  repository.py    stooq_price.py ──► browser.py
                 (データアクセス)   (日次txt株価取得)     (CAPTCHA突破)
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
| `cli.py`         | `config`, `db.schema`, `fmt`, `log`, `stealth`, `browser`, `worker` |
|                  | サブコマンド経由: `irbank`, `irbank_bs`, `irbank_forecast`          |
|                  | `stooq_price`, `bootstrap`, `screener`, `repository`                |

### データ取得層 (`scrape/`)

| モジュール           | 依存先                           | 役割                                             |
| :------------------- | :------------------------------- | :----------------------------------------------- |
| `http_fetch.py`      | `config`, `browser`, `stealth`   | BrowserService 経由 HTML 取得の共通リトライループ |
| `irbank.py`          | `repository`                     | JSON -> DB インポート                            |
| `irbank_bs.py`       | `config`                         | /bs ページのパース・行生成                       |
| `irbank_forecast.py` | `config`                         | /results ページのパース・行生成                  |
| `irbank_common.py`   | `http_fetch`                     | IR BANK URL ビルダー (http_fetch に委譲)         |
| `kabutan_shares.py`  | `config`, `stealth`              | kabutan 発行済株式数の取得・パース (plain HTTPS)  |
| `stooq_price.py`     | `browser`                        | Stooq 日次テキストファイルによる株価一括取得      |

### コア層

| モジュール              | 依存先                                        | 役割                                                         |
| :---------------------- | :-------------------------------------------- | :----------------------------------------------------------- |
| `screener.py`           | `config`, `repository`, `metrics`, `db.schema` | 戦略ファイルの動的ロード・宣言的フォーマット解釈・全銘柄並列適用 |
| `metrics.py`            | (なし)                                        | 財務データ + 株価 -> 派生指標の事前計算。PER は `market_cap / net_income` で計算し、per-share 値 (EPS) に依存しない (株式分割安全) |
| `indicators/`           | `config`                                      | 戦略から呼ぶオンデマンド指標 (FCFイールド, CROIC 等)         |
| `worker.py`             | `config`, `scrape.*`, `repository`, `db.schema`, `stealth`, `browser` | スクレイピング・株価取得のワーカー制御。`fetch_prices_stooq` は `get_browser` callable を受け取り、ローカル Stooq txt が無い時だけ lazy に browser を起動 |
| `bootstrap.py`          | `config`, `repository`, `db.schema`, `cli`, `worker`, `scrape.*` | empty DB 検出時の auto-import/scrape/fetch。`required_sources` で対象を絞り、scrape が必要なときだけ proxy / browser を lazy に取得 |

### インフラ層

| モジュール         | 依存先     | 役割                                                      |
| :----------------- | :--------- | :-------------------------------------------------------- |
| `config.py`        | (なし)     | TOML 読み込み、パス定数                                   |
| `db/schema.py`     | `config`   | DDL、マイグレーション、接続生成                           |
| `db/repository.py` | (なし)     | CRUD 操作 (stocks, financial_items, prices)               |
| `browser.py`       | `config`   | Node.js puppeteer-real-browser サービスの起動・終了・fetch/download (プロキシはオプショナル) |
| `stealth.py`       | `config`   | プロキシプール、reason 付き失敗キャッシュ、分散サイト検証 |
| `log.py`           | `config`   | ロギング設定                                              |
| `fmt.py`           | (なし)     | 全角対応の文字列整形                                      |

### スタンドアロンスクリプト (`scripts/`)

| スクリプト              | 使用モジュール                                                | 用途                     |
| :---------------------- | :------------------------------------------------------------ | :----------------------- |
| `download_irbank.py`    | `config`, `stealth.fetch_live_proxies`                        | JSON ダウンロード        |
| `scrape_irbank_bs.py`   | `cli.main` (→ `scrape-bs` サブコマンドに委譲)                 | BS スクレイピング |
| `fetch_prices.py`       | `cli.main` (→ `fetch-prices` サブコマンドに委譲)              | 株価取得 (Stooq)         |
| `fetch_shares.py`       | `cli.main` (→ `fetch-shares` サブコマンドに委譲)              | 発行済株式数取得 (kabutan) |
| `export_csv.py`         | `config`, `db.schema`, `screener.build_stock_dict`            | CSV エクスポート         |
| `generate_check_sites.py` | (外部: Tranco リスト)                                       | プロキシ検証用サイトリスト生成 |

## データベーススキーマ

3 テーブル構成。すべて SQLite WAL モードで運用。

### stocks

銘柄マスタ。IR BANK JSON インポート時に自動登録、BS スクレイピング時に企業名を更新。`shares_outstanding` は `fetch-shares` で kabutan から取得し、スクリーニング時の時価総額計算に使う。

| カラム              | 型      | 備考                                          |
| :------------------ | :------ | :-------------------------------------------- |
| ticker              | TEXT    | PK                                            |
| edinet_code         | TEXT    | UNIQUE (nullable)                             |
| name                | TEXT    | 企業名                                        |
| sector              | TEXT    | セクター                                      |
| market              | TEXT    | 市場                                          |
| shares_outstanding  | INTEGER | 発行済株式数 (kabutan 由来、分割後の最新値)   |
| shares_updated_at   | TEXT    | shares_outstanding の取得日時                 |
| updated_at          | TEXT    | ISO8601 タイムスタンプ                        |

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

Stooq から取得した株価キャッシュ。`updated_at` が 1 日以上古い場合に再取得。

| カラム     | 型      | 備考                 |
| :--------- | :------ | :------------------- |
| ticker     | TEXT    | 銘柄コード           |
| date       | TEXT    | 日付                 |
| close      | REAL    | 終値                 |
| volume     | INTEGER | 出来高               |
| updated_at | TEXT    | ISO8601 タイムスタンプ |

PK: `(ticker, date)`

## 設定ファイル

| ファイル               | 内容                                                     |
| :--------------------- | :------------------------------------------------------- |
| `config/path.toml`     | データディレクトリ、DB パス、ログディレクトリ等の相対パス |
| `config/magic_numbers.toml` | スクレイピング間隔・ワーカー数・バッチサイズ等の定数 |
| `config/cli_defaults.toml`  | CLI オプションのデフォルト値 (ダウンロード年数、`probe-proxies` のデフォルト等) |
| `config/validation_sites.txt` | プロキシ品質検証用ドメインリスト (Tranco top sites 由来) |

TOML ファイルは `config.py` が起動時に読み込み、`MAGIC`, `PATHS`, `CLI_DEFAULTS` として公開する。`validation_sites.txt` は `stealth.py` がモジュールロード時に読み込む。

## CLI サブコマンド

`uv run python -m formula_screening <command>` で実行。

| コマンド              | 処理内容                                                                                                      |
| :-------------------- | :------------------------------------------------------------------------------------------------------------ |
| `import-irbank`       | `data/irbank/` の JSON を DB にインポート                                                                     |
| `fetch-prices`        | 全銘柄の株価を Stooq 日次テキストファイルから一括取得                                                         |
| `fetch-shares`        | kabutan から発行済株式数を取得し `stocks.shares_outstanding` に保存。BrowserService 不要 (plain HTTPS)         |
| `scrape-bs`           | IR BANK /bs ページから詳細 BS データをスクレイピング                                                          |
| `scrape-forecast`     | IR BANK /results ページから会社予想をスクレイピング                                                           |
| `probe-proxies`       | 公開プロキシ取得だけを診断実行 (`--clear-legacy-cache` で legacy cache を削除)                                |
| `clear-failure-cache` | reason を指定して proxy failure cache を削除し、削除前後の分布を表示                                          |
| `screen`              | 戦略ファイルを適用してスクリーニング実行 (`--workers` で並列化、`--open [N]` で上位N件を四季報オンラインで開く) |

`screen` 実行時には `cli._cmd_screen` が先に `load_strategy()` で戦略モジュールを読み込み、`REQUIRED_SOURCES` を取り出してから `bootstrap.ensure_data_available(required_sources=...)` を呼ぶ。bootstrap は `required_sources` に列挙された `financial_items` の source と `prices` だけをチェック対象にし、空のものがあれば対応する import/scrape/fetch を auto 実行する。戦略が必要としないソース (例: `irbank_forecast`) は空でもスキップされる。すべての required データが揃っている場合はそのまま screening に進む。データの再取得は各サブコマンド (`scrape-bs`, `scrape-forecast`, `fetch-prices`) を明示的に実行することでのみ行う。

`ensure_data_available()` は `get_proxy_pool` / `get_browser` を lazy callable として受け取り、**scrape (`irbank_bs` / `irbank_forecast`) が実際に必要なときだけ** プロキシ取得を発動する。`prices` のみ不足していてローカル Stooq 日次 txt がある場合はプロキシもブラウザも起動せず、`fetch_prices_stooq` が get_browser callable を内部で参照して "ローカルファイル無し" のときだけ lazy に browser を起動する。

プロキシを使うサブコマンド (`fetch-prices`, `scrape-bs`, `scrape-forecast`, `screen`) は `_proxy_args` 親パーサー経由で共通の `--proxy`, `--proxy-file`, `--target-proxies`, `--check-sites` オプションを継承する。`--proxy-file` は `host:port:user:pass` 形式の認証付きプロキシリストファイルを指定し、`ProxyPool.from_file()` で読み込む。`--target-proxies` は検証合格プロキシの目標数 (デフォルト: `proxy.target_count`)、`--check-sites` は各プロキシが通過すべきサイト数 (デフォルト: `proxy.quality_check_count`) を指定する。

`--proxy` は以下の 3 モードを受け付ける。デフォルトは `direct`:

- `direct` (デフォルト): 空の `ProxyPool(direct=True)` に解決され、`fetch_irbank_html` は `browser.fetch(proxy=None)` で IR BANK に直接接続する。進捗ラベルに `proxies=direct` が表示され、プロキシのローテーションや failure cache のクリアは発動しない
- `auto`: `ProxyPool.from_auto()` で公開プロキシを自動取得する（旧デフォルト）。auto 時のみ失敗キャッシュの transient reason 自動クリアが走る
- URL (`http://host:port` / `socks5://host:port` など): `ProxyPool.from_url()` で単一ユーザ指定プロキシ

`probe-proxies` は DB やスクリーニングデータに触れず、公開プロキシ取得だけを診断するためのコマンドで、デフォルトで `--target-proxies` / `--check-sites` を `cli_defaults.toml [probe_proxies]` から取得し最小チェックを行う。`--clear-legacy-cache` を付けると、short TTL に移行する前の legacy failure cache だけを一度削除してから試行できる。

`clear-failure-cache` は `--reason quality_failed --reason anon_unreachable` のように reason を repeatable に指定して、再試行したい failure cache だけを削除する。`--all` を付けると active cache 全件を削除する。引数なしで実行した場合は、削除せず現在の reason 分布だけを表示する。

自動プロキシ解決 (`ProxyPool.from_auto()`) で live proxy を 1 件も確保できなかった場合は `stealth.ProxyUnavailableError` を送出し、CLI とスクリプトは `ABORT: ...` を stderr に出して `exit(1)` する。エラーメッセージには直前の `passed / cache_skipped / prefilter / validation` 要約も含まれる。

`fetch-prices` は Stooq の日次テキストファイルから株価を取得する。`data/stooq/` に配置済みの日次テキストファイル (`YYYYMMDD_d.txt`) があればそれをパースし、なければ `browser_service` 経由で `https://stooq.com/db/` の CAPTCHA を解いた後 `https://stooq.com/db/d/?d={date}&t=d` からダウンロードする。プロキシ不要。

スクレイピング系コマンド (`scrape-bs`, `scrape-forecast`, `fetch-prices`) および `screen` の自動データ取得では、ワーカー数をプロキシプールのサイズ以下に制限する。これにより空サブプールの生成を防ぎ、全ワーカーがプロキシ経由で通信する。つまり `--workers 100` を指定しても、確保できた live proxy が 1 本なら実効ワーカー数は `1` になる。

IR BANK へのスクレイピングは `browser.py` (BrowserService) 経由で行う。`irbank_common.py` が BrowserService の `fetch()` を呼び出し、Node.js の puppeteer-real-browser でページをレンダリングして HTML を取得する。リトライ時は `scrape.retry_delay` (秒) だけ待機してから次の試行に進む。ワーカー制御ロジック (`worker.py`) はスクレイピング・パースモジュールから分離されており、ワーカーの進捗表示やスキップ判定の変更がキャッシュ無効化を発動しない設計になっている。

## 戦略ファイルの仕組み

`strategies/` に配置した `.py` ファイルが戦略となる。`screener.py` が `importlib` で動的にロードし、全銘柄に対してフィルタリングを実行する。

### 宣言的フォーマット (推奨)

モジュールレベル変数で条件を定義する。個別の指標計算ロジックは `indicators/` モジュールに配置し、戦略ファイルでは「どの指標をどの条件で適用するか」のみを記述する。

```python
from formula_screening.indicators import fcf_yield_avg, croic

REQUIRED_SOURCES = ["irbank", "irbank_bs", "prices"]  # auto-bootstrap の対象を絞る

FILTERS = [
    ("net_cash_ratio", ">", 1.0),       # stock["metrics"] のキーを参照
    ("per", "between", (0, 10)),         # 排他的範囲: 0 < per < 10
    ("equity_ratio", ">", 50),
    (fcf_yield_avg, ">", 0),            # Callable は source(stock) で評価
]

SORT = fcf_yield_avg                     # ソートキー (降順)。未定義時は net_cash_ratio

COLUMNS = [                              # 追加表示カラム
    ("FCF_Y%", fcf_yield_avg, "{:.2%}"),
    ("CROIC%", croic, "{:.2%}"),
]
```

**REQUIRED_SOURCES** (optional): 戦略が実際に必要とするデータソース名のリスト。有効値は `"irbank"`, `"irbank_bs"`, `"irbank_forecast"`, `"prices"`。`screen` コマンドはこのリストを `ensure_data_available()` に渡し、列挙されたソースだけを auto-fetch 対象とする。未宣言時は全ソースが対象 (従来の挙動)。必要のないソース (典型的には `irbank_forecast`) を除外することでプロキシ取得・ブラウザ起動の多くを回避できる。

**FILTERS**: `(source, op, threshold)` のリスト。

- `source`: `str` なら `stock["metrics"][source]`、`Callable` なら `source(stock)` で値を取得
- `op`: `">"`, `">="`, `"<"`, `"<="`, `"between"`
- `threshold`: 数値。`between` の場合は `(lo, hi)` タプル (排他的範囲)
- 値が `None` ならそのフィルタは不通過

**SORT**: `str` (metric名) または `Callable` (indicator関数)。降順ソート。

**COLUMNS**: `(header, source, format_str)` のリスト。`None` 値は `"-"` 表示。

`screener.py` の `load_strategy()` が宣言的定義から `screen()` / `sort_key()` / `columns()` 関数を自動生成するため、CLI 側の変更は不要。

### 関数ベースフォーマット (後方互換)

`screen(stock: dict) -> bool` 関数を直接定義する従来の形式も引き続き動作する。`FILTERS` が定義されている場合はそちらが優先される。

### indicators モジュール

`src/formula_screening/indicators/` に戦略から呼ぶオンデマンド指標関数を配置する。`metrics.py` が stock dict 構築時に事前計算する基本指標 (PER, PBR, net_cash_ratio 等) に対し、`indicators/` は戦略ファイルから参照される派生指標を提供する。

| 関数             | モジュール          | 概要                                          |
| :--------------- | :------------------ | :-------------------------------------------- |
| `fcf_yield_avg`  | `indicators/fcf.py` | 過去N年間の平均FCFイールド (FCF / 時価総額)    |
| `croic`          | `indicators/croic.py` | CROIC (FCF / 投下資本)                       |

### `stock` dict の構造

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
    "forecast": {"net_income": float, "basic_eps": float, ...},
    "metrics": {"per": float, "per_actual": float, "net_cash_ratio": float, ...},
    "cf_history": [("2025-03", {"operating_cf": float, ...}), ...],
}
```

## プロキシ検証の仕組み

`stealth.py` の `fetch_live_proxies` は公開プロキシリストから候補を収集し、source ごとの件数と候補ごとの初出 source を保持しながら検証する。ページ取得自体は `browser.py` (BrowserService) に委譲されたため、`stealth.py` はプロキシプール管理と検証に専念する。`ProxyPool.from_auto()` はこの結果を受け取り、1 件も確保できなければ reason 集計付きの `ProxyUnavailableError` で fail-fast する。

ソースは `config/proxy_sources.txt` で管理され、HTTP と SOCKS5 の両プロトコルに対応する。SOCKS5 ソースは行頭に `socks5` タグを付けて区別する。各候補にはソース由来のプロトコルタグ (`http` / `socks5`) が付与され、検証パイプラインと `ProxyPool` はプロトコルに応じて `http://` または `socks5h://` スキームを使い分ける。`socks5h://` は DNS 解決もプロキシ側で行うバリアント。

`ProxyPool` は3種類のファクトリを持つ:

- `from_auto()` — 公開プロキシリストから自動取得・検証
- `from_url(url)` — 単一プロキシ URL を直接指定
- `from_file(path)` — `host:port:user:pass` 形式の認証付きプロキシリストファイルから構築。`get()` は `http://user:pass@host:port` 形式の URL を返す

### 検証パイプライン

候補プロキシは4段階のパイプラインで絞り込まれる:

1. **失敗キャッシュ除外** — `data/.proxy_failures.json` を読み込み、TTL 内の失敗候補を reason ごとにスキップする。
2. **Proxy pre-filter** — 各候補に対して TCP connect テスト (`tcp_timeout`=0.5s, `tcp_workers`=500) と匿名性 endpoint への最小 proxy request を行う。ここで「ポートは開いているが、実際には proxy ではない Web サーバ」を `not_a_proxy` として落とす。
3. **匿名性チェック** — header-echo サービス (httpbin) の全エンドポイントへ並列リクエスト。最初の成功で匿名性を満たせば通過し、リーク検出 (`anon_leak`) で即失敗する。
4. **品質チェック** — `config/validation_sites.txt` のドメインから `quality_check_count` 個 (デフォルト0) をランダム選択し並列リクエスト。全サイト HTTP 200 必須 (`quality_failed` で失敗)。

匿名性チェックと品質チェックは各プロキシごとに **1つの Executor で同時発射** される。匿名性チェックの完了時には品質チェックが既に進行中のため、直列実行と比べてプロキシあたりの検証時間が大幅に短縮される。

外側の `fetch_live_proxies` が `check_workers` (デフォルト200) 個のプロキシを同時検証し、各プロキシ内で `len(anon_urls) + quality_check_count` スレッドを使うため、最大同時接続数は `check_workers × (2 + quality_check_count)` となる。

`_check_proxy()` は内部的に `ok`, `not_a_proxy`, `anon_unreachable`, `anon_leak`, `quality_failed` のような reason を返し、`fetch_live_proxies()` はその結果を failure cache と統計ログに反映する。

### 失敗キャッシュ

検証に失敗したプロキシは `data/.proxy_failures.json` に `{addr: {"reason": "...", "ts": unix_timestamp}}` 形式で記録される。旧形式 `{addr: unix_timestamp}` も読み込み時に受理され、`legacy` reason として短い TTL で扱う。

reason ごとの TTL は次の通り:

- `not_a_proxy`, `anon_leak`: `proxy.failure_cache_ttl_hours` (デフォルト24時間)
- `tcp_unreachable`, `anon_unreachable`, `quality_failed`, `legacy`: 1時間

これにより、「そもそも proxy ではない候補」は長く避けつつ、「一時的に不調だった候補」は短時間で再試行できる。成功したプロキシはキャッシュしない (時間経過で劣化する可能性があるため)。

`fetch_live_proxies()` は failure reason ごとの件数、source ごとの `fetched / cache_skipped / prefilter_pass / ok` 件数もログ出力する。大量に候補を返す source が全滅している場合は warning を出し、公開リストの品質劣化を発見しやすくしている。

サイトリストは `scripts/generate_check_sites.py` で Tranco top sites から生成する。Google / GitHub / Yahoo / IR BANK / EDINET 系および CDN・トラッキング系ドメインは除外済み。

### 失敗時の挙動

- 起動時に live proxy が 0 件なら、そのコマンドは開始せず `exit(1)` する。
- live proxy が 0 件だった場合の例外メッセージには、`0/N passed; cache_skipped=[...] ; prefilter=[...] ; validation=[...]` のような直前集計が含まれる。
- 実行中に現在のプロキシがレート制限や接続失敗で失効した場合は `report_failure()` でローテーションする。
- `fetch-prices` はローテーションの結果プールが空になった時点で `ProxyUnavailableError("All proxies exhausted")` を送出し、中断する。
- `scripts/download_irbank.py` も同様に、live proxy が 0 件なら `exit(1)` する。
- この fail-fast 方針により、プロキシ必須の経路で direct connection が使われることはない。
