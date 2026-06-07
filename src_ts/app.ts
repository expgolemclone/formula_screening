/**
 * formula_screening – app.ts
 *
 * TOML-driven column configuration for StockTable.
 * Fetches column-config.json to build columns dynamically,
 * then fetches screening results and renders them.
 */

import type { ColumnDef, MetricThreshold, StockLink, StockTableConfig } from "@stock-web-ui/runtime";

type StockTableApi = {
  init: (config: StockTableConfig) => void;
};

type StockColumnsApi = {
  buildMetricCol: (spec: MetricColSpec, accessor: (row: Record<string, unknown>) => number | null) => ColumnDef;
  codeCol: ColumnDef;
  nameCol: ColumnDef;
  priceCol: ColumnDef;
  fcfYCol: ColumnDef;
  croicCol: ColumnDef;
  peg5yCol: ColumnDef;
  peg5y2fCol: ColumnDef;
  NCR_SPEC: MetricColSpec;
  PER_A_SPEC: MetricColSpec;
  PER_C_SPEC: MetricColSpec;
  PER_N_SPEC: MetricColSpec;
  EQUITY_SPEC: MetricColSpec;
  COMMON_THRESHOLDS: Record<string, MetricThreshold>;
  METRIC_TITLES: Record<string, string>;
};

interface MetricColSpec {
  key: string;
  header: string;
  title?: string;
  decimals: number;
  scale?: number;
  suffix?: string;
}

type Row = Record<string, unknown>;

/* ------------------------------------------------------------------ */
/*  Column config type (from TOML via JSON)                            */
/* ------------------------------------------------------------------ */

interface ColumnConfig {
  source: string;
  header?: string;
  type?: string;
  format?: string;
  decimals?: number;
  scale?: number;
  suffix?: string;
  title?: string;
  toggleable?: boolean;
  status_source?: string;
  metric_key?: string;
  stock_link?: StockLink;
}

/* ------------------------------------------------------------------ */
/*  Module getters                                                     */
/* ------------------------------------------------------------------ */

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

/* ------------------------------------------------------------------ */
/*  PEG status labels                                                  */
/* ------------------------------------------------------------------ */

const PEG_STATUS_LABELS: Record<string, string> = {
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

function toNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metricsAccessor(key: string): (row: Row) => number | null {
  return (row: Row): number | null => {
    const metrics = row.metrics as Record<string, unknown> | undefined;
    return toNumber(metrics?.[key]);
  };
}

/* ------------------------------------------------------------------ */
/*  Dynamic column builders                                           */
/* ------------------------------------------------------------------ */

function buildNumCol(cfg: ColumnConfig): ColumnDef {
  const source = cfg.source;
  const scale = cfg.scale ?? 1;
  const decimals = cfg.decimals ?? 1;
  const suffix = cfg.suffix ?? "";

  const col: ColumnDef = {
    key: source,
    header: cfg.header ?? source,
    type: "num",
    title: cfg.title,
    toggleable: cfg.toggleable ?? true,
    render: (row: Row): string => {
      const raw = toNumber(row[source]);
      if (raw === null) return "-";
      const v = raw * scale;
      return v.toFixed(decimals) + suffix;
    },
    sortValue: (row: Row): number | null => {
      const raw = toNumber(row[source]);
      return raw !== null ? raw * scale : null;
    },
  };
  if (cfg.stock_link) {
    col.stockLink = cfg.stock_link;
  }
  return col;
}

function buildMetricNumCol(cfg: ColumnConfig): ColumnDef {
  const metricKey = cfg.metric_key ?? cfg.source;
  const scale = cfg.scale ?? 1;
  const decimals = cfg.decimals ?? 1;
  const suffix = cfg.suffix ?? "";

  const col: ColumnDef = {
    key: cfg.source,
    header: cfg.header ?? cfg.source,
    type: "num",
    title: cfg.title,
    toggleable: cfg.toggleable ?? true,
    render: (row: Row): string => {
      const raw = metricsAccessor(metricKey)(row);
      if (raw === null) return "-";
      const v = raw * scale;
      return v.toFixed(decimals) + suffix;
    },
    sortValue: (row: Row): number | null => {
      const raw = metricsAccessor(metricKey)(row);
      return raw !== null ? raw * scale : null;
    },
  };
  if (cfg.stock_link) {
    col.stockLink = cfg.stock_link;
  }
  return col;
}

function buildPegCol(cfg: ColumnConfig): ColumnDef {
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
    render: (row: Row): string => {
      const value = toNumber(row[source]);
      if (value !== null) {
        return value.toFixed(decimals);
      }
      const status = typeof row[statusSource] === "string" ? (row[statusSource] as string) : null;
      if (status === null || status === "ok") return "-";
      return PEG_STATUS_LABELS[status] ?? "-";
    },
    sortValue: (row: Row): number | null => toNumber(row[source]),
  };
}

function buildBoolCol(cfg: ColumnConfig): ColumnDef {
  const source = cfg.source;

  return {
    key: source,
    header: cfg.header ?? source,
    type: "text",
    title: cfg.title,
    toggleable: cfg.toggleable ?? true,
    render: (row: Row): string => {
      const value = row[source];
      if (value === true) return "yes";
      if (value === false) return "no";
      return "-";
    },
    sortValue: (row: Row): number | null => {
      const value = row[source];
      if (value === true) return 1;
      if (value === false) return 0;
      return null;
    },
  };
}

function buildColumnsFromConfig(configs: ColumnConfig[]): ColumnDef[] {
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

const METRIC_THRESHOLDS: Record<string, MetricThreshold> = {
  ...C.COMMON_THRESHOLDS,
  pbr: { good: (v: number): boolean => v < 0.5 },
  dividend_yield: { good: (v: number): boolean => v >= 4 },
};

/* ------------------------------------------------------------------ */
/*  Bootstrap                                                          */
/* ------------------------------------------------------------------ */

async function bootstrap(): Promise<void> {
  const columnConfigUrl = IS_GITHUB_PAGES
    ? "assets/column-config.json"
    : "/assets/column-config.json";

  let columns: ColumnDef[];

  try {
    const response = await fetch(columnConfigUrl, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("HTTP " + response.status);
    }
    const configs: ColumnConfig[] = await response.json();
    columns = buildColumnsFromConfig(configs);
  } catch (err) {
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
    balanceSheetHistoryUrl: (code: string): string => IS_GITHUB_PAGES
      ? `assets/bs-history/${encodeURIComponent(code)}.json`
      : `/api/balance-sheet?code=${encodeURIComponent(code)}`,
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => void bootstrap());
} else {
  void bootstrap();
}
