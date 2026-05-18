use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::Path;

use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use stock_db_core::screening::{HistoricalItems, ItemMap, ScreeningStock, StatementMap};

pub type MetricMap = HashMap<String, Option<f64>>;

#[derive(Debug, Clone)]
pub struct Stock {
    pub ticker: String,
    pub name: String,
    pub price: Option<f64>,
    pub shares_outstanding: Option<i64>,
    pub financials: StatementMap,
    pub metrics: MetricMap,
    pub cf_history: Vec<HistoricalItems>,
    pub pl_history: Vec<HistoricalItems>,
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

#[derive(Debug, Clone, Deserialize)]
pub struct Column {
    pub header: String,
    pub source: String,
    pub format: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScreeningPayload {
    pub code: String,
    pub name: String,
    pub price: Option<f64>,
    pub metrics: PublicMetrics,
    pub fcf_yield_avg: Option<f64>,
    pub croic: Option<f64>,
    pub peg_trailing_5: Option<f64>,
    pub peg_trailing_5_status: PegStatus,
    pub peg_blended_5y_actual_2f: Option<f64>,
    pub peg_blended_5y_actual_2f_status: PegStatus,
    pub has_preferred_shares: Option<bool>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct MissingMetricDiagnostic {
    pub code: String,
    pub name: String,
    pub missing_fields: Vec<String>,
    pub unavailable_fields: Vec<UnavailableMetricDiagnostic>,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct UnavailableMetricDiagnostic {
    pub field: String,
    pub reason: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ScreeningRunResult {
    pub payload: Vec<ScreeningPayload>,
    pub diagnostics: Vec<MissingMetricDiagnostic>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PublicMetrics {
    pub net_cash_ratio: Option<f64>,
    pub per_actual: Option<f64>,
    pub per: Option<f64>,
    pub per_next: Option<f64>,
    pub pbr: Option<f64>,
    pub dividend_yield: Option<f64>,
    pub equity_ratio: Option<f64>,
    pub market_cap: Option<f64>,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum PegStatus {
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

    fn is_input_missing(self) -> bool {
        matches!(self, Self::MissingInput | Self::InsufficientHistory)
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
struct PegComputation {
    value: Option<f64>,
    status: PegStatus,
}

#[derive(Debug, Clone, Deserialize)]
pub struct ScreeningConfig {
    pub fcf_years: usize,
    pub peg_trailing_years: usize,
    pub peg_blended_actual_years: usize,
}

#[derive(Debug, Clone, Deserialize)]
struct MagicNumbersConfig {
    screening: ScreeningConfig,
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
    let metrics = compute_metrics(&raw.financials, raw.price, raw.shares_outstanding);
    Stock {
        ticker: raw.ticker,
        name: raw.name,
        price: raw.price,
        shares_outstanding: raw.shares_outstanding,
        financials: raw.financials,
        metrics,
        cf_history: raw.cf_history,
        pl_history: raw.pl_history,
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
    db_path: &Path,
    tickers: Option<&[String]>,
    return_all: bool,
) -> Result<Vec<ScreeningPayload>, String> {
    let config = load_screening_config()?;
    let strategy = load_strategy(strategy_path)?;
    let raw_stocks = stock_db_core::screening::load_screening_stocks(
        db_path,
        tickers,
        config.fcf_years,
        config.pl_history_periods(),
    )?;
    let stocks = raw_stocks.into_iter().map(build_stock).collect::<Vec<_>>();
    run_strategy_with_mode(&strategy, stocks, return_all)
        .iter()
        .map(|stock| serialize_stock_with_config(stock, &config))
        .collect()
}

pub fn run_screening_payload_with_diagnostics(
    strategy_path: &Path,
    db_path: &Path,
    tickers: Option<&[String]>,
    return_all: bool,
) -> Result<ScreeningRunResult, String> {
    let config = load_screening_config()?;
    let strategy = load_strategy(strategy_path)?;
    let raw_stocks = stock_db_core::screening::load_screening_stocks(
        db_path,
        tickers,
        config.fcf_years,
        config.pl_history_periods(),
    )?;
    let stocks = raw_stocks.into_iter().map(build_stock).collect::<Vec<_>>();
    let diagnostics = collect_missing_metric_diagnostics_with_config(&stocks, &config);
    let payload = run_strategy_with_mode(&strategy, stocks, return_all)
        .iter()
        .map(|stock| serialize_stock_with_config(stock, &config))
        .collect::<Result<Vec<_>, _>>()?;
    Ok(ScreeningRunResult {
        payload,
        diagnostics,
    })
}

pub fn serialize_stock(stock: &Stock) -> Result<ScreeningPayload, String> {
    let config = load_screening_config()?;
    serialize_stock_with_config(stock, &config)
}

fn serialize_stock_with_config(
    stock: &Stock,
    config: &ScreeningConfig,
) -> Result<ScreeningPayload, String> {
    let peg_trailing = peg_trailing_result(stock, config.peg_trailing_years);
    let peg_blended = peg_blended_2f_result(stock, config.peg_blended_actual_years);
    Ok(ScreeningPayload {
        code: stock.ticker.clone(),
        name: stock.name.clone(),
        price: stock.price,
        metrics: PublicMetrics {
            net_cash_ratio: metric(stock, "net_cash_ratio"),
            per_actual: metric(stock, "per_actual"),
            per: metric(stock, "per"),
            per_next: metric(stock, "per_next"),
            pbr: metric(stock, "pbr"),
            dividend_yield: metric(stock, "dividend_yield"),
            equity_ratio: metric(stock, "equity_ratio"),
            market_cap: metric(stock, "market_cap"),
        },
        fcf_yield_avg: fcf_yield_avg(stock, config.fcf_years),
        croic: croic(stock),
        peg_trailing_5: peg_trailing.value,
        peg_trailing_5_status: peg_trailing.status,
        peg_blended_5y_actual_2f: peg_blended.value,
        peg_blended_5y_actual_2f_status: peg_blended.status,
        has_preferred_shares: preferred_share_flag(stock)?,
    })
}

pub fn collect_missing_metric_diagnostics(
    stocks: &[Stock],
) -> Result<Vec<MissingMetricDiagnostic>, String> {
    let config = load_screening_config()?;
    Ok(collect_missing_metric_diagnostics_with_config(
        stocks, &config,
    ))
}

fn collect_missing_metric_diagnostics_with_config(
    stocks: &[Stock],
    config: &ScreeningConfig,
) -> Vec<MissingMetricDiagnostic> {
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
                ("metrics.pbr", metric(stock, "pbr")),
                ("metrics.dividend_yield", metric(stock, "dividend_yield")),
                ("metrics.equity_ratio", metric(stock, "equity_ratio")),
                ("metrics.market_cap", metric(stock, "market_cap")),
                ("fcf_yield_avg", fcf_yield_avg(stock, config.fcf_years)),
                ("croic", croic(stock)),
            ] {
                if value.is_none() {
                    missing_fields.push(field.to_string());
                }
            }
            let mut unavailable_fields = Vec::new();
            push_peg_diagnostic(
                &mut missing_fields,
                &mut unavailable_fields,
                "peg_trailing_5",
                peg_trailing_result(stock, config.peg_trailing_years).status,
            );
            push_peg_diagnostic(
                &mut missing_fields,
                &mut unavailable_fields,
                "peg_blended_5y_actual_2f",
                peg_blended_2f_result(stock, config.peg_blended_actual_years).status,
            );
            match preferred_share_flag(stock) {
                Ok(Some(_)) => {}
                Ok(None) => missing_fields.push("has_preferred_shares".to_string()),
                Err(_) => unavailable_fields.push(UnavailableMetricDiagnostic {
                    field: "has_preferred_shares".to_string(),
                    reason: "invalid_input".to_string(),
                }),
            }
            MissingMetricDiagnostic {
                code: stock.ticker.clone(),
                name: stock.name.clone(),
                missing_fields,
                unavailable_fields,
            }
        })
        .filter(|diagnostic| {
            !diagnostic.missing_fields.is_empty() || !diagnostic.unavailable_fields.is_empty()
        })
        .collect()
}

fn push_peg_diagnostic(
    missing_fields: &mut Vec<String>,
    unavailable_fields: &mut Vec<UnavailableMetricDiagnostic>,
    field: &str,
    status: PegStatus,
) {
    if status == PegStatus::Ok {
        return;
    }
    if status.is_input_missing() {
        missing_fields.push(field.to_string());
    }
    unavailable_fields.push(UnavailableMetricDiagnostic {
        field: field.to_string(),
        reason: status.as_str().to_string(),
    });
}

pub fn compute_all_metrics(
    db_path: &Path,
) -> Result<HashMap<String, HashMap<String, Option<PublicMetricValue>>>, String> {
    let config = load_screening_config()?;
    let raw_stocks = stock_db_core::screening::load_screening_stocks(
        db_path,
        None,
        config.fcf_years,
        config.pl_history_periods(),
    )?;
    let mut result = HashMap::new();
    for raw_stock in raw_stocks {
        if raw_stock.financials.is_empty() {
            continue;
        }
        let stock = build_stock(raw_stock);
        let preferred_share_value = preferred_share_flag(&stock)?.map(PublicMetricValue::Bool);
        let peg_trailing = peg_trailing_result(&stock, config.peg_trailing_years);
        let peg_blended = peg_blended_2f_result(&stock, config.peg_blended_actual_years);
        result.insert(
            stock.ticker.clone(),
            HashMap::from([
                ("price".to_string(), metric_value(stock.price)),
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
                    "equity_ratio".to_string(),
                    metric_value(metric(&stock, "equity_ratio")),
                ),
                (
                    "fcf_yield_avg".to_string(),
                    metric_value(fcf_yield_avg(&stock, config.fcf_years)),
                ),
                ("croic".to_string(), metric_value(croic(&stock))),
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
                    "market_cap".to_string(),
                    metric_value(metric(&stock, "market_cap")),
                ),
                ("has_preferred_shares".to_string(), preferred_share_value),
            ]),
        );
    }
    Ok(result)
}

impl ScreeningConfig {
    fn pl_history_periods(&self) -> usize {
        std::cmp::max(
            self.peg_trailing_years + 1,
            self.peg_blended_actual_years + 1,
        )
    }
}

fn load_screening_config() -> Result<ScreeningConfig, String> {
    let config_path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("config")
        .join("magic_numbers.toml");
    let content = fs::read_to_string(&config_path).map_err(|err| err.to_string())?;
    let parsed: MagicNumbersConfig = toml::from_str(&content).map_err(|err| err.to_string())?;
    Ok(parsed.screening)
}

#[derive(Debug, Clone)]
pub enum PublicMetricValue {
    Float(f64),
    Bool(bool),
    Text(String),
}

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
    for column in &strategy.columns {
        if !valid_sources.contains(column.source.as_str()) {
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
        "peg_trailing_5",
        "peg_blended_5y_actual_2f",
        "preferred_share_label",
    ])
}

fn resolve_numeric_value(stock: &Stock, source: &str) -> Option<f64> {
    match source {
        "fcf_yield_avg" => fcf_yield_avg(stock, 10),
        "croic" => croic(stock),
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

fn resolve_free_cf(items: &ItemMap) -> Option<f64> {
    item(items, "free_cf").or_else(|| {
        match (item(items, "operating_cf"), item(items, "investing_cf")) {
            (Some(operating_cf), Some(investing_cf)) => Some(operating_cf + investing_cf),
            _ => None,
        }
    })
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
        return peg_unavailable(PegStatus::InsufficientHistory);
    }
    let Some(per_actual) = metric(stock, "per_actual") else {
        return peg_unavailable(PegStatus::MissingInput);
    };
    if per_actual <= 0.0 {
        return peg_unavailable(PegStatus::NonPositivePer);
    }
    if stock.pl_history.len() < years + 1 {
        return peg_unavailable(PegStatus::InsufficientHistory);
    }
    let recent = &stock.pl_history[..years + 1];
    let Some(eps_values) = positive_eps_values(recent) else {
        return peg_unavailable(PegStatus::MissingInput);
    };
    if eps_values.iter().any(|value| *value <= 0.0) {
        return peg_unavailable(PegStatus::NonPositiveEps);
    }
    let latest_eps = eps_values[0];
    let oldest_eps = eps_values[years];
    let cagr = (latest_eps / oldest_eps).powf(1.0 / years as f64) - 1.0;
    if cagr <= 0.0 {
        return peg_unavailable(PegStatus::NonPositiveGrowth);
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
    if actual_years < 1 {
        return peg_unavailable(PegStatus::InsufficientHistory);
    }
    let Some(per_next) = metric(stock, "per_next") else {
        return peg_unavailable(PegStatus::MissingInput);
    };
    if per_next <= 0.0 {
        return peg_unavailable(PegStatus::NonPositivePer);
    }
    let Some(forecast) = stock.financials.get("forecast") else {
        return peg_unavailable(PegStatus::MissingInput);
    };
    let Some(eps_current) = item(forecast, "eps_current") else {
        return peg_unavailable(PegStatus::MissingInput);
    };
    let Some(eps_next) = item(forecast, "eps_next") else {
        return peg_unavailable(PegStatus::MissingInput);
    };
    if eps_current <= 0.0 || eps_next <= 0.0 {
        return peg_unavailable(PegStatus::NonPositiveEps);
    }
    if stock.pl_history.len() < actual_years + 1 {
        return peg_unavailable(PegStatus::InsufficientHistory);
    }
    let recent = &stock.pl_history[..actual_years + 1];
    let Some(eps_values) = positive_eps_values(recent) else {
        return peg_unavailable(PegStatus::MissingInput);
    };
    if eps_values.iter().any(|value| *value <= 0.0) {
        return peg_unavailable(PegStatus::NonPositiveEps);
    }
    let oldest_actual_eps = eps_values[actual_years];
    let total_periods = actual_years + 2;
    let cagr = (eps_next / oldest_actual_eps).powf(1.0 / total_periods as f64) - 1.0;
    if cagr <= 0.0 {
        return peg_unavailable(PegStatus::NonPositiveGrowth);
    }
    PegComputation {
        value: Some(per_next / (cagr * 100.0)),
        status: PegStatus::Ok,
    }
}

fn positive_eps_values(periods: &[HistoricalItems]) -> Option<Vec<f64>> {
    periods
        .iter()
        .map(|period| item(&period.items, "eps"))
        .collect()
}

fn peg_unavailable(status: PegStatus) -> PegComputation {
    PegComputation {
        value: None,
        status,
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
#[pyo3(signature = (db_path=None))]
fn compute_all_stock_metrics(py: Python<'_>, db_path: Option<String>) -> PyResult<PyObject> {
    let resolved_path = db_path.unwrap_or_else(|| "../stock_db/var/db/stocks.db".to_string());
    let metrics = py
        .allow_threads(|| compute_all_metrics(Path::new(&resolved_path)))
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    metrics_to_py(py, &metrics)
}

#[pyfunction]
#[pyo3(signature = (strategy_path, db_path, tickers=None, return_all=false))]
fn run_screening_payload_py(
    py: Python<'_>,
    strategy_path: String,
    db_path: String,
    tickers: Option<Vec<String>>,
    return_all: bool,
) -> PyResult<PyObject> {
    let payload = py
        .allow_threads(|| {
            run_screening_payload(
                Path::new(&strategy_path),
                Path::new(&db_path),
                tickers.as_deref(),
                return_all,
            )
        })
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    payloads_to_py(py, &payload)
}

#[pyfunction]
#[pyo3(signature = (strategy_path, db_path, tickers=None, return_all=false))]
fn run_screening_payload_with_diagnostics_py(
    py: Python<'_>,
    strategy_path: String,
    db_path: String,
    tickers: Option<Vec<String>>,
    return_all: bool,
) -> PyResult<PyObject> {
    let result = py
        .allow_threads(|| {
            run_screening_payload_with_diagnostics(
                Path::new(&strategy_path),
                Path::new(&db_path),
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
        for (key, value) in values {
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
        set_optional_float(py, &metrics, "pbr", payload.metrics.pbr)?;
        set_optional_float(
            py,
            &metrics,
            "dividend_yield",
            payload.metrics.dividend_yield,
        )?;
        set_optional_float(py, &metrics, "equity_ratio", payload.metrics.equity_ratio)?;
        set_optional_float(py, &metrics, "market_cap", payload.metrics.market_cap)?;
        row.set_item("metrics", metrics)?;

        set_optional_float(py, &row, "fcf_yield_avg", payload.fcf_yield_avg)?;
        set_optional_float(py, &row, "croic", payload.croic)?;
        set_optional_float(py, &row, "peg_trailing_5", payload.peg_trailing_5)?;
        row.set_item(
            "peg_trailing_5_status",
            payload.peg_trailing_5_status.as_str(),
        )?;
        set_optional_float(
            py,
            &row,
            "peg_blended_5y_actual_2f",
            payload.peg_blended_5y_actual_2f,
        )?;
        row.set_item(
            "peg_blended_5y_actual_2f_status",
            payload.peg_blended_5y_actual_2f_status.as_str(),
        )?;
        match payload.has_preferred_shares {
            Some(value) => row.set_item("has_preferred_shares", value)?,
            None => row.set_item("has_preferred_shares", py.None())?,
        }
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
        let unavailable_fields = pyo3::types::PyList::empty(py);
        for unavailable in &diagnostic.unavailable_fields {
            let unavailable_item = pyo3::types::PyDict::new(py);
            unavailable_item.set_item("field", &unavailable.field)?;
            unavailable_item.set_item("reason", &unavailable.reason)?;
            unavailable_fields.append(unavailable_item)?;
        }
        item.set_item("unavailable_fields", unavailable_fields)?;
        diagnostics.append(item)?;
    }
    row.set_item("diagnostics", diagnostics)?;
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
        let metrics = compute_metrics(&financials, Some(1000.0), Some(10_000_000));
        Stock {
            ticker: "1301".to_string(),
            name: "test".to_string(),
            price: Some(1000.0),
            shares_outstanding: Some(10_000_000),
            financials,
            metrics,
            cf_history: (0..10)
                .map(|year| HistoricalItems {
                    period: format!("20{:02}-03", 25 - year),
                    items: HashMap::from([("free_cf".to_string(), Some(10.0 - year as f64))]),
                })
                .collect(),
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
    }

    #[test]
    fn indicators_and_preferred_share_flag_match_python_contract() {
        let stock = sample_stock();
        assert!(fcf_yield_avg(&stock, 10).is_some());
        assert!(peg_trailing(&stock, 5).is_some());
        assert_eq!(peg_trailing_result(&stock, 5).status, PegStatus::Ok);
        assert!(peg_blended_2f(&stock, 5).is_some());
        assert_eq!(peg_blended_2f_result(&stock, 5).status, PegStatus::Ok);
        assert_eq!(preferred_share_flag(&stock), Ok(Some(true)));
    }

    #[test]
    fn peg_status_reports_negative_growth_separately() {
        let mut stock = sample_stock();
        stock.pl_history = vec![
            ("2025-03", 80.0),
            ("2024-03", 90.0),
            ("2023-03", 100.0),
            ("2022-03", 110.0),
            ("2021-03", 120.0),
            ("2020-03", 130.0),
        ]
        .into_iter()
        .map(|(period, eps)| HistoricalItems {
            period: period.to_string(),
            items: HashMap::from([("eps".to_string(), Some(eps))]),
        })
        .collect();
        stock
            .financials
            .get_mut("forecast")
            .expect("sample stock has forecast")
            .insert("eps_next".to_string(), Some(60.0));

        assert_eq!(
            peg_trailing_result(&stock, 5),
            PegComputation {
                value: None,
                status: PegStatus::NonPositiveGrowth,
            }
        );
        assert_eq!(
            peg_blended_2f_result(&stock, 5),
            PegComputation {
                value: None,
                status: PegStatus::NonPositiveGrowth,
            }
        );
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
                unavailable_fields: vec![],
            }])
        );
    }

    #[test]
    fn missing_metric_diagnostics_report_peg_unavailable_reasons() {
        let mut stock = sample_stock();
        stock.pl_history = vec![
            ("2025-03", 80.0),
            ("2024-03", 90.0),
            ("2023-03", 100.0),
            ("2022-03", 110.0),
            ("2021-03", 120.0),
            ("2020-03", 130.0),
        ]
        .into_iter()
        .map(|(period, eps)| HistoricalItems {
            period: period.to_string(),
            items: HashMap::from([("eps".to_string(), Some(eps))]),
        })
        .collect();
        stock
            .financials
            .get_mut("forecast")
            .expect("sample stock has forecast")
            .insert("eps_next".to_string(), Some(60.0));

        assert_eq!(
            collect_missing_metric_diagnostics(&[stock]),
            Ok(vec![MissingMetricDiagnostic {
                code: "1301".to_string(),
                name: "test".to_string(),
                missing_fields: vec![],
                unavailable_fields: vec![
                    UnavailableMetricDiagnostic {
                        field: "peg_trailing_5".to_string(),
                        reason: "non_positive_growth".to_string(),
                    },
                    UnavailableMetricDiagnostic {
                        field: "peg_blended_5y_actual_2f".to_string(),
                        reason: "non_positive_growth".to_string(),
                    },
                ],
            }])
        );
    }
}
