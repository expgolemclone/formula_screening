/**
 * formula_screening – app.ts
 *
 * Flat-mode configuration for StockTable.
 * Fetches screening results from /api/screening and renders them.
 */

import type { ColumnDef, MetricThreshold, StockTableConfig } from "@stock-web-ui/runtime";

type StockTableApi = {
  init: (config: StockTableConfig) => void;
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

const StockTable: StockTableApi = getStockTable();
const IS_GITHUB_PAGES: boolean = location.hostname === "expgolemclone.github.io";

/* ------------------------------------------------------------------ */
/*  Column definitions                                                 */
/* ------------------------------------------------------------------ */

const COLUMNS: ColumnDef[] = [
  {
    key: "code",
    header: "code",
    type: "code",
    title: "銘柄コード",
    render: (row): string => String(row.code ?? ""),
    stockLink: "monex",
  },
  {
    key: "name",
    header: "name",
    type: "name",
    title: "会社名",
    render: (row): string => String(row.name ?? ""),
    stockLink: "yazi",
  },
  {
    key: "price",
    header: "price",
    type: "num",
    title: "株価（終値）",
    toggleable: true,
    stockLink: "shikiho",
    render: (row): string => {
      const v = row.price as number | null | undefined;
      return v !== null && v !== undefined
        ? v.toLocaleString("ja-JP", { minimumFractionDigits: 1, maximumFractionDigits: 1 })
        : "-";
    },
    sortValue: (row): number | null => (row.price as number) ?? null,
  },
  {
    key: "net_cash_ratio",
    header: "ncr",
    type: "num",
    title: "(流動資産 - 棚卸資産 + 有価証券 * 0.7) / 時価総額",
    toggleable: true,
    render: (row): string => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      const v = metrics?.net_cash_ratio as number | null | undefined;
      return v !== null && v !== undefined ? v.toFixed(2) : "-";
    },
    sortValue: (row): number | null => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      return (metrics?.net_cash_ratio as number) ?? null;
    },
  },
  {
    key: "per",
    header: "PER",
    type: "num",
    title: "時価総額 / 四季報今期予想純利益",
    toggleable: true,
    render: (row): string => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      const v = metrics?.per as number | null | undefined;
      return v !== null && v !== undefined ? v.toFixed(1) : "-";
    },
    sortValue: (row): number | null => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      return (metrics?.per as number) ?? null;
    },
  },
  {
    key: "per_next",
    header: "PER+1",
    type: "num",
    title: "時価総額 / 四季報来期予想純利益",
    toggleable: true,
    render: (row): string => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      const v = metrics?.per_next as number | null | undefined;
      return v !== null && v !== undefined ? v.toFixed(1) : "-";
    },
    sortValue: (row): number | null => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      return (metrics?.per_next as number) ?? null;
    },
  },
  {
    key: "peg_5",
    header: "peg_5",
    type: "num",
    title: "実績PER / 過去5期純利益CAGR[%]",
    toggleable: true,
    render: (row): string => {
      const v = row.peg_5 as number | null | undefined;
      return v !== null && v !== undefined ? v.toFixed(2) : "-";
    },
    sortValue: (row): number | null => (row.peg_5 as number) ?? null,
  },
  {
    key: "pbr",
    header: "PBR",
    type: "num",
    title: "株価純資産倍率",
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
  {
    key: "dividend_yield",
    header: "Div%",
    type: "num",
    title: "配当利回り",
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
  {
    key: "equity_ratio",
    header: "Equity%",
    type: "num",
    title: "自己資本 / 総資産 * 100",
    toggleable: true,
    render: (row): string => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      const v = metrics?.equity_ratio as number | null | undefined;
      return v !== null && v !== undefined ? v.toFixed(1) + "%" : "-";
    },
    sortValue: (row): number | null => {
      const metrics = row.metrics as Record<string, unknown> | undefined;
      return (metrics?.equity_ratio as number) ?? null;
    },
  },
  {
    key: "fcf_yield_avg",
    header: "FCF_Y%",
    type: "num",
    title: "過去N期の平均FCF / 時価総額",
    toggleable: true,
    render: (row): string => {
      const v = row.fcf_yield_avg as number | null | undefined;
      if (v === null || v === undefined) { return "-"; }
      return (v * 100).toFixed(2) + "%";
    },
    sortValue: (row): number | null => {
      const v = row.fcf_yield_avg as number | null | undefined;
      return v != null ? v * 100 : null;
    },
  },
  {
    key: "croic",
    header: "CROIC%",
    type: "num",
    title: "FCF / (自己資本 + 有利子負債)",
    toggleable: true,
    render: (row): string => {
      const v = row.croic as number | null | undefined;
      if (v === null || v === undefined) { return "-"; }
      return (v * 100).toFixed(2) + "%";
    },
    sortValue: (row): number | null => {
      const v = row.croic as number | null | undefined;
      return v != null ? v * 100 : null;
    },
  },
];

const METRIC_THRESHOLDS: Record<string, MetricThreshold> = {
  net_cash_ratio: { good: (v): boolean => v > 1 },
  per: { good: (v): boolean => v > 0 && v <= 7, bad: (v): boolean => v > 7 },
  per_next: { good: (v): boolean => v > 0 && v <= 7, bad: (v): boolean => v > 7 },
  pbr: { good: (v): boolean => v < 0.5 },
  dividend_yield: { good: (v): boolean => v >= 4 },
  equity_ratio: { good: (v): boolean => v >= 50 },
  fcf_yield_avg: { good: (v): boolean => v >= 10 },
  croic: { good: (v): boolean => v >= 15 },
};

/* ------------------------------------------------------------------ */
/*  Bootstrap                                                          */
/* ------------------------------------------------------------------ */

function bootstrap(): void {
  StockTable.init({
    defaultTitle: "Formula Screening",
    dataUrl: "/api/screening",
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
