/**
 * formula_screening – app.js
 *
 * Flat-mode configuration for StockTable.
 * Fetches screening results from /api/screening and renders them.
 */
"use strict";

/* ------------------------------------------------------------------ */
/*  Column definitions                                                */
/* ------------------------------------------------------------------ */

var COLUMNS = [
    {
        key: "code",
        header: "code",
        type: "code",
        title: "\u9298\u67C4\u30B3\u30FC\u30C9",
        render: function (row) { return row.code; },
        sortValue: function (row) { return row.code; },
    },
    {
        key: "name",
        header: "name",
        type: "name",
        title: "\u4F1A\u793E\u540D",
        render: function (row) { return row.name || ""; },
    },
    {
        key: "price",
        header: "price",
        type: "num",
        title: "\u682A\u4FA1\uFF08\u7D42\u5024\uFF09",
        toggleable: true,
        render: function (row) {
            var v = row.price;
            return v !== null && v !== undefined ? v.toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "-";
        },
        sortValue: function (row) { return row.price != null ? row.price : null; },
    },
    {
        key: "net_cash_ratio",
        header: "NC_Ratio",
        type: "num",
        title: "(\u6D41\u52D5\u8CC7\u7523 - \u68DA\u5378\u8CC7\u7523 + \u6709\u4FA1\u8A3C\u5238 * 0.7) / \u6642\u4FA1\u7DCF\u984D",
        toggleable: true,
        render: function (row) {
            var v = row.metrics && row.metrics.net_cash_ratio;
            return v !== null && v !== undefined ? v.toFixed(2) : "-";
        },
        sortValue: function (row) { return row.metrics && row.metrics.net_cash_ratio != null ? row.metrics.net_cash_ratio : null; },
    },
    {
        key: "per",
        header: "PER",
        type: "num",
        title: "\u682A\u4FA1 / \u6765\u671F\u4E88\u60F3EPS",
        toggleable: true,
        render: function (row) {
            var v = row.metrics && row.metrics.per;
            return v !== null && v !== undefined ? v.toFixed(1) : "-";
        },
        sortValue: function (row) { return row.metrics && row.metrics.per != null ? row.metrics.per : null; },
    },
    {
        key: "pbr",
        header: "PBR",
        type: "num",
        title: "\u682A\u4FA1\u7D14\u8CC7\u7523\u500D\u7387",
        toggleable: true,
        render: function (row) {
            var v = row.metrics && row.metrics.pbr;
            return v !== null && v !== undefined ? v.toFixed(2) : "-";
        },
        sortValue: function (row) { return row.metrics && row.metrics.pbr != null ? row.metrics.pbr : null; },
    },
    {
        key: "dividend_yield",
        header: "Div%",
        type: "num",
        title: "\u914D\u5F53\u5229\u56DE\u308A",
        toggleable: true,
        render: function (row) {
            var v = row.metrics && row.metrics.dividend_yield;
            return v !== null && v !== undefined ? v.toFixed(2) : "-";
        },
        sortValue: function (row) { return row.metrics && row.metrics.dividend_yield != null ? row.metrics.dividend_yield : null; },
    },
    {
        key: "equity_ratio",
        header: "Equity%",
        type: "num",
        title: "\u81EA\u5DF1\u8CC7\u672C / \u7DCF\u8CC7\u7523 * 100",
        toggleable: true,
        render: function (row) {
            var v = row.metrics && row.metrics.equity_ratio;
            return v !== null && v !== undefined ? v.toFixed(1) + "%" : "-";
        },
        sortValue: function (row) { return row.metrics && row.metrics.equity_ratio != null ? row.metrics.equity_ratio : null; },
    },
    {
        key: "fcf_yield_avg",
        header: "FCF_Y%",
        type: "num",
        title: "\u904E\u53BBN\u671F\u306E\u5E73\u5747FCF / \u6642\u4FA1\u7DCF\u984D",
        toggleable: true,
        render: function (row) {
            var v = row.fcf_yield_avg;
            if (v === null || v === undefined) { return "-"; }
            return (v * 100).toFixed(2) + "%";
        },
        sortValue: function (row) { return row.fcf_yield_avg != null ? row.fcf_yield_avg * 100 : null; },
    },
    {
        key: "croic",
        header: "CROIC%",
        type: "num",
        title: "FCF / (\u81EA\u5DF1\u8CC7\u672C + \u6709\u5229\u5B50\u8CA0\u50B5)",
        toggleable: true,
        render: function (row) {
            var v = row.croic;
            if (v === null || v === undefined) { return "-"; }
            return (v * 100).toFixed(2) + "%";
        },
        sortValue: function (row) { return row.croic != null ? row.croic * 100 : null; },
    },
];

var METRIC_THRESHOLDS = {
    net_cash_ratio: { good: function (v) { return v > 1; } },
    per: { good: function (v) { return v > 0 && v <= 7; }, bad: function (v) { return v > 7; } },
    pbr: { good: function (v) { return v < 0.5; } },
    dividend_yield: { good: function (v) { return v >= 4; } },
    equity_ratio: { good: function (v) { return v >= 50; } },
    fcf_yield_avg: { good: function (v) { return v >= 10; } },
    croic: { good: function (v) { return v >= 15; } },
};

/* ------------------------------------------------------------------ */
/*  Bootstrap                                                         */
/* ------------------------------------------------------------------ */

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
} else {
    bootstrap();
}

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
