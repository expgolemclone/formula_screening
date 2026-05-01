/**
 * Generic stock table renderer.
 *
 * Initialise via `StockTable.init(config)` after the DOM is ready.
 * Each page supplies a config object that declares columns, data
 * sources, metric thresholds, and optional tab/position behaviour.
 */
export type SortDirection = "asc" | "desc";
export interface MetricThreshold {
    good?: (v: number) => boolean;
    bad?: (v: number) => boolean;
}
export type ColumnType = "text" | "num" | "code" | "name" | "links" | "position";
export type LinkMode = "direct" | "browser" | "yazi";
export interface RenderContext {
    githubPages: boolean;
}
export interface ColumnDef {
    key: string;
    header: string;
    title?: string;
    type: ColumnType;
    render: (row: Record<string, unknown>) => string;
    sortValue?: (row: Record<string, unknown>) => number | null;
    cssClass?: string;
    linkHref?: (row: Record<string, unknown>, context: RenderContext) => string | null;
    linkMode?: LinkMode | ((row: Record<string, unknown>, context: RenderContext) => LinkMode);
    browserKey?: string;
    isPosition?: boolean;
    toggleable?: boolean;
}
export interface StockTableConfig {
    defaultTitle: string;
    dataUrl: string;
    columns: ColumnDef[];
    metricThresholds: Record<string, MetricThreshold>;
    defaultSortKey: string;
    defaultSortDirection: SortDirection;
    tabMode?: boolean;
    defaultTabKey?: string;
    githubPages?: boolean;
}
export declare const StockTable: {
    init: typeof init;
};
declare function init(config: StockTableConfig): void;
export {};
