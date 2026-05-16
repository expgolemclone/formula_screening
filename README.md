# formula_screening

Pythonで条件指定できるスクリーナー
データ取得は `stock_db`、画面表示は `stock_web_ui` に委譲します。

- GitHub Pages: https://expgolemclone.github.io/formula_screening/
- 詳細設計: [ARCHITECTURE.md](./ARCHITECTURE.md)

## 前提

- Python 3.13 以上
- `uv`
- `../stock_db` と `../stock_web_ui` が同じ親ディレクトリ配下にあること
- `stock_db` 側に価格・財務データが投入済みであること

## 実行

```bash
uv sync
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 1867
```

複数銘柄、全銘柄、範囲、CSV も指定できます。

```bash
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 1867 7203
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t all --workers 8
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t 1000-2000
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t csv:data/tickers.csv
```

静的配信用 JSON を更新する場合:

```bash
uv run python -m formula_screening screen -s strategies/net_cash_fcf.py -t all --json docs/assets/screening.json
```

## 戦略

戦略ファイルは `FILTERS` または `screen(stock) -> bool` で定義します。例は [strategies/net_cash_fcf.py](./strategies/net_cash_fcf.py) を参照してください。
