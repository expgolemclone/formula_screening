/**
 * formula_screening – app.ts
 *
 * Flat-mode configuration for StockTable.
 * Fetches screening results from /api/screening and renders them.
 */

import type { ColumnDef, MetricThreshold, StockTableConfig } from "@stock-web-ui/runtime";
import type { MetricColSpec } from "@stock-web-ui/columns";

type StockTableApi = {
  init: (config: StockTableConfig) => void;
};

type StockColumnsApi = {
  buildMetricCol: (spec: MetricColSpec, accessor: (row: Record<string, unknown>) => number | null) => ColumnDef;
  codeCol: ColumnDef;
  nameCol: ColumnDef;
  priceCol: ColumnDef;
  peg5yCol: ColumnDef;
  peg5y2fCol: ColumnDef;
  fcfYCol: ColumnDef;
  croicCol: ColumnDef;
  NCR_SPEC: MetricColSpec;
  PER_A_SPEC: MetricColSpec;
  PER_C_SPEC: MetricColSpec;
  PER_N_SPEC: MetricColSpec;
  EQUITY_SPEC: MetricColSpec;
  COMMON_THRESHOLDS: Record<string, MetricThreshold>;
};

function getStockTable(): StockTableApi {
  const runtime: StockTableApi | undefined = (
    globalThis as typeof globalThis & { StockTable?: StockTableApi }
  ).StockTable;
  if (!runtime) {
    throw new Error("Shared StockTable runtime is not loaded.");
  }
  return runtime;
}

function getStockColumns(): StockColumnsApi {
  const cols: StockColumnsApi | undefined = (
    globalThis as typeof globalThis & { StockColumns?: StockColumnsApi }
  ).StockColumns;
  if (!cols) {
    throw new Error("Shared StockColumns module is not loaded.");
  }
  return cols;
}

const StockTable: StockTableApi = getStockTable();
const C: StockColumnsApi = getStockColumns();
const IS_GITHUB_PAGES: boolean = location.hostname === "expgolemclone.github.io";
const PAYOUT_SPEC: MetricColSpec = {
  key: "total_payout_ratio",
  header: "payout%",
  title: "総還元額 / 時価総額 * 100",
  decimals: 1,
  suffix: "%",
};

/* ------------------------------------------------------------------ */
/*  Metrics accessor (nested under row.metrics)                        */
/* ------------------------------------------------------------------ */

function metricsAccessor(key: string): (row: Record<string, unknown>) => number | null {
  return (row: Record<string, unknown>): number | null => {
    const metrics = row.metrics as Record<string, unknown> | undefined;
    return (metrics?.[key] as number) ?? null;
  };
}

/* ------------------------------------------------------------------ */
/*  Column definitions                                                 */
/* ------------------------------------------------------------------ */

const COLUMNS: ColumnDef[] = [
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
    render: (row): string => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      const v = metrics?.dividend_yield as number | null | undefined;
      return v !== null && v !== undefined ? v.toFixed(2) : "-";
    },
    sortValue: (row): number | null => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      return (metrics?.dividend_yield as number) ?? null;
    },
  },
  C.buildMetricCol(PAYOUT_SPEC, metricsAccessor("total_payout_ratio")),
  {
    key: "has_preferred_shares",
    header: "pref",
    type: "text",
    title: "優先株",
    toggleable: true,
    render: (row): string => {
      const value = row.has_preferred_shares;
      if (value === true) {
        return "yes";
      }
      if (value === false) {
        return "no";
      }
      return "-";
    },
    sortValue: (row): number | null => {
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
    render: (row): string => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      const v = metrics?.pbr as number | null | undefined;
      return v !== null && v !== undefined ? v.toFixed(2) : "-";
    },
    sortValue: (row): number | null => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      return (metrics?.pbr as number) ?? null;
    },
  },
];

const METRIC_THRESHOLDS: Record<string, MetricThreshold> = {
  ...C.COMMON_THRESHOLDS,
  pbr: { good: (v): boolean => v < 0.5 },
  dividend_yield: { good: (v): boolean => v >= 4 },
};

/* ------------------------------------------------------------------ */
/*  Bootstrap                                                          */
/* ------------------------------------------------------------------ */

function bootstrap(): void {
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
} else {
  bootstrap();
}
