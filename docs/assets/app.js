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
const StockTable = getStockTable();
function buildMonexUrl(code) {
    return "https://monex.ifis.co.jp/index.php?sa=report_zaimu&bcode=" + encodeURIComponent(code);
}
function buildShikihoUrl(code) {
    return "https://shikiho.toyokeizai.net/stocks/" + encodeURIComponent(code) + "/shikiho";
}
/* ------------------------------------------------------------------ */
/*  Column definitions                                                 */
/* ------------------------------------------------------------------ */
const COLUMNS = [
    {
        key: "code",
        header: "code",
        type: "code",
        title: "銘柄コード",
        render: (row) => String(row.code ?? ""),
        linkHref: (row) => buildMonexUrl(String(row.code ?? "")),
        linkMode: "browser",
        browserKey: "monex",
    },
    {
        key: "name",
        header: "name",
        type: "name",
        title: "会社名",
        render: (row) => String(row.name ?? ""),
        linkHref: (row) => buildShikihoUrl(String(row.code ?? "")),
        linkMode: "browser",
        browserKey: "shikiho",
    },
    {
        key: "price",
        header: "price",
        type: "num",
        title: "株価（終値）",
        toggleable: true,
        render: (row) => {
            const v = row.price;
            return v !== null && v !== undefined
                ? v.toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 })
                : "-";
        },
        sortValue: (row) => row.price ?? null,
    },
    {
        key: "net_cash_ratio",
        header: "NC_Ratio",
        type: "num",
        title: "(流動資産 - 棚卸資産 + 有価証券 * 0.7) / 時価総額",
        toggleable: true,
        render: (row) => {
            const metrics = row.metrics;
            const v = metrics?.net_cash_ratio;
            return v !== null && v !== undefined ? v.toFixed(2) : "-";
        },
        sortValue: (row) => {
            const metrics = row.metrics;
            return metrics?.net_cash_ratio ?? null;
        },
    },
    {
        key: "per",
        header: "PER",
        type: "num",
        title: "株価 / 来期予想EPS",
        toggleable: true,
        render: (row) => {
            const metrics = row.metrics;
            const v = metrics?.per;
            return v !== null && v !== undefined ? v.toFixed(1) : "-";
        },
        sortValue: (row) => {
            const metrics = row.metrics;
            return metrics?.per ?? null;
        },
    },
    {
        key: "pbr",
        header: "PBR",
        type: "num",
        title: "株価純資産倍率",
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
    {
        key: "dividend_yield",
        header: "Div%",
        type: "num",
        title: "配当利回り",
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
    {
        key: "equity_ratio",
        header: "Equity%",
        type: "num",
        title: "自己資本 / 総資産 * 100",
        toggleable: true,
        render: (row) => {
            const metrics = row.metrics;
            const v = metrics?.equity_ratio;
            return v !== null && v !== undefined ? v.toFixed(1) + "%" : "-";
        },
        sortValue: (row) => {
            const metrics = row.metrics;
            return metrics?.equity_ratio ?? null;
        },
    },
    {
        key: "fcf_yield_avg",
        header: "FCF_Y%",
        type: "num",
        title: "過去N期の平均FCF / 時価総額",
        toggleable: true,
        render: (row) => {
            const v = row.fcf_yield_avg;
            if (v === null || v === undefined) {
                return "-";
            }
            return (v * 100).toFixed(2) + "%";
        },
        sortValue: (row) => {
            const v = row.fcf_yield_avg;
            return v != null ? v * 100 : null;
        },
    },
    {
        key: "croic",
        header: "CROIC%",
        type: "num",
        title: "FCF / (自己資本 + 有利子負債)",
        toggleable: true,
        render: (row) => {
            const v = row.croic;
            if (v === null || v === undefined) {
                return "-";
            }
            return (v * 100).toFixed(2) + "%";
        },
        sortValue: (row) => {
            const v = row.croic;
            return v != null ? v * 100 : null;
        },
    },
];
const METRIC_THRESHOLDS = {
    net_cash_ratio: { good: (v) => v > 1 },
    per: { good: (v) => v > 0 && v <= 7, bad: (v) => v > 7 },
    pbr: { good: (v) => v < 0.5 },
    dividend_yield: { good: (v) => v >= 4 },
    equity_ratio: { good: (v) => v >= 50 },
    fcf_yield_avg: { good: (v) => v >= 10 },
    croic: { good: (v) => v >= 15 },
};
/* ------------------------------------------------------------------ */
/*  Bootstrap                                                          */
/* ------------------------------------------------------------------ */
function bootstrap() {
    StockTable.init({
        defaultTitle: "Formula Screening",
        dataUrl: "/api/screening",
        columns: COLUMNS,
        metricThresholds: METRIC_THRESHOLDS,
        defaultSortKey: "net_cash_ratio",
        defaultSortDirection: "desc",
        tabMode: false,
    });
}
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
}
else {
    bootstrap();
}
export {};
