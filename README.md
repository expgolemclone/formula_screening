# formula_screening

- TOML で条件指定できるスクリーナー
- [`strategies/net_cash_fcf.toml`](https://github.com/expgolemclone/formula_screening/blob/main/strategies/net_cash_fcf.toml)の[実行結果](https://expgolemclone.github.io/formula_screening/)
- Rust CLI: `cargo run --manifest-path rust/Cargo.toml --bin formula-screening -- screen -s strategies/net_cash_fcf.toml -t 1867 --json /tmp/screening.json`
- 下流 Python repo 向けの `compute_all_stock_metrics()` は Rust binding を利用する。

---

> [!NOTE]
> 仕様は[ARCHITECTURE.md](ARCHITECTURE.md)を参照
