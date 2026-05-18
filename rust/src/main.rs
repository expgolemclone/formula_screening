use std::fs;
use std::path::PathBuf;

use clap::{Parser, Subcommand};
use formula_screening_core::run_screening_payload;
use serde_json::Value;
use stock_web_ui_core::{BrowserEntry, IndexPage, ServeConfig, ServerConfig};

#[derive(Debug, Parser)]
#[command(name = "formula-screening")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    Screen(ScreenArgs),
}

#[derive(Debug, clap::Args)]
struct ScreenArgs {
    #[arg(short, long)]
    strategy: PathBuf,
    #[arg(short, long)]
    ticker: Vec<String>,
    #[arg(long)]
    json: Option<PathBuf>,
    #[arg(long, default_value = "../stock_db/var/db/stocks.db")]
    db_path: PathBuf,
}

fn main() -> Result<(), String> {
    let cli = Cli::parse();
    match cli.command {
        Command::Screen(args) => run_screen(args),
    }
}

fn run_screen(args: ScreenArgs) -> Result<(), String> {
    let tickers = resolve_ticker_args(&args.ticker)?;
    let payload = run_screening_payload(&args.strategy, &args.db_path, tickers.as_deref(), false)?;
    println!("{} stocks matched", payload.len());

    let default_json = PathBuf::from("docs/assets/screening.json");
    write_json(&default_json, &payload)?;

    if let Some(path) = args.json {
        write_json(&path, &payload)?;
        println!("Saved to {}", path.display());
        return Ok(());
    }

    let serve_payload = serde_json::to_value(payload).map_err(|err| err.to_string())?;
    serve_local(serve_payload)
}

fn resolve_ticker_args(values: &[String]) -> Result<Option<Vec<String>>, String> {
    if values.is_empty() || values == ["all"] {
        return Ok(None);
    }
    if values.len() == 1 {
        let value = &values[0];
        if let Some((lo, hi)) = value.split_once('-') {
            let lo = lo.parse::<u32>().map_err(|err| err.to_string())?;
            let hi = hi.parse::<u32>().map_err(|err| err.to_string())?;
            return Ok(Some((lo..=hi).map(|value| value.to_string()).collect()));
        }
        if let Some(path) = value.strip_prefix("csv:") {
            let content = fs::read_to_string(path).map_err(|err| err.to_string())?;
            let tickers = content
                .lines()
                .filter_map(|line| line.split(',').next())
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(ToOwned::to_owned)
                .collect::<Vec<_>>();
            if tickers.is_empty() {
                return Err(format!("No tickers found in {path}"));
            }
            return Ok(Some(tickers));
        }
    }
    Ok(Some(values.to_vec()))
}

fn write_json<T: serde::Serialize>(path: &PathBuf, payload: &T) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let body = serde_json::to_vec_pretty(payload).map_err(|err| err.to_string())?;
    fs::write(path, body).map_err(|err| err.to_string())
}

fn serve_local(payload: Value) -> Result<(), String> {
    stock_web_ui_core::serve(ServeConfig {
        server: ServerConfig {
            host: "127.0.0.1".to_string(),
            port: 8080,
        },
        static_root: PathBuf::from("docs/assets"),
        shared_assets_root: PathBuf::from("../stock_web_ui/docs/assets"),
        index_template_path: PathBuf::from("../stock_web_ui/docs/index.template.html"),
        index_page: IndexPage {
            title: "Formula Screening".to_string(),
            loading_message: "スクリーニング結果を読み込み中です。".to_string(),
            tab_aria_label: "タブ切替".to_string(),
            asset_version: String::new(),
            shared_asset_base_url: String::new(),
        },
        api_path: "/api/screening".to_string(),
        api_payload: payload,
        yazi_base_dir: Some(PathBuf::from("../japan_company_handbook/data")),
        browser_entries: vec![
            (
                "shikiho".to_string(),
                BrowserEntry {
                    command: "google-chrome".to_string(),
                    allowed_url_prefix: "https://shikiho.toyokeizai.net/".to_string(),
                },
            ),
            (
                "monex".to_string(),
                BrowserEntry {
                    command: "google-chrome".to_string(),
                    allowed_url_prefix: "https://scouter.monex.co.jp/".to_string(),
                },
            ),
        ],
    })
}
