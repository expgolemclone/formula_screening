# formula_screening

- TOML で条件指定できるスクリーナー
- [`strategies/net_cash_fcf.toml`](https://github.com/expgolemclone/formula_screening/blob/main/strategies/net_cash_fcf.toml)の[実行結果](https://expgolemclone.github.io/formula_screening/)
- Rust CLI: `cargo run --bin formula-screening -- screen -s strategies/net_cash_fcf.toml -t 1867 --json /tmp/screening.json`
- 下流 Python repo 向けの `compute_all_stock_metrics()` は Rust binding を利用する。

---

## 開発環境

Nix dev shell は必須ではない。標準の開発環境はローカルツールと `.venv` を使う。

必要なツール:

- `uv`
- Python 3.13
- Rust/Cargo
- Node.js/npm
- `git-lfs`（LFS 対象ファイルを扱う場合）

初期化:

```sh
uv sync --frozen --extra dev
npm ci
```

検証:

```sh
UV_PROJECT_ENVIRONMENT=.venv uv run --frozen pytest
cargo test --locked
npx tsc --noEmit
```

> [!NOTE]
> 仕様は[ARCHITECTURE.md](ARCHITECTURE.md)を参照
