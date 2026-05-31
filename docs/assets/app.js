/**
 * formula_screening – app.ts
 *
 * TOML-driven column configuration for StockTable.
 * Fetches column-config.json to build columns dynamically,
 * then fetches screening results and renders them.
 */
/* ------------------------------------------------------------------ */
/*  Module getters                                                     */
/* ------------------------------------------------------------------ */
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
/* ------------------------------------------------------------------ */
/*  PEG status labels                                                  */
/* ------------------------------------------------------------------ */
const PEG_STATUS_LABELS = {
    missing_input: "miss",
    insufficient_history: "hist",
    non_positive_per: "per-",
    non_positive_eps: "eps-",
    non_positive_growth: "growth-",
};
const PEG_STATUS_LEGEND = "未算出: miss=入力欠損 / hist=履歴不足 / per-=PER<=0 / eps-=EPS<=0 / growth-=成長率<=0";
/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */
function toNumber(value) {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function metricsAccessor(key) {
    return (row) => {
        const metrics = row.metrics;
        return toNumber(metrics?.[key]);
    };
}
/* ------------------------------------------------------------------ */
/*  Dynamic column builders                                           */
/* ------------------------------------------------------------------ */
function buildNumCol(cfg) {
    const source = cfg.source;
    const scale = cfg.scale ?? 1;
    const decimals = cfg.decimals ?? 1;
    const suffix = cfg.suffix ?? "";
    return {
        key: source,
        header: cfg.header ?? source,
        type: "num",
        title: cfg.title,
        toggleable: cfg.toggleable ?? true,
        render: (row) => {
            const raw = toNumber(row[source]);
            if (raw === null)
                return "-";
            const v = raw * scale;
            return v.toFixed(decimals) + suffix;
        },
        sortValue: (row) => {
            const raw = toNumber(row[source]);
            return raw !== null ? raw * scale : null;
        },
    };
}
function buildMetricNumCol(cfg) {
    const metricKey = cfg.metric_key ?? cfg.source;
    const scale = cfg.scale ?? 1;
    const decimals = cfg.decimals ?? 1;
    const suffix = cfg.suffix ?? "";
    return {
        key: cfg.source,
        header: cfg.header ?? cfg.source,
        type: "num",
        title: cfg.title,
        toggleable: cfg.toggleable ?? true,
        render: (row) => {
            const raw = metricsAccessor(metricKey)(row);
            if (raw === null)
                return "-";
            const v = raw * scale;
            return v.toFixed(decimals) + suffix;
        },
        sortValue: (row) => {
            const raw = metricsAccessor(metricKey)(row);
            return raw !== null ? raw * scale : null;
        },
    };
}
function buildPegCol(cfg) {
    const source = cfg.source;
    const statusSource = cfg.status_source ?? source + "_status";
    const decimals = cfg.decimals ?? 2;
    const resolvedTitle = cfg.title ?? C.METRIC_TITLES[source];
    return {
        key: source,
        header: cfg.header ?? source,
        type: "num",
        title: resolvedTitle ? `${resolvedTitle} (${PEG_STATUS_LEGEND})` : undefined,
        toggleable: cfg.toggleable ?? true,
        render: (row) => {
            const value = toNumber(row[source]);
            if (value !== null) {
                return value.toFixed(decimals);
            }
            const status = typeof row[statusSource] === "string" ? row[statusSource] : null;
            if (status === null || status === "ok")
                return "-";
            return PEG_STATUS_LABELS[status] ?? "-";
        },
        sortValue: (row) => toNumber(row[source]),
    };
}
function buildBoolCol(cfg) {
    const source = cfg.source;
    return {
        key: source,
        header: cfg.header ?? source,
        type: "text",
        title: cfg.title,
        toggleable: cfg.toggleable ?? true,
        render: (row) => {
            const value = row[source];
            if (value === true)
                return "yes";
            if (value === false)
                return "no";
            return "-";
        },
        sortValue: (row) => {
            const value = row[source];
            if (value === true)
                return 1;
            if (value === false)
                return 0;
            return null;
        },
    };
}
function buildColumnsFromConfig(configs) {
    return configs.map(cfg => {
        switch (cfg.type) {
            case "code": return C.codeCol;
            case "name": return C.nameCol;
            case "price": return C.priceCol;
            case "num": return buildNumCol(cfg);
            case "metric_num": return buildMetricNumCol(cfg);
            case "peg": return buildPegCol(cfg);
            case "bool": return buildBoolCol(cfg);
            default: throw new Error(`Unknown column type: ${cfg.type}`);
        }
    });
}
/* ------------------------------------------------------------------ */
/*  Thresholds                                                         */
/* ------------------------------------------------------------------ */
const METRIC_THRESHOLDS = {
    ...C.COMMON_THRESHOLDS,
    pbr: { good: (v) => v < 0.5 },
    dividend_yield: { good: (v) => v >= 4 },
};
/* ------------------------------------------------------------------ */
/*  Bootstrap                                                          */
/* ------------------------------------------------------------------ */
async function bootstrap() {
    const columnConfigUrl = IS_GITHUB_PAGES
        ? "assets/column-config.json"
        : "/assets/column-config.json";
    let columns;
    try {
        const response = await fetch(columnConfigUrl, { cache: "no-store" });
        if (!response.ok) {
            throw new Error("HTTP " + response.status);
        }
        const configs = await response.json();
        columns = buildColumnsFromConfig(configs);
    }
    catch (err) {
        console.error("Failed to load column config, using defaults:", err);
        columns = [C.codeCol, C.nameCol];
    }
    StockTable.init({
        defaultTitle: "Formula Screening",
        dataUrl: IS_GITHUB_PAGES ? "assets/screening.json" : "/api/screening",
        metadataUrl: IS_GITHUB_PAGES ? "assets/stock-price-meta.json" : "/api/stock-price-meta",
        columns,
        metricThresholds: METRIC_THRESHOLDS,
        defaultSortKey: "net_cash_ratio",
        defaultSortDirection: "desc",
        tabMode: false,
        githubPages: IS_GITHUB_PAGES,
    });
}
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => void bootstrap());
}
else {
    void bootstrap();
}
export {};
