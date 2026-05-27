/**
 * formula_screening – app.ts
 *
 * Flat-mode configuration for StockTable.
 * Fetches screening results from /api/screening and renders them.
 */
function getStockTable() {
    const runtime = globalThis.StockTable;
    if (!runtime) {
        throw new Error("Shared StockTable runtime is not loaded.");
    }
    return runtime;
}
function getStockColumns() {
    const cols = globalThis.StockColumns;
    if (!cols) {
        throw new Error("Shared StockColumns module is not loaded.");
    }
    return cols;
}
const StockTable = getStockTable();
const C = getStockColumns();
const IS_GITHUB_PAGES = location.hostname === "expgolemclone.github.io";
const PAYOUT_SPEC = {
    key: "total_payout_ratio",
    header: "payout%",
    title: "総還元額 / 時価総額 * 100",
    decimals: 1,
    suffix: "%",
};
/* ------------------------------------------------------------------ */
/*  Metrics accessor (nested under row.metrics)                        */
/* ------------------------------------------------------------------ */
function metricsAccessor(key) {
    return (row) => {
        const metrics = row.metrics;
        return metrics?.[key] ?? null;
    };
}
/* ------------------------------------------------------------------ */
/*  Column definitions                                                 */
/* ------------------------------------------------------------------ */
const COLUMNS = [
    C.codeCol,
    C.nameCol,
    C.priceCol,
    C.buildMetricCol(C.NCR_SPEC, metricsAccessor("net_cash_ratio")),
    C.buildMetricCol(C.PER_A_SPEC, metricsAccessor("per_actual")),
    C.buildMetricCol(C.PER_C_SPEC, metricsAccessor("per")),
    C.buildMetricCol(C.PER_N_SPEC, metricsAccessor("per_next")),
    C.fcfYCol,
    C.buildMetricCol(C.EQUITY_SPEC, metricsAccessor("equity_ratio")),
    C.peg5yCol,
    C.peg5y2fCol,
    {
        key: "dividend_yield",
        header: "div%",
        type: "num",
        title: "dividend yield",
        toggleable: true,
        render: (row) => {
            const metrics = row.metrics;
            const v = metrics?.dividend_yield;
            return v !== null && v !== undefined ? v.toFixed(2) : "-";
        },
        sortValue: (row) => {
            const metrics = row.metrics;
            return metrics?.dividend_yield ?? null;
        },
    },
    C.buildMetricCol(PAYOUT_SPEC, metricsAccessor("total_payout_ratio")),
    {
        key: "has_preferred_shares",
        header: "pref",
        type: "text",
        title: "優先株",
        toggleable: true,
        render: (row) => {
            const value = row.has_preferred_shares;
            if (value === true) {
                return "yes";
            }
            if (value === false) {
                return "no";
            }
            return "-";
        },
        sortValue: (row) => {
            const value = row.has_preferred_shares;
            if (value === true) {
                return 1;
            }
            if (value === false) {
                return 0;
            }
            return null;
        },
    },
    C.croicCol,
    {
        key: "pbr",
        header: "pbr",
        type: "num",
        title: "price book value ratio",
        toggleable: true,
        render: (row) => {
            const metrics = row.metrics;
            const v = metrics?.pbr;
            return v !== null && v !== undefined ? v.toFixed(2) : "-";
        },
        sortValue: (row) => {
            const metrics = row.metrics;
            return metrics?.pbr ?? null;
        },
    },
];
const METRIC_THRESHOLDS = {
    ...C.COMMON_THRESHOLDS,
    pbr: { good: (v) => v < 0.5 },
    dividend_yield: { good: (v) => v >= 4 },
};
/* ------------------------------------------------------------------ */
/*  Bootstrap                                                          */
/* ------------------------------------------------------------------ */
function bootstrap() {
    StockTable.init({
        defaultTitle: "Formula Screening",
        dataUrl: IS_GITHUB_PAGES ? "assets/screening.json" : "/api/screening",
        metadataUrl: IS_GITHUB_PAGES ? "assets/stock-price-meta.json" : "/api/stock-price-meta",
        columns: COLUMNS,
        metricThresholds: METRIC_THRESHOLDS,
        defaultSortKey: "net_cash_ratio",
        defaultSortDirection: "desc",
        tabMode: false,
        githubPages: IS_GITHUB_PAGES,
    });
}
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
}
else {
    bootstrap();
}
export {};
