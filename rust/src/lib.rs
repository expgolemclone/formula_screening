use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::Path;

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use stock_db_core::screening::{
    HistoricalItems, ItemMap, PotentialEquitySummary, ScreeningStock, StatementMap,
};

pub type MetricMap = HashMap<String, Option<f64>>;

#[derive(Debug, Clone)]
pub struct Stock {
    pub ticker: String,
    pub name: String,
    pub price: Option<f64>,
    pub price_date: Option<String>,
    pub shares_outstanding: Option<i64>,
    pub financials: StatementMap,
    pub metrics: MetricMap,
    pub cf_history: Vec<HistoricalItems>,
    pub pl_history: Vec<HistoricalItems>,
    pub dividend_history: Vec<HistoricalItems>,
    pub potential_equity_summary: PotentialEquitySummary,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Strategy {
    #[serde(default)]
    pub required_sources: Vec<String>,
    pub sort: Option<String>,
    pub filters: Vec<Filter>,
    #[serde(default)]
    pub columns: Vec<Column>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Filter {
    pub source: String,
    pub operator: String,
    pub threshold: Threshold,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(untagged)]
pub enum Threshold {
    Scalar(f64),
    Range([f64; 2]),
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Column {
    pub header: Option<String>,
    pub source: String,
    #[serde(rename = "type", skip_serializing_if = "Option::is_none")]
    pub column_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub format: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub decimals: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub scale: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub suffix: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub title: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub toggleable: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status_source: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metric_key: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stock_link: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct CfHistoryEntry {
    pub period: String,
    pub items: HashMap<String, Option<f64>>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScreeningPayload {
    pub code: String,
    pub name: String,
    pub price: Option<f64>,
    pub price_date: Option<String>,
    pub metrics: PublicMetrics,
    pub fcf_yield_avg: Option<f64>,
    pub peg_trailing_5: Option<f64>,
    pub peg_trailing_5_status: String,
    pub peg_blended_5y_actual_2f: Option<f64>,
    pub peg_blended_5y_actual_2f_status: String,
    pub has_preferred_shares: Option<bool>,
    pub has_potential_equity: Option<bool>,
    pub potential_common_shares: Option<f64>,
    pub has_unquantified_potential_equity: bool,
    pub croic: Option<f64>,
    pub fcf_cagr: Option<f64>,
    pub fcf_cagr_r2: Option<f64>,
    pub fcf_sma_cagr: Option<f64>,
    pub cf_history: Vec<CfHistoryEntry>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct MissingMetricDiagnostic {
    pub code: String,
    pub name: String,
    pub missing_fields: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScreeningRunResult {
    pub payload: Vec<ScreeningPayload>,
    pub diagnostics: Vec<MissingMetricDiagnostic>,
    pub column_config: Vec<Column>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PublicMetrics {
    pub net_cash_ratio: Option<f64>,
    pub per_actual: Option<f64>,
    pub per: Option<f64>,
    pub per_next: Option<f64>,
    pub equity_ratio: Option<f64>,
    pub dividend_yield: Option<f64>,
    pub total_payout_ratio: Option<f64>,
    pub retained_earnings_ratio: Option<f64>,
    pub pbr: Option<f64>,
    pub market_cap: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum PegStatus {
    Ok,
    MissingInput,
    InsufficientHistory,
    NonPositivePer,
    NonPositiveEps,
    NonPositiveGrowth,
}

impl PegStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Ok => "ok",
            Self::MissingInput => "missing_input",
            Self::InsufficientHistory => "insufficient_history",
            Self::NonPositivePer => "non_positive_per",
            Self::NonPositiveEps => "non_positive_eps",
            Self::NonPositiveGrowth => "non_positive_growth",
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct PegComputation {
    value: Option<f64>,
    status: PegStatus,
}

pub fn load_strategy(path: &Path) -> Result<Strategy, String> {
    if path.extension().and_then(|value| value.to_str()) != Some("toml") {
        return Err(format!("strategy files must be TOML: {}", path.display()));
    }
    let content = fs::read_to_string(path).map_err(|err| err.to_string())?;
    let strategy: Strategy = toml::from_str(&content).map_err(|err| err.to_string())?;
    validate_strategy(&strategy)?;
    Ok(strategy)
}

pub fn build_stock(raw: ScreeningStock) -> Stock {
    let metrics = compute_metrics(
        &raw.financials,
        raw.price,
        raw.shares_outstanding,
        &raw.cf_history,
        &raw.dividend_history,
    );
    Stock {
        ticker: raw.ticker,
        name: raw.name,
        price: raw.price,
        price_date: raw.price_date,
        shares_outstanding: raw.shares_outstanding,
        financials: raw.financials,
        metrics,
        cf_history: raw.cf_history,
        pl_history: raw.pl_history,
        dividend_history: raw.dividend_history,
        potential_equity_summary: raw.potential_equity_summary,
    }
}

pub fn run_strategy(strategy: &Strategy, stocks: Vec<Stock>) -> Vec<Stock> {
    run_strategy_with_mode(strategy, stocks, false)
}

pub fn run_strategy_with_mode(
    strategy: &Strategy,
    stocks: Vec<Stock>,
    return_all: bool,
) -> Vec<Stock> {
    let mut hits = if return_all {
        stocks
    } else {
        stocks
            .into_iter()
            .filter(|stock| strategy_matches(strategy, stock))
            .collect::<Vec<_>>()
    };
    if let Some(sort) = &strategy.sort {
        hits.sort_by(|left, right| {
            let left_value = resolve_numeric_value(left, sort).unwrap_or(f64::NEG_INFINITY);
            let right_value = resolve_numeric_value(right, sort).unwrap_or(f64::NEG_INFINITY);
            right_value.total_cmp(&left_value)
        });
    }
    hits
}

pub fn run_screening_payload(
    strategy_path: &Path,
    tickers: Option<&[String]>,
    return_all: bool,
) -> Result<Vec<ScreeningPayload>, String> {
    let strategy = load_strategy(strategy_path)?;
    let raw_stocks = stock_db_core::screening::load_default_screening_stocks(tickers, 10, 6, 10)?;
    let stocks = raw_stocks.into_iter().map(build_stock).collect::<Vec<_>>();
    run_strategy_with_mode(&strategy, stocks, return_all)
        .iter()
        .map(serialize_stock)
        .collect()
}

pub fn run_screening_payload_with_diagnostics(
    strategy_path: &Path,
    tickers: Option<&[String]>,
    return_all: bool,
) -> Result<ScreeningRunResult, String> {
    let strategy = load_strategy(strategy_path)?;
    let raw_stocks = stock_db_core::screening::load_default_screening_stocks(tickers, 10, 6, 10)?;
    let stocks = raw_stocks.into_iter().map(build_stock).collect::<Vec<_>>();
    let diagnostics = collect_missing_metric_diagnostics(&stocks)?;
    let payload = run_strategy_with_mode(&strategy, stocks, return_all)
        .iter()
        .map(serialize_stock)
        .collect::<Result<Vec<_>, _>>()?;
    Ok(ScreeningRunResult {
        payload,
        diagnostics,
        column_config: strategy.columns,
    })
}

pub fn serialize_stock(stock: &Stock) -> Result<ScreeningPayload, String> {
    let peg_trailing = peg_trailing_result(stock, 5);
    let peg_blended = peg_blended_2f_result(stock, 5);

    Ok(ScreeningPayload {
        code: stock.ticker.clone(),
        name: stock.name.clone(),
        price: stock.price,
        price_date: stock.price_date.clone(),
        metrics: PublicMetrics {
            net_cash_ratio: metric(stock, "net_cash_ratio"),
            per_actual: metric(stock, "per_actual"),
            per: metric(stock, "per"),
            per_next: metric(stock, "per_next"),
            equity_ratio: metric(stock, "equity_ratio"),
            dividend_yield: metric(stock, "dividend_yield"),
            total_payout_ratio: metric(stock, "total_payout_ratio"),
            retained_earnings_ratio: metric(stock, "retained_earnings_ratio"),
            pbr: metric(stock, "pbr"),
            market_cap: metric(stock, "market_cap"),
        },
        fcf_yield_avg: fcf_yield_avg(stock, 10),
        peg_trailing_5: peg_trailing.value,
        peg_trailing_5_status: peg_trailing.status.as_str().to_string(),
        peg_blended_5y_actual_2f: peg_blended.value,
        peg_blended_5y_actual_2f_status: peg_blended.status.as_str().to_string(),
        has_preferred_shares: preferred_share_flag(stock)?,
        has_potential_equity: stock.potential_equity_summary.has_potential_equity,
        potential_common_shares: stock.potential_equity_summary.total_potential_common_shares,
        has_unquantified_potential_equity: stock.potential_equity_summary.has_unquantified_terms,
        croic: croic(stock),
        fcf_cagr: fcf_cagr(stock, 10),
        fcf_cagr_r2: fcf_cagr_r2(stock, 10),
        fcf_sma_cagr: fcf_sma_cagr(stock, 10, 3),
        cf_history: stock
            .cf_history
            .iter()
            .map(|h| CfHistoryEntry {
                period: h.period.clone(),
                items: h.items.clone(),
            })
            .collect(),
    })
}

pub fn collect_missing_metric_diagnostics(
    stocks: &[Stock],
) -> Result<Vec<MissingMetricDiagnostic>, String> {
    stocks
        .iter()
        .map(|stock| {
            let mut missing_fields = Vec::new();
            if stock.price.is_none() {
                missing_fields.push("price".to_string());
            }
            for (field, value) in [
                ("metrics.net_cash_ratio", metric(stock, "net_cash_ratio")),
                ("metrics.per_actual", metric(stock, "per_actual")),
                ("metrics.per", metric(stock, "per")),
                ("metrics.per_next", metric(stock, "per_next")),
                ("fcf_yield_avg", fcf_yield_avg(stock, 10)),
                ("metrics.equity_ratio", metric(stock, "equity_ratio")),
                ("peg_trailing_5", peg_trailing(stock, 5)),
                ("peg_blended_5y_actual_2f", peg_blended_2f(stock, 5)),
                ("metrics.dividend_yield", metric(stock, "dividend_yield")),
                (
                    "metrics.total_payout_ratio",
                    metric(stock, "total_payout_ratio"),
                ),
                (
                    "metrics.retained_earnings_ratio",
                    metric(stock, "retained_earnings_ratio"),
                ),
            ] {
                if value.is_none() {
                    missing_fields.push(field.to_string());
                }
            }
            if preferred_share_flag(stock)?.is_none() {
                missing_fields.push("has_preferred_shares".to_string());
            }
            for (field, value) in [
                ("croic", croic(stock)),
                ("fcf_cagr", fcf_cagr(stock, 10)),
                ("fcf_cagr_r2", fcf_cagr_r2(stock, 10)),
                ("fcf_sma_cagr", fcf_sma_cagr(stock, 10, 3)),
                ("metrics.pbr", metric(stock, "pbr")),
                ("metrics.market_cap", metric(stock, "market_cap")),
            ] {
                if value.is_none() {
                    missing_fields.push(field.to_string());
                }
            }
            Ok(MissingMetricDiagnostic {
                code: stock.ticker.clone(),
                name: stock.name.clone(),
                missing_fields,
            })
        })
        .filter_map(
            |diagnostic: Result<MissingMetricDiagnostic, String>| match diagnostic {
                Ok(diagnostic) if diagnostic.missing_fields.is_empty() => None,
                result => Some(result),
            },
        )
        .collect()
}

pub fn compute_all_metrics()
-> Result<HashMap<String, HashMap<String, Option<PublicMetricValue>>>, String> {
    let raw_stocks = stock_db_core::screening::load_default_screening_stocks(None, 10, 6, 10)?;
    let mut result = HashMap::new();
    for raw_stock in raw_stocks {
        if raw_stock.financials.is_empty() {
            continue;
        }
        let stock = build_stock(raw_stock);
        let preferred_share_value = preferred_share_flag(&stock)?.map(PublicMetricValue::Bool);
        let potential_equity_value = stock
            .potential_equity_summary
            .has_potential_equity
            .map(PublicMetricValue::Bool);
        let peg_trailing = peg_trailing_result(&stock, 5);
        let peg_blended = peg_blended_2f_result(&stock, 5);
        result.insert(
            stock.ticker.clone(),
            HashMap::from([
                ("price".to_string(), metric_value(stock.price)),
                (
                    "price_date".to_string(),
                    stock.price_date.clone().map(PublicMetricValue::Text),
                ),
                (
                    "net_cash_ratio".to_string(),
                    metric_value(metric(&stock, "net_cash_ratio")),
                ),
                (
                    "per_actual".to_string(),
                    metric_value(metric(&stock, "per_actual")),
                ),
                ("per".to_string(), metric_value(metric(&stock, "per"))),
                (
                    "per_next".to_string(),
                    metric_value(metric(&stock, "per_next")),
                ),
                (
                    "fcf_yield_avg".to_string(),
                    metric_value(fcf_yield_avg(&stock, 10)),
                ),
                (
                    "equity_ratio".to_string(),
                    metric_value(metric(&stock, "equity_ratio")),
                ),
                (
                    "peg_trailing_5".to_string(),
                    metric_value(peg_trailing.value),
                ),
                (
                    "peg_trailing_5_status".to_string(),
                    Some(PublicMetricValue::Text(
                        peg_trailing.status.as_str().to_string(),
                    )),
                ),
                (
                    "peg_blended_5y_actual_2f".to_string(),
                    metric_value(peg_blended.value),
                ),
                (
                    "peg_blended_5y_actual_2f_status".to_string(),
                    Some(PublicMetricValue::Text(
                        peg_blended.status.as_str().to_string(),
                    )),
                ),
                (
                    "dividend_yield".to_string(),
                    metric_value(metric(&stock, "dividend_yield")),
                ),
                (
                    "total_payout_ratio".to_string(),
                    metric_value(metric(&stock, "total_payout_ratio")),
                ),
                (
                    "retained_earnings_ratio".to_string(),
                    metric_value(metric(&stock, "retained_earnings_ratio")),
                ),
                ("has_preferred_shares".to_string(), preferred_share_value),
                ("has_potential_equity".to_string(), potential_equity_value),
                (
                    "potential_common_shares".to_string(),
                    metric_value(stock.potential_equity_summary.total_potential_common_shares),
                ),
                (
                    "has_unquantified_potential_equity".to_string(),
                    Some(PublicMetricValue::Bool(
                        stock.potential_equity_summary.has_unquantified_terms,
                    )),
                ),
                ("croic".to_string(), metric_value(croic(&stock))),
                ("fcf_cagr".to_string(), metric_value(fcf_cagr(&stock, 10))),
                (
                    "fcf_cagr_r2".to_string(),
                    metric_value(fcf_cagr_r2(&stock, 10)),
                ),
                (
                    "fcf_sma_cagr".to_string(),
                    metric_value(fcf_sma_cagr(&stock, 10, 3)),
                ),
                ("pbr".to_string(), metric_value(metric(&stock, "pbr"))),
                (
                    "market_cap".to_string(),
                    metric_value(metric(&stock, "market_cap")),
                ),
            ]),
        );
    }
    Ok(result)
}

#[derive(Debug, Clone)]
pub enum PublicMetricValue {
    Float(f64),
    Bool(bool),
    Text(String),
}

const PUBLIC_METRIC_PY_ORDER: [&str; 25] = [
    "price",
    "price_date",
    "net_cash_ratio",
    "per_actual",
    "per",
    "per_next",
    "fcf_yield_avg",
    "equity_ratio",
    "peg_trailing_5",
    "peg_trailing_5_status",
    "peg_blended_5y_actual_2f",
    "peg_blended_5y_actual_2f_status",
    "dividend_yield",
    "total_payout_ratio",
    "retained_earnings_ratio",
    "has_preferred_shares",
    "has_potential_equity",
    "potential_common_shares",
    "has_unquantified_potential_equity",
    "croic",
    "fcf_cagr",
    "fcf_cagr_r2",
    "fcf_sma_cagr",
    "pbr",
    "market_cap",
];

fn metric_value(value: Option<f64>) -> Option<PublicMetricValue> {
    value.map(PublicMetricValue::Float)
}

pub fn strategy_matches(strategy: &Strategy, stock: &Stock) -> bool {
    strategy.filters.iter().all(|filter| {
        let Some(value) = resolve_numeric_value(stock, &filter.source) else {
            return false;
        };
        match (&*filter.operator, &filter.threshold) {
            (">", Threshold::Scalar(threshold)) => value > *threshold,
            (">=", Threshold::Scalar(threshold)) => value >= *threshold,
            ("<", Threshold::Scalar(threshold)) => value < *threshold,
            ("<=", Threshold::Scalar(threshold)) => value <= *threshold,
            ("between", Threshold::Range([lo, hi])) => *lo < value && value < *hi,
            _ => false,
        }
    })
}

pub fn compute_metrics(
    financials: &StatementMap,
    price: Option<f64>,
    shares_outstanding: Option<i64>,
    cf_history: &[HistoricalItems],
    dividend_history: &[HistoricalItems],
) -> MetricMap {
    let empty = ItemMap::new();
    let pl = financials.get("pl").unwrap_or(&empty);
    let bs = financials.get("bs").unwrap_or(&empty);
    let cf = financials.get("cf").unwrap_or(&empty);
    let forecast = financials.get("forecast").unwrap_or(&empty);
    let dividend = financials.get("dividend").unwrap_or(&empty);

    let shares = shares_outstanding.map(|value| value as f64);
    let market_cap = match (price, shares) {
        (Some(price), Some(shares)) if price != 0.0 && shares != 0.0 => Some(price * shares),
        _ => None,
    };
    let revenue = item(pl, "revenue");
    let operating_income = item(pl, "operating_income");
    let ordinary_income = item(pl, "ordinary_income");
    let net_income = item(pl, "net_income");
    let total_assets = item(bs, "total_assets");
    let stockholders_equity = item(bs, "stockholders_equity");
    let retained_earnings = item(bs, "retained_earnings");
    let total_equity = item(bs, "total_equity");
    let total_debt = item(bs, "total_debt");
    let gross_profit = match (revenue, item(pl, "cost_of_revenue")) {
        (Some(revenue), Some(cost_of_revenue)) => Some(revenue - cost_of_revenue),
        _ => None,
    };
    let free_cf = item(cf, "free_cf").or_else(|| {
        match (item(cf, "operating_cf"), item(cf, "investing_cf")) {
            (Some(operating_cf), Some(investing_cf)) => Some(operating_cf + investing_cf),
            _ => None,
        }
    });
    let total_liabilities = match (total_assets, total_equity) {
        (Some(total_assets), Some(total_equity)) => Some(total_assets - total_equity),
        _ => None,
    };
    let interest_bearing_debt = Some(
        item(bs, "short_term_debt").unwrap_or(0.0) + item(bs, "long_term_debt").unwrap_or(0.0),
    );
    let net_cash = match (
        item(bs, "current_assets"),
        item(bs, "current_liabilities"),
        item(bs, "non_current_liabilities"),
    ) {
        (Some(current_assets), Some(current_liabilities), Some(non_current_liabilities)) => {
            let mut value = current_assets - current_liabilities - non_current_liabilities;
            if let Some(inventories) = item(bs, "inventories") {
                value -= inventories;
            }
            if let Some(investment_securities) = item(bs, "investment_securities") {
                value += investment_securities * 0.7;
            }
            Some(value)
        }
        _ => None,
    };

    HashMap::from([
        ("market_cap".to_string(), market_cap),
        (
            "per".to_string(),
            safe_div(market_cap, item(forecast, "net_income_current")),
        ),
        (
            "per_next".to_string(),
            safe_div(market_cap, item(forecast, "net_income_next")),
        ),
        ("per_actual".to_string(), safe_div(market_cap, net_income)),
        ("pbr".to_string(), safe_div(market_cap, total_equity)),
        (
            "dividend_yield".to_string(),
            pct(item(dividend, "dps"), price),
        ),
        (
            "total_payout_ratio".to_string(),
            total_payout_ratio(cf_history, dividend_history, market_cap),
        ),
        (
            "retained_earnings_ratio".to_string(),
            safe_div(retained_earnings, market_cap),
        ),
        ("gross_margin".to_string(), pct(gross_profit, revenue)),
        (
            "operating_margin".to_string(),
            pct(operating_income, revenue),
        ),
        ("ordinary_margin".to_string(), pct(ordinary_income, revenue)),
        ("net_income_margin".to_string(), pct(net_income, revenue)),
        ("roe".to_string(), pct(net_income, stockholders_equity)),
        ("roa".to_string(), pct(net_income, total_assets)),
        (
            "equity_ratio".to_string(),
            pct(stockholders_equity, total_assets),
        ),
        (
            "debt_equity_ratio".to_string(),
            pct(total_debt, stockholders_equity),
        ),
        (
            "operating_cf_margin".to_string(),
            pct(item(cf, "operating_cf"), revenue),
        ),
        ("free_cf".to_string(), free_cf),
        ("free_cf_ratio".to_string(), pct(free_cf, revenue)),
        ("total_liabilities".to_string(), total_liabilities),
        ("interest_bearing_debt".to_string(), interest_bearing_debt),
        ("net_cash".to_string(), net_cash),
        ("net_cash_ratio".to_string(), safe_div(net_cash, market_cap)),
    ])
}

fn validate_strategy(strategy: &Strategy) -> Result<(), String> {
    if strategy.filters.is_empty() {
        return Err("strategy must define at least one filter".to_string());
    }
    let valid_sources = valid_sources();
    for filter in &strategy.filters {
        if !valid_sources.contains(filter.source.as_str()) {
            return Err(format!("unknown strategy source: {}", filter.source));
        }
        match (&*filter.operator, &filter.threshold) {
            (">" | ">=" | "<" | "<=", Threshold::Scalar(_)) | ("between", Threshold::Range(_)) => {}
            _ => return Err(format!("invalid strategy filter: {:?}", filter)),
        }
    }
    if let Some(sort) = &strategy.sort
        && !valid_sources.contains(sort.as_str())
    {
        return Err(format!("unknown strategy source: {sort}"));
    }
    let web_only_sources: HashSet<&str> = HashSet::from([
        "code",
        "name",
        "price",
        "has_preferred_shares",
        "has_potential_equity",
        "potential_common_shares",
        "has_unquantified_potential_equity",
    ]);
    for column in &strategy.columns {
        if !web_only_sources.contains(column.source.as_str())
            && !valid_sources.contains(column.source.as_str())
        {
            return Err(format!("unknown strategy source: {}", column.source));
        }
    }
    Ok(())
}

fn valid_sources() -> HashSet<&'static str> {
    HashSet::from([
        "market_cap",
        "per",
        "per_next",
        "per_actual",
        "pbr",
        "dividend_yield",
        "total_payout_ratio",
        "retained_earnings_ratio",
        "gross_margin",
        "operating_margin",
        "ordinary_margin",
        "net_income_margin",
        "roe",
        "roa",
        "equity_ratio",
        "debt_equity_ratio",
        "operating_cf_margin",
        "free_cf",
        "free_cf_ratio",
        "total_liabilities",
        "interest_bearing_debt",
        "net_cash",
        "net_cash_ratio",
        "fcf_yield_avg",
        "croic",
        "fcf_cagr",
        "fcf_cagr_r2",
        "fcf_sma_cagr",
        "peg_trailing_5",
        "peg_blended_5y_actual_2f",
        "preferred_share_label",
    ])
}

fn resolve_numeric_value(stock: &Stock, source: &str) -> Option<f64> {
    match source {
        "fcf_yield_avg" => fcf_yield_avg(stock, 10),
        "croic" => croic(stock),
        "fcf_cagr" => fcf_cagr(stock, 10),
        "fcf_cagr_r2" => fcf_cagr_r2(stock, 10),
        "fcf_sma_cagr" => fcf_sma_cagr(stock, 10, 3),
        "peg_trailing_5" => peg_trailing(stock, 5),
        "peg_blended_5y_actual_2f" => peg_blended_2f(stock, 5),
        _ => metric(stock, source),
    }
}

fn metric(stock: &Stock, key: &str) -> Option<f64> {
    stock.metrics.get(key).copied().flatten()
}

fn item(items: &ItemMap, key: &str) -> Option<f64> {
    items.get(key).copied().flatten()
}

fn safe_div(left: Option<f64>, right: Option<f64>) -> Option<f64> {
    match (left, right) {
        (Some(left), Some(right)) if right != 0.0 => Some(left / right),
        _ => None,
    }
}

fn pct(left: Option<f64>, right: Option<f64>) -> Option<f64> {
    safe_div(left, right).map(|value| value * 100.0)
}

fn total_payout_ratio(
    cf_history: &[HistoricalItems],
    dividend_history: &[HistoricalItems],
    market_cap: Option<f64>,
) -> Option<f64> {
    let market_cap = market_cap?;
    if market_cap <= 0.0 {
        return None;
    }

    let mut payout_total = 0.0;
    let mut has_payout = false;
    for items in cf_history {
        if let Some(value) = items
            .items
            .get("treasury_stock_purchase")
            .copied()
            .flatten()
        {
            payout_total += value.abs();
            has_payout = true;
        }
    }
    for items in dividend_history {
        if let Some(value) = items.items.get("dividend_payment").copied().flatten() {
            payout_total += value.abs();
            has_payout = true;
        }
    }
    has_payout.then_some(payout_total / market_cap * 100.0)
}

fn resolve_free_cf(items: &ItemMap) -> Option<f64> {
    item(items, "free_cf").or_else(|| {
        match (item(items, "operating_cf"), item(items, "investing_cf")) {
            (Some(operating_cf), Some(investing_cf)) => Some(operating_cf + investing_cf),
            _ => None,
        }
    })
}

/// Simple linear regression y = α + βx (x = 0,1,...,n-1).
/// Returns (slope β, R²). Returns None if fewer than 2 points or denominator is zero.
fn linreg_slope_r2(y_values: &[f64]) -> Option<(f64, f64)> {
    let n = y_values.len();
    if n < 2 {
        return None;
    }
    let n_f = n as f64;
    let s_x = (0..n).map(|i| i as f64).sum::<f64>();
    let s_y: f64 = y_values.iter().copied().sum();
    let s_xx: f64 = (0..n).map(|i| (i as f64).powi(2)).sum();
    let s_xy: f64 = (0..n)
        .zip(y_values.iter().copied())
        .map(|(i, y)| i as f64 * y)
        .sum();
    let s_yy: f64 = y_values.iter().map(|y| y * y).sum();
    let denom = n_f * s_xx - s_x * s_x;
    if denom == 0.0 {
        return None;
    }
    let slope = (n_f * s_xy - s_x * s_y) / denom;
    let denom_r2 = (n_f * s_xx - s_x * s_x) * (n_f * s_yy - s_y * s_y);
    if denom_r2 <= 0.0 {
        return Some((slope, 0.0));
    }
    let r2 = (n_f * s_xy - s_x * s_y).powi(2) / denom_r2;
    Some((slope, r2))
}

/// Collect positive FCF values from cf_history (oldest first) for regression.
fn collect_fcf_values(stock: &Stock, years: usize) -> Option<Vec<f64>> {
    let history: Vec<f64> = stock
        .cf_history
        .iter()
        .take(years)
        .filter_map(|period| resolve_free_cf(&period.items))
        .collect();
    if history.len() < years {
        return None;
    }
    Some(history.into_iter().rev().collect()) // oldest first
}

/// Linear regression growth rate: slope / |mean| * 100.
/// Returns None if mean is zero (degenerate case).
fn linear_cagr_pct(values: &[f64]) -> Option<f64> {
    let (slope, _) = linreg_slope_r2(values)?;
    let mean = values.iter().sum::<f64>() / values.len() as f64;
    if mean == 0.0 {
        return None;
    }
    Some((slope / mean.abs()) * 100.0)
}

pub fn fcf_cagr(stock: &Stock, years: usize) -> Option<f64> {
    let values = collect_fcf_values(stock, years)?;
    if values.iter().all(|v| *v > 0.0) {
        let log_values: Vec<f64> = values.iter().map(|v| v.ln()).collect();
        let (slope, _) = linreg_slope_r2(&log_values)?;
        Some((slope.exp() - 1.0) * 100.0)
    } else {
        linear_cagr_pct(&values)
    }
}

pub fn fcf_cagr_r2(stock: &Stock, years: usize) -> Option<f64> {
    let values = collect_fcf_values(stock, years)?;
    let reg_values: Vec<f64> = if values.iter().all(|v| *v > 0.0) {
        values.iter().map(|v| v.ln()).collect()
    } else {
        values
    };
    let (_, r2) = linreg_slope_r2(&reg_values)?;
    Some(r2)
}

pub fn fcf_sma_cagr(stock: &Stock, years: usize, sma_window: usize) -> Option<f64> {
    if sma_window < 1 {
        return None;
    }
    let values = collect_fcf_values(stock, years)?;
    if values.len() < sma_window {
        return None;
    }
    let sma_count = values.len() - sma_window + 1;
    if sma_count < 2 {
        return None;
    }
    let mut sma_values = Vec::with_capacity(sma_count);
    for i in 0..sma_count {
        let avg: f64 = values[i..i + sma_window].iter().sum::<f64>() / sma_window as f64;
        sma_values.push(avg);
    }
    let first = *sma_values.first()?;
    let last = *sma_values.last()?;
    let n_years = (sma_count - 1) as f64;
    if first > 0.0 && last > 0.0 {
        Some((last / first).powf(1.0 / n_years) - 1.0)
    } else if first == 0.0 {
        None
    } else {
        Some((last - first) / first.abs() / n_years)
    }
}

pub fn fcf_yield_avg(stock: &Stock, years: usize) -> Option<f64> {
    let market_cap = metric(stock, "market_cap")?;
    if market_cap <= 0.0 {
        return None;
    }
    let values = stock
        .cf_history
        .iter()
        .take(years)
        .filter_map(|period| resolve_free_cf(&period.items).map(|value| value / market_cap))
        .collect::<Vec<_>>();
    if values.len() < years {
        return None;
    }
    Some(values.iter().sum::<f64>() / values.len() as f64)
}

pub fn croic(stock: &Stock) -> Option<f64> {
    let free_cf = metric(stock, "free_cf")?;
    let bs = stock.financials.get("bs")?;
    let invested_capital =
        item(bs, "stockholders_equity")? + metric(stock, "interest_bearing_debt")?;
    if invested_capital <= 0.0 {
        return None;
    }
    Some(free_cf / invested_capital)
}

pub fn peg_trailing(stock: &Stock, years: usize) -> Option<f64> {
    peg_trailing_result(stock, years).value
}

fn peg_trailing_result(stock: &Stock, years: usize) -> PegComputation {
    if years < 1 {
        return PegComputation {
            value: None,
            status: PegStatus::InsufficientHistory,
        };
    }
    let Some(per_actual) = metric(stock, "per_actual") else {
        return PegComputation {
            value: None,
            status: PegStatus::MissingInput,
        };
    };
    if per_actual <= 0.0 {
        return PegComputation {
            value: None,
            status: PegStatus::NonPositivePer,
        };
    }
    if stock.pl_history.len() < years + 1 {
        return PegComputation {
            value: None,
            status: PegStatus::InsufficientHistory,
        };
    }
    let recent = &stock.pl_history[..years + 1];
    let mut eps_values = Vec::with_capacity(recent.len());
    for period in recent {
        let Some(eps) = item(&period.items, "eps") else {
            return PegComputation {
                value: None,
                status: PegStatus::MissingInput,
            };
        };
        if eps <= 0.0 {
            return PegComputation {
                value: None,
                status: PegStatus::NonPositiveEps,
            };
        }
        eps_values.push(eps);
    }
    let latest_eps = eps_values[0];
    let oldest_eps = eps_values[years];
    let cagr = (latest_eps / oldest_eps).powf(1.0 / years as f64) - 1.0;
    if cagr <= 0.0 {
        return PegComputation {
            value: None,
            status: PegStatus::NonPositiveGrowth,
        };
    }
    PegComputation {
        value: Some(per_actual / (cagr * 100.0)),
        status: PegStatus::Ok,
    }
}

pub fn peg_blended_2f(stock: &Stock, actual_years: usize) -> Option<f64> {
    peg_blended_2f_result(stock, actual_years).value
}

fn peg_blended_2f_result(stock: &Stock, actual_years: usize) -> PegComputation {
    if actual_years < 1 || stock.pl_history.len() < actual_years + 1 {
        return PegComputation {
            value: None,
            status: PegStatus::InsufficientHistory,
        };
    }
    let Some(per_next) = metric(stock, "per_next") else {
        return PegComputation {
            value: None,
            status: PegStatus::MissingInput,
        };
    };
    if per_next <= 0.0 {
        return PegComputation {
            value: None,
            status: PegStatus::NonPositivePer,
        };
    }
    let Some(forecast) = stock.financials.get("forecast") else {
        return PegComputation {
            value: None,
            status: PegStatus::MissingInput,
        };
    };
    let (Some(eps_current), Some(eps_next)) =
        (item(forecast, "eps_current"), item(forecast, "eps_next"))
    else {
        return PegComputation {
            value: None,
            status: PegStatus::MissingInput,
        };
    };
    if eps_current <= 0.0 || eps_next <= 0.0 {
        return PegComputation {
            value: None,
            status: PegStatus::NonPositiveEps,
        };
    }
    let recent = &stock.pl_history[..actual_years + 1];
    for period in recent {
        let Some(eps) = item(&period.items, "eps") else {
            return PegComputation {
                value: None,
                status: PegStatus::MissingInput,
            };
        };
        if eps <= 0.0 {
            return PegComputation {
                value: None,
                status: PegStatus::NonPositiveEps,
            };
        }
    }
    let Some(oldest_actual_eps) = item(&recent[actual_years].items, "eps") else {
        return PegComputation {
            value: None,
            status: PegStatus::MissingInput,
        };
    };
    let total_periods = actual_years + 2;
    let cagr = (eps_next / oldest_actual_eps).powf(1.0 / total_periods as f64) - 1.0;
    if cagr <= 0.0 {
        return PegComputation {
            value: None,
            status: PegStatus::NonPositiveGrowth,
        };
    }
    PegComputation {
        value: Some(per_next / (cagr * 100.0)),
        status: PegStatus::Ok,
    }
}

pub fn preferred_share_flag(stock: &Stock) -> Result<Option<bool>, String> {
    let Some(bs) = stock.financials.get("bs") else {
        return Ok(None);
    };
    match item(bs, "has_preferred_shares") {
        None => Ok(None),
        Some(1.0) => Ok(Some(true)),
        Some(0.0) => Ok(Some(false)),
        Some(value) => Err(format!(
            "bs.has_preferred_shares must be 1.0, 0.0, or None: {value:?}"
        )),
    }
}

#[pyfunction]
fn compute_all_stock_metrics(py: Python<'_>) -> PyResult<PyObject> {
    let metrics = py
        .allow_threads(compute_all_metrics)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    metrics_to_py(py, &metrics)
}

#[pyfunction]
#[pyo3(signature = (strategy_path, tickers=None, return_all=false))]
fn run_screening_payload_py(
    py: Python<'_>,
    strategy_path: String,
    tickers: Option<Vec<String>>,
    return_all: bool,
) -> PyResult<PyObject> {
    let payload = py
        .allow_threads(|| {
            run_screening_payload(Path::new(&strategy_path), tickers.as_deref(), return_all)
        })
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    payloads_to_py(py, &payload)
}

#[pyfunction]
#[pyo3(signature = (strategy_path, tickers=None, return_all=false))]
fn run_screening_payload_with_diagnostics_py(
    py: Python<'_>,
    strategy_path: String,
    tickers: Option<Vec<String>>,
    return_all: bool,
) -> PyResult<PyObject> {
    let result = py
        .allow_threads(|| {
            run_screening_payload_with_diagnostics(
                Path::new(&strategy_path),
                tickers.as_deref(),
                return_all,
            )
        })
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    run_result_to_py(py, &result)
}

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compute_all_stock_metrics, m)?)?;
    m.add_function(wrap_pyfunction!(run_screening_payload_py, m)?)?;
    m.add_function(wrap_pyfunction!(
        run_screening_payload_with_diagnostics_py,
        m
    )?)?;
    Ok(())
}

fn metrics_to_py(
    py: Python<'_>,
    metrics: &HashMap<String, HashMap<String, Option<PublicMetricValue>>>,
) -> PyResult<PyObject> {
    let outer = pyo3::types::PyDict::new(py);
    for (ticker, values) in metrics {
        let inner = pyo3::types::PyDict::new(py);
        for key in PUBLIC_METRIC_PY_ORDER {
            let value = values.get(key).ok_or_else(|| {
                pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "missing public metric key: {key}"
                ))
            })?;
            match value {
                Some(PublicMetricValue::Float(number)) => inner.set_item(key, number)?,
                Some(PublicMetricValue::Bool(flag)) => inner.set_item(key, flag)?,
                Some(PublicMetricValue::Text(text)) => inner.set_item(key, text)?,
                None => inner.set_item(key, py.None())?,
            }
        }
        outer.set_item(ticker, inner)?;
    }
    Ok(outer.into())
}

fn payloads_to_py(py: Python<'_>, payloads: &[ScreeningPayload]) -> PyResult<PyObject> {
    let rows = pyo3::types::PyList::empty(py);
    for payload in payloads {
        let row = pyo3::types::PyDict::new(py);
        row.set_item("code", &payload.code)?;
        row.set_item("name", &payload.name)?;
        set_optional_float(py, &row, "price", payload.price)?;
        match &payload.price_date {
            Some(value) => row.set_item("price_date", value)?,
            None => row.set_item("price_date", py.None())?,
        }

        let metrics = pyo3::types::PyDict::new(py);
        set_optional_float(
            py,
            &metrics,
            "net_cash_ratio",
            payload.metrics.net_cash_ratio,
        )?;
        set_optional_float(py, &metrics, "per_actual", payload.metrics.per_actual)?;
        set_optional_float(py, &metrics, "per", payload.metrics.per)?;
        set_optional_float(py, &metrics, "per_next", payload.metrics.per_next)?;
        set_optional_float(py, &metrics, "equity_ratio", payload.metrics.equity_ratio)?;
        set_optional_float(
            py,
            &metrics,
            "dividend_yield",
            payload.metrics.dividend_yield,
        )?;
        set_optional_float(
            py,
            &metrics,
            "total_payout_ratio",
            payload.metrics.total_payout_ratio,
        )?;
        set_optional_float(
            py,
            &metrics,
            "retained_earnings_ratio",
            payload.metrics.retained_earnings_ratio,
        )?;
        set_optional_float(py, &metrics, "pbr", payload.metrics.pbr)?;
        set_optional_float(py, &metrics, "market_cap", payload.metrics.market_cap)?;
        row.set_item("metrics", metrics)?;

        set_optional_float(py, &row, "fcf_yield_avg", payload.fcf_yield_avg)?;
        set_optional_float(py, &row, "peg_trailing_5", payload.peg_trailing_5)?;
        row.set_item("peg_trailing_5_status", &payload.peg_trailing_5_status)?;
        set_optional_float(
            py,
            &row,
            "peg_blended_5y_actual_2f",
            payload.peg_blended_5y_actual_2f,
        )?;
        row.set_item(
            "peg_blended_5y_actual_2f_status",
            &payload.peg_blended_5y_actual_2f_status,
        )?;
        match payload.has_preferred_shares {
            Some(value) => row.set_item("has_preferred_shares", value)?,
            None => row.set_item("has_preferred_shares", py.None())?,
        }
        match payload.has_potential_equity {
            Some(value) => row.set_item("has_potential_equity", value)?,
            None => row.set_item("has_potential_equity", py.None())?,
        }
        set_optional_float(
            py,
            &row,
            "potential_common_shares",
            payload.potential_common_shares,
        )?;
        row.set_item(
            "has_unquantified_potential_equity",
            payload.has_unquantified_potential_equity,
        )?;
        set_optional_float(py, &row, "croic", payload.croic)?;
        set_optional_float(py, &row, "fcf_cagr", payload.fcf_cagr)?;
        set_optional_float(py, &row, "fcf_cagr_r2", payload.fcf_cagr_r2)?;
        set_optional_float(py, &row, "fcf_sma_cagr", payload.fcf_sma_cagr)?;

        let cf_list = pyo3::types::PyList::empty(py);
        for entry in &payload.cf_history {
            let item = pyo3::types::PyDict::new(py);
            item.set_item("period", &entry.period)?;
            let items_dict = pyo3::types::PyDict::new(py);
            for (k, v) in &entry.items {
                match v {
                    Some(val) => items_dict.set_item(k, *val)?,
                    None => items_dict.set_item(k, py.None())?,
                }
            }
            item.set_item("items", items_dict)?;
            cf_list.append(item)?;
        }
        row.set_item("cf_history", cf_list)?;

        rows.append(row)?;
    }
    Ok(rows.into())
}

fn run_result_to_py(py: Python<'_>, result: &ScreeningRunResult) -> PyResult<PyObject> {
    let row = pyo3::types::PyDict::new(py);
    row.set_item("payload", payloads_to_py(py, &result.payload)?)?;

    let diagnostics = pyo3::types::PyList::empty(py);
    for diagnostic in &result.diagnostics {
        let item = pyo3::types::PyDict::new(py);
        item.set_item("code", &diagnostic.code)?;
        item.set_item("name", &diagnostic.name)?;
        item.set_item("missing_fields", &diagnostic.missing_fields)?;
        diagnostics.append(item)?;
    }
    row.set_item("diagnostics", diagnostics)?;

    let column_config_json = serde_json::to_string(&result.column_config).map_err(|err| {
        pyo3::exceptions::PyRuntimeError::new_err(format!(
            "failed to serialize column_config: {err}"
        ))
    })?;
    let json_module = py.import("json")?;
    let column_config_py = json_module.call_method1("loads", (column_config_json,))?;
    row.set_item("column_config", column_config_py)?;

    Ok(row.into())
}

fn set_optional_float(
    py: Python<'_>,
    row: &Bound<'_, pyo3::types::PyDict>,
    key: &str,
    value: Option<f64>,
) -> PyResult<()> {
    match value {
        Some(value) => row.set_item(key, value),
        None => row.set_item(key, py.None()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_stock() -> Stock {
        let financials = HashMap::from([
            (
                "pl".to_string(),
                HashMap::from([
                    ("revenue".to_string(), Some(100_000_000_000.0)),
                    ("operating_income".to_string(), Some(10_000_000_000.0)),
                    ("ordinary_income".to_string(), Some(9_000_000_000.0)),
                    ("net_income".to_string(), Some(6_000_000_000.0)),
                ]),
            ),
            (
                "bs".to_string(),
                HashMap::from([
                    ("total_assets".to_string(), Some(50_000_000_000.0)),
                    ("stockholders_equity".to_string(), Some(25_000_000_000.0)),
                    ("retained_earnings".to_string(), Some(4_000_000_000.0)),
                    ("total_equity".to_string(), Some(25_000_000_000.0)),
                    ("total_debt".to_string(), Some(10_000_000_000.0)),
                    ("current_assets".to_string(), Some(20_000_000_000.0)),
                    ("current_liabilities".to_string(), Some(8_000_000_000.0)),
                    ("non_current_liabilities".to_string(), Some(5_000_000_000.0)),
                    ("has_preferred_shares".to_string(), Some(1.0)),
                ]),
            ),
            (
                "cf".to_string(),
                HashMap::from([
                    ("operating_cf".to_string(), Some(8_000_000_000.0)),
                    ("investing_cf".to_string(), Some(-3_000_000_000.0)),
                    (
                        "treasury_stock_purchase".to_string(),
                        Some(-1_000_000_000.0),
                    ),
                ]),
            ),
            (
                "dividend".to_string(),
                HashMap::from([("dps".to_string(), Some(50.0))]),
            ),
            (
                "forecast".to_string(),
                HashMap::from([
                    ("net_income_current".to_string(), Some(7_000_000_000.0)),
                    ("net_income_next".to_string(), Some(8_000_000_000.0)),
                    ("eps_current".to_string(), Some(220.0)),
                    ("eps_next".to_string(), Some(240.0)),
                ]),
            ),
        ]);
        let cf_history: Vec<HistoricalItems> = (0..10)
            .map(|year| {
                let mut items = HashMap::new();
                items.insert("free_cf".to_string(), Some(10.0 - year as f64));
                if year == 0 {
                    items.insert(
                        "treasury_stock_purchase".to_string(),
                        Some(-1_000_000_000.0),
                    );
                }
                HistoricalItems {
                    period: format!("20{:02}-03", 25 - year),
                    items,
                }
            })
            .collect();
        let dividend_history = vec![HistoricalItems {
            period: "2025-03".to_string(),
            items: HashMap::from([("dividend_payment".to_string(), Some(-2_000_000_000.0))]),
        }];
        let metrics = compute_metrics(
            &financials,
            Some(1000.0),
            Some(10_000_000),
            &cf_history,
            &dividend_history,
        );
        Stock {
            ticker: "1301".to_string(),
            name: "test".to_string(),
            price: Some(1000.0),
            price_date: Some("2026-05-20".to_string()),
            shares_outstanding: Some(10_000_000),
            financials,
            metrics,
            cf_history,
            pl_history: vec![
                ("2025-03", 200.0),
                ("2024-03", 180.0),
                ("2023-03", 160.0),
                ("2022-03", 140.0),
                ("2021-03", 120.0),
                ("2020-03", 100.0),
            ]
            .into_iter()
            .map(|(period, eps)| HistoricalItems {
                period: period.to_string(),
                items: HashMap::from([("eps".to_string(), Some(eps))]),
            })
            .collect(),
            dividend_history,
            potential_equity_summary: PotentialEquitySummary {
                has_potential_equity: Some(true),
                total_potential_common_shares: Some(100_000.0),
                has_unquantified_terms: true,
                instrument_types: vec![
                    "share_acquisition_right".to_string(),
                    "other_potential_equity".to_string(),
                ],
            },
        }
    }

    #[test]
    fn metrics_match_expected_per_values() {
        let stock = sample_stock();
        assert_eq!(metric(&stock, "market_cap"), Some(10_000_000_000.0));
        assert_eq!(
            metric(&stock, "per"),
            Some(10_000_000_000.0 / 7_000_000_000.0)
        );
        assert_eq!(
            metric(&stock, "per_next"),
            Some(10_000_000_000.0 / 8_000_000_000.0)
        );
        assert_eq!(metric(&stock, "retained_earnings_ratio"), Some(0.4));
    }

    #[test]
    fn net_cash_ratio_subtracts_current_and_non_current_liabilities() {
        let stock = sample_stock();
        assert_eq!(metric(&stock, "net_cash"), Some(7_000_000_000.0));
        assert_eq!(metric(&stock, "net_cash_ratio"), Some(0.7));
    }

    #[test]
    fn total_payout_ratio_uses_dividends_and_treasury_stock_purchase() {
        let stock = sample_stock();
        assert_eq!(metric(&stock, "total_payout_ratio"), Some(30.0));
    }

    #[test]
    fn indicators_and_preferred_share_flag_match_python_contract() {
        let stock = sample_stock();
        assert!(fcf_yield_avg(&stock, 10).is_some());
        assert!(peg_trailing(&stock, 5).is_some());
        assert!(peg_blended_2f(&stock, 5).is_some());
        assert_eq!(preferred_share_flag(&stock), Ok(Some(true)));
    }

    #[test]
    fn missing_metric_diagnostics_skip_complete_stocks() {
        let stock = sample_stock();

        assert_eq!(collect_missing_metric_diagnostics(&[stock]), Ok(vec![]));
    }

    #[test]
    fn missing_metric_diagnostics_report_public_payload_fields() {
        let mut stock = sample_stock();
        stock.price = None;
        stock.metrics.insert("per".to_string(), None);
        stock
            .financials
            .get_mut("bs")
            .expect("sample stock has bs")
            .remove("has_preferred_shares");

        assert_eq!(
            collect_missing_metric_diagnostics(&[stock]),
            Ok(vec![MissingMetricDiagnostic {
                code: "1301".to_string(),
                name: "test".to_string(),
                missing_fields: vec![
                    "price".to_string(),
                    "metrics.per".to_string(),
                    "has_preferred_shares".to_string(),
                ],
            }])
        );
    }

    #[test]
    fn fcf_cagr_returns_positive_growth_for_linearly_increasing_fcf() {
        let stock = sample_stock();
        // cf_history: 10, 9, 8, ..., 1 (most recent first)
        // oldest first: 1, 2, 3, ..., 10 → constant ~10% annual growth
        let cagr = fcf_cagr(&stock, 10).expect("fcf_cagr should return a value");
        assert!(cagr > 0.0, "cagr should be positive, got {cagr}");
    }

    #[test]
    fn fcf_cagr_r2_is_high_for_consistent_growth() {
        let stock = sample_stock();
        let r2 = fcf_cagr_r2(&stock, 10).expect("fcf_cagr_r2 should return a value");
        assert!(
            r2 > 0.9,
            "R² should be close to 1 for linear growth, got {r2}"
        );
    }

    #[test]
    fn fcf_sma_cagr_returns_positive_growth() {
        let stock = sample_stock();
        let sma = fcf_sma_cagr(&stock, 10, 3).expect("fcf_sma_cagr should return a value");
        assert!(sma > 0.0, "sma_cagr should be positive, got {sma}");
    }

    #[test]
    fn fcf_cagr_returns_linear_fallback_for_negative_fcf() {
        let mut stock = sample_stock();
        // Insert a negative FCF value in the history
        stock.cf_history[3]
            .items
            .insert("free_cf".to_string(), Some(-5.0));
        // Should now use linear regression fallback instead of returning None
        let cagr = fcf_cagr(&stock, 10);
        assert!(
            cagr.is_some(),
            "fcf_cagr should return a value even with negative FCF"
        );
        let r2 = fcf_cagr_r2(&stock, 10);
        assert!(
            r2.is_some(),
            "fcf_cagr_r2 should return a value even with negative FCF"
        );
    }

    #[test]
    fn fcf_sma_cagr_returns_none_for_insufficient_data() {
        let mut stock = sample_stock();
        stock.cf_history.truncate(3); // Only 3 periods, need at least sma_window + 1 = 4
        assert!(fcf_sma_cagr(&stock, 10, 3).is_none());
    }

    #[test]
    fn linreg_slope_r2_perfect_linear() {
        // y = 2x + 1 → slope = 2, R² = 1
        let (slope, r2) = linreg_slope_r2(&[1.0, 3.0, 5.0, 7.0]).unwrap();
        assert!((slope - 2.0).abs() < 1e-9, "slope should be 2, got {slope}");
        assert!((r2 - 1.0).abs() < 1e-9, "R² should be 1, got {r2}");
    }
}
